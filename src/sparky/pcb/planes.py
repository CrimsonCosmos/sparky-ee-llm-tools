"""Plane/pour intent: which net owns which copper layer. KiCad fills the zones (kicad-cli --refill-zones); we never compute copper."""
from __future__ import annotations

from .board import Board


def _ground_net(board: Board) -> str | None:
    c = board.circuit
    best, score = None, -1
    for name, pins in c.nets.items():
        a = c.net_attrs.get(name)
        if ((a and a.role == "ground") or name in ("GND", "PGND")) and len(pins) > score:
            best, score = name, len(pins)
    return best


def _power_net(board: Board, exclude: set[str]) -> str | None:
    c = board.circuit
    best, score = None, 0.0
    for name, pins in c.nets.items():
        a = c.net_attrs.get(name)
        if name in exclude or not a or a.role != "power":
            continue
        s = len(pins) + a.current_a * 4
        if len(pins) >= 4 and s > score:
            best, score = name, s
    return best


def assign_planes(board: Board, outer_pour: bool = True, power_plane: bool = True) -> None:
    n = len(board.layers)
    gnd = _ground_net(board)
    board.plane_nets.clear()
    board.pour_nets.clear()
    if gnd is None:
        board.warnings.append("no ground net found; no planes assigned")
        return
    if n == 2:
        board.pour_nets[gnd] = list(board.layers)
        return
    board.plane_nets[board.layers[1]] = gnd
    pwr = _power_net(board, {gnd}) if power_plane else None
    if pwr:
        board.plane_nets[board.layers[-2] if n > 4 else board.layers[2]] = pwr
    if outer_pour:
        board.pour_nets[gnd] = [board.layers[0], board.layers[-1]]
