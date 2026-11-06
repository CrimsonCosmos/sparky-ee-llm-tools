"""Buck converter design equations (pure, SI units: V, A, Hz, H, F, ohm).

Follows the published TI design procedure (e.g. TPS54360B datasheet section 8.2.2, Eq. 27-51), with every
device-specific number (reference voltage, switching frequency, recommended inductor range, transconductances)
supplied by the caller from the datasheet / part database - nothing about a particular IC is hardcoded here."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .eseries import E12, E24, E6, nearest, round_up


@dataclass(frozen=True)
class BuckSpec:
    vin_min: float
    vin_max: float
    vout: float
    iout: float
    fsw: float                       # Hz (from datasheet / RT resistor)
    vref: float                      # feedback reference (V)
    ripple_ratio: float = 0.3        # inductor ripple / iout (KIND)
    l_range: tuple[float, float] | None = None    # datasheet-recommended inductance window (H), if any
    cout_range: tuple[float, float] | None = None  # datasheet-allowed total output capacitance (F), if any
    r_bottom: float = 10e3           # feedback divider bottom resistor
    synchronous: bool = True


@dataclass(frozen=True)
class BuckDesign:
    r_top: float
    r_bottom: float
    vout_actual: float
    inductance: float
    inductor_ripple: float           # A pk-pk
    inductor_peak: float             # A
    inductor_isat_min: float         # A
    cout_required: float             # F (effective, after the most stringent criterion)
    cin_required: float              # F (effective)
    duty_max: float
    notes: tuple[str, ...] = field(default_factory=tuple)


def feedback_divider(vout: float, vref: float, r_bottom: float = 10e3) -> tuple[float, float, float]:
    """Vout = vref*(1 + Rtop/Rbottom). Returns (Rtop, Rbottom, actual Vout) using E96-quality values (1 %)."""
    from .eseries import E96
    r_top = nearest(r_bottom * (vout / vref - 1.0), E96)
    return r_top, r_bottom, vref * (1.0 + r_top / r_bottom)


def design_buck(s: BuckSpec) -> BuckDesign:
    notes: list[str] = []
    d_max = s.vout / s.vin_min if s.vin_min > 0 else 1.0
    d_min = s.vout / s.vin_max
    # inductor from the *highest* input voltage (largest ripple)
    l_calc = (s.vin_max - s.vout) * d_min / (s.fsw * s.ripple_ratio * s.iout)
    l = round_up(l_calc, E6)
    if s.l_range:
        lo, hi = s.l_range
        if not lo <= l <= hi:
            notes.append(f"inductance {l_calc * 1e6:.2f} uH outside datasheet-recommended {lo * 1e6:g}-{hi * 1e6:g} uH; clamped")
            l = min((v for v in (m * 1e-6 for m in (1.0, 1.5, 2.2, 3.3, 4.7, 6.8, 10, 15, 22, 33, 47)) if lo * 0.999 <= v <= hi * 1.001 and v >= min(l_calc, hi) * 0.999),
                    default=hi)
    di = (s.vin_max - s.vout) * d_min / (s.fsw * l)
    ipk = s.iout + di / 2
    # output capacitance: load step (2 clock cycles), energy overshoot, ripple - most stringent wins (TI Eq. 32-34)
    d_i = 0.5 * s.iout
    c_step = 2 * d_i / (s.fsw * 0.04 * s.vout)
    i_high, i_low = 0.75 * s.iout, 0.25 * s.iout
    c_energy = l * (i_high ** 2 - i_low ** 2) / ((1.04 * s.vout) ** 2 - s.vout ** 2)
    cout = max(c_step, c_energy)
    if s.cout_range:
        lo, hi = s.cout_range
        if cout < lo:
            notes.append(f"output capacitance raised to datasheet minimum {lo * 1e6:g} uF")
        cout = min(max(cout, lo), hi)
    # input capacitance: ripple of 0.4 V at the worst duty (TI Eq. 39)
    cin = 0.25 * s.iout / (s.fsw * 0.4)
    r_top, r_bot, vact = feedback_divider(s.vout, s.vref, s.r_bottom)
    if abs(vact / s.vout - 1) > 0.02:
        notes.append(f"feedback divider gives {vact:.3f} V (>2 % from {s.vout} V)")
    if s.vin_min < s.vout + 1.0:
        notes.append("input minimum within 1 V of the output: dropout risk")
    return BuckDesign(r_top, r_bot, vact, l, di, ipk, ipk * 1.3, cout, cin, d_max, tuple(notes))


def type2a_compensation(vout: float, iout: float, cout_eff: float, fsw: float, vref: float, gm_ea: float, gm_ps: float,
                        esr: float = 2.5e-3) -> dict[str, float]:
    """Type-2A compensation (R, C, Cpole) per TI TPS54360B Eq. 44-51 (ceramic output capacitors)."""
    fp = iout / (2 * math.pi * vout * cout_eff)
    fz = 1 / (2 * math.pi * esr * cout_eff)
    fco = min(math.sqrt(fp * fz), math.sqrt(fp * fsw / 2))
    r = nearest((2 * math.pi * fco * cout_eff / gm_ps) * (vout / (vref * gm_ea)), E24)
    c = nearest(1 / (2 * math.pi * r * fp), E12)
    c_pole = nearest(max(cout_eff * esr / r, 1 / (r * fsw * math.pi), 10e-12), E12)
    return {"r_comp": r, "c_comp": c, "c_pole": c_pole, "f_crossover": fco, "f_pole_mod": fp}


def uvlo_divider(vin_min: float, v_en_threshold: float, hysteresis_current: float | None = None,
                 start_margin: float = 0.92) -> dict[str, float]:
    """Enable-pin divider setting the UVLO start voltage (TI Eq. 40/41 when the part has a hysteresis current)."""
    from .eseries import E96
    v_start = max(vin_min * start_margin, 2 * v_en_threshold)
    v_stop = 0.8 * v_start
    if hysteresis_current:
        r_top = nearest((v_start - v_stop) / hysteresis_current, E96)
        r_bot = nearest(v_en_threshold / ((v_start - v_en_threshold) / r_top), E96)
    else:
        r_bot = 100e3
        r_top = nearest(r_bot * (v_start / v_en_threshold - 1), E96)
    return {"r_top": r_top, "r_bottom": r_bot, "v_start": v_start, "v_stop": v_stop}
