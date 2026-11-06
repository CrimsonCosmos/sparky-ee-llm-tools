"""Footprints come from KiCad's footprint libraries ('Library:Name') or from a .kicad_mod file (an imported vendor part). We generate none."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .model import Footprint


def kicad_dir() -> Path | None:
    """Root of KiCad's *.pretty libraries (discovered per OS in toolchain.py)."""
    from .toolchain import kicad_library_dir
    d = kicad_library_dir("footprints")
    return Path(d) if d and Path(d).is_dir() and any(Path(d).glob("*.pretty")) else None


def kicad_available() -> bool:
    return kicad_dir() is not None


def kicad_mod_path(lib_id: str) -> Path | None:
    """'Library:Name' -> .kicad_mod path (None if absent)."""
    root = kicad_dir()
    if root is None or ":" not in lib_id:
        return None
    lib, name = lib_id.split(":", 1)
    p = root / f"{lib}.pretty" / f"{name}.kicad_mod"
    return p if p.is_file() else None


@lru_cache(maxsize=2048)
def _load(spec: str, root: str) -> Footprint:
    from .kicad_mod import load_file
    if spec.endswith(".kicad_mod") and Path(spec).is_file():            # imported vendor footprint: the file is the source of truth
        fp = load_file(spec)
        fp.name, fp.source = Path(spec).stem, f"file:{spec}"
        return fp
    lib_id = spec[len("sparky:"):] if spec.startswith("sparky:") else spec
    p = kicad_mod_path(lib_id)
    if p is None:
        raise KeyError(f"KiCad footprint '{spec}' not found under {root or 'any KiCad library (is KiCad installed?)'}")
    fp = load_file(p)
    fp.name, fp.source, fp.lib_id = lib_id.split(":", 1)[1], f"kicad:{lib_id}", lib_id
    return fp


def resolve_footprint(spec) -> Footprint:
    """Footprint object | 'Library:Name' | path to a .kicad_mod -> Footprint (courtyard already extended to enclose the pads)."""
    if isinstance(spec, Footprint):
        return spec
    return _load(str(spec), str(kicad_dir() or ""))
