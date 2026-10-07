"""Build data/state_stats.json: how common health-based violations are, per state and nationally.

Run:  .venv/Scripts/python scripts/build_state_stats.py
Takes ~1 minute. Re-run every few months; the app only reads the JSON file.

For each state / territory we ask Envirofacts for:
  1. every active community water system (CWS)  -> the denominator
  2. every health-based violation that began in the last 5 years (or is still
     open/known) -> the numerators
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.epa_client import query  # noqa: E402
from app.rules import KNOWN_STILL_CURRENT_YEARS, counts_as_health_based, is_current  # noqa: E402

# Primacy agencies that are a US state, DC or territory (tribal/Navajo agencies are left out
# on purpose: "compare to your state" would not make sense for them).
STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "AS": "American Samoa", "GU": "Guam", "MP": "Northern Mariana Islands",
    "PR": "Puerto Rico", "VI": "U.S. Virgin Islands",
}

STATUS_MAP = {"O": "open", "K": "known", "R": "resolved"}  # same meaning as data_store.py
TODAY = date.today()
CUTOFF = date(TODAY.year - KNOWN_STILL_CURRENT_YEARS, TODAY.month, min(TODAY.day, 28)).isoformat()


def one_state(code):
    cws = [("primacy_agency_code", "equals", code), ("pws_type_code", "equals", "CWS"),
           ("pws_activity_code", "equals", "A")]
    systems = query("SDWIS.WATER_SYSTEM", cws, rows="1:10000", timeout=90)
    # The server-side date filter works ("greaterThan" + YYYY-MM-DD). Open ones can be older
    # than the cutoff, so fetch them in a second query.
    health = cws + [("is_health_based_ind", "equals", "Y")]
    recent = query("SDWIS.VIOLATION", health + [("compl_per_begin_date", "greaterThan", CUTOFF)],
                   rows="1:10000", timeout=90)
    still_open = query("SDWIS.VIOLATION", health + [("compliance_status_code", "equals", "O")],
                       rows="1:10000", timeout=90)

    pwsids = {s["pwsid"] for s in systems}
    recent_ids, current_ids = set(), set()
    for raw in recent + still_open:
        if raw["pwsid"] not in pwsids:
            continue
        begin = (raw.get("compl_per_begin_date") or "")[:10] or None
        violation = {"status": STATUS_MAP.get(raw.get("compliance_status_code"), "archived"),
                     "begin_date": begin, "is_health_based": True,
                     "contaminant_code": raw.get("contaminant_code")}
        # Same rule as the badge: lead pipe inventory gaps are record-keeping, not health-based.
        if not counts_as_health_based(violation):
            continue
        if begin and begin >= CUTOFF:
            recent_ids.add(raw["pwsid"])
        if is_current(violation, TODAY):
            current_ids.add(raw["pwsid"])
    return code, len(pwsids), len(current_ids), len(recent_ids)


def pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def main():
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one_state, STATES))
    out_states, total = {}, [0, 0, 0]
    for code, n, current, recent in results:
        out_states[code] = {
            "name": STATES[code], "systems": n,
            "current_health_based": current, "health_based_5yr": recent,
            "pct_current_health_based": pct(current, n), "pct_health_based_5yr": pct(recent, n),
        }
        total = [total[0] + n, total[1] + current, total[2] + recent]
    data = {
        "as_of": TODAY.isoformat(),
        "source": "EPA Envirofacts SDWIS (active community water systems)",
        "national": {"systems": total[0], "current_health_based": total[1], "health_based_5yr": total[2],
                     "pct_current_health_based": pct(total[1], total[0]),
                     "pct_health_based_5yr": pct(total[2], total[0])},
        "states": out_states,
    }
    (ROOT / "data" / "state_stats.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    print("national", data["national"])
    print("MI", out_states["MI"])


if __name__ == "__main__":
    main()
