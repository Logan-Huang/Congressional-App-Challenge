"""Location helpers: address -> point (Census geocoder) and point -> water systems (EPA boundaries).

Like epa_client.py, this file only asks questions. data_store.py decides what the
answers mean. Every network failure becomes a GeoError so the caller can fall back
to cached or bundled data instead of crashing.

Both services are free and need no API key (see docs/DATA_CONTRACT.md).
"""

import re

import requests

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
BOUNDARY_URL = (
    "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/"
    "Water_System_Boundaries/FeatureServer/0/query"
)
TIMEOUT_SECONDS = 5  # two of these plus the EPA calls must fit in the lookup deadline
MAX_OUTLINE_POINTS = 400       # enough to draw a recognizable shape, small enough to cache and ship
OUTLINE_OFFSET_DEGREES = 0.001  # ArcGIS simplifies server-side: ~100 m of detail is plenty for a map

# One shared Session reuses HTTPS connections (same idea as epa_client).
_session = requests.Session()


class GeoError(Exception):
    """Raised when the geocoder or boundary service cannot give us a clean answer."""


def _get_json(url, params):
    try:
        response = _session.get(url, params=params, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:  # timeouts, DNS, connection resets
        raise GeoError(f"Request failed: {exc}") from exc
    if response.status_code != 200:
        raise GeoError(f"Service returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise GeoError("Service did not return JSON") from exc
    if not isinstance(data, dict):
        raise GeoError("Unexpected response shape")
    # ArcGIS reports problems as HTTP 200 with an "error" object.
    if "error" in data:
        raise GeoError(f"Service error: {data['error']}")
    return data


# --------------------------------------------------------------------------
# Address -> point
# --------------------------------------------------------------------------

def geocode_address(address):
    """Look an address up with the Census geocoder.

    Returns {"matched_address", "lat", "lon", "zip", "state", "tract"} or None when
    the Census has no match. Raises GeoError if the service itself fails.
    """
    data = _get_json(CENSUS_URL, {
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "Census Tracts",
        "format": "json",
    })
    matches = (data.get("result") or {}).get("addressMatches") or []
    if not matches:
        return None
    best = matches[0]  # the geocoder lists its best guess first
    try:
        lon, lat = float(best["coordinates"]["x"]), float(best["coordinates"]["y"])
    except (KeyError, TypeError, ValueError):
        raise GeoError("Geocoder match had no coordinates")
    parts = best.get("addressComponents") or {}
    tracts = (best.get("geographies") or {}).get("Census Tracts") or []
    return {
        "matched_address": best.get("matchedAddress"),
        "lat": lat,
        "lon": lon,
        "zip": parts.get("zip") or None,
        "state": parts.get("state") or None,
        "tract": tracts[0].get("GEOID") if tracts else None,  # 11 digits: state+county+tract
    }


# --------------------------------------------------------------------------
# Point -> water systems
# --------------------------------------------------------------------------

def systems_at_point(lat, lon):
    """Water systems whose service-area polygon contains this point.

    Returns a list of {"pwsid", "name", "state", "population_served", "quality"}
    where quality is "reported" (system/state supplied) or "modeled" (EPA estimate).
    Raises GeoError if the service fails.
    """
    data = _get_json(BOUNDARY_URL, {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PWSID,PWS_Name,Primacy_Agency,Population_Served_Count,Symbology_Field",
        "returnGeometry": "false",
        "f": "json",
    })
    systems = []
    for feature in data.get("features") or []:
        attrs = feature.get("attributes") or {}
        if not attrs.get("PWSID"):
            continue
        systems.append({
            "pwsid": attrs["PWSID"],
            "name": attrs.get("PWS_Name"),
            "state": attrs.get("Primacy_Agency"),
            "population_served": attrs.get("Population_Served_Count"),
            "quality": "modeled" if attrs.get("Symbology_Field") == "Modeled" else "reported",
        })
    return systems


def decimate_rings(rings, max_points=MAX_OUTLINE_POINTS):
    """Shrink polygon rings so the total number of points is <= max_points.

    WHY: a big utility's outline can have thousands of points; the browser only
    needs the rough shape. We thin each ring evenly (keeping it closed) and drop
    the smallest rings first if there are too many of them.
    """
    rings = [r for r in rings if len(r) >= 4]
    if not rings:
        return []
    rings.sort(key=len, reverse=True)  # big rings matter most visually
    # Every ring needs at least 4 points (triangle + closing point).
    while len(rings) * 4 > max_points:
        rings.pop()
    total = sum(len(r) for r in rings)
    if total <= max_points:
        return rings

    # Give each ring a share of the budget proportional to its size.
    result = []
    for ring in rings:
        budget = max(4, int(max_points * len(ring) / total))
        if len(ring) > budget:
            step = (len(ring) - 1) / (budget - 1)
            thinned = [ring[round(i * step)] for i in range(budget)]  # first and last stay put
            ring = thinned
        result.append(ring)
    # Rounding up to the 4-point minimum could push us slightly over; trim the biggest ring.
    while sum(len(r) for r in result) > max_points:
        biggest = max(result, key=len)
        if len(biggest) <= 4:
            result.remove(biggest)
        else:
            del biggest[len(biggest) // 2]
    return result


def boundary_outline(pwsid):
    """Simplified service-area outline for one system: a list of rings of [lon, lat].

    Returns None if the system has no polygon. Raises GeoError if the service fails.
    """
    if not re.fullmatch(r"[A-Za-z0-9]{3,12}", pwsid or ""):
        return None  # the id goes into a query string, so only accept plain ids
    data = _get_json(BOUNDARY_URL, {
        "where": f"PWSID='{pwsid}'",
        "outFields": "PWSID",
        "returnGeometry": "true",
        "outSR": 4326,
        "maxAllowableOffset": OUTLINE_OFFSET_DEGREES,
        "f": "json",
    })
    rings = []
    for feature in data.get("features") or []:
        for ring in (feature.get("geometry") or {}).get("rings") or []:
            rings.append([[round(p[0], 5), round(p[1], 5)] for p in ring])
    rings = decimate_rings(rings)
    return rings or None
