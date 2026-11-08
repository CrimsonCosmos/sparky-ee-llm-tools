"""Back-drilling: remove the unused stub of high-speed through vias. KiCad 10 stores a back-drill in the via (`(backdrill ...)` from the top, `(tertiary_drill ...)` from the
bottom, each with a size and the layer pair it drills between) and exports one drill file per back-drill with its depth; we only decide where and how deep, from the fab's
capability (`Rules.backdrill`), and re-check the result.

KiCad's end layer E means the drill stops at the far face of E's copper, so E's copper is kept: with L the last layer the signal uses, E = L leaves no stub (but no margin);
the stub left for E below L is the distance between the faces of L and E. We pick the nearest E whose stub is at least the fab's depth tolerance (L is never cut) and at most
the fab's longest stub."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import sexpr
from .rules import OZ_CU_MM, Rules


@dataclass
class Job:
    at: tuple[float, float]
    net: str
    side: str            # 'top' | 'bottom'
    end_layer: str
    size_mm: float
    residual_mm: float   # stub left after drilling
    depth_mm: float      # drilled depth from that face (the number KiCad prints in the drill file)


@dataclass
class Finding:
    where: str
    problem: str


def _faces(rules: Rules, names: list[str], layer_z: dict[str, float]) -> dict[str, tuple[float, float]]:
    n = len(names)
    cu = {nm: (rules.copper_oz_outer if k in (0, n - 1) else rules.copper_oz_inner) * OZ_CU_MM for k, nm in enumerate(names)}
    return {nm: (layer_z[nm] - cu[nm] / 2, layer_z[nm] + cu[nm] / 2) for nm in names}


def _used_layers(pcb_text: str, nets: set[str], tol: float, pad_points: list[tuple[float, float, set[str]]]) -> dict[tuple[float, float, str], set[str]]:
    """Layers on which each via of `nets` is actually connected: tracks ending at its centre and pads at it."""
    root = sexpr.parse(pcb_text)
    names = {str(n[1]): str(n[2]) for n in root if isinstance(n, list) and len(n) >= 3 and n[0] == "net"}

    def net_of(node) -> str | None:
        nt = sexpr.find(node, "net")
        return names.get(str(nt[1]), str(nt[1])) if nt else None

    vias = [(float(sexpr.find(v, "at")[1]), float(sexpr.find(v, "at")[2]), net_of(v)) for v in root if isinstance(v, list) and v and v[0] == "via" and net_of(v) in nets]
    used: dict[tuple[float, float, str], set[str]] = {(x, y, n): set() for x, y, n in vias}
    for node in root:
        if isinstance(node, list) and node and node[0] in ("segment", "arc"):
            net = net_of(node)
            ly = sexpr.find(node, "layer")
            for key in ("start", "end"):
                p = sexpr.find(node, key)
                if p and ly:
                    for (x, y, n), u in used.items():
                        if n == net and abs(x - float(p[1])) < tol and abs(y - float(p[2])) < tol:
                            u.add(str(ly[1]))
    for (x, y, n), u in used.items():
        for px, py, ls in pad_points:
            if abs(px - x) < tol and abs(py - y) < tol:
                u |= ls
    return used


def plan(pcb_text: str, rules: Rules, nets: set[str], names: list[str], layer_z: dict[str, float], pad_points: list[tuple[float, float, set[str]]] | None = None,
         tol: float = 0.01) -> tuple[list[Job], list[Finding]]:
    """Back-drill jobs for the through vias of `nets` whose unused stub is longer than the fab's limit, and the vias that cannot be back-drilled within its capability."""
    bd = rules.backdrill
    if bd is None:
        return [], [Finding("-", "the fab profile offers no back-drilling")]
    f = _faces(rules, names, layer_z)
    idx = {nm: i for i, nm in enumerate(names)}
    top_surface, bottom_surface = f[names[0]][0], rules.thickness_mm - rules.mask_thickness_mm          # KiCad measures the depth from the bottom copper's outer face (mask side)
    jobs, findings = [], []
    for (x, y, net), used in _used_layers(pcb_text, nets, tol, pad_points or []).items():
        if not used or len(used) == len(names):
            continue
        order = sorted(idx[l] for l in used)
        first, last = order[0], order[-1]
        for side, ref, rng in (("bottom", last, range(last, len(names))), ("top", first, range(first, -1, -1))):
            stub = (f[names[-1]][1] - f[names[ref]][1]) if side == "bottom" else (f[names[ref]][0] - f[names[0]][0])
            if stub <= bd.max_stub_mm:
                continue
            for e in rng:
                residual = (f[names[e]][1] - f[names[ref]][1]) if side == "bottom" else (f[names[ref]][0] - f[names[e]][0])
                if residual >= bd.depth_tol_mm:
                    break
            else:
                findings.append(Finding(f"via at ({x}, {y}) net {net}", f"no end layer leaves a stub of at least {bd.depth_tol_mm} mm"))
                continue
            if residual > bd.max_stub_mm:
                findings.append(Finding(f"via at ({x}, {y}) net {net}", f"nearest feasible back-drill leaves a {residual:.3f} mm stub, over the fab's {bd.max_stub_mm} mm"))
                continue
            depth = (bottom_surface - f[names[e]][1]) if side == "bottom" else (f[names[e]][0] - top_surface)
            jobs.append(Job((x, y), net, side, names[e], 0.0, residual, depth))
    return jobs, findings


