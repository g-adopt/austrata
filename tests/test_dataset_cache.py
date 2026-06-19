"""Offline unit tests for the DatasetCache (tmp_path, fake fetch callables)."""
import json

import geopandas as gpd
import pytest
from shapely.geometry import Point

from austrata.infrastructure.dataset_cache import (
    CACHE_FORMAT_VERSION,
    DatasetCache,
    FetchPlan,
)

KEY = "abc123"


def _gdf(n=2):
    pts = [Point(149.0 + i * 0.01, -35.0 - i * 0.01) for i in range(n)]
    return gpd.GeoDataFrame({"eno": list(range(n))}, geometry=pts, crs="EPSG:4283")


def _plan(data, *, calls, fingerprint=None, unchanged=None, provenance=None):
    """A FetchPlan whose fetch records each invocation in ``calls``."""
    def fetch_fn():
        calls.append("fetch")
        return data, (provenance or {})

    return FetchPlan(
        fetch_fn=fetch_fn,
        fingerprint_fn=fingerprint,
        unchanged_fn=unchanged,
        provenance=provenance or {},
    )


def _cache(tmp_path, **kw):
    return DatasetCache(cache_dir=tmp_path, **kw)


def test_put_get_roundtrip_identical(tmp_path):
    cache = _cache(tmp_path)
    gdf = _gdf(3)
    cache.put(KEY, gdf, {"query": {"service": "wfs"}})
    out = cache.get(KEY)
    assert list(out["eno"]) == [0, 1, 2]
    assert out.crs.to_epsg() == 4283
    assert out.geometry.equals(gdf.geometry)


def test_get_missing_returns_none(tmp_path):
    assert _cache(tmp_path).get("nope") is None


def test_manifest_has_provenance_fields(tmp_path):
    cache = _cache(tmp_path)
    cache.put(KEY, _gdf(), {
        "query": {"service": "wfs", "layer": "BoreholeView"},
        "citation": "Geoscience Australia",
        "license": "CC BY 4.0",
        "source_url": "https://services.ga.gov.au/...",
        "etag": '"xyz"',
    })
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["cache_format_version"] == CACHE_FORMAT_VERSION
    entry = manifest["entries"][KEY]
    assert entry["feature_count"] == 2
    assert entry["citation"] == "Geoscience Australia"
    assert entry["license"] == "CC BY 4.0"
    assert entry["etag"] == '"xyz"'
    assert entry["content_sha256"]
    assert entry["cache_format_version"] == CACHE_FORMAT_VERSION


# -- freshness ----------------------------------------------------------

def test_matching_fingerprint_within_ttl_no_refetch(tmp_path):
    cache = _cache(tmp_path)
    calls = []
    # Seed an entry.
    cache.put(KEY, _gdf(), {"server_fingerprint": {"numberMatched": 10}})
    plan = _plan(
        _gdf(), calls=calls,
        fingerprint=lambda stored: {"server_fingerprint": {"numberMatched": 10}},
        unchanged=lambda stored, cur: stored.get("server_fingerprint") == cur.get("server_fingerprint"),
    )
    cache.get_or_fetch(KEY, plan)
    assert calls == []  # served from cache, fetch not called


def test_changed_fingerprint_triggers_refetch(tmp_path):
    cache = _cache(tmp_path)
    calls = []
    cache.put(KEY, _gdf(), {"server_fingerprint": {"numberMatched": 10}})
    plan = _plan(
        _gdf(), calls=calls,
        fingerprint=lambda stored: {"server_fingerprint": {"numberMatched": 99}},
        unchanged=lambda stored, cur: stored.get("server_fingerprint") == cur.get("server_fingerprint"),
    )
    cache.get_or_fetch(KEY, plan)
    assert calls == ["fetch"]


def test_expired_ttl_triggers_refetch(tmp_path):
    cache = _cache(tmp_path, max_age=0)  # everything immediately stale
    calls = []
    cache.put(KEY, _gdf(), {"server_fingerprint": {"numberMatched": 10}})
    plan = _plan(
        _gdf(), calls=calls,
        fingerprint=lambda stored: {"server_fingerprint": {"numberMatched": 10}},
        unchanged=lambda stored, cur: True,
    )
    cache.get_or_fetch(KEY, plan)
    assert calls == ["fetch"]  # TTL backstop forces refetch despite match


