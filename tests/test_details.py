"""Tests for app/details.py and app/state_sources. No real network: HTTP is faked."""

import pytest

from app import details, epa_client, state_sources
from app.state_sources import california


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


BOUNDARY_REPLY = {"features": [
    {"attributes": {"PWSID": "MI0002310", "PWS_Name": "FLINT", "Population_Served_Count": 81000}},
    {"attributes": {"PWSID": "MI0000002", "PWS_Name": "SMALL", "Population_Served_Count": 100}},
    {"attributes": {"PWSID": "MI0000001", "PWS_Name": "BIG", "Population_Served_Count": 5000}},
]}

SAFER_ROW = {
    "WATER_SYSTEM_NUMBER": "CA1010018", "FINAL_SAFER_STATUS": "Failing", "CURRENT_FAILING": "Failing",
    "FAILING_START_DATE": "2021-08-06",
    "PRIMARY_MCL_VIOLATION": "YES", "PRIMARY_ANALYTES": "1,2,3-TRICHLOROPROPANE",
    "SECONDARY_MCL_VIOLATION": "NO", "SECONDARY_ANALYTES": "N/A",
    "E_COLI_VIOLATION": "NO", "E_COLI_ANALYTES": "N/A",
    "TREATMENT_TECHNIQUE_VIOLATION": "NO", "TT_ANALYTES": "N/A",
    "MONITORING_AND_REPORTING_VIOLATION": "YES", "MONITORING_AND_REPORTING_ANALYTES": "N/A",
    "SOURCE_CAPACITY_VIOLATION": "NO", "SOURCE_CAPACITY_ANALYTES": "N/A",
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Each test gets an empty cache file and starts online."""
    monkeypatch.setattr(details, "CACHE_DB", tmp_path / "cache.sqlite3")
    monkeypatch.delenv("WQT_OFFLINE", raising=False)


def fake_web(monkeypatch, boundary=True, violations=True, safer=True):
    """Fake all three web calls. Each flag set to False makes that call raise."""
    def fake_get(url, params=None, timeout=None):
        if "arcgis" in url:
            if not boundary:
                raise RuntimeError("boundary down")
            return FakeResponse(BOUNDARY_REPLY)
        if not safer:
            raise RuntimeError("safer down")
        return FakeResponse({"result": {"records": [SAFER_ROW]}})

    def fake_query(table, filters, rows=None, timeout=8):
        if not violations:
            raise epa_client.EpaApiError("down")
        # MI0000001 has an open health-based violation; the other neighbor has none.
        if ("pwsid", "equals", "MI0000001") in filters:
            return [{"compliance_status_code": "O", "compl_per_begin_date": "2020-01-01 00:00:00",
                     "is_health_based_ind": "Y"}]
        return []

    monkeypatch.setattr(details.requests, "get", fake_get)
    monkeypatch.setattr(california.requests, "get", fake_get)
    monkeypatch.setattr(details.epa_client, "query", fake_query)


def test_all_parts_work(monkeypatch):
    fake_web(monkeypatch)
    d = details.get_details("MI0002310", "MI", 43.0, -83.6, "48502", None)
    comp = d["comparison"]
    assert comp["state"]["code"] == "MI" and comp["national"]["systems"] > 1000
    # Excludes itself, sorted by population, flags come from the violation query.
    assert [n["pwsid"] for n in comp["neighbors"]] == ["MI0000001", "MI0000002"]
    assert comp["neighbors"][0]["has_current_health_based"] is True
    assert comp["neighbors"][1]["has_current_any"] is False
    assert d["state_report"] is None  # Michigan has no adapter
    assert d["lead_pipes"]["housing"]["geo"] == "zip"


def test_boundary_failure_only_loses_neighbors(monkeypatch):
    fake_web(monkeypatch, boundary=False)
    # A pwsid that is not in details_fallback.json, so no snapshot fills the gap.
    d = details.get_details("MI9999999", "MI", 43.0, -83.6, "48502", None)
    assert d["comparison"]["neighbors"] == []
    assert d["comparison"]["state"]["code"] == "MI"
    assert d["lead_pipes"]["housing"] is not None


def test_violation_failure_keeps_neighbor_list(monkeypatch):
    fake_web(monkeypatch, violations=False)
    d = details.get_details("MI0002310", "MI", 43.0, -83.6, "48502", None)
    assert len(d["comparison"]["neighbors"]) == 2
    assert d["comparison"]["neighbors"][0]["has_current_any"] is None


def test_state_site_failure_only_loses_state_report(monkeypatch):
    fake_web(monkeypatch, safer=False)
    d = details.get_details("CA9999999", "CA", 36.7, -120.0, "93630", None)
    assert d["state_report"] is None
    assert len(d["comparison"]["neighbors"]) == 3


def test_failed_live_part_uses_demo_snapshot(monkeypatch):
    fake_web(monkeypatch, safer=False)
    d = details.get_details("CA1010018", "CA", 36.7, -120.0, "93630", None)
    assert d["state_report"]["status"] == "Failing"


def test_state_report_for_california(monkeypatch):
    fake_web(monkeypatch)
    d = details.get_details("CA1010018", "CA", 36.7, -120.0, "93630", None)
    assert d["state_report"]["status"] == "Failing"


def test_everything_down_never_raises(monkeypatch):
    fake_web(monkeypatch, boundary=False, violations=False, safer=False)
    d = details.get_details("CA1010018", "CA", 36.7, -120.0, "93630", None)
    assert set(d) == {"comparison", "state_report", "lead_pipes"}


def test_unknown_state_has_no_comparison(monkeypatch):
    fake_web(monkeypatch)
    d = details.get_details("ZZ0000001", "ZZ", None, None, None, None)
    assert d["comparison"] is None
    assert d["lead_pipes"] == {"housing": None, "state_lsl_estimate": None}


def test_result_is_cached(monkeypatch):
    fake_web(monkeypatch)
    details.get_details("MI0002310", "MI", 43.0, -83.6, "48502", None)

    def boom(*a, **k):
        raise AssertionError("should have used the cache")
    monkeypatch.setattr(details.requests, "get", boom)
    d = details.get_details("MI0002310", "MI", 43.0, -83.6, "48502", None)
    assert len(d["comparison"]["neighbors"]) == 2


def test_offline_uses_bundled_data_and_snapshot(monkeypatch):
    monkeypatch.setenv("WQT_OFFLINE", "1")

    def boom(*a, **k):
        raise AssertionError("no network in offline mode")
    monkeypatch.setattr(details.requests, "get", boom)
    monkeypatch.setattr(details.epa_client, "query", boom)
    d = details.get_details("CA1010018", "CA", 36.7, -120.0, "93630", None)
    assert d["comparison"]["state"]["code"] == "CA"
    assert d["state_report"]["status"] == "Failing"  # from details_fallback.json
    flint = details.get_details("MI0002310", "MI", 43.0, -83.6, "48502", None)
    assert len(flint["comparison"]["neighbors"]) > 0


def test_california_mapping():
    report = california.map_row(SAFER_ROW)
    assert report["status"] == "Failing"
    assert report["failing_since"] == "2021-08-06"
    assert report["state_violations"] == [
        {"kind": "Primary MCL", "analytes": "1,2,3-TRICHLOROPROPANE"},
        {"kind": "Monitoring and Reporting", "analytes": ""},
    ]


def test_california_not_failing_has_no_date_or_violations():
    row = dict(SAFER_ROW, FINAL_SAFER_STATUS="Not At-Risk", CURRENT_FAILING="Not Failing",
               FAILING_START_DATE="N/A", PRIMARY_MCL_VIOLATION="NO", MONITORING_AND_REPORTING_VIOLATION="NO")
    report = california.map_row(row)
    assert report["failing_since"] is None and report["state_violations"] == []


def test_california_adapter_no_row(monkeypatch):
    monkeypatch.setattr(california.requests, "get",
                        lambda *a, **k: FakeResponse({"result": {"records": []}}))
    assert california.ADAPTER.fetch("CA0000000") is None


def test_registry():
    assert state_sources.get_adapter("ca") is california.ADAPTER
    assert state_sources.get_adapter("MI") is None
    assert state_sources.fetch_state_report("MI", "MI0002310") is None


def test_registry_swallows_adapter_errors(monkeypatch):
    class Broken:
        def fetch(self, pwsid):
            raise RuntimeError("boom")
    monkeypatch.setitem(state_sources.ADAPTERS, "XX", Broken())
    assert state_sources.fetch_state_report("XX", "XX1") is None


def test_state_stats_lookup():
    comp = details.state_comparison("mi")
    assert comp["state"]["name"] == "Michigan"
    assert comp["state"]["systems"] > 1000
    assert 0 < comp["national"]["pct_current_health_based"] < 100
    assert comp["as_of"]
    assert details.state_comparison("ZZ") is None


def test_housing_tract_beats_zip(monkeypatch):
    monkeypatch.setitem(details._files, "housing",
                        {("tract", "26049000100"): 1950, ("zip", "48502"): 1960})
    assert details.lead_pipes("MI", "48502", "26049000100")["housing"] == {"geo": "tract", "median_year_built": 1950}
    assert details.lead_pipes("MI", "48502", None)["housing"] == {"geo": "zip", "median_year_built": 1960}
    # An unknown tract falls back to the zip.
    assert details.lead_pipes("MI", "48502", "99999999999")["housing"]["geo"] == "zip"
    assert details.lead_pipes("MI", None, None)["housing"] is None


def test_housing_bundled_file_has_values():
    table = details._housing_table()
    assert any(k[0] == "tract" for k in table) and any(k[0] == "zip" for k in table)
    assert all(1800 <= year <= 2030 for year in table.values())
