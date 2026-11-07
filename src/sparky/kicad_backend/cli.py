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


def write_kicad_pro(path: str | Path, rules, name: str | None = None, net_classes: list[dict] | None = None) -> Path:
    """Write a minimal .kicad_pro so KiCad's DRC uses OUR fab rules instead of its defaults.

    `rules` is a sparky `pcb.rules.Rules` (or any object with the same attribute names). `net_classes`: [{name, track_width, clearance, nets: [...]}] - extra classes
    (wider tracks for current, larger spacing for voltage); the router and KiCad's DRC both honour them."""
    path = Path(path)
    g = lambda k: getattr(rules, k)            # every engineering value comes from the Rules object: no fallbacks here
    clr, trk, via, drl = g("clearance"), g("track"), g("via_dia"), g("via_drill")
    pro = {
        "board": {"design_settings": {
            # KiCad's own cosmetic defaults (graphics/text sizes): not engineering values
            "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2, "copper_text_size_h": 1.5,
                         "copper_text_size_v": 1.5, "copper_text_thickness": 0.3, "other_line_width": 0.1,
                         "silk_line_width": 0.12, "silk_text_size_h": 0.8, "silk_text_size_v": 0.8,
                         "silk_text_thickness": 0.12},
            "rules": {"min_clearance": clr, "min_track_width": g("min_track"),
                      "min_via_diameter": g("min_drill") + 2 * g("min_annular"),
                      "min_through_hole_diameter": g("min_drill"), "min_via_annular_width": g("min_annular"),
                      "min_hole_to_hole": g("hole_to_hole"), "min_copper_edge_clearance": g("edge_clearance"),
                      "min_hole_clearance": g("hole_clearance"), "min_microvia_diameter": g("microvia_dia"), "min_microvia_drill": g("microvia_drill"),
                      "min_silk_clearance": 0.0, "min_text_height": 0.8, "min_text_thickness": 0.08,
                      "min_resolved_spokes": 2, "min_connection_width": 0.0, "solder_mask_to_copper_clearance": 0.0},
            # footprints come from the project's own generator, not a KiCad library table
            "rule_severities": {"lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore"},
        }},
        "meta": {"filename": name or path.name, "version": 1},
        "net_settings": {"classes": [{"name": "Default", "clearance": clr, "track_width": trk, "via_diameter": via,
                                      "via_drill": drl, "diff_pair_gap": clr, "diff_pair_via_gap": clr,
                                      "diff_pair_width": trk, "microvia_diameter": g("microvia_dia"), "microvia_drill": g("microvia_drill"),
                                      "wire_width": 6, "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
                                      "schematic_color": "rgba(0, 0, 0, 0.000)"}],
                         "meta": {"version": 3}, "net_colors": None},
    }
    ns = pro["net_settings"]
    for nc in net_classes or []:
        ns["classes"].append({**ns["classes"][0], "name": nc["name"], "track_width": nc["track_width"], "clearance": nc["clearance"]})
    ns["netclass_patterns"] = [{"netclass": nc["name"], "pattern": n} for nc in net_classes or [] for n in nc["nets"]]
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
    tool: str = "kicad-cli pcb drc"

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
        return {"tool": self.tool, "version": self.version, "zones_refilled": self.refilled, "clean": self.clean,
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


def _need_rules(rules):
    if rules is None:
        raise ValueError("run_drc needs `rules` (or a .kicad_pro beside the board): there are no built-in fab limits")
    return rules


def run_drc(pcb_path: str | Path, refill: bool = True, rules=None, report_path: str | Path | None = None,
            save_to: str | Path | None = None) -> DrcReport:
    """KiCad DRC. Zones are refilled first (`--refill-zones`); with `save_to` the refilled board is written there for plotting."""
    exe = _need_cli()
    pcb = Path(pcb_path).resolve()
    with tempfile.TemporaryDirectory(prefix="kidrc_") as td:
        work = Path(td) / pcb.name
        shutil.copy(pcb, work)
        pro = pcb.with_suffix(".kicad_pro")
        dru = pcb.with_suffix(".kicad_dru")
        if dru.exists():                                  # custom rules (length/skew constraints) travel with the board
            shutil.copy(dru, work.with_suffix(".kicad_dru"))
        shutil.copy(pro, work.with_suffix(".kicad_pro")) if pro.exists() else write_kicad_pro(work.with_suffix(".kicad_pro"), _need_rules(rules))
        out = Path(td) / "drc.json"
        cmd = [exe, "pcb", "drc", "--format", "json", "--severity-all", "--all-track-errors", "--output", str(out)]
        sch = pcb.with_suffix(".kicad_sch")
        if sch.exists():                                   # the schematic beside the board: KiCad also checks board <-> schematic parity
            for f in (sch, pcb.parent / "sym-lib-table", pcb.parent / "fp-lib-table"):
                if f.exists():
                    shutil.copy(f, Path(td) / f.name)
            cmd.append("--schematic-parity")
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
def run_erc(sch_path: str | Path, report_path: str | Path | None = None) -> DrcReport:
    """KiCad's own electrical rules check of a schematic (`kicad-cli sch erc`), with the project's library tables beside it."""
    exe = kicad_cli()
    if not exe or not kicad_has("sch_erc"):
        raise RuntimeError("KiCad >= 8 (kicad-cli with `sch erc`) is required")
    sch = Path(sch_path).resolve()
    with tempfile.TemporaryDirectory(prefix="kierc_") as td:
        for f in [sch] + [sch.parent / n for n in ("sym-lib-table", "fp-lib-table", sch.stem + ".kicad_pro")]:
            if f.exists():
                shutil.copy(f, Path(td) / f.name)
        pro = Path(td) / (sch.stem + ".kicad_pro")
        if not pro.exists():                               # KiCad reads the project library tables only when a project file sits beside the schematic
            pro.write_text(json.dumps({"meta": {"filename": pro.name, "version": 1}}))
        out = Path(td) / "erc.json"
        r = subprocess.run([exe, "sch", "erc", "--format", "json", "--severity-all", "--output", str(out), str(Path(td) / sch.name)],
                           capture_output=True, text=True, timeout=300)
        if not out.exists():
            raise RuntimeError(f"kicad-cli sch erc failed (rc={r.returncode}): {(r.stderr or r.stdout).strip()[-600:]}")
        d = json.loads(out.read_text(errors="replace"))
    rep = DrcReport(version=d.get("kicad_version"), raw=json.dumps(d))
    for sheet in d.get("sheets", []):
        for v in sheet.get("violations", []):
            rep.violations.append({"type": v.get("type"), "description": v.get("description"), "severity": v.get("severity"),
                                   "items": [{"desc": i.get("description"), "x": i.get("pos", {}).get("x"), "y": i.get("pos", {}).get("y")} for i in v.get("items", [])]})
    rep.refilled = False
    rep.tool = "kicad-cli sch erc"
    if report_path:
        Path(report_path).write_text(json.dumps(rep.to_dict(), indent=1))
    return rep


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


# ---------------------------------------------------------------------------------------------------- review / test / mechanical exports
def export_extras(pcb_path: str | Path, outdir: str | Path, project: str, sch_path: str | Path | None = None) -> tuple[dict[str, Path], dict[str, str]]:
    """Everything else an engineer asks KiCad for, all produced by kicad-cli: review plots (layer PDF, 3D renders, schematic PDF), the e-test netlist
    (IPC-D-356), ODB++ and GenCAD for fabs that take them, GLB / VRML / DXF outline for the mechanical team, and the board statistics.
    Returns (files, problems): a step that fails is reported by name instead of being skipped silently."""
    pcb = Path(pcb_path).resolve()
    out = Path(outdir)
    for d in ("review", "test", "mechanical"):
        (out / d).mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    problems: dict[str, str] = {}
    n = _copper_count(pcb)
    copper = gerber_layers(n)
    plots = [l for l in copper if l.endswith(".Cu")] + ["F.Silkscreen", "B.Silkscreen", "F.Mask", "B.Mask", "F.Paste", "B.Paste", "F.Fab", "B.Fab", "F.Courtyard", "B.Courtyard"]
    steps = [                                       # (file, kicad-cli subcommand, options)
        ("review/layers.pdf", ["pcb", "export", "pdf"], ["--mode-multipage", "-l", ",".join(plots + ["Edge.Cuts"]), "--cl", "Edge.Cuts", "--sketch-pads-on-fab-layers", "--check-zones"]),
        ("review/render_top.png", ["pcb", "render"], ["--side", "top", "--quality", "basic", "--width", "1600", "--height", "1200"]),
        ("review/render_bottom.png", ["pcb", "render"], ["--side", "bottom", "--quality", "basic", "--width", "1600", "--height", "1200"]),
        ("test/netlist.d356", ["pcb", "export", "ipcd356"], []),
        ("test/board.odb", ["pcb", "export", "odb"], []),
        ("test/board.cad", ["pcb", "export", "gencad"], []),
        ("mechanical/board.glb", ["pcb", "export", "glb"], ["--force", "--include-tracks", "--include-pads", "--include-zones"]),
        ("mechanical/board.wrl", ["pcb", "export", "vrml"], []),
        ("mechanical/outline.dxf", ["pcb", "export", "dxf"], ["-l", "Edge.Cuts"]),
        ("review/stats.json", ["pcb", "export", "stats"], ["--format", "json"]),
    ]
    with tempfile.TemporaryDirectory(prefix="kiextra_") as td:
        work = Path(td) / pcb.name
        shutil.copy(pcb, work)
        for rel, cmd, opts in steps:
            dest = out / rel
            try:
                if cmd[-1] == "dxf":                       # kicad-cli takes a directory here and names the file after the board and layer
                    tmpd = Path(td) / "dxf"
                    _cli(*cmd, "-o", str(tmpd), *opts, str(work))
                    got = sorted(tmpd.glob("*.dxf"))
                    if got:
                        shutil.copy(got[0], dest)
                else:
                    _cli(*cmd, "-o", str(dest), *opts, str(work))
            except RuntimeError as e:
                problems[rel] = str(e)[-200:]
                continue
            if dest.exists():
                files[rel] = dest
            else:
                problems[rel] = "kicad-cli reported success but wrote no file"
        if sch_path and Path(sch_path).exists():
            sch = Path(sch_path).resolve()
            for f in [sch] + [sch.parent / nme for nme in ("sym-lib-table", "fp-lib-table", sch.stem + ".kicad_pro")]:
                if f.exists():
                    shutil.copy(f, Path(td) / f.name)
            dest = out / "review" / "schematic.pdf"
            try:
                _cli("sch", "export", "pdf", "-o", str(dest), str(Path(td) / sch.name))
                files["review/schematic.pdf"] = dest
            except RuntimeError as e:
                problems["review/schematic.pdf"] = str(e)[-200:]
    return files, problems


def board_stats(path: str | Path) -> dict:
    """The numbers of `kicad-cli pcb export stats --format json` (strings with units are kept as KiCad prints them)."""
    return json.loads(Path(path).read_text())
