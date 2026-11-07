"""KiCad exports: .kicad_pcb (v7 format), XML netlist, and a net-label based .kicad_sch."""
from __future__ import annotations

import math
import uuid
from xml.sax.saxutils import escape

from .. import sexpr
from ..sexpr import Q, dump
from ..model import Circuit, Pad
from ..pcb.board import Board


def _uuid(seed: str) -> Q:
    return Q(str(uuid.uuid5(uuid.NAMESPACE_URL, "sparky/" + seed)))


def _layers_def(n: int):
    layers = [[0, Q("F.Cu"), "signal"]]
    for i in range(1, n - 1):
        layers.append([i, Q(f"In{i}.Cu"), "signal"])
    layers.append([31, Q("B.Cu"), "signal"])
    layers += [[32, Q("B.Adhes"), "user", Q("B.Adhesive")], [33, Q("F.Adhes"), "user", Q("F.Adhesive")],
               [34, Q("B.Paste"), "user"], [35, Q("F.Paste"), "user"], [36, Q("B.SilkS"), "user", Q("B.Silkscreen")],
               [37, Q("F.SilkS"), "user", Q("F.Silkscreen")], [38, Q("B.Mask"), "user"], [39, Q("F.Mask"), "user"],
               [40, Q("Dwgs.User"), "user", Q("User.Drawings")], [41, Q("Cmts.User"), "user", Q("User.Comments")],
               [44, Q("Edge.Cuts"), "user"], [45, Q("Margin"), "user"], [46, Q("B.CrtYd"), "user", Q("B.Courtyard")],
               [47, Q("F.CrtYd"), "user", Q("F.Courtyard")], [48, Q("B.Fab"), "user"], [49, Q("F.Fab"), "user"]]
    return ["layers"] + layers


def layer_z_mm(rules, n: int) -> dict[str, float]:
    """Height (mm from the top copper surface) of each copper layer's centre, from the same stackup the exporter writes (copper per layer + equal cores)."""
    from ..pcb.rules import OZ_CU_MM
    names = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, n - 1)] + ["B.Cu"]
    cu = [rules.copper_oz_outer * OZ_CU_MM if k in (0, n - 1) else rules.copper_oz_inner * OZ_CU_MM for k in range(n)]
    diel = max((rules.thickness_mm - sum(cu) - 2 * rules.mask_thickness_mm) / (n - 1), 0.0)
    z, out = 0.0, {}
    for k, nm in enumerate(names):
        out[nm] = z + cu[k] / 2
        z += cu[k] + diel
    return out


def _stackup(rules, n: int) -> list:
    """(stackup ...) describing the physical layers, derived entirely from `rules` (copper weights, board thickness, mask, dielectric)."""
    from ..pcb.rules import OZ_CU_MM
    names = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, n - 1)] + ["B.Cu"]
    cu = [rules.copper_oz_outer * OZ_CU_MM if k in (0, n - 1) else rules.copper_oz_inner * OZ_CU_MM for k in range(n)]
    diel = max((rules.thickness_mm - sum(cu) - 2 * rules.mask_thickness_mm) / (n - 1), 0.0)
    st: list = ["stackup", ["layer", Q("F.SilkS"), ["type", Q("Top Silk Screen")]], ["layer", Q("F.Paste"), ["type", Q("Top Solder Paste")]],
                ["layer", Q("F.Mask"), ["type", Q("Top Solder Mask")], ["thickness", rules.mask_thickness_mm]]]
    for i, nm in enumerate(names):
        st.append(["layer", Q(nm), ["type", Q("copper")], ["thickness", round(cu[i], 5)]])
        if i < n - 1:
            st.append(["layer", Q(f"dielectric {i + 1}"), ["type", Q("core")], ["thickness", round(diel, 4)], ["material", Q("FR4")],
                       ["epsilon_r", rules.dielectric_er], ["loss_tangent", rules.dielectric_tan_d]])
    st += [["layer", Q("B.Mask"), ["type", Q("Bottom Solder Mask")], ["thickness", rules.mask_thickness_mm]],
           ["layer", Q("B.Paste"), ["type", Q("Bottom Solder Paste")]], ["layer", Q("B.SilkS"), ["type", Q("Bottom Silk Screen")]],
           ["copper_finish", Q("None")], ["dielectric_constraints", "no"]]
    return st


from .bom import UNASSEMBLED  # noqa: E402


