"""Build data/details_fallback.json: a snapshot of the live "insights" for the demo systems.

Run (needs internet):  .venv/Scripts/python scripts/build_details_fallback.py
The snapshot lets the demo work with WQT_OFFLINE=1 (neighbors + state report).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import details, state_sources  # noqa: E402

# pwsid: (state, lat, lon) - the lat/lon is a point inside the service area (a zip center).
DEMO_SYSTEMS = {
    "MI0002310": ("MI", 43.0125, -83.6875),   # Flint
    "MA3035000": ("MA", 42.3601, -71.0589),   # Boston
    "MO1010415": ("MO", 39.0997, -94.5786),   # Kansas City
    "NJ1111001": ("NJ", 40.2206, -74.7597),   # Trenton
    "LA1105008": ("LA", 30.7330, -90.5090),   # Tangipahoa (Amite)
    "CA1910067": ("CA", 34.0522, -118.2437),  # LA DWP
    "NY7003493": ("NY", 40.7128, -74.0060),   # New York City
    "IL0316000": ("IL", 41.8781, -87.6298),   # Chicago
    "CA1010018": ("CA", 36.7306, -120.0724),  # Kerman, CA: SAFER "Failing" (zip 93630)
    "CA1910077": ("CA", 33.9731, -118.2479),  # first system for zip 90001
    "DC0000002": ("DC", 38.8987, -77.0352),   # DC Water (White House address demo)
}


def main():
    snapshot = {}
    for pwsid, (state, lat, lon) in DEMO_SYSTEMS.items():
        entry = {}
        try:
            entry["neighbors"] = details.live_neighbors(pwsid, lat, lon)
        except Exception as exc:
            print(pwsid, "neighbors failed:", exc)
            entry["neighbors"] = []
        report = None
        if state_sources.get_adapter(state):
            try:
                report = state_sources.get_adapter(state).fetch(pwsid)
            except Exception as exc:
                print(pwsid, "state report failed:", exc)
        entry["state_report"] = report
        snapshot[pwsid] = entry
        print(pwsid, len(entry["neighbors"]), "neighbors;", report and report["status"])
    (ROOT / "data" / "details_fallback.json").write_text(json.dumps(snapshot, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
