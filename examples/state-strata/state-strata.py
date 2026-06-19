"""Export borehole stratigraphy per source in the GA ABSUC table format.

ABSUC is Geoscience Australia's Australian Borehole Stratigraphic Units
Compilation — a flat, one-row-per-interval stratigraphy table keyed on borehole
identity, depth (measured and AHD) and unit name. ``ABSUC_sample.csv`` next to
this script is the reference layout; this example reproduces that exact column
order from austrata's data so the output drops straight into any workflow that
already consumes ABSUC.

Four sources are supported. The NGIS state cores (NSW/VIC/QLD) via ``NGISClient``
are the dense in-package per-state source, and the only one carrying the
per-interval AHD elevations that ABSUC's TOP_AHD_M/BASE_AHD_M columns want
natively; NGIS has no GA ENO/GUID, so those columns stay blank and the bore's
HydroCode rides in UWI. The GA national WFS via ``GADataClient`` is the opposite
trade: every bore carries a real ENO and unit PID (so GA_ENO and GA_ASUD_NO fill
in), but the per-interval AHD is null, so we derive it from the reference
elevation minus depth. Either way DATA_SOURCE records the real provenance ("GA",
"NGIS:NSW", ...), and a missing per-interval AHD falls back to ref minus depth.

First runs are slow: each NGIS state downloads and optimises its gdb once (a few
minutes, several hundred MB under AUSTRATA_NGIS_DIR); GA pulls the whole national
header + stratigraphy set over WFS once. Every run after is served from cache.

Run:  python examples/state-strata/state-strata.py [SOURCE ...]
      no args does NSW VIC QLD; pass any of NSW VIC QLD GA, e.g. "GA NSW".
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

from austrata import GADataClient, NGISClient
from austrata.infrastructure.ngis_sources import NGIS_STATES, get_source

OUT = Path(__file__).parent

# GA's national WFS is one dataset, not per-state; pull the whole continent.
AUS_NATIONAL_BBOX = (112.0, -44.0, 154.0, -9.0)  # mainland + Tasmania

SOURCES = list(NGIS_STATES) + ["GA"]

# The ABSUC column order, straight from ABSUC_sample.csv's header.
ABSUC_COLUMNS = [
    "OBJECTID", "GA_GUID", "GA_BOREHOLE_GUID", "GA_ENO", "UWI", "BOREHOLE_NAME",
    "GDA94_LATITUDE", "GDA94_LONGITUDE", "GL_AHD_M", "WD_AHD_M", "SRTM_HE_AHD_M",
    "DATUM_ELEVATION_AHD_M", "DATUM_NAME", "TD_MD_M", "SOURCE_UNIT", "GA_UNIT",
    "GA_ASUD_NO", "TOP_MD_M", "BASE_MD_M", "TOP_AHD_M", "BASE_AHD_M", "Z_AHD_M",
    "DATA_SOURCE", "GA_HYDROSTRAT", "HIERARCHY", "TAG", "Z_M_SRTM", "PREFERRED",
    "COMMENT",
]


def collect(source: str):
    """Return a stratigraphy-loaded BoreholeCollection for a source.

    ``source`` is an NGIS state (NSW/VIC/QLD) or "GA" for the national WFS.
    """
    if source == "GA":
        bores = GADataClient().boreholes(bbox=AUS_NATIONAL_BBOX)
    else:
        bores = NGISClient().boreholes(source, bbox=get_source(source).extent)
    bores.load_logs("stratigraphy")
    return bores


def _finite(x):
    """True if x is a usable number (not None and not NaN).

    GA's per-interval AHD fields arrive as NaN floats rather than None, so a
    bare ``is not None`` check would treat them as present.
    """
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def _ahd(elev, ref, depth):
    """Per-interval AHD elevation, or reference elevation minus depth as fallback."""
    if _finite(elev):
        return elev
    if _finite(ref) and _finite(depth):
        return ref - depth
    return None


def _blank_nan(v):
    """Render missing values (None or NaN) as an empty cell, like the ABSUC sample."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return v


