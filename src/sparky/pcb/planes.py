"""Plane/pour intent: which net owns which copper layer. KiCad fills the zones (kicad-cli --refill-zones); we never compute copper.

Stackup principle (any layer count): every inner SIGNAL layer sits next to a reference plane (return current path, controlled impedance); outer layers carry parts and
signals with a ground pour. Planes go on inner layers 1 and n-2 and on every second layer between them, then any inner layer still without a plane neighbour gets one.
Nets take plane slots in order: ground first, then power nets by importance, ground repeated in the remaining slots (power planes are always next to ground planes)."""
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


def _power_nets(board: Board, exclude: set[str]) -> list[str]:
    c = board.circuit
    scored = []
    for name, pins in c.nets.items():
        a = c.net_attrs.get(name)
        if name in exclude or not a or a.role != "power" or len(pins) < 4:
            continue
        scored.append((len(pins) + a.current_a * 4, name))
    return [n for _, n in sorted(scored, reverse=True)]


def plane_indices(n: int) -> list[int]:
    """Inner-layer indices that carry planes for an n-layer board (n >= 4)."""
    inner = list(range(1, n - 1))
    planes = {1, n - 2} | set(range(3, n - 2, 2))
    for i in inner:                                      # every inner signal layer needs a plane neighbour
        if i not in planes and not ({i - 1, i + 1} & planes):
            planes.add(i)
    return sorted(planes)


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
    slots = plane_indices(n)
    power = _power_nets(board, {gnd}) if power_plane else []
    pi = 0
    for k, idx in enumerate(slots):
        if k % 2 == 0 or pi >= len(power):               # ground on even slots (and wherever power nets run out), power nets on odd slots in order
            board.plane_nets[board.layers[idx]] = gnd
        else:
            board.plane_nets[board.layers[idx]] = power[pi]
            pi += 1
    if outer_pour:
        board.pour_nets[gnd] = [board.layers[0], board.layers[-1]]
