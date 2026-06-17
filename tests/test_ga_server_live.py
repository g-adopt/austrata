"""Live GA-server health & contract tests (implements GA_SERVER_TEST_PLAN.md).

These hit the real Geoscience Australia services. They are gated by the ``live``
marker and further split into ``smoke`` (fast nightly subset), ``contract``
(weekly schema/contract subset), and ``heavy`` (on-demand only, skipped by
default). Each test name encodes its plan ID (e.g. ``test_C2_...``).

Run::

    pytest -m "live and smoke" -q       # nightly
    pytest -m "live and contract" -q    # weekly
    pytest -m "live and heavy" -q       # on demand
"""
from __future__ import annotations

import socket
import ssl
import xml.etree.ElementTree as ET

import pytest

from tests.ga_live_support import (
    ACT_BBOX,
    ACT_BBOX_STR,
    COUNT_BOREHOLES,
    COUNT_EARTH_MATERIAL,
    COUNT_STRATIGRAPHY,
    DEPTH_BOTTOM_FIELD,
    DEPTH_LENGTH_FIELD,
    DEPTH_REF_ELEV_FIELD,
    DEPTH_TOP_FIELD,
    HYDRO_BASE,
    LOGGED_ENOS,
    WFS_BASE,
    first_coord,
    http,
    in_australia,
    iter_coords,
    parse_number_matched,
    within_tolerance,
    wfs_hits,
)

pytestmark = pytest.mark.live

EXPECTED_WFS_LAYERS = {
    "gsmlp:BoreholeView",
    "bh:BoreholeStratigraphyLogs",
    "bh:BoreholeEarthMaterialLogs",
    "bh:Boreholes",
    "bh:BoreholeConstructionLogs",
    "bh:BoreholeDirectionalSurveyStations",
    "bh:BoreholeSamples",
    "gsmlbh:Borehole",
}
EXPECTED_HYDRO_FIELDS = {"aquif_ty", "distbn", "prodty", "type", "feature", "ufi"}


# ======================================================================
# 1. Connectivity & server health
# ======================================================================

@pytest.mark.smoke
def test_C1_wfs_tls_resolves():
    """C1: DNS + TLS handshake to services.ga.gov.au:443 with a valid cert."""
    ctx = ssl.create_default_context()
    with socket.create_connection(("services.ga.gov.au", 443), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname="services.ga.gov.au") as ssock:
            assert ssock.getpeercert()  # chain verified, non-expired


@pytest.mark.smoke
def test_C2_wfs_getcapabilities_responds():
    """C2: WFS GetCapabilities returns HTTP 200."""
    r = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetCapabilities"})
    assert r.status_code == 200


@pytest.mark.smoke
def test_C3_arcgis_service_responds():
    """C3: ArcGIS MapServer?f=json returns HTTP 200 + JSON."""
    r = http().get(HYDRO_BASE, params={"f": "json"})
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


@pytest.mark.smoke
def test_C6_no_maintenance_html_page():
    """C6: WFS is XML and ArcGIS is JSON (not an HTML maintenance page)."""
    w = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetCapabilities"})
    assert "xml" in w.headers.get("Content-Type", "").lower()
    assert "<html" not in w.text[:500].lower()
    a = http().get(HYDRO_BASE, params={"f": "json"})
    assert "json" in a.headers.get("Content-Type", "").lower()


@pytest.mark.contract
def test_C7_no_offsite_redirect():
    """C7: final URL stays on services.ga.gov.au."""
    r = http().get(HYDRO_BASE, params={"f": "json"})
    assert "services.ga.gov.au" in r.url


@pytest.mark.smoke
def test_C8_layer0_trivial_query_status():
    """C8: MapServer/0?f=json returns 200 (not 403/500/503)."""
    r = http().get(f"{HYDRO_BASE}/0", params={"f": "json"})
    assert r.status_code == 200


# ======================================================================
# 2. WFS capability / contract
# ======================================================================

def _capabilities_root():
    r = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetCapabilities"})
    return ET.fromstring(r.text)


