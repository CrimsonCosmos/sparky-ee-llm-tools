"""Adapters for two open-source KiCad companions, run as external processes in an interpreter that can import pcbnew (loose coupling, nothing re-implemented):
InteractiveHtmlBom (MIT, `generate_interactive_bom`: a clickable placement/BOM page for assembly and debug) and KiKit (MIT, `kikit panelize`: panels with
rails, tabs, V-cuts, tooling holes and fiducials). Each is found at runtime (env var, then PATH) and reported NOT RUN when absent or not working."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _exe(env: str, name: str) -> str | None:
    return os.environ.get(env) or shutil.which(name)


def ibom_available() -> bool:
    return _exe("SPARKY_IBOM", "generate_interactive_bom") is not None


def kikit_available() -> bool:
    exe = _exe("SPARKY_KIKIT", "kikit")
    if not exe:
        return False
    r = subprocess.run([exe, "--version"], capture_output=True, text=True)
    return r.returncode == 0


def interactive_bom(pcb: str | Path, outdir: str | Path, name: str = "ibom") -> Path:
    """The interactive BOM page for a board (headless: no browser, no display)."""
    exe = _exe("SPARKY_IBOM", "generate_interactive_bom")
    if not exe:
        raise RuntimeError("InteractiveHtmlBom (generate_interactive_bom) not found; `pip install InteractiveHtmlBom` into the KiCad Python (SPARKY_IBOM)")
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ibom_") as td:
        src = Path(td) / Path(pcb).name
        shutil.copy(pcb, src)
        r = subprocess.run([exe, "--no-browser", "--dest-dir", str(Path(td) / "o"), "--name-format", name, str(src)], capture_output=True, text=True, timeout=300,
                           env={**os.environ, "INTERACTIVE_HTML_BOM_NO_DISPLAY": "1"})
        page = Path(td) / "o" / f"{name}.html"
        if not page.exists():
            raise RuntimeError(f"InteractiveHtmlBom failed: {(r.stderr or r.stdout).strip()[-300:]}")
        dest = out / f"{name}.html"
        shutil.copy(page, dest)
    return dest


def panelize(pcb: str | Path, out_pcb: str | Path, *, rows: int, cols: int, space_mm: float, tab_width_mm: float, cut: str = "vcuts",
             frame_width_mm: float | None = None, frame_space_mm: float | None = None, extra: list[str] | None = None) -> Path:
    """A rows x cols panel of the board via `kikit panelize` (layout grid, fixed tabs, `cut` = vcuts | mousebites). Frame, tooling and fiducial options go in
    `extra` as raw kikit arguments (e.g. ['--tooling', '3hole; hoffset: 2.5mm; voffset: 2.5mm; size: 1.152mm']): their values are the fab's, not ours."""
    exe = _exe("SPARKY_KIKIT", "kikit")
    if not exe or not kikit_available():
        raise RuntimeError("KiKit not usable (needs `pip install kikit` into an interpreter with pcbnew and wxPython); set SPARKY_KIKIT")
    cmd = [exe, "panelize", "--layout", f"grid; rows: {rows}; cols: {cols}; space: {space_mm}mm", "--tabs", f"fixed; width: {tab_width_mm}mm", "--cuts", cut]
    if frame_width_mm:
        cmd += ["--framing", f"frame; width: {frame_width_mm}mm; space: {frame_space_mm or 0}mm"]
    cmd += list(extra or []) + [str(pcb), str(out_pcb)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not Path(out_pcb).exists():
        raise RuntimeError(f"kikit panelize failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return Path(out_pcb)
