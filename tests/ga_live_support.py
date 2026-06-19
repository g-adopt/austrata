"""Shared constants and helpers for the live GA-server health/contract tests.

These tests verify the *upstream* Geoscience Australia services — reachability,
advertised capabilities, schema, CRS, and that real data downloads — not austrata
itself. A failure means the GA service drifted or went down. They are polite:
small counts, the shared HttpClient politeness delay, a descriptive User-Agent.
"""
from __future__ import annotations

import requests

from austrata.infrastructure.http import HttpClient

WFS_BASE = "https://services.ga.gov.au/gis/boreholes/wfs"
HYDRO_BASE = (
    "https://services.ga.gov.au/gis/rest/services/"
    "Hydrogeology_of_Australia/MapServer"
)

# Reference region (ACT/Canberra, lon/lat GDA94) used throughout the plan.
ACT_BBOX_STR = "148.9,-35.6,149.3,-35.1"
ACT_BBOX = (148.9, -35.6, 149.3, -35.1)
# Known boreholes carrying logs (verified by probe).
LOGGED_ENOS = (35147, 35151)

# Australian continental bounds for plausibility checks (lon/lat).
AUS_LON = (112.0, 154.0)
AUS_LAT = (-44.0, -10.0)

# National counts at probe time; integrity checks allow +/-20% drift.
COUNT_BOREHOLES = 52338
COUNT_STRATIGRAPHY = 190016
COUNT_EARTH_MATERIAL = 551852
COUNT_TOLERANCE = 0.20

# Verified log depth schema (DescribeFeatureType, both log layers; all decimal).
# Depths are metres measured DOWN from the depth reference point; the reference
# point elevation is metres AHD (Australian Height Datum), the vertical datum.
DEPTH_TOP_FIELD = "INTERVAL_BEGIN_M"          # top depth, m below ref point
DEPTH_BOTTOM_FIELD = "INTERVAL_END_M"         # bottom depth, m (>= begin)
DEPTH_LENGTH_FIELD = "INTERVAL_LENGTH_M"      # thickness, m (= end - begin)
DEPTH_REF_ELEV_FIELD = "DEPTH_REF_POINT_ELEV_M_AHD"   # ref-point elevation, m AHD
# Per-interval absolute elevation fields exist but are routinely null; when
# populated, absolute elevation = these, else ref_elev_m_AHD - depth_m.
DEPTH_TOP_ELEV_FIELD = "INTERVAL_BEGIN_ELEV_M_AHD"
DEPTH_BOTTOM_ELEV_FIELD = "INTERVAL_END_ELEV_M_AHD"


def http() -> HttpClient:
    """A polite shared HTTP client for the raw-request checks."""
    return HttpClient(politeness_delay=0.15)


def within_tolerance(value: float, expected: float, frac: float = COUNT_TOLERANCE) -> bool:
    return abs(value - expected) <= expected * frac


def in_australia(lon: float, lat: float) -> bool:
    return AUS_LON[0] <= lon <= AUS_LON[1] and AUS_LAT[0] <= lat <= AUS_LAT[1]


def first_coord(geometry: dict):
    """First (lon, lat) pair from a Point/Polygon/MultiPolygon GeoJSON geometry."""
    coords = geometry["coordinates"]
    if geometry["type"] == "Point":
        return coords[0], coords[1]
    while isinstance(coords[0][0], (list, tuple)):
        coords = coords[0]
    return coords[0][0], coords[0][1]


def iter_coords(geometry: dict):
    """Yield every (lon, lat) vertex of a Polygon/MultiPolygon geometry."""
    def walk(node):
        if isinstance(node[0], (int, float)):
            yield node[0], node[1]
            return
        for child in node:
            yield from walk(child)

    yield from walk(geometry["coordinates"])


def wfs_hits(typename: str, bbox: str | None = None) -> requests.Response:
    """Raw WFS resultType=hits request (returns the Response for header checks)."""
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": typename, "resultType": "hits",
    }
    if bbox is not None:
        params["bbox"] = bbox
    return http().get(WFS_BASE, params=params)


def parse_number_matched(text: str) -> int:
    import re

    m = re.search(r'numberMatched=["\'](\d+)["\']', text)
    if not m:
        raise AssertionError("numberMatched not found in WFS hits response")
    return int(m.group(1))
