"""Radiated emission of a switching loop: copper network + parts + a harmonic current source, via pcb-analysis (MIT; beta).

Unlike `analysis.emc` (one current path closed by a straight element), this models the actual loop: the board's copper is solved as a pad-port network at each
frequency, the parts that join its nets (input capacitor: C + ESR + ESL) close it, and a current source (the switch) drives it; the copper currents and the parts'
currents radiate together. Pure setup code: no field solver of our own.

Inputs are explicit (pads with their positions, part models, the source spectrum, band, distance, limit): nothing here is a default."""
from __future__ import annotations

import math
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .. import sexpr
from ..pcb.rules import OZ_CU_MM
from ..toolchain import kicad_cli

COPPER_RESISTIVITY_OHM_M = 1.724e-8      # annealed copper at 20 C (IEC 60028 standard value)


@dataclass(frozen=True)
class LoopPad:
    name: str            # unique, e.g. "U1.3"
    net: str
    x_mm: float          # KiCad board coordinates (y down)
    y_mm: float
    layer: str
    half_w_mm: float
    half_h_mm: float


@dataclass(frozen=True)
class CapPart:
    name: str
    pad_a: str
    pad_b: str
    farads: float
    esr_ohm: float
    esl_h: float


@dataclass
class Harmonic:
    frequency_hz: float
    field_dbuv_per_m: float
    limit_dbuv_per_m: float | None
    margin_db: float | None
    radiated_power_w: float


def available() -> bool:
    try:
        import electrical.port_network  # noqa: F401
        import emc.tiled_dipole_superposition  # noqa: F401
        from geometry.cad_import.mesh import native_available
    except ImportError:
        return False
    return native_available() and kicad_cli() is not None


def trapezoid_harmonic_a(ipk_a: float, duty: float, fsw_hz: float, rise_s: float, f_hz: float) -> float:
    """Amplitude (A) of the Fourier component at f of a trapezoidal pulse train (peak ipk, duty d at the 50 % points, equal rise/fall time tr, period T):
    |c| = 2 * ipk * d * |sinc(pi f d T)| * |sinc(pi f tr)|  (the standard envelope for a trapezoid; Paul, Introduction to EMC)."""
    t = 1.0 / fsw_hz
    def sinc(x: float) -> float:
        return 1.0 if x == 0 else abs(math.sin(x) / x)
    return 2.0 * ipk_a * duty * sinc(math.pi * f_hz * duty * t) * sinc(math.pi * f_hz * rise_s)


def trapezoid_envelope_a(ipk_a: float, duty: float, fsw_hz: float, rise_s: float, f_hz: float) -> float:
    """Worst-case (upper-envelope) harmonic amplitude near f: the asymptotes 2*ipk*d, then -20 dB/decade above 1/(pi d T), then -40 dB/decade above 1/(pi tr).
    Use this for pre-compliance: the exact line amplitudes have zeros that depend on the duty cycle."""
    t = 1.0 / fsw_hz
    return 2.0 * ipk_a * duty * min(1.0, 1.0 / (math.pi * f_hz * duty * t)) * min(1.0, 1.0 / (math.pi * f_hz * rise_s))


def _via_cells_and_segments(root: list, names: dict, raster, layer_z: dict[str, float], plating_mm: float):
    out = []
    order = sorted(layer_z, key=layer_z.get)
    for k, node in enumerate(n for n in root if isinstance(n, list) and n and n[0] == "via"):
        at, ly, sz, dr = sexpr.find(node, "at"), sexpr.find(node, "layers"), sexpr.find(node, "size"), sexpr.find(node, "drill")
        if not (at and ly and dr):
            continue
        a, b = str(ly[1]), str(ly[2])
        if a not in layer_z or b not in layer_z:
            continue
        lo, hi = sorted((order.index(a), order.index(b)))
        row, col = raster.cell_of(float(at[1]) * 1e-3, -float(at[2]) * 1e-3)
        d = float(dr[1])
        area_mm2 = math.pi * plating_mm * (d + plating_mm)                       # plated barrel annulus
        segs = []
        for i in range(lo, hi):
            length = abs(layer_z[order[i + 1]] - layer_z[order[i]])
            segs.append({"upper_layer": order[i], "lower_layer": order[i + 1], "resistance_ohm": COPPER_RESISTIVITY_OHM_M * length * 1e-3 / (area_mm2 * 1e-6),
                         "length_mm": length})
        if segs:
            out.append({"name": f"via_{k}", "cell": {"x": int(col), "y": int(row)}, "segments": segs})
    return out


