"""Build data/fallback.json: saved real results for a few demo zips.

If the EPA API is down during a live pitch, lookup_zip() serves these instead.
Run (needs internet; takes about a minute):
    .venv/Scripts/python scripts/build_fallback.py

Picked on 2026-10-07 by querying open violations in the live API. Violations change
over time, so re-run this before a demo and check the "why" notes still hold.
"""

import json
import os
import sys

# Allow "python scripts/build_fallback.py" from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import data_store  # noqa: E402

OUT = os.path.join(data_store.DATA_DIR, "fallback.json")

DEMO_ZIPS = {
    "48502": "Flint, MI: famous lead crisis, today no open violations (green)",
    "02119": "Boston, MA: open health-based treatment violation (red)",
    "64130": "Kansas City, MO: only an open monitoring violation (yellow); 2 systems",
    "08638": "Trenton, NJ: 3 open issues, mixed health-based and paperwork (red)",
    "90001": "Los Angeles, CA: large city, no open violations (green)",
    "70451": "Natalbany, LA: Tangipahoa Parish with 4 open health-based issues (red)",
}


def main():
    results = {}
    for zip_code, why in DEMO_ZIPS.items():
        result = data_store.fetch_live(zip_code)  # raises if EPA is down: better to fail loudly here
        result["source"] = "fallback"
        results[zip_code] = result
        print(zip_code, len(result["systems"]), "systems -", why)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    print("Wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
