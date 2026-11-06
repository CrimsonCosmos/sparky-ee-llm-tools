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
    source: str          # reference of the part the current enters from (regulator output inductor / connector)
    amps: float
    loads: list[str]     # references of the load parts (each is solved with the FULL rail current: a worst case)
    sense: str = ""
    vnom: float | None = None

    def spec(self) -> str:
        """kicad-irdrop rail syntax NET:SOURCE:AMPS:LOAD1+LOAD2:SENSE:VNOM"""
        return f"{self.net}:{self.source}:{self.amps:g}:{'+'.join(self.loads)}:{self.sense}:{'' if self.vnom is None else f'{self.vnom:g}'}"


def available() -> bool:
    py = kicad_python()
    if not py:
        return False
    return subprocess.run([py, "-c", "import kicad_irdrop"], capture_output=True).returncode == 0


def run_irdrop(pcb: str | Path, rails: list[Rail], *, grid: float, budget_pct: float, timeout: int = 900) -> dict:
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


def rails_from_circuit(circuit) -> list[Rail]:
    """Power rails to analyse, from the circuit's declared power-out ports and its topology: source = the part with a power-output pin on the net, else an
    inductor, else a connector; loads = ICs with a power-input pin on the net. Rails without a source or load are skipped (the caller reports them)."""
    pin_net = circuit.pin_net_map()
    rails = []
    for port in circuit.ports.values():
        if port.kind != "power" or port.direction != "out" or not port.current_a or port.net not in circuit.nets:
            continue
        members = [(p.partition(".")[0], p.partition(".")[2]) for p in circuit.nets[port.net]]
        kinds = {r: circuit.components[r].part for r, _ in members}
        def pins(ref, num): return circuit.components[ref].part.pin(num)
        src = next((r for r, n in members if pins(r, n).etype == "power_out"), None) or \
            next((r for r, _ in members if kinds[r].category == "inductor"), None) or next((r for r, _ in members if kinds[r].category == "connector"), None)
        loads = sorted({r for r, n in members if r != src and kinds[r].category == "ic" and pins(r, n).etype == "power_in"})
        if src and loads:
            rails.append(Rail(port.net, src, port.current_a, loads, vnom=port.voltage or None))
    return rails
