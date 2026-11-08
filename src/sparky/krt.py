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
from .constraints import ControlledImpedance, DiffPair, Fanout, LengthGroup
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


def microstrip_z0(w_mm: float, h_mm: float, t_mm: float, er: float) -> float:
    """IPC-2141 surface microstrip: Z0 = 87/sqrt(er+1.41) * ln(5.98 h / (0.8 w + t)); accurate to a few percent for 0.1 < w/h < 2 (not a field solver)."""
    import math
    return 87.0 / math.sqrt(er + 1.41) * math.log(5.98 * h_mm / (0.8 * w_mm + t_mm))


def check_impedance(pcb_text: str, constraints: list, rules) -> list[dict]:
    """Independent check of ControlledImpedance nets: every OUTER-layer track width is converted to an impedance with the IPC-2141 microstrip formula and the
    stackup the exporter wrote (dielectric to the next copper layer). Inner layers and differential pairs are NOT verified (no independent formula here): reported as such."""
    from .export.kicad import layer_z_mm
    root = sexpr.parse(pcb_text)
    names = {str(n[1]): str(n[2]) for n in root if isinstance(n, list) and len(n) >= 3 and n[0] == "net"}
    n = rules.layers
    z = layer_z_mm(rules, n)
    ordered = sorted(z.items(), key=lambda kv: kv[1])
    from .pcb.rules import OZ_CU_MM
    t = rules.copper_oz_outer * OZ_CU_MM
    rows = []
    for c in constraints:
        if isinstance(c, DiffPair) and c.impedance_ohm:
            rows.append(_check_pair(root, names, c, rules, ordered, t))
        if not isinstance(c, ControlledImpedance):
            continue
        widths: dict[str, list[float]] = {}
        for node in root:
            if isinstance(node, list) and node and node[0] == "segment":
                nt, w, ly = sexpr.find(node, "net"), sexpr.find(node, "width"), sexpr.find(node, "layer")
                net = names.get(str(nt[1]), str(nt[1])) if nt else None
                if net in c.nets and w and ly and str(ly[1]) in (ordered[0][0], ordered[-1][0]):
                    widths.setdefault(str(ly[1]), []).append(float(w[1]))
        zs = []
        for layer, ws in widths.items():
            k = 0 if layer == ordered[0][0] else -1
            nxt = 1 if k == 0 else -2
            cu_next = (rules.copper_oz_outer if n == 2 else rules.copper_oz_inner) * OZ_CU_MM
            h = abs(ordered[k][1] - ordered[nxt][1]) - (t + cu_next) / 2             # dielectric thickness between the outer copper and the next copper layer
            zs += [microstrip_z0(w, h, t, rules.dielectric_er) for w in ws]
        if not zs:
            rows.append({"kind": "impedance", "name": "+".join(c.nets), "ok": None, "note": "no outer-layer tracks to check (inner layers are not verified)"})
            continue
        err = max(abs(v / c.impedance_ohm - 1) * 100 for v in zs)
        rows.append({"kind": "impedance", "name": "+".join(c.nets), "ok": err <= c.tolerance_pct, "z_ohm": [round(min(zs), 1), round(max(zs), 1)], "target_ohm": c.impedance_ohm,
                     "error_pct": round(err, 1), "limit_pct": c.tolerance_pct, "note": "IPC-2141 microstrip over the next copper layer (assumed a reference plane)"})
    return rows


