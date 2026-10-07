# Data Contract (v2)

The layers (data → translation → web) talk to each other only through the shapes below.
If you change a shape, update this file in the same commit.

## External data sources (all verified 2026-10-07, all free, no API keys)

| Source | Used for | Access | Speed |
|---|---|---|---|
| EPA Envirofacts SDWIS (`https://data.epa.gov/efservice/`) | systems, violations, lead samples | REST, see below | ~3 s/query |
| EPA Water System Boundaries (`https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/Water_System_Boundaries/FeatureServer/0`) | point → water system (address + zip centroid), neighbors | ArcGIS REST `query` | ~0.3 s |
| US Census Geocoder (`https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress`) | address → lat/lon, zip, census tract | REST, `benchmark=Public_AR_Current&vintage=Current_Current&layers=Census%20Tracts&format=json` | ~0.7 s |
| GeoNames US postal codes (CC BY 4.0) | zip → city, state, lat/lon | bundled `data/zip_to_city.csv` | offline |
| Census ACS 5-yr table B25035 (median year structure built) | lead-pipe housing-age signal | bundled `data/housing_age.csv`, built from `https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25035.dat` (no key) | offline |
| EPA SDWA reference codes (from ECHO `SDWA_latest_downloads.zip`) | official names for contaminant / rule / violation codes | bundled `data/sdwa_ref_codes.csv` | offline |
| California SAFER risk assessment (data.ca.gov CKAN resource `255887bb-5451-4c19-8e35-27899ae8c3ad`) | state-level data (CA) | `https://data.ca.gov/api/3/action/datastore_search?resource_id=...&filters={"WATER_SYSTEM_NUMBER":"CA1910067"}` | ~0.4 s |

### Envirofacts facts
- URL pattern: `{TABLE}/{column}/{operator}/{value}/.../{rows}/JSON`; column names **lowercase**; operators
  `equals`, `beginsWith`, `notEquals`, `lessThan`, `greaterThan`, `contains`; row range `.../1:100/JSON`.
- Bad query → HTTP 200 with `{"error": "..."}`. Empty → `[]`. No count endpoint.
- Tables: `SDWIS.WATER_SYSTEM`, `SDWIS.GEOGRAPHIC_AREA`, `SDWIS.VIOLATION`, `SDWIS.LCR_SAMPLE_RESULT`, `SDWIS.LCR_SAMPLE`.
- `compliance_status_code` (official): `O` Open, `K` Known (not returned to compliance), `R` Returned to Compliance, `I` System Inactive.

### Boundary layer facts
- Point query: `query?geometry={lon},{lat}&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=PWSID,PWS_Name,Primacy_Agency,Population_Served_Count,Symbology_Field&returnGeometry=false&f=json`
- Neighbors: same plus `&distance=5&units=esriSRUnit_StatuteMile`.
- Simplified outline for drawing: `returnGeometry=true&outSR=4326&maxAllowableOffset=0.001` (rings of [lon, lat]).
- `Symbology_Field`: `"System Sourced"` (boundary reported by state/utility) or `"Modeled"` (EPA machine-learning estimate).
- Covers ~44,000 community water systems (99% of people on public water). Includes NYC, Chicago, Seattle, which the
  zip strategies below miss.

### Lead service line inventories
EPA's per-system Service Line Inventory dashboard (Nov 2025) is behind an APEX app with session-checksum protection
and is not in Envirofacts or the ECHO bulk files. We do not scrape it. Lead-pipe signals we use instead:
the system's own LSL-inventory violations (contaminant `5200`, violation code `2E` = "LSL Inventory"),
neighborhood housing age (homes built before 1986, when lead pipes were banned), and lead 90th-percentile tests.

## Matching a search to water systems

| match_type         | How                                                                          | Label shown to user                          |
|--------------------|------------------------------------------------------------------------------|----------------------------------------------|
| `address_boundary` | geocoded address point inside the system's service-area polygon             | "Your address is in this service area"       |
| `zip_boundary`     | zip's center point (GeoNames lat/lon) inside the polygon                    | "Serves the center of your zip code"         |
| `service_area`     | `GEOGRAPHIC_AREA.zip_code_served == zip`                                     | "Serves your zip code"                       |
| `city_served`      | zip → city via `data/zip_to_city.csv`, `GEOGRAPHIC_AREA.city_served == CITY` | "Serves {City}"                              |
| `admin_address`    | `WATER_SYSTEM.zip_code beginsWith zip`                                       | "Based in your zip code"                     |

Ranked best → worst in that order. If `boundary_quality == "modeled"`, the web layer appends " (estimated boundary)".

