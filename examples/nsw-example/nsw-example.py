"""Pull all NSW groundwater data from the NGIS state core.

The NSW core is the dense in-package source: every borehole the Bureau of
Meteorology holds for the state, plus its downhole stratigraphy, earth-material
and construction logs (with per-interval AHD elevations the GA WFS lacks). We
query the whole state by handing NGISClient the registered NSW extent as the
box, then load each log kind and export tidy one-row-per-interval tables.

The first run downloads and optimises the NSW gdb once (a few minutes, several
hundred MB under AUSTRATA_NGIS_DIR); every run after that filters the cached fast
DB in memory, offline.

Run:  ~/Workplace/python3.12/bin/python3.12 examples/nsw-example/nsw-example.py
"""
from __future__ import annotations

from pathlib import Path

from austrata import NGISClient
from austrata.infrastructure.ngis_sources import get_source

OUT = Path(__file__).parent

# The full NSW geographic extent (EPSG:4283), straight from the pinned registry.
NSW_BBOX = get_source("NSW").extent


def main() -> None:
    ngis = NGISClient()

    # Every NSW bore (the box is the whole state, so nothing is clipped out).
    bores = ngis.boreholes("NSW", bbox=NSW_BBOX)
    print(f"NSW NGIS boreholes: {len(bores)}")

    bores.to_geodataframe().to_file(OUT / "nsw_boreholes.gpkg", driver="GPKG")

    # Load and export each downhole log kind as a tidy interval table.
    exporters = {
        "stratigraphy": bores.stratigraphy_geodataframe,
        "earth_material": bores.earth_material_geodataframe,
        "construction": bores.construction_geodataframe,
    }
    for kind, export in exporters.items():
        bores.load_logs(kind)
        gdf = export()
        path = OUT / f"nsw_{kind}.gpkg"
        gdf.to_file(path, driver="GPKG")
        print(f"  {kind}: {len(gdf)} interval rows -> {path.name}")

    print("\n" + bores.citation())


if __name__ == "__main__":
    main()
