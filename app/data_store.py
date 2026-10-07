"""Zip code or street address -> water systems + violations (the data layer).

Public entry points: lookup_zip(zip) and lookup_address(address). Their return
shape is defined in docs/DATA_CONTRACT.md. They NEVER raise; the source chain is:

    fresh cache (< 7 days) -> live (EPA + Census + boundaries) -> stale cache
    -> data/fallback.json -> empty

Set env var WQT_OFFLINE=1 to skip the live APIs (demo safety switch).
"""

import csv
import json
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date

from app import epa_client, geo, rules
from app.epa_client import EpaApiError
from app.geo import GeoError

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
ZIP_CSV_PATH = os.path.join(DATA_DIR, "zip_to_city.csv")
FALLBACK_PATH = os.path.join(DATA_DIR, "fallback.json")
CACHE_PATH = os.path.join(DATA_DIR, "cache.sqlite3")

CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
MAX_SYSTEMS = 10          # more than this is noise for a parent reading the page
MAX_CANDIDATES = 25       # how many systems we fetch details for before trimming to 10
LOOKUP_DEADLINE_SECONDS = 12  # whole live lookup (geocoder included); after this we use cache/fallback
MAX_OLD_VIOLATIONS = 5    # non-current violations kept per system (current ones are always kept)
HISTORY_YEARS = 10        # the trend covers this many calendar years, incl. this one

# Lower number = better match. Used for dedupe and sorting.
MATCH_RANK = {
    "address_boundary": 0, "zip_boundary": 1,
    "service_area": 2, "city_served": 3, "admin_address": 4,
}
BOUNDARY_MATCHES = ("address_boundary", "zip_boundary")

VIOLATION_CATEGORIES = {"MCL", "MRDL", "TT", "MR", "MON", "RPT"}

# EPA compliance_status_code: O open, K known (never returned to compliance),
# R returned to compliance. Anything else is "archived". Whether a violation is
# *current* is decided by rules.is_current(), which every layer shares.
STATUS_MAP = {"O": "open", "K": "known", "R": "resolved"}

_zip_table = None   # zip -> (city, state), loaded once on first use
_zip_coords = None  # zip -> (lat, lon) of the zip's center point


# --------------------------------------------------------------------------
# zip -> (city, state) and zip center point
# --------------------------------------------------------------------------

