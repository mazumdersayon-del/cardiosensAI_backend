"""Rebuild app/data/in_pincodes.csv.gz (PIN code → location, district, state) for "Near me".

Sources:
  --geonames IN.txt     GeoNames postal codes for India (https://download.geonames.org/export/zip/IN.zip, CC BY 4.0).
                        This is what ships: free to use with credit, but positions are coarse (often district level).
  --india-post FILE.csv India Post "All India Pincode Directory" with latitude/longitude (data.gov.in): columns
                        pincode, latitude, longitude, district, statename, officename. Check its licence first.

Each PIN gets the median position of its post offices. Run: python -m app.scripts.build_pincodes --geonames IN.txt
"""

import argparse
import collections
import csv
import gzip
import re
import statistics
from pathlib import Path

from app.services.pincodes import DATA

PIN = re.compile(r"^[1-9]\d{5}$")


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_geonames(path: Path) -> dict[str, list[tuple]]:
    rows: dict[str, list[tuple]] = collections.defaultdict(list)
    for line in path.open(encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        if len(f) > 10 and PIN.match(f[1]):
            rows[f[1]].append((_num(f[9]), _num(f[10]), f[5], f[3], f[7] or f[2]))
    return rows


def read_india_post(path: Path) -> dict[str, list[tuple]]:
    rows: dict[str, list[tuple]] = collections.defaultdict(list)
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items()}
            pin = r.get("pincode", "")
            if PIN.match(pin):
                rows[pin].append((_num(r.get("latitude")), _num(r.get("longitude")), r.get("district", "").title(),
                                  r.get("statename", "").title(), r.get("officename", "")))  # fmt: skip
    return rows


def write(rows: dict[str, list[tuple]], out: Path) -> int:
    def common(vals: list[str]) -> str:
        vals = [v for v in vals if v]
        return collections.Counter(vals).most_common(1)[0][0] if vals else ""

    n = 0
    with gzip.open(out, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pin", "lat", "lon", "district", "state", "place"])
        for pin in sorted(rows):
            r = rows[pin]
            pts = [(a, b) for a, b, *_ in r if a is not None and b is not None and 6 <= a <= 37.5 and 68 <= b <= 97.5]
            if not pts:
                continue
            lat = round(statistics.median(p[0] for p in pts), 4)
            lon = round(statistics.median(p[1] for p in pts), 4)
            w.writerow([pin, lat, lon, common([x[2] for x in r]), common([x[3] for x in r]), common([x[4] for x in r])])
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--geonames", type=Path)
    g.add_argument("--india-post", type=Path)
    ap.add_argument("--out", type=Path, default=DATA)
    a = ap.parse_args()
    rows = read_geonames(a.geonames) if a.geonames else read_india_post(a.india_post)
    print(f"{write(rows, a.out)} PIN codes written to {a.out}")


if __name__ == "__main__":
    main()
