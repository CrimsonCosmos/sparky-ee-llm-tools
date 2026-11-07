"""Transmission-line impedance via scikit-rf (BSD): a thin adapter, no formulas of our own. KiCad's PCB Calculator is GUI-only, so this is the scriptable
equivalent for microstrip and coplanar waveguide. Lengths in mm. scikit-rf is an optional dependency (`pip install scikit-rf`)."""
from __future__ import annotations

import warnings


def available() -> bool:
    try:
        import skrf  # noqa: F401
        return True
    except ImportError:
        return False


def _freq(f_hz: float):
    import skrf
    return skrf.Frequency(f_hz, f_hz, npoints=1, unit="hz")


def microstrip(w_mm: float, h_mm: float, t_mm: float, er: float, f_hz: float = 1e9, tan_d: float = 0.0, rho_ohm_m: float = 1.724e-8) -> tuple[float, float]:
    """(Z0 ohm, effective permittivity) of a surface microstrip (scikit-rf MLine, Hammerstad-Jensen / Kirschning-Jansen family)."""
    from skrf.media import MLine
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # loss model warns for very thin test strips; impedance is unaffected
        m = MLine(_freq(f_hz), w=w_mm * 1e-3, h=h_mm * 1e-3, t=max(t_mm, 1e-6) * 1e-3, ep_r=er, tand=tan_d, rho=rho_ohm_m)
    return float(m.z0[0].real), float(m.ep_reff[0].real)


def microstrip_width_mm(z0_ohm: float, h_mm: float, t_mm: float, er: float, f_hz: float = 1e9) -> float:
    """Width giving `z0_ohm` (bisection on `microstrip`; impedance falls monotonically with width)."""
    lo, hi = 0.02 * h_mm, 40 * h_mm
    if not microstrip(hi, h_mm, t_mm, er, f_hz)[0] <= z0_ohm <= microstrip(lo, h_mm, t_mm, er, f_hz)[0]:
        raise ValueError(f"no microstrip width gives {z0_ohm} ohm on this stackup")
    for _ in range(60):
        mid = (lo * hi) ** 0.5
        lo, hi = (mid, hi) if microstrip(mid, h_mm, t_mm, er, f_hz)[0] > z0_ohm else (lo, mid)
    return (lo * hi) ** 0.5


def coplanar(w_mm: float, gap_mm: float, er: float, h_mm: float, t_mm: float = 0.0, f_hz: float = 1e9) -> tuple[float, float]:
    """(Z0, er_eff) of a coplanar waveguide on a substrate of thickness `h_mm` (scikit-rf CPW; add a ground plane with the backside option there if needed)."""
    from skrf.media import CPW
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = CPW(_freq(f_hz), w=w_mm * 1e-3, s=gap_mm * 1e-3, h=h_mm * 1e-3, t=t_mm * 1e-3 if t_mm else None, ep_r=er)
    return float(m.z0[0].real), float(m.ep_reff[0].real)
