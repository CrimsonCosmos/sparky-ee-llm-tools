"""Board build: our placer -> KiCad board file -> Freerouting -> KiCad DRC. Routing, copper fill and checking are KiCad/Freerouting's job."""
from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import krt
from ..export.kicad import layer_z_mm
from ..constraints import ControlledImpedance, DiffPair, Fanout, LengthGroup
from ..model import Circuit
from . import analysis
from .board import Board
from .placement import PlaceOptions, place
from .planes import assign_planes
from .rules import net_classes_for, rules_for


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
    fiducials: int = 0                     # global assembly fiducials on the top face (0-3); the assembler's machine decides whether it needs them
    test_points: tuple | str = ()          # net names to give a test point, or 'rails' for every power and ground net
    stitch_pitch_mm: float | None = None   # ground-stitching via grid pitch; or give stitch_hz and the pitch is lambda/20 at that frequency
    stitch_hz: float | None = None
    corner_radius_mm: float = 0.0          # rounded board corners (0 = square)
    silk_text: str = ""                    # title/revision printed on the front silkscreen
    testpoint_footprint: str = "TestPoint:TestPoint_Pad_D1.5mm"    # the designer's probe choice (KiCad library footprint)


@dataclass
class BuildResult:
    board: Board                           # placement + intent (planes/pours); copper lives in `pcb_path`
    drc: object                            # kicad_backend.DrcReport
    analysis: dict
    log: list[str]
    success: bool
    pcb_path: Path | None = None           # routed, zone-filled KiCad board
    routing_checks: list = field(default_factory=list)     # diff-pair skew / length-group spread re-measured from the copper (krt.verify)


