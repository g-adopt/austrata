"""Cache port: the dataset-cache interface the application layer depends on.

The concrete cache (hash-named GeoParquet files + a provenance manifest, with
the hybrid freshness logic from DESIGN) lives in the infrastructure layer. The
application layer asks only: do we have a fresh copy of this query, and if so
give it to me; otherwise let me fetch and then store it. The freshness decision
(conditional GET, fingerprint, TTL, ``force_refresh``) is the cache's concern,
not the use case's.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from geopandas import GeoDataFrame


@runtime_checkable
class DatasetCache(Protocol):
    """A provenance-aware store of query-result datasets keyed by a stable hash."""

    def get(self, key: str) -> Optional[GeoDataFrame]:
        """Return the cached dataset for ``key`` if present, else ``None``.

        Does not consult the network; pure read of the on-disk artifact.
        """
        ...

    def put(self, key: str, data: GeoDataFrame, provenance: dict) -> None:
        """Store ``data`` under ``key`` with its ``provenance`` manifest entry.

        Writes atomically (temp file then rename for both the parquet artifact
        and the manifest), so an interrupted write never commits a partial entry.
        """
        ...

    def is_fresh(self, key: str, validators: dict) -> bool:
        """Decide whether the cached entry for ``key`` is still current.

        ``validators`` carries whatever the backend can offer (etag,
        last_modified, server fingerprint such as WFS ``numberMatched`` + extent).
        Returns ``False`` when there is no entry, when validators indicate change,
        or when the entry has aged past its max-age TTL.
        """
        ...

    def has(self, key: str) -> bool:
        """True if an entry exists for ``key`` (regardless of freshness)."""
        ...