def to_kicad_pcb(board: Board, title: str = "sparky") -> str:
    c = board.circuit
    nets = {"": 0}
    for n in c.nets:
        nets[n] = len(nets)
    pn = c.pin_net_map()
    nc: dict[str, str] = {}                           # KiCad's own names for pins left unconnected in the schematic (parity needs them on the pads)
    for ref, comp in c.components.items():
        for pin in comp.part.pins:
            if f"{ref}.{pin.number}" not in pn:
                nc[f"{ref}.{pin.number}"] = f"unconnected-({ref}-{pin.name}-Pad{pin.number})" if pin.name else f"unconnected-({ref}-Pad{pin.number})"
    for n in nc.values():
        nets[n] = len(nets)
    root: list = ["kicad_pcb", ["version", 20221018], ["generator", Q("sparky")],
                  ["general", ["thickness", board.rules.thickness_mm]], ["paper", Q("A4")],
                  _layers_def(len(board.layers)),
                  ["setup", _stackup(board.rules, len(board.layers)), ["pad_to_mask_clearance", board.rules.mask_expansion],
                   ["pcbplotparams", ["layerselection", "0x00010fc_ffffffff"], ["usegerberextensions", "false"]]]]
    for n, i in nets.items():
        root.append(["net", i, Q(n)])
    # footprints
    for ref, comp in c.components.items():
        pl = board.place[ref]
        fp = comp.footprint
        pn = c.pin_net_map()
        bot = pl.side == "bottom"
        F = "B" if bot else "F"
        mx = (lambda v: -v) if bot else (lambda v: v)          # a bottom part is the footprint flipped about its Y axis
        eff = ["effects", ["font", ["size", 0.8, 0.8], ["thickness", 0.12]]] + ([["justify", "mirror"]] if bot else [])
        fl: list = ["footprint", Q(fp.lib_id or f"sparky:{fp.name}"), ["layer", Q(f"{F}.Cu")], ["tstamp", _uuid(ref)],
                    ["at", pl.x, pl.y, pl.rot] if pl.rot else ["at", pl.x, pl.y],
                    ["fp_text", "reference", Q(ref), ["at", 0, -(abs(fp.courtyard[1]) + 1.0), 0], ["layer", Q(f"{F}.SilkS")],
                     eff, ["tstamp", _uuid(ref + "-ref")]],
                    ["fp_text", "value", Q(comp.value or comp.part.mpn), ["at", 0, abs(fp.courtyard[3]) + 1.0, 0], ["layer", Q(f"{F}.Fab")],
                     eff, ["tstamp", _uuid(ref + "-val")]],
                    ["property", Q("MPN"), Q(comp.part.mpn)],
                    ["property", Q("Datasheet"), Q(comp.part.datasheet_url or "~")],
                    ["attr", "smd" if not any(p.thru for p in fp.pads) else "through_hole"]
                    + (["exclude_from_pos_files", "exclude_from_bom"] if comp.part.category in UNASSEMBLED else [])]
        for s in fp.silk:
            if s[0] == "line":
                fl.append(["fp_line", ["start", mx(s[1]), s[2]], ["end", mx(s[3]), s[4]], ["stroke", ["width", 0.12], ["type", "solid"]], ["layer", Q(f"{F}.SilkS")], ["tstamp", _uuid(f"{ref}-s{len(fl)}")]])
            elif s[0] == "circle":
                fl.append(["fp_circle", ["center", mx(s[1]), s[2]], ["end", mx(s[1]) + s[3], s[2]], ["stroke", ["width", 0.12], ["type", "solid"]], ["fill", "none"], ["layer", Q(f"{F}.SilkS")], ["tstamp", _uuid(f"{ref}-c{len(fl)}")]])
        x0, y0, x1, y1 = fp.courtyard
        if bot:
            x0, x1 = -x1, -x0
        fl.append(["fp_rect", ["start", x0, y0], ["end", x1, y1], ["stroke", ["width", 0.05], ["type", "solid"]], ["fill", "none"], ["layer", Q(f"{F}.CrtYd")], ["tstamp", _uuid(f"{ref}-cy")]])
        for pad in fp.pads:
            net = (pn.get(f"{ref}.{pad.number}") or nc.get(f"{ref}.{pad.number}")) if pad.number else None
            thru = pad.thru
            ptype = "thru_hole" if (thru and pad.plated) else ("np_thru_hole" if thru else "smd")
            shape = {"rect": "rect", "circle": "circle", "oval": "oval", "roundrect": "roundrect"}.get(pad.shape, "rect")
            layers = ["*.Cu", "*.Mask"] if thru else [f"{F}.Cu", f"{F}.Paste", f"{F}.Mask"] if pad.paste else [f"{F}.Cu", f"{F}.Mask"]
            px = mx(pad.x)
            pl_ = ["pad", Q(pad.number), ptype, shape, ["at", px, pad.y, pl.rot] if pl.rot else ["at", px, pad.y],
                   ["size", pad.w, pad.h]]
            if thru:
                pl_.append(["drill", pad.drill])
            pl_.append(["layers"] + [Q(l) for l in layers])
            if shape == "roundrect":
                pl_.append(["roundrect_rratio", pad.rratio])
            if net:
                pl_.append(["net", nets[net], Q(net)])
            pl_.append(["tstamp", _uuid(f"{ref}-p{pad.number}-{pad.x}-{pad.y}")])
            fl.append(pl_)
        root.append(fl)
    k = 0
    # outline: straight edges as lines, rounded corners as true arcs
    W, H, r = board.width, board.height, board.corner_radius
    stroke = [["stroke", ["width", 0.1], ["type", "default"]], ["layer", Q("Edge.Cuts")]]
    seg = [((r, 0), (W - r, 0)), ((W, r), (W, H - r)), ((W - r, H), (r, H)), ((0, H - r), (0, r))]
    for a_, b_ in seg:
        if a_ != b_:
            k += 1
            root.append(["gr_line", ["start", *a_], ["end", *b_], *stroke, ["tstamp", _uuid(f"e{k}")]])
    if r > 0:
        d = r * (1 - 0.5 ** 0.5)
        for (st, mid, en) in (((W, r), (W - d, d), (W - r, 0)), ((W - r, H), (W - d, H - d), (W, H - r)), ((r, H), (d, H - d), (0, H - r)), ((0, r), (d, d), (r, 0))):
            k += 1
            root.append(["gr_arc", ["start", *st], ["mid", *mid], ["end", *en], *stroke, ["tstamp", _uuid(f"e{k}")]])
    if board.silk_text:
        k += 1
        root.append(["gr_text", Q(board.silk_text), ["at", board.rules.edge_clearance + 1.5, H - board.rules.edge_clearance - 1.5], ["layer", Q("F.SilkS")],
                     ["effects", ["font", ["size", 1.0, 1.0], ["thickness", board.rules.silk_width]], ["justify", "left"]], ["tstamp", _uuid(f"t{k}")]])
    # inner-layer planes are exported with the board (Freerouting routes the net through vias to the plane). Outer-layer POURS are added after routing
    # (`add_pours`): the router must not treat a fragmenting outer pour as connectivity, so those nets are routed as ordinary tracks first.
    for layer, net in board.plane_nets.items():
        if net in nets:
            k += 1
            root.append(_zone(board, nets[net], net, layer, f"z{k}"))
    return dump(root) + "\n"