def _check_pair(root, names, c, rules, ordered, t) -> dict:
    """Differential impedance of a routed outer-layer pair by a 2-D field solve (atlc, see analysis/diffpair.py) of the width and spacing actually in the copper.
    NOT VERIFIED when atlc is missing or the pair has no outer-layer copper (inner layers are stripline, which this does not solve)."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    from .analysis import diffpair
    from .pcb.rules import OZ_CU_MM
    name = f"{c.p}/{c.n}"
    base = {"kind": "impedance", "name": name, "ok": None, "target_ohm": c.impedance_ohm, "limit_pct": c.impedance_tolerance_pct}
    if not diffpair.available():
        return {**base, "note": "differential impedance NOT verified: atlc (2-D field solver) not installed"}
    seg: dict[tuple[str, str], list] = {}
    for node in root:
        if isinstance(node, list) and node and node[0] == "segment":
            nt, w, ly, a, b = (sexpr.find(node, k) for k in ("net", "width", "layer", "start", "end"))
            net = names.get(str(nt[1]), str(nt[1])) if nt else None
            if net in (c.p, c.n) and w and ly and str(ly[1]) in (ordered[0][0], ordered[-1][0]):
                seg.setdefault((str(ly[1]), net), []).append((float(w[1]), LineString([(float(a[1]), float(a[2])), (float(b[1]), float(b[2]))])))
    layers = {l for l, _ in seg if (l, c.p) in seg and (l, c.n) in seg}
    if not layers:
        return {**base, "note": "differential impedance NOT verified: no outer-layer copper on both nets (inner-layer stripline is not solved)"}
    layer = max(layers, key=lambda l: sum(g.length for _, g in seg[(l, c.p)] + seg[(l, c.n)]))
    p_geo, n_geo = unary_union([g for _, g in seg[(layer, c.p)]]), unary_union([g for _, g in seg[(layer, c.n)]])
    widths = sorted(w for w, _ in seg[(layer, c.p)] + seg[(layer, c.n)])
    w = widths[len(widths) // 2]                                              # the width most of the pair is routed at
    gap = p_geo.distance(n_geo) - w                                           # centre-line distance minus one width = edge-to-edge spacing
    if gap <= 0:
        return {**base, "note": "differential impedance NOT verified: routed pair touches or overlaps"}
    k = 0 if layer == ordered[0][0] else -1
    nxt = 1 if k == 0 else -2
    cu_next = (rules.copper_oz_outer if rules.layers == 2 else rules.copper_oz_inner) * OZ_CU_MM
    h = abs(ordered[k][1] - ordered[nxt][1]) - (t + cu_next) / 2
    try:
        z = diffpair.microstrip_pair(w, gap, h, t, rules.dielectric_er).z_diff_ohm
    except RuntimeError as e:
        return {**base, "note": f"differential impedance NOT verified: {e}"}
    err = abs(z / c.impedance_ohm - 1) * 100
    return {**base, "ok": err <= c.impedance_tolerance_pct, "z_ohm": [round(z, 1), round(z, 1)], "error_pct": round(err, 1),
            "note": f"atlc 2-D field solve (quasi-static, +-3 % grid error, no solder mask) at routed width {w:g} mm, spacing {gap:.3f} mm on {layer}"}


def _delay_row(pcb_text: str, c, layer_z: dict[str, float], rules) -> dict:
    """Delay-matched group graded by our own propagation-delay measurement (analysis/delay.py): KiCad has no verdict for it, so this is a cross-check, not an authority."""
    from .analysis import delay, lines
    base = {"kind": "delay_group", "name": "+".join(c.nets), "limit_ps": c.time_ps}
    if rules is None or not lines.available():
        return {**base, "ok": None, "note": "delay NOT verified: needs scikit-rf and the rules' stackup"}
    d = delay.net_delays_ps(pcb_text, rules, layer_z)
    ds = [d.get(n, 0.0) for n in c.nets]
    spread = max(ds) - min(ds) if ds else 0.0
    return {**base, "delays_ps": [round(x, 2) for x in ds], "spread_ps": round(spread, 2), "ok": min(ds, default=0) > 0 and spread <= c.time_ps,
            "note": "own measurement (scikit-rf outer-layer er_eff, TEM stripline inner layers, via barrels); no KiCad verdict exists for delay"}


def _hit(viol: list[dict], i: int) -> bool:
    return any(f"rule 'sparky_{i}'" in v.get("description", "") for v in viol)


def _actual(viol: list[dict], i: int) -> float | None:
    for v in viol:
        m = re.search(rf"rule 'sparky_{i}'.*?actual (-?[\d.]+) mm", v.get("description", ""))
        if m:
            return abs(float(m.group(1)))
    return None


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
    if raw.with_suffix(".kicad_dru").exists():
        shutil.copy(raw.with_suffix(".kicad_dru"), cur.with_suffix(".kicad_dru"))
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
        if cur.with_suffix(".kicad_dru").exists():
            shutil.copy(cur.with_suffix(".kicad_dru"), nxt.with_suffix(".kicad_dru"))
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
                           "--length-match-tolerance", f"{d.skew_mm:g}", "--diff-pair-intra-match", *(["--impedance", f"{d.impedance_ohm:g}"] if d.impedance_ohm else [])])
        routed |= {d.p, d.n}
    for j, z in enumerate(c for c in constraints if isinstance(c, ControlledImpedance)):
        step(f"impedance_{j}", ["route.py", "--nets", *z.nets, "--impedance", f"{z.impedance_ohm:g}", "--clearance", f"{clearance:g}"])
        routed |= set(z.nets)
    rest = [n for n in all_nets if n not in routed]
    args = ["route.py", "--nets", *rest, "--clearance", f"{clearance:g}"]
    wide = [(n, c["track_width"]) for c in net_classes for n in c["nets"] if n in rest]
    if wide:
        args += ["--power-nets", *[n for n, _ in wide], "--power-nets-widths", *[f"{w:g}" for _, w in wide]]
    for g in groups:
        args += ["--length-match-group", *g.nets]
    if groups:
        args += ["--length-match-tolerance", f"{min(g.tolerance_mm for g in groups):g}"]
        timed = [g.time_ps for g in groups if g.time_ps]
        if timed:                                         # match propagation delay (inner layers are slower than outer ones) instead of length
            args += ["--time-matching", "--time-match-tolerance", f"{min(timed):g}"]
    step("route", args)
    shutil.copy(cur, out)
    shutil.copy(cur.with_suffix(".kicad_pro"), out.with_suffix(".kicad_pro"))
    res.seconds = time.time() - t0
    return res


def _blocks(text: str, heads=("segment", "via", "arc")) -> list[tuple[int, int, str]]:
    """(start, end, head) of every top-level copper item of a KiCad board text, found by parenthesis depth."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
            if depth == 2:
                start = i
        elif ch == ")":
            if depth == 2:
                m = re.match(r"\((\w+)", text[start:start + 12])
                if m and m.group(1) in heads:
                    out.append((start, i + 1, m.group(1)))
            depth -= 1
    return out