def build_board(circuit: Circuit, spec: BoardSpec | None = None, log=print) -> BuildResult:
    from .. import kicad_backend, routing
    from ..export.kicad import add_pours, to_kicad_pcb
    spec = spec or BoardSpec()
    if spec.fiducials or spec.test_points:
        import copy
        from . import features
        circuit = copy.deepcopy(circuit)                  # never add parts to the caller's circuit
        probe = features.rail_nets(circuit) if spec.test_points == "rails" else list(spec.test_points)
        if probe:
            features.add_test_points(circuit, probe, spec.testpoint_footprint)
        if spec.fiducials:
            features.add_fiducials(circuit, spec.fiducials, rules_for(spec.layers or 2, spec.profile).fiducial_footprint)
    t_start = time.time()
    work = Path(spec.workdir or tempfile.mkdtemp(prefix=f"sparky_{circuit.name}_"))
    work.mkdir(parents=True, exist_ok=True)
    msgs: list[str] = []

    def say(s: str) -> None:
        msgs.append(s)
        if log:
            log(f"[{circuit.name}] {s}")
    # layer count is a design input (spec.layers); otherwise start at 2 and escalate through the even counts a fab offers until the board routes cleanly
    layer_options = [spec.layers] if spec.layers else list(range(2, spec.max_layers + 1, 2))
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
            board.set_corner_radius(spec.corner_radius_mm)
            board.silk_text = spec.silk_text
            assign_planes(board)
            tag = f"{layers}L_u{util}"
            raw, routed = work / f"{tag}.kicad_pcb", work / f"{tag}_routed.kicad_pcb"
            raw.write_text(to_kicad_pcb(board, circuit.name))
            special = [c for c in circuit.constraints if isinstance(c, (DiffPair, LengthGroup, Fanout, ControlledImpedance))]
            staged = bool(special and rules.via_spans)       # KiCadRoutingTools lays through vias only: it routes the constrained nets, Freerouting (with the partial-depth padstacks) the rest
            dru = []
            if special:                                      # KiCad grades length/skew itself from this rules file (router and DRC both read it)
                dru.append(krt.dru_text([c for c in special if isinstance(c, (DiffPair, LengthGroup))]))
            if rules.via_spans:                              # per-type via limits for KiCad's own DRC (the board-setup minimums are the loosest type's)
                from . import hdi
                dru.append(("" if dru else "(version 1)\n") + hdi.dru_text(rules.via_spans, rules))
            if dru:
                raw.with_suffix(".kicad_dru").write_text("".join(dru))
            kicad_backend.write_kicad_pro(raw.with_suffix(".kicad_pro"), rules, net_classes=net_classes_for(circuit, rules))
            say(f"{layers}L util={util}: board {board.width}x{board.height} mm; routing with {'KiCadRoutingTools (' + str(len(special)) + ' routing constraints)' if special else 'Freerouting'}")
            try:
                if special:
                    if not krt.available():
                        raise routing.RoutingError("this board has differential-pair / length-match / fanout constraints, which need KiCadRoutingTools (SPARKY_KRT); not installed")
                    t_r = time.time()
                    try:
                        constrained = sorted({n for c in special for n in ((c.p, c.n) if isinstance(c, DiffPair) else c.nets if isinstance(c, (LengthGroup, ControlledImpedance)) else ())})
                        krt_out = work / f"{tag}_krt.kicad_pcb" if staged else routed
                        kr = krt.route(raw, krt_out, rules=rules, constraints=special, all_nets=constrained if staged else [n for n, p in circuit.nets.items() if len(p) > 1],
                                       net_classes=net_classes_for(circuit, rules), timeout=int(max(120, spec.time_budget_s * 0.6)))
                        if staged:
                            keep_nets = set(constrained) | {n for f in special if isinstance(f, Fanout) for n, pins in circuit.nets.items() if any(q.startswith(f.ref + ".") for q in pins)}
                            say("   constrained nets routed by KiCadRoutingTools (through vias); the rest by Freerouting with partial-depth vias")
                            routing.route_pcb(krt_out, routed, passes=spec.passes, timeout=int(max(120, spec.time_budget_s * 0.6)), via_spans=rules.via_spans, layers=board.layers, protect=keep_nets)
                            routed.write_text(krt.restore_nets(krt_out.read_text(), routed.read_text(), keep_nets))
                    except (RuntimeError, ValueError) as e:
                        raise routing.RoutingError(str(e)) from e
                    rr = type("R", (), {"seconds": time.time() - t_r})()
                    msgs.append("krt: " + ", ".join(f"{s['step']} {s['seconds']}s" for s in kr.steps))
                else:
                    rr = routing.route_pcb(raw, routed, passes=spec.passes, timeout=int(max(120, spec.time_budget_s * 0.6)), via_spans=rules.via_spans, layers=board.layers)
            except routing.RoutingError as e:
                say(f"   routing failed: {e}")
                continue
            if rules.via_spans:                                              # KiCad's SES import labels every partial via 'buried': restore the fab's types
                from . import hdi
                routed.write_text(hdi.retype_vias(routed.read_text(), rules.via_spans, layers, board.layers))
            routed.write_text(add_pours(routed.read_text(), board))        # outer pours go in after routing (see export.kicad)
            if spec.stitch_pitch_mm or spec.stitch_hz:
                from . import stitching
                from .planes import _ground_net
                gnd = _ground_net(board)
                pitch = spec.stitch_pitch_mm or stitching.pitch_for_frequency(spec.stitch_hz, rules.dielectric_er)
                if gnd:
                    text, nv = stitching.add_stitching_vias(routed.read_text(), board, gnd, pitch)
                    routed.write_text(text)
                    board.stats["stitch_vias"], board.stats["stitch_pitch_mm"] = nv, round(pitch, 3)
                    say(f"   {nv} ground-stitching vias on a {pitch:.2f} mm grid")
            routed.with_suffix(".kicad_pro").write_bytes(raw.with_suffix(".kicad_pro").read_bytes())
            if raw.with_suffix(".kicad_dru").exists():
                routed.with_suffix(".kicad_dru").write_bytes(raw.with_suffix(".kicad_dru").read_bytes())
            filled = work / f"{tag}_final.kicad_pcb"
            drc = kicad_backend.run_drc(routed, refill=True, rules=rules, save_to=filled)
            filled.with_suffix(".kicad_pro").write_bytes(raw.with_suffix(".kicad_pro").read_bytes())
            if raw.with_suffix(".kicad_dru").exists():
                filled.with_suffix(".kicad_dru").write_bytes(raw.with_suffix(".kicad_dru").read_bytes())
            checks = (krt.verify(routed.read_text(), [c for c in special if isinstance(c, (DiffPair, LengthGroup))], layer_z_mm(rules, layers), drc.violations) + krt.check_impedance(routed.read_text(), special, rules)) if special else []
            if rules.via_spans:
                from . import hdi
                found, counts = hdi.check(routed.read_text(), rules, layer_z_mm(rules, layers), board.layers)
                checks = checks + [{"kind": "via_span", "name": "partial-depth vias", "ok": not found, "counts": counts, "lamination": hdi.lamination_sequence(rules, board.layers), "findings": [f"{f.where}: {f.problem}" for f in found]}]
            miss = [c["name"] for c in checks if c["ok"] is False]
            if miss:
                say(f"   routing constraints NOT met (re-measured): {', '.join(miss)}")
            ok = not drc.errors and not drc.unconnected and not miss
            say(f"   -> kicad drc[{drc.summary()}] ({rr.seconds:.0f}s routing) {'OK' if ok else 'retry'}")
            res = BuildResult(board, drc, {}, msgs, ok, filled, checks)
            score = (len(drc.unconnected), len(drc.errors), len(miss), board.width * board.height)
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
