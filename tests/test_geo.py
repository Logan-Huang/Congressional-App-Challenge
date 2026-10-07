"""Tests for app/geo.py. The HTTP session is faked, so no network is used."""

import math

import pytest
import requests

from app import geo
from app.geo import GeoError


class FakeResponse:
    def __init__(self, status=200, body=None, json_ok=True):
        self.status_code = status
        self._body = body
        self._json_ok = json_ok

    def json(self):
        if not self._json_ok:
            raise ValueError("not json")
        return self._body


def fake_session(monkeypatch, response=None, exc=None, seen=None):
    def get(url, params=None, timeout=None):
        if seen is not None:
            seen.update(url=url, params=params, timeout=timeout)
        if exc:
            raise exc
        return response
    monkeypatch.setattr(geo._session, "get", get)


CENSUS_MATCH = {"result": {"addressMatches": [{
    "matchedAddress": "1101 S SAGINAW ST, FLINT, MI, 48502",
    "coordinates": {"x": -83.6859, "y": 43.0107},
    "addressComponents": {"zip": "48502", "state": "MI"},
    "geographies": {"Census Tracts": [{"GEOID": "26049002800"}]},
}]}}


def test_geocode_address_parses_match(monkeypatch):
    seen = {}
    fake_session(monkeypatch, FakeResponse(body=CENSUS_MATCH), seen=seen)
    assert geo.geocode_address("1101 S Saginaw St, Flint, MI") == {
        "matched_address": "1101 S SAGINAW ST, FLINT, MI, 48502", "lat": 43.0107, "lon": -83.6859,
        "zip": "48502", "state": "MI", "tract": "26049002800"}
    assert seen["timeout"] == geo.TIMEOUT_SECONDS and seen["params"]["address"] == "1101 S Saginaw St, Flint, MI"


def test_geocode_address_no_match_is_none(monkeypatch):
    fake_session(monkeypatch, FakeResponse(body={"result": {"addressMatches": []}}))
    assert geo.geocode_address("nowhere") is None


def test_geocode_address_missing_tract_is_none(monkeypatch):
    body = {"result": {"addressMatches": [{**CENSUS_MATCH["result"]["addressMatches"][0], "geographies": {}}]}}
    fake_session(monkeypatch, FakeResponse(body=body))
    assert geo.geocode_address("x")["tract"] is None


@pytest.mark.parametrize("kwargs", [
    {"exc": requests.Timeout("slow")},
    {"response": FakeResponse(status=503)},
    {"response": FakeResponse(json_ok=False)},
    {"response": FakeResponse(body={"error": {"message": "bad"}})},
])
def test_network_failures_raise_geo_error(monkeypatch, kwargs):
    fake_session(monkeypatch, **kwargs)
    with pytest.raises(GeoError):
        geo.geocode_address("x")
    with pytest.raises(GeoError):
        geo.systems_at_point(43.0, -83.0)
    with pytest.raises(GeoError):
        geo.boundary_outline("MI0002310")


def test_systems_at_point_parses_features(monkeypatch):
    seen = {}
    body = {"features": [
        {"attributes": {"PWSID": "MI0002310", "PWS_Name": "FLINT, CITY OF", "Primacy_Agency": "MI",
                        "Population_Served_Count": 81252, "Symbology_Field": "System Sourced"}},
        {"attributes": {"PWSID": "MI9", "PWS_Name": "X", "Primacy_Agency": "MI",
                        "Population_Served_Count": None, "Symbology_Field": "Modeled"}},
        {"attributes": {"PWS_Name": "no id"}},
    ]}
    fake_session(monkeypatch, FakeResponse(body=body), seen=seen)
    systems = geo.systems_at_point(43.01, -83.69)
    assert [s["pwsid"] for s in systems] == ["MI0002310", "MI9"]
    assert systems[0]["quality"] == "reported" and systems[1]["quality"] == "modeled"
    assert systems[0]["population_served"] == 81252 and systems[0]["state"] == "MI"
    assert seen["params"]["geometry"] == "-83.69,43.01"  # ArcGIS wants lon,lat


def test_boundary_outline_decimates_to_400_points(monkeypatch):
    ring = [[math.cos(i / 1000 * 6.2832), math.sin(i / 1000 * 6.2832)] for i in range(1000)]
    ring.append(ring[0])
    small = [[0, 0], [0, 1], [1, 1], [0, 0]]
    seen = {}
    body = {"features": [{"geometry": {"rings": [ring, small]}}]}
    fake_session(monkeypatch, FakeResponse(body=body), seen=seen)
    rings = geo.boundary_outline("MI0002310")
    assert sum(len(r) for r in rings) <= 400
    assert rings[0][0] == rings[0][-1]                 # still a closed ring
    assert seen["params"]["maxAllowableOffset"] == 0.001
    assert "MI0002310" in seen["params"]["where"]


def test_boundary_outline_none_when_no_polygon_or_bad_id(monkeypatch):
    fake_session(monkeypatch, FakeResponse(body={"features": []}))
    assert geo.boundary_outline("MI0002310") is None
    assert geo.boundary_outline("x or 1=1") is None    # never put odd text in the query


def test_decimate_keeps_small_shapes_and_handles_many_rings():
    square = [[0, 0], [1, 0], [1, 1], [0, 0]]
    assert geo.decimate_rings([square]) == [square]
    many = [[[i, 0], [i, 1], [i + 1, 1], [i, 0]] for i in range(300)]  # 1200 points total
    result = geo.decimate_rings(many)
    assert 0 < sum(len(r) for r in result) <= 400
