"""Offline tests for the NGIS fast-DB cache use-case + the §6 stamp gate.

Uses a real synthetic .gdb (fiona-written), a tmp_path DatasetCache, and injected
``ensure_gdb`` / ``optimise_state`` spies so we can prove the build-once contract
(one optimise pass across all four layers) and the stamp round-trip.
"""
import fiona
import pytest
from fiona.crs import CRS

from austrata.application.fetch_ngis import (
    NGIS_LAYERS,
    expected_fingerprint,
    load_ngis_frames,
    ngis_cache_key,
)
from austrata.infrastructure import ngis_optimiser, ngis_sources
from austrata.infrastructure.dataset_cache import DatasetCache
from austrata.infrastructure.ngis_optimiser import (
    OPTIMISER_VERSION,
    build_stamp,
    optimise_state,
)

_ALBERS = CRS.from_epsg(3577)


def _write_gdb(path):
    bore_schema = {"geometry": "Point", "properties": {
        "HydroCode": "str", "Longitude": "float", "Latitude": "float"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_Bore",
                    schema=bore_schema, crs=_ALBERS) as c:
        c.write({"geometry": {"type": "Point", "coordinates": (1e6, -3e6)},
                 "properties": {"HydroCode": "GW1", "Longitude": 143.5, "Latitude": -35.0}})
    strat_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "Description": "str", "FromDepth": "float", "ToDepth": "float"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_BoreholeLog",
                    schema=strat_schema) as c:
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "Description": "Shepparton Formation",
            "FromDepth": 0.0, "ToDepth": 5.0}})
    lith_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "MajorLithCode": "str"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_LithologyLog",
                    schema=lith_schema) as c:
        c.write({"geometry": None, "properties": {"HydroCode": "GW1", "MajorLithCode": "CLAY"}})
    cons_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "ConstructionType": "str"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_ConstructionLog",
                    schema=cons_schema) as c:
        c.write({"geometry": None, "properties": {"HydroCode": "GW1", "ConstructionType": "Screen"}})


@pytest.fixture
def synthetic_gdb(tmp_path):
    path = tmp_path / "synthetic.gdb"
    _write_gdb(str(path))
    return str(path)


@pytest.fixture
def fake_tas(monkeypatch):
    src = ngis_sources.NgisSource(
        state="TAS", vintage="2014-test", dataset_url="https://data.gov.au/test",
        resource_url="https://x/y.zip", mirror_url="https://m/y.zip",
        zip_bytes=1, zip_md5="md5aaa", gdb_relpath="x.gdb")
    monkeypatch.setitem(ngis_sources.NGIS_SOURCES, "TAS", src)
    return src


class _Spies:
    """Injectable ensure_gdb + optimise_state that count their calls."""

    def __init__(self, gdb_path):
        self.gdb_path = gdb_path
        self.ensure_calls = 0
        self.optimise_calls = 0
        self.ensure_force = []

    def ensure_gdb(self, state, *, ngis_dir=None, http=None, force=False):
        self.ensure_calls += 1
        self.ensure_force.append(force)
        return self.gdb_path

    def optimise_state(self, gdb_path, state):
        self.optimise_calls += 1
        return optimise_state(gdb_path, state)


def _load(cache, spies, **kw):
    return load_ngis_frames(
        cache, "TAS",
        ensure_gdb=spies.ensure_gdb, optimise_state=spies.optimise_state,
        optimiser_version=OPTIMISER_VERSION, build_stamp=build_stamp, **kw)


def test_cache_key_is_filename_safe():
    assert ngis_cache_key("NSW", "stratigraphy", 1) == "ngis-nsw-stratigraphy-opt1"


def test_first_call_builds_once_and_writes_all_four(tmp_path, synthetic_gdb, fake_tas):
    cache = DatasetCache(tmp_path / "cache")
    spies = _Spies(synthetic_gdb)
    frames = _load(cache, spies)
    assert set(frames) == set(NGIS_LAYERS)
    assert spies.optimise_calls == 1  # ONE fiona pass for all four layers
    assert spies.ensure_calls == 1
    for layer in NGIS_LAYERS:
        assert cache.has(ngis_cache_key("TAS", layer, OPTIMISER_VERSION))


