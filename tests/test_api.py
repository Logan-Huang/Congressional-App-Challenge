"""Tests for the Flask layer, using fake data/translate functions built from the contract shapes."""
import pytest

from app.main import create_app, match_label, tidy_name


def fake_system(**overrides):
    system = {
        "pwsid": "MI0002310", "name": "FLINT, CITY OF", "city": "FLINT", "state": "MI",
        "population_served": 81252, "match_type": "city_served", "boundary_quality": None,
        "boundary": None, "total_violation_count": 0, "violations": [], "lead_90th": None,
        "history": {"by_year": [], "lead_90th": []},
        "contact": {"org_name": "City of Flint", "phone": "8107662000", "email": None, "address": None},
    }
    system.update(overrides)
    return system


def fake_translate(system):
    return {"status": "green", "status_label": "No current violations",
            "sentences": ["Nothing to report."],
            "trend": {"direction": "no_history", "sentence": "No history."},
            "guidance": [{"title": "Read your report", "text": "Ask your utility.", "link": None}]}


def fake_zip(z):
    return {"zip": z, "source": "live",
            "query": {"type": "zip", "input": z, "matched_address": None, "lat": 43.0, "lon": -83.7,
                      "state": "MI", "tract": None, "not_found": False},
            "systems": [fake_system()]}


def fake_address(a):
    return {"zip": "48502", "source": "live",
            "query": {"type": "address", "input": a, "matched_address": "1 MAIN ST, FLINT, MI, 48502",
                      "lat": 43.0, "lon": -83.7, "state": "MI", "tract": "26049000100",
                      "not_found": False},
            "systems": [fake_system(match_type="address_boundary")]}


def fake_details(pwsid, state, lat, lon, zip_code, tract):
    fake_details.last = (pwsid, state, lat, lon, zip_code, tract)
    return {"comparison": {"state": {"code": "MI"}, "national": {}, "neighbors": []},
            "state_report": None,
            "lead_pipes": {"housing": {"geo": "zip", "median_year_built": 1948},
                           "state_lsl_estimate": None}}


def fake_translate_details(details, system):
    return {"comparison_sentences": ["Better than most."], "state_sentences": [],
            "lead_pipe": {"level": "typical", "sentences": ["Typical."]}}


def make_app(**fns):
    defaults = dict(lookup_zip_fn=fake_zip, lookup_address_fn=fake_address, details_fn=fake_details,
                    translate_fn=fake_translate, translate_details_fn=fake_translate_details)
    defaults.update(fns)
    return create_app(**defaults)


@pytest.fixture
def client():
    return make_app().test_client()


# ---------- zip lookups ----------

def test_found_zip_result_shape(client):
    resp = client.get("/api/lookup?zip=48502")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["found"] is True and data["source"] == "live" and data["message"] is None
    assert data["zip"] == "48502"
    assert data["query"]["type"] == "zip"
    entry = data["systems"][0]
    assert entry["name"] == "City of Flint"
    # Contact shown on the page never includes an email (often a named staff member).
    assert "email" not in (entry["contact"] or {})
    assert entry["match_label"] == "Serves Flint"
    assert entry["status"] == "green"
    assert entry["sentences"] == ["Nothing to report."]
    # Raw chart data is passed through for the page to draw.
    for key in ("trend", "guidance", "history", "boundary", "contact", "lead_90th"):
        assert key in entry
    assert entry["contact"]["org_name"] == "City of Flint"


def test_zip_not_found_message():
    app = make_app(lookup_zip_fn=lambda z: {"zip": z, "source": "live", "systems": []})
    data = app.test_client().get("/api/lookup?zip=00000").get_json()
    assert data["found"] is False and data["systems"] == []
    assert data["message"] == "We don't have data for this zip code yet."


@pytest.mark.parametrize("query", ["?zip=1234", "?zip=abcde", "?zip=123456", "?zip=", "", "?zip=12 34"])
def test_invalid_zip_is_400(client, query):
    resp = client.get("/api/lookup" + query)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Please enter a 5-digit zip code."}


def test_whitespace_padded_zip_accepted():
    seen = []

    def lookup(z):
        seen.append(z)
        return fake_zip(z)

    resp = make_app(lookup_zip_fn=lookup).test_client().get("/api/lookup?zip=%20%2048502%20")
    assert resp.status_code == 200 and seen == ["48502"]


def test_lookup_exception_gives_friendly_200():
    def boom(z):
        raise RuntimeError("database is on fire")

    resp = make_app(lookup_zip_fn=boom).test_client().get("/api/lookup?zip=48502")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["found"] is False and data["systems"] == []
    assert "Something went wrong" in data["message"]
    assert "on fire" not in resp.get_data(as_text=True)


def test_translate_exception_gives_friendly_200():
    def bad_translate(system):
        raise ValueError("bad")

    resp = make_app(translate_fn=bad_translate).test_client().get("/api/lookup?zip=48502")
    assert resp.status_code == 200
    assert resp.get_json()["found"] is False


# ---------- address lookups ----------

def test_address_lookup_happy_path(client):
    resp = client.get("/api/lookup?address=1+Main+St,+Flint,+MI")
    data = resp.get_json()
    assert resp.status_code == 200 and data["found"] is True
    assert data["query"]["type"] == "address"
    assert data["query"]["matched_address"].startswith("1 MAIN ST")
    assert data["zip"] == "48502"
    assert data["systems"][0]["match_label"] == "Your address is in this service area"


