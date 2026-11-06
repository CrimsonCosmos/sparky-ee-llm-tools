"""Preferred-number series (IEC 60063). Pure functions."""
from __future__ import annotations

import math

E6 = (1.0, 1.5, 2.2, 3.3, 4.7, 6.8)
E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)
E24 = (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1)
E96 = tuple(round(10 ** (i / 96), 2) for i in range(96))        # E96 = 96 logarithmic steps (rounded to 3 sig. digits in practice)


def nearest(value: float, series: tuple[float, ...]) -> float:
    """Closest series value (log distance)."""
    if value <= 0:
        raise ValueError("value must be positive")
    dec = math.floor(math.log10(value))
    cands = [m * 10 ** d for d in (dec - 1, dec, dec + 1) for m in series]
    return min(cands, key=lambda v: abs(math.log(v / value)))


def round_up(value: float, series: tuple[float, ...]) -> float:
    """Smallest series value >= value."""
    dec = math.floor(math.log10(value))
    cands = sorted(m * 10 ** d for d in (dec, dec + 1) for m in series)
    return next(v for v in cands if v >= value * 0.9999)
