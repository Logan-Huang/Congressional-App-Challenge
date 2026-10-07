"""California adapter: the State Water Board's SAFER risk-assessment table (data.ca.gov).

One row per public water system, keyed by WATER_SYSTEM_NUMBER (which is the same
as the EPA PWSID, e.g. CA1910067).
"""

import json

import requests

RESOURCE_ID = "255887bb-5451-4c19-8e35-27899ae8c3ad"
API_URL = "https://data.ca.gov/api/3/action/datastore_search"
SOURCE_URL = "https://data.ca.gov/dataset/safer-failing-and-at-risk-drinking-water-systems"

# (yes/no column, analytes column, label shown to the translation layer)
VIOLATION_FIELDS = [
    ("PRIMARY_MCL_VIOLATION", "PRIMARY_ANALYTES", "Primary MCL"),
    ("SECONDARY_MCL_VIOLATION", "SECONDARY_ANALYTES", "Secondary MCL"),
    ("E_COLI_VIOLATION", "E_COLI_ANALYTES", "E. coli"),
    ("TREATMENT_TECHNIQUE_VIOLATION", "TT_ANALYTES", "Treatment Technique"),
    ("MONITORING_AND_REPORTING_VIOLATION", "MONITORING_AND_REPORTING_ANALYTES", "Monitoring and Reporting"),
    ("SOURCE_CAPACITY_VIOLATION", "SOURCE_CAPACITY_ANALYTES", "Source Capacity"),
]
KNOWN_STATUSES = {"Failing", "At-Risk", "Potentially At-Risk", "Not At-Risk", "Not Assessed"}


def _clean(value):
    """The table uses 'N/A' and blanks for 'nothing'. Return None for those."""
    text = str(value).strip() if value is not None else ""
    return None if text.upper() in ("", "N/A", "NA", "NONE", "NO") else text


def map_row(row):
    """Turn one SAFER row into the contract's state_report dict."""
    status = (row.get("FINAL_SAFER_STATUS") or "").strip()
    if status not in KNOWN_STATUSES:
        status = "Not Assessed"

    # Only "Failing" systems have a meaningful failing-since date.
    failing_since = None
    if str(row.get("CURRENT_FAILING", "")).strip().lower() == "failing" or status == "Failing":
        failing_since = _clean(row.get("FAILING_START_DATE"))
        if failing_since:
            failing_since = failing_since[:10]

    violations = []
    for flag_col, analytes_col, kind in VIOLATION_FIELDS:
        if _clean(row.get(flag_col)) is None:  # "NO" / "N/A" -> skip
            continue
        violations.append({"kind": kind, "analytes": _clean(row.get(analytes_col)) or ""})

    return {
        "state": "CA",
        "agency": "California State Water Resources Control Board",
        "program": "SAFER Drinking Water Risk Assessment",
        "status": status,
        "failing_since": failing_since,
        "state_violations": violations,
        "source_url": SOURCE_URL,
    }


class CaliforniaAdapter:
    STATE = "CA"

    def fetch(self, pwsid):
        response = requests.get(
            API_URL,
            params={"resource_id": RESOURCE_ID, "filters": json.dumps({"WATER_SYSTEM_NUMBER": pwsid})},
            timeout=6,
        )
        response.raise_for_status()
        records = response.json().get("result", {}).get("records", [])
        return map_row(records[0]) if records else None


ADAPTER = CaliforniaAdapter()
