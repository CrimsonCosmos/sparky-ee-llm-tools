"""Write the manufacturing + review package for a built board. Gerbers, drills, positions, IPC-2581 and the board image are produced by KiCad."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .. import kicad_backend
from ..pcb.flow import BuildResult
from . import bom as bomx
from . import kicad
from ..analysis import irdrop as irdropx
from ..analysis import thermal as thermalx
from ..recipes.policy import Policy
from .report import kicad_drc_section, render_report


def write_package(res: BuildResult, outdir: str | Path, project: str, extra: dict | None = None, policy: Policy | None = None) -> dict[str, Path]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    b = res.board
    c = b.circuit
    if res.pcb_path is None:
        raise ValueError("BuildResult has no routed board (pcb_path)")
    extra = dict(extra or {})
    files: dict[str, Path] = {}
    kfiles = kicad_backend.export_fab(res.pcb_path, out, project)
    files.update({k: v for k, v in kfiles.items()})
    (out / "kicad_drc.json").write_text(json.dumps(res.drc.to_dict(), indent=1))
    files["kicad_drc.json"] = out / "kicad_drc.json"
    extra["KiCad DRC (authoritative)"] = kicad_drc_section(res.drc)
    if res.pcb_path.with_suffix(".kicad_pro").exists():
        (out / f"{project}.kicad_pro").write_bytes(res.pcb_path.with_suffix(".kicad_pro").read_bytes())

    pol = policy or Policy()
    extra["DC IR drop (open-source: kicad-irdrop)"] = _irdrop(res, out, files, pol)
    extra["Thermal (open-source FEM: pcb-analysis)"] = _thermal(res, out, files, pol)

    def w(name: str, text: str) -> None:
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        files[name] = p
    w("bom.csv", bomx.bom_csv(c))
    w("bom_jlcpcb.csv", bomx.bom_jlcpcb(c))
    w("cpl_jlcpcb.csv", bomx.cpl_csv(b))
    w(f"{project}.net", kicad.to_kicad_netlist(c))
    w("REPORT.md", render_report(res, project, extra))
    gfiles = [v for k, v in kfiles.items() if k.startswith("gerber:")]
    zp = out / f"{project}_fab.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for v in gfiles:
            if v.suffix != ".gbrjob" and "drl_map" not in v.name:
                z.write(v, v.name)
    files["fab.zip"] = zp
    zp2 = out / f"{project}_assembly.zip"
    with zipfile.ZipFile(zp2, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(out / "bom_jlcpcb.csv", "bom.csv")
        z.write(out / "cpl_jlcpcb.csv", "cpl.csv")
    files["assembly.zip"] = zp2
    return files


def _thermal(res: BuildResult, out: Path, files: dict, pol: Policy) -> str:
    """Steady-state board temperature from the dissipation the recipes declared. Says plainly when it did not run."""
    b = res.board
    power = {ref: c.power_w for ref, c in b.circuit.components.items() if c.power_w > 0}
    if not power:
        return "NOT RUN: no component declared a dissipation (recipes set `PowerW` where they can compute it)."
    if not thermalx.available():
        return "NOT RUN: pcb-analysis with its compiled kernels is not installed (see docs/DEPENDENCIES.md); the unverified dissipations are: " + \
               ", ".join(f"{r} {w:.2f} W" for r, w in power.items())
    half = {ref: 0.5 * min(x1 - x0, y1 - y0) for ref in power for (x0, y0, x1, y1) in [b.courtyard(ref)]}
    r = thermalx.run_thermal(res.pcb_path, power, out / "kicad_positions.csv", ambient_c=pol.ambient_c, h_w_m2k=pol.convection_h_w_m2k, emissivity=pol.emissivity,
                             pitch_mm=pol.thermal_pitch_mm, source_half_mm=half)
    import json
    (out / "thermal.json").write_text(json.dumps(r.__dict__, indent=1))
    files["thermal.json"] = out / "thermal.json"
    rows = "\n".join(f"- {ref}: {power[ref]:.2f} W -> +{r.part_rise_k.get(ref, 0):.1f} K at its footprint" for ref in power)
    return (f"Ambient {r.ambient_c:g} C, natural convection h = {pol.convection_h_w_m2k:g} W/m2K on both faces, emissivity {pol.emissivity:g}; heat balance error "
            f"{r.balance_error_w:.1e} W. **Peak rise {r.peak_rise_k:.1f} K** above ambient (total {r.heat_in_w:.2f} W).\n\n{rows}\n\n"
            "Dissipation inputs are the recipes' own estimates (e.g. regulator conduction loss), not measurements; package thermal resistance and an enclosure are not modelled.")


def _irdrop(res: BuildResult, out: Path, files: dict, pol: Policy) -> str:
    """DC voltage drop on each power rail's real copper (pours, tracks, via barrels). Each load is solved with the rail's full current (worst case)."""
    rails = irdropx.rails_from_circuit(res.board.circuit)
    if not rails:
        return "NOT RUN: no power rail with a declared current, a source part and an IC load was found in this circuit."
    if not irdropx.available():
        return "NOT RUN: kicad-irdrop is not installed in the KiCad Python (see docs/DEPENDENCIES.md)."
    import json
    data = irdropx.run_irdrop(res.pcb_path, rails, grid=pol.irdrop_grid_mm, budget_pct=pol.irdrop_budget_pct)
    (out / "irdrop.json").write_text(json.dumps(data, indent=1))
    files["irdrop.json"] = out / "irdrop.json"
    L = [f"Raster {pol.irdrop_grid_mm:g} mm, budget {pol.irdrop_budget_pct:g} % of the rail voltage. Current assumed = the rail's declared current through each load.", "",
         "| Rail | Source | Loads | A | Worst drop (mV) | R_eff (mOhm) | Over budget |", "|--|--|--|--|--|--|--|"]
    for r in data["rails"]:
        L.append(f"| {r['net']} | {r['source']} | {', '.join(r['loads'])} | {r['current_a']:g} | {r['worst_drop_mv']:.2f} | {r['r_eff_mohm']:.2f} | "
                 f"{'**YES**' if r.get('over_budget') else 'no' if r.get('over_budget') is not None else 'n/a'} |")
        if r.get("unreached"):
            L.append(f"\n**Not connected to the source in copper: {', '.join(r['unreached'])}**")
    return "\n".join(L)
