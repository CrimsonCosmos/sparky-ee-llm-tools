"""Generic passives from KiCad's Device library. Sizing/footprints follow KiCad's own naming scheme."""
from __future__ import annotations

from ..units import fmt_si
from . import skidl

_METRIC = {"0201": "0603", "0402": "1005", "0603": "1608", "0805": "2012", "1206": "3216", "1210": "3225", "2512": "6332"}


def chip_footprint(kind: str, size: str) -> str:
    lib = {"R": "Resistor_SMD", "C": "Capacitor_SMD", "L": "Inductor_SMD", "D": "LED_SMD"}[kind]
    return f"{lib}:{kind}_{size}_{_METRIC[size]}Metric"


def passive(kind: str, value: float, size: str = "0402", **fields):
    """kind: R | C | L. Returns a SKiDL Part (value string like '4.7k'); extra fields (voltage, tolerance...) become KiCad fields."""
    s = skidl()
    p = s.Part("Device", kind, value=fmt_si(value), footprint=chip_footprint(kind, size))
    for k, v in fields.items():
        p.fields[k] = v
    return p
