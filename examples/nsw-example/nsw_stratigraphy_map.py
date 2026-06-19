"""Map every NSW bore that carries stratigraphy logs.

Plots the ~10k NSW NGIS bores with named stratigraphy over the faint backdrop of
all NSW bores, coloured by how many stratigraphy intervals each hole logs (a
proxy for how richly it profiles the section). Writes a PNG next to this script.

Run:  ~/Workplace/python3.12/bin/python3.12 examples/nsw-example/nsw_stratigraphy_map.py
"""
from __future__ import annotations

from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import pandas as pd

from austrata import NGISClient
from austrata.infrastructure.ngis_sources import get_source

OUT = Path(__file__).parent


def main() -> None:
    ngis = NGISClient()
    bores = ngis.boreholes("NSW", bbox=get_source("NSW").extent)
    bores.load_logs("stratigraphy")

    all_pts = bores.to_geodataframe()

    # Bores that actually carry a stratigraphy column, with their interval count.
    rows = [
        (b.longitude, b.latitude, len(b.stratigraphy))
        for b in bores if b.stratigraphy
    ]
    strat_pts = pd.DataFrame(rows, columns=["lon", "lat", "n_intervals"]).sort_values(
        "n_intervals"  # draw richest holes last (on top)
    )
    vmax = float(strat_pts["n_intervals"].quantile(0.98))
    print(f"NSW bores total: {len(all_pts)};  with stratigraphy: {len(strat_pts)}")

    proj = ccrs.PlateCarree()
    fig, ax = plt.subplots(figsize=(12, 11), subplot_kw={"projection": proj})
    ax.set_extent([112, 154, -44, -9], crs=proj)  # mainland Australia + Tasmania

    # Australia coastline + state/territory borders (Natural Earth, via cartopy).
    ax.add_feature(cfeature.LAND, facecolor="0.96")
    ax.add_feature(cfeature.OCEAN, facecolor="#dceaf2")
    ax.add_feature(cfeature.COASTLINE, linewidth=0.6, edgecolor="0.4")
    ax.add_feature(cfeature.STATES, linewidth=0.6, edgecolor="0.55")

    ax.scatter(all_pts.geometry.x, all_pts.geometry.y, transform=proj,
               s=1, color="0.7", alpha=0.4, zorder=2, label="all NSW bores")
    sc = ax.scatter(
        strat_pts["lon"], strat_pts["lat"], transform=proj,
        c=strat_pts["n_intervals"], cmap="viridis",
        s=8, vmax=vmax, zorder=3,
    )
    fig.colorbar(sc, ax=ax, shrink=0.5, label="stratigraphy intervals per bore")
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color="0.8")
    gl.top_labels = gl.right_labels = False
    ax.set_title(f"NSW NGIS bores with stratigraphy ({len(strat_pts):,} of {len(all_pts):,})")
    ax.legend(loc="lower left", markerscale=6, framealpha=0.9)

    png = OUT / "nsw_stratigraphy_map.png"
    fig.savefig(png, dpi=150, bbox_inches="tight")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
