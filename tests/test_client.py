"""Offline tests for GADataClient with fake injected adapters + tmp cache."""
import pytest

from austrata.client import GADataClient
from austrata.domain.borehole import BoreholeCollection
from austrata.infrastructure.dataset_cache import DatasetCache

ACT_BBOX = (148.9, -35.6, 149.3, -35.1)


def _header(eno, lon=149.2, lat=-35.1):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"eno": eno, "name": f"BH{eno}", "state": "NSW",
                       "GDA94_dlong": lon, "GDA94_dlat": lat},
    }


def _strat(eno, top, bottom, unit):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [149.2, -35.1]},
            "properties": {"ENO": eno, "BOREHOLE_NAME": f"BH{eno}",
                           "INTERVAL_BEGIN_M": top, "INTERVAL_END_M": bottom,
                           "STRAT_UNIT_NAME": unit}}


def _hydro(uid):
    return {"type": "Feature",
            "geometry": {"type": "Polygon",
                         "coordinates": [[[149.0, -35.0], [149.1, -35.0],
                                          [149.1, -35.1], [149.0, -35.0]]]},
            "properties": {"ufi": uid, "feature": "aquifer", "prodty": "high"}}


class FakeWfs:
    def __init__(self, headers, strat=None, earth=None, count=None):
        self.headers = headers
        self.strat = strat or []
        self.earth = earth or []
        self._count = count if count is not None else len(headers)
        self.fetch_calls = 0
        self.count_calls = 0

    def count_headers(self, region, cql_filter=None):
        self.count_calls += 1
        return self._count

    def fetch_headers(self, region, cql_filter=None):
        self.fetch_calls += 1
        return self.headers

    def fetch_header(self, identifier):
        return self.headers[0] if self.headers else None

    def fetch_stratigraphy(self, enos):
        return [f for f in self.strat if int(f["properties"]["ENO"]) in set(enos)]

    def fetch_earth_material(self, enos):
        return [f for f in self.earth if int(f["properties"]["ENO"]) in set(enos)]


class FakeArcGis:
    def __init__(self, units, etag='"v1"'):
        self.units = units
        self.etag = etag
        self.fetch_calls = 0

    def count_units(self, region, where=None):
        return len(self.units)

    def fetch_units(self, region, where=None):
        self.fetch_calls += 1
        return self.units

    def probe_etag(self, region, where=None, etag=None):
        if etag is not None and etag == self.etag:
            return {"not_modified": True, "etag": self.etag}
        return {"not_modified": False, "etag": self.etag}


def _client(tmp_path, wfs=None, arcgis=None, **kw):
    cache = DatasetCache(cache_dir=tmp_path, **kw)
    return GADataClient(wfs=wfs, arcgis=arcgis, cache=cache)


# -- boreholes ----------------------------------------------------------

def test_boreholes_returns_populated_collection(tmp_path):
    wfs = FakeWfs([_header(1), _header(2)])
    ga = _client(tmp_path, wfs=wfs)
    coll = ga.boreholes(bbox=ACT_BBOX)
    assert isinstance(coll, BoreholeCollection)
    assert len(coll) == 2
    assert sorted(coll.enos) == [1, 2]
    assert coll[0].longitude == 149.2


def test_boreholes_second_call_served_from_cache(tmp_path):
    wfs = FakeWfs([_header(1), _header(2)])
    ga = _client(tmp_path, wfs=wfs)
    ga.boreholes(bbox=ACT_BBOX)
    assert wfs.fetch_calls == 1
    # Same query, fingerprint (count) unchanged -> no second fetch.
    ga.boreholes(bbox=ACT_BBOX)
    assert wfs.fetch_calls == 1


def test_boreholes_refetch_when_count_changes(tmp_path):
    wfs = FakeWfs([_header(1)], count=1)
    ga = _client(tmp_path, wfs=wfs)
    ga.boreholes(bbox=ACT_BBOX)
    assert wfs.fetch_calls == 1
    # Server count grew -> fingerprint differs -> refetch.
    wfs._count = 5
    ga.boreholes(bbox=ACT_BBOX)
    assert wfs.fetch_calls == 2


def test_boreholes_count_only_no_full_pull(tmp_path):
    wfs = FakeWfs([_header(1), _header(2)], count=42)
    ga = _client(tmp_path, wfs=wfs)
    n = ga.boreholes(bbox=ACT_BBOX, count_only=True)
    assert n == 42
    assert wfs.fetch_calls == 0


