"""SI value <-> engineering-notation strings used in KiCad 'Value' fields."""
from __future__ import annotations

import re

_SCALE = ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"))
_PARSE = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}


def fmt_si(v: float) -> str:
    """4700 -> '4.7k', 1e-7 -> '100n'."""
    if v == 0:
        return "0"
    for scale, suf in _SCALE:
        if abs(v) >= scale * 0.9999:
            return f"{v / scale:.3g}{suf}"
    return f"{v:.3g}"


def parse_si(s: str) -> float:
    s = re.sub(r"[FHVAWΩ]|ohm", "", s.strip(), flags=re.I) if not re.fullmatch(r"[\d.]+[pnuµmkKMG]?", s.strip()) else s.strip()
    m = re.fullmatch(r"(\d+)([pnuµmkKMG])(\d+)", s)
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * _PARSE[m.group(2)]
    m = re.fullmatch(r"([\d.]+)\s*([pnuµmkKMG]?)", s)
    if not m:
        raise ValueError(f"cannot parse '{s}'")
    return float(m.group(1)) * (_PARSE[m.group(2)] if m.group(2) else 1.0)
