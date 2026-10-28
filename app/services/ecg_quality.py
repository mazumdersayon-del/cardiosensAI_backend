"""Checks every ECG snapshot before it is stored (docs/device-protocol.md, "Snapshot rules").

Hard checks refuse the snapshot: wrong size, no signal at all, or limb leads that contradict each other. The
device derives III, aVR, aVL and aVF from I and II (Einthoven / Goldberger), so in a genuine recording they agree
to within rounding; random, shifted or corrupted data doesn't. Soft checks only grade quality: flat, saturated,
noisy or lead-off channels and a plausible heart rate. No diagnosis is made here.
"""

import sys
from array import array
from dataclasses import dataclass, field

from app.core.ecg_leads import STANDARD_12

FS = 500
SNAPSHOT_S = 20
FRAMES = FS * SNAPSHOT_S
NLEADS = len(STANDARD_12)
BYTES = FRAMES * NLEADS * 2
RAIL = 32767
# Derived limb leads may differ from the exact formula by rounding (half a count) and by clipping at the rails.
DERIVED_TOLERANCE = 2


class SnapshotRejected(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Quality:
    quality: str
    average_hr: int | None
    detail: dict = field(default_factory=dict)


def _lead(samples: array, i: int) -> array:
    return samples[i::NLEADS]


def _leads(raw: bytes) -> dict[str, array]:
    samples = array("h")
    samples.frombytes(raw)
    if samples.itemsize != 2:  # pragma: no cover - every supported platform
        raise SnapshotRejected("wrong_format", "int16 samples expected")
    if sys.byteorder != "little":  # pragma: no cover
        samples.byteswap()
    return {name: _lead(samples, i) for i, name in enumerate(STANDARD_12)}


def _check_derived(leads: dict[str, array], frames: int) -> None:
    """Einthoven / Goldberger: III = II - I, aVR = -(I + II) / 2, aVL = I - II / 2, aVF = II - I / 2."""
    one, two = leads["I"], leads["II"]
    derived = {
        "III": lambda a, b: b - a,
        "aVR": lambda a, b: -(a + b) / 2,
        "aVL": lambda a, b: a - b / 2,
        "aVF": lambda a, b: b - a / 2,
    }
    allowed = frames // 200  # 0.5 %
    for name, f in derived.items():
        got = leads[name]
        bad = 0
        for k in range(frames):
            a, b, g = one[k], two[k], got[k]
            if abs(g - f(a, b)) > DERIVED_TOLERANCE and RAIL not in (abs(a), abs(b), abs(g)) and -RAIL - 1 not in (a, b, g):
                bad += 1
                if bad > allowed:
                    raise SnapshotRejected("inconsistent_leads", f"Lead {name} doesn't match leads I and II: the data is corrupted")


def check_live_chunk(raw: bytes) -> None:
    """A live-ECG chunk (whole 12-lead int16 frames) must pass the same lead-consistency check as a snapshot."""
    frames = len(raw) // (2 * NLEADS)
    _check_derived(_leads(raw), frames)


def check_snapshot(raw: bytes, lead_off: list[str] | None = None) -> Quality:
    if len(raw) != BYTES:
        raise SnapshotRejected("wrong_length", f"A snapshot is exactly {FRAMES} frames x {NLEADS} leads of int16 ({BYTES} bytes)")
    leads = _leads(raw)

    ranges = {name: max(v) - min(v) for name, v in leads.items()}
    if all(r < 20 for r in ranges.values()):
        raise SnapshotRejected("no_signal", "Every lead is flat: no ECG signal")
    _check_derived(leads, FRAMES)

    off = sorted(set(lead_off or []) & set(STANDARD_12))
    flat = [n for n, r in ranges.items() if r < 50 and n not in off]
    saturated = [n for n, v in leads.items() if sum(1 for x in v if x >= RAIL or x <= -RAIL) > FRAMES // 100]
    noisy = []
    for n, v in leads.items():
        if n in off or n in flat:
            continue
        diff_rms = (sum((v[k] - v[k - 1]) ** 2 for k in range(1, FRAMES)) / (FRAMES - 1)) ** 0.5
        if diff_rms > 150:  # µV per 2 ms: muscle noise or mains hum, not ECG
            noisy.append(n)

    hr = _heart_rate(leads["II"] if "II" not in off and "II" not in flat else leads["I"])
    problems = set(off) | set(flat) | set(saturated) | set(noisy)
    limb_bad = problems & {"I", "II"}
    if hr is None or limb_bad or len(problems) > 4:
        grade = "poor"
    elif problems:
        grade = "acceptable"
    else:
        grade = "good"
    detail = {k: v for k, v in (("leadOff", off), ("flat", flat), ("saturated", saturated), ("noisy", noisy)) if v}
    return Quality(quality=grade, average_hr=hr, detail=detail)


def _heart_rate(v: array) -> int | None:
    """R peaks on the slope of one lead: crude, only to grade quality and show a number, never for diagnosis."""
    slope = [abs(v[k + 1] - v[k - 1]) for k in range(1, FRAMES - 1)]
    top = sorted(slope)[int(len(slope) * 0.995)]
    if top < 20:
        return None
    threshold = top * 0.5
    refractory = int(0.25 * FS)
    peaks, last = [], -refractory
    for k, s in enumerate(slope):
        if s >= threshold and k - last >= refractory:
            peaks.append(k)
            last = k
    if len(peaks) < 3:
        return None
    rr = [(b - a) / FS for a, b in zip(peaks, peaks[1:], strict=False)]
    rr.sort()
    median = rr[len(rr) // 2]
    hr = round(60 / median)
    return hr if 25 <= hr <= 250 else None
