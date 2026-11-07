"""Board-level features an assembler or a test engineer expects, added to the circuit before placement: fiducials and test points.
Both are ordinary KiCad library parts (so they appear in the schematic, in the board and in the parity check) and are kept out of the BOM and CPL."""
from __future__ import annotations

from ..model import Circuit, PartDef, PinDef

FIDUCIAL_CORNERS = ("tl", "tr", "br")          # three corners, never a symmetric set, so the assembler can tell the board's orientation


def add_fiducials(circuit: Circuit, count: int, footprint: str) -> list[str]:
    """`count` (1..3) global fiducials at the corners, on the top face. Returns their references."""
    if not 1 <= count <= len(FIDUCIAL_CORNERS):
        raise ValueError(f"fiducials: count must be 1..{len(FIDUCIAL_CORNERS)}")
    part = PartDef(mpn="Fiducial", description="Fiducial marker", category="fiducial", package=footprint.split(":")[-1], footprint=footprint,
                   params={"symbol": "Mechanical:Fiducial"})
    return [circuit.add(part, ref=f"FID{i}", value="Fiducial", corner=FIDUCIAL_CORNERS[i - 1]).ref for i in range(1, count + 1)]


def add_test_points(circuit: Circuit, nets: list[str], footprint: str) -> list[str]:
    """One test point per net (a bare pad a probe can hold)."""
    missing = [n for n in nets if n not in circuit.nets]
    if missing:
        raise ValueError(f"test points: no such net(s): {', '.join(missing)}")
    refs = []
    for n in nets:
        part = PartDef(mpn="TestPoint", description="Test point", category="testpoint", package=footprint.split(":")[-1], footprint=footprint,
                       pins=[PinDef("1", "1", "passive")], params={"symbol": "Connector:TestPoint"})
        comp = circuit.add(part, value=n)
        circuit.connect(n, f"{comp.ref}.1")
        refs.append(comp.ref)
    return refs


def rail_nets(circuit: Circuit) -> list[str]:
    """Every power and ground net a recipe or the derivation marked as such, in a stable order."""
    return sorted(n for n, a in circuit.net_attrs.items() if a.role in ("power", "ground") and len(circuit.nets.get(n, [])) > 1)
