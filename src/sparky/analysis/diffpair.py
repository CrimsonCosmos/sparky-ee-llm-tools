"""Differential impedance of an edge-coupled surface microstrip pair from a 2-D field solve by atlc (Arbitrary Transmission-line Calculator, GPL, run as an
external process; Debian/Ubuntu package `atlc`). atlc's own `create_bmp_for_microstrip_coupler` draws the cross-section and `atlc` solves Laplace's equation
on it, reporting Zodd, Zeven and Zdiff = 2 Zodd. No solver or formula of ours. Lengths in mm. Quasi-static: no dispersion or loss, and no solder mask."""
from __future__ import annotations

import re
import shutil
from functools import lru_cache
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
    bitmap_size: int = 0            # atlc grid actually used (the solver's run time is erratic in it, so neighbours are tried on a timeout)


def available() -> bool:
    return all(shutil.which(x) for x in ("atlc", "create_bmp_for_microstrip_coupler"))


@lru_cache(maxsize=256)
def microstrip_pair(w_mm: float, s_mm: float, h_mm: float, t_mm: float, er: float, bitmap_size: int = 5, side_gap_mm: float | None = None,
                    timeout: int = 25) -> Pair:
    """Zdiff of two outer-layer strips of width `w`, edge spacing `s`, over a ground plane `h` below, copper thickness `t`, dielectric `er`.
    `bitmap_size` (1-15) is atlc's grid resolution: finer is slower (minutes at 8) and closer to the continuum answer."""
    if not available():
        raise RuntimeError("atlc not installed (apt install atlc)")
    g = side_gap_mm if side_gap_mm is not None else max(10 * h_mm, 2 * w_mm + s_mm)        # side walls far enough not to load the pair
    last = ""
    for size in dict.fromkeys([bitmap_size, bitmap_size + 1, bitmap_size - 1, bitmap_size - 2]):     # atlc's convergence time jumps between neighbouring grids
        if not 1 <= size <= 15:
            continue
        try:
            return _solve(w_mm, s_mm, h_mm, t_mm, er, g, size, timeout)
        except subprocess.TimeoutExpired:
            last = f"timed out at grid {size}"
    raise RuntimeError(f"atlc did not converge in {timeout} s on any grid near {bitmap_size} ({last})")


def _solve(w_mm, s_mm, h_mm, t_mm, er, g, bitmap_size, timeout) -> Pair:
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
        return Pair(z_diff, z_odd, z_even, e_odd, e_even, bitmap_size)


# ---- stripline (inner layers): our own cross-section bitmap, atlc's solver ------------------------------------------------------------------------------
_GROUND, _POS, _NEG, _AIR = (0, 255, 0), (255, 0, 0), (0, 0, 255), (255, 255, 255)      # atlc's colour code: green = ground, red/blue = +/- conductor, white = vacuum
_DIEL = (172, 130, 172)                                                                  # a colour atlc does not predefine; declared with -d


def _write_bmp(path: Path, rgb) -> None:
    """24-bit uncompressed BMP from an (h, w, 3) uint8 array (top row first)."""
    import struct

    import numpy as np
    h, w, _ = rgb.shape
    row = (w * 3 + 3) // 4 * 4
    pad = np.zeros((h, row - w * 3), dtype=np.uint8)
    data = np.concatenate([rgb[::-1, :, ::-1].reshape(h, w * 3), pad], axis=1).tobytes()           # BMP is bottom-up BGR
    head = struct.pack("<2sIHHI", b"BM", 54 + len(data), 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(data), 2835, 2835, 0, 0)
    path.write_bytes(head + data)


ROWS = 160                                                                             # grid rows between the planes (atlc's accuracy follows the grid)


def _cross_section(strips: list[tuple[float, float, tuple[int, int, int]]], t_mm: float, h_below: float, h_above: float, er: float, box_w: float, rows: int | None = None):
    """Pixel grid of a stripline cross-section: ground planes above and below, side walls far away, uniform dielectric `er`, strips (x centre, width, colour) of thickness
    `t` at `h_below` above the lower plane. Pixel size is the plane spacing / ROWS; a strip thinner than a pixel is drawn one pixel thick."""
    import numpy as np
    px = (h_below + h_above + t_mm) / (rows or ROWS)
    n_h, n_w = round((h_below + t_mm + h_above) / px) + 2, round(box_w / px)
    g = np.empty((n_h, n_w, 3), dtype=np.uint8)
    g[:] = _DIEL
    g[0], g[-1], g[:, 0], g[:, -1] = _GROUND, _GROUND, _GROUND, _GROUND
    y0 = 1 + round(h_above / px)                                                          # top of the strips, counted from the top plane
    for xc, w, colour in strips:
        x0 = round((xc - w / 2) / px)
        g[y0:y0 + max(round(t_mm / px), 1), x0:x0 + max(round(w / px), 1)] = colour
    return g


def _run_atlc(grid, er: float, timeout: int) -> str:
    with tempfile.TemporaryDirectory(prefix="atlc_sl_") as td:
        bmp = Path(td) / "sl.bmp"
        _write_bmp(bmp, grid)
        r = subprocess.run(["atlc", "-s", "-S", "-d", f"{_DIEL[0]:02x}{_DIEL[1]:02x}{_DIEL[2]:02x}={er:g}", str(bmp)], capture_output=True, text=True, timeout=timeout, cwd=td)
        return r.stdout + r.stderr


def stripline(w_mm: float, h_below_mm: float, h_above_mm: float, t_mm: float, er: float, timeout: int = 120) -> float:
    """Z0 of one stripline of width `w` and thickness `t` between two ground planes (the strip `h_below` above the lower plane, `h_above` below the upper one)."""
    if not available():
        raise RuntimeError("atlc not installed (apt install atlc)")
    box_w = max(20 * (h_below_mm + h_above_mm + t_mm), 8 * w_mm)
    text = _run_atlc(_cross_section([(box_w / 2, w_mm, _POS)], t_mm, h_below_mm, h_above_mm, er, box_w), er, timeout)
    m = re.search(r"Zo=\s*([\d.]+)", text)
    if not m:
        raise RuntimeError(f"atlc gave no impedance: {text.strip()[-300:]}")
    return float(m.group(1))


def stripline_pair(w_mm: float, s_mm: float, h_below_mm: float, h_above_mm: float, t_mm: float, er: float, timeout: int = 120) -> Pair:
    """Zdiff of an edge-coupled stripline pair (two strips of width `w`, edge spacing `s`) between two ground planes."""
    if not available():
        raise RuntimeError("atlc not installed (apt install atlc)")
    box_w = max(20 * (h_below_mm + h_above_mm + t_mm), 8 * (2 * w_mm + s_mm))
    c = box_w / 2
    strips = [(c - (w_mm + s_mm) / 2, w_mm, _POS), (c + (w_mm + s_mm) / 2, w_mm, _NEG)]
    text = _run_atlc(_cross_section(strips, t_mm, h_below_mm, h_above_mm, er, box_w), er, timeout)
    out = re.search(r"Er_odd=\s*([\d.]+)\s+Er_even=\s*([\d.]+)\s+Zodd=\s*([\d.]+)\s+Zeven=\s*([\d.]+)\s+Zo=\s*[\d.]+\s+Zdiff=\s*([\d.]+)", text)
    if not out:
        raise RuntimeError(f"atlc gave no impedance: {text.strip()[-300:]}")
    e_odd, e_even, z_odd, z_even, z_diff = map(float, out.groups())
    return Pair(z_diff, z_odd, z_even, e_odd, e_even, 0)
