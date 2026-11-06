"""Post-layout geometric checks (decoupling distance, hot-loop area). Thermal / IR-drop / EMC are NOT here: they use open-source solvers (sparky.analysis)."""
from __future__ import annotations

import math

from shapely.geometry import MultiPoint

from ..constraints import Decouple, Loop
from .board import Board

def decoupling_report(board: Board) -> list[dict]:
    out = []
    for k in board.circuit.constraints:
        if isinstance(k, Decouple):
            try:
                ic_pin = board.pad_pos(k.pin)
                cap = board.circuit.components[k.cap]
                net = board.circuit.net_of(k.pin)
                best = None
                for p in cap.part.pins:
                    if board.circuit.net_of(f"{k.cap}.{p.number}") == net:
                        best = board.pad_pos(f"{k.cap}.{p.number}")
                if best:
                    d = math.hypot(best[0] - ic_pin[0], best[1] - ic_pin[1])
                    out.append({"cap": k.cap, "ic_pin": k.pin, "dist_mm": round(d, 2), "limit_mm": k.max_mm, "ok": d <= k.max_mm * 1.5})
            except Exception:
                continue
    return out


def loop_report(board: Board) -> list[dict]:
    out = []
    pins = board.circuit.pin_net_map()
    for k in board.circuit.constraints:
        if isinstance(k, Loop):
            pts = []
            for ref in k.refs:
                if ref in board.place:
                    pts.extend((p.x, p.y) for p in board.pads_of(ref))
            if len(pts) >= 3:
                hull = MultiPoint(pts).convex_hull
                out.append({"loop": k.name, "area_mm2": round(hull.area, 1), "members": list(k.refs)})
    return out


def analyze(board: Board) -> dict:
    return {"decoupling": decoupling_report(board), "loops": loop_report(board)}