def _total_depth(b):
    """Total measured depth: the bore's logged depth, else the deepest interval."""
    if b.bore_depth_m is not None:
        return b.bore_depth_m
    if b.drilled_depth_m is not None:
        return b.drilled_depth_m
    bottoms = [iv.bottom_depth for iv in b.stratigraphy if iv.bottom_depth is not None]
    return max(bottoms) if bottoms else None


def absuc_rows(bores):
    """Yield one ABSUC row dict per stratigraphy interval in the collection.

    Source-agnostic: each ABSUC field is filled from whatever the bore/interval
    carries (GA populates ENO and the ASUD unit PID; NGIS populates per-interval
    AHD), and stays blank where that source has no equivalent.
    """
    oid = 0
    for b in bores:
        ref = b.elevation_m  # ground/datum AHD elevation (GA elevation_m / NGIS RefElev)
        total_depth = _total_depth(b)
        for iv in b.stratigraphy:
            oid += 1
            datum_elev = iv.ref_elevation_m_ahd if iv.ref_elevation_m_ahd is not None else ref
            top_ahd = _ahd(iv.top_elev_m_ahd, iv.ref_elevation_m_ahd, iv.top_depth)
            base_ahd = _ahd(iv.bottom_elev_m_ahd, iv.ref_elevation_m_ahd, iv.bottom_depth)
            yield {
                "OBJECTID": oid,
                "GA_GUID": None,                 # no per-interval GUID in austrata
                "GA_BOREHOLE_GUID": None,        # no borehole GUID in austrata
                "GA_ENO": b.eno,                 # populated on GA, blank on NGIS
                "UWI": b.identifier or b.eno,    # PID (GA) / HydroCode (NGIS)
                "BOREHOLE_NAME": iv.borehole_name or b.name,
                "GDA94_LATITUDE": b.latitude,
                "GDA94_LONGITUDE": b.longitude,
                "GL_AHD_M": ref,
                "WD_AHD_M": None,
                "SRTM_HE_AHD_M": None,
                "DATUM_ELEVATION_AHD_M": datum_elev,
                "DATUM_NAME": b.depth_reference,
                "TD_MD_M": total_depth,
                "SOURCE_UNIT": iv.unit,
                "GA_UNIT": iv.unit,
                "GA_ASUD_NO": iv.unit_pid,       # ASUD unit PID on GA, blank on NGIS
                "TOP_MD_M": iv.top_depth,
                "BASE_MD_M": iv.bottom_depth,
                "TOP_AHD_M": top_ahd,
                "BASE_AHD_M": base_ahd,
                "Z_AHD_M": top_ahd,
                "DATA_SOURCE": b.source,
                "GA_HYDROSTRAT": None,
                "HIERARCHY": None,
                "TAG": None,
                "Z_M_SRTM": None,
                "PREFERRED": None,
                "COMMENT": iv.comment,
            }


def export(source: str) -> Path:
    """Collect a source's stratigraphy and write its ABSUC CSV."""
    bores = collect(source)
    path = OUT / f"{source.lower()}_strata.csv"
    n = 0
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ABSUC_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in absuc_rows(bores):
            writer.writerow({k: _blank_nan(v) for k, v in row.items()})
            n += 1
    print(f"  {source}: {len(bores)} bores -> {n} interval rows -> {path.name}")
    return path


def main() -> None:
    sources = [s.upper() for s in sys.argv[1:]] or list(NGIS_STATES)
    unknown = [s for s in sources if s not in SOURCES]
    if unknown:
        raise SystemExit(f"unknown source {unknown}; choose from {SOURCES}")

    print(f"Exporting ABSUC stratigraphy for: {', '.join(sources)}")
    for source in sources:
        export(source)


if __name__ == "__main__":
    main()
