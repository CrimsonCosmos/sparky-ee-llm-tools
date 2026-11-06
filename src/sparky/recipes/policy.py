"""Engineering policy: every design-margin / preference number a recipe uses, in ONE overridable object (never inline in a recipe).

Values are conventional engineering practice, not facts about a part; override per project with `Policy(...)`. Each field says where its default comes from."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    cap_voltage_derating: float = 0.8        # ceramic/electrolytic working voltage <= this x rated (common derating practice)
    current_margin: float = 1.2              # ratings >= this x the computed continuous current
    chip_resistor: tuple[str, ...] = ("0402", "0603")   # acceptable chip packages, smallest first (JLCPCB basic-part sizes)
    chip_capacitor: tuple[str, ...] = ("0402", "0603", "0805", "1206")
    ambient_c: float = 25.0                  # thermal checks start here unless the system spec says otherwise
    tj_max_c: float = 125.0                  # default junction limit for plastic packages
    ldo_dissipation_warn_w: float = 0.5      # beyond this suggest a switching stage first
