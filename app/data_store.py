"""Zip code -> water systems + violations (the data layer).

Public entry point: lookup_zip(zip). Its return shape is defined in
docs/DATA_CONTRACT.md. It NEVER raises; the source chain is:

    fresh cache (< 7 days) -> live EPA API -> stale cache -> data/fallback.json -> empty

Set env var WQT_OFFLINE=1 to skip the live API (demo safety switch).
"""

import csv
import json
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from app import epa_client
from app.epa_client import EpaApiError

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
ZIP_CSV_PATH = os.path.join(DATA_DIR, "zip_to_city.csv")
FALLBACK_PATH = os.path.join(DATA_DIR, "fallback.json")
CACHE_PATH = os.path.join(DATA_DIR, "cache.sqlite3")

CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
MAX_SYSTEMS = 10          # more than this is noise for a parent reading the page
MAX_CANDIDATES = 25       # how many systems we fetch details for before trimming to 10
LOOKUP_DEADLINE_SECONDS = 20  # whole live lookup; after this we use cache/fallback instead
MAX_OLD_VIOLATIONS = 5    # non-open violations kept per system (open ones are always kept)

# Lower number = better match. Used for dedupe and sorting.
MATCH_RANK = {"service_area": 0, "city_served": 1, "admin_address": 2}

VIOLATION_CATEGORIES = {"MCL", "MRDL", "TT", "MR", "MON", "RPT"}

# EPA compliance_status_code. R = returned to compliance. K = EPA's "addressed/known"
# (enforcement taken or old, but never marked resolved; the contract calls it "archived").
# Only O counts as a current, open violation.
STATUS_MAP = {"O": "open", "R": "resolved", "K": "archived"}

_zip_table = None  # loaded once, on first use


# --------------------------------------------------------------------------
# zip -> (city, state)
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
    """Keep every open violation plus the 5 most recent others, newest first."""
    # Missing dates sort as "" so they land last when newest-first.
    newest_first = sorted(violations, key=lambda v: v["begin_date"] or "", reverse=True)
    open_ones = [v for v in newest_first if v["status"] == "open"]
    others = [v for v in newest_first if v["status"] != "open"][:MAX_OLD_VIOLATIONS]
    return sorted(open_ones + others, key=lambda v: v["begin_date"] or "", reverse=True)


def _latest_lead_90th(results, samples):
    """Most recent lead 90th-percentile result from the two raw tables, or None."""
    # The results table has no dates; the samples table does. Join on sample_id.
    end_dates = {s.get("sample_id"): _date(s.get("sampling_end_date")) for s in samples}
    best = None
    for r in results:
        value = _float(r.get("sample_measure"))
        sample_date = end_dates.get(r.get("sample_id"))
        if value is None or sample_date is None:
            continue
        if best is None or sample_date > best["sample_date"]:
            best = {"value_mg_l": value, "sample_date": sample_date}
    return best


# --------------------------------------------------------------------------
# Live lookup
# --------------------------------------------------------------------------

