"""Use case: build the cache fetch-plan for hydrogeology polygons (ArcGIS).

The ArcGIS backend emits an ETag, so freshness uses a conditional probe: send
``If-None-Match`` with the stored etag and treat a 304 as fresh. The probe is a
cheap ``returnCountOnly`` query the adapter owns (``probe_etag``), so this module
stays HTTP-free and the cache stays backend-agnostic.
"""
from __future__ import annotations

from typing import Optional, Protocol

from austrata.domain.region import Region
from austrata.infrastructure.dataset_cache import FetchPlan
from austrata.infrastructure.feature_mapper import hydrogeology_features_to_gdf

ARCGIS_SERVICE = "ga-hydrogeology-arcgis"
ARCGIS_LAYER = "Hydrogeology_of_Australia/0"
ARCGIS_CITATION = (
    "Hydrogeology of Australia, Geoscience Australia, accessed via the "
    "Hydrogeology_of_Australia ArcGIS MapServer (layer 0)."
)
ARCGIS_LICENSE = "CC BY 4.0"
ARCGIS_SOURCE_URL = (
    "https://services.ga.gov.au/gis/rest/services/"
    "Hydrogeology_of_Australia/MapServer"
)
ARCGIS_SERVICE_VERSION = "ArcGIS REST MapServer"


class HydrogeologyProbeSource(Protocol):
    """The hydrogeology source plus the conditional ETag probe this plan needs."""

    def fetch_units(self, region: Region, where: Optional[str] = None) -> list: ...
    def probe_etag(self, region: Region, where: Optional[str] = None,
                   etag: Optional[str] = None) -> dict: ...


def hydrogeology_cache_key(region: Region, where: Optional[str]) -> str:
    """Stable cache key for a hydrogeology query: region + layer + where."""
    descriptor = f"{ARCGIS_SERVICE}|{ARCGIS_LAYER}|{where or ''}"
    return f"{region.cache_key()}-{_short_hash(descriptor)}"


def build_hydrogeology_plan(
    source: HydrogeologyProbeSource, region: Region, where: Optional[str]
) -> FetchPlan:
    """Compose the FetchPlan the cache uses to fetch/revalidate hydrogeology."""

    def conditional_headers(stored: dict) -> dict:
        etag = stored.get("etag")
        return {"If-None-Match": etag} if etag else {}

    def fingerprint(stored: dict) -> dict:
        # A 304 here proves freshness; otherwise we carry the new etag forward.
        return source.probe_etag(region, where, etag=stored.get("etag"))

    def unchanged(stored: dict, current: dict) -> bool:
        if current.get("not_modified"):
            return True
        cur_etag = current.get("etag")
        return cur_etag is not None and cur_etag == stored.get("etag")

    def fetch():
        features = source.fetch_units(region, where)
        gdf = hydrogeology_features_to_gdf(features)
        probe = source.probe_etag(region, where)
        provenance_extra = {
            "etag": probe.get("etag"),
            "citation": ARCGIS_CITATION,
            "license": ARCGIS_LICENSE,
            "source_url": ARCGIS_SOURCE_URL,
            "service_version": ARCGIS_SERVICE_VERSION,
        }
        return gdf, provenance_extra

    return FetchPlan(
        fetch_fn=fetch,
        fingerprint_fn=fingerprint,
        unchanged_fn=unchanged,
        conditional_headers_fn=conditional_headers,
        query={
            "service": ARCGIS_SERVICE,
            "layer": ARCGIS_LAYER,
            "region_wkt": region.geometry.wkt,
            "filter": where,
        },
    )


def _short_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
