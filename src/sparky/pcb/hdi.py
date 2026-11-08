"""Blind / buried / micro vias without a router of our own.

Freerouting understands partial-depth vias when the Specctra DSN lists padstacks that have copper on only some layers and offers them to the net class; KiCad's
exporter writes only the through via, so `patch_dsn` adds the fab's partial-depth padstacks (ordered before the through via, which the autorouter otherwise always
prefers). KiCad's SES import maps a padstack named Via[a-b]_dia:drill_um to a via on layers a..b but labels every partial via 'buried'; `retype_vias` gives each its true
type from the fab's span table. KiCad's own DRC then judges the result with the per-type limits `dru_text` writes, and `check` re-reads the copper independently
(span in the table, sizes, aspect ratio). What the fab will really build is the fab's call: its DFM check is not part of this."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import sexpr
from .rules import Rules, ViaSpan


def padstack_name(sp: ViaSpan) -> str:
    return f'"Via[{sp.first}-{sp.last}]_{round(sp.dia * 1000)}:{round(sp.drill * 1000)}_um"'


def patch_dsn(dsn: str, spans: tuple[ViaSpan, ...], layers: list[str]) -> str:
    """Add each span's padstack and offer it (first in the list) wherever the through via is offered."""
    if not spans:
        return dsn
    stacks = "".join(f"    (padstack {padstack_name(sp)}\n" + "".join(f"      (shape (circle {layers[i]} {round(sp.dia * 1000)}))\n" for i in range(sp.first, sp.last + 1))
                     + "      (attach off)\n    )\n" for sp in spans)
    names = " ".join(padstack_name(sp) for sp in spans)
    through = re.search(r'\(via ("Via\[0-\d+\][^"]*")\)', dsn)
    if not through:
        raise ValueError("DSN has no through-via padstack to order the partial vias before")
    t = through.group(1)
    dsn = dsn.replace(f"(via {t})", f"(via {names} {t})", 1)
    dsn = dsn.replace(f"(use_via {t})", f"(use_via {names} {t})")
    i = dsn.index("  (network")
    j = dsn.rindex("  )\n", 0, i)                        # the closing of (library ...)
    return dsn[:j] + stacks + dsn[j:] + ""


def retype_vias(pcb_text: str, spans: tuple[ViaSpan, ...], n_layers: int, names: list[str]) -> str:
    """Rewrite each partial via's type word from the span table (KiCad's SES import calls them all 'buried')."""
    by_layers = {(names[sp.first], names[sp.last]): sp.kind for sp in spans}
    out, pos = [], 0
    for m in re.finditer(r"\(via(\s+(?:blind|buried|micro))?\s*\n", pcb_text):
        end = pcb_text.find("\n\t)", m.end())
        end = len(pcb_text) if end < 0 else end
        body = pcb_text[m.end():end]
        ly = re.search(r'\(layers "([^"]+)" "([^"]+)"\)', body)
        if not ly or (ly.group(1), ly.group(2)) == (names[0], names[-1]):
            continue
        kind = by_layers.get((ly.group(1), ly.group(2)))
        if kind is None:
            continue                                      # not in the table: left as KiCad wrote it so `check` reports it
        out.append(pcb_text[pos:m.start()] + f"(via {kind}\n")
        pos = m.end()
    out.append(pcb_text[pos:])
    return "".join(out)


def dru_text(spans: tuple[ViaSpan, ...], rules: Rules) -> str:
    """KiCad custom rules, one per via type. The board-setup minimums are loosened to the loosest type (kicad_backend.write_kicad_pro); these rules put the through
    via's own limits back for through vias and give blind/buried and micro vias the fab's limits for their spans."""
    out = [f'(rule sparky_via_through\n  (condition "A.Via_Type == \'Through\'")\n'
           f"  (constraint via_diameter (min {rules.min_drill + 2 * rules.min_annular:g}mm))\n  (constraint hole_size (min {rules.min_drill:g}mm))\n"
           f"  (constraint annular_width (min {rules.min_annular:g}mm)))"]
    for kicad_type, kinds in (("Micro", ("micro",)), ("Blind/buried", ("blind", "buried"))):
        group = [sp for sp in spans if sp.kind in kinds]
        if group:
            out.append(f'(rule sparky_via_{kinds[0]}\n  (condition "A.Via_Type == \'{kicad_type}\'")\n'
                       f"  (constraint via_diameter (min {min(s.dia for s in group):g}mm))\n  (constraint hole_size (min {min(s.drill for s in group):g}mm))\n"
                       f"  (constraint annular_width (min {min(s.annular for s in group):g}mm)))")
    return "\n".join(out) + "\n"


