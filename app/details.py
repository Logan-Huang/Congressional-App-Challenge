"""Extra context for one water system: comparisons, state data, lead-pipe signals.

get_details() builds three independent parts. If one part fails it becomes
None / [] and the other parts still work (see docs/DATA_CONTRACT.md, Layer 1b).

Where each part comes from:
  comparison   -> data/state_stats.json (bundled) + a live "nearby systems" lookup
  state_report -> app/state_sources (e.g. California SAFER)
  lead_pipes   -> data/housing_age.csv (bundled) + data/state_lsl_estimates.json (optional)
Live lookups fall back to data/details_fallback.json (a snapshot of demo systems).
"""

import csv
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import requests

from . import epa_client, rules, state_sources
from .geo import BOUNDARY_URL  # one copy of the EPA boundary-service URL

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DB = DATA_DIR / "cache.sqlite3"
CACHE_DAYS = 7

MAX_NEIGHBORS = 8
NEIGHBOR_MILES = 5
LIVE_BUDGET_SECONDS = 8  # keep the whole live lookup short; the page should not hang

# Same meaning as data_store.py: O open, K known, R resolved.
_STATUS_MAP = {"O": "open", "K": "known", "R": "resolved"}


def _offline():
    return os.environ.get("WQT_OFFLINE") == "1"


# ---------------------------------------------------------------- bundled files

_files = {}  # loaded once, then reused


def _load_json(name):
    if name not in _files:
        try:
            _files[name] = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _files[name] = {}
    return _files[name]


def _housing_table():
    """{('tract' or 'zip', geoid): median_year_built}"""
    if "housing" not in _files:
        table = {}
        try:
            with open(DATA_DIR / "housing_age.csv", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    table[(row["geo_type"], row["geoid"])] = int(row["median_year_built"])
        except (OSError, ValueError, KeyError):
            pass
        _files["housing"] = table
    return _files["housing"]


# ---------------------------------------------------------------- part 1: comparison

def state_comparison(state):
    """State + national rates from the bundled stats file (no network). None if unavailable."""
    stats = _load_json("state_stats.json")
    state_row = (stats.get("states") or {}).get((state or "").upper())
    national = stats.get("national")
    if not state_row or not national:
        return None
    return {
        "state": {
            "code": state.upper(), "name": state_row["name"], "systems": state_row["systems"],
            "pct_current_health_based": state_row["pct_current_health_based"],
            "pct_health_based_5yr": state_row["pct_health_based_5yr"],
        },
        "national": {
            "systems": national["systems"],
            "pct_current_health_based": national["pct_current_health_based"],
            "pct_health_based_5yr": national["pct_health_based_5yr"],
        },
        "neighbors": [],
        "as_of": stats.get("as_of"),
    }


def _nearby_systems(pwsid, lat, lon):
    """Boundary query: systems within 5 miles of the point, biggest first, excluding this one."""
    params = {
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "distance": NEIGHBOR_MILES,
        "units": "esriSRUnit_StatuteMile",
        "outFields": "PWSID,PWS_Name,Primacy_Agency,Population_Served_Count",
        "returnGeometry": "false", "f": "json",
        "orderByFields": "Population_Served_Count DESC",
        "resultRecordCount": MAX_NEIGHBORS + 5,  # a few spare in case we drop some
    }
    response = requests.get(BOUNDARY_URL, params=params, timeout=5)
    response.raise_for_status()
    found = []
    for feature in response.json().get("features", []):
        a = feature.get("attributes") or {}
        if a.get("PWSID") and a["PWSID"] != pwsid:
            found.append({"pwsid": a["PWSID"], "name": a.get("PWS_Name"),
                          "population_served": a.get("Population_Served_Count")})
    found.sort(key=lambda s: s["population_served"] or 0, reverse=True)
    return found[:MAX_NEIGHBORS]


def _violation_flags(pwsid):
    """(has_current_health_based, has_current_any) for one system, using rules.is_current."""
    rows = epa_client.query(
        "SDWIS.VIOLATION", [("pwsid", "equals", pwsid), ("compliance_status_code", "notEquals", "R")],
        rows="1:500", timeout=6)
    health = anything = False
    for raw in rows:
        violation = {
            "status": _STATUS_MAP.get(raw.get("compliance_status_code"), "archived"),
            "begin_date": (raw.get("compl_per_begin_date") or "")[:10] or None,
            "is_health_based": raw.get("is_health_based_ind") == "Y",
            "contaminant_code": raw.get("contaminant_code"),
        }
        if rules.is_current(violation):
            anything = True
            health = health or rules.counts_as_health_based(violation)
    return health, anything


def live_neighbors(pwsid, lat, lon):
    """Nearby systems with their current-violation flags. Raises if the boundary lookup fails."""
    neighbors = _nearby_systems(pwsid, lat, lon)

    def add_flags(neighbor):
        try:
            health, anything = _violation_flags(neighbor["pwsid"])
        except Exception:
            health = anything = None  # unknown for this one neighbor; keep the rest
        return {**neighbor, "has_current_health_based": health, "has_current_any": anything}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(add_flags, neighbors))


