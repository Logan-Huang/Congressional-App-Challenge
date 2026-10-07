# Data Contract

The three layers (data → translation → web) talk to each other only through the
shapes below. If you change a shape, update this file in the same commit.

## Facts about the EPA API (verified 2026-10-07)

- Base URL: `https://data.epa.gov/efservice/`
- URL pattern: `{TABLE}/{column}/{operator}/{value}/.../{rows}/JSON`
  - Column names are **lowercase** (`pwsid`, `zip_code`). Uppercase → "column does not exist".
  - Operators: `equals`, `beginsWith`, `notEquals`, `lessThan`, `greaterThan`, `contains`.
  - Multiple filters chain: `SDWIS.WATER_SYSTEM/zip_code/beginsWith/48502/pws_type_code/equals/CWS/JSON`
  - Row range: `.../1:100/JSON`
- Tables: `SDWIS.WATER_SYSTEM`, `SDWIS.GEOGRAPHIC_AREA`, `SDWIS.VIOLATION`,
  `SDWIS.LCR_SAMPLE_RESULT` (no dates on this one), `SDWIS.LCR_SAMPLE`.
- Typical response time: ~3 s per query. Empty result = `[]`. Bad query = `{"error": "..."}` with HTTP 200.

## Zip → water system (the hard part)

`GEOGRAPHIC_AREA.zip_code_served` is almost always null in federal data. Strategies, in order of quality:

| match_type     | How                                                                                         | Label shown to user                         |
|----------------|---------------------------------------------------------------------------------------------|---------------------------------------------|
| `service_area` | `GEOGRAPHIC_AREA.zip_code_served == zip`                                                    | "Serves your zip code"                      |
| `city_served`  | zip → (city, state) via `data/zip_to_city.csv`, then `GEOGRAPHIC_AREA.city_served == CITY`  | "Serves {City}"                             |
| `admin_address`| `WATER_SYSTEM.zip_code beginsWith zip`                                                      | "Based in your zip code"                    |

Rules for every strategy:
- Community water systems only: `pws_type_code == "CWS"`, `pws_activity_code == "A"`.
- **Same-state filter**: keep a system only if its `primacy_agency_code` equals the zip's state.
  Without this, a zip with a management company's office returns trailer parks in other states.
- Union all strategies, dedupe by `pwsid` (keep the best match_type), sort by match quality then population desc.
- `admin_address` matches are cross-checked against the system's own `GEOGRAPHIC_AREA.city_served` list
  (whole-word match; values look like `"EWING TWP.-1102,TRENTON CITY-1111"`): if it names the zip's city the
  match is upgraded to `city_served`; if it names only other towns it is dropped, unless that would leave no systems.

## Layer 1 → Layer 2: `lookup_zip(zip: str) -> LookupResult`  (`app/data_store.py`)

```python
LookupResult = {
    "zip": "48502",
    "source": "live" | "cache" | "fallback",   # where the data came from
    "systems": [WaterSystem, ...],             # [] if nothing matched
}

WaterSystem = {
    "pwsid": "MI0002310",
    "name": "FLINT, CITY OF",                  # raw EPA name; the web layer title-cases it
    "city": "FLINT",
    "state": "MI",
    "population_served": 81252,                # int or None
    "match_type": "service_area" | "city_served" | "admin_address",
    "total_violation_count": 5,                # all violations ever on record
    "violations": [Violation, ...],            # every OPEN one + up to 5 most recent non-open, newest first
    "lead_90th": None | {"value_mg_l": 0.01, "sample_date": "2023-12-31"},  # optional; None if unknown
}

Violation = {
    "violation_id": "486",
    "contaminant_code": "5000",
    "violation_code": "52",
    "category_code": "MCL" | "MRDL" | "TT" | "MR" | "MON" | "RPT" | "Other",
    "rule_code": "350",
    "is_health_based": True,                   # from is_health_based_ind == "Y"
    "status": "open" | "resolved" | "archived",# compliance_status_code O / R / K
    "begin_date": "2020-07-01",                # YYYY-MM-DD or None
    "end_date": None,
    "returned_to_compliance_date": "2020-12-31" or None,
    "measure": 41.0 or None,                   # viol_measure
    "unit": "mg/L" or None,
    "state_mcl": 0.01 or None,
    "notification_tier": 1 | 2 | 3 | None,     # public_notification_tier: 1 = immediate health risk
}
```

`lookup_zip` never raises: on API failure it falls back to the SQLite cache, then `data/fallback.json`.
Env var `WQT_OFFLINE=1` skips the live API entirely (demo safety switch).

## Layer 2 → Layer 3: `translate_system(system: WaterSystem) -> Translation`  (`app/translate.py`)

```python
Translation = {
    "status": "green" | "yellow" | "red",
    "status_label": "No current violations" | "Open paperwork/monitoring issue" | "Active health-based violation",
    "sentences": ["...", "..."],               # 1-3 plain-English sentences, no jargon, no codes
}
```

- `red`    = at least one **open** violation with `is_health_based == True`.
- `yellow` = open violations exist, but none are health-based (missed tests, late reports).
- `green`  = no open violations.

## Layer 3: HTTP API  (`app/main.py`, Flask)

`GET /api/lookup?zip=48502`

```json
{
  "zip": "48502",
  "found": true,
  "source": "live",
  "message": null,
  "systems": [
    {"pwsid": "MI0002310", "name": "Flint, City Of", "city": "Flint", "state": "MI",
     "population_served": 81252, "match_type": "city_served", "match_label": "Serves Flint",
     "status": "green", "status_label": "No current violations", "sentences": ["..."]}
  ]
}
```

- No match → `200 {"found": false, "systems": [], "message": "We don't have data for this zip code yet."}`
- Invalid zip → `400 {"error": "Please enter a 5-digit zip code."}`
- Any unexpected exception → `200 {"found": false, "message": "Something went wrong looking up that zip code. Please try again."}`; it is never a 500 or a stack trace.
