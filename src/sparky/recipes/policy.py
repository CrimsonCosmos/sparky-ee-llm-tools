"""Engineering policy: every design-margin / preference number a recipe uses, in ONE overridable object (never inline in a recipe).

Values are conventional engineering practice, not facts about a part; override per project with `Policy(...)`. Each field says where its default comes from."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    cap_voltage_derating: float = 0.8        # ceramic/electrolytic working voltage <= this x rated (common derating practice)
    current_margin: float = 1.2              # ratings >= this x the computed continuous current
    chip_resistor: tuple[str, ...] = ("0402", "0603")   # acceptable chip packages, smallest first (JLCPCB basic-part sizes)
    mlcc_effective_fraction: float = 0.6     # usable fraction of nominal MLCC capacitance (DC bias + tolerance + temperature); use the maker's DC-bias curve for tight designs
    cap_value_window: float = 3.0            # accept a standard value up to this multiple of the nominal need (avoids oversizing)
    max_parallel_caps: int = 4               # fewest identical parts that reach the capacitance, up to this many
    chip_capacitor: tuple[str, ...] = ("0402", "0603", "0805", "1206")
    ambient_c: float = 25.0                  # thermal checks start here unless the system spec says otherwise
    tj_max_c: float = 125.0                  # default junction limit for plastic packages
    ldo_dissipation_warn_w: float = 0.5      # beyond this suggest a switching stage first
    # verification (ngspice) settings - simulation effort and acceptance, not facts about parts
    spice_cycles: int = 600
    spice_settle_cycles: int = 550
    spice_points_per_cycle: int = 200
    spice_tolerance: float = 0.05            # simulated vs equation ripple/peak must agree within this fraction
    cap_esr_ohm: float = 5e-3                # typical MLCC ESR at switching frequency; override from the capacitor datasheet
    switch_ron_ohm: float = 50e-3            # used when the regulator datasheet gives no Rds(on)
    chip_inductor: tuple[str, ...] = ("0805", "1008", "1210", "1812")   # chip-code inductor packages that map to KiCad Inductor_SMD footprints
    fuse_margin: float = 1.25                 # fuse rating >= this x continuous current (fuses are derated ~25 % for ambient/aging)
    chip_fuse: tuple[str, ...] = ("0603", "0805", "1206", "1210", "1812")
    surge_margin: float = 1.02               # TVS stand-off must exceed the highest working voltage by at least this factor
    connector_derating: float = 0.8          # continuous current <= this x the connector's contact rating
    demand_margin: float = 1.1               # a regulator is sized for the downstream demand x this
    converter_efficiency: float = 0.9        # assumed when estimating a converter's INPUT current from its output (replace with the datasheet curve for tight budgets)
    logic_level_tolerance_v: float = 0.55   # logic-level mismatch above this needs a level shifter
    capacity_warn_fraction: float = 0.8      # warn when a supply is loaded beyond this fraction of its rating
    contingency_fraction: float = 0.10       # added to the system cost roll-up
    buy_ratio: float = 1.15                  # buy instead of build when the purchased price <= build BOM x this + buy_abs_usd (buy-vs-build policy)
    buy_abs_usd: float = 3.0
    spare_passive_fraction: float = 0.10     # ordering overage: passives +this fraction (at least spare_passive_min), other parts +spare_other_min or this fraction x qty
    spare_passive_min: int = 5
    spare_other_fraction: float = 0.04
    spare_other_min: int = 1