Rules:
- Community water systems only (`CWS`, active). Same-state filter (`primacy_agency_code == search state`).
- Zip search: run `zip_boundary` + the three SDWIS strategies; union, dedupe by pwsid keeping the best match_type,
  sort by match rank then population desc, cap 10.
- Address search: `address_boundary` only (it is precise). If the point hits no polygon, fall back to a zip search on
  the geocoded zip, and keep `query.type == "address"`.
- `admin_address` cross-check against `city_served` (whole-word, e.g. `"EWING TWP.-1102,TRENTON CITY-1111"`):
  names the city → upgrade to `city_served`; names only other towns → drop, unless that leaves nothing.

## Layer 1: data (`app/data_store.py`)

```python
lookup_zip(zip: str) -> LookupResult
lookup_address(address: str) -> LookupResult      # never raises; same source chain as zips

LookupResult = {
    "zip": "48502",                                # the zip searched or geocoded (None if address not found)
    "source": "live" | "cache" | "fallback",
    "query": {
        "type": "zip" | "address",
        "input": "1600 Pennsylvania Ave NW, Washington DC",   # what the user typed
        "matched_address": "1600 PENNSYLVANIA AVE NW, WASHINGTON, DC, 20500" | None,
        "lat": 38.8987, "lon": -77.0352,           # address point, or zip center; None if unknown
        "state": "DC",
        "tract": "11001980000" | None,             # 11-digit census tract GEOID (address searches only)
        "not_found": False,                        # True if the address could not be geocoded
    },
    "systems": [WaterSystem, ...],                 # [] if nothing matched
}

WaterSystem = {
    "pwsid": "MI0002310",
    "name": "FLINT, CITY OF",                      # raw EPA name; web layer title-cases it
    "city": "FLINT", "state": "MI",
    "population_served": 81252,                    # int or None
    "match_type": "address_boundary" | "zip_boundary" | "service_area" | "city_served" | "admin_address",
    "boundary_quality": "reported" | "modeled" | None,     # None for non-boundary matches
    "boundary": [[[lon, lat], ...], ...] | None,   # simplified polygon rings, <= ~400 points total; None if unavailable
    "total_violation_count": 5,
    "violations": [Violation, ...],                # every CURRENT one (see below) + up to 5 most recent others, newest first
    "lead_90th": None | {"value_mg_l": 0.01, "sample_date": "2023-12-31"},
    "history": {
        "by_year": [{"year": 2017, "health_based": 0, "other": 2}, ...],   # exactly the last 10 calendar years incl. this one, ascending, zeros filled
        "lead_90th": [{"date": "2019-06-30", "value_mg_l": 0.006}, ...],     # every PB90 result with a date in the last 10 years, ascending
    },
    "contact": {                                   # from WATER_SYSTEM; organization info only, never ADMIN_NAME (a person)
        "org_name": "City of Flint" | None, "phone": "8107662000" | None, "email": "x@y.gov" | None,
        "address": "1101 S SAGINAW ST, FLINT, MI 48502" | None,
    },
}

Violation = {
    "violation_id": "486", "contaminant_code": "5000", "violation_code": "52",
    "category_code": "MCL" | "MRDL" | "TT" | "MR" | "MON" | "RPT" | "Other",
    "rule_code": "350",
    "is_health_based": True,
    "status": "open" | "known" | "resolved" | "archived",   # O / K / R / anything else
    "begin_date": "2020-07-01" | None, "end_date": None, "returned_to_compliance_date": "2020-12-31" | None,
    "measure": 41.0 | None, "unit": "mg/L" | None, "state_mcl": 0.01 | None,
    "notification_tier": 1 | 2 | 3 | None,
}
```

A violation is **current** if `status == "open"`, or `status == "known"` and `begin_date` is within the last 5 years
(EPA "Known" = never returned to compliance). `history.by_year` counts violations by the year of `begin_date`
using ALL violations on record (before trimming).

Source chain (both lookups): fresh SQLite cache (< 7 days) → live → stale cache → `data/fallback.json` → empty.
Never raises. `WQT_OFFLINE=1` skips live. Fallback/cache keys: `zip:48502`, `addr:<normalized address>`.

## Layer 1b: insights (`app/details.py`)

