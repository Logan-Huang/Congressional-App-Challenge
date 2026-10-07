"""Tests for the data layer. The EPA client is faked, so no network is used.

Set WQT_LIVE_TESTS=1 to also run one real-API smoke test.
"""

import json
import os
import time
from datetime import date

import pytest

from app import data_store, epa_client, geo, rules
from app.epa_client import EpaApiError


# --------------------------------------------------------------------------
# Fake EPA data
# --------------------------------------------------------------------------

def geo_row(pwsid, state="MI"):
    return {"pwsid": pwsid, "primacy_agency_code": state, "pws_type_code": "CWS", "pws_activity_code": "A"}


def system_row(pwsid, name, pop, state="MI", city="FLINT"):
    return {
        "pwsid": pwsid, "pws_name": name, "city_name": city, "state_code": state,
        "primacy_agency_code": state, "population_served_count": pop,
        "pws_type_code": "CWS", "pws_activity_code": "A",
    }


def raw_violation(vid, status="O", begin="2024-01-01 00:00:00", health="N", category="MR", **extra):
    row = {
        "violation_id": vid, "compliance_status_code": status, "compl_per_begin_date": begin,
        "compl_per_end_date": None, "rtc_date": None, "is_health_based_ind": health,
        "violation_category_code": category, "violation_code": "03", "contaminant_code": "5000",
        "rule_code": "350", "viol_measure": None, "unit_of_measure": None, "state_mcl": None,
        "public_notification_tier": 3,
    }
    row.update(extra)
    return row


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own empty cache and fallback, and a tiny zip table."""
    monkeypatch.setattr(data_store, "CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    monkeypatch.setattr(data_store, "FALLBACK_PATH", str(tmp_path / "fallback.json"))
    monkeypatch.setattr(data_store, "_zip_table", {"48502": ("FLINT", "MI"), "10001": ("NEW YORK", "NY")})
    monkeypatch.setattr(data_store, "_zip_coords", {"48502": (43.0151, -83.6948), "10001": (40.7484, -73.9967)})
    monkeypatch.delenv("WQT_OFFLINE", raising=False)


@pytest.fixture
def fake_epa(monkeypatch):
    """Install a fake EPA. Returns a dict of knobs the test can change."""
    fake = {
        "zip_served": [], "city_served": [], "admin": [],
        "details": {}, "violations": {}, "lead_results": {}, "lead_samples": {}, "areas": {}, "calls": [],
        # Map/geocoder side: what a point query returns, outlines per pwsid, geocoder answer.
        "points": [], "outlines": {}, "geocode": None, "point_calls": [],
    }

    def zip_served(zip_code):
        fake["calls"].append("zip_served")
        return fake["zip_served"]

    def city_served(city, state):
        fake["calls"].append("city_served")
        return fake["city_served"]

    def admin(zip_code, state):
        fake["calls"].append("admin")
        return fake["admin"]

    def details(pwsid):
        return [fake["details"][pwsid]] if pwsid in fake["details"] else []

    monkeypatch.setattr(epa_client, "systems_by_zip_served", zip_served)
    monkeypatch.setattr(epa_client, "systems_by_city_served", city_served)
    monkeypatch.setattr(epa_client, "systems_by_admin_zip", admin)
    monkeypatch.setattr(epa_client, "water_system_details", details)
    monkeypatch.setattr(epa_client, "violations_by_pwsid", lambda p: fake["violations"].get(p, []))
    monkeypatch.setattr(epa_client, "lead_90th_results", lambda p: fake["lead_results"].get(p, []))
    monkeypatch.setattr(epa_client, "lead_samples", lambda p: fake["lead_samples"].get(p, []))
    monkeypatch.setattr(
        epa_client, "service_areas",
        lambda p: [{"pwsid": p, "city_served": c} for c in fake["areas"].get(p, [])],
    )

    def systems_at_point(lat, lon):
        fake["point_calls"].append((lat, lon))
        return fake["points"]

    monkeypatch.setattr(geo, "systems_at_point", systems_at_point)
    monkeypatch.setattr(geo, "boundary_outline", lambda p: fake["outlines"].get(p))
    monkeypatch.setattr(geo, "geocode_address", lambda a: fake["geocode"])
    return fake


def add_system(fake, pwsid, name, pop, **kwargs):
    fake["details"][pwsid] = system_row(pwsid, name, pop, **kwargs)


# --------------------------------------------------------------------------
# Matching, filtering, sorting
# --------------------------------------------------------------------------

def test_same_state_filter_drops_out_of_state_systems(fake_epa):
    add_system(fake_epa, "MI1", "FLINT, CITY OF", 80000)
    add_system(fake_epa, "OH1", "TRAILER PARK", 50, state="OH")
    fake_epa["city_served"] = [geo_row("MI1")]
    fake_epa["admin"] = [system_row("OH1", "TRAILER PARK", 50, state="OH")]
    result = data_store.lookup_zip("48502")
    assert [s["pwsid"] for s in result["systems"]] == ["MI1"]


def test_non_cws_or_inactive_rows_are_ignored(fake_epa):
    add_system(fake_epa, "MI1", "GOOD", 100)
    bad = geo_row("MI2")
    bad["pws_type_code"] = "TNCWS"
    inactive = geo_row("MI3")
    inactive["pws_activity_code"] = "I"
    fake_epa["city_served"] = [geo_row("MI1"), bad, inactive]
    result = data_store.lookup_zip("48502")
    assert [s["pwsid"] for s in result["systems"]] == ["MI1"]


def test_dedupe_keeps_best_match_type(fake_epa):
    add_system(fake_epa, "MI1", "FLINT, CITY OF", 80000)
    fake_epa["zip_served"] = [geo_row("MI1")]
    fake_epa["city_served"] = [geo_row("MI1")]
    fake_epa["admin"] = [system_row("MI1", "FLINT, CITY OF", 80000)]
    systems = data_store.lookup_zip("48502")["systems"]
    assert len(systems) == 1
    assert systems[0]["match_type"] == "service_area"


def test_sorted_by_match_quality_then_population(fake_epa):
    add_system(fake_epa, "A", "BIG CITY SYSTEM", 90000)
    add_system(fake_epa, "B", "SMALL CITY SYSTEM", 500)
    add_system(fake_epa, "C", "SERVICE AREA TINY", 10)
    fake_epa["city_served"] = [geo_row("B"), geo_row("A")]
    fake_epa["zip_served"] = [geo_row("C")]
    fake_epa["admin"] = [system_row("D", "ADMIN HUGE", 1000000)]
    order = [(s["pwsid"], s["match_type"]) for s in data_store.lookup_zip("48502")["systems"]]
    assert order == [("C", "service_area"), ("A", "city_served"), ("B", "city_served"), ("D", "admin_address")]


def test_admin_match_serving_other_towns_is_dropped(fake_epa):
    # A Trenton-style zip: the office of a system serving a different town sits here.
    add_system(fake_epa, "MI1", "FLINT, CITY OF", 80000)
    add_system(fake_epa, "MI2", "FAR AWAY TWP", 19000)
    fake_epa["city_served"] = [geo_row("MI1")]
    fake_epa["admin"] = [system_row("MI2", "FAR AWAY TWP", 19000)]
    fake_epa["areas"]["MI2"] = ["FAR AWAY TWP.-0319"]
    systems = data_store.lookup_zip("48502")["systems"]
    assert [s["pwsid"] for s in systems] == ["MI1"]


def test_admin_match_listing_the_city_is_upgraded(fake_epa):
    add_system(fake_epa, "MI1", "FLINT WATER WORKS", 80000)
    fake_epa["admin"] = [system_row("MI1", "FLINT WATER WORKS", 80000)]
    fake_epa["areas"]["MI1"] = ["EWING TWP.-1102,FLINT CITY-1111"]
    systems = data_store.lookup_zip("48502")["systems"]
    assert systems[0]["match_type"] == "city_served"


def test_admin_matches_kept_when_dropping_would_leave_nothing(fake_epa):
    # e.g. a neighborhood zip ("ROXBURY") whose only match lists the bigger city.
    add_system(fake_epa, "MI1", "ELSEWHERE", 500)
    fake_epa["admin"] = [system_row("MI1", "ELSEWHERE", 500)]
    fake_epa["areas"]["MI1"] = ["ELSEWHERE"]
    systems = data_store.lookup_zip("48502")["systems"]
    assert [(s["pwsid"], s["match_type"]) for s in systems] == [("MI1", "admin_address")]


def test_admin_match_with_no_listed_cities_is_kept(fake_epa):
    add_system(fake_epa, "MI1", "FLINT, CITY OF", 80000)
    add_system(fake_epa, "MI2", "PARISH DISTRICT", 70000)
    fake_epa["city_served"] = [geo_row("MI1")]
    fake_epa["admin"] = [system_row("MI2", "PARISH DISTRICT", 70000)]
    systems = data_store.lookup_zip("48502")["systems"]
    assert [s["pwsid"] for s in systems] == ["MI1", "MI2"]


def test_caps_at_ten_systems(fake_epa):
    rows = []
    for i in range(15):
        add_system(fake_epa, f"MI{i:02d}", f"SYS {i}", 1000 + i)
        rows.append(geo_row(f"MI{i:02d}"))
    fake_epa["city_served"] = rows
    systems = data_store.lookup_zip("48502")["systems"]
    assert len(systems) == 10
    assert systems[0]["population_served"] == 1014  # the biggest ones are kept


def test_live_with_zero_systems_is_a_valid_answer(fake_epa):
    result = data_store.lookup_zip("48502")
    assert result["source"] == "live"
    assert result["systems"] == []


def test_unknown_zip_returns_empty_without_calling_epa(fake_epa):
    result = data_store.lookup_zip("99999")
    assert result["systems"] == []
    assert fake_epa["calls"] == []


# --------------------------------------------------------------------------
# Violations
# --------------------------------------------------------------------------

def test_violation_normalization():
    v = data_store.normalize_violation(raw_violation(
        486, status="R", begin="2020-07-01 00:00:00", health="Y", category="MCL",
        compl_per_end_date="2020-12-31 00:00:00", rtc_date="2021-01-15 00:00:00",
        viol_measure=41, unit_of_measure="mg/L", state_mcl=0.01, public_notification_tier=1,
    ))
    assert v == {
        "violation_id": "486", "contaminant_code": "5000", "violation_code": "03",
        "category_code": "MCL", "rule_code": "350", "is_health_based": True,
        "status": "resolved", "begin_date": "2020-07-01", "end_date": "2020-12-31",
        "returned_to_compliance_date": "2021-01-15", "measure": 41.0, "unit": "mg/L",
        "state_mcl": 0.01, "notification_tier": 1,
    }


def test_status_codes_and_unknown_values():
    norm = data_store.normalize_violation
    assert norm(raw_violation(1, status="O"))["status"] == "open"
    assert norm(raw_violation(1, status="R"))["status"] == "resolved"
    assert norm(raw_violation(1, status="K"))["status"] == "known"
    assert norm(raw_violation(1, status="I"))["status"] == "archived"
    odd = norm(raw_violation(1, category="XYZ", public_notification_tier=None, begin=None))
    assert odd["category_code"] == "Other"
    assert odd["notification_tier"] is None
    assert odd["begin_date"] is None


def test_keeps_all_open_plus_five_most_recent_others(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]
    rows = [raw_violation(f"old{i}", status="R", begin=f"20{10 + i}-01-01 00:00:00") for i in range(8)]
    rows += [raw_violation("open_old", status="O", begin="2001-01-01 00:00:00"),
             raw_violation("open_new", status="O", begin="2024-06-01 00:00:00")]
    fake_epa["violations"]["MI1"] = rows
    system = data_store.lookup_zip("48502")["systems"][0]
    ids = [v["violation_id"] for v in system["violations"]]
    assert system["total_violation_count"] == 10
    # 2 open + 5 newest non-open (old7..old3), newest first overall
    assert ids == ["open_new", "old7", "old6", "old5", "old4", "old3", "open_old"]


def test_lead_90th_joins_result_to_sample_date(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]
    fake_epa["lead_results"]["MI1"] = [
        {"sample_id": "S1", "sample_measure": 0.01}, {"sample_id": "S2", "sample_measure": 0.004},
        {"sample_id": "S3", "sample_measure": 0.5},  # no matching sample row -> ignored
    ]
    fake_epa["lead_samples"]["MI1"] = [
        {"sample_id": "S1", "sampling_end_date": "2019-06-30 00:00:00"},
        {"sample_id": "S2", "sampling_end_date": "2023-12-31 00:00:00"},
    ]
    system = data_store.lookup_zip("48502")["systems"][0]
    assert system["lead_90th"] == {"value_mg_l": 0.004, "sample_date": "2023-12-31"}


def test_lead_failure_does_not_fail_lookup(fake_epa, monkeypatch):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]

    def boom(pwsid):
        raise EpaApiError("lead table down")

    monkeypatch.setattr(epa_client, "lead_90th_results", boom)
    result = data_store.lookup_zip("48502")
    assert result["source"] == "live"
    assert result["systems"][0]["lead_90th"] is None


# --------------------------------------------------------------------------
# Source chain: cache -> live -> stale cache -> fallback -> empty
# --------------------------------------------------------------------------

def make_down(monkeypatch):
    def down(*args, **kwargs):
        raise EpaApiError("EPA is down")
    monkeypatch.setattr(epa_client, "systems_by_zip_served", down)


def write_fallback(zip_code="48502"):
    payload = {"zip:" + zip_code: {"zip": zip_code, "source": "fallback", "systems": [{"pwsid": "FB1"}]}}
    with open(data_store.FALLBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_second_lookup_is_a_cache_hit(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]
    assert data_store.lookup_zip("48502")["source"] == "live"
    fake_epa["calls"].clear()
    second = data_store.lookup_zip("48502")
    assert second["source"] == "cache"
    assert second["systems"][0]["pwsid"] == "MI1"
    assert fake_epa["calls"] == []  # no EPA traffic on a cache hit


def test_api_error_falls_back_to_fallback_file(fake_epa, monkeypatch):
    make_down(monkeypatch)
    write_fallback()
    result = data_store.lookup_zip("48502")
    assert result["source"] == "fallback"
    assert result["systems"][0]["pwsid"] == "FB1"


def test_api_error_prefers_stale_cache_over_fallback(fake_epa, monkeypatch):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]
    data_store.lookup_zip("48502")  # fills the cache
    # Age the cache entry past 7 days.
    old = time.time() - 8 * 24 * 3600
    conn = data_store._cache_connect()
    with conn:
        conn.execute("UPDATE lookups SET fetched_at = ?", (old,))
    conn.close()
    make_down(monkeypatch)
    write_fallback()
    result = data_store.lookup_zip("48502")
    assert result["source"] == "cache"
    assert result["systems"][0]["pwsid"] == "MI1"


def test_stale_cache_triggers_live_refresh(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]
    data_store.lookup_zip("48502")
    conn = data_store._cache_connect()
    with conn:
        conn.execute("UPDATE lookups SET fetched_at = ?", (time.time() - 8 * 24 * 3600,))
    conn.close()
    assert data_store.lookup_zip("48502")["source"] == "live"


def test_partial_live_failure_fails_whole_lookup(fake_epa, monkeypatch):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]

    def bad_violations(pwsid):
        raise EpaApiError("timeout")

    monkeypatch.setattr(epa_client, "violations_by_pwsid", bad_violations)
    write_fallback()
    assert data_store.lookup_zip("48502")["source"] == "fallback"


def test_no_cache_no_fallback_gives_empty_result(fake_epa, monkeypatch):
    make_down(monkeypatch)
    result = data_store.lookup_zip("48502")
    assert result["systems"] == []
    assert result["zip"] == "48502"


def test_offline_mode_skips_live_api(fake_epa, monkeypatch):
    monkeypatch.setenv("WQT_OFFLINE", "1")
    write_fallback()
    result = data_store.lookup_zip("48502")
    assert result["source"] == "fallback"
    assert fake_epa["calls"] == []


def test_offline_mode_still_uses_cache(fake_epa, monkeypatch):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]
    data_store.lookup_zip("48502")
    monkeypatch.setenv("WQT_OFFLINE", "1")
    assert data_store.lookup_zip("48502")["source"] == "cache"


def test_lookup_never_raises(fake_epa, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("unexpected bug")
    monkeypatch.setattr(epa_client, "systems_by_zip_served", explode)
    assert data_store.lookup_zip("48502")["systems"] == []


# --------------------------------------------------------------------------
# epa_client error handling (fake HTTP session)
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, body=None, text_ok=True):
        self.status_code = status
        self._body = body
        self._text_ok = text_ok

    def json(self):
        if not self._text_ok:
            raise ValueError("not json")
        return self._body


@pytest.mark.parametrize("response", [
    FakeResponse(status=500),
    FakeResponse(text_ok=False),
    FakeResponse(body={"error": "column does not exist"}),
])
def test_epa_client_raises_on_bad_responses(monkeypatch, response):
    monkeypatch.setattr(epa_client._session, "get", lambda url, timeout: response)
    with pytest.raises(EpaApiError):
        epa_client.query("SDWIS.WATER_SYSTEM", [("pwsid", "equals", "X")])


def test_epa_client_raises_on_timeout_and_encodes_values(monkeypatch):
    import requests
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        raise requests.Timeout("slow")

    monkeypatch.setattr(epa_client._session, "get", fake_get)
    with pytest.raises(EpaApiError):
        epa_client.query("SDWIS.GEOGRAPHIC_AREA", [("city_served", "equals", "NEW YORK")], rows="1:5")
    assert seen["url"].endswith("/SDWIS.GEOGRAPHIC_AREA/city_served/equals/NEW%20YORK/1:5/JSON")


# --------------------------------------------------------------------------
# Optional live smoke test
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("WQT_LIVE_TESTS") != "1", reason="set WQT_LIVE_TESTS=1 to hit the real EPA API")
def test_live_flint_smoke(monkeypatch):
    monkeypatch.setattr(data_store, "_zip_table", None)  # use the real CSV
    result = data_store.lookup_zip("48502")
    assert result["source"] == "live"
    assert any(s["pwsid"] == "MI0002310" for s in result["systems"])


def test_live_lookup_gives_up_after_deadline(fake_epa, monkeypatch):
    import threading
    release = threading.Event()
    monkeypatch.setattr(data_store, "LOOKUP_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(epa_client, "systems_by_zip_served", lambda z: release.wait(5) or [])
    try:
        with pytest.raises(EpaApiError):
            data_store.fetch_live("48502")
    finally:
        release.set()


# --------------------------------------------------------------------------
# Boundary matching (v2)
# --------------------------------------------------------------------------

def hit(pwsid, state="MI", quality="reported", pop=1000):
    return {"pwsid": pwsid, "name": pwsid, "state": state, "population_served": pop, "quality": quality}


def test_zip_boundary_ranks_first_and_dedupes(fake_epa):
    add_system(fake_epa, "MI1", "FLINT, CITY OF", 80000)
    add_system(fake_epa, "MI2", "BIG COUNTY", 500000)
    fake_epa["points"] = [hit("MI1")]
    fake_epa["city_served"] = [geo_row("MI1"), geo_row("MI2")]  # MI1 matched twice
    fake_epa["outlines"]["MI1"] = [[[0, 0], [1, 0], [1, 1], [0, 0]]]
    systems = data_store.lookup_zip("48502")["systems"]
    assert [s["pwsid"] for s in systems] == ["MI1", "MI2"]  # boundary beats the bigger city match
    assert systems[0]["match_type"] == "zip_boundary"
    assert systems[0]["boundary_quality"] == "reported"
    assert systems[0]["boundary"] == [[[0, 0], [1, 0], [1, 1], [0, 0]]]
    assert systems[1]["match_type"] == "city_served"
    assert systems[1]["boundary_quality"] is None and systems[1]["boundary"] is None
    assert fake_epa["point_calls"] == [(43.0151, -83.6948)]


def test_modeled_vs_reported_boundary_quality(fake_epa):
    add_system(fake_epa, "MI1", "A", 10)
    add_system(fake_epa, "MI2", "B", 20)
    fake_epa["points"] = [hit("MI1", quality="modeled"), hit("MI2", quality="reported")]
    by_id = {s["pwsid"]: s for s in data_store.lookup_zip("48502")["systems"]}
    assert by_id["MI1"]["boundary_quality"] == "modeled"
    assert by_id["MI2"]["boundary_quality"] == "reported"


def test_boundary_hit_in_other_state_is_dropped(fake_epa):
    add_system(fake_epa, "OH1", "ACROSS THE LINE", 10, state="OH")
    fake_epa["points"] = [hit("OH1", state="OH")]
    assert data_store.lookup_zip("48502")["systems"] == []


def test_boundary_hit_that_is_not_an_active_cws_is_dropped(fake_epa):
    fake_epa["details"]["MI9"] = {**system_row("MI9", "SCHOOL", 50), "pws_type_code": "NTNCWS"}
    fake_epa["points"] = [hit("MI9")]
    assert data_store.lookup_zip("48502")["systems"] == []


def test_boundary_service_down_still_answers_but_is_not_cached(fake_epa, monkeypatch):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]

    def down(lat, lon):
        raise geo.GeoError("map service down")

    monkeypatch.setattr(geo, "systems_at_point", down)
    first = data_store.lookup_zip("48502")
    assert first["source"] == "live" and first["systems"][0]["pwsid"] == "MI1"
    assert data_store.lookup_zip("48502")["source"] == "live"  # not cached, so it asks again


def test_outline_failure_means_no_boundary_not_a_failed_lookup(fake_epa, monkeypatch):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["points"] = [hit("MI1")]

    def boom(pwsid):
        raise geo.GeoError("no outline")

    monkeypatch.setattr(geo, "boundary_outline", boom)
    system = data_store.lookup_zip("48502")["systems"][0]
    assert system["match_type"] == "zip_boundary" and system["boundary"] is None


def test_zip_query_block(fake_epa):
    query = data_store.lookup_zip("48502")["query"]
    assert query == {"type": "zip", "input": "48502", "matched_address": None, "lat": 43.0151,
                     "lon": -83.6948, "state": "MI", "tract": None, "not_found": False}


# --------------------------------------------------------------------------
# Address lookups
# --------------------------------------------------------------------------

FOUND = {"matched_address": "1101 S SAGINAW ST, FLINT, MI, 48502", "lat": 43.01, "lon": -83.68,
         "zip": "48502", "state": "MI", "tract": "26049002800"}


def test_address_found_uses_address_boundary_only(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 80000)
    add_system(fake_epa, "MI2", "OTHER", 5)
    fake_epa["geocode"] = FOUND
    fake_epa["points"] = [hit("MI1", quality="modeled")]
    fake_epa["city_served"] = [geo_row("MI2")]  # zip strategies must NOT run for a boundary hit
    result = data_store.lookup_address("1101 S Saginaw St, Flint, MI 48502")
    assert result["zip"] == "48502" and result["source"] == "live"
    assert [s["pwsid"] for s in result["systems"]] == ["MI1"]
    assert result["systems"][0]["match_type"] == "address_boundary"
    assert result["systems"][0]["boundary_quality"] == "modeled"
    assert fake_epa["calls"] == []
    assert result["query"] == {
        "type": "address", "input": "1101 S Saginaw St, Flint, MI 48502",
        "matched_address": FOUND["matched_address"], "lat": 43.01, "lon": -83.68,
        "state": "MI", "tract": "26049002800", "not_found": False,
    }


def test_address_not_geocoded_is_not_found(fake_epa):
    result = data_store.lookup_address("zzzz nowhere")
    assert result["systems"] == [] and result["zip"] is None
    assert result["query"]["not_found"] is True
    assert result["query"]["type"] == "address"


def test_address_outside_every_polygon_falls_back_to_zip_search(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 80000)
    fake_epa["geocode"] = FOUND
    fake_epa["points"] = []                       # address point is in a polygon gap
    fake_epa["city_served"] = [geo_row("MI1")]
    result = data_store.lookup_address("1101 S Saginaw St")
    assert result["query"]["type"] == "address"   # stays an address search
    assert result["query"]["matched_address"] == FOUND["matched_address"]
    assert result["query"]["lat"] == 43.01        # the address point, not the zip center
    assert [s["pwsid"] for s in result["systems"]] == ["MI1"]
    assert result["systems"][0]["match_type"] == "city_served"


def test_address_results_are_cached_under_normalized_key(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 80000)
    fake_epa["geocode"] = FOUND
    fake_epa["points"] = [hit("MI1")]
    assert data_store.lookup_address("1101 S Saginaw St, Flint")["source"] == "live"
    second = data_store.lookup_address("  1101   s SAGINAW st,   FLINT ")
    assert second["source"] == "cache"
    assert second["query"]["input"] == "1101   s SAGINAW st,   FLINT"  # shows what was typed this time
    assert data_store._cache_get("addr:1101 s saginaw st, flint") is not None
    assert data_store._cache_get("zip:48502") is None  # zip and address keys never collide


def test_address_not_found_is_not_cached(fake_epa):
    data_store.lookup_address("zzzz nowhere")
    assert data_store._cache_get("addr:zzzz nowhere") is None


def test_geocoder_down_falls_back_to_fallback_file(fake_epa, monkeypatch):
    def down(address):
        raise geo.GeoError("census down")

    monkeypatch.setattr(geo, "geocode_address", down)
    payload = {"addr:1 main st": {"zip": "48502", "source": "fallback", "systems": [{"pwsid": "FB1"}],
                                  "query": {"type": "address", "input": "1 main st", "not_found": False}}}
    with open(data_store.FALLBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    result = data_store.lookup_address("1 Main  St")
    assert result["source"] == "fallback"
    assert result["systems"][0]["pwsid"] == "FB1"
    assert result["query"]["input"] == "1 Main  St"


def test_geocoder_down_and_no_fallback_gives_empty_not_found(fake_epa, monkeypatch):
    def down(address):
        raise geo.GeoError("census down")

    monkeypatch.setattr(geo, "geocode_address", down)
    result = data_store.lookup_address("1 Main St")
    assert result["systems"] == [] and result["query"]["not_found"] is True


def test_address_offline_uses_fallback_and_skips_network(fake_epa, monkeypatch):
    monkeypatch.setenv("WQT_OFFLINE", "1")

    def boom(address):
        raise AssertionError("must not geocode offline")

    monkeypatch.setattr(geo, "geocode_address", boom)
    payload = {"addr:1 main st": {"zip": "48502", "systems": [{"pwsid": "FB1"}]}}
    with open(data_store.FALLBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    assert data_store.lookup_address("1 main st")["systems"][0]["pwsid"] == "FB1"
    assert data_store.lookup_address("something else")["systems"] == []


def test_lookup_address_never_raises(fake_epa, monkeypatch):
    def explode(address):
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr(geo, "geocode_address", explode)
    assert data_store.lookup_address("1 Main St")["systems"] == []


# --------------------------------------------------------------------------
# Violations: "known" handling, history, contact
# --------------------------------------------------------------------------

def test_trim_keeps_every_current_violation_including_recent_known():
    recent = date.today().year - 1
    old = date.today().year - 8
    vs = [data_store.normalize_violation(raw_violation(f"r{i}", status="R", begin=f"{2000 + i}-01-01 00:00:00"))
          for i in range(8)]
    vs.append(data_store.normalize_violation(raw_violation("k_recent", status="K", begin=f"{recent}-02-01 00:00:00")))
    vs.append(data_store.normalize_violation(raw_violation("k_old", status="K", begin=f"{old}-02-01 00:00:00")))
    vs.append(data_store.normalize_violation(raw_violation("o_ancient", status="O", begin="1999-01-01 00:00:00")))
    ids = [v["violation_id"] for v in data_store.trim_violations(vs)]
    assert "k_recent" in ids and "o_ancient" in ids       # current: always kept
    assert "k_old" in ids                                  # not current, but among the 5 newest others
    assert len(ids) == 2 + 5


def test_history_by_year_shape_and_counting():
    today = date(2026, 10, 7)
    raws = [
        raw_violation(1, begin="2026-01-05 00:00:00", health="Y"),
        raw_violation(2, begin="2026-03-05 00:00:00", health="N"),
        raw_violation(3, begin="2017-12-31 00:00:00", health="N", status="R"),
        raw_violation(4, begin="2016-12-31 00:00:00", health="Y", status="R"),   # too old: outside the 10 years
        raw_violation(5, begin=None, health="Y"),                                   # undated: not counted
        raw_violation(6, begin="2020-06-01 00:00:00", health="Y", status="R"),
        raw_violation(7, begin="2020-07-01 00:00:00", health="Y", status="R"),
    ]
    by_year = data_store.history_by_year([data_store.normalize_violation(r) for r in raws], today=today)
    assert [row["year"] for row in by_year] == list(range(2017, 2027))     # exactly 10, ascending, incl. this year
    counts = {row["year"]: (row["health_based"], row["other"]) for row in by_year}
    assert counts[2026] == (1, 1)
    assert counts[2017] == (0, 1)
    assert counts[2020] == (2, 0)
    assert counts[2021] == (0, 0)                                          # zeros filled
    assert sum(h + o for h, o in counts.values()) == 5


def test_history_counts_all_violations_not_just_the_trimmed_ones(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]
    year = date.today().year
    fake_epa["violations"]["MI1"] = [
        raw_violation(f"v{i}", status="R", begin=f"{year - 1}-0{1 + i % 9}-01 00:00:00") for i in range(9)]
    system = data_store.lookup_zip("48502")["systems"][0]
    assert len(system["violations"]) == 5                                  # trimmed...
    by_year = {row["year"]: row for row in system["history"]["by_year"]}
    assert by_year[year - 1]["other"] == 9                                 # ...but history sees all nine
    assert len(system["history"]["by_year"]) == 10


def test_lead_history_only_last_ten_years_ascending(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]
    year = date.today().year
    fake_epa["lead_results"]["MI1"] = [
        {"sample_id": "A", "sample_measure": 0.02}, {"sample_id": "B", "sample_measure": 0.004},
        {"sample_id": "C", "sample_measure": 0.009}, {"sample_id": "D", "sample_measure": None},
    ]
    fake_epa["lead_samples"]["MI1"] = [
        {"sample_id": "A", "sampling_end_date": f"{year - 20}-06-30 00:00:00"},   # too old for history
        {"sample_id": "B", "sampling_end_date": f"{year - 1}-06-30 00:00:00"},
        {"sample_id": "C", "sampling_end_date": f"{year - 5}-06-30 00:00:00"},
        {"sample_id": "D", "sampling_end_date": f"{year - 2}-06-30 00:00:00"},
    ]
    system = data_store.lookup_zip("48502")["systems"][0]
    assert system["history"]["lead_90th"] == [
        {"date": f"{year - 5}-06-30", "value_mg_l": 0.009}, {"date": f"{year - 1}-06-30", "value_mg_l": 0.004}]
    assert system["lead_90th"] == {"value_mg_l": 0.004, "sample_date": f"{year - 1}-06-30"}


def test_contact_never_contains_admin_name(fake_epa):
    add_system(fake_epa, "MI1", "FLINT, CITY OF", 1000)
    fake_epa["details"]["MI1"].update({
        "org_name": "City of Flint", "admin_name": "EDWARDS, CLYDE", "phone_number": "810-766-7346",
        "email_addr": "Water@CityOfFlint.com", "address_line1": "1101 S SAGINAW ST", "address_line2": None,
        "zip_code": "48502"})
    fake_epa["city_served"] = [geo_row("MI1")]
    system = data_store.lookup_zip("48502")["systems"][0]
    assert system["contact"] == {
        "org_name": "City of Flint", "phone": "8107667346", "email": "water@cityofflint.com",
        "address": "1101 S SAGINAW ST, FLINT, MI 48502"}
    assert "EDWARDS" not in json.dumps(system)


def test_contact_uses_system_name_when_org_name_is_the_admin_person():
    contact = data_store.build_contact({
        "org_name": "EDWARDS, CLYDE", "admin_name": "Edwards, Clyde", "pws_name": "FLINT, CITY OF",
        "phone_number": None, "email_addr": "not-an-email"})
    assert contact == {"org_name": "FLINT, CITY OF", "phone": None, "email": None, "address": None}


def test_known_status_is_current_only_when_recent(fake_epa):
    add_system(fake_epa, "MI1", "FLINT", 1000)
    fake_epa["city_served"] = [geo_row("MI1")]
    fake_epa["violations"]["MI1"] = [raw_violation("k", status="K", begin=f"{date.today().year - 1}-01-01 00:00:00")]
    v = data_store.lookup_zip("48502")["systems"][0]["violations"][0]
    assert v["status"] == "known" and rules.is_current(v)


def test_epa_client_retries_once_on_rate_limit(monkeypatch):
    monkeypatch.setattr(epa_client, "RETRY_WAIT_SECONDS", 0)
    answers = [FakeResponse(status=429), FakeResponse(body=[{"pwsid": "X"}])]
    monkeypatch.setattr(epa_client._session, "get", lambda url, timeout: answers.pop(0))
    assert epa_client.query("SDWIS.WATER_SYSTEM", [("pwsid", "equals", "X")]) == [{"pwsid": "X"}]


def test_epa_client_gives_up_after_second_rate_limit(monkeypatch):
    monkeypatch.setattr(epa_client, "RETRY_WAIT_SECONDS", 0)
    monkeypatch.setattr(epa_client._session, "get", lambda url, timeout: FakeResponse(status=429))
    with pytest.raises(EpaApiError):
        epa_client.query("SDWIS.WATER_SYSTEM", [("pwsid", "equals", "X")])


def test_history_counts_lead_inventory_violations_as_paperwork():
    # WHY: EPA flags code 5200 (lead pipe inventory) health-based, but it is record-keeping.
    v = data_store.normalize_violation({
        "violation_id": "1", "contaminant_code": "5200", "violation_code": "2E",
        "violation_category_code": "TT", "is_health_based_ind": "Y", "compliance_status_code": "O",
        "compl_per_begin_date": "2024-03-01"})
    rows = data_store.history_by_year([v], today=date(2025, 6, 1))
    row = [r for r in rows if r["year"] == 2024][0]
    assert row["health_based"] == 0 and row["other"] == 1


def test_offline_address_fallback_accepts_short_forms(monkeypatch, tmp_path):
    monkeypatch.setenv("WQT_OFFLINE", "1")
    monkeypatch.setattr(data_store, "CACHE_PATH", str(tmp_path / "none.sqlite3"))
    # The autouse fixture points FALLBACK_PATH at an empty temp file; use the real bundled one here.
    monkeypatch.setattr(data_store, "FALLBACK_PATH", os.path.join(data_store.DATA_DIR, "fallback.json"))
    for text in ("1101 S Saginaw St, Flint", "1101 S Saginaw St, Flint, MI", "1101 S Saginaw St, Flint, MI 48502",
                 "1600 Pennsylvania Ave NW, DC", "121 N LaSalle St, Chicago"):
        assert data_store.lookup_address(text)["systems"], text
    assert not data_store.lookup_address("1101 S Saginaw St, Detroit")["systems"]
