"""gadata quickstart — boreholes, logs, hydrogeology, counts, caching, citation.

Runnable end to end against the live Geoscience Australia services. It only
touches the network when executed directly (``python examples/quickstart.py``),
so importing this module is side-effect free. A small ACT/Canberra bounding box
keeps the pulls fast.
"""
from __future__ import annotations

import tempfile

from gadata import GADataClient, hydrogeology_citation

# Small ACT/Canberra bounding box, lon/lat (GDA94).
ACT_BBOX = (148.9, -35.6, 149.3, -35.1)
# Murray Basin bbox around boreholes that carry stratigraphy logs.
LOGGED_BBOX = (140.68, -34.49, 140.71, -34.46)


def main() -> None:
    # Use a throwaway cache dir so the demo never pollutes the user cache.
    ga = GADataClient(cache_dir=tempfile.mkdtemp(prefix="gadata-quickstart-"))

    # 1. Dry-run counts first (cheap; no full download).
    n_bores = ga.boreholes(bbox=ACT_BBOX, count_only=True)
    n_units = ga.hydrogeology(bbox=ACT_BBOX, count_only=True)
    print(f"ACT bbox: {n_bores} boreholes, {n_units} hydrogeology units")

    # 2. Boreholes in the region (paginated + cached).
    bores = ga.boreholes(bbox=ACT_BBOX)
    print(f"Fetched {len(bores)} borehole headers")
    headers = bores.to_geodataframe()
    print(f"Header GeoDataFrame: {len(headers)} rows, CRS {headers.crs}")

    # 3. A repeat call is served from the cache (revalidated, not refetched).
    again = ga.boreholes(bbox=ACT_BBOX)
    print(f"Repeat call returned {len(again)} (served from cache)")

    # 4. Downhole logs for boreholes that actually have them.
    logged = ga.boreholes(bbox=LOGGED_BBOX)
    logged.load_logs("stratigraphy")
    total = sum(len(b.stratigraphy) for b in logged)
    print(f"Loaded {total} stratigraphy intervals across {len(logged)} boreholes")
    for b in logged:
        for iv in b.stratigraphy[:3]:
            print(f"  {b.name}: {iv.top_depth}-{iv.bottom_depth} m  {iv.unit}")

    # 5. Hydrogeology polygons to overlay.
    hydro = ga.hydrogeology(bbox=ACT_BBOX)
    print(f"Hydrogeology: {len(hydro)} polygons, CRS {hydro.crs}")

    # 6. Provenance / citation.
    print("Borehole citation:", bores.citation())
    print("Hydrogeology citation:", hydrogeology_citation(hydro))

    # 7. Cache inspection.
    info = ga.cache.info()
    print(f"Cache: {info['entry_count']} entries, {info['total_bytes']} bytes "
          f"at {info['cache_dir']}")


if __name__ == "__main__":
    main()
