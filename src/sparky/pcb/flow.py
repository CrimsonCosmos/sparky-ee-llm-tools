"""Board build: our placer -> KiCad board file -> Freerouting -> KiCad DRC. Routing, copper fill and checking are KiCad/Freerouting's job."""
from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..model import Circuit
from . import analysis
from .board import Board
from .placement import PlaceOptions, place
from .planes import assign_planes
from .rules import rules_for


@dataclass
class BoardSpec:
    layers: int | None = None              # None = choose automatically
    width: float | None = None
    height: float | None = None
    profile: str | None = None
    seed: int = 1
    copper_oz: float = 1.0
    max_layers: int = 6
    utilizations: tuple = (0.42, 0.32, 0.24, 0.18)
    time_budget_s: float = 1200.0
    passes: int = 20                       # Freerouting optimisation passes
    rule_overrides: dict = field(default_factory=dict)
    workdir: str | None = None


@dataclass
class BuildResult:
    board: Board                           # placement + intent (planes/pours); copper lives in `pcb_path`
    drc: object                            # kicad_backend.DrcReport
    analysis: dict
    log: list[str]
    success: bool
    pcb_path: Path | None = None           # routed, zone-filled KiCad board


def suggest_layers(circuit: Circuit) -> int:
    n = len(circuit.components)
    hot = sum(1 for c in circuit.components.values() if c.power_w >= 0.5)
    high_i = any(a.current_a >= 5 for a in circuit.net_attrs.values())
    fine = any(p.part.category.startswith("ic.mcu") for p in circuit.components.values())
    return 4 if (hot or high_i or n > 45 or fine) else 2


def build_board(circuit: Circuit, spec: BoardSpec | None = None, log=print) -> BuildResult:
    from .. import kicad_backend, routing
    from ..export.kicad import to_kicad_pcb
    spec = spec or BoardSpec()
    t_start = time.time()
    work = Path(spec.workdir or tempfile.mkdtemp(prefix=f"sparky_{circuit.name}_"))
    work.mkdir(parents=True, exist_ok=True)
    try:                                    # official KiCad footprint geometry where the library has it
        from ..footprint_sync import sync_circuit
        sync_circuit(circuit)
    except Exception:
        pass
    msgs: list[str] = []

    def say(s: str) -> None:
        msgs.append(s)
        if log:
            log(f"[{circuit.name}] {s}")
    layer_options = [spec.layers] if spec.layers else list(dict.fromkeys(l for l in (suggest_layers(circuit), 4, 6) if l <= spec.max_layers))
    utils = spec.utilizations if not (spec.width and spec.height) else (1.0,)
    best: BuildResult | None = None
    best_score = None
    for layers in layer_options:
        for util in utils:
            if time.time() - t_start > spec.time_budget_s:
                say("time budget exhausted")
                break
            over = dict(spec.rule_overrides, copper_oz_outer=spec.copper_oz)
            rules = rules_for(layers, spec.profile, **over)
            board = place(circuit, rules, PlaceOptions(width=spec.width, height=spec.height, utilization=util, seed=spec.seed,
                                                       fixed_outline=bool(spec.width and spec.height)))
            assign_planes(board)
            tag = f"{layers}L_u{util}"
            raw, routed = work / f"{tag}.kicad_pcb", work / f"{tag}_routed.kicad_pcb"
            raw.write_text(to_kicad_pcb(board, circuit.name))
            kicad_backend.write_kicad_pro(raw.with_suffix(".kicad_pro"), rules)
            say(f"{layers}L util={util}: board {board.width}x{board.height} mm; routing with Freerouting")
            try:
                rr = routing.route_pcb(raw, routed, passes=spec.passes, timeout=int(max(120, spec.time_budget_s * 0.6)))
            except routing.RoutingError as e:
                say(f"   routing failed: {e}")
                continue
            routed.with_suffix(".kicad_pro").write_bytes(raw.with_suffix(".kicad_pro").read_bytes())
            filled = work / f"{tag}_final.kicad_pcb"
            drc = kicad_backend.run_drc(routed, refill=True, rules=rules, save_to=filled)
            filled.with_suffix(".kicad_pro").write_bytes(raw.with_suffix(".kicad_pro").read_bytes())
            ok = not drc.errors and not drc.unconnected
            say(f"   -> kicad drc[{drc.summary()}] ({rr.seconds:.0f}s routing) {'OK' if ok else 'retry'}")
            res = BuildResult(board, drc, {}, msgs, ok, filled)
            score = (len(drc.unconnected), len(drc.errors), board.width * board.height)
            if best is None or score < best_score:
                best, best_score = res, score
            if ok:
                best.analysis = analysis.analyze(best.board)
                return best
    if best is None:
        raise RuntimeError("no board could be built: " + "; ".join(msgs[-3:]))
    best.analysis = analysis.analyze(best.board)
    say("WARNING: no attempt produced a clean, fully routed board; returning the best attempt")
    return best
