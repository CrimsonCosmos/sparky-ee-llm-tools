"""Radiated-emission pre-compliance estimate from the board's copper currents, via pcb-analysis (MIT; beta).

Contract: routed .kicad_pcb + current sources (where a harmonic's current enters and leaves the copper) -> far field at the CISPR/FCC distance and the margin
to the chosen limit line. The solver is the library's (sheet-PEEC current distribution -> Hertzian-dipole superposition); we only set up the case, including the straight return element between the two pads.
Limits of the method (the library's own): current-only description, free space unless a ground plane is given, no cables/enclosure/dielectric radiation:
this screens a layout, it does not replace a compliance test."""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..toolchain import kicad_cli


@dataclass(frozen=True)
class Source:
    """One harmonic current: `amps` enters the copper at (x_in, y_in) and leaves at (x_out, y_out); KiCad board coordinates (mm, y down), on `layer`."""
    name: str
    frequency_hz: float
    amps: float
    x_in: float
    y_in: float
    x_out: float
    y_out: float
    layer: str = "F.Cu"


@dataclass
class EmcResult:
    frequency_hz: float
    distance_m: float
    field_dbuv_per_m: float
    limit_dbuv_per_m: float
    margin_db: float              # positive = below the limit
    compliant: bool
    limit_name: str


def available() -> bool:
    try:
        import emc.tiled_dipole_superposition  # noqa: F401
        import electrical.sheet_peec  # noqa: F401
        from geometry.cad_import.mesh import native_available
    except ImportError:
        return False
    return native_available() and kicad_cli() is not None


def run_emc(pcb: str | Path, src: Source, *, distance_m: float, limit: str, pitch_mm: float, pad_half_mm: float) -> EmcResult:
    """`limit` = name of a library limit line: CISPR32_CLASS_A/B, FCC_PART15_CLASS_A/B. All numbers are caller-supplied (no defaults)."""
    import numpy as np
    import emc.tiled_dipole_superposition as emc
    from electrical.sheet_peec import build_current_field_sheet_inputs, solve_sheet_case
    from geometry.cad_import import (board_barrels, board_vias, current_field_problem_mapping, kicad_step_body_map, layers_from_kicad_stackup, load_step,
                                     rasterize_board, read_kicad_stackup, resolve_bodies)
    if not available():
        raise RuntimeError("pcb-analysis with compiled kernels and kicad-cli are required; see docs/DEPENDENCIES.md")
    pcb = Path(pcb).resolve()
    with tempfile.TemporaryDirectory(prefix="emc_") as td:
        step = Path(td) / "b.step"
        r = subprocess.run([kicad_cli(), "pcb", "export", "step", "--include-tracks", "--include-pads", "--include-zones", "--include-inner-copper",
                            "--no-extra-pad-thickness", "--no-components", "--force", "--output", str(step), str(pcb)], capture_output=True, text=True, timeout=600)
        if not step.exists():
            raise RuntimeError(f"kicad-cli step export failed: {(r.stderr or r.stdout).strip()[-400:]}")
        layers, top_mm = layers_from_kicad_stackup(read_kicad_stackup(str(pcb)))
        body_map = kicad_step_body_map(layers, board_top_z_mm=top_mm)
        resolved = resolve_bodies(load_step(str(step)), body_map)
        raster = rasterize_board(resolved, body_map.board, pitch_mm=pitch_mm, y_down=True)

    def cells(x_mm: float, y_mm: float):
        row, col = raster.cell_of(x_mm * 1e-3, -y_mm * 1e-3)            # the STEP export negates KiCad's y
        k = [s.name for s in raster.layers].index(src.layer)
        rr = int(np.ceil(pad_half_mm / raster.pitch_mm))
        rows, cols = raster.shape
        return [{"layer": src.layer, "x": x, "y": y} for y in range(max(row - rr, 0), min(row + rr + 1, rows)) for x in range(max(col - rr, 0), min(col + rr + 1, cols))
                if raster.occupancy[k, y, x] > 0]

    terminals = [{"name": "IN", "pad": "in", "current_a": src.amps, "cells": cells(src.x_in, src.y_in)},
                 {"name": "OUT", "pad": "out", "current_a": -src.amps, "cells": cells(src.x_out, src.y_out)}]
    if not terminals[0]["cells"] or not terminals[1]["cells"]:
        raise ValueError("a current terminal does not lie on copper on that layer")
    problem = current_field_problem_mapping(raster, terminals=terminals, frequency_hz=0.0, vias=board_vias(resolved, raster), barrels=board_barrels(resolved, raster))
    mesh, operator, sheet_terminals, _ = build_current_field_sheet_inputs(problem)
    current = solve_sheet_case(mesh, operator, sheet_terminals, frequency_hz=0.0)      # DC solve: the current's PATH; fields evaluated at the harmonic (quasi-static)
    dipoles = emc.dipoles_from_sheet_peec(mesh, current)
    # The copper only carries the current from IN to OUT. In a real circuit it returns through a component/cable between those pads; without that element the
    # distribution is an OPEN wire that radiates like a dipole (the library's own demo shows +70 dB). Close it with one straight element OUT -> IN.
    x0, y0 = mesh.grid.origin_m
    k = [s_.name for s_ in raster.layers].index(src.layer)
    z = float(mesh.stackup.layers[k].z_m)

    def centre(t):
        cs = t["cells"]
        return [x0 + (float(np.mean([c["x"] for c in cs])) + 0.5) * mesh.pitch_m, y0 + (float(np.mean([c["y"] for c in cs])) + 0.5) * mesh.pitch_m, z]
    dipoles = dipoles.concatenate(emc.path_dipoles({"return": src.amps}, {"return": ("OUT", "IN")}, {"IN": centre(terminals[0]), "OUT": centre(terminals[1])}))
    pattern = emc.far_field_pattern(dipoles, src.frequency_hz, distance_m=distance_m)
    lim = getattr(emc, limit)
    m = emc.emission_margin(pattern.max_polarised_field_v_per_m, src.frequency_hz, lim, distance_m=distance_m)
    return EmcResult(src.frequency_hz, distance_m, float(m.predicted_dbuv_per_m), float(m.limit_dbuv_per_m), float(m.margin_db), bool(m.compliant), limit)
