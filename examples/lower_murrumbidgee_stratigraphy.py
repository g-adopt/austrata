"""The 5 boreholes with stratigraphy logs in the Lower Murrumbidgee domain.

Prints each hole's downhole sequence and exports a tidy table (one row per
interval) to GeoPackage + CSV using austrata's public log-export helper,
``BoreholeCollection.stratigraphy_geodataframe()``.

Run:  ~/Workplace/python3.12/bin/python3.12 examples/lower_murrumbidgee_stratigraphy.py
"""
from __future__ import annotations

from pathlib import Path

from austrata import GADataClient
from lower_murrumbidgee_boreholes import build_regions

OUT = Path(__file__).parent


def main() -> None:
    full_poly, _ = build_regions()
    ga = GADataClient()
    bores = ga.boreholes(region=full_poly)
    bores.load_logs("stratigraphy")

    logged = [b for b in bores if b.stratigraphy]
    print(f"{len(logged)} boreholes carry stratigraphy logs.\n")

    for b in logged:
        ivs = sorted(b.stratigraphy, key=lambda iv: (iv.top_depth or 0.0))
        total = max((iv.bottom_depth or 0.0) for iv in ivs)
        print(f"ENO {b.eno}  {b.name!r}  ({b.longitude:.3f}, {b.latitude:.3f})  "
              f"ground {b.elevation_m} m AHD  ->  {len(ivs)} intervals to {total:.0f} m")
        for iv in ivs:
            age = f"  age: {iv.older_age}" if iv.older_age else ""
            print(f"   {iv.top_depth:7.1f} - {iv.bottom_depth:7.1f} m   "
                  f"{iv.unit or '(unspecified)'}{age}")
        print()

    # One row per interval across the whole collection, geometry = borehole point.
    gdf = bores.stratigraphy_geodataframe()
    csv_path = OUT / "lower_murrumbidgee_stratigraphy.csv"
    gpkg_path = OUT / "lower_murrumbidgee_stratigraphy.gpkg"
    gdf.drop(columns="geometry").to_csv(csv_path, index=False)
    gdf.to_file(gpkg_path, driver="GPKG")
    print(f"Wrote {len(gdf)} interval rows:\n  {csv_path}\n  {gpkg_path}")
    print("\n" + bores.citation())


if __name__ == "__main__":
    main()
