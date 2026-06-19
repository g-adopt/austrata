"""Use case: the NGIS fast-DB cache + the gdb↔fast-DB consistency gate (§6).

Turns "I need NGIS ``<state>``" into the four cached fast-DB GeoDataFrames
(``bores``, ``stratigraphy``, ``earth_material``, ``construction``), each a
:class:`DatasetCache` entry keyed ``ngis-<state>-<layer>-opt<version>``.

The consistency gate
--------------------
A state's whole gdb is optimised in **one** fiona pass producing all four frames,
so a rebuild runs ``ensure_gdb`` + ``optimise_state`` exactly once and stores all
four. Each entry persists a **stamp** — the registry's pinned ``gdb_md5`` and the
``optimiser_version`` — encoded as the entry's ``server_fingerprint`` (a key the
cache already round-trips). Freshness is then: every layer present AND its stored
fingerprint equals the currently-expected one. A version bump or a future md5
change flips the fingerprint and forces a rebuild; ``force_refresh`` forces one
unconditionally. ``offline`` serves from cache when complete, else raises — it
never downloads.

Dependency rule: this is the application layer, so the concrete infrastructure
(``ensure_gdb`` / ``optimise_state``) is **injected** as callables, not imported.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

import geopandas as gpd

from austrata.ports.cache import DatasetCache

#: The four fast-DB layers, in optimise order.
NGIS_LAYERS = ("bores", "stratigraphy", "earth_material", "construction")

#: Injected callable signatures (kept loose so the client wires the real infra).
EnsureGdb = Callable[..., object]
OptimiseState = Callable[..., Dict[str, gpd.GeoDataFrame]]
BuildStamp = Callable[[str], dict]


def ngis_cache_key(state: str, layer: str, optimiser_version: object) -> str:
    """Deterministic, filename-safe cache key, e.g. ``ngis-nsw-stratigraphy-opt1``."""
    return f"ngis-{state.strip().lower()}-{layer}-opt{optimiser_version}"


def expected_fingerprint(state: str, gdb_md5: str, optimiser_version: object) -> dict:
    """The fingerprint a fresh entry must carry: ``state|gdb_md5|opt<version>``.

    Stored under ``server_fingerprint`` (which :class:`DatasetCache` round-trips),
    so the gate can read it back and compare — not merely recompute it.
    """
    return {"ngis_stamp": f"{state.strip().upper()}|{gdb_md5}|opt{optimiser_version}"}


def load_ngis_frames(
    cache: DatasetCache,
    state: str,
    *,
    ensure_gdb: EnsureGdb,
    optimise_state: OptimiseState,
    optimiser_version: object,
    build_stamp: BuildStamp,
    ngis_dir: Optional[object] = None,
    http: Optional[object] = None,
    force_refresh: bool = False,
    offline: bool = False,
) -> Dict[str, gpd.GeoDataFrame]:
    """Return all four fast-DB frames for ``state``, building once if needed."""
    stamp = build_stamp(state)
    want = expected_fingerprint(state, stamp["gdb_md5"], optimiser_version)
    keys = {layer: ngis_cache_key(state, layer, optimiser_version) for layer in NGIS_LAYERS}

    if not force_refresh and _all_fresh(cache, keys, want):
        return _load_cached(cache, keys)

    if offline:
        # Never download offline; serve only if the full set is already cached.
        if _all_present(cache, keys):
            return _load_cached(cache, keys)
        raise RuntimeError(
            f"Offline and NGIS {state} fast DB is incomplete/stale; "
            f"run once online to build it."
        )

    return _rebuild(
        cache, state, keys, want, stamp,
        ensure_gdb=ensure_gdb, optimise_state=optimise_state,
        ngis_dir=ngis_dir, http=http, force_refresh=force_refresh,
    )


# -- freshness gate -----------------------------------------------------


def _all_present(cache: DatasetCache, keys: Dict[str, str]) -> bool:
    return all(cache.has(key) for key in keys.values())


def _all_fresh(cache: DatasetCache, keys: Dict[str, str], want: dict) -> bool:
    """Every layer present AND its stored stamp equals the expected one."""
    for key in keys.values():
        if not cache.has(key):
            return False
        stored = cache.provenance(key).get("server_fingerprint")
        if stored != want["ngis_stamp"]:
            return False
    return True


def _load_cached(cache: DatasetCache, keys: Dict[str, str]) -> Dict[str, gpd.GeoDataFrame]:
    frames = {}
    for layer, key in keys.items():
        frame = cache.get(key)
        if frame is None:
            raise RuntimeError(f"NGIS cache entry {key} vanished mid-load.")
        frames[layer] = frame
    return frames


# -- rebuild (one optimise pass, store all four) ------------------------


def _rebuild(
    cache: DatasetCache,
    state: str,
    keys: Dict[str, str],
    want: dict,
    stamp: dict,
    *,
    ensure_gdb: EnsureGdb,
    optimise_state: OptimiseState,
    ngis_dir: Optional[object],
    http: Optional[object],
    force_refresh: bool,
) -> Dict[str, gpd.GeoDataFrame]:
    # Local-first ensure: ``ensure_gdb`` only downloads when the gdb is absent;
    # ``force`` here re-acquires the gdb only on an explicit force_refresh.
    gdb_path = ensure_gdb(state, ngis_dir=ngis_dir, http=http, force=force_refresh)
    frames = optimise_state(gdb_path, state)

    out: Dict[str, gpd.GeoDataFrame] = {}
    for layer, key in keys.items():
        # A layer absent from the gdb still gets an (empty) entry so the set is
        # complete and the next call is a fresh cache hit, not a rescan.
        frame = frames.get(layer)
        if frame is None:
            frame = gpd.GeoDataFrame(geometry=[], crs="EPSG:4283")
        cache.put(key, frame, _provenance_for(layer, key, want, stamp))
        out[layer] = frame

    _sweep_superseded(cache, stamp["state"], keep=set(keys.values()))
    return out


def _sweep_superseded(cache: DatasetCache, state: str, keep: set) -> None:
    """Drop this state's stale-version fast-DB entries (e.g. after a version bump).

    A version bump changes the ``opt<version>`` key suffix, so the old per-layer
    parquet set would otherwise be orphaned on disk (NSW/VIC are hundreds of MB).
    Only this state's ``ngis-<state>-*`` entries not in ``keep`` are removed.
    """
    prefix = f"ngis-{state.strip().lower()}-"
    for key in list(cache.list()):
        if key.startswith(prefix) and key not in keep:
            cache.clear(key)


def _provenance_for(layer: str, key: str, want: dict, stamp: dict) -> dict:
    """Provenance dict for one entry, carrying the stamp as ``server_fingerprint``."""
    return {
        "server_fingerprint": want["ngis_stamp"],
        "citation": stamp.get("citation"),
        "license": "CC BY 4.0",
        "source_url": stamp.get("source_url"),
        "service_version": f"NGIS {stamp.get('vintage')} (opt{stamp.get('optimiser_version')})",
        "query": {"service": "ngis", "state": stamp.get("state"), "layer": layer},
    }