def _run_parallel(func, items, workers, deadline):
    """Like pool.map, but gives up (EpaApiError) once `deadline` (a time.monotonic() value) passes.

    WHY: each request has its own timeout, but several rounds of them add up.
    The user would stare at a spinner, so we stop and let lookup_zip fall back.
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


def _find_candidates(zip_code, city, state, deadline=None):
    """Run the three match strategies; return {pwsid: (match_type, details_row_or_None)}."""
    if deadline is None:
        deadline = time.monotonic() + LOOKUP_DEADLINE_SECONDS
    calls = [
        lambda: epa_client.systems_by_zip_served(zip_code),
        lambda: epa_client.systems_by_city_served(city, state),
        lambda: epa_client.systems_by_admin_zip(zip_code, state),
    ]
    rows_per_strategy = _run_parallel(lambda call: call(), calls, 3, deadline)
    by_strategy = list(zip(["service_area", "city_served", "admin_address"], rows_per_strategy))

    candidates = {}
    for match_type, rows in by_strategy:
        for row in rows:
            # Same-state filter: a management company's office can sit in this zip
            # while the systems it runs are in other states.
            if row.get("primacy_agency_code") != state:
                continue
            if row.get("pws_type_code") != "CWS" or row.get("pws_activity_code") != "A":
                continue
            # Admin-address rows are full WATER_SYSTEM rows, so we keep them as details.
            details = row if "pws_name" in row else None
            old = candidates.get(row["pwsid"])
            if old is None:
                candidates[row["pwsid"]] = (match_type, details)
            else:
                # Strategies run best-first, so the existing match_type stays.
                candidates[row["pwsid"]] = (old[0], old[1] or details)
    return candidates


def _fetch_details(pwsid, candidate):
    """Return (pwsid, match_type, details_row), or None if not an active CWS."""
    match_type, details = candidate
    if details is None:
        rows = epa_client.water_system_details(pwsid)
        if not rows:
            return None
        details = rows[0]
    if details.get("pws_type_code") != "CWS" or details.get("pws_activity_code") != "A":
        return None
    return (pwsid, match_type, details)


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


def _build_system(pwsid, match_type, details, city):
    """Fetch violations (and lead) for one system.

    Returns (WaterSystem, serves_city) where serves_city is True/False/None and is
    only checked for admin-address matches (see fetch_live).
    """
    # Independent ~3s requests: run them side by side.
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_viol = pool.submit(epa_client.violations_by_pwsid, pwsid)
        f_lead = pool.submit(epa_client.lead_90th_results, pwsid)
        f_samples = pool.submit(epa_client.lead_samples, pwsid)
        f_areas = pool.submit(epa_client.service_areas, pwsid) if match_type == "admin_address" else None
        violations = [normalize_violation(v) for v in f_viol.result()]
        try:
            lead = _latest_lead_90th(f_lead.result(), f_samples.result())
        except EpaApiError:
            lead = None  # lead is optional; it must never fail the whole lookup
        serves_city = None
        if f_areas is not None:
            try:
                serves_city = _serves_city(f_areas.result(), city)
            except EpaApiError:
                serves_city = None  # unknown: keep the match rather than fail the lookup
    if serves_city:
        # Its office is in this zip AND it lists this city as served: a real match.
        match_type = "city_served"
    population = details.get("population_served_count")
    system = {
        "pwsid": pwsid,
        "name": details.get("pws_name"),
        "city": details.get("city_name"),
        "state": details.get("state_code") or details.get("primacy_agency_code"),
        "population_served": int(population) if population is not None else None,
        "match_type": match_type,
        "total_violation_count": len(violations),
        "violations": trim_violations(violations),
        "lead_90th": lead,
    }
    return system, serves_city


def fetch_live(zip_code):
    """Full live lookup. Raises EpaApiError if ANY sub-request fails."""
    place = zip_to_city_state(zip_code)
    if place is None:  # not a real zip: nothing to ask EPA about
        return {"zip": zip_code, "source": "live", "systems": []}
    city, state = place
    deadline = time.monotonic() + LOOKUP_DEADLINE_SECONDS

    candidates = _find_candidates(zip_code, city, state, deadline)

    # Strategies 1 and 2 return no population, so we need each system's details
    # to rank it. Best matches first, so a big city never makes us fetch hundreds.
    ordered = sorted(candidates.items(), key=lambda item: MATCH_RANK[item[1][0]])
    ordered = ordered[:MAX_CANDIDATES]
    detailed = _run_parallel(lambda item: _fetch_details(*item), ordered, 8, deadline)
    detailed = [d for d in detailed if d is not None]
    detailed.sort(key=lambda d: (MATCH_RANK[d[1]], -(d[2].get("population_served_count") or 0)))
    top = detailed[:MAX_SYSTEMS]

    built = _run_parallel(lambda d: _build_system(*d, city), top, 8, deadline)

    # An office in this zip whose service area lists only OTHER towns is usually a
    # management company, not this zip's water supplier (e.g. a Trenton zip
    # returning Maple Shade). Drop those, unless that would leave nothing at all.
    confirmed = [system for system, serves_city in built if serves_city is not False]
    systems = confirmed or [system for system, _ in built]
    systems.sort(key=lambda s: (MATCH_RANK[s["match_type"]], -(s["population_served"] or 0)))
    return {"zip": zip_code, "source": "live", "systems": systems}


# --------------------------------------------------------------------------
# Cache (SQLite) and fallback file
# --------------------------------------------------------------------------

def _cache_connect():
    # New connection per call: Flask is threaded and sqlite3 connections are not shareable.
    conn = sqlite3.connect(CACHE_PATH, timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lookups (zip TEXT PRIMARY KEY, fetched_at REAL, payload TEXT)"
    )
    return conn


def _cache_get(zip_code):
    """Return (result, age_in_seconds) or None. Any cache trouble just means 'no cache'."""
    try:
        conn = _cache_connect()
        try:
            row = conn.execute(
                "SELECT fetched_at, payload FROM lookups WHERE zip = ?", (zip_code,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return json.loads(row[1]), time.time() - row[0]
    except (sqlite3.Error, OSError, ValueError):
        return None


def _cache_put(zip_code, result):
    try:
        conn = _cache_connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO lookups (zip, fetched_at, payload) VALUES (?, ?, ?)",
                    (zip_code, time.time(), json.dumps(result)),
                )
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        pass  # failing to cache must never break a lookup


def _fallback_get(zip_code):
    try:
        with open(FALLBACK_PATH, encoding="utf-8") as f:
            return json.load(f).get(zip_code)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def lookup_zip(zip_code):
    """Return a LookupResult for a 5-digit zip string. Never raises."""
    zip_code = str(zip_code).strip()
    try:
        cached = _cache_get(zip_code)
        if cached and cached[1] < CACHE_MAX_AGE_SECONDS:
            return {**cached[0], "source": "cache"}

        if os.environ.get("WQT_OFFLINE") != "1":
            try:
                result = fetch_live(zip_code)
                _cache_put(zip_code, result)
                return result
            except EpaApiError:
                pass  # fall through to stale cache / fallback

        if cached:  # stale, but better than nothing
            return {**cached[0], "source": "cache"}

        fallback = _fallback_get(zip_code)
        if fallback:
            return {**fallback, "source": "fallback"}
    except Exception:  # last-resort guard: the contract says we never raise
        pass
    return {"zip": zip_code, "source": "fallback", "systems": []}
