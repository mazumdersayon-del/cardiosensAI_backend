"""Synthetic 12-lead ECG for demos and the live-stream simulator. NOT physiological, never health data.

Each beat is a sum of Gaussian waves (P, Q, R, S, T) with per-lead amplitudes roughly like a normal
12-lead tracing. The limb leads III, aVR, aVL and aVF are derived from I and II (Einthoven/Goldberger),
so the tracing is internally consistent. Output: interleaved int16 frames in microvolts.
"""

import math
import random
import struct

from app.core.ecg_leads import STANDARD_12

# (centre within beat in s, width in s) for P, Q, R, S, T
WAVES = ((0.10, 0.025), (0.205, 0.008), (0.23, 0.010), (0.255, 0.010), (0.45, 0.040))
# mV amplitudes for P, Q, R, S, T
AMPLITUDES = {
    "I": (0.10, -0.05, 0.90, -0.15, 0.25),
    "II": (0.15, -0.08, 1.20, -0.20, 0.35),
    "V1": (0.08, 0.00, 0.25, -1.00, -0.10),
    "V2": (0.08, 0.00, 0.45, -1.20, 0.45),
    "V3": (0.08, -0.03, 0.85, -0.80, 0.45),
    "V4": (0.08, -0.05, 1.40, -0.40, 0.40),
    "V5": (0.08, -0.06, 1.30, -0.20, 0.35),
    "V6": (0.08, -0.06, 1.00, -0.10, 0.30),
}


class Synth12:
    """Continuous generator: successive calls to frames() continue the same tracing."""

    def __init__(self, fs: int = 500, hr: int = 72, seed: int = 4812, leads: tuple[str, ...] = STANDARD_12):
        self.fs, self.hr, self.leads = fs, hr, leads
        self.rnd = random.Random(seed)  # noqa: S311 - synthetic demo signal
        self.t = 0.0
        self.beat_start = 0.0
        self.rr = 60 / hr

    def _beat(self, lead: str, ph: float) -> float:
        amps = AMPLITUDES[lead]
        return sum(a * math.exp(-((ph - m) ** 2) / (2 * sd * sd)) for (m, sd), a in zip(WAVES, amps, strict=True))

    def frames(self, n: int) -> list[tuple[int, ...]]:
        out = []
        for _ in range(n):
            ph = self.t - self.beat_start
            if ph >= self.rr:
                self.beat_start += self.rr
                self.rr = max(0.4, 60 / self.hr + self.rnd.gauss(0, 0.03))  # mild heart-rate variability
                ph = self.t - self.beat_start
            wander = 0.05 * math.sin(2 * math.pi * 0.25 * self.t)
            v = {lead: self._beat(lead, ph) for lead in AMPLITUDES}
            v["III"] = v["II"] - v["I"]
            v["aVR"] = -(v["I"] + v["II"]) / 2
            v["aVL"] = v["I"] - v["II"] / 2
            v["aVF"] = v["II"] - v["I"] / 2
            out.append(tuple(round((v[lead] + wander + self.rnd.gauss(0, 0.012)) * 1000) for lead in self.leads))
            self.t += 1 / self.fs
        return out

    def raw(self, n: int) -> bytes:
        flat = [x for frame in self.frames(n) for x in frame]
        return struct.pack(f"<{len(flat)}h", *flat)