@pytest.mark.contract
def test_W1_getcapabilities_valid_xml():
    """W1: GetCapabilities parses as XML."""
    root = _capabilities_root()
    assert root.tag.endswith("WFS_Capabilities")


@pytest.mark.contract
def test_W2_expected_layers_advertised():
    """W2: all expected feature types are advertised."""
    root = _capabilities_root()
    names = {e.text for e in root.iter() if e.tag.endswith("}Name") and e.text}
    missing = EXPECTED_WFS_LAYERS - names
    assert not missing, f"WFS missing advertised layers: {missing}"


@pytest.mark.contract
def test_W3_json_output_supported():
    """W3: application/json is offered for GetFeature."""
    r = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "gsmlp:BoreholeView", "count": 1,
        "outputFormat": "application/json"})
    assert r.status_code == 200
    assert "json" in r.headers.get("Content-Type", "").lower()


@pytest.mark.smoke
def test_W4_hits_returns_number_matched():
    """W4: resultType=hits carries a parseable numberMatched."""
    r = wfs_hits("gsmlp:BoreholeView")
    assert r.status_code == 200
    assert parse_number_matched(r.text) >= 0


@pytest.mark.contract
def test_W5_header_schema_unchanged():
    """W5: header GeoJSON exposes lowercase eno + mapped header fields."""
    r = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "gsmlp:BoreholeView", "count": 1,
        "outputFormat": "application/json", "bbox": f"{ACT_BBOX_STR},EPSG:4283"})
    props = r.json()["features"][0]["properties"]
    assert "eno" in props
    for field in ("name", "state", "elevation_m"):
        assert field in props, f"header field {field!r} missing"


@pytest.mark.contract
def test_W6_stratigraphy_schema_unchanged():
    """W6: stratigraphy log exposes ENO/PID/NAME, the metre depth schema, + unit.

    Depth schema is the verified DescribeFeatureType set: INTERVAL_BEGIN_M /
    INTERVAL_END_M / INTERVAL_LENGTH_M (metres below the ref point) and
    DEPTH_REF_POINT_ELEV_M_AHD (ref-point elevation, m AHD).
    """
    props = _one_log("bh:BoreholeStratigraphyLogs")
    for field in ("ENO", "BOREHOLE_PID", "BOREHOLE_NAME", "STRAT_UNIT_NAME",
                  DEPTH_TOP_FIELD, DEPTH_BOTTOM_FIELD, DEPTH_LENGTH_FIELD,
                  DEPTH_REF_ELEV_FIELD):
        assert field in props, f"stratigraphy field {field!r} missing"
    for field in (DEPTH_TOP_FIELD, DEPTH_BOTTOM_FIELD):
        if props[field] is not None:
            float(props[field])  # numeric/parseable


@pytest.mark.contract
def test_W7_earth_material_schema_unchanged():
    """W7: earth-material log exposes ENO/PID, the metre depth schema, + material."""
    props = _one_log("bh:BoreholeEarthMaterialLogs")
    for field in ("ENO", "BOREHOLE_PID", "LITHOLOGY",
                  DEPTH_TOP_FIELD, DEPTH_BOTTOM_FIELD, DEPTH_LENGTH_FIELD,
                  DEPTH_REF_ELEV_FIELD):
        assert field in props, f"earth-material field {field!r} missing"
    for field in (DEPTH_TOP_FIELD, DEPTH_BOTTOM_FIELD):
        if props[field] is not None:
            float(props[field])  # numeric/parseable


def _one_log(typename: str) -> dict:
    r = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": typename, "count": 1, "outputFormat": "application/json"})
    return r.json()["features"][0]["properties"]


@pytest.mark.contract
def test_W8_native_crs_is_4283():
    """W8: header GeoJSON declares GDA94 / EPSG:4283."""
    r = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "gsmlp:BoreholeView", "count": 1,
        "outputFormat": "application/json", "bbox": f"{ACT_BBOX_STR},EPSG:4283"})
    crs = r.json().get("crs", {}).get("properties", {}).get("name", "")
    assert "4283" in crs


