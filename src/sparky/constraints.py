"""Placement/EMI/thermal constraints that blocks attach to a Circuit.

Blocks know *why* a part must be somewhere (a decoupling cap belongs next to its IC
pin; a buck converter's hot loop must be tiny). The placer simply optimises them.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class _Remap:
    refs: tuple[str, ...] = ()

    def remap(self, refmap: dict[str, str], netmap: dict[str, str]) -> None:
        for f in ("cap", "ic", "ref", "a", "b"):
            if hasattr(self, f) and isinstance(getattr(self, f), str):
                setattr(self, f, refmap.get(getattr(self, f), getattr(self, f)))
        for f in ("refs", "group_a", "group_b"):
            if hasattr(self, f):
                setattr(self, f, [refmap.get(r, r) for r in getattr(self, f)])
        if hasattr(self, "pin") and isinstance(getattr(self, "pin"), str) and "." in self.pin:
            r, _, p = self.pin.partition(".")
            self.pin = f"{refmap.get(r, r)}.{p}"
        if hasattr(self, "net") and isinstance(self.net, str):
            self.net = netmap.get(self.net, self.net)

    def rename_net(self, old: str, new: str) -> None:
        if getattr(self, "net", None) == old:
            self.net = new


@dataclass
class Decouple(_Remap):
    """Bypass capacitor `cap` must sit within `max_mm` of power pin `pin` ('U1.24') of `ic`."""
    cap: str
    ic: str
    pin: str
    max_mm: float = 3.0
    weight: float = 6.0


@dataclass
class Loop(_Remap):
    """Components forming a high-di/dt current loop; minimise the area they span."""
    refs: list[str]
    name: str = "loop"
    weight: float = 4.0


@dataclass
class Cluster(_Remap):
    """Keep these components together (e.g. crystal + load caps next to the MCU)."""
    refs: list[str]
    weight: float = 1.5
    max_mm: float = 12.0


@dataclass
class Edge(_Remap):
    """Connector / interface part that should sit at the board edge (side: any|left|right|top|bottom)."""
    ref: str
    side: str = "any"
    weight: float = 3.0


@dataclass
class Separate(_Remap):
    """EMI/thermal keep-apart between two groups."""
    group_a: list[str]
    group_b: list[str]
    min_mm: float = 15.0
    weight: float = 2.0
    reason: str = "emi"


# ---- routing constraints (consumed by the KiCadRoutingTools stage, see sparky.krt) ------------------------------------------------
class _NetRemap:
    def remap(self, refmap: dict[str, str], netmap: dict[str, str]) -> None:
        for f in self._net_fields:
            v = getattr(self, f)
            setattr(self, f, [netmap.get(n, n) for n in v] if isinstance(v, list) else netmap.get(v, v))
        if hasattr(self, "ref"):
            self.ref = refmap.get(self.ref, self.ref)

    def rename_net(self, old: str, new: str) -> None:
        for f in self._net_fields:
            v = getattr(self, f)
            setattr(self, f, [new if n == old else n for n in v] if isinstance(v, list) else (new if v == old else v))


@dataclass
class DiffPair(_NetRemap):
    """Coupled differential pair. width/gap come from the interface standard or the impedance target (the designer's number, never a default);
    skew_mm = allowed P/N length difference. The net names must follow a pairing convention the router recognises (X_P/X_N, X+/X-, XP/XN)."""
    p: str
    n: str
    width_mm: float
    gap_mm: float
    skew_mm: float
    impedance_ohm: float | None = None      # differential target; when set the router solves the width from the stackup and width_mm is only the floor
    _net_fields = ("p", "n")


@dataclass
class LengthGroup(_NetRemap):
    """Nets whose routed lengths must agree within tolerance_mm (e.g. a parallel bus)."""
    nets: list[str]
    tolerance_mm: float
    _net_fields = ("nets",)


@dataclass
class ControlledImpedance(_NetRemap):
    """Single-ended nets with a target impedance (e.g. 50 ohm): the router solves each layer's width from the board stackup. tolerance_pct is the acceptable error
    when the routed widths are checked against the independent IPC-2141 microstrip formula (that formula is itself only accurate to a few percent)."""
    nets: list[str]
    impedance_ohm: float
    tolerance_pct: float
    _net_fields = ("nets",)


@dataclass
class HotLoop:
    """A switching loop to assess for radiated emission: the switch IC's supply and ground pins, the input capacitors that close the loop, and the switched current
    (peak, duty at its maximum, switching frequency, edge time). Declared only when every number has a source (datasheet / measurement)."""
    ref: str                 # switch IC
    hi_pin: str              # its supply pin number
    lo_pin: str              # its ground pin number
    cap_refs: list[str]      # input capacitors closing the loop
    ipk_a: float
    duty: float
    fsw_hz: float
    rise_s: float

    def remap(self, refmap: dict[str, str], netmap: dict[str, str]) -> None:
        self.ref = refmap.get(self.ref, self.ref)
        self.cap_refs = [refmap.get(r, r) for r in self.cap_refs]


@dataclass
class Fanout:
    """Escape-route the balls of this array package (BGA/QFN) before routing the nets."""
    ref: str

    def remap(self, refmap: dict[str, str], netmap: dict[str, str]) -> None:
        self.ref = refmap.get(self.ref, self.ref)
