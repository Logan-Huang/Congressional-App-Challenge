# Water Quality Tracker

Type a 5-digit US zip code and get a plain-English answer: does the public water system serving that area currently meet federal (EPA) drinking-water standards, and if not, what is wrong? One input, one honest answer, written for a parent or a judge, not a chemist. Paperwork problems (late reports, missed tests) are clearly separated from health-based violations so the app is accurate without being alarmist.

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

Then open http://127.0.0.1:5000 in your browser.

## Demo zip codes

| Zip | Place | Expected status | Why it is interesting |
|---|---|---|---|
| 48502 | Flint, MI | Green | The famous lead crisis, now with no open violations |
| 02119 | Boston, MA | Red (first system) | Open health-based violation; also shows two systems in one zip |
| 64130 | Kansas City, MO | Yellow (Kansas City) | Monitoring/reporting problem only, not a health violation |
| 08638 | Trenton, NJ | Red | Several open issues of mixed severity ("2+ issues" wording) |
| 70451 | Tangipahoa Parish, LA | Red (first system) | Four open violations, three systems on one page |
| 90001 | Los Angeles, CA | Green | Big city, clean result |

Statuses come from live EPA data and can change. Check them before presenting. Avoid big-city zips like 10001 (NYC) or 60601 (Chicago); see Known limitations.

## Demo safety switch

Live lookups take about 3-4 seconds (some big cities longer). If the venue Wi-Fi is bad, start the app with no network use at all:

    WQT_OFFLINE=1 python -m app.main            (macOS/Linux)
    $env:WQT_OFFLINE="1"; python -m app.main    (Windows PowerShell)

Offline mode uses the saved cache, then `data/fallback.json` (a snapshot covering the demo zips). To refresh that snapshot before the pitch, with internet on:

    python scripts/build_fallback.py

Then re-check the demo table above.

## How it works

1. Data (`app/epa_client.py`, `app/data_store.py`): queries EPA SDWIS through the Envirofacts API, caches results in SQLite for 7 days, and falls back to stale cache, then `data/fallback.json`. It never raises.
2. Translation (`app/translate.py`, `app/codes.py`): rule-based, no AI. Turns violation codes into a status (red = open health-based violation, yellow = open non-health issue, green = nothing open) and 1-3 plain sentences.
3. Web (`app/main.py`, `static/`): Flask serves one page and `GET /api/lookup?zip=XXXXX`.

Honest caveat: federal data does not map zip codes to water service areas. We match a zip to a city (using `data/zip_to_city.csv`), then find systems whose registered service-area city or administrative address matches, filtered to the same state. An address-only match is dropped when that system's own service-area list names only other towns (usually a management company's office, not your supplier). Each result is labeled with how it was matched ("Serves ..." or "Based in your zip code"). The shared interface is documented in `docs/DATA_CONTRACT.md`.

## Project layout

    app/          epa_client.py, data_store.py, codes.py, translate.py, main.py
    static/       index.html, style.css, app.js
    data/         zip_to_city.csv, fallback.json (cache.sqlite3 is created at runtime)
    scripts/      build_zip_table.py, build_fallback.py
    tests/        test_data_store.py, test_translate.py, test_api.py
    docs/         DATA_CONTRACT.md

## Running tests

    python -m pytest -q

Add `WQT_LIVE_TESTS=1` to include one live EPA smoke test.

## Known limitations

- Zip level only, no address lookup (a zip can span several systems).
- Some large utilities are not found by zip (NYC, Chicago, Seattle) because EPA's city and address fields do not match; these show "We don't have data for this zip code yet."
- No trends, charts, comparisons, or state data sources.
- Lead service line (pipe) inventories are not shown beyond violations.
- Violations with EPA status code "K" (addressed but not resolved) are treated as not open.
- Contaminant and rule code wording in `app/codes.py` was written from memory and should be checked against EPA's reference code list.
- Lead test dates are the end of the monitoring period, so only the year is shown.

## Data attribution

- U.S. EPA Safe Drinking Water Information System (SDWIS), via the Envirofacts API.
- Zip code to city table from GeoNames postal codes, licensed CC BY 4.0.

## Who owns what

| Step | Files |
|---|---|
| 1. Data layer | `app/epa_client.py`, `app/data_store.py`, `scripts/`, `data/` |
| 2. Translation | `app/codes.py`, `app/translate.py` |
| 3. Web | `app/main.py`, `static/` |
| 4. Integration, docs, tests | `README.md`, `tests/`, `.gitignore` |
