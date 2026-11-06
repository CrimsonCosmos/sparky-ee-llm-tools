"""Design rules, fab profiles, stackups and IPC-2221 current-capacity trace sizing."""
from __future__ import annotations

import math

OZ_CU_MM = 0.035   # unit conversion: 1 oz/ft^2 copper = 35 um
from dataclasses import dataclass, field


@dataclass
class Rules:
    layers: int = 2
    copper_oz_outer: float = 1.0
    copper_oz_inner: float = 0.5
    thickness_mm: float = 1.6
    mask_thickness_mm: float = 0.01      # stackup inputs: set per fab profile / real stackup, never inline in code
    dielectric_er: float = 4.5
    dielectric_tan_d: float = 0.02
    track: float = 0.2             # default signal width
    min_track: float = 0.127
    clearance: float = 0.2
    via_dia: float = 0.6
    via_drill: float = 0.3
    min_drill: float = 0.3
    min_annular: float = 0.13
    hole_to_hole: float = 0.35
    edge_clearance: float = 0.4
    mask_expansion: float = 0.05
    mask_bridge: float = 0.1
    silk_width: float = 0.15
    temp_rise_c: float = 20.0
    hole_clearance: float = 0.25     # copper to drill hole (KiCad min_hole_clearance); fab-profile value
    microvia_dia: float = 0.2        # only used by HDI/advanced profiles; recorded so DRC has an explicit value
    microvia_drill: float = 0.1
    plane_thermal_gap: float = 0.3
    plane_spoke: float = 0.35
    profile: str = "generic"
    fab_tier: str = "standard"    # process capability the design is built for: 'standard' keeps vias/annulars within this Rules' limits; 'advanced' allows via-in-pad and finer vias
                                   # (needs a fab that fills+caps vias, IPC-4761 type VII, and matching via/annular limits in rule_overrides)

    @property
    def min_via_pitch(self) -> float:
        return self.via_dia + self.clearance


FAB_PROFILES = {
    # conservative numbers that standard prototype services accept without surcharge
    "generic": dict(),
    "jlcpcb-2l": dict(layers=2, track=0.2, min_track=0.127, clearance=0.2, via_dia=0.6, via_drill=0.3, min_annular=0.13, edge_clearance=0.4),
    "jlcpcb-4l": dict(layers=4, track=0.15, min_track=0.1, clearance=0.15, via_dia=0.5, via_drill=0.25, min_drill=0.2, min_annular=0.125, edge_clearance=0.3),
    "jlcpcb-6l": dict(layers=6, track=0.15, min_track=0.09, clearance=0.15, via_dia=0.5, via_drill=0.25, min_drill=0.2, min_annular=0.125, edge_clearance=0.3),
}


def rules_for(layers: int = 2, profile: str | None = None, **over) -> Rules:
    name = profile or {2: "jlcpcb-2l", 4: "jlcpcb-4l", 6: "jlcpcb-6l"}.get(layers, "generic")
    kw = dict(FAB_PROFILES.get(name, {}))
    kw.update(over)
    kw["layers"] = layers
    r = Rules(**kw)
    r.profile = name
    return r


def layer_names(n: int) -> list[str]:
    if n == 2:
        return ["F.Cu", "B.Cu"]
    return ["F.Cu"] + [f"In{i}.Cu" for i in range(1, n - 1)] + ["B.Cu"]


def trace_width_for_current(amps: float, rises_c: float = 20.0, oz: float = 1.0, external: bool = True) -> float:
    """IPC-2221 width (mm) for `amps` at `rises_c` temperature rise."""
    if amps <= 0:
        return 0.0
    k = 0.048 if external else 0.024
    area_mil2 = (amps / (k * rises_c ** 0.44)) ** (1 / 0.725)
    thick_mil = 1.378 * oz
    return area_mil2 / thick_mil * 0.0254


WIDTH_RESOLUTION_MM = 0.001   # fab data precision: widths/clearances are rounded UP to 1 um


def net_classes_for(circuit, rules) -> list[dict]:
    """Extra net classes from each net's declared current (IPC-2221 width at rules.temp_rise_c, outer-layer copper) and voltage (IPC-2221 B2 spacing).
    A net keeps the default class unless it needs a wider track or more clearance. Nets are grouped by (width, clearance)."""
    groups: dict[tuple[float, float], list[str]] = {}
    for net, a in circuit.net_attrs.items():
        w = trace_width_for_current(a.current_a, rules.temp_rise_c, rules.copper_oz_outer) if a.current_a > 0 else 0.0
        c = spacing_for_voltage(a.voltage) if a.voltage > 0 else 0.0
        w = math.ceil(max(w, rules.track) / WIDTH_RESOLUTION_MM) * WIDTH_RESOLUTION_MM
        c = math.ceil(max(c, rules.clearance) / WIDTH_RESOLUTION_MM) * WIDTH_RESOLUTION_MM
        if w > rules.track + 1e-9 or c > rules.clearance + 1e-9:
            groups.setdefault((round(w, 3), round(c, 3)), []).append(net)
    return [{"name": f"w{w:.3f}_c{c:.3f}", "track_width": w, "clearance": c, "nets": nets} for (w, c), nets in sorted(groups.items())]


def current_for_width(width_mm: float, rises_c: float = 20.0, oz: float = 1.0, external: bool = True) -> float:
    k = 0.048 if external else 0.024
    area_mil2 = (width_mm / 0.0254) * 1.378 * oz
    return k * rises_c ** 0.44 * area_mil2 ** 0.725


def spacing_for_voltage(volts: float) -> float:
    """IPC-2221 B2 minimum conductor spacing (mm), uncoated external, sea level - conservative steps."""
    table = [(15, 0.1), (30, 0.1), (50, 0.6), (100, 0.6), (150, 0.6), (170, 1.25), (250, 1.25), (300, 1.25), (500, 2.5)]
    for v, s in table:
        if volts <= v:
            return s
    return 2.5 + (volts - 500) * 0.005
