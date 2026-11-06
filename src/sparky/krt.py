"""Constraint-aware routing stage: KiCadRoutingTools (MIT, https://github.com/drandyhaas/KiCadRoutingTools), run as external commands on a KiCad file.

Used instead of Freerouting when a board carries differential pairs, length-match groups or array-package (BGA/QFN) fanout: fanout -> coupled pairs -> remaining nets
(+ length matching). Both routers work on the same .kicad_pcb/.kicad_pro (net classes carry widths and spacing). Routed length / skew are re-measured from the
resulting copper here (never taken from the tool's own report) and compared with the requirement."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import sexpr
from .constraints import DiffPair, Fanout, LengthGroup
from .toolchain import krt_dir

_PAIR = [(r"^(.*)_P$", r"\1_N"), (r"^(.*)\+$", r"\1-"), (r"^(.*[0-9_A-Z])P$", r"\1N")]


def available() -> bool:
    return krt_dir() is not None


def check_pair_names(p: str, n: str) -> None:
    """KiCadRoutingTools pairs nets by NAME; refuse a pair it would not recognise instead of silently routing two single nets."""
    for pat, repl in _PAIR:
        if re.match(pat, p) and re.sub(pat, repl, p) == n:
            return
    raise ValueError(f"differential pair nets '{p}'/'{n}' do not follow a pairing convention (X_P/X_N, X+/X-, XP/XN); rename the nets")


@dataclass
class KrtResult:
    steps: list[dict] = field(default_factory=list)          # {step, seconds, rc, summary}
    seconds: float = 0.0
    log: str = ""


def net_lengths(pcb_text: str, layer_z: dict[str, float] | None = None) -> dict[str, float]:
    """Routed conductor length per net (mm): straight segments and arc chords, plus each via's barrel (the distance between the copper layers it joins, from
    `layer_z`) when given - a via is conductor length and matters at high speed."""
    import math
    root = sexpr.parse(pcb_text)
    names = {str(n[1]): str(n[2]) for n in root if isinstance(n, list) and len(n) >= 3 and n[0] == "net"}      # top-level (net id "name"); segments may use either form
    out: dict[str, float] = {}
    for node in root:
        if isinstance(node, list) and node and node[0] in ("segment", "arc"):
            a, b, nt = sexpr.find(node, "start"), sexpr.find(node, "end"), sexpr.find(node, "net")
            if a and b and nt:
                net = names.get(str(nt[1]), str(nt[1]))
                out[net] = out.get(net, 0.0) + math.hypot(float(b[1]) - float(a[1]), float(b[2]) - float(a[2]))
        elif isinstance(node, list) and node and node[0] == "via" and layer_z:
            ly, nt = sexpr.find(node, "layers"), sexpr.find(node, "net")
            if ly and nt and len(ly) >= 3 and str(ly[1]) in layer_z and str(ly[2]) in layer_z:
                net = names.get(str(nt[1]), str(nt[1]))
                out[net] = out.get(net, 0.0) + abs(layer_z[str(ly[1])] - layer_z[str(ly[2])])
    return out


def _run(args: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    r = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _summary(out: str) -> dict:
    for line in reversed(out.splitlines()):
        if line.startswith("JSON_SUMMARY:"):
            try:
                return json.loads(line[len("JSON_SUMMARY:"):])
            except json.JSONDecodeError:
                return {}
    return {}


def fab_overrides(rules) -> str:
    """The tool's fab-floor file, written from OUR Rules so the two never disagree about what the fab can make (this pins the floor and disables its own escalation)."""
    via_dia = rules.min_drill + 2 * rules.min_annular
    return "\n".join(f"{k} = {v:g}" for k, v in (
        ("via_diameter", via_dia), ("via_drill", rules.min_drill), ("clearance", rules.clearance), ("track_width", rules.min_track),
        ("hole_to_hole", rules.hole_to_hole), ("board_edge", rules.edge_clearance), ("annular", rules.min_annular))) + "\n"