def restore_nets(krt_text: str, routed_text: str, nets: set[str]) -> str:
    """Put `nets`' copper exactly as KiCadRoutingTools routed it into the board another router finished afterwards: the second router adds small pin stubs and may
    nudge fixed wires, which would spoil length matching. The other router's copper for these nets is dropped, KRT's is written back with nets named (the form
    KiCad >= 9 files use). KiCad's DRC on the result is what says whether the two routers' copper still fits together."""
    names = {m.group(1): m.group(2) for m in re.finditer(r'\(net (\d+) "([^"]*)"\)', krt_text)}

    def net_of(item: str, table: dict[str, str]) -> str | None:
        m = re.search(r'\(net (?:"([^"]*)"|(\d+))\)', item)
        if not m:
            return None
        return m.group(1) if m.group(1) is not None else table.get(m.group(2))

    keep, pos = [], 0
    for a, b, _ in _blocks(routed_text):
        if net_of(routed_text[a:b], {}) in nets:
            keep.append(routed_text[pos:a])
            pos = b
    keep.append(routed_text[pos:])
    base = "".join(keep)
    back = []
    for a, b, _ in _blocks(krt_text):
        item = krt_text[a:b]
        n = net_of(item, names)
        if n in nets:
            back.append(re.sub(r'\(net (?:"[^"]*"|\d+)\)', lambda _m: f'(net "{n}")', item, count=1))
    table = {m.group(2): m.group(1) for m in re.finditer(r'\(net (\d+) "([^"]*)"\)', base)}      # a file with a net table writes items by id, one without by name
    if table:
        back = [re.sub(r'\(net "([^"]*)"\)', lambda m: f"(net {table[m.group(1)]})", b, count=1) for b in back]
    i = base.rstrip().rfind(")")
    return base[:i] + "\n".join(back) + "\n" + base[i:]