def test_bbox_and_region_equivalent(tmp_path):
    from shapely.geometry import box
    wfs = FakeWfs([_header(1), _header(2)])
    ga = _client(tmp_path, wfs=wfs)
    ga.boreholes(bbox=ACT_BBOX)
    # Same footprint via region= must hit the same cache entry (no new fetch).
    ga.boreholes(region=box(*ACT_BBOX))
    assert wfs.fetch_calls == 1


def test_single_borehole(tmp_path):
    wfs = FakeWfs([_header(35147)])
    ga = _client(tmp_path, wfs=wfs)
    bh = ga.borehole("35147")
    assert bh is not None and bh.eno == 35147


def test_region_and_bbox_both_raises(tmp_path):
    ga = _client(tmp_path, wfs=FakeWfs([]))
    from shapely.geometry import box
    with pytest.raises(ValueError):
        ga.boreholes(region=box(*ACT_BBOX), bbox=ACT_BBOX)


# -- logs ---------------------------------------------------------------

def test_load_logs_distributes_by_eno(tmp_path):
    wfs = FakeWfs(
        [_header(1), _header(2)],
        strat=[_strat(1, 0, 5, "SandA"), _strat(1, 5, 10, "ClayB"), _strat(2, 0, 3, "SandC")],
    )
    ga = _client(tmp_path, wfs=wfs)
    coll = ga.boreholes(bbox=ACT_BBOX)
    coll.load_logs("stratigraphy")
    by_eno = {b.eno: b for b in coll}
    assert len(by_eno[1].stratigraphy) == 2
    assert len(by_eno[2].stratigraphy) == 1
    assert by_eno[1].stratigraphy[0].unit == "SandA"
    assert by_eno[2].stratigraphy[0].unit == "SandC"


def test_load_logs_empty_collection_noop(tmp_path):
    ga = _client(tmp_path, wfs=FakeWfs([]))
    coll = ga.boreholes(bbox=ACT_BBOX)
    coll.load_logs("stratigraphy")  # no enos -> no error
    assert len(coll) == 0


# -- hydrogeology -------------------------------------------------------

def test_hydrogeology_returns_geodataframe(tmp_path):
    arc = FakeArcGis([_hydro(1), _hydro(2)])
    ga = _client(tmp_path, arcgis=arc)
    gdf = ga.hydrogeology(bbox=ACT_BBOX)
    assert len(gdf) == 2
    assert gdf.crs.to_epsg() == 4283
    assert "feature" in gdf.columns


def test_hydrogeology_cache_hit_via_etag_304(tmp_path):
    arc = FakeArcGis([_hydro(1)])
    ga = _client(tmp_path, arcgis=arc)
    ga.hydrogeology(bbox=ACT_BBOX)
    assert arc.fetch_calls == 1
    # Stored etag matches -> probe returns not_modified -> served from cache.
    ga.hydrogeology(bbox=ACT_BBOX)
    assert arc.fetch_calls == 1


def test_hydrogeology_count_only(tmp_path):
    arc = FakeArcGis([_hydro(1), _hydro(2), _hydro(3)])
    ga = _client(tmp_path, arcgis=arc)
    assert ga.hydrogeology(bbox=ACT_BBOX, count_only=True) == 3
    assert arc.fetch_calls == 0


# -- provenance ---------------------------------------------------------

def test_borehole_collection_provenance_and_citation(tmp_path):
    wfs = FakeWfs([_header(1), _header(2)])
    ga = _client(tmp_path, wfs=wfs)
    coll = ga.boreholes(bbox=ACT_BBOX)
    prov = coll.provenance()
    assert prov["license"] == "CC BY 4.0"
    assert prov["source_url"].startswith("https://services.ga.gov.au")
    assert prov["feature_count"] == 2
    assert prov["service_version"] == "WFS 2.0.0"
    cite = coll.citation()
    assert "Geoscience Australia" in cite
    assert "Accessed" in cite
    assert "CC BY 4.0" in cite


def test_hydrogeology_provenance_helpers(tmp_path):
    from austrata.client import hydrogeology_citation, hydrogeology_provenance
    arc = FakeArcGis([_hydro(1)])
    ga = _client(tmp_path, arcgis=arc)
    gdf = ga.hydrogeology(bbox=ACT_BBOX)
    prov = hydrogeology_provenance(gdf)
    assert prov["license"] == "CC BY 4.0"
    assert "Hydrogeology" in prov["citation"]
    cite = hydrogeology_citation(gdf)
    assert "Accessed" in cite and "CC BY 4.0" in cite


def test_collection_built_outside_client_has_empty_provenance():
    from austrata.domain.borehole import BoreholeCollection
    from austrata.domain.region import Region
    coll = BoreholeCollection([], Region.from_bbox(*ACT_BBOX))
    assert coll.provenance() == {}
    assert "Geoscience Australia" in coll.citation()
