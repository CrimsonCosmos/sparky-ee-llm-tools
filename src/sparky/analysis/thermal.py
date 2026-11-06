"""Steady-state board temperature rise via pcb-analysis (MIT, https://github.com/AFLOY/pcb-analysis; beta).

Contract: routed .kicad_pcb + {reference: dissipated watts} -> ThermalResult. The solver is the library's matrix-free FEM over the
board's real copper/laminate stack (KiCad STEP export -> raster -> mesh); we only place heat sources and boundaries. Every environment/grid number (ambient, h, emissivity, pitch, source size) is a required
argument supplied from the system spec or fab profile - no defaults here."""
from __future__ import annotations

import csv
import logging
import time
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..toolchain import kicad_cli

log = logging.getLogger("sparky.thermal")   # pcb-analysis has no debug output of its own; enable DEBUG to see stage timings


@dataclass
class ThermalResult:
    ambient_c: float
    peak_rise_k: float
    heat_in_w: float
    balance_error_w: float
    part_rise_k: dict[str, float]      # mean temperature rise under each heat source's footprint centre


def available() -> bool:
    """True only with the library AND its compiled kernels: without them rasterising falls back to pure NumPy (minutes-hours per board)."""
    try:
        import thermal.matrix_free_mpir_fem  # noqa: F401
        from geometry.cad_import.mesh import native_available
    except ImportError:
        return False
    return native_available() and kicad_cli() is not None


def _positions(csv_path: Path) -> dict[str, tuple[float, float]]:
    out = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            out[row["Ref"]] = (float(row["PosX"]), float(row["PosY"]))
    return out


def run_thermal(pcb: str | Path, power_w: dict[str, float], positions_csv: str | Path, *, ambient_c: float,
                h_w_m2k: float, emissivity: float, pitch_mm: float, source_half_mm: dict[str, float]) -> ThermalResult:
    import numpy as np
    from geometry.cad_import import (board_thermal_mesh, kicad_step_body_map, layers_from_kicad_stackup, load_step, rasterize_board,
                                     read_kicad_stackup, resolve_bodies)
    from thermal.matrix_free_mpir_fem import ConvectionBoundary, HeatSource, RadiationBoundary, ThermalConductionProblem, solve_thermal_conduction

    if not available():
        raise RuntimeError("pcb-analysis with compiled kernels (and kicad-cli) is required; see docs/DEPENDENCIES.md")
    exe = kicad_cli()
    t0 = time.perf_counter()

    def lap(what: str) -> None:
        nonlocal t0
        log.debug("thermal %s: %.1fs", what, time.perf_counter() - t0)
        t0 = time.perf_counter()

    pcb = Path(pcb).resolve()
    pos = _positions(Path(positions_csv))
    with tempfile.TemporaryDirectory(prefix="therm_") as td:
        step = Path(td) / "b.step"
        r = subprocess.run([exe, "pcb", "export", "step", "--include-tracks", "--include-pads", "--include-zones", "--include-inner-copper",
                            "--no-extra-pad-thickness", "--no-components", "--force", "--output", str(step), str(pcb)],
                           capture_output=True, text=True, timeout=600)
        if not step.exists():
            raise RuntimeError(f"kicad-cli step export failed: {(r.stderr or r.stdout).strip()[-400:]}")
        lap("step export")
        layers, top_mm = layers_from_kicad_stackup(read_kicad_stackup(str(pcb)))
        body_map = kicad_step_body_map(layers, board_top_z_mm=top_mm)
        resolved = resolve_bodies(load_step(str(step)), body_map)
        raster = rasterize_board(resolved, body_map.board, pitch_mm=pitch_mm, y_down=True)
    lap("load+rasterize")
    mesh = board_thermal_mesh(raster).mesh
    log.debug("thermal mesh nodes %s", mesh.node_shape)

    def top_nodes(x_mm: float, y_mm: float, half_mm: float):
        row, col = raster.cell_of(x_mm * 1e-3, y_mm * 1e-3)           # `kicad-cli pos` PosY is already y-up, i.e. the STEP frame
        r = int(np.ceil(half_mm / raster.pitch_mm))
        z = mesh.node_shape[0] - 1
        return tuple((z, y, x) for y in range(row - r, row + r + 2) for x in range(col - r, col + r + 2))

    missing = [k for k in power_w if k not in pos]
    if missing:
        raise KeyError(f"no position for {missing}")
    srcs = tuple(HeatSource(top_nodes(*pos[k], source_half_mm[k]), power_w=w, name=k) for k, w in power_w.items() if w > 0)
    amb = ambient_c + 273.15
    prob = ThermalConductionProblem(mesh, convection=(ConvectionBoundary("top", h_w_m2k, amb), ConvectionBoundary("bottom", h_w_m2k, amb)),
                                    radiation=((RadiationBoundary("top", emissivity, amb),) if emissivity > 0 else ()), heat_sources=srcs)
    sol = solve_thermal_conduction(prob)
    lap("solve")
    top = sol.temperature_k[-1]
    rises = {}
    for s in srcs:
        vals = [sol.temperature_k[z, y, x] for z, y, x in s.nodes if 0 <= y < top.shape[0] and 0 <= x < top.shape[1]]
        rises[s.name] = float(np.mean(vals)) - amb
    return ThermalResult(ambient_c, float(sol.max_temperature_k - amb), float(sol.total_heat_input_w), float(sol.heat_balance_error_w), rises)