def run_hot_loop(pcb: str | Path, *, pads: list[LoopPad], references: dict[str, str], parts: list[CapPart], source_hi: str, source_lo: str,
                 amps_by_f: dict[float, float], rules, plating_mm: float, pitch_mm: float, distance_m: float, limit: str | None) -> list[Harmonic]:
    """Solve the copper as a port network at each frequency of `amps_by_f`, close it with `parts`, drive `source_lo -> source_hi` with the given harmonic amplitudes,
    and return the radiated field per frequency (and its margin to the `limit` line where that has one). `references[pad]` = the reference pad of the same net."""
    import numpy as np
    import emc.tiled_dipole_superposition as emc
    from electrical.port_network import BoardNetwork, CurrentSource, Part, capacitor
    from electrical.sheet_peec import PortImpedanceProblem, solve_port_impedance_problem
    from geometry.cad_import import (kicad_step_body_map, layers_from_kicad_stackup, load_step, rasterize_board, read_kicad_stackup, resolve_bodies)
    from ..export.kicad import layer_z_mm
    if not available():
        raise RuntimeError("pcb-analysis with compiled kernels and kicad-cli are required; see docs/DEPENDENCIES.md")
    pcb = Path(pcb).resolve()
    text = pcb.read_text()
    with tempfile.TemporaryDirectory(prefix="emcn_") as td:
        step = Path(td) / "b.step"
        r = subprocess.run([kicad_cli(), "pcb", "export", "step", "--include-tracks", "--include-pads", "--include-zones", "--include-inner-copper",
                            "--no-extra-pad-thickness", "--no-components", "--force", "--output", str(step), str(pcb)], capture_output=True, text=True, timeout=600)
        if not step.exists():
            raise RuntimeError(f"kicad-cli step export failed: {(r.stderr or r.stdout).strip()[-400:]}")
        layers, top_mm = layers_from_kicad_stackup(read_kicad_stackup(str(pcb)))
        resolved = resolve_bodies(load_step(str(step)), kicad_step_body_map(layers, board_top_z_mm=top_mm))
        raster = rasterize_board(resolved, kicad_step_body_map(layers, board_top_z_mm=top_mm).board, pitch_mm=pitch_mm, y_down=True)
    z = layer_z_mm(rules, rules.layers)
    names = [s.name for s in raster.layers]
    front, back = min(z, key=z.get), max(z, key=z.get)
    copper = {nm: [{"x": int(c), "y": int(rr)} for rr, c in np.argwhere(raster.occupancy[k] > 0)] for k, nm in enumerate(names)}
    root = sexpr.parse(text)

    def cells(p: LoopPad) -> list[dict]:
        row0, col0 = raster.cell_of((p.x_mm - p.half_w_mm) * 1e-3, -(p.y_mm - p.half_h_mm) * 1e-3)
        row1, col1 = raster.cell_of((p.x_mm + p.half_w_mm) * 1e-3, -(p.y_mm + p.half_h_mm) * 1e-3)
        k = names.index(p.layer)
        return [{"layer": p.layer, "x": c, "y": rr} for rr in range(min(row0, row1), max(row0, row1) + 1) for c in range(min(col0, col1), max(col0, col1) + 1)
                if raster.occupancy[k, rr, c] > 0]

    value = {"schema": "port-impedance-problem/v1", "name": "hot_loop", "grid": {"rows": int(raster.shape[0]), "columns": int(raster.shape[1]), "pitch_mm": pitch_mm},
             # order 0 = front (top) copper, by physical height (the raster's own layer order can run bottom-up)
             "layers": [{"name": nm, "order": k, "center_z_mm": -z[nm],
                         "thickness_mm": (rules.copper_oz_outer if nm in (front, back) else rules.copper_oz_inner) * OZ_CU_MM, "resistivity_ohm_m": COPPER_RESISTIVITY_OHM_M}
                        for k, nm in enumerate(sorted(z, key=z.get))],
             "copper_by_layer": copper, "vertical_connections": _via_cells_and_segments(root, {}, raster, z, plating_mm),
             "frequencies_hz": sorted(amps_by_f),
             "pads": [{"name": p.name, "part": p.name.split(".")[0], "net": p.net, "cells": cells(p)} for p in pads],
             "ports": [{"pad": p.name, "reference": references[p.name]} for p in pads if p.name in references and references[p.name] != p.name]}
    empty = [p["name"] for p in value["pads"] if not p["cells"]]
    if empty:
        raise ValueError(f"pads with no copper cells on their layer: {empty}")
    result = solve_port_impedance_problem(value, {"maximum_filaments": 1, "relative_tolerance": 1e-9}, keep_solutions=True)
    problem = PortImpedanceProblem.from_mapping(value)
    net_parts = [Part(c.name, c.pad_a, c.pad_b, capacitor(c.farads, c.esr_ohm, c.esl_h)) for c in parts]
    sources = [CurrentSource("Q", source_lo, source_hi, dict(amps_by_f))]
    network = BoardNetwork(result.impedances, net_parts, ground=references.get(source_lo, source_lo), current_sources=sources)
    freqs = sorted(amps_by_f)
    solutions = [network.solve(f) for f in freqs]
    coppers = {f: result.impedances[i] for i, f in enumerate(freqs)}
    out = emc.network_emission(network, solutions, coppers, result.meshes_by_frequency, problem.pad_positions_m(), distance_m=distance_m,
                               limit=getattr(emc, limit) if limit else None)
    return [Harmonic(e.frequency_hz, float(emc.db_microvolt_per_m(e.far_field_v_per_m)), (e.margin.limit_dbuv_per_m if e.margin else None),
                     (e.margin.margin_db if e.margin else None), float(e.radiated_power_w)) for e in out]