def route(raw: Path, out: Path, *, rules, constraints: list, all_nets: list[str], net_classes: list[dict], timeout: int = 900) -> KrtResult:
    """Run the stages on a copy of `raw` (a .kicad_pcb with its .kicad_pro beside it) and write the routed board to `out`."""
    root = Path(krt_dir() or "")
    if not root.is_dir():
        raise RuntimeError("KiCadRoutingTools not found: set SPARKY_KRT (a checkout whose Rust router is built); see docs/DEPENDENCIES.md")
    work = out.parent / f"{out.stem}_krt"
    work.mkdir(parents=True, exist_ok=True)
    cur = work / "step0.kicad_pcb"
    shutil.copy(raw, cur)
    shutil.copy(raw.with_suffix(".kicad_pro"), cur.with_suffix(".kicad_pro"))
    res, t0 = KrtResult(), time.time()
    pairs = [c for c in constraints if isinstance(c, DiffPair)]
    groups = [c for c in constraints if isinstance(c, LengthGroup)]
    fans = [c for c in constraints if isinstance(c, Fanout)]

    clearance, min_track, fab_tier = rules.clearance, rules.min_track, rules.fab_tier
    (work / "fab_overrides.txt").write_text(fab_overrides(rules))
    tier = ["--fab-tier", fab_tier, "--fab-overrides", str(work / "fab_overrides.txt")] + (["--escalation", "off", "--strict-sizes"] if fab_tier == "standard" else [])

    def step(name: str, args: list[str]) -> None:
        nonlocal cur
        nxt = work / f"{name}.kicad_pcb"
        shutil.copy(cur.with_suffix(".kicad_pro"), nxt.with_suffix(".kicad_pro"))
        t = time.time()
        io = [cur.name, "-o", nxt.name] if args[0] == "bga_fanout.py" else [cur.name, nxt.name]        # fanout names its output with -o; the routers take it positionally
        rc, text = _run([str(root / "py_router" / args[0]), *io, *args[1:], *tier], work, timeout)
        res.steps.append({"step": name, "seconds": round(time.time() - t, 1), "rc": rc, "summary": _summary(text)})
        res.log += f"\n=== {name} rc={rc}\n" + text[-2500:]
        if rc != 0 or not nxt.exists():
            hint = (" The requested geometry needs features finer than the fab tier allows (necks narrower than the minimum track): use a finer fab profile / "
                    "fab_tier='advanced' with matching rule_overrides, a wider pair pitch, or a less dense escape.") if "delivered below the requested size" in text else ""
            raise RuntimeError(f"KiCadRoutingTools step '{name}' failed (rc={rc}): {text.strip()[-300:]}{hint}")
        cur = nxt

    for i, f in enumerate(fans):
        step(f"fanout_{f.ref}", ["bga_fanout.py", "-c", f.ref, "--clearance", f"{clearance:g}"])
    routed: set[str] = set()
    for i, d in enumerate(pairs):
        check_pair_names(d.p, d.n)
        if fab_tier == "standard" and (d.gap_mm < clearance - 1e-9 or d.width_mm < min_track - 1e-9):
            raise ValueError(f"pair {d.p}/{d.n}: width {d.width_mm:g} / gap {d.gap_mm:g} mm is below this fab profile's limits (min track {min_track:g}, clearance {clearance:g}); "
                             "use a finer fab profile (rule_overrides, fab_tier='advanced') or change the pair")
        step(f"diff_{i}", ["route_diff.py", "--nets", d.p, d.n, "--diff-pair-gap", f"{d.gap_mm:g}", "--track-width", f"{d.width_mm:g}", "--clearance", f"{clearance:g}",
                           "--length-match-tolerance", f"{d.skew_mm:g}", "--diff-pair-intra-match"])
        routed |= {d.p, d.n}
    rest = [n for n in all_nets if n not in routed]
    args = ["route.py", "--nets", *rest, "--clearance", f"{clearance:g}"]
    wide = [(n, c["track_width"]) for c in net_classes for n in c["nets"] if n in rest]
    if wide:
        args += ["--power-nets", *[n for n, _ in wide], "--power-nets-widths", *[f"{w:g}" for _, w in wide]]
    for g in groups:
        args += ["--length-match-group", *g.nets]
    if groups:
        args += ["--length-match-tolerance", f"{min(g.tolerance_mm for g in groups):g}"]
    step("route", args)
    shutil.copy(cur, out)
    shutil.copy(cur.with_suffix(".kicad_pro"), out.with_suffix(".kicad_pro"))
    res.seconds = time.time() - t0
    return res


def verify(pcb_text: str, constraints: list, layer_z: dict[str, float]) -> list[dict]:
    """Re-measure pair skew and group mismatch (tracks + via barrels) from the routed copper and compare with each requirement."""
    L = net_lengths(pcb_text, layer_z)
    rows = []
    for c in constraints:
        if isinstance(c, DiffPair):
            lp, ln = L.get(c.p, 0.0), L.get(c.n, 0.0)
            rows.append({"kind": "diff_pair", "name": f"{c.p}/{c.n}", "lengths_mm": [round(lp, 3), round(ln, 3)], "skew_mm": round(abs(lp - ln), 3),
                         "limit_mm": c.skew_mm, "ok": lp > 0 and ln > 0 and abs(lp - ln) <= c.skew_mm + 1e-6})
        elif isinstance(c, LengthGroup):
            ls = [L.get(n, 0.0) for n in c.nets]
            spread = max(ls) - min(ls) if ls else 0.0
            rows.append({"kind": "length_group", "name": "+".join(c.nets), "lengths_mm": [round(x, 3) for x in ls], "skew_mm": round(spread, 3),
                         "limit_mm": c.tolerance_mm, "ok": min(ls, default=0) > 0 and spread <= c.tolerance_mm + 1e-6})
    return rows
