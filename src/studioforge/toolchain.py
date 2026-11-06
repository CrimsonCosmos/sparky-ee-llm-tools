"""Locate external tools on Windows / macOS / Linux. Nothing here is reimplemented - we only find and version-check.

Override any location with an environment variable (SPARKY_KICAD_CLI, SPARKY_JAVA, SPARKY_NGSPICE, SPARKY_KICAD_PYTHON,
KICAD_FOOTPRINT_DIR, KICAD_SYMBOL_DIR)."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

_WIN = sys.platform.startswith("win")
_MAC = sys.platform == "darwin"


def _first_existing(cands) -> str | None:
    for c in cands:
        if c and Path(c).exists():
            return str(c)
    return None


def _which_or(name: str, env: str, extra=()) -> str | None:
    if os.environ.get(env):
        return os.environ[env]
    return shutil.which(name) or _first_existing(extra)


def _kicad_roots() -> list[str]:
    if _WIN:
        pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        return sorted((r for p in pf for r in glob.glob(str(Path(p) / "KiCad" / "*"))), reverse=True)
    if _MAC:
        return ["/Applications/KiCad/KiCad.app/Contents"]
    return ["/usr", "/usr/local"]


@lru_cache(maxsize=None)
def kicad_cli() -> str | None:
    extra = []
    for r in _kicad_roots():
        extra += [Path(r) / "bin" / "kicad-cli.exe", Path(r) / "MacOS" / "kicad-cli", Path(r) / "bin" / "kicad-cli"]
    return _which_or("kicad-cli", "SPARKY_KICAD_CLI", extra)


@lru_cache(maxsize=None)
def kicad_version() -> tuple[int, int, int] | None:
    exe = kicad_cli()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def kicad_has(feature: str) -> bool:
    """Feature gates that moved between KiCad releases (do not hardcode a version number at call sites)."""
    v = kicad_version()
    if v is None:
        return False
    return {"pcb_drc": v >= (8, 0), "sch_erc": v >= (8, 0), "ipc2581": v >= (8, 0), "ipc_api": v >= (9, 0)}.get(feature, False)


@lru_cache(maxsize=None)
def kicad_python() -> str | None:
    """An interpreter that can `import pcbnew` (KiCad's bundled one on Windows/macOS, system python on Linux)."""
    if os.environ.get("SPARKY_KICAD_PYTHON"):
        return os.environ["SPARKY_KICAD_PYTHON"]
    cands: list[str | None] = []
    for r in _kicad_roots():
        if _WIN:
            cands.append(str(Path(r) / "bin" / "python.exe"))
        elif _MAC:
            cands += glob.glob(str(Path(r) / "Frameworks" / "Python.framework" / "Versions" / "*" / "bin" / "python3"))
    cands += [shutil.which("python3"), shutil.which("python"), sys.executable]
    for c in cands:
        if not c or not Path(c).exists():
            continue
        try:
            if subprocess.run([c, "-c", "import pcbnew"], capture_output=True, timeout=60).returncode == 0:
                return c
        except Exception:
            continue
    return None


def kicad_library_dir(kind: str) -> str | None:
    """kind: 'footprints' | 'symbols'. Honors KICAD_FOOTPRINT_DIR / KICAD_SYMBOL_DIR, then per-OS install locations."""
    env = {"footprints": "KICAD_FOOTPRINT_DIR", "symbols": "KICAD_SYMBOL_DIR"}[kind]
    if os.environ.get(env):
        return os.environ[env]
    cands: list[Path] = []
    for r in _kicad_roots():
        base = Path(r)
        cands += [base / "share" / "kicad" / kind, base / "SharedSupport" / kind, base / kind]
    return _first_existing(cands)


def java() -> str | None:
    return _which_or("java", "SPARKY_JAVA")


def ngspice() -> str | None:
    extra = [r"C:\Program Files\Spice64\bin\ngspice_con.exe"] if _WIN else ["/opt/homebrew/bin/ngspice", "/usr/local/bin/ngspice"]
    return _which_or("ngspice", "SPARKY_NGSPICE", extra)


def pdftotext() -> str | None:
    return shutil.which("pdftotext")


def report() -> dict[str, str | None]:
    v = kicad_version()
    return {"kicad-cli": kicad_cli(), "kicad version": ".".join(map(str, v)) if v else None, "kicad python (pcbnew)": kicad_python(),
            "footprint libs": kicad_library_dir("footprints"), "symbol libs": kicad_library_dir("symbols"), "java": java(),
            "ngspice": ngspice(), "pdftotext": pdftotext(), "claude": shutil.which("claude"), "platform": sys.platform}
