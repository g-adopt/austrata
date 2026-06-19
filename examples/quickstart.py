"""austrata quickstart — a tour of what the package can do.

Run it directly to see each feature against the live Geoscience Australia
services::

    python examples/quickstart.py

Every network call is guarded under ``if __name__ == "__main__"``, so importing
this module does nothing. A small ACT/Canberra bounding box and a small polygon
keep the pulls fast and polite. Each section is labelled and prints what it got.
"""
from __future__ import annotations

import tempfile

from shapely.geometry import Polygon

from austrata import GADataClient, hydrogeology_citation

# Reference region (ACT/Canberra), lon/lat in GDA94 (EPSG:4283).
ACT_BBOX = (148.9, -35.6, 149.3, -35.1)
# A small Murray Basin bbox around boreholes that carry downhole logs.
LOGGED_BBOX = (140.68, -34.49, 140.71, -34.46)
# An arbitrary (non-rectangular) region: a triangle inside the ACT box.
ACT_TRIANGLE = Polygon([(148.95, -35.55), (149.25, -35.55), (149.10, -35.15)])


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    # ------------------------------------------------------------------
    section("1. Construct the client")
    # cache_dir defaults to the OS user cache dir (override with cache_dir= or
    # the AUSTRATA_DATA_DIR env var). offline=True serves only from cache. Here we
    # use a throwaway dir so the demo never touches your real cache.
    ga = GADataClient(cache_dir=tempfile.mkdtemp(prefix="austrata-demo-"))
    print(f"client ready; cache at {ga.cache.cache_dir}")

    # ------------------------------------------------------------------
    section("2. count_only dry-run — how many boreholes before pulling")
    n = ga.boreholes(bbox=ACT_BBOX, count_only=True)
    print(f"{n} boreholes intersect the ACT bbox (no features downloaded yet)")

    # ------------------------------------------------------------------
    section("3. Boreholes by bbox -> BoreholeCollection")
    bores = ga.boreholes(bbox=ACT_BBOX)
    print(f"fetched {len(bores)} boreholes")
    first = bores[0]
    print(f"first: eno={first.eno} name={first.name!r} "
          f"lon/lat=({first.longitude}, {first.latitude}) state={first.state}")
    gdf = bores.to_geodataframe()
    print(f"to_geodataframe(): {len(gdf)} rows, CRS {gdf.crs}, "
          f"columns include {list(gdf.columns)[:5]}...")

    # ------------------------------------------------------------------
    section("4. Boreholes by an arbitrary shapely polygon region")
    tri = ga.boreholes(region=ACT_TRIANGLE)
    print(f"{len(tri)} boreholes for the triangle region "
          f"(WFS filters by the polygon's bounding box)")

    # ------------------------------------------------------------------
    section("5. A single borehole by ENO")
    one = ga.borehole("35147")
    if one is not None:
        print(f"borehole 35147: name={one.name!r} province={one.province} "
              f"elevation_m={one.elevation_m}")

    # ------------------------------------------------------------------
    section("6. Downhole logs: stratigraphy and earth material")
    logged = ga.boreholes(bbox=LOGGED_BBOX)
    print(f"{len(logged)} boreholes in the logged bbox")
    logged.load_logs("stratigraphy")
    logged.load_logs("earth_material")
    for b in logged:
        if b.stratigraphy:
            iv = b.stratigraphy[0]
            print(f"  {b.name} stratigraphy[0]: {iv.top_depth}-{iv.bottom_depth} m "
                  f"unit={iv.unit!r} valid={iv.valid}")
        if b.earth_material:
            em = b.earth_material[0]
            print(f"  {b.name} earth_material[0]: {em.top_depth}-{em.bottom_depth} m "
                  f"lithology={em.lithology!r} valid={em.valid}")
    total_strat = sum(len(b.stratigraphy) for b in logged)
    print(f"total stratigraphy intervals loaded: {total_strat} "
          "(check the .valid flag before using a row)")

    # ------------------------------------------------------------------
    section("7. Hydrogeology polygons by bbox -> GeoDataFrame")
    hydro = ga.hydrogeology(bbox=ACT_BBOX)
    print(f"{len(hydro)} hydrogeology polygons, CRS {hydro.crs}")
    print(f"columns: {list(hydro.columns)}")
    if len(hydro):
        row = hydro.iloc[0]
        print(f"first polygon: feature={row.get('feature')!r} "
              f"type={row.get('type')!r} geom={row.geometry.geom_type}")

    # ------------------------------------------------------------------
    section("8. Cache reuse and offline mode")
    again = ga.boreholes(bbox=ACT_BBOX)  # revalidated, served from cache
    print(f"repeat ACT query returned {len(again)} (served from cache, no re-pull)")
    info = ga.cache.info()
    print(f"cache now holds {info['entry_count']} entries, {info['total_bytes']} bytes")
    # An offline client over the same cache dir serves cached queries and raises
    # for anything not already cached.
    offline = GADataClient(cache_dir=ga.cache.cache_dir, offline=True)
    print(f"offline client: ACT boreholes still available -> "
          f"{len(offline.boreholes(bbox=ACT_BBOX))}")

    # ------------------------------------------------------------------
    section("9. Provenance and citation")
    print("boreholes:", bores.citation())
    print("hydrogeology:", hydrogeology_citation(hydro))


if __name__ == "__main__":
    main()
