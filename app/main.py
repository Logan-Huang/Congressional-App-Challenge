"""Flask web layer: one page + two JSON endpoints (see docs/DATA_CONTRACT.md, Layer 3)."""
import importlib
import logging
import re
from pathlib import Path

from flask import Flask, jsonify, request

from app.guidance import contact_label
from app.names import display_name, tidy_address, tidy_name  # noqa: F401  (tidy_name re-exported for tests)
from app.rules import counts_as_health_based, is_current

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

NOT_FOUND_MESSAGE = "We don't have data for this zip code yet."
ADDRESS_NO_SYSTEM_MESSAGE = ("We couldn't find a water system for that address. "
                             "Try searching by zip code.")
ADDRESS_NOT_FOUND_MESSAGE = ("We couldn't find that address. Try adding the city and state, "
                             "or search by zip code.")
ERROR_MESSAGE = "Something went wrong looking that up. Please try again."
BAD_ZIP_MESSAGE = "Please enter a 5-digit zip code."
BAD_ADDRESS_MESSAGE = "Please enter a street address (5 to 200 characters), or search by zip code."
BAD_DETAILS_MESSAGE = "That request wasn't valid."

MATCH_LABELS = {
    "address_boundary": "Your address is in this service area",
    "zip_boundary": "Serves the center of your zip code",
    "service_area": "Serves your zip code",
    "admin_address": "Based in your zip code",
}


def match_label(match_type, city, boundary_quality=None):
    """Human label for how we linked the search to this system."""
    if match_type == "city_served":
        label = "Serves " + tidy_name(city) if city else "Serves your area"
    else:
        label = MATCH_LABELS.get(match_type, "Possibly serves your area")
    # WHY: modeled boundaries are EPA machine-learning estimates, so we say so.
    if boundary_quality == "modeled" and match_type in ("address_boundary", "zip_boundary"):
        label += " (estimated boundary)"
    return label


def _signals(system):
    """Whether this system has a current health-based issue / lead inventory gap (same rule as the badge)."""
    current = [v for v in (system.get("violations") or []) if is_current(v)]
    return {
        "current_health_based": any(counts_as_health_based(v) for v in current),
        "lsl_inventory": any(str(v.get("contaminant_code")) == "5200"
                             and str(v.get("violation_code")).upper() == "2E" for v in current),
    }


def build_entry(system, translation, zip_city=None):
    """Merge a WaterSystem's display fields with its Translation.

    zip_city is the city the SEARCHED zip belongs to. WHY: a "city_served" match
    means the system lists that city as served, not that the system's own
    (admin) city is the one we searched for.
    """
    return {
        "pwsid": system.get("pwsid"),
        "name": display_name(system.get("name")),
        "city": tidy_name(system.get("city")),
        "state": system.get("state"),
        "population_served": system.get("population_served"),
        "match_type": system.get("match_type"),
        "match_label": match_label(system.get("match_type"), zip_city,
                                   system.get("boundary_quality")),
        "boundary_quality": system.get("boundary_quality"),
        "status": translation["status"],
        "status_label": translation["status_label"],
        "sentences": list(translation.get("sentences") or []),
        "trend": translation.get("trend"),
        "guidance": list(translation.get("guidance") or []),
        # Raw data the page draws its charts from:
        "history": system.get("history"),
        "boundary": system.get("boundary"),
        "contact": _display_contact(system),
        "lead_90th": system.get("lead_90th"),
        # Tiny facts the page sends back to /api/details so the comparison text can say
        # whether THIS system has a current problem (the details call never sees violations).
        "signals": _signals(system),
    }


def _display_comparison(comparison):
    """Same name tidying for neighbor systems as for the main result ("City of Alexandria")."""
    if not comparison:
        return comparison
    neighbors = [{**n, "name": display_name(n.get("name")) or n.get("name")}
                 for n in comparison.get("neighbors") or []]
    return {**comparison, "neighbors": neighbors}


def _display_contact(system):
    """Organization contact details for the page.

    WHY no email: EPA's email field is often a named staff member's own address,
    which we shouldn't publish. Phone and street address are the utility's.
    """
    contact = system.get("contact") or {}
    return {
        "org_name": contact_label(system),   # None when the "org" looks like a person's name
        "phone": contact.get("phone"),
        "address": tidy_address(contact.get("address")) or None,
    }


def _zip_city(zip_code):
    """City name for a zip, or None (label then says 'Serves your area')."""
    try:
        from app.data_store import zip_to_city_state
        place = zip_to_city_state(zip_code)
        return place[0] if place else None
    except Exception:
        return None


def _is_zip(text):
    # isascii: str.isdigit() accepts things like Arabic-Indic digits.
    return len(text) == 5 and text.isascii() and text.isdigit()


def _float_arg(name):
    """Optional float query arg. Returns (value, ok)."""
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None, True
    try:
        value = float(raw)
    except ValueError:
        return None, False
    if value != value or abs(value) > 180:  # NaN or out of range
        return None, False
    return value, True


