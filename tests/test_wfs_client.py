"""Offline unit tests for the OgcWfsClient (responses-mocked)."""
import json

import pytest
import responses

from gadata.domain.region import Region
from gadata.infrastructure.http import HttpClient
from gadata.infrastructure.ogc_wfs_client import OgcWfsClient

BASE = "https://services.ga.gov.au/gis/boreholes/wfs"


def _client(page_size=2, eno_chunk_size=2):
    http = HttpClient(politeness_delay=0.0, backoff_base=0.0)
    return OgcWfsClient(http, page_size=page_size, eno_chunk_size=eno_chunk_size)


def _feature(eno):
    return {"type": "Feature", "properties": {"eno": eno}, "geometry": None}


def _page(features):
    return json.dumps({"type": "FeatureCollection", "features": features})


@responses.activate
def test_count_headers_parses_number_matched():
    hits_xml = '<wfs:FeatureCollection numberMatched="42" numberReturned="0"/>'
    responses.add(responses.GET, BASE, body=hits_xml, status=200,
                  content_type="text/xml")
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    assert _client().count_headers(region) == 42


@responses.activate
def test_fetch_headers_paginates_to_completion():
    # total=3, page_size=2 -> page1 has 2 (full), page2 has 1 (short -> stop).
    responses.add(responses.GET, BASE,
                  body='<x numberMatched="3"/>', status=200, content_type="text/xml")
    responses.add(responses.GET, BASE, body=_page([_feature(1), _feature(2)]), status=200)
    responses.add(responses.GET, BASE, body=_page([_feature(3)]), status=200)
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    feats = _client(page_size=2).fetch_headers(region)
    assert len(feats) == 3
    # 1 hits call + 2 data pages = 3 calls.
    assert len(responses.calls) == 3


@responses.activate
def test_fetch_headers_stops_on_exact_multiple():
    # total=2, page_size=2 -> first page is full; loop must stop via total guard.
    responses.add(responses.GET, BASE,
                  body='<x numberMatched="2"/>', status=200, content_type="text/xml")
    responses.add(responses.GET, BASE, body=_page([_feature(1), _feature(2)]), status=200)
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    feats = _client(page_size=2).fetch_headers(region)
    assert len(feats) == 2
    assert len(responses.calls) == 2  # no spurious extra page fetch


@responses.activate
def test_cql_filter_uses_bbox_predicate():
    responses.add(responses.GET, BASE, body='<x numberMatched="0"/>', status=200,
                  content_type="text/xml")
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    _client().count_headers(region, cql_filter="status='unknown'")
    sent = responses.calls[0].request.url
    assert "cql_filter" in sent
    assert "BBOX" in sent
    assert "bbox=" not in sent  # spatial constraint folded into cql_filter


@responses.activate
def test_fetch_stratigraphy_chunks_enos_into_multiple_posts():
    # 5 ENOs, chunk size 2 -> 3 chunks -> 3 POSTs (each one short page).
    for _ in range(3):
        responses.add(responses.POST, BASE, body=_page([_feature(1)]), status=200)
    feats = _client(page_size=10, eno_chunk_size=2).fetch_stratigraphy([1, 2, 3, 4, 5])
    assert len(responses.calls) == 3
    # Each POST should carry an ENO IN (...) filter.
    for call in responses.calls:
        assert "ENO+IN" in call.request.body or "ENO IN" in call.request.body
    assert len(feats) == 3


@responses.activate
def test_fetch_stratigraphy_empty_eno_list_no_request():
    assert _client().fetch_stratigraphy([]) == []
    assert len(responses.calls) == 0


@responses.activate
def test_fetch_header_single_by_eno():
    responses.add(responses.GET, BASE, body=_page([_feature(35147)]), status=200)
    feat = _client().fetch_header("35147")
    assert feat["properties"]["eno"] == 35147
    assert "cql_filter=eno%3D35147" in responses.calls[0].request.url


def test_identifier_to_eno_accepts_pid_url():
    c = _client()
    assert c._identifier_to_eno("http://pid.geoscience.gov.au/samplingFeature/au/BH35147") == 35147
    assert c._identifier_to_eno("35147") == 35147
    with pytest.raises(ValueError):
        c._identifier_to_eno("not-an-eno")
