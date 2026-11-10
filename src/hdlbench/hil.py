"""Optional hardware-in-the-loop: turn a verified design into an iCE40 bitstream (Yosys + nextpnr + icepack) and, if a programmer and board are present, load it. This is a FINAL sanity check, never part of the
training/search loop. Board facts (device, package, pin constraints) are INPUTS: nothing here knows a particular board. Without the tools or a board the functions say NOT RUN."""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import tools


@dataclass
class HilResult:
    ok: bool
    stage: str
    detail: str
    bitstream: Path | None = None


def programmer() -> str | None:
    return shutil.which("openFPGALoader") or shutil.which("iceprog")


def build_bitstream(files: list[Path], top: str, out_bin: Path, device_flag: str, package: str, pcf: Path | None = None, freq_mhz: float | None = None) -> HilResult:
    y, n, pack = tools.which("yosys"), tools.nextpnr_ice40(), tools.which("yowasp-icepack") or tools.which("icepack")
    if not (y and n and pack):
        return HilResult(False, "tools", f"NOT RUN: need yosys, nextpnr-ice40 and icepack (pip install yowasp-nextpnr-ice40 yowasp-icepack); found yosys={bool(y)} nextpnr={bool(n)} icepack={bool(pack)}")
    out_bin = Path(out_bin).resolve()
    with tempfile.TemporaryDirectory(prefix="hb_hil_") as td:
        td = Path(td)
        fl = " ".join(str(Path(f).resolve()) for f in files)
        rc, text = tools.run([y, "-q", "-p", f"read_verilog -sv {fl}; synth_ice40 -top {top} -json d.json"], td, 300)
        if rc != 0:
            return HilResult(False, "synthesis", text.strip()[-300:])
        cmd = [n, device_flag, "--package", package, "--json", "d.json", "--asc", "d.asc"] + (["--pcf", str(_stage(pcf, td))] if pcf else ["--pcf-allow-unconstrained"]) + (["--freq", f"{freq_mhz:g}"] if freq_mhz else [])
        rc, text = tools.run(cmd, td, 600)
        if rc != 0:
            return HilResult(False, "place-and-route", text.strip()[-300:])
        rc, text = tools.run([pack, "d.asc", "d.bin"], td, 120)
        if rc != 0 or not (td / "d.bin").is_file():
            return HilResult(False, "bitstream", text.strip()[-300:])
        out_bin.write_bytes((td / "d.bin").read_bytes())
    return HilResult(True, "bitstream", f"wrote {out_bin} ({out_bin.stat().st_size} bytes)", out_bin)


def _stage(f: Path, td: Path) -> str:
    dst = td / Path(f).name
    dst.write_bytes(Path(f).read_bytes())
    return dst.name


def load(bitstream: Path) -> HilResult:
    prog = programmer()
    if not prog:
        return HilResult(False, "programmer", "NOT RUN: no openFPGALoader/iceprog on PATH (and no board attached)")
    rc, text = tools.run([prog, str(bitstream)], None, 120)
    return HilResult(rc == 0, "program", text.strip()[-300:], bitstream)