def _system_from_args(pwsid, state):
    """Rebuild just enough of a WaterSystem for translate_details from the page's query args.

    WHY: /api/details does not re-fetch violations (slow). The page already has them, so it
    sends hb=1/0 (current health-based issue), lsl=1/0 (lead inventory gap) and lead=<mg/L>.
    No hb arg means "unknown", and translate_details then leaves out its this-system clause.
    """
    system = {"pwsid": pwsid, "state": state,
              "name": (request.args.get("name") or "")[:120] or None, "violations": None}
    hb = request.args.get("hb")
    if hb in ("0", "1"):
        system["violations"] = []
        if hb == "1":
            system["violations"].append({"status": "open", "is_health_based": True})
        if request.args.get("lsl") == "1":
            system["violations"].append({"status": "open", "contaminant_code": "5200",
                                         "violation_code": "2E"})
    lead, ok = _float_arg("lead")
    if ok and lead is not None and lead >= 0:
        system["lead_90th"] = {"value_mg_l": lead}
    return system


def _lazy(module, name):
    """Import a real function only when first called, so a broken layer can't stop the app starting."""
    def call(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)
    return call


def create_app(lookup_zip_fn=None, lookup_address_fn=None, details_fn=None,
               translate_fn=None, translate_details_fn=None):
    """App factory. Tests pass fake functions; real runs use the real ones."""
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

    lookup_zip_fn = lookup_zip_fn or _lazy("app.data_store", "lookup_zip")
    lookup_address_fn = lookup_address_fn or _lazy("app.data_store", "lookup_address")
    details_fn = details_fn or _lazy("app.details", "get_details")
    translate_fn = translate_fn or _lazy("app.translate", "translate_system")
    translate_details_fn = translate_details_fn or _lazy("app.translate", "translate_details")

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/lookup")
    def api_lookup():
        zip_code = (request.args.get("zip") or "").strip()
        address = (request.args.get("address") or "").strip()
        is_address = not zip_code and bool(address)
        if is_address:
            if not 5 <= len(address) <= 200:
                return jsonify({"error": BAD_ADDRESS_MESSAGE}), 400
        elif not _is_zip(zip_code):
            return jsonify({"error": BAD_ZIP_MESSAGE}), 400
        qtype = "address" if is_address else "zip"
        try:
            result = (lookup_address_fn(address) if is_address else lookup_zip_fn(zip_code)) or {}
            query = dict(result.get("query") or {})
            query.setdefault("type", qtype)
            query.setdefault("input", address if is_address else zip_code)
            searched_zip = result.get("zip") or (None if is_address else zip_code)
            query["zip"] = searched_zip
            source = result.get("source")

            def reply(found, message, systems):
                return jsonify({"found": found, "source": source, "message": message,
                                "query": query, "zip": searched_zip, "systems": systems})

            if query.get("not_found"):
                return reply(False, ADDRESS_NOT_FOUND_MESSAGE, [])
            zip_city = _zip_city(searched_zip) if searched_zip else None
            systems = [build_entry(s, translate_fn(s), zip_city) for s in result.get("systems") or []]
            if not systems:
                return reply(False, ADDRESS_NO_SYSTEM_MESSAGE if is_address else NOT_FOUND_MESSAGE, [])
            return reply(True, None, systems)
        except Exception:
            # Never show a stack trace to a user; keep it in the server log instead.
            log.exception("Lookup failed (%s)", qtype)
            return jsonify({"found": False, "source": None, "message": ERROR_MESSAGE,
                            "query": {"type": qtype}, "zip": None, "systems": []})

    @app.get("/api/details")
    def api_details():
        pwsid = (request.args.get("pwsid") or "").strip().upper()
        state = (request.args.get("state") or "").strip().upper()
        zip_code = (request.args.get("zip") or "").strip()
        tract = (request.args.get("tract") or "").strip()
        lat, lat_ok = _float_arg("lat")
        lon, lon_ok = _float_arg("lon")
        valid = (re.fullmatch(r"[A-Z0-9]{5,12}", pwsid) and re.fullmatch(r"[A-Z]{2}", state)
                 and lat_ok and lon_ok and (not zip_code or _is_zip(zip_code))
                 and (not tract or (tract.isascii() and tract.isdigit() and len(tract) == 11)))
        if not valid:
            return jsonify({"error": BAD_DETAILS_MESSAGE}), 400
        try:
            details = details_fn(pwsid, state, lat, lon, zip_code or None, tract or None) or {}
            system = _system_from_args(pwsid, state)
            tr = translate_details_fn(details, system) or {}
            lead = details.get("lead_pipes") or {}
            return jsonify({
                "comparison": _display_comparison(details.get("comparison")),
                "comparison_sentences": tr.get("comparison_sentences") or [],
                "state_report": details.get("state_report"),
                "state_sentences": tr.get("state_sentences") or [],
                "lead_pipe": tr.get("lead_pipe"),
                "lead_housing": lead.get("housing"),
                "lead_state_estimate": lead.get("state_lsl_estimate"),
            })
        except Exception:
            log.exception("Details failed for %s", pwsid)
            # Empty parts: the page hides every card whose data is missing.
            return jsonify({"comparison": None, "comparison_sentences": [], "state_report": None,
                            "state_sentences": [], "lead_pipe": None, "lead_housing": None,
                            "lead_state_estimate": None, "message": ERROR_MESSAGE})

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000)