@pytest.mark.contract
def test_W9_bbox_without_crs_400s():
    """W9: bbox missing its CRS suffix returns HTTP 400 (regression guard)."""
    import requests
    with pytest.raises(requests.HTTPError) as exc:
        http().get(WFS_BASE, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "gsmlp:BoreholeView", "count": 1,
            "outputFormat": "application/json", "bbox": ACT_BBOX_STR})
    assert exc.value.response.status_code == 400


@pytest.mark.contract
def test_W10_bbox_epsg_shortform_is_lonlat():
    """W10: EPSG:4283 short form returns features inside the ACT box."""
    r = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "gsmlp:BoreholeView", "count": 50,
        "outputFormat": "application/json", "bbox": f"{ACT_BBOX_STR},EPSG:4283"})
    feats = r.json()["features"]
    assert len(feats) > 0
    for f in feats:
        lon, lat = first_coord(f["geometry"])
        assert ACT_BBOX[0] <= lon <= ACT_BBOX[2]
        assert ACT_BBOX[1] <= lat <= ACT_BBOX[3]


@pytest.mark.contract
def test_W11_urn_form_does_not_behave_as_lonlat():
    """W11: the urn CRS form is NOT a drop-in for the short EPSG:4283 form.

    The plan expected the urn form to flip to lat/lon axis order. Live, this
    WFS instead rejects our lon/lat box under the urn CRS outright (HTTP 400) —
    because read as lat/lon the coordinates are out of range. Either way the urn
    form does not return the ACT lon/lat box, which is what justifies the Region
    value object emitting the short ``EPSG:4283`` form. Accept either outcome:
    a 400, or a 200 whose features fall outside the intended ACT box.
    """
    import requests
    try:
        r = http().get(WFS_BASE, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "gsmlp:BoreholeView", "count": 50,
            "outputFormat": "application/json",
            "bbox": f"{ACT_BBOX_STR},urn:ogc:def:crs:EPSG::4283"})
    except requests.HTTPError as exc:
        assert exc.response.status_code == 400  # rejected as out-of-range lat/lon
        return
    feats = r.json().get("features", [])
    in_act = [f for f in feats
              if ACT_BBOX[0] <= first_coord(f["geometry"])[0] <= ACT_BBOX[2]
              and ACT_BBOX[1] <= first_coord(f["geometry"])[1] <= ACT_BBOX[3]]
    assert len(in_act) == 0, "urn form unexpectedly matched the lon/lat box"


@pytest.mark.contract
def test_W12_wfs_sends_no_validators():
    """W12: WFS emits no ETag/Last-Modified (hits-fingerprint still needed)."""
    r = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "gsmlp:BoreholeView", "count": 1,
        "outputFormat": "application/json", "bbox": f"{ACT_BBOX_STR},EPSG:4283"})
    assert "ETag" not in r.headers
    assert "Last-Modified" not in r.headers


@pytest.mark.smoke
def test_W13_post_getfeature_accepted():
    """W13: POST GetFeature with an ENO IN (...) body returns features."""
    in_list = ",".join(str(e) for e in LOGGED_ENOS)
    r = http().post(WFS_BASE, data={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "bh:BoreholeStratigraphyLogs",
        "outputFormat": "application/json", "count": 5,
        "cql_filter": f"ENO IN ({in_list})"})
    assert r.status_code == 200
    assert len(r.json().get("features", [])) > 0


# ======================================================================
# 3. ArcGIS capability / contract
# ======================================================================

def _service_json():
    return http().get(HYDRO_BASE, params={"f": "json"}).json()


def _layer0_json():
    return http().get(f"{HYDRO_BASE}/0", params={"f": "json"}).json()


@pytest.mark.smoke
def test_A1_service_json_lists_layers():
    """A1: service JSON parses and lists layers."""
    info = _service_json()
    assert isinstance(info.get("layers"), list) and info["layers"]