@dataclass
class ViaFinding:
    where: str
    problem: str


def check(pcb_text: str, rules: Rules, layer_z: dict[str, float], names: list[str]) -> tuple[list[ViaFinding], dict[str, int]]:
    """Independent re-read of every via in the routed copper: a partial via must be one the fab offers (same layers, same type, pad and drill at least the span's),
    within the span's aspect ratio. Returns (findings, count per span label)."""
    table = {(names[sp.first], names[sp.last]): sp for sp in rules.via_spans}
    findings, counts = [], {}
    for v in sexpr.find_all(sexpr.parse(pcb_text), "via"):
        ly, at, size, drill = sexpr.find(v, "layers"), sexpr.find(v, "at"), sexpr.find(v, "size"), sexpr.find(v, "drill")
        pair = (str(ly[1]), str(ly[2]))
        if pair == (names[0], names[-1]):
            continue
        where = f"via at ({at[1]}, {at[2]}) {pair[0]}-{pair[1]}"
        kind = str(v[1]) if isinstance(v[1], str) and v[1] in ("blind", "buried", "micro") else "through"
        sp = table.get(pair)
        if sp is None:
            findings.append(ViaFinding(where, "layer span is not one the fab offers"))
            continue
        counts[sp.label(len(names))] = counts.get(sp.label(len(names)), 0) + 1
        if kind != sp.kind:
            findings.append(ViaFinding(where, f"typed {kind} but the fab's span is {sp.kind}"))
        if float(size[1]) < sp.dia - 1e-6 or float(drill[1]) < sp.drill - 1e-6:
            findings.append(ViaFinding(where, f"size {size[1]}/{drill[1]} below the span's {sp.dia:g}/{sp.drill:g} mm"))
        depth = abs(layer_z[pair[1]] - layer_z[pair[0]])
        if depth / float(drill[1]) > sp.max_aspect + 1e-6:
            findings.append(ViaFinding(where, f"aspect ratio {depth / float(drill[1]):.2f} exceeds {sp.max_aspect:g}"))
    findings += _stacked(pcb_text, table)
    return findings, counts


def _stacked(pcb_text: str, table: dict) -> list[ViaFinding]:
    """Two partial vias at the same place whose spans share a layer are stacked: allowed only where the fab says both spans are stackable."""
    seen: dict[tuple[float, float], list[tuple[str, str]]] = {}
    for v in sexpr.find_all(sexpr.parse(pcb_text), "via"):
        ly, at = sexpr.find(v, "layers"), sexpr.find(v, "at")
        pair = (str(ly[1]), str(ly[2]))
        if pair in table:
            seen.setdefault((round(float(at[1]), 2), round(float(at[2]), 2)), []).append(pair)
    out = []
    for (x, y), pairs in seen.items():
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                a, b = pairs[i], pairs[j]
                if a != b and not (table[a].stackable and table[b].stackable) and ({a[0], a[1]} & {b[0], b[1]}):
                    out.append(ViaFinding(f"vias at ({x}, {y})", f"stacked {a[0]}-{a[1]} on {b[0]}-{b[1]} but the fab does not allow stacked vias"))
    return out


def lamination_sequence(rules: Rules, names: list[str]) -> list[str]:
    """The pressing/drilling order the span table implies (a DERIVED plan for the fab to confirm, not the fab's process): inner (buried) spans must be formed
    before the layers above and below are pressed on; outer blind/micro spans are drilled after the final press; through holes last."""
    steps = []
    buried = sorted((sp for sp in rules.via_spans if sp.kind == "buried"), key=lambda sp: sp.last - sp.first)
    for sp in buried:
        steps.append(f"laminate layers {names[sp.first]}..{names[sp.last]} as a sub-stack, drill and plate the buried span {sp.first + 1}-{sp.last + 1}")
    steps.append("press the remaining layers on (final lamination)")
    for sp in rules.via_spans:
        if sp.kind in ("blind", "micro"):
            steps.append(f"drill {'(laser) ' if sp.kind == 'micro' else ''}and plate the {sp.kind} span {sp.first + 1}-{sp.last + 1}")
    steps.append("drill and plate the through holes")
    return steps
