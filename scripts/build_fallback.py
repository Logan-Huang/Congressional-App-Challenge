"""Build data/fallback.json: saved real results for the demo zips and addresses.

If the live APIs are down (or WQT_OFFLINE=1) during a pitch, lookup_zip() and
lookup_address() serve these instead.
Run (needs internet; takes a few minutes because it goes slowly on purpose):
    .venv/Scripts/python scripts/build_fallback.py

File structure (one flat JSON object, each value is a full LookupResult):
    "zip:48502"                               -> result for lookup_zip("48502")
    "addr:1101 s saginaw st, flint, mi 48502" -> result for lookup_address(...), key is the
                                                 address lowercased with whitespace collapsed
                                                 (data_store.normalize_address)

Picked on 2026-10-07 by querying the live API. Violations change over time, so
re-run this before a demo and check the "why" notes still hold.
"""

import json
import os
import sys
import time

# Allow "python scripts/build_fallback.py" from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import data_store  # noqa: E402
from app.epa_client import EpaApiError  # noqa: E402
from app.geo import GeoError  # noqa: E402

OUT = os.path.join(data_store.DATA_DIR, "fallback.json")

# EPA's API is shared and throttles bursts (HTTP 429), so we pause between lookups.
PAUSE_SECONDS = 12
RETRIES = 4
RETRY_WAIT_SECONDS = 30

DEMO_ZIPS = {
    "48502": "Flint, MI: famous lead crisis, today no open violations (green)",
    "02119": "Boston, MA: open health-based treatment violation (red)",
    "64130": "Kansas City, MO: only an open monitoring violation (yellow); 2 systems",
    "08638": "Trenton, NJ: 3 open issues, mixed health-based and paperwork (red)",
    "90001": "Los Angeles, CA: large city, no open violations (green)",
    "70451": "Natalbany, LA: Tangipahoa Parish with 4 open health-based issues (red)",
    "10001": "New York, NY: needs the boundary match (zip strategies alone find nothing)",
    "60601": "Chicago, IL: needs the boundary match; one very large system",
    "93630": "Kerman, CA: California SAFER 'Failing' system (state-data demo)",
}

DEMO_ADDRESSES = {
    "1101 S Saginaw St, Flint, MI 48502": "Flint City Hall: address-level match in Flint",
    "121 N LaSalle St, Chicago, IL 60602": "Chicago City Hall: address-level match in a big city",
    "1600 Pennsylvania Ave NW, Washington, DC 20500": "The White House: DC Water address match",
}


def fetch_with_retries(fetch, arg):
    """Run one live fetch (returns (result, degraded)); retry a few times if the APIs throttle us."""
    for attempt in range(1, RETRIES + 1):
        try:
            result, degraded = fetch(arg)
            if degraded:
                raise EpaApiError("map service failed partway; boundaries would be missing")
            return result
        except (EpaApiError, GeoError) as exc:
            print(f"  attempt {attempt} failed: {exc}")
            if attempt == RETRIES:
                raise  # better to fail loudly here than save a half-empty fallback
            time.sleep(RETRY_WAIT_SECONDS)


def main():
    results = {}
    jobs = [("zip:" + z, data_store._fetch_zip, z, why) for z, why in DEMO_ZIPS.items()]
    jobs += [("addr:" + data_store.normalize_address(a), data_store._fetch_address, a, why)
             for a, why in DEMO_ADDRESSES.items()]
    for key, fetch, arg, why in jobs:
        started = time.time()
        result = fetch_with_retries(fetch, arg)
        if result["query"]["not_found"]:
            raise SystemExit(f"{arg!r} could not be geocoded")
        result["source"] = "fallback"
        results[key] = result
        print(f"{key}: {len(result['systems'])} systems in {time.time() - started:.1f}s - {why}")
        time.sleep(PAUSE_SECONDS)
    with open(OUT, "w", encoding="utf-8") as f:
        # No indent: boundaries are long lists of numbers and indenting would triple the size.
        json.dump(results, f, separators=(",", ":"))
    print("Wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