@pytest.mark.smoke
def test_A2_layer0_json_reachable():
    """A2: layer 0 JSON reachable."""
    assert _layer0_json().get("name")


@pytest.mark.smoke
def test_A3_layer0_geometry_is_polygon():
    """A3: layer 0 geometryType is esriGeometryPolygon."""
    assert _layer0_json().get("geometryType") == "esriGeometryPolygon"


@pytest.mark.contract
def test_A4_max_record_count_2000():
    """A4: maxRecordCount unchanged at 2000 (pagination math)."""
    assert _layer0_json().get("maxRecordCount") == 2000


@pytest.mark.smoke
def test_A5_supports_pagination_true():
    """A5: advancedQueryCapabilities.supportsPagination is true."""
    caps = _layer0_json().get("advancedQueryCapabilities", {})
    assert caps.get("supportsPagination") is True


@pytest.mark.contract
def test_A6_field_list_unchanged():
    """A6: mapped hydrogeology fields still present."""
    names = {f["name"] for f in _layer0_json().get("fields", [])}
    missing = EXPECTED_HYDRO_FIELDS - names
    assert not missing, f"hydrogeology fields missing: {missing}"


@pytest.mark.contract
def test_A7_arcgis_sends_etag():
    """A7: a /query response carries an ETag + must-revalidate."""
    r = _arcgis_query({"where": "1=1", "resultRecordCount": 1,
                       "f": "geojson", "returnGeometry": "true"})
    assert "ETag" in r.headers
    assert "must-revalidate" in r.headers.get("Cache-Control", "").lower()


@pytest.mark.contract
def test_A8_editing_info_null():
    """A8: editingInfo/modified still null (ETag is the freshness signal)."""
    assert _service_json().get("modified") in (None, 0)
    assert _layer0_json().get("editingInfo") is None


@pytest.mark.smoke
def test_A9_outsr_4283_returns_wkid_4283():
    """A9: f=json&outSR=4283 returns spatialReference.wkid 4283."""
    r = _arcgis_query({"where": "1=1", "resultRecordCount": 1, "f": "json",
                       "outSR": 4283, "returnGeometry": "true"})
    sr = r.json().get("spatialReference", {})
    assert sr.get("wkid") == 4283 or sr.get("latestWkid") == 4283


@pytest.mark.contract
def test_A10_geojson_omits_crs():
    """A10: f=geojson omits the crs field (silent-4326 trap guard)."""
    r = _arcgis_query({"where": "1=1", "resultRecordCount": 1, "f": "geojson",
                       "returnGeometry": "true"})
    assert "crs" not in r.json()


@pytest.mark.contract
def test_A11_exceeded_transfer_limit_exposed():
    """A11: a query that exceeds the page size exposes exceededTransferLimit.

    ArcGIS only emits the flag when the limit is actually exceeded, so we force
    it with a national ``where=1=1`` capped at a tiny ``resultRecordCount`` —
    far fewer rows than exist, so the flag must appear and be True.
    """
    r = _arcgis_query({"where": "1=1", "f": "geojson", "returnGeometry": "true",
                       "resultRecordCount": 1})
    body = r.json()
    assert "exceededTransferLimit" in body
    assert body["exceededTransferLimit"] is True


def _arcgis_query(params: dict):
    return http().get(f"{HYDRO_BASE}/0/query", params=params)


# ======================================================================
# 4. Data download / sampling
# ======================================================================

@pytest.mark.smoke
def test_D1_header_sample_returns_features():
    """D1: ACT bbox header sample returns >0 Point features."""
    feats = _header_sample()
    assert len(feats) > 0
    assert all(f["geometry"]["type"] == "Point" for f in feats)


def _header_sample(count: int = 50):
    r = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "gsmlp:BoreholeView", "count": count,
        "outputFormat": "application/json", "bbox": f"{ACT_BBOX_STR},EPSG:4283"})
    return r.json()["features"]


