"""Design rules, fab profiles, stackups and IPC-2221 current-capacity trace sizing."""
from __future__ import annotations

import math

OZ_CU_MM = 0.035   # unit conversion: 1 oz/ft^2 copper = 35 um
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ViaSpan:
    """A partial-depth plated via a fab can build: it connects copper layers `first`..`last` (0 = top, n-1 = bottom; both included) and nothing else.
    Everything here is the fab's capability table, never ours: `kind` micro = laser drilled one dielectric deep, blind = from an outer layer, buried = inner only."""
    kind: str            # 'micro' | 'blind' | 'buried'
    first: int
    last: int
    dia: float           # pad diameter, mm
    drill: float         # finished hole, mm
    annular: float       # minimum annular ring, mm
    max_aspect: float    # maximum (depth of the span) : drill
    stackable: bool = False   # the fab allows this via directly on top of another partial via (filled and capped); False = staggered only

    def label(self, n_layers: int) -> str:
        return f"{self.kind} {self.first + 1}-{self.last + 1}"


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
    min_dielectric_mm: float = 0.1   # thinnest prepreg/core between copper layers the fab stocks (fab-profile value); sets the minimum board thickness for high layer counts
    max_aspect_ratio: float = 10.0   # board thickness : finished drill; plated through-holes beyond this are not reliable (fab-profile value)
    hole_clearance: float = 0.25     # copper to drill hole (KiCad min_hole_clearance); fab-profile value
    microvia_dia: float = 0.2        # only used by HDI/advanced profiles; recorded so DRC has an explicit value
    microvia_drill: float = 0.1
    plane_thermal_gap: float = 0.3
    plane_spoke: float = 0.35
    profile: str = "generic"
    via_in_pad: bool = False        # the fab fills and caps vias (IPC-4761 type VII), so a via may sit inside a pad; needed by constraints.ViaInPad
    via_spans: tuple = ()           # partial-depth vias the fab offers (ViaSpan); empty = plated through-holes only. Needs a fab that builds them: see docs/HDI.md
    fiducial_footprint: str = "Fiducial:Fiducial_1mm_Mask2mm"   # KiCad footprint of the assembler's optical alignment mark (size per the assembler's machine; fab-profile value)
    corner_inset_mm: float = 4.0     # centre of a corner-fixed part (mounting hole, fiducial) from the two nearest board edges
    assembly_sides: int = 2       # faces the assembler can populate (1 = top only); a `Side` request for the bottom is refused when 1
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
    # ILLUSTRATIVE HDI capability for tests and demos (not any fab's published table): replace with the chosen fab's spans and limits through rule_overrides / a real profile
    "generic-hdi-6l": dict(layers=6, min_dielectric_mm=0.06, hole_clearance=0.2, via_in_pad=True, track=0.1, min_track=0.075, clearance=0.1, via_dia=0.5, via_drill=0.25, min_drill=0.2, min_annular=0.125, edge_clearance=0.3,
                           via_spans=(ViaSpan("micro", 0, 1, 0.25, 0.1, 0.075, 1.0), ViaSpan("micro", 4, 5, 0.25, 0.1, 0.075, 1.0),
                                      ViaSpan("blind", 0, 2, 0.4, 0.2, 0.1, 1.0), ViaSpan("buried", 1, 4, 0.35, 0.2, 0.1, 8.0))),
    "jlcpcb-6l": dict(layers=6, track=0.15, min_track=0.09, clearance=0.15, via_dia=0.5, via_drill=0.25, min_drill=0.2, min_annular=0.125, edge_clearance=0.3),
}


def rules_for(layers: int = 2, profile: str | None = None, **over) -> Rules:
    name = profile or {2: "jlcpcb-2l", 4: "jlcpcb-4l", 6: "jlcpcb-6l"}.get(layers, "generic")
    kw = dict(FAB_PROFILES.get(name, {}))
    kw.update(over)
    kw["layers"] = layers
    r = Rules(**kw)
    r.profile = name
    _fit_stackup(r)
    return r


