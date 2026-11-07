"""Ground-stitching vias on a routed board: a grid of vias that tie the pours/planes of one net together so return currents have a short way between layers
and no pour fragment floats. Pitch comes from the highest frequency the board must behave at (lambda/20 in the board's dielectric) unless given.
Only positions clear of every other net's copper and holes are used; KiCad's DRC remains the judge of the result."""
from __future__ import annotations

import math
import re

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from .. import sexpr
from ..sexpr import Q, dump
from .board import Board

C0_M_PER_S = 299_792_458.0            # speed of light in vacuum
WAVELENGTH_FRACTION = 20              # stitching rule of thumb: spacing <= lambda / 20 at the highest frequency of concern


def pitch_for_frequency(f_hz: float, dielectric_er: float) -> float:
    """lambda/20 in mm, using the effective permittivity of a microstrip-like environment, (er + 1) / 2."""
    er_eff = (dielectric_er + 1.0) / 2.0
    return C0_M_PER_S / (WAVELENGTH_FRACTION * f_hz * math.sqrt(er_eff)) * 1e3


def _copper(pcb_text: str, board: Board, net: str):
    """Obstacles for a via of `net`: (geometry, clearance_extra) of every other net's copper on any layer, plus the drills."""
    tree = sexpr.parse(pcb_text)
    ids = {n[2]: int(n[1]) for n in sexpr.find_all(tree, "net") if len(n) == 3 and str(n[1]).lstrip("-").isdigit()}
    by_id = {v: k for k, v in ids.items()}

    def name_of(item_net) -> str:                          # KiCad files name a track's net either by id or, since 9, by name
        v = item_net[1]
        return by_id.get(int(v)) if str(v).lstrip("-").isdigit() else str(v)
    obst, holes = [], []
    for sg in sexpr.find_all(tree, "segment"):
        d = {x[0]: x for x in sg if isinstance(x, list)}
        if name_of(d["net"]) == net:
            continue
        a, b, w = d["start"], d["end"], float(d["width"][1])
        obst.append(LineString([(float(a[1]), float(a[2])), (float(b[1]), float(b[2]))]).buffer(w / 2))
    for v in sexpr.find_all(tree, "via"):
        d = {x[0]: x for x in v if isinstance(x, list)}
        at, size, drill = d["at"], float(d["size"][1]), float(d["drill"][1])
        p = Point(float(at[1]), float(at[2]))
        holes.append((p, drill / 2))
        if name_of(d["net"]) != net:
            obst.append(p.buffer(size / 2))
    for pad in board.pads():
        if pad.net != net:
            obst.append(pad.geom())
        if pad.drill > 0:
            holes.append((Point(pad.x, pad.y), pad.drill / 2))
    return obst, holes, ids


def add_stitching_vias(pcb_text: str, board: Board, net: str, pitch_mm: float) -> tuple[str, int]:
    """Insert stitching vias for `net` on a pitch grid. Returns (new text, number of vias added)."""
    r = board.rules
    layers = list(board.pour_nets.get(net, [])) + [l for l, n in board.plane_nets.items() if n == net]
    if len(set(layers)) < 2:
        return pcb_text, 0                                  # a via ties layers together: it needs the net's copper on at least two of them
    obst, holes, ids = _copper(pcb_text, board, net)
    named = bool(re.search(r'\(net "[^"]*"\)', pcb_text))        # the file names nets on tracks (no ids) -> write vias the same way
    if net not in ids and not named:
        return pcb_text, 0
    tree = STRtree(obst) if obst else None
    keep_in = board.outline.buffer(-(r.edge_clearance + r.via_dia / 2))
    reach = r.via_dia / 2 + r.clearance
    x0, y0, x1, y1 = keep_in.bounds
    placed: list[Point] = []
    out = []
    ny, nx = int((y1 - y0) // pitch_mm) + 1, int((x1 - x0) // pitch_mm) + 1
    for j in range(ny):
        for i in range(nx):
            p = Point(x0 + i * pitch_mm, y0 + j * pitch_mm)
            if not keep_in.contains(p):
                continue
            if tree is not None and any(obst[k].distance(p) < reach for k in tree.query(p.buffer(reach + 1e-6))):
                continue
            if any(h.distance(p) < r.via_drill / 2 + hr + r.hole_clearance for h, hr in holes + [(q, r.via_drill / 2) for q in placed]):
                continue
            placed.append(p)
            out.append(["via", ["at", round(p.x, 4), round(p.y, 4)], ["size", r.via_dia], ["drill", r.via_drill],
                        ["layers", Q(board.layers[0]), Q(board.layers[-1])], ["net", Q(net) if named else ids[net]], ["uuid" if named else "tstamp", Q(_stamp(net, i, j))]])
    if not out:
        return pcb_text, 0
    i = pcb_text.rstrip().rfind(")")
    return pcb_text[:i] + "\n".join(dump(v) for v in out) + "\n" + pcb_text[i:], len(out)


def _stamp(net: str, i: int, j: int) -> str:
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"stitch:{net}:{i}:{j}"))