```python
get_details(pwsid: str, state: str, lat: float|None, lon: float|None, zip_code: str|None, tract: str|None) -> Details
# never raises; each part independently None/[] if unavailable; cached like lookups; offline uses bundled data + fallback

Details = {
    "comparison": {
        "state":    {"code": "MI", "name": "Michigan", "systems": 1380, "pct_current_health_based": 2.4, "pct_health_based_5yr": 18.0},
        "national": {"systems": 49000, "pct_current_health_based": 3.1, "pct_health_based_5yr": 20.5},
        "neighbors": [{"pwsid": "MI0001010", "name": "BURTON, CITY OF", "population_served": 21000,
                       "has_current_health_based": False, "has_current_any": True}, ...],   # <= 8 nearest-by-size within 5 mi, excluding itself; [] offline
        "as_of": "2026-10-07",                     # when state_stats.json was built
    } | None,
    "state_report": {                              # only for states with an adapter (California today)
        "state": "CA", "agency": "California State Water Resources Control Board",
        "program": "SAFER Drinking Water Risk Assessment",
        "status": "Failing" | "At-Risk" | "Potentially At-Risk" | "Not At-Risk" | "Not Assessed",
        "failing_since": "2019-01-01" | None,
        "state_violations": [{"kind": "Primary MCL" | "Secondary MCL" | "E. coli" | "Treatment Technique" | "Monitoring and Reporting" | "Source Capacity", "analytes": "1,2,3-TRICHLOROPROPANE"}],
        "source_url": "https://data.ca.gov/dataset/safer-failing-and-at-risk-drinking-water-systems",
    } | None,
    "lead_pipes": {
        "housing": {"geo": "tract" | "zip", "median_year_built": 1948} | None,
        "state_lsl_estimate": {"count": 1040000, "source": "EPA 7th DWINSA (2025)"} | None,
    },
}
```

## Layer 2: translation (`app/translate.py`, `app/guidance.py`, `app/codes.py`)

```python
translate_system(system: WaterSystem) -> Translation
Translation = {
    "status": "green" | "yellow" | "red",
    "status_label": "No current violations" | "Open paperwork/monitoring issue" | "Active health-based violation",
    "sentences": ["...", "..."],                   # 1-3 plain-English sentences
    "trend": {"direction": "improving" | "worsening" | "steady" | "no_history", "sentence": "..."},
    "guidance": [{"title": "Use a certified filter for lead", "text": "...", "link": "https://..." | None}],  # 0-4, most relevant first
}

translate_details(details: Details, system: WaterSystem) -> DetailsTranslation
DetailsTranslation = {
    "comparison_sentences": ["..."],               # 0-2
    "state_sentences": ["..."],                    # 0-2
    "lead_pipe": {"level": "elevated" | "typical" | "unknown", "sentences": ["..."]},   # 1-3 sentences
}
```
Status: `red` = any current health-based violation; `yellow` = current violations, none health-based; `green` = none current.
"Health-based" always means `rules.counts_as_health_based(v)`: EPA's `is_health_based`, except lead service line
inventory violations (contaminant `5200`), which are record-keeping. Badge, trend, guidance, comparisons and
`data/state_stats.json` all use this one rule.

## Layer 3: HTTP API (`app/main.py`, Flask)

`GET /api/lookup?zip=48502` or `GET /api/lookup?address=1600+Pennsylvania+Ave+NW,+Washington+DC`

```json
{
  "found": true, "source": "live", "message": null,
  "query": {"type": "zip", "input": "48502", "matched_address": null, "lat": 43.0, "lon": -83.7, "state": "MI", "tract": null, "zip": "48502"},
  "systems": [
    {"pwsid": "MI0002310", "name": "Flint, City of", "city": "Flint", "state": "MI",
     "population_served": 81252, "match_type": "zip_boundary", "match_label": "Serves the center of your zip code",
     "status": "green", "status_label": "No current violations", "sentences": ["..."],
     "trend": {...}, "guidance": [...], "history": {...}, "boundary": [...] , "contact": {...}, "lead_90th": {...}}
  ]
}
```
`GET /api/details?pwsid=MI0002310&state=MI&lat=43.01&lon=-83.69&zip=48502&tract=` →
`{"comparison": {...raw...}, "comparison_sentences": [...], "state_report": {...}|null, "state_sentences": [...], "lead_pipe": {...}, "lead_housing": {...}|null}`

- No match → `200 {"found": false, "systems": [], "message": "We don't have data for this zip code yet."}`
- Address not geocoded → `200 {"found": false, "message": "We couldn't find that address. Try adding the city and state, or search by zip code."}`
- Invalid zip → `400 {"error": "Please enter a 5-digit zip code."}`; address shorter than 5 chars or longer than 200 → `400`.
- Any unexpected exception → `200 {"found": false, "message": "Something went wrong..."}`; never a 500 or stack trace.
