"""Offline tests for the federated GroundwaterClient (GA + NGIS).

GA is driven by an injected FakeWfs (the repo's established offline pattern, same
as test_client.py); NGIS by a real synthetic .gdb with a monkeypatched registry
and tmp cache/ngis_dir. No network.
"""
import fiona
import pytest
from fiona.crs import CRS

from austrata.client import GADataClient
from austrata.groundwater_client import GroundwaterClient
from austrata.infrastructure import ngis_download, ngis_sources
from austrata.infrastructure.ngis_sources import ngis_states_intersecting
from austrata.ngis_client import NGISClient

_ALBERS = CRS.from_epsg(3577)
# Box over the synthetic 'TAS' state's extent; GW1 + edge GW3 inside, GW2 out.
_BOX = (143.0, -36.0, 144.0, -34.0)


# -- GA fake adapter (mirrors test_client.py) ---------------------------


def _ga_header(eno, lon, lat):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"eno": eno, "name": f"BH{eno}", "state": "NSW",
                           "GDA94_dlong": lon, "GDA94_dlat": lat}}


def _ga_strat(eno, top, bottom, unit):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [143.5, -35.0]},
            "properties": {"ENO": eno, "BOREHOLE_NAME": f"BH{eno}",
                           "INTERVAL_BEGIN_M": top, "INTERVAL_END_M": bottom,
                           "STRAT_UNIT_NAME": unit}}


class FakeWfs:
    def __init__(self, headers, strat=None):
        self.headers = headers
        self.strat = strat or []
        self.fetch_calls = 0

    def count_headers(self, region, cql_filter=None):
        return len(self.headers)

    def fetch_headers(self, region, cql_filter=None):
        self.fetch_calls += 1
        return self.headers

    def fetch_header(self, identifier):
        return self.headers[0] if self.headers else None

    def fetch_stratigraphy(self, enos):
        return [f for f in self.strat if int(f["properties"]["ENO"]) in set(enos)]

    def fetch_earth_material(self, enos):
        return []


# -- NGIS synthetic gdb -------------------------------------------------


