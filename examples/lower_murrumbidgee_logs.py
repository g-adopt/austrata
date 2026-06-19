"""Pull and summarise the downhole logs for the Lower Murrumbidgee domain.

Builds on lower_murrumbidgee_boreholes.py: georeferences the OMEGA mesh frame to
lon/lat, fetches the GA borehole headers in the full MODFLOW grid, then loads the
stratigraphy and earth-material logs and summarises:
  * how many boreholes actually carry logs,
  * how many intervals ("layers") we see,
  * which stratigraphic units / lithologies appear (and how often),
  * the depth coverage, and
  * which fields are populated (so we know what info is usable for interpolation).

Run:  ~/Workplace/python3.12/bin/python3.12 examples/lower_murrumbidgee_logs.py
"""
from __future__ import annotations

from collections import Counter
from dataclasses import fields

from austrata import GADataClient
from lower_murrumbidgee_boreholes import build_regions


def fill_rates(intervals: list, ignore=("valid", "invalid_reason")) -> list[tuple[str, float]]:
    """Fraction of intervals with a non-null value for each dataclass field."""
    if not intervals:
        return []
    names = [f.name for f in fields(intervals[0]) if f.name not in ignore]
    out = []
    for name in names:
        n = sum(1 for iv in intervals if getattr(iv, name) not in (None, ""))
        out.append((name, n / len(intervals)))
    return out


def depth_span(intervals: list) -> tuple[float, float]:
    tops = [iv.top_depth for iv in intervals if iv.top_depth is not None]
    bots = [iv.bottom_depth for iv in intervals if iv.bottom_depth is not None]
    return (min(tops) if tops else float("nan"), max(bots) if bots else float("nan"))


def summarise(label: str, per_hole: dict, unit_attr: str) -> None:
    all_iv = [iv for ivs in per_hole.values() for iv in ivs]
    valid = [iv for iv in all_iv if iv.valid]
    holes_with = sum(1 for ivs in per_hole.values() if ivs)
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    print(f"boreholes with {label.lower()}: {holes_with} / {len(per_hole)}")
    print(f"intervals (layers): {len(all_iv)}  (valid: {len(valid)}, "
          f"flagged invalid: {len(all_iv) - len(valid)})")
    if not all_iv:
        return
    top, bot = depth_span(all_iv)
    per_counts = [len(ivs) for ivs in per_hole.values() if ivs]
    print(f"depth coverage: {top:.1f} - {bot:.1f} m below reference point")
    print(f"intervals per logged hole: min {min(per_counts)}, "
          f"max {max(per_counts)}, mean {sum(per_counts)/len(per_counts):.1f}")

    units = Counter(getattr(iv, unit_attr) or "(unspecified)" for iv in all_iv)
    print(f"\ndistinct {unit_attr} values: {len(units)}")
    for name, n in units.most_common(15):
        print(f"  {n:5d}  {name}")

    print("\nfield availability (fraction of intervals populated):")
    for name, frac in fill_rates(all_iv):
        bar = "#" * int(round(frac * 20))
        print(f"  {name:24s} {frac*100:5.1f}%  {bar}")


def main() -> None:
    full_poly, _active = build_regions()
    ga = GADataClient()

    bores = ga.boreholes(region=full_poly)
    print(f"Fetched {len(bores)} borehole headers in the full MODFLOW grid.")

    bores.load_logs("stratigraphy")
    bores.load_logs("earth_material")

    strat = {b.eno: list(b.stratigraphy) for b in bores}
    earth = {b.eno: list(b.earth_material) for b in bores}

    summarise("Stratigraphy logs", strat, unit_attr="unit")
    summarise("Earth-material logs", earth, unit_attr="lithology")

    # Geological provinces seen (context for the model area).
    provs = Counter(
        iv.geological_province
        for ivs in strat.values() for iv in ivs
        if iv.geological_province
    )
    if provs:
        print(f"\n{'=' * 70}\nGeological provinces represented\n{'=' * 70}")
        for name, n in provs.most_common():
            print(f"  {n:5d}  {name}")

    print("\n" + bores.citation())


if __name__ == "__main__":
    main()
