"""Linear-regulator equations (pure, SI units)."""
from __future__ import annotations


def dissipation_w(vin_max: float, vout: float, iout: float, iq: float = 0.0) -> float:
    """Worst-case pass-element dissipation P = (Vin_max - Vout) * Iout + Vin_max * Iq."""
    return (vin_max - vout) * iout + vin_max * iq


def max_theta_ja(p_w: float, ambient_c: float, tj_max_c: float) -> float:
    """Largest junction-to-ambient thermal resistance (K/W) that keeps Tj <= tj_max: (Tj_max - Ta) / P."""
    return float("inf") if p_w <= 0 else (tj_max_c - ambient_c) / p_w


def min_input_v(vout: float, dropout_v: float) -> float:
    return vout + dropout_v