def test_force_refresh_always_refetches(tmp_path):
    cache = _cache(tmp_path)
    calls = []
    cache.put(KEY, _gdf(), {"server_fingerprint": {"numberMatched": 10}})
    plan = _plan(
        _gdf(), calls=calls,
        fingerprint=lambda stored: {"server_fingerprint": {"numberMatched": 10}},
        unchanged=lambda stored, cur: True,
    )
    cache.get_or_fetch(KEY, plan, force_refresh=True)
    assert calls == ["fetch"]


def test_not_modified_304_is_fresh(tmp_path):
    cache = _cache(tmp_path)
    calls = []
    cache.put(KEY, _gdf(), {"etag": '"v1"'})
    # fingerprint reports a 304 -> unchanged regardless of unchanged_fn.
    plan = _plan(_gdf(), calls=calls, fingerprint=lambda stored: {"not_modified": True})
    cache.get_or_fetch(KEY, plan)
    assert calls == []


def test_offline_present_served(tmp_path):
    cache = _cache(tmp_path, offline=True)
    cache.put(KEY, _gdf(3), {})
    out = cache.get_or_fetch(KEY, _plan(_gdf(), calls=[]))
    assert len(out) == 3


def test_offline_absent_raises(tmp_path):
    cache = _cache(tmp_path, offline=True)
    with pytest.raises(RuntimeError, match="Offline"):
        cache.get_or_fetch(KEY, _plan(_gdf(), calls=[]))


def test_miss_fetches_and_stores(tmp_path):
    cache = _cache(tmp_path)
    calls = []
    plan = _plan(_gdf(4), calls=calls, provenance={"query": {"service": "wfs"}})
    out = cache.get_or_fetch(KEY, plan)
    assert calls == ["fetch"]
    assert len(out) == 4
    assert cache.has(KEY)


# -- atomicity ----------------------------------------------------------

def test_fetch_exception_leaves_no_committed_entry(tmp_path):
    cache = _cache(tmp_path)

    def boom():
        raise ValueError("mid-fetch failure")

    plan = FetchPlan(fetch_fn=boom)
    with pytest.raises(ValueError, match="mid-fetch"):
        cache.get_or_fetch(KEY, plan)
    # No parquet promoted, no manifest entry, no leftover .partial.
    assert not (tmp_path / f"{KEY}.parquet").exists()
    assert not (tmp_path / f"{KEY}.partial").exists()
    assert cache.list() == []


def test_parquet_write_failure_leaves_manifest_intact(tmp_path, monkeypatch):
    cache = _cache(tmp_path)
    # Seed a good entry first.
    cache.put("good", _gdf(), {"query": {"service": "wfs"}})

    # Now make a parquet write blow up mid-way and ensure the manifest survives.
    bad = _gdf()
    orig = gpd.GeoDataFrame.to_parquet

    def failing_to_parquet(self, path, *a, **k):
        # Touch the .partial then fail, simulating an interrupted write.
        open(path, "wb").close()
        raise OSError("disk full")

    monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", failing_to_parquet)
    with pytest.raises(OSError, match="disk full"):
        cache.put("bad", bad, {})
    monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", orig)

    # Manifest is still valid JSON and still has only the good entry.
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert "good" in manifest["entries"]
    assert "bad" not in manifest["entries"]
    assert not (tmp_path / "bad.parquet").exists()
    assert not (tmp_path / "bad.partial").exists()


def test_unreadable_manifest_treated_as_empty(tmp_path):
    cache = _cache(tmp_path)
    cache._ensure_dir()
    (tmp_path / "manifest.json").write_text("{ this is not json")
    assert cache.list() == []  # does not raise


# -- management API -----------------------------------------------------

def test_list_info_clear(tmp_path):
    cache = _cache(tmp_path)
    cache.put("k1", _gdf(2), {"query": {"service": "wfs"}})
    cache.put("k2", _gdf(5), {"query": {"service": "arcgis"}})
    assert set(cache.list()) == {"k1", "k2"}

    info = cache.info()
    assert info["entry_count"] == 2
    assert info["total_bytes"] > 0
    assert info["entries"]["k2"]["feature_count"] == 5

    cache.clear("k1")
    assert cache.list() == ["k2"]
    assert not (tmp_path / "k1.parquet").exists()

    cache.clear()
    assert cache.list() == []
    assert not (tmp_path / "k2.parquet").exists()


def test_env_var_overrides_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AUSTRATA_DATA_DIR", str(tmp_path / "envcache"))
    cache = DatasetCache()
    cache.put(KEY, _gdf(), {})
    assert (tmp_path / "envcache" / f"{KEY}.parquet").exists()
