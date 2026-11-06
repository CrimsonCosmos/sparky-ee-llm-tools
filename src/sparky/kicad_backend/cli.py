"""KiCad (>= 8) through `kicad-cli`: DRC, zone fill, Gerber/drill/position/IPC-2581 export. No pcbnew scripting, no version-7 branches."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..toolchain import kicad_cli, kicad_has, kicad_version as _version


def find_kicad_cli() -> str | None:
    return kicad_cli()


def available() -> bool:
    return kicad_cli() is not None and kicad_has("pcb_drc")


def kicad_version() -> str | None:
    v = _version()
    return ".".join(map(str, v)) if v else None


def capabilities() -> dict:
    return {"kicad_cli": kicad_cli(), "version": kicad_version(), "cli_drc": kicad_has("pcb_drc"), "sch_erc": kicad_has("sch_erc"),
            "ipc2581": kicad_has("ipc2581")}


def write_kicad_pro(path: str | Path, rules=None, name: str | None = None) -> Path:
    """Write a minimal .kicad_pro so KiCad's DRC uses OUR fab rules instead of its defaults.

    `rules` is a sparky `pcb.rules.Rules` (or any object with the same attribute names)."""
    path = Path(path)
    g = (lambda k, d: getattr(rules, k, d)) if rules is not None else (lambda k, d: d)
    clr, trk, via, drl = g("clearance", 0.2), g("track", 0.2), g("via_dia", 0.6), g("via_drill", 0.3)
    pro = {
        "board": {"design_settings": {
            "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2, "copper_text_size_h": 1.5,
                         "copper_text_size_v": 1.5, "copper_text_thickness": 0.3, "other_line_width": 0.1,
                         "silk_line_width": 0.12, "silk_text_size_h": 0.8, "silk_text_size_v": 0.8,
                         "silk_text_thickness": 0.12},
            "rules": {"min_clearance": clr, "min_track_width": g("min_track", 0.127),
                      "min_via_diameter": max(g("min_drill", 0.3) + 2 * g("min_annular", 0.13), 0.0),
                      "min_through_hole_diameter": g("min_drill", 0.3), "min_via_annular_width": g("min_annular", 0.13),
                      "min_hole_to_hole": g("hole_to_hole", 0.35), "min_copper_edge_clearance": g("edge_clearance", 0.4),
                      "min_hole_clearance": 0.25, "min_microvia_diameter": 0.2, "min_microvia_drill": 0.1,
                      "min_silk_clearance": 0.0, "min_text_height": 0.8, "min_text_thickness": 0.08,
                      "min_resolved_spokes": 2, "min_connection_width": 0.0, "solder_mask_to_copper_clearance": 0.0},
            # footprints come from the project's own generator, not a KiCad library table
            "rule_severities": {"lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore"},
        }},
        "meta": {"filename": name or path.name, "version": 1},
        "net_settings": {"classes": [{"name": "Default", "clearance": clr, "track_width": trk, "via_diameter": via,
                                      "via_drill": drl, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                                      "diff_pair_width": 0.2, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                                      "wire_width": 6, "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
                                      "schematic_color": "rgba(0, 0, 0, 0.000)"}],
                         "meta": {"version": 3}, "net_colors": None},
    }
    path.write_text(json.dumps(pro, indent=2))
    return path


# ---------------------------------------------------------------------------------------------------- DRC
@dataclass
class DrcReport:
    version: str | None = None
    violations: list[dict] = field(default_factory=list)     # {type, description, severity, rule, items:[{x,y,desc}]}
    unconnected: list[dict] = field(default_factory=list)
    footprint_errors: list[dict] = field(default_factory=list)
    refilled: bool = True
    raw: str = ""

    @property
    def by_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.violations:
            out[v["type"]] = out.get(v["type"], 0) + 1
        return out

    @property
    def errors(self) -> list[dict]:
        return [v for v in self.violations if v["severity"] == "error"]

    @property
    def warnings(self) -> list[dict]:
        return [v for v in self.violations if v["severity"] != "error"]

    @property
    def clean(self) -> bool:
        return not self.errors and not self.unconnected and not self.footprint_errors

    def summary(self) -> str:
        return (f"{len(self.errors)} errors, {len(self.warnings)} warnings, {len(self.unconnected)} unconnected, "
                f"{len(self.footprint_errors)} footprint errors")

    def to_dict(self) -> dict:
        return {"tool": "kicad-cli pcb drc", "version": self.version, "zones_refilled": self.refilled, "clean": self.clean,
                "counts": {"errors": len(self.errors), "warnings": len(self.warnings), "unconnected": len(self.unconnected),
                           "footprint_errors": len(self.footprint_errors)},
                "by_type": self.by_type, "violations": self.violations, "unconnected_items": self.unconnected,
                "footprint_errors": self.footprint_errors}


def parse_drc_json(text: str) -> DrcReport:
    """`kicad-cli pcb drc --format json` report -> DrcReport."""
    d = json.loads(text)

    def norm(v: dict) -> dict:
        return {"type": v.get("type", ""), "description": v.get("description", ""), "severity": v.get("severity", "error"), "rule": "",
                "items": [{"x": (it.get("pos") or {}).get("x", 0.0), "y": (it.get("pos") or {}).get("y", 0.0), "desc": it.get("description", "")}
                          for it in v.get("items", [])]}
    rep = DrcReport(raw=text, version=d.get("kicad_version"))
    rep.violations = [norm(v) for v in d.get("violations", [])]
    rep.unconnected = [norm(v) for v in d.get("unconnected_items", [])]
    rep.footprint_errors = [norm(v) for v in d.get("schematic_parity", [])]
    return rep


def _need_cli() -> str:
    exe = kicad_cli()
    if not exe or not kicad_has("pcb_drc"):
        raise RuntimeError("KiCad >= 8 (kicad-cli with `pcb drc`) is required; install KiCad 10 from kicad.org")
    return exe


def run_drc(pcb_path: str | Path, refill: bool = True, rules=None, report_path: str | Path | None = None,
            save_to: str | Path | None = None) -> DrcReport:
    """KiCad DRC. Zones are refilled first (`--refill-zones`); with `save_to` the refilled board is written there for plotting."""
    exe = _need_cli()
    pcb = Path(pcb_path).resolve()
    with tempfile.TemporaryDirectory(prefix="kidrc_") as td:
        work = Path(td) / pcb.name
        shutil.copy(pcb, work)
        pro = pcb.with_suffix(".kicad_pro")
        shutil.copy(pro, work.with_suffix(".kicad_pro")) if pro.exists() else write_kicad_pro(work.with_suffix(".kicad_pro"), rules)
        out = Path(td) / "drc.json"
        cmd = [exe, "pcb", "drc", "--format", "json", "--severity-all", "--all-track-errors", "--output", str(out)]
        if refill:
            cmd.append("--refill-zones")
        if save_to:
            cmd.append("--save-board")
        r = subprocess.run(cmd + [str(work)], capture_output=True, text=True, timeout=900)
        if not out.exists():
            raise RuntimeError(f"kicad-cli pcb drc failed (rc={r.returncode}): {(r.stderr or r.stdout).strip()[-600:]}")
        rep = parse_drc_json(out.read_text(errors="replace"))
        if save_to:
            shutil.copy(work, save_to)
    rep.refilled = refill
    if report_path:
        Path(report_path).write_text(json.dumps(rep.to_dict(), indent=1))
    return rep


# ---------------------------------------------------------------------------------------------------- manufacturing export
def gerber_layers(n_copper: int) -> list[str]:
    """Untranslated KiCad layer names for a fab set. Works for 2/4/6/... layer boards."""
    cu = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, n_copper - 1)] + ["B.Cu"]
    return cu + ["F.Paste", "B.Paste", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask", "Edge.Cuts"]


def _copper_count(pcb: Path) -> int:
    txt = pcb.read_text(errors="replace")
    names = set(re.findall(r'"((?:F|B|In\d+)\.Cu)"', txt.split("(footprint", 1)[0]))
    return max(2, len(names))


def _cli(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    r = subprocess.run([_need_cli(), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"kicad-cli {' '.join(args[:3])} failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-600:]}")
    return r


def export_fab(pcb_path: str | Path, outdir: str | Path, project: str | None = None, make_zip: bool = True) -> dict[str, Path]:
    """Gerbers + job file, Excellon drills (PTH/NPTH + maps), position CSV, IPC-2581, board SVG - all produced by kicad-cli from a zone-refilled copy."""
    pcb = Path(pcb_path).resolve()
    out = Path(outdir)
    gdir = out / "gerber"
    gdir.mkdir(parents=True, exist_ok=True)
    project = project or pcb.stem
    files: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="kifab_") as td:
        src = Path(td) / f"{project}.kicad_pcb"
        run_drc(pcb, refill=True, save_to=src)                      # refilled copy (zones filled by KiCad itself)
        n = _copper_count(pcb)
        _cli("pcb", "export", "gerbers", "-o", str(gdir) + "/", "-l", ",".join(gerber_layers(n)), "--subtract-soldermask", str(src))
        _cli("pcb", "export", "drill", "-o", str(gdir) + "/", "--format", "excellon", "--excellon-separate-th", "--excellon-units", "mm",
             "--excellon-zeros-format", "decimal", "--generate-map", "--map-format", "gerberx2", str(src))
        pos = out / "kicad_positions.csv"
        _cli("pcb", "export", "pos", "-o", str(pos), "--format", "csv", "--units", "mm", "--side", "both", str(src))
        if kicad_has("ipc2581"):
            ipc = out / f"{project}.ipc2581.xml"
            _cli("pcb", "export", "ipc2581", "-o", str(ipc), str(src))
            files["ipc2581"] = ipc
        svg = out / "board.svg"
        _cli("pcb", "export", "svg", "-o", str(svg), "-l", ",".join(gerber_layers(n)[:1] + ["Edge.Cuts"]), "--page-size-mode", "2", "--exclude-drawing-sheet", str(src))
        if svg.exists():
            files["svg"] = svg
        step = out / f"{project}.step"                              # mechanical hand-off (board body + pads/tracks; 3D models if footprints carry them)
        try:
            _cli("pcb", "export", "step", "-o", str(step), "--force", "--include-tracks", "--include-pads", "--include-zones", str(src))
            files["step"] = step
        except RuntimeError:
            pass
        shutil.copy(src, out / f"{project}.kicad_pcb")              # the routed + filled board is the deliverable
        files["kicad_pcb"] = out / f"{project}.kicad_pcb"
    for p in sorted(gdir.iterdir()):
        files[f"gerber:{p.name}"] = p
    if pos.exists():
        files["positions"] = pos
    if make_zip:
        zp = out / f"{project}_kicad_fab.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(gdir.iterdir()):
                z.write(p, p.name)
        files["fab_zip"] = zp
    return files