def apply(pcb_text: str, jobs: list[Job], via_drill_by_pos: dict[tuple[float, float], float] | None, oversize_mm: float) -> str:
    """Write the jobs into the board text: after each via's `(drill ...)`, a `(backdrill ...)` (top) or `(tertiary_drill ...)` (bottom) block."""
    from ..krt import _blocks
    by_pos: dict[tuple[float, float], list[Job]] = {}
    for j in jobs:
        by_pos.setdefault((round(j.at[0], 3), round(j.at[1], 3)), []).append(j)
    out, pos = [], 0
    for a, b, head in _blocks(pcb_text, ("via",)):
        item = pcb_text[a:b]
        m = re.search(r"\(at ([-\d.]+) ([-\d.]+)\)", item)
        d = re.search(r"\(drill ([\d.]+)\)", item)
        key = (round(float(m.group(1)), 3), round(float(m.group(2)), 3)) if m else None
        if key not in by_pos or not d or "backdrill" in item or "tertiary_drill" in item:
            continue
        size = round(float(d.group(1)) + oversize_mm, 4)
        blocks = "".join(f'\n\t\t({"backdrill" if j.side == "top" else "tertiary_drill"}\n\t\t\t(size {size:g})\n\t\t\t(layers "{ "F.Cu" if j.side == "top" else "B.Cu" }" "{j.end_layer}")\n\t\t)'
                         for j in by_pos[key])
        out.append(pcb_text[pos:a] + item[:d.end()] + blocks + item[d.end():])
        pos = b
    out.append(pcb_text[pos:])
    return "".join(out)


def check(pcb_text: str, rules: Rules, names: list[str], layer_z: dict[str, float], nets: set[str], pad_points=None, tol: float = 0.01) -> list[Finding]:
    """Independent re-read: every via of `nets` either has a stub within the fab's limit or a back-drill that (a) stops beyond its last used layer by at least the depth
    tolerance and (b) leaves at most the longest stub."""
    bd = rules.backdrill
    if bd is None:
        return [Finding("-", "the fab profile offers no back-drilling")]
    f = _faces(rules, names, layer_z)
    idx = {nm: i for i, nm in enumerate(names)}
    findings = []
    root = sexpr.parse(pcb_text)
    drilled: dict[tuple[float, float], dict[str, str]] = {}
    for v in root:
        if isinstance(v, list) and v and v[0] == "via":
            at = sexpr.find(v, "at")
            for tag, side in (("backdrill", "top"), ("tertiary_drill", "bottom")):
                blk = sexpr.find(v, tag)
                if blk:
                    ly = sexpr.find(blk, "layers")
                    drilled.setdefault((round(float(at[1]), 3), round(float(at[2]), 3)), {})[side] = str(ly[2])
    for (x, y, net), used in _used_layers(pcb_text, nets, tol, pad_points or []).items():
        if not used or len(used) == len(names):
            continue
        order = sorted(idx[l] for l in used)
        first, last = order[0], order[-1]
        d = drilled.get((round(x, 3), round(y, 3)), {})
        for side, ref in (("bottom", last), ("top", first)):
            where = f"via at ({x}, {y}) net {net}"
            raw = (f[names[-1]][1] - f[names[ref]][1]) if side == "bottom" else (f[names[ref]][0] - f[names[0]][0])
            e = d.get(side)
            if e is None:
                if raw > bd.max_stub_mm:
                    findings.append(Finding(where, f"{side} stub {raw:.3f} mm is over the fab's {bd.max_stub_mm} mm and not back-drilled"))
                continue
            ei = idx[e]
            beyond = ei >= ref if side == "bottom" else ei <= ref
            resid = (f[names[ei]][1] - f[names[ref]][1]) if side == "bottom" else (f[names[ref]][0] - f[names[ei]][0])
            if not beyond or resid < bd.depth_tol_mm - 1e-9:
                findings.append(Finding(where, f"{side} back-drill to {e} would cut or touch layer {names[ref]} the signal uses (residual {resid:.3f} mm < tolerance {bd.depth_tol_mm})"))
            elif resid > bd.max_stub_mm + 1e-9:
                findings.append(Finding(where, f"{side} back-drill to {e} leaves {resid:.3f} mm, over the fab's {bd.max_stub_mm} mm"))
    return findings
