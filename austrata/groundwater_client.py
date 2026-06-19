"""``GroundwaterClient`` — federated "one box, all sources" borehole query.

Wraps a :class:`GADataClient` and an :class:`NGISClient` and answers a lon/lat box
by querying GA **and** every NGIS state core whose extent intersects the box,
returning a single source-tagged :class:`BoreholeCollection`. There is no GA↔NGIS
dedup: an overlapping physical bore appears once per source, each carrying its
own ``.source`` tag (GA uses ENO, NGIS uses HydroCode — no shared key to merge on).

State-extent routing means a NSW box never opens the 1 GB VIC gdb. ``load_logs``
dispatches per source: GA bores fetch via the WFS/ENO path, NGIS bores via the
gdb/HydroCode path; construction (NGIS-only) skips GA without error. Because the
merged collection holds the *same* ``Borehole`` instances as the per-source
sub-collections, populating a sub-collection's logs populates them in the merge.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

from shapely.geometry.base import BaseGeometry

from austrata.client import GADataClient
from austrata.domain.borehole import Borehole, BoreholeCollection
from austrata.domain.region import Region
from austrata.infrastructure.http import HttpClient
from austrata.infrastructure.ngis_sources import ngis_states_intersecting
from austrata.ngis_client import NGISClient

logger = logging.getLogger("austrata.groundwater")

BBox = Tuple[float, float, float, float]


class GroundwaterClient:
    """Federated facade over GA + the NGIS state cores covering a box."""

    def __init__(
        self,
        cache_dir=None,
        ngis_dir=None,
        *,
        offline: bool = False,
        http: Optional[HttpClient] = None,
        ga: Optional[GADataClient] = None,
        ngis: Optional[NGISClient] = None,
    ) -> None:
        http = http or HttpClient()
        self.ga = ga or GADataClient(cache_dir, offline=offline, http=http)
        self.ngis = ngis or NGISClient(ngis_dir, cache_dir, offline=offline, http=http)

    # -- federated box query --------------------------------------------

    def boreholes(
        self,
        *,
        bbox: Optional[BBox] = None,
        region: Optional[BaseGeometry] = None,
        sources: Optional[Sequence[str]] = None,
        force_refresh: bool = False,
    ) -> BoreholeCollection:
        """GA + intersecting-NGIS bores in ``bbox``/``region``, one tagged collection.

        ``sources`` restricts which backends are queried (default: all that cover
        the box). Accepted forms, case-insensitive: ``"GA"``; ``"NGIS"`` (all
        intersecting states); a specific ``"NGIS:NSW"``; or a bare state code
        ``"NSW"`` (treated as ``NGIS:NSW``).
        """
        reg = self._resolve_region(region, bbox)
        wanted = _normalise_sources(sources)
        subs: List[BoreholeCollection] = []

        if _want_ga(wanted):
            ga_bc = self.ga.boreholes(region=reg.geometry, force_refresh=force_refresh)
            assert isinstance(ga_bc, BoreholeCollection)  # never count_only here
            subs.append(ga_bc)
        for state in ngis_states_intersecting(reg):
            if _want_state(wanted, state):
                subs.append(self.ngis.boreholes(state, region=reg.geometry,
                                                force_refresh=force_refresh))

        return self._merge(subs, reg)

    # -- merge + federated loading --------------------------------------

    def _merge(self, subs: List[BoreholeCollection], region: Region) -> BoreholeCollection:
        bores: List[Borehole] = [b for sub in subs for b in sub]
        merged = BoreholeCollection(bores, region)
        merged._loader = self._make_federated_loader(subs)
        merged._provenance = {
            "sources": [sub.provenance() for sub in subs],
            # A combined citation so the federated result attributes EVERY source
            # (GA + each NGIS state / BoM); the base citation() uses this string.
            "citation": _federated_citation(subs),
        }
        return merged

    @staticmethod
    def _make_federated_loader(subs: List[BoreholeCollection]):
        def load(kind: str = "stratigraphy", *, force_refresh: bool = False) -> None:
            for sub in subs:
                # Construction is NGIS-only: skip any sub-collection (i.e. GA)
                # whose bores can't carry it rather than erroring.
                if kind == "construction" and not _is_ngis(sub):
                    for b in sub:
                        b.set_construction([])
                    continue
                sub.load_logs(kind, force_refresh=force_refresh)
        return load

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _resolve_region(region: Optional[BaseGeometry], bbox: Optional[BBox]) -> Region:
        if region is not None and bbox is not None:
            raise ValueError("Pass either region= or bbox=, not both.")
        if bbox is not None:
            return Region.from_bbox(*bbox)
        if region is not None:
            return region if isinstance(region, Region) else Region(region)
        raise ValueError("A region= geometry or bbox= tuple is required.")


# -- source-filter parsing ----------------------------------------------


def _normalise_sources(sources: Optional[Sequence[str]]) -> Optional[set]:
    """Upper-case the requested source filter, or ``None`` for "all"."""
    if sources is None:
        return None
    return {s.strip().upper() for s in sources}


def _want_ga(wanted: Optional[set]) -> bool:
    return wanted is None or "GA" in wanted


def _want_state(wanted: Optional[set], state: str) -> bool:
    if wanted is None:
        return True
    state = state.upper()
    return "NGIS" in wanted or f"NGIS:{state}" in wanted or state in wanted


def _is_ngis(sub: BoreholeCollection) -> bool:
    """True when a sub-collection's bores are NGIS-sourced (carry construction)."""
    for b in sub:
        return bool(b.source and b.source.startswith("NGIS"))
    return False


def _federated_citation(subs: List[BoreholeCollection]) -> str:
    """Join each sub-collection's citation, deduped, in stable (query) order."""
    seen, parts = set(), []
    for sub in subs:
        cite = sub.citation()
        if cite and cite not in seen:
            seen.add(cite)
            parts.append(cite)
    return " ".join(parts)
