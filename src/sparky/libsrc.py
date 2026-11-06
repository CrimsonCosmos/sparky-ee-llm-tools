"""Symbol + footprint for the EXACT orderable part, imported from its LCSC/EasyEDA record with the open-source `easyeda2kicad` command.

Why: the footprint must match the chip actually bought. Matching orderable MPNs to KiCad library names is guesswork (ordering suffixes, variants).
`easyeda2kicad` is AGPL-3.0, so it is only ever run as a separate process - never imported or bundled.
Checked against KiCad's own symbols (TPS5430DDA, TPS563201): pin numbers and functions agree; two vendor quirks are corrected on import:
the courtyard (see kicad_mod.parse_footprint) and pin electrical types (EasyEDA symbols are all `unspecified`; we infer them from pin names and mark them inferred)."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import data_dir

_TYPE_BY_NAME = [(r"^(GND|PGND|AGND|DGND|VSS|V-|PAD|EP|PowerPAD)\w*$", "power_in"), (r"^(VIN|VCC|VDD|V\+|VS|VBAT|VBUS|PVIN|AVIN|VI)\d*$", "power_in"),
                 (r"^(VOUT|VO|OUT)\d*$", "power_out"), (r"^(NC|DNC)\d*$", "no_connect")]


@dataclass(frozen=True)
class PartFiles:
    lcsc: str
    symbol_file: Path
    symbol_name: str
    footprint_file: Path


def _exe() -> str:
    for cand in (shutil.which("easyeda2kicad"), str(Path(sys.executable).parent / "easyeda2kicad")):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("easyeda2kicad not found: pip install easyeda2kicad (AGPL-3.0; used only as an external command)")


def lib_dir() -> Path:
    d = data_dir() / "partlib"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _infer_types(text: str) -> str:
    """Replace each `(pin unspecified ...` by a type inferred from that pin's name (layout-agnostic: the name follows within the same pin form)."""
    def fix(m: re.Match) -> str:
        for pat, typ in _TYPE_BY_NAME:
            if re.match(pat, m.group(2), re.I):
                return f"(pin {typ}{m.group(1)}(name \"{m.group(2)}\""
        return f"(pin passive{m.group(1)}(name \"{m.group(2)}\""
    return re.sub(r'\(pin unspecified((?:(?!\(pin ).)*?)\(name "([^"]*)"', fix, text, flags=re.S)


def _mark_inferred(text: str) -> str:
    return text.replace('(property "Reference"', '(property "PinTypesInferred" "yes"\n      (at 0 0 0)\n      (effects (font (size 1.27 1.27)) hide)\n    )\n    (property "Reference"', 1)


def ensure_part(lcsc: str, timeout: int = 120) -> PartFiles:
    """Import (once, cached under <data>/partlib/<lcsc>/) and return the files. Raises with the tool's message on failure."""
    d = lib_dir() / lcsc
    sym, pretty = d / "part.kicad_sym", d / "part.pretty"
    if not (sym.exists() and any(pretty.glob("*.kicad_mod"))):
        d.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([_exe(), "--symbol", "--footprint", f"--lcsc_id={lcsc}", "--output", str(d / "part"), "--overwrite"],
                           capture_output=True, text=True, timeout=timeout)
        if not (sym.exists() and any(pretty.glob("*.kicad_mod"))):
            raise RuntimeError(f"easyeda2kicad failed for {lcsc}: {(r.stdout + r.stderr).strip()[-400:]}")
    if "PinTypesInferred" not in sym.read_text():                      # idempotent: also applies to files already in the cache
        sym.write_text(_mark_inferred(_infer_types(sym.read_text())))
    name = re.search(r'\(symbol "([^"]+)"', sym.read_text()).group(1)
    return PartFiles(lcsc, sym, name, next(pretty.glob("*.kicad_mod")))
