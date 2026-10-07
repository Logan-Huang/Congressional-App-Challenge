"""Flask web layer: one page + one JSON endpoint (see docs/DATA_CONTRACT.md)."""
import logging
import re
from pathlib import Path

from flask import Flask, jsonify, request

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

NOT_FOUND_MESSAGE = "We don't have data for this zip code yet."
ERROR_MESSAGE = "Something went wrong looking up that zip code. Please try again."
BAD_ZIP_MESSAGE = "Please enter a 5-digit zip code."

# EPA names are ALL CAPS. These acronyms must stay upper-case when we tidy them.
ACRONYMS = {"USA", "PUD", "MUD", "MHP", "WSC", "LADWP", "PWS", "WD", "CWS", "SD", "WA",
            "NE", "NW", "SE", "SW", "II", "III", "IV", "HOA", "RV", "LLC", "INC", "DWSD", "MWRA"}
# Short words that look like acronyms (no vowels) but are really abbreviations.
NOT_ACRONYMS = {"ST", "MT", "FT", "DR", "MR", "CT", "TWP", "CO", "HWY", "RD", "NY"}
# Small words stay lower-case unless they start the name.
SMALL_WORDS = {"of", "the", "and", "for", "in", "at", "on", "to"}

MATCH_LABELS = {
    "service_area": "Serves your zip code",
    "admin_address": "Based in your zip code",
}


def tidy_name(raw):
    """'FLINT, CITY OF' -> 'Flint, City of'. Leaves lower/mixed-case text alone."""
    if not raw:
        return ""
    if raw != raw.upper():  # already mixed case: someone formatted it on purpose
        return raw.strip()
    words = []
    for i, word in enumerate(raw.split()):
        bare = re.sub(r"[^A-Z]", "", word)
        if word.startswith("(") or word.endswith(")"):
            # Parenthesised text like "(MWRA)" is usually an acronym; "(SECOND WARD)" is not.
            if bare in ACRONYMS or (2 <= len(bare) <= 5 and bare not in NOT_ACRONYMS
                                    and not re.search(r"[AEIOUY]", bare)):
                words.append(word)
                continue
            words.append(word[0] + word[1:].capitalize() if word.startswith("(") else word.capitalize())
            continue
        # No vowels + short = almost certainly an acronym (LADWP, MHP, WSC).
        looks_like_acronym = (2 <= len(bare) <= 5 and not re.search(r"[AEIOUY]", bare)
                              and bare not in NOT_ACRONYMS)
        if bare in ACRONYMS or looks_like_acronym:
            words.append(word)
        elif bare.lower() in SMALL_WORDS and i > 0:
            words.append(word.lower())
        else:
            # Capitalize after hyphens/slashes too, but not after apostrophes ("Mary's").
            words.append("-".join(
                "/".join(p.capitalize() for p in h.split("/")) for h in word.split("-")))
    return " ".join(words)


def match_label(match_type, city):
    """Human label for how we linked the zip to this system."""
    if match_type == "city_served":
        return "Serves " + tidy_name(city) if city else "Serves your area"
    return MATCH_LABELS.get(match_type, "Possibly serves your area")


def build_entry(system, translation, zip_city=None):
    """Merge a WaterSystem's display fields with its Translation.

    zip_city is the city the SEARCHED zip belongs to. WHY: a "city_served" match
    means the system lists that city as served, not that the system's own
    (admin) city is the one we searched for.
    """
    city = tidy_name(system.get("city"))
    return {
        "pwsid": system.get("pwsid"),
        "name": tidy_name(system.get("name")),
        "city": city,
        "state": system.get("state"),
        "population_served": system.get("population_served"),
        "match_type": system.get("match_type"),
        "match_label": match_label(system.get("match_type"), zip_city),
        "status": translation["status"],
        "status_label": translation["status_label"],
        "sentences": list(translation["sentences"]),
    }


def _zip_city(zip_code):
    """City name for the searched zip, or None (label then says 'Serves your area')."""
    try:
        from app.data_store import zip_to_city_state
        place = zip_to_city_state(zip_code)
        return place[0] if place else None
    except Exception:
        return None


def create_app(lookup_fn=None, translate_fn=None):
    """App factory. Tests pass fake functions; real runs import the real ones."""
    # Imported lazily so this module loads even if the other layers aren't written yet.
    if lookup_fn is None:
        from app.data_store import lookup_zip as lookup_fn
    if translate_fn is None:
        from app.translate import translate_system as translate_fn

    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/lookup")
    def api_lookup():
        zip_code = (request.args.get("zip") or "").strip()
        # isascii: str.isdigit() accepts things like Arabic-Indic digits.
        if not (len(zip_code) == 5 and zip_code.isascii() and zip_code.isdigit()):
            return jsonify({"error": BAD_ZIP_MESSAGE}), 400
        try:
            result = lookup_fn(zip_code)
            zip_city = _zip_city(zip_code)
            systems = [build_entry(s, translate_fn(s), zip_city) for s in result.get("systems", [])]
            if not systems:
                return jsonify({"zip": zip_code, "found": False, "source": result.get("source"),
                                "message": NOT_FOUND_MESSAGE, "systems": []})
            return jsonify({"zip": zip_code, "found": True, "source": result.get("source"),
                            "message": None, "systems": systems})
        except Exception:
            # Never show a stack trace to a user; keep it in the server log instead.
            log.exception("Lookup failed for zip %s", zip_code)
            return jsonify({"zip": zip_code, "found": False, "source": None,
                            "message": ERROR_MESSAGE, "systems": []})

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000)
