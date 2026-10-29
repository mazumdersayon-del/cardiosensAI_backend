"""Indian PIN codes → approximate location, district and state (app/data/in_pincodes.csv.gz).

Built from the GeoNames postal code dump for India (CC BY 4.0, credit: geonames.org): 19k PINs, the median of each
PIN's post offices. Some PINs only have a district-level position, so distances are approximate (a few km, at
worst ~25 km). Loaded once, on first use.
"""

import csv
import gzip
import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "in_pincodes.csv.gz"
PIN_RE = re.compile(r"^[1-9]\d{5}$")


@dataclass(frozen=True)
class Pin:
    pin: str
    lat: float
    lon: float
    district: str
    state: str
    place: str


@cache
def _table() -> dict[str, Pin]:
    with gzip.open(DATA, "rt", encoding="utf-8") as f:
        return {r["pin"]: Pin(r["pin"], float(r["lat"]), float(r["lon"]), r["district"], r["state"], r["place"]) for r in csv.DictReader(f)}


def clean(v: str | None) -> str | None:
    """'560 001' → '560001'; None for empty; ValueError if it isn't a 6-digit PIN."""
    if v is None or not v.strip():
        return None
    pin = re.sub(r"\s", "", v)
    if not PIN_RE.match(pin):
        raise ValueError("A PIN code has 6 digits and doesn't start with 0")
    return pin


def lookup(pin: str | None) -> Pin | None:
    return _table().get(pin) if pin else None


def km(a: Pin, b: Pin) -> float:
    """Great-circle distance in km."""
    la1, lo1, la2, lo2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))