def _zone(board: Board, net_id: int | None, net: str, layer: str, seed: str) -> list:
    """`net_id=None` writes the net by name (KiCad >= 9 files carry no net table); otherwise the older id + name form."""
    outline = board.outline.buffer(-board.rules.edge_clearance)
    netref = [["net", Q(net)]] if net_id is None else [["net", net_id], ["net_name", Q(net)]]
    return ["zone", *netref, ["layer", Q(layer)], ["tstamp", _uuid(seed)], ["hatch", "edge", 0.5],
            ["connect_pads", "yes", ["clearance", board.rules.clearance]], ["min_thickness", max(0.127, board.rules.min_track)],
            ["fill", "yes", ["thermal_gap", board.rules.plane_thermal_gap], ["thermal_bridge_width", board.rules.plane_spoke], ["island_removal_mode", 1]],
            ["polygon", ["pts"] + [["xy", x, y] for x, y in outline.exterior.coords[:-1]]]]


def add_pours(pcb_text: str, board: Board) -> str:
    """Append the outer-layer copper pours to a ROUTED board, using the net ids the routed file itself defines (pcbnew may renumber nets)."""
    import re
    ids = {m.group(2): int(m.group(1)) for m in re.finditer(r'\(net (\d+) "([^"]*)"\)', pcb_text)}
    named = not ids and bool(re.search(r'\(net "[^"]*"\)', pcb_text))      # KiCad >= 9 names nets on every item instead of keeping a net table
    zones = []
    for net, layers in board.pour_nets.items():
        for layer in layers:
            if layer not in board.plane_nets and (net in ids or named):
                zones.append(dump(_zone(board, None if named else ids[net], net, layer, f"pour-{net}-{layer}")))
    if not zones:
        return pcb_text
    i = pcb_text.rstrip().rfind(")")
    return pcb_text[:i] + "\n".join(zones) + "\n" + pcb_text[i:]


def to_kicad_netlist(circuit: Circuit) -> str:
    """KiCad XML netlist (importable via Pcbnew > File > Import > Netlist)."""
    out = ['(export (version "E")', f'  (design (source "{escape(circuit.name)}") (tool "sparky"))', "  (components"]
    for ref, c in circuit.components.items():
        fp = c.part.footprint if isinstance(c.part.footprint, str) else c.part.footprint.name
        out.append(f'    (comp (ref "{ref}") (value "{escape(c.value or c.part.mpn)}") (footprint "sparky:{escape(fp)}")'
                   f' (fields (field (name "MPN") "{escape(c.part.mpn)}")))')
    out.append("  )")
    out.append("  (nets")
    for i, (n, pins) in enumerate(circuit.nets.items(), 1):
        out.append(f'    (net (code "{i}") (name "{escape(n)}")')
        for p in pins:
            ref, _, pin = p.partition(".")
            out.append(f'      (node (ref "{ref}") (pin "{pin}"))')
        out.append("    )")
    out.append("  )\n)")
    return "\n".join(out) + "\n"