def dram_groups(net_names: list[str], context: list[str] | None = None) -> list[list[str]]:
    """The DRAM byte-lane and command/address groups KiCadRoutingTools' own classifier finds among `net_names` (run in its checkout as a separate process)."""
    root = krt_dir()
    if not root:
        raise RuntimeError("KiCadRoutingTools not found: set SPARKY_KRT")
    code = ("import sys, json, io, contextlib\nsys.path.insert(0, sys.argv[1] + '/py_router')\nimport length_matching as lm\n"
            "names = json.loads(sys.argv[2])\nbuf = io.StringIO()\nwith contextlib.redirect_stdout(buf):\n    g = lm.auto_group_ddr4_nets(names, json.loads(sys.argv[3]))\n"
            "print('SPARKY_GROUPS:' + json.dumps(g))\n")
    r = subprocess.run([sys.executable, "-c", code, str(root), json.dumps(net_names), json.dumps(context or net_names)], capture_output=True, text=True, timeout=120)
    for line in r.stdout.splitlines():
        if line.startswith("SPARKY_GROUPS:"):
            return json.loads(line[len("SPARKY_GROUPS:"):])
    raise RuntimeError(f"KRT DRAM classifier failed: {(r.stderr or r.stdout).strip()[-300:]}")


def pair_base(p: str, n: str) -> str:
    """KiCad's inDiffPair() takes the pair's base name (the net name without its polarity suffix)."""
    for pat, repl in _PAIR:
        if re.match(pat, p) and re.sub(pat, repl, p) == n:
            return re.sub(pat, r"\1", p)
    raise ValueError(f"'{p}'/'{n}' is not a recognised pair")


def dru_text(constraints: list) -> str:
    """KiCad custom rules (.kicad_dru) for the constraints: each gets a rule named `sparky_<i>` so a DRC violation maps back to it. KiCad's own measurement then
    decides (skew_out_of_range); the router reads the same file."""
    out = ["(version 1)"]
    for i, c in enumerate(constraints):
        if isinstance(c, DiffPair):
            out.append(f'(rule "sparky_{i}"\n  (condition "A.inDiffPair(\'{pair_base(c.p, c.n)}\')")\n  (constraint skew (max {c.skew_mm:g}mm)))')
        elif isinstance(c, LengthGroup) and c.time_ps is None:        # KiCad's skew rule is graded in mm: a delay-matched group has no KiCad verdict
            cond = " || ".join(f"A.NetName == '{n}'" for n in c.nets)
            out.append(f'(rule "sparky_{i}"\n  (condition "{cond}")\n  (constraint skew (max {c.tolerance_mm:g}mm)))')
    return "\n".join(out) + "\n"


def verify(pcb_text: str, constraints: list, layer_z: dict[str, float], drc_violations: list[dict] | None = None, rules=None) -> list[dict]:
    """Per constraint: KiCad's DRC verdict (authoritative: the rule `sparky_<i>` from dru_text was or was not violated) plus our own measurement of the routed copper
    (centre-line tracks + via barrels, a cross-check that differs from KiCad by a few hundredths of a mm)."""
    viol = drc_violations or []
    L = net_lengths(pcb_text, layer_z)
    rows = []
    for i, c in enumerate(constraints):
        if isinstance(c, DiffPair):
            lp, ln = L.get(c.p, 0.0), L.get(c.n, 0.0)
            rows.append({"kind": "diff_pair", "name": f"{c.p}/{c.n}", "lengths_mm": [round(lp, 3), round(ln, 3)], "skew_mm": round(abs(lp - ln), 3),
                         "limit_mm": c.skew_mm, "ok": lp > 0 and ln > 0 and not _hit(viol, i), "kicad": _actual(viol, i)})
        elif isinstance(c, LengthGroup) and c.time_ps is not None:
            rows.append(_delay_row(pcb_text, c, layer_z, rules))
        elif isinstance(c, LengthGroup):
            ls = [L.get(n, 0.0) for n in c.nets]
            spread = max(ls) - min(ls) if ls else 0.0
            rows.append({"kind": "length_group", "name": "+".join(c.nets), "lengths_mm": [round(x, 3) for x in ls], "skew_mm": round(spread, 3),
                         "limit_mm": c.tolerance_mm, "ok": min(ls, default=0) > 0 and not _hit(viol, i), "kicad": _actual(viol, i)})
    return rows
