"""Tests for the Flask layer, using fake lookup/translate functions."""
import pytest

from app.main import create_app, tidy_name


def fake_system(**overrides):
    system = {
        "pwsid": "MI0002310", "name": "FLINT, CITY OF", "city": "FLINT", "state": "MI",
        "population_served": 81252, "match_type": "city_served",
        "total_violation_count": 0, "violations": [], "lead_90th": None,
    }
    system.update(overrides)
    return system


def fake_translate(system):
    return {"status": "green", "status_label": "No current violations",
            "sentences": ["Nothing to report."]}


def make_client(lookup_fn):
    return create_app(lookup_fn=lookup_fn, translate_fn=fake_translate).test_client()


@pytest.fixture
def client():
    return make_client(lambda z: {"zip": z, "source": "live", "systems": [fake_system()]})


def test_found_result_shape(client):
    resp = client.get("/api/lookup?zip=48502")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["found"] is True
    assert data["zip"] == "48502"
    assert data["source"] == "live"
    assert data["message"] is None
    entry = data["systems"][0]
    assert entry == {
        "pwsid": "MI0002310", "name": "Flint, City of", "city": "Flint", "state": "MI",
        "population_served": 81252, "match_type": "city_served", "match_label": "Serves Flint",
        "status": "green", "status_label": "No current violations",
        "sentences": ["Nothing to report."],
    }


def test_not_found_message():
    resp = make_client(lambda z: {"zip": z, "source": "live", "systems": []}).get("/api/lookup?zip=00000")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["found"] is False
    assert data["systems"] == []
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
        return {"zip": z, "source": "cache", "systems": [fake_system()]}

    resp = make_client(lookup).get("/api/lookup?zip=%20%2048502%20")
    assert resp.status_code == 200
    assert seen == ["48502"]


def test_lookup_exception_gives_friendly_200():
    def boom(z):
        raise RuntimeError("database is on fire")

    resp = make_client(boom).get("/api/lookup?zip=48502")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["found"] is False
    assert data["message"] == "Something went wrong looking up that zip code. Please try again."
    assert "on fire" not in resp.get_data(as_text=True)


def test_translate_exception_gives_friendly_200():
    def bad_translate(system):
        raise ValueError("bad")

    app = create_app(lookup_fn=lambda z: {"zip": z, "source": "live", "systems": [fake_system()]},
                     translate_fn=bad_translate)
    resp = app.test_client().get("/api/lookup?zip=48502")
    assert resp.status_code == 200
    assert resp.get_json()["found"] is False


def test_multiple_systems_and_match_labels():
    systems = [fake_system(match_type="service_area"),
               fake_system(pwsid="X2", name="LADWP", match_type="admin_address")]
    resp = make_client(lambda z: {"zip": z, "source": "fallback", "systems": systems}).get("/api/lookup?zip=48502")
    labels = [s["match_label"] for s in resp.get_json()["systems"]]
    assert labels == ["Serves your zip code", "Based in your zip code"]


def test_index_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Enter your zip code" in resp.data


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


def test_city_served_label_uses_the_searched_zips_city():
    # System is based in BELTON but was matched because it serves the zip's city.
    client = make_client(lambda z: {"zip": z, "source": "live",
                                    "systems": [fake_system(city="BELTON")]})
    label = client.get("/api/lookup?zip=48502").get_json()["systems"][0]["match_label"]
    assert label == "Serves Flint"
