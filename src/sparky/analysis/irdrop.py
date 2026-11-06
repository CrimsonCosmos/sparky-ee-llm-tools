"""DC IR drop on the routed board's real copper via kicad-irdrop (Apache-2.0, https://github.com/firedanceio/kicad-irdrop).

Install: `pip install git+https://github.com/firedanceio/kicad-irdrop` into the interpreter that can import pcbnew (see toolchain.kicad_python)."""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..toolchain import kicad_python


@dataclass
class Rail:
    net: str
    source: str          # reference of the part the current enters from (regulator output / connector)
    amps: float
    load: str            # reference of the load part
    sense: str = ""
    vnom: float | None = None

    def spec(self) -> str:
        """kicad-irdrop rail syntax NET:SOURCE:AMPS:LOAD:SENSE:VNOM"""
        return f"{self.net}:{self.source}:{self.amps:g}:{self.load}:{self.sense}:{'' if self.vnom is None else f'{self.vnom:g}'}"


def available() -> bool:
    py = kicad_python()
    if not py:
        return False
    return subprocess.run([py, "-c", "import kicad_irdrop"], capture_output=True).returncode == 0


def run_irdrop(pcb: str | Path, rails: list[Rail], *, grid: float = 0.05, budget_pct: float = 2.0, timeout: int = 900) -> dict:
    py = kicad_python()
    if not py:
        raise RuntimeError("no Python with pcbnew found (needed by kicad-irdrop)")
    with tempfile.TemporaryDirectory(prefix="irdrop_") as td:
        out = Path(td) / "r.json"
        cmd = [py, "-m", "kicad_irdrop", str(pcb), "--grid", str(grid), "--budget-pct", str(budget_pct), "--json", str(out)]
        for r in rails:
            cmd += ["--rail", r.spec()]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if not out.exists():
            raise RuntimeError(f"kicad-irdrop failed (rc={p.returncode}): {(p.stderr or p.stdout).strip()[-500:]}")
        return json.loads(out.read_text())