@pytest.mark.contract
def test_D2_header_geometry_in_bounds():
    """D2: header coords are in Australia and inside the ACT box."""
    for f in _header_sample():
        lon, lat = first_coord(f["geometry"])
        assert in_australia(lon, lat)
        assert ACT_BBOX[0] <= lon <= ACT_BBOX[2]
        assert ACT_BBOX[1] <= lat <= ACT_BBOX[3]


@pytest.mark.contract
def test_D4_stratigraphy_for_known_enos():
    """D4: POST stratigraphy for ENO 35147,35151 returns rows."""
    feats = _logs("bh:BoreholeStratigraphyLogs")
    assert len(feats) > 0


@pytest.mark.contract
def test_D5_earth_material_for_known_enos():
    """D5: POST earth-material for ENO 35147,35151 returns rows."""
    feats = _logs("bh:BoreholeEarthMaterialLogs")
    assert len(feats) > 0


def _logs(typename: str):
    in_list = ",".join(str(e) for e in LOGGED_ENOS)
    r = http().post(WFS_BASE, data={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": typename, "outputFormat": "application/json", "count": 200,
        "cql_filter": f"ENO IN ({in_list})"})
    return r.json()["features"]


@pytest.mark.contract
def test_D6_log_depth_fields_sane():
    """D6: log depths are numeric, non-negative, top <= bottom.

    Depths are metres below the reference point (INTERVAL_BEGIN_M /
    INTERVAL_END_M); DEPTH_REF_POINT_ELEV_M_AHD is the ref-point elevation in
    metres AHD and must be numeric when present.
    """
    for typename in ("bh:BoreholeStratigraphyLogs", "bh:BoreholeEarthMaterialLogs"):
        for f in _logs(typename):
            p = f["properties"]
            top, bottom = p.get(DEPTH_TOP_FIELD), p.get(DEPTH_BOTTOM_FIELD)
            if top is not None and bottom is not None:
                top, bottom = float(top), float(bottom)
                assert top >= 0 and bottom >= 0
                assert top <= bottom, f"{typename}: top {top} > bottom {bottom}"
            ref_elev = p.get(DEPTH_REF_ELEV_FIELD)
            if ref_elev is not None:
                float(ref_elev)  # m AHD, numeric/parseable


@pytest.mark.contract
def test_D7_join_key_on_logs():
    """D7: every log row carries an ENO in the requested set."""
    wanted = set(LOGGED_ENOS)
    for f in _logs("bh:BoreholeStratigraphyLogs"):
        assert int(f["properties"]["ENO"]) in wanted


@pytest.mark.contract
def test_D8_join_key_on_headers():
    """D8: every header feature carries an eno."""
    for f in _header_sample():
        assert "eno" in f["properties"]


@pytest.mark.contract
def test_D9_hydrogeology_polygons_for_act():
    """D9: ACT bbox hydrogeology query returns valid polygons."""
    feats = _hydro_sample()
    assert len(feats) > 0
    for f in feats:
        geom = f["geometry"]
        assert geom["type"] in {"Polygon", "MultiPolygon"}
        ring = geom["coordinates"][0]
        while isinstance(ring[0][0], (list, tuple)):
            ring = ring[0]
        assert len(ring) >= 4


def _hydro_sample():
    import json
    geom = {"xmin": ACT_BBOX[0], "ymin": ACT_BBOX[1],
            "xmax": ACT_BBOX[2], "ymax": ACT_BBOX[3],
            "spatialReference": {"wkid": 4283}}
    r = _arcgis_query({
        "where": "1=1", "geometry": json.dumps(geom),
        "geometryType": "esriGeometryEnvelope", "inSR": 4283, "outSR": 4283,
        "spatialRel": "esriSpatialRelIntersects", "f": "geojson",
        "returnGeometry": "true", "outFields": "*"})
    return r.json()["features"]


@pytest.mark.contract
def test_D10_hydro_coords_in_bounds():
    """D10: every hydrogeology vertex is within Australian bounds."""
    for f in _hydro_sample():
        for lon, lat in iter_coords(f["geometry"]):
            assert in_australia(lon, lat)