def _write_gdb(path):
    bore_schema = {"geometry": "Point", "properties": {
        "HydroCode": "str", "Longitude": "float", "Latitude": "float", "RefElev": "float"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_Bore",
                    schema=bore_schema, crs=_ALBERS) as c:
        for code, lon, lat in [("GW1", 143.5, -35.0), ("GW2", 148.0, -33.0),
                               ("GW3", 144.0, -35.0)]:  # GW3 on the east edge
            c.write({"geometry": {"type": "Point", "coordinates": (1e6, -3e6)},
                     "properties": {"HydroCode": code, "Longitude": lon,
                                    "Latitude": lat, "RefElev": 100.0}})
    strat_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "Description": "str", "FromDepth": "float", "ToDepth": "float"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_BoreholeLog",
                    schema=strat_schema) as c:
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "Description": "Calivil Formation",
            "FromDepth": 0.0, "ToDepth": 5.0}})
    cons_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "ConstructionType": "str", "FromDepth": "float", "ToDepth": "float"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_ConstructionLog",
                    schema=cons_schema) as c:
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "ConstructionType": "Screen",
            "FromDepth": 30.0, "ToDepth": 42.0}})
    lith_schema = {"geometry": "None", "properties": {"HydroCode": "str", "MajorLithCode": "str"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_LithologyLog",
                    schema=lith_schema) as c:
        c.write({"geometry": None, "properties": {"HydroCode": "GW1", "MajorLithCode": "CLAY"}})


@pytest.fixture
def synthetic_gdb(tmp_path):
    path = tmp_path / "synthetic.gdb"
    _write_gdb(str(path))
    return str(path)


@pytest.fixture
def fake_tas(monkeypatch):
    """A 'TAS' state whose extent matches the test box (the other states don't)."""
    src = ngis_sources.NgisSource(
        state="TAS", vintage="2014-test", dataset_url="https://data.gov.au/test",
        resource_url="https://x/y.zip", mirror_url="https://m/y.zip",
        zip_bytes=1, zip_md5="md5aaa", gdb_relpath="x.gdb",
        extent=(142.0, -37.0, 145.0, -33.0))
    # A SECOND state whose extent is far from the test box — it must NEVER be
    # opened (belt-and-suspenders: catches a routing-bypass that opens everything).
    far = ngis_sources.NgisSource(
        state="FAR", vintage="2014-test", dataset_url="https://data.gov.au/far",
        resource_url="https://x/far.zip", mirror_url="https://m/far.zip",
        zip_bytes=1, zip_md5="md5far", gdb_relpath="far.gdb",
        extent=(110.0, -20.0, 115.0, -15.0))
    monkeypatch.setattr(ngis_sources, "NGIS_SOURCES", {"TAS": src, "FAR": far})
    return src


@pytest.fixture
def opened_states(monkeypatch):
    """Spy: record every state whose gdb ensure_gdb is asked to open."""
    opened = []
    return opened


@pytest.fixture
def client(tmp_path, synthetic_gdb, fake_tas, opened_states, monkeypatch):
    def fake_ensure(state, **kw):
        opened_states.append(state)
        return synthetic_gdb
    monkeypatch.setattr(ngis_download, "ensure_gdb", fake_ensure)

    ga = GADataClient(cache_dir=tmp_path / "ga", wfs=FakeWfs(
        headers=[_ga_header(111, 143.6, -35.1)],          # in-box GA bore
        strat=[_ga_strat(111, 0, 8, "Shepparton Formation")]))
    ngis = NGISClient(ngis_dir=tmp_path / "ngis", cache_dir=tmp_path / "ngiscache")
    return GroundwaterClient(ga=ga, ngis=ngis), opened_states


# -- routing ------------------------------------------------------------


def test_routing_real_states():
    # A NSW-only box must not route to VIC/QLD; a box over VIC routes to VIC.
    nsw_box = (150.0, -33.0, 151.0, -32.0)
    assert ngis_states_intersecting(nsw_box) == ["NSW"]
    vic_box = (144.0, -37.5, 145.0, -36.5)
    assert "VIC" in ngis_states_intersecting(vic_box)
    assert "QLD" not in ngis_states_intersecting(vic_box)


def test_routing_edge_inclusive():
    # A box touching NSW's western edge exactly still routes to NSW.
    edge_box = (140.0, -33.0, 140.99, -32.0)  # max_lon == NSW min_lon
    assert "NSW" in ngis_states_intersecting(edge_box)


def test_only_intersecting_state_is_opened(client):
    gw, opened = client
    gw.boreholes(bbox=_BOX)
    # TAS covers the box and is opened; FAR (registry-present but far from the box)
    # is routed out and its gdb is NEVER opened — proves routing isn't bypassed.
    assert opened == ["TAS"]
    assert "FAR" not in opened


# -- merge + tagging ----------------------------------------------------


def test_merged_collection_has_both_sources_no_dedup(client):
    gw, _ = client
    bc = gw.boreholes(bbox=_BOX)
    sources = sorted(b.source for b in bc)
    # 1 GA bore + 2 in-box NGIS bores (GW1 + edge GW3); GW2 routed out.
    assert sources == ["GA", "NGIS:TAS", "NGIS:TAS"]


def test_overlapping_point_appears_once_per_source(
    tmp_path, synthetic_gdb, fake_tas, monkeypatch
):
    # A GA bore at the SAME location as NGIS GW1 — no dedup, both kept.
    monkeypatch.setattr(ngis_download, "ensure_gdb", lambda state, **kw: synthetic_gdb)
    ga = GADataClient(cache_dir=tmp_path / "ga2",
                      wfs=FakeWfs(headers=[_ga_header(222, 143.5, -35.0)]))
    ngis = NGISClient(ngis_dir=tmp_path / "n2", cache_dir=tmp_path / "n2c")
    gw = GroundwaterClient(ga=ga, ngis=ngis)
    bc = gw.boreholes(bbox=_BOX)
    at_point = [b for b in bc if b.longitude == 143.5 and b.latitude == -35.0]
    assert {b.source for b in at_point} == {"GA", "NGIS:TAS"}


# -- federated load_logs ------------------------------------------------


def test_federated_load_logs_stratigraphy_both_sources(client):
    gw, _ = client
    bc = gw.boreholes(bbox=_BOX)
    bc.load_logs("stratigraphy")
    gdf = bc.stratigraphy_geodataframe()
    assert set(gdf["source"]) == {"GA", "NGIS:TAS"}
    assert "Shepparton Formation" in set(gdf["unit"])   # GA
    assert "Calivil Formation" in set(gdf["unit"])      # NGIS


def test_federated_construction_ngis_only_ga_empty(client):
    gw, _ = client
    bc = gw.boreholes(bbox=_BOX)
    bc.load_logs("construction")  # must not error despite GA having no loader
    gdf = bc.construction_geodataframe()
    assert set(gdf["source"]) == {"NGIS:TAS"}      # only NGIS contributes rows
    # The GA bore loaded-but-empty (no construction), not raised.
    ga_bores = [b for b in bc if b.source == "GA"]
    assert ga_bores and ga_bores[0].construction == []


# -- sources= filter ----------------------------------------------------


def test_sources_filter_ga_only(client):
    gw, opened = client
    bc = gw.boreholes(bbox=_BOX, sources=["GA"])
    assert {b.source for b in bc} == {"GA"}
    assert opened == []  # NGIS never opened when GA-only requested


def test_sources_filter_ngis_only(client):
    gw, _ = client
    bc = gw.boreholes(bbox=_BOX, sources=["NGIS"])
    assert {b.source for b in bc} == {"NGIS:TAS"}


def test_sources_filter_specific_state(client):
    gw, _ = client
    bc = gw.boreholes(bbox=_BOX, sources=["NGIS:TAS"])
    assert {b.source for b in bc} == {"NGIS:TAS"}


# -- provenance ---------------------------------------------------------


def test_provenance_is_per_source(client):
    gw, _ = client
    bc = gw.boreholes(bbox=_BOX)
    prov = bc.provenance()
    assert "sources" in prov
    assert len(prov["sources"]) == 2  # GA + TAS


def test_federated_citation_mentions_both_sources(client):
    gw, _ = client
    bc = gw.boreholes(bbox=_BOX)
    cite = bc.citation()
    # The merged citation must attribute BOTH GA and the NGIS/BoM source.
    assert "Geoscience Australia" in cite
    assert "National Groundwater Information System" in cite or "NGIS" in cite