@pytest.mark.parametrize("address", ["abcd", "x" * 201])
def test_bad_address_length_is_400(client, address):
    resp = client.get("/api/lookup", query_string={"address": address})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_address_not_geocoded_message():
    def nowhere(a):
        return {"zip": None, "source": "live", "systems": [],
                "query": {"type": "address", "input": a, "not_found": True}}

    data = make_app(lookup_address_fn=nowhere).test_client().get(
        "/api/lookup?address=nowhere+at+all").get_json()
    assert data["found"] is False
    assert data["message"] == ("We couldn't find that address. Try adding the city and state, "
                               "or search by zip code.")


def test_address_exception_gives_friendly_200():
    def boom(a):
        raise RuntimeError("geocoder down")

    resp = make_app(lookup_address_fn=boom).test_client().get("/api/lookup?address=1+Main+St+Flint")
    assert resp.status_code == 200
    assert resp.get_json()["found"] is False


# ---------- match labels ----------

def test_multiple_systems_and_match_labels():
    systems = [fake_system(match_type="service_area"),
               fake_system(pwsid="X2", name="LADWP", match_type="admin_address")]
    app = make_app(lookup_zip_fn=lambda z: {"zip": z, "source": "fallback", "systems": systems})
    labels = [s["match_label"] for s in app.test_client().get("/api/lookup?zip=48502").get_json()["systems"]]
    assert labels == ["Serves your zip code", "Based in your zip code"]


@pytest.mark.parametrize("match_type, quality, expected", [
    ("address_boundary", "reported", "Your address is in this service area"),
    ("address_boundary", "modeled", "Your address is in this service area (estimated boundary)"),
    ("zip_boundary", "reported", "Serves the center of your zip code"),
    ("zip_boundary", "modeled", "Serves the center of your zip code (estimated boundary)"),
    ("service_area", None, "Serves your zip code"),
    ("admin_address", None, "Based in your zip code"),
    ("city_served", None, "Serves Flint"),
    ("something_new", None, "Possibly serves your area"),
])
def test_match_label(match_type, quality, expected):
    assert match_label(match_type, "FLINT", quality) == expected


def test_city_served_without_city():
    assert match_label("city_served", None) == "Serves your area"


def test_city_served_label_uses_the_searched_zips_city():
    # System is based in BELTON but was matched because it serves the zip's city.
    app = make_app(lookup_zip_fn=lambda z: {"zip": z, "source": "live",
                                            "systems": [fake_system(city="BELTON")]})
    label = app.test_client().get("/api/lookup?zip=48502").get_json()["systems"][0]["match_label"]
    assert label == "Serves Flint"


# ---------- details ----------

def test_details_happy_path(client):
    resp = client.get("/api/details?pwsid=MI0002310&state=MI&lat=43.01&lon=-83.69&zip=48502&tract=")
    assert resp.status_code == 200
    data = resp.get_json()
    assert fake_details.last == ("MI0002310", "MI", 43.01, -83.69, "48502", None)
    assert data["comparison_sentences"] == ["Better than most."]
    assert data["lead_pipe"]["level"] == "typical"
    assert data["lead_housing"]["median_year_built"] == 1948
    assert data["state_report"] is None


def test_details_minimal_params(client):
    assert client.get("/api/details?pwsid=MI0002310&state=MI").status_code == 200


@pytest.mark.parametrize("query", [
    "", "?pwsid=MI0002310", "?state=MI", "?pwsid=bad!&state=MI", "?pwsid=MI0002310&state=Michigan",
    "?pwsid=MI0002310&state=MI&lat=abc", "?pwsid=MI0002310&state=MI&zip=12",
    "?pwsid=MI0002310&state=MI&tract=123", "?pwsid=MI0002310&state=MI&lat=999",
])
def test_details_validation(client, query):
    resp = client.get("/api/details" + query)
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_details_exception_gives_friendly_200():
    def boom(*args):
        raise RuntimeError("kaboom")

    resp = make_app(details_fn=boom).test_client().get("/api/details?pwsid=MI0002310&state=MI")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["comparison"] is None and data["lead_pipe"] is None
    assert "kaboom" not in resp.get_data(as_text=True)


# ---------- page + helpers ----------

def test_index_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Water Quality Tracker" in resp.data
    assert b"/static/app.js" in resp.data


@pytest.mark.parametrize("raw, expected", [
    ("FLINT, CITY OF", "Flint, City of"),
    ("LADWP", "LADWP"),
    ("SUNSET MHP", "Sunset MHP"),
    ("MARY'S WSC", "Mary's WSC"),
    ("ST. LOUIS COUNTY PUD", "St. Louis County PUD"),
    ("NORTH-SOUTH WATER", "North-South Water"),
    ("USA WATER", "USA Water"),
    ("", ""),
    (None, ""),
])
def test_tidy_name(raw, expected):
    assert tidy_name(raw) == expected


def test_details_passes_page_signals_to_translate():
    seen = {}

    def spy(details, system):
        seen.update(system)
        return fake_translate_details(details, system)

    client = make_app(translate_details_fn=spy).test_client()
    client.get("/api/details?pwsid=MI0002310&state=MI&hb=1&lsl=1&lead=0.02")
    assert any(v.get("is_health_based") for v in seen["violations"])
    assert any(v.get("contaminant_code") == "5200" for v in seen["violations"])
    assert seen["lead_90th"] == {"value_mg_l": 0.02}
    seen.clear()
    client.get("/api/details?pwsid=MI0002310&state=MI")
    assert seen["violations"] is None   # unknown, not "no violations"
