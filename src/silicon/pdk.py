"""PDK names LibreLane supports with an open PDK. A name is all we hold: every engineering value (supply, corners, rules, cell library) comes from the PDK itself through LibreLane's
resolved configuration, never from this repository."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PdkProfile:
    name: str                 # LibreLane's PDK name
    family: str               # ciel family used to download it
    note: str
    sim_defines: tuple = ()   # macros the cell Verilog models need for plain functional simulation (from the PDK library's own documentation)


OPEN_PDKS = {
    "sky130A": PdkProfile("sky130A", "sky130", "SkyWater 130 nm, open", ("-DFUNCTIONAL", "-DUNIT_DELAY=#0")),
    "gf180mcuD": PdkProfile("gf180mcuD", "gf180mcu", "GlobalFoundries 180 nm MCU, open (variant D)"),
    "ihp-sg13g2": PdkProfile("ihp-sg13g2", "ihp-sg13g2", "IHP 130 nm SiGe BiCMOS, open"),
}


def profile(name: str) -> PdkProfile:
    """The profile of an open PDK; an unknown name is an error (a PDK we hold no knowledge of would run with unchecked assumptions)."""
    try:
        return OPEN_PDKS[name]
    except KeyError:
        raise ValueError(f"unknown PDK '{name}': known open PDKs are {', '.join(OPEN_PDKS)}") from None
