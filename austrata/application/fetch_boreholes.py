"""Use case: build the cache fetch-plan for borehole headers (WFS).

The WFS backend emits no ETag/Last-Modified, so freshness uses the cheap
``resultType=hits`` ``numberMatched`` count as a server fingerprint, ANDed with
the cache's max-age TTL backstop (a same-count content edit is undetectable by
count alone — documented best-effort, ``force_refresh`` is the only guarantee).

This module knows the *strategy* (fingerprint by count, no conditional headers)
but not HTTP detail — it composes a :class:`FetchPlan` from the injected
``BoreholeSource`` adapter.
"""
from __future__ import annotations

from typing import Optional

from austrata.domain.region import Region
from austrata.infrastructure.dataset_cache import FetchPlan
from austrata.infrastructure.feature_mapper import borehole_features_to_gdf
from austrata.ports.data_source import BoreholeSource

WFS_SERVICE = "ga-boreholes-wfs"
WFS_LAYER = "gsmlp:BoreholeView"
WFS_CITATION = (
    "Geoscience Australia Borehole Database, accessed via the GA Boreholes "
    "WFS (gsmlp:BoreholeView)."
)
WFS_LICENSE = "CC BY 4.0"
WFS_SOURCE_URL = "https://services.ga.gov.au/gis/boreholes/wfs"
WFS_SERVICE_VERSION = "WFS 2.0.0"


def header_cache_key(region: Region, cql_filter: Optional[str]) -> str:
    """Stable cache key for a headers query: region geometry + layer + filter."""
    descriptor = f"{WFS_SERVICE}|{WFS_LAYER}|{cql_filter or ''}"
    return f"{region.cache_key()}-{_short_hash(descriptor)}"


def build_header_plan(
    source: BoreholeSource, region: Region, cql_filter: Optional[str]
) -> FetchPlan:
    """Compose the FetchPlan the cache uses to fetch/revalidate headers."""

    def fingerprint(_stored: dict) -> dict:
        # WFS fingerprint is the live count; it does not depend on the stored
        # entry (``_stored``), unlike the ArcGIS etag probe.
        return {"server_fingerprint": {"numberMatched": source.count_headers(region, cql_filter)}}

    def unchanged(stored: dict, current: dict) -> bool:
        return stored.get("server_fingerprint") == current.get("server_fingerprint")

    def fetch():
        features = source.fetch_headers(region, cql_filter)
        gdf = borehole_features_to_gdf(features)
        provenance_extra = {
            "server_fingerprint": {"numberMatched": len(features)},
            "citation": WFS_CITATION,
            "license": WFS_LICENSE,
            "source_url": WFS_SOURCE_URL,
            "service_version": WFS_SERVICE_VERSION,
        }
        return gdf, provenance_extra

    return FetchPlan(
        fetch_fn=fetch,
        fingerprint_fn=fingerprint,
        unchanged_fn=unchanged,
        query={
            "service": WFS_SERVICE,
            "layer": WFS_LAYER,
            "region_wkt": region.geometry.wkt,
            "filter": cql_filter,
        },
    )


def log_cache_key(kind: str, enos) -> str:
    """Stable cache key for a log pull, keyed by kind + the sorted ENO set."""
    unique = sorted({int(e) for e in enos})
    descriptor = f"{WFS_SERVICE}|logs|{kind}|{','.join(map(str, unique))}"
    return f"logs-{kind}-{_short_hash(descriptor)}"


def build_log_plan(source: BoreholeSource, kind: str, enos) -> FetchPlan:
    """Compose a FetchPlan for an ENO-set log pull (stratigraphy/earth-material).

    Logs for a fixed ENO set are content-stable enough that freshness leans on
    the TTL backstop; no cheap per-ENO fingerprint exists, so no fingerprint_fn
    is supplied (the cache then revalidates purely by max-age).
    """
    from austrata.infrastructure.feature_mapper import log_features_to_dataframe

    unique = sorted({int(e) for e in enos})

    def fetch():
        if kind == "stratigraphy":
            features = source.fetch_stratigraphy(unique)
        elif kind == "earth_material":
            features = source.fetch_earth_material(unique)
        else:
            raise ValueError(f"Unknown log kind {kind!r}.")
        df = log_features_to_dataframe(features)
        provenance_extra = {
            "citation": WFS_CITATION,
            "license": WFS_LICENSE,
            "source_url": WFS_SOURCE_URL,
            "service_version": WFS_SERVICE_VERSION,
        }
        return df, provenance_extra

    return FetchPlan(
        fetch_fn=fetch,
        query={"service": WFS_SERVICE, "layer": f"logs:{kind}", "enos": unique},
    )


def _short_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
