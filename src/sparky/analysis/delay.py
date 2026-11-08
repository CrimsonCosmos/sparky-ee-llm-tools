"""Propagation delay of routed nets, an INDEPENDENT measurement for checking delay (time) matching: KiCadRoutingTools matches with its own per-layer constants; this
re-derives each layer's speed from the stackup with scikit-rf (outer-layer microstrip effective permittivity at the routed width) and the TEM stripline relation (inner layers
see the full dielectric), and walks the routed copper. Estimates: ignores solder mask, surface roughness and dispersion."""
from __future__ import annotations

import math

from .. import sexpr
from ..constants import C0_M_PER_S
from . import lines


def ps_per_mm(er_eff: float) -> float:
    return math.sqrt(er_eff) / C0_M_PER_S * 1e12 * 1e-3


def net_delays_ps(pcb_text: str, rules, layer_z: dict[str, float]) -> dict[str, float]:
    """ps per net: straight segments and arc chords at the speed of their layer, plus via barrels at the dielectric's speed. Needs scikit-rf for the outer layers."""
    from ..pcb.rules import OZ_CU_MM
    root = sexpr.parse(pcb_text)
    names = {str(n[1]): str(n[2]) for n in root if isinstance(n, list) and len(n) >= 3 and n[0] == "net"}
    ordered = sorted(layer_z.items(), key=lambda kv: kv[1])
    outer = {ordered[0][0]: ordered[1][1], ordered[-1][0]: ordered[-2][1]}
    t = rules.copper_oz_outer * OZ_CU_MM
    cache: dict[tuple[str, float], float] = {}

    def speed(layer: str, w: float) -> float:
        if layer not in outer:
            return ps_per_mm(rules.dielectric_er)                                 # stripline: TEM in a homogeneous dielectric
        key = (layer, round(w, 4))
        if key not in cache:
            cu_next = (rules.copper_oz_outer if rules.layers == 2 else rules.copper_oz_inner) * OZ_CU_MM
            h = abs(layer_z[layer] - outer[layer]) - (t + cu_next) / 2
            cache[key] = ps_per_mm(lines.microstrip(w, h, t, rules.dielectric_er)[1])
        return cache[key]

    out: dict[str, float] = {}
    for node in root:
        if not (isinstance(node, list) and node):
            continue
        nt = sexpr.find(node, "net")
        if not nt:
            continue
        net = names.get(str(nt[1]), str(nt[1]))
        if node[0] in ("segment", "arc"):
            a, b, w, ly = (sexpr.find(node, k) for k in ("start", "end", "width", "layer"))
            if a and b and w and ly:
                out[net] = out.get(net, 0.0) + math.hypot(float(b[1]) - float(a[1]), float(b[2]) - float(a[2])) * speed(str(ly[1]), float(w[1]))
        elif node[0] == "via":
            ly = sexpr.find(node, "layers")
            if ly and str(ly[1]) in layer_z and str(ly[2]) in layer_z:
                out[net] = out.get(net, 0.0) + abs(layer_z[str(ly[1])] - layer_z[str(ly[2])]) * ps_per_mm(rules.dielectric_er)
    return out
