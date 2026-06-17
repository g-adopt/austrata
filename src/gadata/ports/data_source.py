"""Data-source ports: the interfaces the application layer depends on.

These ``Protocol``s describe what a backend adapter must provide without
binding to WFS, ArcGIS, or HTTP. The infrastructure layer implements them; the
application layer and ``client`` depend only on these signatures. Adapters
return raw GeoJSON-shaped feature lists (``list[dict]``) — mapping to domain
objects is the mappers' job, keeping the boundary thin and the contract stable.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Protocol, runtime_checkable

from gadata.domain.region import Region


@runtime_checkable
class BoreholeSource(Protocol):
    """A source of borehole headers and downhole logs."""

    def fetch_headers(self, region: Region, cql_filter: Optional[str] = None) -> List[dict]:
        """Return all borehole-header GeoJSON features intersecting ``region``.

        Paginates to completion. ``cql_filter`` is an optional backend filter
        (e.g. ``drillingMethod='Diamond'``) ANDed with the spatial filter.
        """
        ...

    def fetch_header(self, identifier: str) -> Optional[dict]:
        """Return a single borehole header by identifier/ENO, or ``None``."""
        ...

    def fetch_stratigraphy(self, enos: Iterable[int]) -> List[dict]:
        """Return stratigraphy-log features for the given ENO set (chunked POST)."""
        ...

    def fetch_earth_material(self, enos: Iterable[int]) -> List[dict]:
        """Return earth-material-log features for the given ENO set (chunked POST)."""
        ...

    def count_headers(self, region: Region, cql_filter: Optional[str] = None) -> int:
        """Cheap count of headers in ``region`` (WFS ``resultType=hits``)."""
        ...


@runtime_checkable
class HydrogeologySource(Protocol):
    """A source of hydrogeology polygons."""

    def fetch_units(self, region: Region, where: Optional[str] = None) -> List[dict]:
        """Return all hydrogeology polygon features intersecting ``region``.

        Paginates to completion. ``where`` is an optional ArcGIS SQL predicate
        ANDed with the spatial intersect.
        """
        ...

    def count_units(self, region: Region, where: Optional[str] = None) -> int:
        """Cheap count of units in ``region`` (ArcGIS ``returnCountOnly``)."""
        ...
