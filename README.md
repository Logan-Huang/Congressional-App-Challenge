# Water Quality Tracker

Type a US street address or 5-digit zip code and get a plain-English answer: does the public water system serving that spot currently meet federal (EPA) drinking-water standards, and if not, what is wrong? One input, one honest answer, written for a parent or a judge, not a chemist. Paperwork problems (late reports, missed tests) are kept separate from health-based violations so the app is accurate without being alarmist. It never calls water "safe" or "unsafe" outright.

## Features

- Address or zip lookup, matched to the water system's real service-area boundary (with a map outline).
- Green / yellow / red status plus 1-3 plain sentences. No raw EPA codes are shown.
- 10-year violation history and lead test trend, with a one-line "improving / steady / worsening" summary.
- Comparison to your state, the U.S., and neighboring water systems.
- State report where we have one (California SAFER list, through a small adapter).
- Lead pipe hint from the age of homes in your neighborhood (an estimate, not a test).
- What you can do: certified filters, who to call, the utility's contact info.

## Quick start

Windows (PowerShell):

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    python -m app.main

macOS / Linux:

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    python -m app.main

Then open http://127.0.0.1:5000. A search can be shared as a link: `/?zip=48502` or `/?address=...`.

## Demo inputs

All of these also work offline (see below). Statuses come from live EPA data and can change, so check them before presenting.

| Input | Place | What it shows |
|---|---|---|
| 48502 | Flint, MI | No open violations (green) despite the famous lead crisis; neighbors; older housing |
| 02119 | Boston, MA | Open health-based violation (red) |
| 64130 | Kansas City, MO | Monitoring/reporting problem only (yellow); 2 systems |
| 08638 | Trenton, NJ | Many open issues of mixed severity (red) |
| 70451 | Tangipahoa Parish, LA | Open violations, 3 systems on one page |
| 90001 | Los Angeles, CA | Large city; California state report ("Potentially At-Risk") |
| 93630 | Kerman, CA | The California failing-system demo: green on federal data, "Failing" on the state SAFER list |
| 10001 | New York, NY | Big city that zip matching alone could not find; matched by boundary |
| 60601 | Chicago, IL | Big city matched by boundary; yellow |
| 1101 S Saginaw St, Flint, MI 48502 | Flint City Hall | Address-level match |
| 121 N LaSalle St, Chicago, IL 60602 | Chicago City Hall | Address-level match in a big city |
| 1600 Pennsylvania Ave NW, Washington, DC 20500 | The White House | Address match to DC Water |

## Offline switch (demo safety)

If the venue Wi-Fi is bad, start with no network use at all:

    WQT_OFFLINE=1 python -m app.main            (macOS/Linux)
    $env:WQT_OFFLINE="1"; python -m app.main    (Windows PowerShell)

Offline mode serves the saved cache, then `data/fallback.json` (verdict) and `data/details_fallback.json` (comparison/state cards) for the demo inputs above. Anything else says "We don't have data yet" instead of failing.

To refresh the demo data before a pitch (internet on; run in this order, the first two are slow on purpose to avoid EPA's rate limit):

    python scripts/build_zip_table.py          # data/zip_to_city.csv (zip -> city, state, lat/lon)
    python scripts/build_housing_age.py        # data/housing_age.csv (Census ACS)
    python scripts/build_state_stats.py        # data/state_stats.json (state/national rates)
    python scripts/build_fallback.py           # data/fallback.json (several minutes)
    python scripts/build_details_fallback.py   # data/details_fallback.json

Then re-check the demo table above. (`data/sdwa_ref_codes.csv` is EPA's reference code list and only changes rarely.)

## How matching works

1. Address: the Census geocoder turns it into a point (and census tract). EPA's Water System Boundaries layer tells us which service-area polygon(s) contain that point. If the point is in none, we fall back to a zip search on the geocoded zip.
2. Zip: we take the zip's center point and run the same boundary check first (this fixes big cities like NYC and Chicago). Then we add systems from EPA SDWIS whose registered service-area city or administrative address matches the zip's city, in the same state.
3. Each result is labeled with how it was matched ("Your address is in this service area", "Serves your zip code", "Based in your zip code"). Boundaries EPA modeled rather than received from the utility are labeled "estimated boundary".

## How it works

- Data (`app/epa_client.py`, `app/geo.py`, `app/data_store.py`, `app/details.py`): queries EPA SDWIS (Envirofacts), the Census geocoder and the EPA boundary layer. Caches results in SQLite for 7 days, then falls back to stale cache, then the bundled fallback files. It never raises.
- Rules (`app/rules.py`): the shared definitions every layer uses.
  - **Current violation:** EPA status "Open", or "Known" (never returned to compliance) if it began in the last 5 years.
  - **Health-based:** EPA's own flag, except missing lead pipe inventories (Lead and Copper Rule Revisions), which we treat as record-keeping. They show yellow, not red, and are excluded from the trend and the state/national rates. Including them made the national "current health-based" rate 8.3% instead of 5.6%. To follow EPA's flag exactly, change `counts_as_health_based`.
- Translation (`app/codes.py`, `app/translate.py`, `app/guidance.py`): rule-based, no AI. Turns violation codes into a status and plain sentences, a trend, and action tips.
- Web (`app/main.py`, `static/`): Flask serves one page, `GET /api/lookup?zip=` or `?address=`, and `GET /api/details?pwsid=...` (loaded after the verdict so the verdict shows fast).

The shared interface between these layers is `docs/DATA_CONTRACT.md`.

## Adding another state

State data plugs in through adapters. See the "HOW TO ADD ANOTHER STATE" note at the top of `app/state_sources/__init__.py` and copy `app/state_sources/california.py`.

## Project layout

    app/            epa_client.py, geo.py, data_store.py, details.py, rules.py,
                    codes.py, translate.py, guidance.py, main.py, state_sources/
    static/         index.html, style.css, app.js
    data/           zip_to_city.csv, housing_age.csv, state_stats.json, sdwa_ref_codes.csv,
                    fallback.json, details_fallback.json (cache.sqlite3 is created at runtime)
    scripts/        build_*.py (see "Offline switch")
    tests/          test_*.py
    docs/           DATA_CONTRACT.md

## Running tests

    python -m pytest -q

Add `WQT_LIVE_TESTS=1` to include one live EPA smoke test.

## Known limitations

- Lead service line inventories are not public per address, so the lead pipe card is only an estimate from the age of homes in the neighborhood. EPA says lead pipes were banned in 1986.
- Some boundaries are modeled by EPA (machine-learning estimates), not supplied by the utility; the page says so.
- Federal data lags: violations can take months to appear, and "no open violations" means none on record.
- State reports exist only for California so far.
- Lead test dates are the end of the monitoring period, so only the year is shown.
- EPA's shared API throttles bursts, so a live lookup can occasionally fall back to saved data.
- The EPA Safe Drinking Water Hotline number in the tips (1-800-426-4791) should be double-checked before launch.

## Data attribution

- U.S. EPA Safe Drinking Water Information System (SDWIS), via the Envirofacts API.
- U.S. EPA Water System Boundaries (service-area polygons).
- U.S. Census Bureau Geocoder and American Community Survey (housing age).
- Zip code to city and lat/lon table from GeoNames postal codes, licensed CC BY 4.0.
- California State Water Resources Control Board SAFER dashboard data.
- EPA SDWA reference codes (contaminant and rule names).
