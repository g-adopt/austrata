"""Standalone smoke health check for the live Geoscience Australia services.

Runs the fast nightly subset of GA_SERVER_TEST_PLAN.md (the same checks marked
``smoke`` in the test suite) without pytest, prints a concise pass/fail line per
check, and exits non-zero if any fail. This is what a scheduler / cron / CI can
call before a production pull.

Usage::

    python scripts/ga_healthcheck.py
"""
from __future__ import annotations

import socket
import ssl
import sys

from gadata.infrastructure.http import HttpClient

WFS_BASE = "https://services.ga.gov.au/gis/boreholes/wfs"
HYDRO_BASE = (
    "https://services.ga.gov.au/gis/rest/services/"
    "Hydrogeology_of_Australia/MapServer"
)
ACT_BBOX = "148.9,-35.6,149.3,-35.1"
COUNT_BOREHOLES = 52338
TOLERANCE = 0.20

_http = HttpClient(politeness_delay=0.15)


def _number_matched(text: str) -> int:
    import re
    m = re.search(r'numberMatched=["\'](\d+)["\']', text)
    if not m:
        raise AssertionError("numberMatched not found")
    return int(m.group(1))


def c1_tls():
    ctx = ssl.create_default_context()
    with socket.create_connection(("services.ga.gov.au", 443), timeout=15) as s:
        with ctx.wrap_socket(s, server_hostname="services.ga.gov.au") as ss:
            assert ss.getpeercert()


def c2_wfs_capabilities():
    r = _http.get(WFS_BASE, params={"service": "WFS", "version": "2.0.0",
                                    "request": "GetCapabilities"})
    assert r.status_code == 200 and "xml" in r.headers.get("Content-Type", "").lower()


def c3_arcgis_service():
    r = _http.get(HYDRO_BASE, params={"f": "json"})
    assert r.status_code == 200 and isinstance(r.json(), dict)


def c6_no_html():
    r = _http.get(WFS_BASE, params={"service": "WFS", "version": "2.0.0",
                                    "request": "GetCapabilities"})
    assert "<html" not in r.text[:500].lower()


def c8_layer0():
    assert _http.get(f"{HYDRO_BASE}/0", params={"f": "json"}).status_code == 200


def w4_hits():
    r = _http.get(WFS_BASE, params={"service": "WFS", "version": "2.0.0",
                                    "request": "GetFeature",
                                    "typeNames": "gsmlp:BoreholeView",
                                    "resultType": "hits"})
    assert _number_matched(r.text) >= 0


def a1_service_layers():
    assert _http.get(HYDRO_BASE, params={"f": "json"}).json().get("layers")


def a2_layer0_json():
    assert _http.get(f"{HYDRO_BASE}/0", params={"f": "json"}).json().get("name")


def a3_polygon():
    info = _http.get(f"{HYDRO_BASE}/0", params={"f": "json"}).json()
    assert info.get("geometryType") == "esriGeometryPolygon"


def a5_pagination():
    info = _http.get(f"{HYDRO_BASE}/0", params={"f": "json"}).json()
    assert info["advancedQueryCapabilities"]["supportsPagination"] is True


def a9_outsr():
    r = _http.get(f"{HYDRO_BASE}/0/query", params={
        "where": "1=1", "resultRecordCount": 1, "f": "json", "outSR": 4283,
        "returnGeometry": "true"})
    sr = r.json().get("spatialReference", {})
    assert sr.get("wkid") == 4283 or sr.get("latestWkid") == 4283


def d1_header_sample():
    r = _http.get(WFS_BASE, params={"service": "WFS", "version": "2.0.0",
                                    "request": "GetFeature",
                                    "typeNames": "gsmlp:BoreholeView", "count": 50,
                                    "outputFormat": "application/json",
                                    "bbox": f"{ACT_BBOX},EPSG:4283"})
    assert len(r.json()["features"]) > 0


def i1_count():
    r = _http.get(WFS_BASE, params={"service": "WFS", "version": "2.0.0",
                                    "request": "GetFeature",
                                    "typeNames": "gsmlp:BoreholeView",
                                    "resultType": "hits"})
    n = _number_matched(r.text)
    assert abs(n - COUNT_BOREHOLES) <= COUNT_BOREHOLES * TOLERANCE, f"count {n}"


def i6_hydro_count():
    r = _http.get(f"{HYDRO_BASE}/0/query", params={
        "where": "1=1", "returnCountOnly": "true", "f": "json"})
    assert int(r.json()["count"]) > 0


CHECKS = [
    ("C1 WFS TLS", c1_tls),
    ("C2 WFS GetCapabilities", c2_wfs_capabilities),
    ("C3 ArcGIS service", c3_arcgis_service),
    ("C6 no HTML maintenance page", c6_no_html),
    ("C8 ArcGIS layer 0 status", c8_layer0),
    ("W4 WFS hits numberMatched", w4_hits),
    ("A1 ArcGIS lists layers", a1_service_layers),
    ("A2 ArcGIS layer 0 JSON", a2_layer0_json),
    ("A3 ArcGIS polygon geometry", a3_polygon),
    ("A5 ArcGIS pagination", a5_pagination),
    ("A9 ArcGIS outSR=4283", a9_outsr),
    ("D1 header sample in ACT bbox", d1_header_sample),
    ("I1 borehole count drift", i1_count),
    ("I6 hydro count nonzero", i6_hydro_count),
]


def main() -> int:
    print("GA services smoke health check\n" + "=" * 40)
    failures = []
    for name, fn in CHECKS:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001 - we want any failure reported
            failures.append((name, exc))
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print("=" * 40)
    if failures:
        print(f"{len(failures)}/{len(CHECKS)} checks FAILED")
        return 1
    print(f"All {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
