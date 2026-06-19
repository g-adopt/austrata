"""Detailed look at the earth-material logs for the Lower Murrumbidgee domain.

Run:  ~/Workplace/python3.12/bin/python3.12 examples/lower_murrumbidgee_earth_material.py
"""
from __future__ import annotations

from collections import Counter

from austrata import GADataClient
from lower_murrumbidgee_boreholes import build_regions


def main() -> None:
    full_poly, _ = build_regions()
    ga = GADataClient()
    bores = ga.boreholes(region=full_poly)
    bores.load_logs("earth_material")

    em = [(b, list(b.earth_material)) for b in bores]
    em = [(b, ivs) for b, ivs in em if ivs]
    all_iv = [iv for _, ivs in em for iv in ivs]
    print(f"{len(em)} boreholes carry earth-material logs; {len(all_iv)} intervals total.\n")

    print("lithology_group:")
    for g, n in Counter(iv.lithology_group or "(none)" for iv in all_iv).most_common():
        print(f"  {n:4d}  {g}")

    print("\nlithology:")
    for lith, n in Counter(iv.lithology or "(none)" for iv in all_iv).most_common():
        print(f"  {n:4d}  {lith}")

    print("\nlithology_qualifier:")
    for q, n in Counter(iv.lithology_qualifier or "(none)" for iv in all_iv).most_common():
        print(f"  {n:4d}  {q}")

    print("\ndescription values:")
    for d, n in Counter(iv.description or "(none)" for iv in all_iv).most_common(15):
        print(f"  {n:4d}  {d}")

    # Depth distribution per lithology (where does each material occur?).
    print("\ndepth range (m) by lithology:")
    by_lith: dict[str, list[float]] = {}
    for iv in all_iv:
        if iv.top_depth is None or iv.bottom_depth is None:
            continue
        by_lith.setdefault(iv.lithology or "(none)", []).extend([iv.top_depth, iv.bottom_depth])
    for lith, ds in sorted(by_lith.items(), key=lambda kv: -len(kv[1])):
        print(f"  {lith:20s} {min(ds):7.1f} - {max(ds):7.1f}")

    # A few example holes, shown as a downhole sequence.
    print("\nexample downhole sequences (deepest-logged holes):")
    for b, ivs in sorted(em, key=lambda bi: -len(bi[1]))[:4]:
        ivs = sorted(ivs, key=lambda iv: (iv.top_depth if iv.top_depth is not None else 0))
        print(f"\n  ENO {b.eno}  {b.name!r}  ({b.longitude:.3f}, {b.latitude:.3f})  "
              f"ref {b.elevation_m} m")
        for iv in ivs:
            desc = f"  [{iv.description}]" if iv.description else ""
            print(f"    {iv.top_depth:7.1f} - {iv.bottom_depth:7.1f} m  "
                  f"{iv.lithology_group}/{iv.lithology}{desc}")

    print("\n" + bores.citation())


if __name__ == "__main__":
    main()
