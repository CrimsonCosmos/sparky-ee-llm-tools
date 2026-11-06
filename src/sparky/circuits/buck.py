"""Buck converter SKiDL circuit from a BuckDesign (pure numbers) and a KiCad library regulator.

Pins are addressed by NAME through the KiCad symbol (never by hand-written pin numbers)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..equations.buck import BuckDesign
from ..equations.eseries import nearest, E6
from . import skidl
from .passives import passive
from .pins import pins

@dataclass
class Block:
    """What a circuit function returns: the parts it created and its named interface nets."""
    parts: dict[str, object] = field(default_factory=dict)
    nets: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def build_buck(design: BuckDesign, *, ic_lib: str, ic_name: str, vin, vout, gnd, c_in: float = 2.2e-6, n_in: int = 3,
               c_out: float = 22e-6, n_out: int = 2, c_boot: float = 100e-9, en_to_vin: bool = True, footprint: str | None = None) -> Block:
    s = skidl()
    b = Block()
    ic = s.Part(ic_lib, ic_name, footprint=footprint) if footprint else s.Part(ic_lib, ic_name)
    b.parts["ic"] = ic
    sw, fb = s.Net("SW"), s.Net("FB")
    for p in pins(ic, "vin"):
        p += vin
    for p in pins(ic, "gnd") + pins(ic, "pad", required=False):
        p += gnd
    for p in pins(ic, "sw"):
        p += sw
    for p in pins(ic, "fb"):
        p += fb
    if en_to_vin:
        for p in pins(ic, "en", required=False):
            p += vin
    # feedback divider
    r_top, r_bot = passive("R", design.r_top), passive("R", design.r_bottom)
    vout & r_top & fb & r_bot & gnd
    # inductor
    l = passive("L", design.inductance, "1210", isat=f"{design.inductor_isat_min:.2f}A")
    sw & l & vout
    # bootstrap capacitor between BOOT and SW
    for p in pins(ic, "boot", required=False):
        cb = passive("C", c_boot, "0402")
        p += cb[1]
        cb[2] += sw
        b.parts["c_boot"] = cb
    # capacitors
    b.parts["c_in"] = [passive("C", c_in, "1210", voltage="100V") for _ in range(n_in)]
    b.parts["c_out"] = [passive("C", c_out, "1206") for _ in range(n_out)]
    for c in b.parts["c_in"]:
        vin & c & gnd
    for c in b.parts["c_out"]:
        vout & c & gnd
    b.parts.update(r_top=r_top, r_bottom=r_bot, inductor=l)
    b.nets.update(vin=vin, vout=vout, gnd=gnd, sw=sw, fb=fb)
    b.notes.extend(design.notes)
    return b