def _load_zip_table():
    global _zip_table
    if _zip_table is None:
        table = {}
        try:
            with open(ZIP_CSV_PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    table[row["zip"]] = (row["city"], row["state"])
        except OSError:
            pass  # a missing CSV just means live lookups find nothing
        _zip_table = table
    return _zip_table


def zip_to_city_state(zip_code):
    """Return (CITY, STATE) for a zip, or None if we do not know it."""
    return _load_zip_table().get(zip_code)


def zip_center(zip_code):
    """Return (lat, lon) of a zip's center point, or None if unknown."""
    global _zip_coords
    if _zip_coords is None:
        coords = {}
        try:
            with open(ZIP_CSV_PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        coords[row["zip"]] = (float(row["lat"]), float(row["lon"]))
                    except (KeyError, TypeError, ValueError):
                        continue  # an old CSV without lat/lon just means no boundary search
        except OSError:
            pass
        _zip_coords = coords
    return _zip_coords.get(zip_code)


# --------------------------------------------------------------------------
# Normalizing raw EPA rows into contract shapes
# --------------------------------------------------------------------------

def _date(value):
    """'2020-07-01 00:00:00' -> '2020-07-01'; None stays None."""
    return value[:10] if value else None


def _float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_violation(raw):
    category = raw.get("violation_category_code")
    tier = raw.get("public_notification_tier")
    return {
        "violation_id": str(raw.get("violation_id")),
        "contaminant_code": raw.get("contaminant_code"),
        "violation_code": raw.get("violation_code"),
        "category_code": category if category in VIOLATION_CATEGORIES else "Other",
        "rule_code": raw.get("rule_code"),
        "is_health_based": raw.get("is_health_based_ind") == "Y",
        "status": STATUS_MAP.get(raw.get("compliance_status_code"), "archived"),
        "begin_date": _date(raw.get("compl_per_begin_date")),
        "end_date": _date(raw.get("compl_per_end_date")),
        "returned_to_compliance_date": _date(raw.get("rtc_date")),
        "measure": _float(raw.get("viol_measure")),
        "unit": raw.get("unit_of_measure"),
        "state_mcl": _float(raw.get("state_mcl")),
        "notification_tier": tier if tier in (1, 2, 3) else None,
    }


def trim_violations(violations):
    """Keep every current violation plus the 5 most recent others, newest first."""
    # Missing dates sort as "" so they land last when newest-first.
    newest_first = sorted(violations, key=lambda v: v["begin_date"] or "", reverse=True)
    current = [v for v in newest_first if rules.is_current(v)]
    others = [v for v in newest_first if not rules.is_current(v)][:MAX_OLD_VIOLATIONS]
    return sorted(current + others, key=lambda v: v["begin_date"] or "", reverse=True)


def history_by_year(violations, today=None):
    """Violations per calendar year for the last 10 years, zeros filled, oldest first.

    WHY all violations (not the trimmed list): trimming drops old ones, which would
    make the past look cleaner than it was.
    """
    this_year = (today or date.today()).year
    rows = {y: {"year": y, "health_based": 0, "other": 0}
            for y in range(this_year - HISTORY_YEARS + 1, this_year + 1)}
    for v in violations:
        begin = v.get("begin_date")
        if not begin or not begin[:4].isdigit():
            continue
        row = rows.get(int(begin[:4]))
        if row is not None:
            # Same health-based rule as the badge (lead pipe inventories count as record-keeping).
            row["health_based" if rules.counts_as_health_based(v) else "other"] += 1
    return [rows[y] for y in sorted(rows)]


def _dated_lead_results(results, samples):
    """Every lead 90th-percentile result that has a date, oldest first."""
    # The results table has no dates; the samples table does. Join on sample_id.
    end_dates = {s.get("sample_id"): _date(s.get("sampling_end_date")) for s in samples}
    dated = []
    for r in results:
        value = _float(r.get("sample_measure"))
        sample_date = end_dates.get(r.get("sample_id"))
        if value is None or sample_date is None:
            continue
        dated.append({"date": sample_date, "value_mg_l": value})
    return sorted(dated, key=lambda d: d["date"])


def lead_summary(dated, today=None):
    """(latest result or None, results from the last 10 calendar years)."""
    first_year = (today or date.today()).year - HISTORY_YEARS + 1
    recent = [d for d in dated if d["date"][:4].isdigit() and int(d["date"][:4]) >= first_year]
    latest = None
    if dated:
        latest = {"value_mg_l": dated[-1]["value_mg_l"], "sample_date": dated[-1]["date"]}
    return latest, recent


def build_contact(details):
    """Who to call about the water: organization info only.

    NEVER include admin_name: it is a private person's name. Some systems also
    put that person's name in org_name, so when the two match we show the
    water system's own name instead.
    """
    org = (details.get("org_name") or "").strip()
    admin = (details.get("admin_name") or "").strip()
    if not org or org.upper() == admin.upper():
        org = (details.get("pws_name") or "").strip()
    digits = re.sub(r"\D", "", details.get("phone_number") or "")
    email = (details.get("email_addr") or "").strip().lower()

    street = ", ".join(p.strip() for p in (details.get("address_line1"), details.get("address_line2")) if p and p.strip())
    city = (details.get("city_name") or "").strip()
    state_zip = " ".join(p for p in ((details.get("state_code") or "").strip(), (details.get("zip_code") or "").strip()) if p)
    place = ", ".join(p for p in (city, state_zip) if p)
    return {
        "org_name": org or None,
        "phone": digits or None,
        "email": email if "@" in email else None,
        "address": ", ".join(p for p in (street, place) if p) or None,
    }


# --------------------------------------------------------------------------
# Live lookup
# --------------------------------------------------------------------------

def _run_parallel(func, items, workers, deadline):
    """Like pool.map, but gives up (EpaApiError) once `deadline` (a time.monotonic() value) passes.

    WHY: each request has its own timeout, but several rounds of them add up.
    The user would stare at a spinner, so we stop and let the caller fall back.
    """
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(func, item) for item in items]
        return [f.result(timeout=max(0, deadline - time.monotonic())) for f in futures]
    except FutureTimeout:
        raise EpaApiError("live lookup took too long")
    finally:
        # Do not wait for stragglers (their own request timeouts will end them).
        pool.shutdown(wait=False, cancel_futures=True)


def _add_candidate(candidates, row_pwsid, match_type, details=None, quality=None):
    """Record a possible match. Strategies run best-first, so the first match_type wins."""
    old = candidates.get(row_pwsid)
    if old is None:
        candidates[row_pwsid] = {"match_type": match_type, "details": details, "quality": quality}
    else:
        old["details"] = old["details"] or details
        old["quality"] = old["quality"] or quality


def _add_boundary_hits(candidates, hits, state, match_type):
    """Add systems whose polygon contains a point. Returns nothing; same-state only."""
    for hit in hits:
        # Same-state filter: edge-of-state polygons and wholesalers can cross borders.
        if state and hit.get("state") != state:
            continue
        _add_candidate(candidates, hit["pwsid"], match_type, quality=hit.get("quality"))


def _find_candidates(zip_code, city, state, deadline=None, center=None):
    """Run the zip match strategies; return ({pwsid: candidate}, boundary_failed).

    A candidate is {"match_type", "details" (a WATER_SYSTEM row or None), "quality"}.
    """
    if deadline is None:
        deadline = time.monotonic() + LOOKUP_DEADLINE_SECONDS
    boundary_failed = []

    def zip_boundary():
        if center is None:
            return []
        try:
            return geo.systems_at_point(*center)
        except GeoError:
            # The map service is an extra: the EPA tables can still answer without it.
            boundary_failed.append(True)
            return []

    calls = [
        zip_boundary,
        lambda: epa_client.systems_by_zip_served(zip_code),
        lambda: epa_client.systems_by_city_served(city, state),
        lambda: epa_client.systems_by_admin_zip(zip_code, state),
    ]
    rows_per_strategy = _run_parallel(lambda call: call(), calls, 4, deadline)
    types = ["zip_boundary", "service_area", "city_served", "admin_address"]

    candidates = {}
    _add_boundary_hits(candidates, rows_per_strategy[0], state, "zip_boundary")
    for match_type, rows in zip(types[1:], rows_per_strategy[1:]):
        for row in rows:
            # Same-state filter: a management company's office can sit in this zip
            # while the systems it runs are in other states.
            if row.get("primacy_agency_code") != state:
                continue
            if row.get("pws_type_code") != "CWS" or row.get("pws_activity_code") != "A":
                continue
            # Admin-address rows are full WATER_SYSTEM rows, so we keep them as details.
            details = row if "pws_name" in row else None
            _add_candidate(candidates, row["pwsid"], match_type, details)
    return candidates, bool(boundary_failed)


def _fetch_details(pwsid, candidate):
    """Return (pwsid, candidate with details filled in), or None if not an active CWS."""
    details = candidate["details"]
    if details is None:
        rows = epa_client.water_system_details(pwsid)
        if not rows:
            return None
        details = rows[0]
    if details.get("pws_type_code") != "CWS" or details.get("pws_activity_code") != "A":
        return None
    return pwsid, {**candidate, "details": details}


def _serves_city(area_rows, city):
    """Does a system's own service-area list name `city`? None if it lists no cities.

    city_served can be a comma list with municipal codes, e.g.
    "EWING TWP.-1102,TRENTON CITY-1111", so we look for the city as a whole word.
    """
    listed = [r["city_served"].upper() for r in area_rows if r.get("city_served")]
    if not listed:
        return None
    pattern = re.compile(r"\b" + re.escape(city.upper()) + r"\b")
    return any(pattern.search(s) for s in listed)


def _build_system(pwsid, candidate, city):
    """Fetch violations, lead results and (if boundary-matched) the outline for one system.

    Returns (WaterSystem, serves_city, boundary_failed) where serves_city is
    True/False/None and is only checked for admin-address matches (see _assemble).
    """
    match_type, details, quality = candidate["match_type"], candidate["details"], candidate["quality"]
    # Independent ~3s requests: run them side by side.
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_viol = pool.submit(epa_client.violations_by_pwsid, pwsid)
        f_lead = pool.submit(epa_client.lead_90th_results, pwsid)
        f_samples = pool.submit(epa_client.lead_samples, pwsid)
        f_areas = pool.submit(epa_client.service_areas, pwsid) if match_type == "admin_address" else None
        f_outline = pool.submit(geo.boundary_outline, pwsid) if match_type in BOUNDARY_MATCHES else None
        violations = [normalize_violation(v) for v in f_viol.result()]
        try:
            dated_lead = _dated_lead_results(f_lead.result(), f_samples.result())
        except EpaApiError:
            dated_lead = []  # lead is optional; it must never fail the whole lookup
        serves_city = None
        if f_areas is not None:
            try:
                serves_city = _serves_city(f_areas.result(), city)
            except EpaApiError:
                serves_city = None  # unknown: keep the match rather than fail the lookup
        boundary, boundary_failed = None, False
        if f_outline is not None:
            try:
                boundary = f_outline.result()
            except GeoError:
                boundary_failed = True  # no outline just means no map shape
    if serves_city:
        # Its office is in this zip AND it lists this city as served: a real match.
        match_type = "city_served"
    latest_lead, recent_lead = lead_summary(dated_lead)
    population = details.get("population_served_count")
    system = {
        "pwsid": pwsid,
        "name": details.get("pws_name"),
        "city": details.get("city_name"),
        "state": details.get("state_code") or details.get("primacy_agency_code"),
        "population_served": int(population) if population is not None else None,
        "match_type": match_type,
        "boundary_quality": quality if match_type in BOUNDARY_MATCHES else None,
        "boundary": boundary,
        "total_violation_count": len(violations),
        "violations": trim_violations(violations),
        "lead_90th": latest_lead,
        "history": {
            "by_year": history_by_year(violations),
            "lead_90th": recent_lead,
        },
        "contact": build_contact(details),
    }
    return system, serves_city, boundary_failed


def _assemble(candidates, city, deadline):
    """Turn candidates into the final ranked WaterSystem list. Returns (systems, boundary_failed)."""
    # Strategies 1 and 2 return no population, so we need each system's details
    # to rank it. Best matches first, so a big city never makes us fetch hundreds.
    ordered = sorted(candidates.items(), key=lambda item: MATCH_RANK[item[1]["match_type"]])
    ordered = ordered[:MAX_CANDIDATES]
    detailed = _run_parallel(lambda item: _fetch_details(*item), ordered, 8, deadline)
    detailed = [d for d in detailed if d is not None]
    detailed.sort(key=lambda d: (
        MATCH_RANK[d[1]["match_type"]], -(d[1]["details"].get("population_served_count") or 0)))
    top = detailed[:MAX_SYSTEMS]

    built = _run_parallel(lambda d: _build_system(d[0], d[1], city), top, 8, deadline)
    boundary_failed = any(failed for _, _, failed in built)

    # An office in this zip whose service area lists only OTHER towns is usually a
    # management company, not this zip's water supplier (e.g. a Trenton zip
    # returning Maple Shade). Drop those, unless that would leave nothing at all.
    confirmed = [system for system, serves_city, _ in built if serves_city is not False]
    systems = confirmed or [system for system, _, _ in built]
    systems.sort(key=lambda s: (MATCH_RANK[s["match_type"]], -(s["population_served"] or 0)))
    return systems, boundary_failed


def _query_block(kind, text, state=None, lat=None, lon=None, matched=None, tract=None, not_found=False):
    return {
        "type": kind, "input": text, "matched_address": matched,
        "lat": lat, "lon": lon, "state": state, "tract": tract, "not_found": not_found,
    }


def _search_zip(zip_code, deadline):
    """Zip search. Returns (systems, boundary_failed, query_state, center)."""
    place = zip_to_city_state(zip_code)
    if place is None:  # not a real zip: nothing to ask EPA about
        return [], False, None, None
    city, state = place
    center = zip_center(zip_code)
    candidates, failed_a = _find_candidates(zip_code, city, state, deadline, center)
    systems, failed_b = _assemble(candidates, city, deadline)
    return systems, failed_a or failed_b, state, center


def _fetch_zip(zip_code):
    """Live zip lookup -> (result, degraded). Raises EpaApiError if ANY EPA sub-request fails.

    degraded=True means an optional map request failed: the answer is usable but
    should not be cached (we would keep serving it, without maps, for a week).
    """
    deadline = time.monotonic() + LOOKUP_DEADLINE_SECONDS
    systems, degraded, state, center = _search_zip(zip_code, deadline)
    lat, lon = center if center else (None, None)
    return {
        "zip": zip_code, "source": "live",
        "query": _query_block("zip", zip_code, state, lat, lon),
        "systems": systems,
    }, degraded


def fetch_live(zip_code):
    """Full live zip lookup. Raises EpaApiError if ANY sub-request fails."""
    return _fetch_zip(zip_code)[0]


def _fetch_address(address):
    """Live address lookup -> (result, degraded). Raises EpaApiError/GeoError on service failure."""
    deadline = time.monotonic() + LOOKUP_DEADLINE_SECONDS
    found = geo.geocode_address(address)
    if found is None:
        return _empty_address(address, source="live"), False

    lat, lon, state = found["lat"], found["lon"], found["state"]
    hits = geo.systems_at_point(lat, lon)
    candidates = {}
    _add_boundary_hits(candidates, hits, state, "address_boundary")

    if candidates:
        systems, degraded = _assemble(candidates, None, deadline)
    else:
        # Point is outside every polygon (boundaries have gaps): search the zip instead.
        systems, degraded, _, _ = _search_zip(found["zip"] or "", deadline)
    return {
        "zip": found["zip"], "source": "live",
        "query": _query_block("address", address, state, lat, lon, found["matched_address"], found["tract"]),
        "systems": systems,
    }, degraded


# --------------------------------------------------------------------------
# Cache (SQLite) and fallback file
# --------------------------------------------------------------------------
# Keys look like "zip:48502" or "addr:1600 pennsylvania ave nw, washington dc".
# (The column is still called "zip" from the MVP; SQLite does not care.)

def _cache_connect():
    # New connection per call: Flask is threaded and sqlite3 connections are not shareable.
    conn = sqlite3.connect(CACHE_PATH, timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lookups (zip TEXT PRIMARY KEY, fetched_at REAL, payload TEXT)"
    )
    return conn


def _cache_get(key):
    """Return (result, age_in_seconds) or None. Any cache trouble just means 'no cache'."""
    try:
        conn = _cache_connect()
        try:
            row = conn.execute(
                "SELECT fetched_at, payload FROM lookups WHERE zip = ?", (key,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return json.loads(row[1]), time.time() - row[0]
    except (sqlite3.Error, OSError, ValueError):
        return None


def _cache_put(key, result):
    try:
        conn = _cache_connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO lookups (zip, fetched_at, payload) VALUES (?, ?, ?)",
                    (key, time.time(), json.dumps(result)),
                )
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        pass  # failing to cache must never break a lookup


def _address_tokens(text):
    """Words of an address with zip codes dropped: 'dc 20500' -> {'dc'}."""
    return {w for w in re.findall(r"[a-z0-9]+", text) if not (w.isdigit() and len(w) == 5)}


def _fallback_get(key):
    try:
        with open(FALLBACK_PATH, encoding="utf-8") as f:
            table = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(table, dict):
        return None
    if key in table or not key.startswith("addr:"):
        return table.get(key)
    # Offline demo: people type "1101 S Saginaw St, Flint" without the state or zip.
    # Match when the street part is identical and every other word they typed is in the saved address.
    street, _, rest = key[len("addr:"):].partition(",")
    wanted = _address_tokens(rest)
    for saved_key, value in table.items():
        s_street, _, s_rest = saved_key[len("addr:"):].partition(",")
        if saved_key.startswith("addr:") and s_street.strip() == street.strip()                 and wanted <= _address_tokens(s_rest):
            return value
    return None


def normalize_address(address):
    """Cache/fallback form of an address: lowercase, single spaces."""
    return " ".join(str(address).lower().split())


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def _empty_zip(zip_code):
    place = zip_to_city_state(zip_code)
    center = zip_center(zip_code)
    lat, lon = center if center else (None, None)
    return {"zip": zip_code, "source": "fallback",
            "query": _query_block("zip", zip_code, place[1] if place else None, lat, lon),
            "systems": []}


def _empty_address(address, source="fallback"):
    # not_found is True because we could not place this address (or could not try).
    return {"zip": None, "source": source,
            "query": _query_block("address", address, not_found=True),
            "systems": []}


def _lookup(key, fetch, empty, label_input=None):
    """The shared source chain. `fetch()` returns (result, degraded) or raises."""

    def finish(result, source):
        result = {**result, "source": source}
        if label_input is not None and isinstance(result.get("query"), dict):
            # The stored copy may have been saved for a differently-typed version of this address.
            result["query"] = {**result["query"], "input": label_input}
        return result

    try:
        cached = _cache_get(key)
        if cached and cached[1] < CACHE_MAX_AGE_SECONDS:
            return finish(cached[0], "cache")

        if os.environ.get("WQT_OFFLINE") != "1":
            try:
                result, degraded = fetch()
                # WHY the systems check: an empty answer cached for a week would hide a later good one.
                if not degraded and not result["query"]["not_found"] and result.get("systems"):
                    _cache_put(key, result)
                return finish(result, "live")
            except (EpaApiError, GeoError):
                pass  # fall through to stale cache / fallback

        if cached:  # stale, but better than nothing
            return finish(cached[0], "cache")

        fallback = _fallback_get(key)
        if fallback:
            return finish(fallback, "fallback")
    except Exception:  # last-resort guard: the contract says we never raise
        pass
    return empty


def lookup_zip(zip_code):
    """Return a LookupResult for a 5-digit zip string. Never raises."""
    zip_code = str(zip_code).strip()
    return _lookup("zip:" + zip_code, lambda: _fetch_zip(zip_code), _empty_zip(zip_code))


def lookup_address(address):
    """Return a LookupResult for a street address. Never raises."""
    address = str(address).strip()
    return _lookup("addr:" + normalize_address(address), lambda: _fetch_address(address),
                   _empty_address(address), label_input=address)
