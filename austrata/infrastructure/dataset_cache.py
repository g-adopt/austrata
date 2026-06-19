"""Provenance-aware dataset cache (implements the ``DatasetCache`` port).

One logical query (region + service + layer + filter) maps to one cache entry:
a GeoParquet file ``<key>.parquet`` plus a record in a single ``manifest.json``.
The cache decides *freshness* before refetching, and writes atomically so an
interrupted pull never corrupts the store.

FreshnessStrategy
-----------------
The two backends revalidate differently (WFS has no ETag, so it fingerprints
``numberMatched`` + extent with a TTL backstop; ArcGIS uses an ETag via
``If-None-Match``). Rather than bake either into the cache, freshness and
fetching are injected per query as a :class:`FetchPlan`:

* ``conditional_headers(stored)`` -> request headers for a cheap conditional
  probe (e.g. ``If-None-Match`` from the stored etag), or ``{}``.
* ``fingerprint(stored)`` -> a dict of current remote validators (etag,
  last_modified, server_fingerprint) cheaply, plus a ``not_modified`` flag if a
  conditional probe already proved freshness (a 304).
* ``is_unchanged(stored, current)`` -> backend comparison of stored vs current
  validators.
* ``fetch()`` -> ``(GeoDataFrame, provenance_extra)`` — the full paginated pull
  and the provenance fields it learned (validators, source_url, citation, ...).

The cache owns only orchestration (offline / force_refresh / TTL / atomicity);
the per-backend semantics live entirely in the injected plan, so the class never
mentions WFS or ArcGIS.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol

import geopandas as gpd
from filelock import FileLock
from platformdirs import user_cache_dir

logger = logging.getLogger("austrata.cache")

CACHE_FORMAT_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days TTL backstop
MANIFEST_NAME = "manifest.json"
_ENV_DIR = "AUSTRATA_DATA_DIR"


class FreshnessStrategy(Protocol):
    """Per-query freshness + fetch plan injected by the application layer."""

    def conditional_headers(self, stored: dict) -> dict: ...
    def fingerprint(self, stored: dict) -> dict: ...
    def is_unchanged(self, stored: dict, current: dict) -> bool: ...
    def fetch(self) -> "tuple[gpd.GeoDataFrame, dict]": ...


@dataclass
class FetchPlan:
    """A concrete, composable :class:`FreshnessStrategy`.

    The application layer builds one of these per query from small callables, so
    each backend supplies only the bits that differ. All callables are optional
    except ``fetch``; sensible defaults make a pure TTL strategy work with none
    of the conditional/fingerprint machinery.
    """

    fetch_fn: Callable[[], "tuple[gpd.GeoDataFrame, dict]"]
    fingerprint_fn: Optional[Callable[[dict], dict]] = None
    unchanged_fn: Optional[Callable[[dict, dict], bool]] = None
    conditional_headers_fn: Optional[Callable[[dict], dict]] = None
    query: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    def conditional_headers(self, stored: dict) -> dict:
        if self.conditional_headers_fn is None:
            return {}
        return self.conditional_headers_fn(stored)

    def fingerprint(self, stored: dict) -> dict:
        if self.fingerprint_fn is None:
            return {}
        return self.fingerprint_fn(stored)

    def is_unchanged(self, stored: dict, current: dict) -> bool:
        if current.get("not_modified"):
            return True
        if self.unchanged_fn is None:
            return False  # no fingerprint -> rely on TTL only
        return self.unchanged_fn(stored, current)

    def fetch(self) -> "tuple[gpd.GeoDataFrame, dict]":
        return self.fetch_fn()


class DatasetCache:
    """Hash-named GeoParquet store with a provenance manifest and freshness."""

    def __init__(
        self,
        cache_dir: Optional[os.PathLike | str] = None,
        *,
        offline: bool = False,
        max_age: float = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        self.cache_dir = Path(cache_dir or os.environ.get(_ENV_DIR) or user_cache_dir("austrata"))
        self.offline = offline
        self.max_age = max_age
        self._manifest_path = self.cache_dir / MANIFEST_NAME
        self._lock = FileLock(str(self.cache_dir / f"{MANIFEST_NAME}.lock"))

    # -- paths / manifest ------------------------------------------------

    def _ensure_dir(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _parquet_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.parquet"

    def _read_manifest(self) -> dict:
        if not self._manifest_path.exists():
            return {"cache_format_version": CACHE_FORMAT_VERSION, "entries": {}}
        try:
            with open(self._manifest_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            logger.warning("Manifest unreadable; treating cache as empty.")
            return {"cache_format_version": CACHE_FORMAT_VERSION, "entries": {}}

    def _write_manifest(self, manifest: dict) -> None:
        """Atomically replace the manifest (temp file + os.replace)."""
        self._ensure_dir()
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, prefix=".manifest.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._manifest_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _entry(self, key: str) -> Optional[dict]:
        return self._read_manifest().get("entries", {}).get(key)

    def provenance(self, key: str) -> dict:
        """The manifest provenance record for ``key`` (or ``{}`` if absent)."""
        return dict(self._entry(key) or {})

    # -- port: low-level get/put/has/is_fresh ---------------------------

    def has(self, key: str) -> bool:
        return self._entry(key) is not None and self._parquet_path(key).exists()

    def get(self, key: str) -> Optional[gpd.GeoDataFrame]:
        path = self._parquet_path(key)
        if not path.exists():
            return None
        return gpd.read_parquet(path)

    def _get_required(self, key: str) -> gpd.GeoDataFrame:
        """Read a parquet that the caller has already confirmed present.

        Used by the guarded ``get_or_fetch`` paths so the return type is an
        honest ``GeoDataFrame`` (never ``None``); raises if it has gone missing
        underneath us (e.g. cleared by another process between check and read).
        """
        data = self.get(key)
        if data is None:
            raise RuntimeError(
                f"Cached parquet for key={key} vanished after a freshness check."
            )
        return data

    def put(self, key: str, data: gpd.GeoDataFrame, provenance: dict) -> None:
        """Store ``data`` atomically and record its manifest entry under lock."""
        self._ensure_dir()
        content_sha = self._write_parquet_atomic(key, data)
        entry = self._build_entry(key, content_sha, len(data), provenance)
        with self._lock:
            manifest = self._read_manifest()
            manifest.setdefault("cache_format_version", CACHE_FORMAT_VERSION)
            manifest.setdefault("entries", {})[key] = entry
            self._write_manifest(manifest)
        logger.info("Cache write: key=%s features=%d", key, len(data))

    def is_fresh(self, key: str, validators: dict) -> bool:
        """Freshness from already-gathered ``validators`` (no network here).

        ``validators`` may carry ``not_modified`` (a 304 happened), a current
        fingerprint, and an ``unchanged`` boolean the caller computed. Combined
        with the stored ``fetched_at`` against ``max_age``.
        """
        entry = self._entry(key)
        if entry is None or not self._parquet_path(key).exists():
            return False
        if self._is_expired(entry):
            return False
        if validators.get("not_modified"):
            return True
        return bool(validators.get("unchanged", False))

    # -- main entry point ------------------------------------------------

    def get_or_fetch(
        self,
        key: str,
        plan: FreshnessStrategy,
        *,
        force_refresh: bool = False,
    ) -> gpd.GeoDataFrame:
        """Return the dataset for ``key``, fetching/revalidating as needed."""
        entry = self._entry(key)
        # ``entry`` is the manifest record only when both it and its parquet
        # exist; narrowing to a local keeps the type a plain ``dict`` below.
        if entry is not None and not self._parquet_path(key).exists():
            entry = None

        if self.offline:
            if entry is not None:
                logger.info("Cache hit (offline): key=%s", key)
                return self._get_required(key)
            raise RuntimeError(
                f"Offline and no cached entry for key={key}; cannot fetch."
            )

        if force_refresh:
            logger.info("Cache refresh forced: key=%s", key)
            return self._refetch(key, plan)

        if entry is not None and not self._is_expired(entry):
            current = plan.fingerprint(entry)
            if plan.is_unchanged(entry, current):
                logger.info("Cache hit (revalidated fresh): key=%s", key)
                return self._get_required(key)
            logger.info("Cache stale (validators changed): key=%s", key)
        elif entry is not None:
            logger.info("Cache stale (TTL expired): key=%s", key)
        else:
            logger.info("Cache miss: key=%s", key)

        return self._refetch(key, plan)

    def _refetch(self, key: str, plan: FreshnessStrategy) -> gpd.GeoDataFrame:
        # fetch() must complete fully before anything is committed; any
        # exception/KeyboardInterrupt propagates and nothing is promoted.
        data, provenance_extra = plan.fetch()
        provenance = dict(getattr(plan, "provenance", {}))
        provenance.update(provenance_extra or {})
        provenance.setdefault("query", getattr(plan, "query", {}))
        self.put(key, data, provenance)
        return data

    # -- management API --------------------------------------------------

    def list(self) -> list[str]:
        """All cached keys (manifest order)."""
        return list(self._read_manifest().get("entries", {}).keys())

    def info(self) -> dict:
        """Summary: entry count, total size on disk, and per-entry metadata."""
        entries = self._read_manifest().get("entries", {})
        total_bytes = 0
        details = {}
        for key, entry in entries.items():
            path = self._parquet_path(key)
            size = path.stat().st_size if path.exists() else 0
            total_bytes += size
            details[key] = {
                "feature_count": entry.get("feature_count"),
                "fetched_at": entry.get("fetched_at"),
                "size_bytes": size,
                "query": entry.get("query"),
            }
        return {
            "cache_dir": str(self.cache_dir),
            "entry_count": len(entries),
            "total_bytes": total_bytes,
            "entries": details,
        }

    def clear(self, key: Optional[str] = None) -> None:
        """Remove one entry (``key``) or the whole cache (``key=None``)."""
        with self._lock:
            if key is None:
                if self.cache_dir.exists():
                    for child in self.cache_dir.glob("*.parquet"):
                        child.unlink()
                    if self._manifest_path.exists():
                        self._manifest_path.unlink()
                logger.info("Cache cleared (all entries).")
                return
            manifest = self._read_manifest()
            manifest.get("entries", {}).pop(key, None)
            self._write_manifest(manifest)
        path = self._parquet_path(key)
        if path.exists():
            path.unlink()
        logger.info("Cache cleared: key=%s", key)

    # -- helpers ---------------------------------------------------------

    def _write_parquet_atomic(self, key: str, data: gpd.GeoDataFrame) -> str:
        """Write to ``<key>.partial`` then ``os.replace`` to ``<key>.parquet``.

        A failure or interrupt before the replace leaves only the quarantined
        ``.partial`` (which we clean up); the committed ``.parquet`` is never
        half-written.
        """
        self._ensure_dir()
        partial = self.cache_dir / f"{key}.partial"
        final = self._parquet_path(key)
        try:
            data.to_parquet(partial)
            content_sha = self._sha256_file(partial)
            os.replace(partial, final)
            return content_sha
        finally:
            if partial.exists():
                partial.unlink()

    def _build_entry(self, key: str, content_sha: str, count: int, provenance: dict) -> dict:
        prov = provenance or {}
        return {
            "key": key,
            "query": prov.get("query", {}),
            "filename": f"{key}.parquet",
            "content_sha256": content_sha,
            "feature_count": count,
            "etag": prov.get("etag"),
            "last_modified": prov.get("last_modified"),
            "server_fingerprint": prov.get("server_fingerprint"),
            "fetched_at": time.time(),
            "citation": prov.get("citation"),
            "license": prov.get("license"),
            "source_url": prov.get("source_url"),
            "service_version": prov.get("service_version"),
            "cache_format_version": CACHE_FORMAT_VERSION,
        }

    def _is_expired(self, entry: dict) -> bool:
        fetched_at = entry.get("fetched_at")
        if fetched_at is None:
            return True
        return (time.time() - float(fetched_at)) > self.max_age

    @staticmethod
    def _sha256_file(path: Path) -> str:
        import hashlib

        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
