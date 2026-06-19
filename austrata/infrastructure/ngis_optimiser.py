"""``optimise_state`` — the optimiser tier of the NGIS two-stage pipeline.

Converts a whole state's ``.gdb`` (once) into the "fast DB": one GeoParquet-ready
GeoDataFrame per layer that every subsequent box query reads and filters in
memory. fiona is the only engine used — pyogrio raises ``GeometryError`` on these
gdbs (an exotic geometry-type flag), so ``geopandas.read_file(..., engine="fiona")``
and ``fiona.open(...)`` are the supported readers.

Policy: **verbatim, miss nothing.** The optimiser does *not* rename NGIS fields to
GA keys or coerce columns away — every original NGIS column is carried through. It
only (a) builds a bores frame with a lon/lat Point geometry from the ``Longitude``/
``Latitude`` attribute columns (the projected Albers ``SHAPE`` is discarded), and
(b) denormalises each parent bore's lon/lat + Point onto every log row by joining
on ``HydroCode`` — so each log frame is self-contained for later bbox filtering and
export. The curated NGIS-column → domain-field mapping lives elsewhere (the NGIS
mapper, alongside the client); this stage is pure transport.

"Unknown" formation rows are kept (flagged invalid downstream, never here). Log
rows whose ``HydroCode`` has no matching bore are kept too, with null lon/lat/
geometry — orphans are surfaced, not silently dropped.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from austrata.infrastructure.ngis_sources import get_source

logger = logging.getLogger("austrata.ngis")

#: Bump whenever this mapping/shape changes — it stamps the fast DB so a stale
#: cache (built by an older optimiser) is detected and rebuilt.
OPTIMISER_VERSION = 1

CRS_LONLAT = "EPSG:4283"

#: gdb layer name -> fast-DB key. The four borehole-data layers we read.
_LAYERS = {
    "NGIS_Bore": "bores",
    "NGIS_BoreholeLog": "stratigraphy",
    "NGIS_LithologyLog": "earth_material",
    "NGIS_ConstructionLog": "construction",
}

#: Attribute columns carrying lon/lat in the bores layer (EPSG:4283).
_LON, _LAT = "Longitude", "Latitude"
#: Join key shared by the bore and every log row.
_JOIN = "HydroCode"


def build_stamp(state: str) -> dict:
    """The fast-DB provenance/consistency stamp for ``state``.

    ``gdb_md5`` is the registry's pinned ``zip_md5`` — the artifact identity the
    application layer compares against to decide whether a cached fast DB is still
    trustworthy. Paired with ``optimiser_version``, it gates a rebuild.
    """
    source = get_source(state)
    return {
        "state": source.state,
        "vintage": source.vintage,
        # The registry's pinned ZIP md5 — the *artifact identity* of the source
        # download. NOT a hash of the extracted .gdb (keep it this way: the §6
        # contract compares against the pinned download, not a re-hashed gdb).
        "gdb_md5": source.zip_md5,
        "optimiser_version": OPTIMISER_VERSION,
        "source": source.source,
        "citation": source.citation,
        "source_url": source.dataset_url,
    }


def optimise_state(gdb_path: os.PathLike | str, state: str) -> Dict[str, gpd.GeoDataFrame]:
    """Read a whole state's gdb into per-layer fast-DB GeoDataFrames.

    Returns ``{"bores", "stratigraphy", "earth_material", "construction"}`` (a key
    is omitted only if its layer is absent from the gdb). No bbox filtering here —
    that happens later on the cached frame.
    """
    source = get_source(state)
    path = str(gdb_path)
    frames: Dict[str, gpd.GeoDataFrame] = {}

    bores = _read_bores(path)
    frames["bores"] = bores
    points = _bore_point_lookup(bores)

    for layer, key in _LAYERS.items():
        if key == "bores":
            continue
        frame = _read_log(path, layer, points)
        if frame is not None:
            frames[key] = frame

    counts = ", ".join(f"{k}={len(v)}" for k, v in frames.items())
    logger.info("NGIS %s optimised (optimiser v%s): %s", source.state, OPTIMISER_VERSION, counts)
    return frames


# -- readers ------------------------------------------------------------


def _read_table(gdb_path: str, layer: str) -> Optional[pd.DataFrame]:
    """Read every row of ``layer`` verbatim as a plain (geometry-free) DataFrame.

    Uses the fiona engine (pyogrio raises on these gdbs); the projected Albers
    geometry is intentionally not read here — lon/lat comes from the attributes.
    """
    import fiona

    if layer not in set(fiona.listlayers(gdb_path)):
        logger.info("NGIS: layer %s absent; skipping", layer)
        return None
    with fiona.open(gdb_path, layer=layer) as src:
        records = [dict(f["properties"]) for f in src]
    return pd.DataFrame.from_records(records)


def _read_bores(gdb_path: str) -> gpd.GeoDataFrame:
    """Bores layer verbatim + a lon/lat Point geometry (Albers SHAPE discarded)."""
    df = _read_table(gdb_path, "NGIS_Bore")
    if df is None:
        raise RuntimeError(f"NGIS gdb {gdb_path!r} has no NGIS_Bore layer")
    geometry = [_point(row.get(_LON), row.get(_LAT)) for _, row in df.iterrows()]
    return gpd.GeoDataFrame(df, geometry=geometry, crs=CRS_LONLAT)


def _read_log(gdb_path: str, layer: str, points: dict) -> Optional[gpd.GeoDataFrame]:
    """One log layer verbatim, with the parent bore's lon/lat + Point denormalised.

    Joins on ``HydroCode``. Rows with no matching bore keep null lon/lat/geometry
    (orphans surfaced, not dropped). "Unknown" formation rows are untouched here.
    """
    df = _read_table(gdb_path, layer)
    if df is None:
        return None
    lons, lats, geoms = [], [], []
    for code in df.get(_JOIN, pd.Series([None] * len(df))):
        lon, lat = points.get(code, (None, None))
        lons.append(lon)
        lats.append(lat)
        geoms.append(_point(lon, lat))
    out = df.copy()
    # NGIS log layers carry no native Longitude/Latitude column, so assigning the
    # denormalised bore lon/lat here cannot clobber an original column (this keeps
    # miss-nothing intact even if a future schema sprouts such a field elsewhere).
    out[_LON] = lons
    out[_LAT] = lats
    return gpd.GeoDataFrame(out, geometry=geoms, crs=CRS_LONLAT)


# -- helpers ------------------------------------------------------------


def _bore_point_lookup(bores: gpd.GeoDataFrame) -> dict:
    """``HydroCode`` -> ``(lon, lat)`` from the bores frame, skipping null keys.

    Multi-pipe bores share one ``HydroCode`` at the same surface location, so this
    deliberately collapses such duplicates to a single point — fine for the log
    join (all pipes sit at that point); the bores frame itself still keeps every row.
    """
    lookup = {}
    for _, row in bores.iterrows():
        code = row.get(_JOIN)
        if pd.notna(code):
            lookup[code] = (row.get(_LON), row.get(_LAT))
    return lookup


def _point(lon: object, lat: object) -> Optional[Point]:
    """A shapely ``Point(lon, lat)``, or ``None`` when either is missing."""
    x, y = _coerce_coord(lon), _coerce_coord(lat)
    if x is None or y is None:
        return None
    return Point(x, y)


def _coerce_coord(value: object) -> Optional[float]:
    """Coerce a lon/lat attribute to ``float``; missing/NaN/garbage -> ``None``."""
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out