@pytest.mark.contract
def test_D11_hydro_attributes_populated():
    """D11: mapped hydrogeology attributes have non-null values somewhere."""
    feats = _hydro_sample()
    seen = {k: False for k in ("aquif_ty", "type", "feature")}
    for f in feats:
        for k in seen:
            if f["properties"].get(k) not in (None, ""):
                seen[k] = True
    assert all(seen.values()), f"some hydro fields all-null: {seen}"


@pytest.mark.contract
def test_D12_pid_url_format():
    """D12: BOREHOLE_PID matches the GA samplingFeature URL pattern."""
    import re
    pat = re.compile(r"^http://pid\.geoscience\.gov\.au/samplingFeature/au/BH\d+$")
    for f in _logs("bh:BoreholeStratigraphyLogs"):
        pid = f["properties"].get("BOREHOLE_PID")
        if pid:
            assert pat.match(pid), f"unexpected PID format: {pid}"


# ======================================================================
# 5. Data integrity / drift detection
# ======================================================================

@pytest.mark.smoke
def test_I1_borehole_count_order_of_magnitude():
    """I1: national BoreholeView count within +/-20% of 52,338."""
    n = parse_number_matched(wfs_hits("gsmlp:BoreholeView").text)
    assert within_tolerance(n, COUNT_BOREHOLES), f"count {n} drifted"


@pytest.mark.contract
def test_I2_stratigraphy_count_order_of_magnitude():
    """I2: stratigraphy count within +/-20% of 190,016."""
    n = parse_number_matched(wfs_hits("bh:BoreholeStratigraphyLogs").text)
    assert within_tolerance(n, COUNT_STRATIGRAPHY), f"count {n} drifted"


@pytest.mark.contract
def test_I3_earth_material_count_order_of_magnitude():
    """I3: earth-material count within +/-20% of 551,852."""
    n = parse_number_matched(wfs_hits("bh:BoreholeEarthMaterialLogs").text)
    assert within_tolerance(n, COUNT_EARTH_MATERIAL), f"count {n} drifted"


@pytest.mark.contract
def test_I4_logs_not_spatially_queryable():
    """I4: a bbox on the log layer returns 0 (regression guard)."""
    n = parse_number_matched(
        wfs_hits("bh:BoreholeStratigraphyLogs", bbox=f"{ACT_BBOX_STR},EPSG:4283").text)
    assert n == 0, f"logs unexpectedly spatially queryable (got {n}) — flag it"


@pytest.mark.contract
def test_I5_header_log_case_asymmetry():
    """I5: header exposes lowercase eno, logs expose uppercase ENO."""
    header = http().get(WFS_BASE, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "gsmlp:BoreholeView", "count": 1,
        "outputFormat": "application/json",
        "bbox": f"{ACT_BBOX_STR},EPSG:4283"}).json()["features"][0]["properties"]
    log = _one_log("bh:BoreholeStratigraphyLogs")
    assert "eno" in header and "eno" not in {k.lower(): k for k in log if k == "eno"}
    assert "ENO" in log


@pytest.mark.smoke
def test_I6_hydro_count_present_nonzero():
    """I6: returnCountOnly on the hydro layer is > 0."""
    r = _arcgis_query({"where": "1=1", "returnCountOnly": "true", "f": "json"})
    assert int(r.json().get("count", 0)) > 0


@pytest.mark.contract
def test_I7_eno_fanout_ratio_sane():
    """I7: stratigraphy rows / 2 boreholes is roughly the ~15x seen at probe."""
    rows = len(_logs("bh:BoreholeStratigraphyLogs"))
    ratio = rows / len(LOGGED_ENOS)
    # Gross drift only (not hard-failed): flag if wildly off the ~15x.
    assert 1 <= ratio <= 100, f"fan-out ratio {ratio} looks wrong"


# ======================================================================
# 6. Freshness / caching signals
# ======================================================================