def _cu(r: Rules) -> list[float]:
    n = r.layers
    return [(r.copper_oz_outer if k in (0, n - 1) else r.copper_oz_inner) * OZ_CU_MM for k in range(n)]


def _span_caps(r: Rules) -> list[float | None]:
    """Largest dielectric (mm) each gap between adjacent copper layers may have so every partial-depth via stays within its fab aspect ratio
    (depth centre-to-centre <= drill x max_aspect). None = unconstrained."""
    cu = _cu(r)
    caps: list[float | None] = [None] * (r.layers - 1)
    for sp in r.via_spans:
        inner = sum(cu[sp.first + 1:sp.last]) + (cu[sp.first] + cu[sp.last]) / 2
        per_gap = (sp.drill * sp.max_aspect - inner) / (sp.last - sp.first)
        if per_gap <= 0:
            raise ValueError(f"via span {sp.label(r.layers)}: drill x aspect ratio is smaller than the copper it crosses")
        if per_gap < r.min_dielectric_mm:
            raise ValueError(f"via span {sp.label(r.layers)} needs dielectric gaps of {per_gap:.3f} mm, thinner than the fab stocks ({r.min_dielectric_mm} mm)")
        for g in range(sp.first, sp.last):
            caps[g] = per_gap if caps[g] is None else min(caps[g], per_gap)
    return caps


def dielectric_gaps(r: Rules) -> list[float]:
    """Dielectric thickness between each pair of adjacent copper layers (n - 1 values, top to bottom), summing to the board thickness. A gap a partial-depth via crosses
    may not exceed the cap its aspect ratio sets; the rest of the thickness is spread equally over the gaps with room (water-filling)."""
    cu = _cu(r)
    caps = _span_caps(r)
    total = r.thickness_mm - sum(cu) - 2 * r.mask_thickness_mm
    gaps: list[float | None] = [None] * len(caps)
    while True:
        free = [i for i, g in enumerate(gaps) if g is None]
        if not free:
            break
        share = (total - sum(g for g in gaps if g is not None)) / len(free)
        bound = [i for i in free if caps[i] is not None and caps[i] < share]
        if not bound:
            for i in free:
                gaps[i] = max(share, 0.0)
            break
        for i in bound:
            gaps[i] = caps[i]
    return [float(g) for g in gaps]


def _fit_stackup(r: Rules) -> None:
    """High layer counts need a thicker board (copper + the thinnest dielectrics + mask) and then a larger drill (thickness / max aspect ratio); both follow from the
    fab-profile limits. Explicit overrides larger than the derived minimum are kept."""
    n = r.layers
    cu = (2 * r.copper_oz_outer + (n - 2) * r.copper_oz_inner) * OZ_CU_MM
    caps = _span_caps(r)
    need = cu + 2 * r.mask_thickness_mm + (n - 1) * r.min_dielectric_mm
    r.thickness_mm = max(r.thickness_mm, math.ceil(need / WIDTH_RESOLUTION_MM) * WIDTH_RESOLUTION_MM)
    if all(c is not None for c in caps):                      # every gap is capped by some via's aspect ratio: the board cannot be thicker than the caps allow
        reach = cu + 2 * r.mask_thickness_mm + sum(caps)
        if r.thickness_mm > reach:
            r.thickness_mm = math.floor(reach / WIDTH_RESOLUTION_MM) * WIDTH_RESOLUTION_MM
        if r.thickness_mm < need - 1e-9:
            raise ValueError(f"the via spans' aspect ratios allow a board of at most {reach:.3f} mm but the layers need at least {need:.3f} mm")
    drill = math.ceil(r.thickness_mm / r.max_aspect_ratio / WIDTH_RESOLUTION_MM) * WIDTH_RESOLUTION_MM
    if drill > r.min_drill:
        r.min_drill = drill
    r.via_drill = max(r.via_drill, r.min_drill)
    r.via_dia = max(r.via_dia, r.via_drill + 2 * r.min_annular)


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
