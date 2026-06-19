"""Map bores carrying stratigraphy logs, for any source, over a map of Australia.

One reusable routine for all four sources. NGIS states (NSW/VIC/QLD) come from the
local state cores via NGISClient; GA comes from the national WFS via GADataClient
(all headers, then stratigraphy fetched by ENO). Each run draws the Australia
coastline + state borders (cartopy / Natural Earth) and overlays the source's
bores, colouring the ones with stratigraphy by interval count.

Run:  ~/Workplace/python3.12/bin/python3.12 examples/nsw-example/stratigraphy_map.py <SOURCE>
      where SOURCE is NSW | VIC | QLD | GA   (default NSW)
"""
from __future__ import annotations

import sys
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import pandas as pd

from austrata import GADataClient, NGISClient
from austrata.infrastructure.ngis_sources import get_source

OUT = Path(__file__).parent
AUS_NATIONAL_BBOX = (112.0, -44.0, 154.0, -9.0)  # mainland + Tasmania


def collect(source: str):
    """Return (all_points_gdf, strat_rows_df) for a source.

    strat_rows_df has columns lon, lat, n_intervals — one row per bore that
    carries at least one stratigraphy interval.
    """
    source = source.upper()
    if source == "GA":
        ga = GADataClient()
        bores = ga.boreholes(bbox=AUS_NATIONAL_BBOX)
    else:
        ngis = NGISClient()
        bores = ngis.boreholes(source, bbox=get_source(source).extent)

    all_pts = bores.to_geodataframe()
    bores.load_logs("stratigraphy")
    rows = [(b.longitude, b.latitude, len(b.stratigraphy)) for b in bores if b.stratigraphy]
    strat = pd.DataFrame(rows, columns=["lon", "lat", "n_intervals"]).sort_values("n_intervals")
    return all_pts, strat


def draw(source: str, all_pts, strat) -> Path:
    vmax = float(strat["n_intervals"].quantile(0.98)) if len(strat) else 1.0
    proj = ccrs.PlateCarree()
    fig, ax = plt.subplots(figsize=(12, 11), subplot_kw={"projection": proj})
    ax.set_extent([112, 154, -44, -9], crs=proj)

    ax.add_feature(cfeature.LAND, facecolor="0.96")
    ax.add_feature(cfeature.OCEAN, facecolor="#dceaf2")
    ax.add_feature(cfeature.COASTLINE, linewidth=0.6, edgecolor="0.4")
    ax.add_feature(cfeature.STATES, linewidth=0.6, edgecolor="0.55")

    ax.scatter(all_pts.geometry.x, all_pts.geometry.y, transform=proj,
               s=1, color="0.7", alpha=0.4, zorder=2, label=f"all {source} bores")
    if len(strat):
        sc = ax.scatter(strat["lon"], strat["lat"], transform=proj,
                        c=strat["n_intervals"], cmap="viridis", s=8, vmax=vmax, zorder=3)
        fig.colorbar(sc, ax=ax, shrink=0.5, label="stratigraphy intervals per bore")
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color="0.8")
    gl.top_labels = gl.right_labels = False
    ax.set_title(f"{source} bores with stratigraphy "
                 f"({len(strat):,} of {len(all_pts):,})")
    ax.legend(loc="lower left", markerscale=6, framealpha=0.9)

    png = OUT / f"{source.lower()}_stratigraphy_map.png"
    fig.savefig(png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return png


def main() -> None:
    source = (sys.argv[1] if len(sys.argv) > 1 else "NSW").upper()
    all_pts, strat = collect(source)
    print(f"{source} bores total: {len(all_pts)};  with stratigraphy: {len(strat)}")
    print(f"Wrote {draw(source, all_pts, strat)}")


if __name__ == "__main__":
    main()
