"""Differential impedance of an edge-coupled surface microstrip pair from a 2-D field solve by atlc (Arbitrary Transmission-line Calculator, GPL, run as an
external process; Debian/Ubuntu package `atlc`). atlc's own `create_bmp_for_microstrip_coupler` draws the cross-section and `atlc` solves Laplace's equation
on it, reporting Zodd, Zeven and Zdiff = 2 Zodd. No solver or formula of ours. Lengths in mm. Quasi-static: no dispersion or loss, and no solder mask."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Pair:
    z_diff_ohm: float
    z_odd_ohm: float
    z_even_ohm: float
    er_odd: float
    er_even: float


def available() -> bool:
    return all(shutil.which(x) for x in ("atlc", "create_bmp_for_microstrip_coupler"))


def microstrip_pair(w_mm: float, s_mm: float, h_mm: float, t_mm: float, er: float, bitmap_size: int = 5, side_gap_mm: float | None = None,
                    timeout: int = 900) -> Pair:
    """Zdiff of two outer-layer strips of width `w`, edge spacing `s`, over a ground plane `h` below, copper thickness `t`, dielectric `er`.
    `bitmap_size` (1-15) is atlc's grid resolution: finer is slower (minutes at 8) and closer to the continuum answer."""
    if not available():
        raise RuntimeError("atlc not installed (apt install atlc)")
    g = side_gap_mm if side_gap_mm is not None else max(10 * h_mm, 2 * w_mm + s_mm)        # side walls far enough not to load the pair
    with tempfile.TemporaryDirectory(prefix="atlc_") as td:
        bmp = Path(td) / "pair.bmp"
        r = subprocess.run(["create_bmp_for_microstrip_coupler", "-b", str(bitmap_size), f"{w_mm:g}", f"{s_mm:g}", f"{g:g}", f"{h_mm:g}", f"{t_mm:g}", "1", f"{er:g}", str(bmp)],
                           capture_output=True, text=True, timeout=60)
        if not bmp.exists():
            raise RuntimeError(f"atlc bitmap failed: {(r.stderr or r.stdout).strip()[-200:]}")
        cmd = ["atlc", "-s", "-S", str(bmp)]
        for _ in range(3):                                                            # the drawn dielectric's colour must be declared once atlc names it
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=td)
            text = r.stdout + r.stderr
            m = re.search(r"colour r=0x\w+ g=0x\w+ b=0x\w+ \(0x([0-9a-f]{6})\)", text)
            if m and "-d" not in cmd:
                cmd = ["atlc", "-s", "-S", "-d", f"{m.group(1)}={er:g}", str(bmp)]
                continue
            break
        out = re.search(r"Er_odd=\s*([\d.]+)\s+Er_even=\s*([\d.]+)\s+Zodd=\s*([\d.]+)\s+Zeven=\s*([\d.]+)\s+Zo=\s*[\d.]+\s+Zdiff=\s*([\d.]+)", text)
        if not out:
            raise RuntimeError(f"atlc gave no impedance: {text.strip()[-300:]}")
        e_odd, e_even, z_odd, z_even, z_diff = map(float, out.groups())
        return Pair(z_diff, z_odd, z_even, e_odd, e_even)
