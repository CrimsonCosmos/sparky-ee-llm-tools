"""Generic passives from KiCad's Device library. Sizing/footprints follow KiCad's own naming scheme."""
from __future__ import annotations

from ..units import fmt_si
from . import skidl

_LIB = {"R": ("Resistor_SMD", "R"), "C": ("Capacitor_SMD", "C"), "L": ("Inductor_SMD", "L"), "F": ("Fuse", "Fuse")}


def chip_footprint(kind: str, code: str) -> str:
    """KiCad chip footprint for an imperial code, found in KiCad's library (see picker.footprints); raises if the library has no single match."""
    from ..picker.footprints import chip
    lib, prefix = _LIB[kind]
    fp = chip(lib, prefix, code)
    if fp is None:
        raise LookupError(f"no unique KiCad footprint for {kind} {code} in {lib}")
    return fp


def passive(kind: str, value: float, size: str = "0402", **fields):
    """kind: R | C | L. Returns a SKiDL Part (value string like '4.7k'); extra fields (voltage, tolerance...) become KiCad fields."""
    s = skidl()
    p = s.Part("Device", kind, value=fmt_si(value), footprint=chip_footprint(kind, size))
    for k, v in fields.items():
        p.fields[k] = v
    return p
