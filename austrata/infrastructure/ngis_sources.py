"""NGIS source registry — the single source of truth for the state cores.

The Bureau of Meteorology National Groundwater Information System (NGIS) ships
as per-state ESRI File Geodatabases on data.gov.au. This module pins, for each
state we serve, everything the downloader and the provenance layer need: the
primary data.gov.au resource URL, our public S3 mirror (the fallback if
data.gov.au is unavailable), the expected zip size and md5 (so a download is
verified, not trusted), the ``.gdb`` path inside the archive, and the citation
metadata.

Only NSW, VIC and QLD exist as standalone cores on data.gov.au. SA/WA/NT/TAS
live only inside the national compilation, whose download is currently truncated
(reported to the custodian), so they are deliberately absent here.

Reproducibility: a result fetched from NGIS traces back through ``provenance()``
to the exact artifact pinned below (URL + md5 + vintage), never to a moving
target. Update this table — not scattered constants — when a custodian reissues.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

#: Base of our public DigitalOcean Spaces mirror (credential-free HTTPS GET).
MIRROR_BASE = "https://gadopt.syd1.digitaloceanspaces.com/austrata/ngis"

#: data.gov.au licence for all three cores (Creative Commons Attribution).
NGIS_LICENSE = "CC BY 4.0"


@dataclass(frozen=True)
class NgisSource:
    """Pinned descriptor for one state's NGIS core geodatabase."""

    state: str               # "NSW" | "VIC" | "QLD"
    vintage: str             # extract date, e.g. "2014-11"
    dataset_url: str         # human-readable data.gov.au landing page
    resource_url: str        # primary direct download (data.gov.au)
    mirror_url: str          # our S3 fallback download
    zip_bytes: int           # expected archive size (byte-for-byte check)
    zip_md5: str             # expected archive md5 (integrity check)
    gdb_relpath: str         # path of the .gdb directory inside the archive
    # (min_lon, min_lat, max_lon, max_lat) in EPSG:4283 — the state's geographic
    # extent, used by the federated client to route a box only to states it can
    # cover (so a NSW box never opens the 1 GB VIC gdb). Derived from the gdb's
    # NGIS_Bore lon/lat bounds, rounded outward ~0.01° for safety. ``None`` = unknown.
    extent: Optional[Tuple[float, float, float, float]] = None

    @property
    def source(self) -> str:
        """Human-readable provenance source string for the cache entry."""
        return (
            f"Bureau of Meteorology National Groundwater Information System "
            f"(NGIS), {self.state} core, {self.vintage} extract"
        )

    @property
    def citation(self) -> str:
        """One-line citation, mirroring the GA provenance style."""
        return (
            f"{self.source}. Licensed {NGIS_LICENSE}. "
            f"Source: {self.dataset_url}"
        )


#: The pinned registry. Keyed by uppercase state code.
NGIS_SOURCES: Dict[str, NgisSource] = {
    "NSW": NgisSource(
        state="NSW",
        vintage="2014-11",
        dataset_url="https://data.gov.au/data/dataset/b0f37930-a810-4819-8fb6-8008538fa53b",
        resource_url=(
            "https://data.gov.au/data/dataset/b0f37930-a810-4819-8fb6-8008538fa53b/"
            "resource/0cbae516-efef-4ab5-8685-b0570ac9ab4c/"
            "download/6c364d09-fc3b-47c3-aa98-6c702d3d8137.zip"
        ),
        mirror_url=f"{MIRROR_BASE}/nsw_ngis_core_v2pt3.zip",
        zip_bytes=98390403,
        zip_md5="8c5006f6e136eec8700464f8d1783373",
        gdb_relpath=(
            "NSWOfficeOfWaterNationalGroundwaterInformationSystem20141101/"
            "NSW_NGIS_Core_v2pt3.gdb"
        ),
        extent=(140.99, -37.70, 153.64, -28.10),
    ),
    "VIC": NgisSource(
        state="VIC",
        vintage="2014-03",
        dataset_url="https://data.gov.au/data/dataset/2eb2630f-313e-4542-9137-c6d7d82171b0",
        resource_url=(
            "https://data.gov.au/data/dataset/2eb2630f-313e-4542-9137-c6d7d82171b0/"
            "resource/54963b10-0515-43f5-a7ec-285f5fecacb9/"
            "download/fc6dbf39-d786-4412-b631-04695db4cc90.zip"
        ),
        mirror_url=f"{MIRROR_BASE}/vic_ngis_core_2014.zip",
        zip_bytes=1105719595,
        zip_md5="1138ee6a47e3c37cef174c20b9f6c76f",
        gdb_relpath=(
            "DEPI_NGIS_Core_v2pt3_20140321/DEPI_NGIS_Core_v2pt3_20140321.gdb"
        ),
        extent=(140.95, -39.05, 149.90, -34.02),
    ),
    "QLD": NgisSource(
        state="QLD",
        vintage="2014",
        dataset_url="https://data.gov.au/data/dataset/d2c06543-7389-4ac2-86c0-74b077bcdaa5",
        resource_url=(
            "https://data.gov.au/data/dataset/d2c06543-7389-4ac2-86c0-74b077bcdaa5/"
            "resource/02424659-834a-4a60-bc83-a1b4ddd23ee8/"
            "download/9f7573c0-e238-4a3c-a62b-a0326eaa9254.zip"
        ),
        mirror_url=f"{MIRROR_BASE}/qld_ngis_core.zip",
        zip_bytes=48203959,
        zip_md5="0e3b845c35c8e37a867ee5f9e7132a32",
        gdb_relpath="NGIS_QLD_Core_superseded/NGIS_QLD_Core.gdb",
        extent=(137.90, -30.80, 153.57, -9.42),
    ),
}

#: States NGIS covers as standalone cores (others need the national file).
NGIS_STATES = tuple(NGIS_SOURCES.keys())


def get_source(state: str) -> NgisSource:
    """Return the pinned source for ``state`` (case-insensitive), or raise."""
    key = (state or "").strip().upper()
    try:
        return NGIS_SOURCES[key]
    except KeyError:
        raise ValueError(
            f"no NGIS core for {state!r}; available: {', '.join(NGIS_STATES)} "
            f"(SA/WA/NT/TAS exist only in the national compilation)"
        ) from None


def ngis_states_intersecting(region_or_bbox) -> List[str]:
    """State codes whose extent intersects ``region_or_bbox`` (edge-inclusive).

    Accepts a ``(min_lon, min_lat, max_lon, max_lat)`` tuple or anything exposing
    a ``.bounds`` of that shape (a :class:`~austrata.domain.region.Region`). The
    federated client uses this so a box only opens the gdbs it can actually cover.
    A state whose ``extent`` is ``None`` is treated as a possible match (kept).
    """
    bounds = getattr(region_or_bbox, "bounds", region_or_bbox)
    min_lon, min_lat, max_lon, max_lat = bounds
    out = []
    for code, src in NGIS_SOURCES.items():
        ext = src.extent
        if ext is None or _bbox_overlaps(ext, (min_lon, min_lat, max_lon, max_lat)):
            out.append(code)
    return out


def _bbox_overlaps(a: Tuple[float, float, float, float],
                   b: Tuple[float, float, float, float]) -> bool:
    """Edge-inclusive overlap of two (min_lon,min_lat,max_lon,max_lat) boxes."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])
