"""Catalogue package text -> KiCad footprint, found by SEARCHING KiCad's installed footprint library (no size/name tables here).

A footprint is returned only when exactly one non-variant library entry matches; zero or several matches return None (the caller then needs an explicit
footprint from the designer). Variants (hand-solder, pad-size suffixes) are excluded by name, not by a list of sizes."""
from __future__ import annotations

import re
from functools import lru_cache

from ..footprints import kicad_dir

_VARIANT = re.compile(r"hand|pad\d|_pad|mask|drawing|modified", re.I)


@lru_cache(maxsize=64)
def _names(lib: str) -> tuple[str, ...]:
    root = kicad_dir()
    d = root / f"{lib}.pretty" if root else None
    return tuple(sorted(p.stem for p in d.glob("*.kicad_mod"))) if d and d.is_dir() else ()


def find(lib: str, pattern: str) -> str | None:
    """'Library:Name' of the single non-variant footprint in `lib` whose name fully matches regex `pattern`; else None."""
    hits = [n for n in _names(lib) if re.fullmatch(pattern, n) and not _VARIANT.search(n)]
    return f"{lib}:{hits[0]}" if len(hits) == 1 else None


def chip(lib: str, prefix: str, code: str) -> str | None:
    """Imperial chip code ('0603') -> e.g. Capacitor_SMD:C_0603_1608Metric, by searching for `{prefix}_{code}_*Metric`."""
    return find(lib, rf"{re.escape(prefix)}_{re.escape(code)}_\d+Metric")


def electrolytic(package: str) -> str | None:
    """'SMD,D10xL10.2mm': the land pattern follows the can DIAMETER; among that diameter's library variants take the nearest height at or above the part's."""
    m = re.fullmatch(r"SMD,\s*D([\d.]+)\s*x\s*L([\d.]+)\s*mm", package.strip(), re.I)
    if not m:
        return None
    d, h = m.group(1), float(m.group(2))
    cand = sorted((float(mm.group(1)), n) for n in _names("Capacitor_SMD") if (mm := re.fullmatch(rf"CP_Elec_{re.escape(d)}x([\d.]+)", n)))
    above = [c for c in cand if c[0] >= h - 1e-9]
    pick = above[0] if above else None
    return f"Capacitor_SMD:{pick[1]}" if pick else None


def diode(package: str) -> str | None:
    """'SMB(DO-214AA)' / 'DO-214AC(SMA)': the SMx token names the library footprint D_SMx."""
    m = re.search(r"\b(SM[ABC])\b", package, re.I)
    return find("Diode_SMD", rf"D_{m.group(1).upper()}") if m else None
