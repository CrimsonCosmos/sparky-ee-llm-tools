"""Insertion loss of routed nets: conductor + dielectric loss per layer, from the stackup, with a frequency-dependent dielectric.
Microstrip (outer layers): scikit-rf MLine (Hammerstad-Jensen with Kirschning-Jansen dispersion, Hammerstad roughness, Djordjevic-Svensson wideband Debye dielectric).
Stripline (inner layers, adjacent layers ASSUMED reference planes): Z0 from an atlc 2-D solve; dielectric loss from scikit-rf's same wideband Debye model for a homogeneous
(TEM) dielectric; conductor loss from the symmetric-stripline closed form in Pozar, Microwave Engineering (A-factor expression), an ESTIMATE that is NOT independently
validated here. Vias are not modelled (a per-via input is accepted). Straight segments and arc chords only; no connector, package or die models."""
from __future__ import annotations

import math
import warnings

from .. import sexpr
from ..constants import COPPER_RESISTIVITY_OHM_M, MU0_H_PER_M, NP_TO_DB
from . import diffpair, lines


def available() -> bool:
    return lines.available() and diffpair.available()


def _freq(f_hz: float):
    import skrf
    return skrf.Frequency(f_hz, f_hz, npoints=1, unit="hz")


def microstrip_alpha_db_per_mm(w_mm, h_mm, t_mm, rules, f_hz, rho=COPPER_RESISTIVITY_OHM_M) -> float:
    from skrf.media import MLine
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = MLine(_freq(f_hz), w=w_mm * 1e-3, h=h_mm * 1e-3, t=max(t_mm, 1e-6) * 1e-3, ep_r=rules.dielectric_er, tand=rules.dielectric_tan_d, rho=rho,
                  rough=rules.copper_roughness_rms_m, f_epr_tand=rules.dielectric_f_ref_hz)
    return float(m.gamma[0].real) * NP_TO_DB * 1e-3


def stripline_alpha_db_per_mm(w_mm, hb_mm, ha_mm, t_mm, rules, f_hz, rho=COPPER_RESISTIVITY_OHM_M) -> float:
    from skrf.media import DefinedAEpTandZ0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        diel = DefinedAEpTandZ0(_freq(f_hz), A=0.0, ep_r=rules.dielectric_er, tanD=rules.dielectric_tan_d, f_ep=rules.dielectric_f_ref_hz, model="djordjevicsvensson")
    a_d = float(diel.gamma[0].real)                                                                  # Np/m, exact for a TEM line in a lossy dielectric
    b, t = (hb_mm + ha_mm + t_mm) * 1e-3, t_mm * 1e-3                                                # plane spacing, strip thickness (m)
    z0 = diffpair.stripline(w_mm, hb_mm, ha_mm, t_mm, rules.dielectric_er)
    rs = math.sqrt(math.pi * f_hz * MU0_H_PER_M * rho)                                              # smooth-conductor surface resistance (no roughness correction on stripline)
    big_a = 1 + 2 * (w_mm * 1e-3) / (b - t) + (1 / math.pi) * ((b + t) / (b - t)) * math.log((2 * b - t) / t)
    a_c = 2.7e-3 * rs * rules.dielectric_er * z0 / (30 * math.pi * (b - t)) * big_a                 # Pozar: valid for sqrt(er) Z0 < 120 ohm
    return (a_d + a_c) * NP_TO_DB * 1e-3


def net_loss_db(pcb_text: str, rules, layer_z: dict[str, float], f_hz: float, via_loss_db_each: float = 0.0) -> dict[str, float]:
    """dB per net at `f_hz`: sum over routed segments of length x loss/mm of their layer and width, plus `via_loss_db_each` per via."""
    from ..krt import _plane_gaps
    from ..pcb.rules import OZ_CU_MM
    root = sexpr.parse(pcb_text)
    names = {str(n[1]): str(n[2]) for n in root if isinstance(n, list) and len(n) >= 3 and n[0] == "net"}
    ordered = sorted(layer_z.items(), key=lambda kv: kv[1])
    outer = {ordered[0][0]: ordered[1][1], ordered[-1][0]: ordered[-2][1]}
    t = rules.copper_oz_outer * OZ_CU_MM
    cache: dict[tuple[str, float], float] = {}

    def per_mm(layer: str, w: float) -> float:
        key = (layer, round(w, 4))
        if key not in cache:
            if layer in outer:
                cu_next = (rules.copper_oz_outer if rules.layers == 2 else rules.copper_oz_inner) * OZ_CU_MM
                h = abs(layer_z[layer] - outer[layer]) - (t + cu_next) / 2
                cache[key] = microstrip_alpha_db_per_mm(w, h, t, rules, f_hz)
            else:
                hb, ha, tl = _plane_gaps(layer, ordered, rules)
                cache[key] = stripline_alpha_db_per_mm(w, hb, ha, tl, rules, f_hz)
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
                out[net] = out.get(net, 0.0) + math.hypot(float(b[1]) - float(a[1]), float(b[2]) - float(a[2])) * per_mm(str(ly[1]), float(w[1]))
        elif node[0] == "via":
            out[net] = out.get(net, 0.0) + via_loss_db_each
    return out


def check(pcb_text: str, budgets: list, rules, layer_z: dict[str, float]) -> list[dict]:
    """One row per LossBudget: the worst net's loss against the limit. NOT VERIFIED when scikit-rf or atlc is missing."""
    rows = []
    for b in budgets:
        base = {"kind": "loss_budget", "name": "+".join(b.nets), "limit_db": b.max_db, "f_hz": b.f_hz}
        if not available():
            rows.append({**base, "ok": None, "note": "insertion loss NOT verified: needs scikit-rf and atlc"})
            continue
        d = net_loss_db(pcb_text, rules, layer_z, b.f_hz, b.via_loss_db_each)
        vals = [d.get(n, 0.0) for n in b.nets]
        rows.append({**base, "losses_db": [round(v, 3) for v in vals], "ok": min(vals, default=0) > 0 and max(vals) <= b.max_db,
                     "note": "estimate: conductor + dielectric per layer (scikit-rf microstrip; atlc Z0 + Pozar stripline); " +
                             ("vias not modelled" if not b.via_loss_db_each else f"vias at {b.via_loss_db_each:g} dB each (input)") + "; no connector/package/die"})
    return rows