def test_second_call_other_layer_served_from_cache_no_rescan(tmp_path, synthetic_gdb, fake_tas):
    cache = DatasetCache(tmp_path / "cache")
    spies = _Spies(synthetic_gdb)
    _load(cache, spies)
    # A second request for the same state must NOT re-optimise.
    frames = _load(cache, spies)
    assert spies.optimise_calls == 1
    assert spies.ensure_calls == 1
    assert len(frames["stratigraphy"]) == 1


def test_stamp_is_readable_and_matches(tmp_path, synthetic_gdb, fake_tas):
    cache = DatasetCache(tmp_path / "cache")
    _load(cache, _Spies(synthetic_gdb))
    want = expected_fingerprint("TAS", "md5aaa", OPTIMISER_VERSION)["ngis_stamp"]
    for layer in NGIS_LAYERS:
        prov = cache.provenance(ngis_cache_key("TAS", layer, OPTIMISER_VERSION))
        assert prov["server_fingerprint"] == want
        assert prov["server_fingerprint"] == "TAS|md5aaa|opt1"


def test_force_refresh_rebuilds(tmp_path, synthetic_gdb, fake_tas):
    cache = DatasetCache(tmp_path / "cache")
    spies = _Spies(synthetic_gdb)
    _load(cache, spies)
    _load(cache, spies, force_refresh=True)
    assert spies.optimise_calls == 2
    assert spies.ensure_calls == 2
    assert spies.ensure_force == [False, True]  # force propagates to ensure_gdb


def test_md5_change_invalidates_and_rebuilds(tmp_path, synthetic_gdb, fake_tas, monkeypatch):
    cache = DatasetCache(tmp_path / "cache")
    spies = _Spies(synthetic_gdb)
    _load(cache, spies)
    # Simulate a custodian reissue: the pinned md5 changes -> stamp mismatch.
    new = ngis_sources.NgisSource(**{**fake_tas.__dict__, "zip_md5": "md5bbb"})
    monkeypatch.setitem(ngis_sources.NGIS_SOURCES, "TAS", new)
    _load(cache, spies)
    assert spies.optimise_calls == 2  # stale stamp forced a rebuild


def test_version_bump_invalidates(tmp_path, synthetic_gdb, fake_tas, monkeypatch):
    cache = DatasetCache(tmp_path / "cache")
    spies = _Spies(synthetic_gdb)
    _load(cache, spies)
    # Bump the optimiser version: keys change AND fingerprint changes -> rebuild.
    monkeypatch.setattr(ngis_optimiser, "OPTIMISER_VERSION", 99)
    frames = load_ngis_frames(
        cache, "TAS",
        ensure_gdb=spies.ensure_gdb, optimise_state=spies.optimise_state,
        optimiser_version=99, build_stamp=build_stamp)
    assert spies.optimise_calls == 2
    assert cache.has(ngis_cache_key("TAS", "bores", 99))
    assert set(frames) == set(NGIS_LAYERS)
    # The superseded opt1 entries are swept, not orphaned on disk.
    for layer in NGIS_LAYERS:
        assert not cache.has(ngis_cache_key("TAS", layer, OPTIMISER_VERSION))


def test_offline_present_serves(tmp_path, synthetic_gdb, fake_tas):
    cache = DatasetCache(tmp_path / "cache")
    spies = _Spies(synthetic_gdb)
    _load(cache, spies)
    frames = _load(cache, spies, offline=True)
    assert spies.optimise_calls == 1  # served from cache, no rebuild
    assert set(frames) == set(NGIS_LAYERS)


def test_offline_absent_raises(tmp_path, synthetic_gdb, fake_tas):
    cache = DatasetCache(tmp_path / "cache")
    spies = _Spies(synthetic_gdb)
    with pytest.raises(RuntimeError, match="Offline and NGIS TAS fast DB"):
        _load(cache, spies, offline=True)
    assert spies.optimise_calls == 0  # never optimised offline