# ---------------------------------------------------------------- part 3: lead pipes

def lead_pipes(state, zip_code, tract):
    """Housing-age hint (tract preferred over zip) and the state's lead-line estimate, if known."""
    housing = None
    table = _housing_table()
    if tract and ("tract", tract) in table:
        housing = {"geo": "tract", "median_year_built": table[("tract", tract)]}
    elif zip_code and ("zip", zip_code) in table:
        housing = {"geo": "zip", "median_year_built": table[("zip", zip_code)]}

    estimate = None
    lsl = _load_json("state_lsl_estimates.json")
    entry = (lsl.get("states") or {}).get((state or "").upper())
    if entry and entry.get("count") is not None:
        estimate = {"count": entry["count"], "source": entry.get("source") or lsl.get("source")}
    return {"housing": housing, "state_lsl_estimate": estimate}


# ---------------------------------------------------------------- cache (SQLite, 7 days)

def _cache_connect():
    conn = sqlite3.connect(CACHE_DB, timeout=3)
    conn.execute("CREATE TABLE IF NOT EXISTS details (key TEXT PRIMARY KEY, payload TEXT, fetched_at REAL)")
    return conn


def _cache_get(key):
    try:
        conn = _cache_connect()
        try:
            row = conn.execute("SELECT payload, fetched_at FROM details WHERE key=?", (key,)).fetchone()
        finally:
            conn.close()
        if row and time.time() - row[1] < CACHE_DAYS * 86400:
            return json.loads(row[0])
    except (sqlite3.Error, ValueError):
        pass
    return None


def _cache_put(key, details):
    try:
        conn = _cache_connect()
        try:
            with conn:
                conn.execute("INSERT OR REPLACE INTO details VALUES (?,?,?)",
                             (key, json.dumps(details), time.time()))
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # the cache is only a speed-up


# ---------------------------------------------------------------- main entry point

def _snapshot(pwsid):
    return (_load_json("details_fallback.json") or {}).get(pwsid) or {}


def _empty_details():
    return {"comparison": None, "state_report": None,
            "lead_pipes": {"housing": None, "state_lsl_estimate": None}}


def get_details(pwsid, state, lat=None, lon=None, zip_code=None, tract=None):
    """Build the Details dict from the contract. Never raises."""
    try:
        return _get_details(pwsid, state, lat, lon, zip_code, tract)
    except Exception:
        return _empty_details()


def _fetch_report(state, pwsid):
    """(report, ok). ok is False when the state site failed, so we skip caching."""
    try:
        return state_sources.get_adapter(state).fetch(pwsid), True
    except Exception:
        return None, False


def _get_details(pwsid, state, lat, lon, zip_code, tract):
    state = (state or (pwsid or "")[:2]).upper()
    snap = _snapshot(pwsid)

    # Bundled parts: no network, so they cannot be slow.
    try:
        comparison = state_comparison(state)
    except Exception:
        comparison = None
    try:
        pipes = lead_pipes(state, zip_code, tract)
    except Exception:
        pipes = {"housing": None, "state_lsl_estimate": None}

    if _offline():
        if comparison is not None:
            comparison["neighbors"] = snap.get("neighbors") or []
        return {"comparison": comparison, "state_report": snap.get("state_report"), "lead_pipes": pipes}

    cache_key = f"{pwsid}|{zip_code or ''}|{tract or ''}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    # The two live parts run at the same time, so the slowest one sets the total time.
    all_ok = True
    has_adapter = state_sources.get_adapter(state) is not None
    neighbors = report = None
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        neighbors_job = (pool.submit(live_neighbors, pwsid, lat, lon)
                         if lat is not None and lon is not None else None)
        report_job = pool.submit(_fetch_report, state, pwsid) if has_adapter else None
        # ONE shared deadline: both jobs started together, so wait for them together.
        jobs = [j for j in (neighbors_job, report_job) if j is not None]
        wait(jobs, timeout=LIVE_BUDGET_SECONDS)
        if neighbors_job is not None:
            try:
                neighbors = neighbors_job.result(timeout=0)
            except Exception:
                all_ok = False
        if report_job is not None:
            try:
                report, report_ok = report_job.result(timeout=0)
                all_ok = all_ok and report_ok
            except Exception:
                all_ok = False
    finally:
        pool.shutdown(wait=False)

    # A failed live part falls back to the demo snapshot (if this system is in it).
    if neighbors is None:
        neighbors = snap.get("neighbors") or []
    if report is None and has_adapter and not all_ok:
        report = snap.get("state_report")
    if comparison is not None:
        comparison["neighbors"] = neighbors

    details = {"comparison": comparison, "state_report": report, "lead_pipes": pipes}
    if any(n.get("has_current_health_based") is None for n in neighbors):
        all_ok = False  # a neighbor's lookup failed (maybe rate-limited): do not cache "unknown"
    if all_ok and lat is not None and lon is not None:
        _cache_put(cache_key, details)  # do not cache half-failed answers
    return details
