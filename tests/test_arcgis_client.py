"""Offline unit tests for the ArcGisRestClient (responses-mocked)."""
import json
from urllib.parse import parse_qs, urlparse

import responses

from gadata.domain.region import Region
from gadata.infrastructure.arcgis_rest_client import ArcGisRestClient
from gadata.infrastructure.http import HttpClient

QUERY_URL = (
    "https://services.ga.gov.au/gis/rest/services/"
    "Hydrogeology_of_Australia/MapServer/0/query"
)


def _client(page_size=2):
    http = HttpClient(politeness_delay=0.0, backoff_base=0.0)
    return ArcGisRestClient(http, page_size=page_size)


def _polygon_feature(uid):
    return {
        "type": "Feature",
        "properties": {"ufi": uid, "feature": "aquifer"},
        "geometry": {"type": "Polygon", "coordinates": [[[149.0, -35.0]]]},
    }


def _geojson_page(features, exceeded):
    return json.dumps(
        {"type": "FeatureCollection", "features": features, "exceededTransferLimit": exceeded}
    )


def _bbox_region():
    return Region.from_bbox(148.9, -35.6, 149.3, -35.1)


def test_count_units_parses_count():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, QUERY_URL, json={"count": 7}, status=200)
        assert _client().count_units(_bbox_region()) == 7
        params = parse_qs(urlparse(rsps.calls[0].request.url).query)
        assert params["returnCountOnly"] == ["true"]
        assert params["outSR"] == ["4283"]


def test_outsr_4283_always_present_on_feature_query():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, QUERY_URL, body=_geojson_page([_polygon_feature(1)], False), status=200)
        _client().fetch_units(_bbox_region())
        params = parse_qs(urlparse(rsps.calls[0].request.url).query)
        assert params["outSR"] == ["4283"]
        assert params["inSR"] == ["4283"]
        assert params["f"] == ["geojson"]


def test_pagination_terminates_on_exceeded_transfer_limit():
    with responses.RequestsMock() as rsps:
        # page1: full (2) + exceeded=True -> continue; page2: full (2) + exceeded=False -> stop.
        rsps.add(responses.GET, QUERY_URL,
                 body=_geojson_page([_polygon_feature(1), _polygon_feature(2)], True), status=200)
        rsps.add(responses.GET, QUERY_URL,
                 body=_geojson_page([_polygon_feature(3), _polygon_feature(4)], False), status=200)
        feats = _client(page_size=2).fetch_units(_bbox_region())
        assert len(feats) == 4
        assert len(rsps.calls) == 2
        # Second request advanced the offset past the first page.
        params2 = parse_qs(urlparse(rsps.calls[1].request.url).query)
        assert params2["resultOffset"] == ["2"]


def test_pagination_stops_on_short_page():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, QUERY_URL,
                 body=_geojson_page([_polygon_feature(1)], False), status=200)
        feats = _client(page_size=2).fetch_units(_bbox_region())
        assert len(feats) == 1
        assert len(rsps.calls) == 1


def test_spatial_filter_envelope_for_bbox():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, QUERY_URL, json={"count": 0}, status=200)
        _client().count_units(_bbox_region())
        params = parse_qs(urlparse(rsps.calls[0].request.url).query)
        assert params["geometryType"] == ["esriGeometryEnvelope"]
        assert params["spatialRel"] == ["esriSpatialRelIntersects"]
        geom = json.loads(params["geometry"][0])
        assert geom["spatialReference"]["wkid"] == 4283
        assert geom["xmin"] == 148.9


def test_polygon_region_uses_post():
    from shapely.geometry import Polygon
    triangle = Region(Polygon([(149.0, -36.0), (150.0, -36.0), (149.5, -35.0)]))
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, QUERY_URL,
                 body=_geojson_page([_polygon_feature(1)], False), status=200)
        _client().fetch_units(triangle)
        body = parse_qs(rsps.calls[0].request.body)
        assert body["geometryType"] == ["esriGeometryPolygon"]
        assert body["outSR"] == ["4283"]


def test_max_pages_cap_raises_instead_of_looping():
    import pytest
    # Server always says exceeded=True with a full page -> would loop forever;
    # the max_pages cap must abort with a RuntimeError instead.
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, QUERY_URL,
                 body=_geojson_page([_polygon_feature(1), _polygon_feature(2)], True),
                 status=200)
        client = ArcGisRestClient(
            HttpClient(politeness_delay=0.0, backoff_base=0.0), page_size=2, max_pages=3)
        with pytest.raises(RuntimeError, match="max_pages"):
            client.fetch_units(_bbox_region())


def test_where_clause_passed_through():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, QUERY_URL, json={"count": 0}, status=200)
        _client().count_units(_bbox_region(), where="prodty='high'")
        params = parse_qs(urlparse(rsps.calls[0].request.url).query)
        assert params["where"] == ["prodty='high'"]
