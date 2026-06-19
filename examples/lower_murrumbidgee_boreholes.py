"""Find Geoscience Australia boreholes over the Lower Murrumbidgee MODFLOW domain.

The OMEGA mesh for this demo lives in a *local metric frame* (metres, arbitrary
(0,0) origin) — see omega/demos/lower_murrumbidgee/full_modflow_domain.md. austrata
queries in lon/lat (EPSG:4283), so we first georeference the local frame to
geographic using the fit documented in that note, then ask austrata how many
boreholes fall in the region.

Two regions are reported:
  * the FULL 330 x 210 km grid frame (the region we want data for, so the mesh
    terrain transform isn't extrapolating), and
  * the active alluvium outline inside it (the original 7-vertex demo polygon).

Georeference (from full_modflow_domain.md, approximate — a few km of error):
  SW corner of the full grid = 143.01 deg E, -35.76 deg (lat), at local (0, 0);
  scale ~91,800 m per degree longitude, ~110,170 m per degree latitude.

Run:  ~/Workplace/python3.12/bin/python3.12 examples/lower_murrumbidgee_boreholes.py
"""
from __future__ import annotations

from shapely.geometry import Polygon

from austrata import GADataClient

# --- georeference: local full-grid frame (metres) -> lon/lat (EPSG:4283) ------
ORIGIN_LON = 143.01       # deg E at full-grid local X = 0 (SW corner)
ORIGIN_LAT = -35.76       # deg   at full-grid local Y = 0 (SW corner)
M_PER_DEG_LON = 91_800.0  # fitted in the note (consistent with ~35 deg S)
M_PER_DEG_LAT = 110_170.0


def to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Map a full-grid-frame point (metres east/north of SW corner) to lon/lat."""
    return (ORIGIN_LON + x / M_PER_DEG_LON, ORIGIN_LAT + y / M_PER_DEG_LAT)


# Full 330 x 210 km MODFLOW grid frame, origin at SW corner (local metres).
FULL_GRID_LOCAL = [(0, 0), (330_000, 0), (330_000, 210_000), (0, 210_000)]

# The active alluvium outline, in ITS OWN local frame (the original demo polygon).
# It sits inside the full grid shifted by (+45,000 m E, +73,800 m N).
ACTIVE_LOCAL = [
    (0, 35_000), (140_000, 0), (280_000, 0), (280_000, 68_000),
    (201_000, 130_000), (121_000, 130_000), (0, 100_000),
]
ACTIVE_OFFSET = (45_000, 73_800)  # active-frame -> full-grid-frame


def build_regions() -> tuple[Polygon, Polygon]:
    full = Polygon([to_lonlat(x, y) for x, y in FULL_GRID_LOCAL])
    ox, oy = ACTIVE_OFFSET
    active = Polygon([to_lonlat(x + ox, y + oy) for x, y in ACTIVE_LOCAL])
    return full, active


def main() -> None:
    full_poly, active_poly = build_regions()
    wlon, slat, elon, nlat = full_poly.bounds
    print("Full MODFLOW grid frame georeferenced to lon/lat (EPSG:4283):")
    print(f"  longitude {wlon:.3f} .. {elon:.3f} E   latitude {slat:.3f} .. {nlat:.3f}")
    print("  (note's reference box: 143.01..146.60 E, -35.76..-33.85)\n")

    ga = GADataClient()

    # Cheap dry-run count first (WFS resultType=hits) over the full grid.
    # The full grid is a rectangle, so its bounding box == the polygon: the
    # server-side BBOX filter returns exactly the domain.
    n_full = ga.boreholes(region=full_poly, count_only=True)
    print(f"Boreholes in the FULL 330 x 210 km grid frame: {n_full}")

    # Pull the headers once (paginated + cached), then clip to the active outline.
    # austrata's spatial filter uses the polygon's bounding box, so we clip the
    # active-alluvium count exactly with a shapely 'within' test locally.
    bores = ga.boreholes(region=full_poly)
    gdf = bores.to_geodataframe()
    inside_active = gdf[gdf.within(active_poly)]
    print(f"  of which inside the active alluvium outline: {len(inside_active)}")

    print(f"\nFetched {len(bores)} borehole headers (CRS {gdf.crs}).")
    if len(bores):
        b = bores[0]
        print(f"  e.g. ENO {b.eno}  {b.name!r}  at ({b.longitude:.4f}, {b.latitude:.4f})")
    print("\n" + bores.citation())


if __name__ == "__main__":
    main()