@pytest.mark.contract
def test_F1_arcgis_etag_stable():
    """F1: identical /query calls return the same ETag."""
    p = {"where": "1=1", "resultRecordCount": 1, "f": "geojson", "returnGeometry": "true"}
    e1 = _arcgis_query(p).headers.get("ETag")
    e2 = _arcgis_query(p).headers.get("ETag")
    assert e1 and e1 == e2


@pytest.mark.contract
def test_F2_conditional_get_honoured():
    """F2: If-None-Match with the stored ETag returns 304."""
    p = {"where": "1=1", "resultRecordCount": 1, "f": "geojson", "returnGeometry": "true"}
    etag = _arcgis_query(p).headers.get("ETag")
    assert etag
    r = http().get(f"{HYDRO_BASE}/0/query", params=p, headers={"If-None-Match": etag})
    assert r.status_code == 304


@pytest.mark.contract
def test_F4_wfs_number_matched_deterministic():
    """F4: numberMatched for the same bbox is identical across calls."""
    bbox = f"{ACT_BBOX_STR},EPSG:4283"
    n1 = parse_number_matched(wfs_hits("gsmlp:BoreholeView", bbox=bbox).text)
    n2 = parse_number_matched(wfs_hits("gsmlp:BoreholeView", bbox=bbox).text)
    assert n1 == n2


# ======================================================================
# 7. Robustness / failure modes
# ======================================================================

@pytest.mark.contract
def test_R1_bad_wfs_layer_fails_fast():
    """R1: a bad typeName fails (4xx), not 200-with-garbage."""
    import requests
    with pytest.raises(requests.HTTPError) as exc:
        http().get(WFS_BASE, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "bh:DoesNotExist", "count": 1,
            "outputFormat": "application/json"})
    assert exc.value.response.status_code in (400, 404)


@pytest.mark.contract
def test_R2_bad_arcgis_layer_fails_fast():
    """R2: a non-existent ArcGIS layer returns an error promptly."""
    r = http().get(f"{HYDRO_BASE}/999", params={"f": "json"})
    # ArcGIS often returns 200 with an {"error": ...} body for a bad layer.
    body = r.json() if r.status_code == 200 else {}
    assert r.status_code >= 400 or "error" in body


@pytest.mark.contract
def test_R7_arcgis_offset_beyond_end():
    """R7: resultOffset past the end returns 0 features, no 500."""
    n = int(_arcgis_query({"where": "1=1", "returnCountOnly": "true",
                           "f": "json"}).json()["count"])
    r = _arcgis_query({"where": "1=1", "f": "geojson", "returnGeometry": "true",
                       "resultOffset": n + 1000, "resultRecordCount": 10})
    assert r.status_code == 200
    assert len(r.json().get("features", [])) == 0


# ----------------------------------------------------------------------
# Heavy / on-demand (skipped by default)
# ----------------------------------------------------------------------

@pytest.mark.heavy
def test_D3_hits_matches_paginated_pull():
    """D3: paginated bbox sweep concatenates to the hits total."""
    bbox = f"{ACT_BBOX_STR},EPSG:4283"
    total = parse_number_matched(wfs_hits("gsmlp:BoreholeView", bbox=bbox).text)
    page_size = 25
    seen = 0
    start = 0
    while True:
        r = http().get(WFS_BASE, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "gsmlp:BoreholeView", "count": page_size,
            "startIndex": start, "outputFormat": "application/json", "bbox": bbox})
        page = r.json()["features"]
        seen += len(page)
        if len(page) < page_size:
            break
        start += page_size
    assert seen == total


@pytest.mark.heavy
def test_R4_long_eno_post_list():
    """R4: a ~200-ENO POST GetFeature returns HTTP 200."""
    enos = list(range(35000, 35200))
    in_list = ",".join(str(e) for e in enos)
    r = http().post(WFS_BASE, data={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "bh:BoreholeStratigraphyLogs",
        "outputFormat": "application/json", "count": 10,
        "cql_filter": f"ENO IN ({in_list})"})
    assert r.status_code == 200
