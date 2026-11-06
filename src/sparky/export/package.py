"""Write the manufacturing + review package for a built board. Gerbers, drills, positions, IPC-2581 and the board image are produced by KiCad."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .. import kicad_backend
from ..pcb.flow import BuildResult
from . import bom as bomx
from . import kicad
from .report import kicad_drc_section, render_report


def write_package(res: BuildResult, outdir: str | Path, project: str, extra: dict | None = None) -> dict[str, Path]:
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
