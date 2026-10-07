"""Thin wrapper around the EPA Envirofacts REST API.

URL pattern (see docs/DATA_CONTRACT.md):
    https://data.epa.gov/efservice/{TABLE}/{column}/{operator}/{value}/.../{rows}/JSON

This file only knows how to ask the API questions. Deciding what the answers
mean is data_store.py's job.
"""

from urllib.parse import quote

import requests

BASE_URL = "https://data.epa.gov/efservice"

# One shared Session reuses the HTTPS connection, which saves ~0.3s per call.
_session = requests.Session()


class EpaApiError(Exception):
    """Raised for anything that stops us getting a clean answer from EPA."""


def query(table, filters, rows=None, timeout=8):
    """Run one Envirofacts query and return a list of row dicts.

    table   -- e.g. "SDWIS.WATER_SYSTEM"
    filters -- list of (column, operator, value) tuples, e.g. [("pwsid", "equals", "MI0002310")]
    rows    -- optional "first:last" range string, e.g. "1:50"
    """
    parts = [table]
    for column, operator, value in filters:
        # safe="" so a "/" inside a value cannot break the URL path.
        parts += [column, operator, quote(str(value), safe="")]
    if rows:
        parts.append(rows)
    parts.append("JSON")
    url = BASE_URL + "/" + "/".join(parts)

    try:
        response = _session.get(url, timeout=timeout)
    except requests.RequestException as exc:  # timeouts, DNS, connection resets
        raise EpaApiError(f"Request failed: {exc}") from exc

    if response.status_code != 200:
        raise EpaApiError(f"EPA API returned HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError as exc:
        raise EpaApiError("EPA API did not return JSON") from exc

    # A bad query comes back as HTTP 200 with {"error": "..."}.
    if isinstance(data, dict):
        raise EpaApiError(f"EPA API error: {data.get('error', data)}")
    if not isinstance(data, list):
        raise EpaApiError("Unexpected response shape from EPA API")
    return data


# Community water systems that are still active: the only kind we show.
_CWS_ACTIVE = [("pws_type_code", "equals", "CWS"), ("pws_activity_code", "equals", "A")]


def systems_by_zip_served(zip_code):
    """Strategy 1: systems that list this zip as part of their service area."""
    return query(
        "SDWIS.GEOGRAPHIC_AREA",
        [("zip_code_served", "equals", zip_code)] + _CWS_ACTIVE,
        rows="1:100",
    )


def systems_by_city_served(city, state):
    """Strategy 2: systems that list this city as served, within the zip's state."""
    return query(
        "SDWIS.GEOGRAPHIC_AREA",
        [("city_served", "equals", city), ("primacy_agency_code", "equals", state)] + _CWS_ACTIVE,
        rows="1:100",
    )


def systems_by_admin_zip(zip_code, state):
    """Strategy 3: systems whose office (admin address) is in this zip."""
    return query(
        "SDWIS.WATER_SYSTEM",
        [("zip_code", "beginsWith", zip_code), ("primacy_agency_code", "equals", state)] + _CWS_ACTIVE,
        rows="1:100",
    )


def service_areas(pwsid):
    """The cities/counties one water system says it serves (GEOGRAPHIC_AREA rows)."""
    return query("SDWIS.GEOGRAPHIC_AREA", [("pwsid", "equals", pwsid)], rows="1:50")


def water_system_details(pwsid):
    """Name, city, population etc. for one water system. Returns a list (0 or 1 rows)."""
    return query("SDWIS.WATER_SYSTEM", [("pwsid", "equals", pwsid)])


def lead_90th_results(pwsid):
    """Lead 90th-percentile results (contaminant PB90). These rows carry no dates."""
    return query(
        "SDWIS.LCR_SAMPLE_RESULT",
        [("pwsid", "equals", pwsid), ("contaminant_code", "equals", "PB90")],
        rows="1:200",
    )


def lead_samples(pwsid):
    """Sampling periods for lead/copper sample rounds; join to results on sample_id for dates."""
    return query("SDWIS.LCR_SAMPLE", [("pwsid", "equals", pwsid)], rows="1:200")


def violations_by_pwsid(pwsid):
    """Every violation on record for one water system (can be a few hundred rows)."""
    return query("SDWIS.VIOLATION", [("pwsid", "equals", pwsid)], rows="1:2000", timeout=15)
