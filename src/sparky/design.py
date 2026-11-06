"""SKiDL circuit -> the geometry/connectivity view the placer, exporters and flow consume.

SKiDL (with KiCad's symbol libraries) is the source of truth for *what* the circuit is. This module only attaches footprint geometry from KiCad's
footprint libraries and derives placement hints from topology (bypass caps next to the IC power pin they serve, switching nets as aggressors, connectors on the
edge). Nothing here is a per-block or per-part table: the hints come from net/pin names and pin electrical types in the symbols themselves."""
from __future__ import annotations

import re
from typing import Any

from .constraints import Cluster, Decouple, Edge
from .model import Circuit, NetAttr, PartDef, PinDef

_GND = re.compile(r"^(GND|PGND|AGND|DGND|VSS|VSSA|V-|COM)\w*$", re.I)
_SW = re.compile(r"^(SW|LX|PH|SWITCH|SW_?\d)$", re.I)
_PWR_PIN = re.compile(r"^(VDD|VCC|VIN|PVIN|AVIN|VBAT|VBUS|V\+|VS|VDDA|VDDIO\d*|VCCIO\d*)\w*$", re.I)
_CATEGORY = [(r"^R", "resistor"), (r"^C", "capacitor"), (r"^L", "inductor"), (r"^FB", "ferrite"), (r"^F\d", "fuse"), (r"^D", "diode"),
             (r"^LED", "led"), (r"^Q", "transistor"), (r"^(J|P|X)", "connector"), (r"^Y", "crystal"), (r"^(TP|T)\d", "testpoint"),
             (r"^(H|MH)", "mounting"), (r"^U", "ic")]
_ETYPE = {"input": "input", "output": "output", "bidir": "bidir", "tristate": "bidir", "passive": "passive", "pwrin": "power_in", "pwrout": "power_out",
          "openc": "output", "opene": "output", "nc": "nc", "free": "passive", "unspec": "passive"}


def _symbol_id(p: Any) -> str:
    """'Library:Name' of the KiCad symbol a SKiDL part was created from (SKiDL records the library file name on the part's lib)."""
    from pathlib import Path
    fn = getattr(getattr(p, "lib", None), "filename", None)
    return f"{Path(str(fn)).stem}:{p.name}" if fn else str(p.name)


def _category(ref: str) -> str:
    for pat, cat in _CATEGORY:
        if re.match(pat, ref):
            return cat
    return "ic"


def _etype(pin: Any) -> str:
    return _ETYPE.get(str(getattr(pin, "func", "passive")).split(".")[-1].lower(), "passive")


def from_skidl(circ: Any | None = None, name: str = "design") -> Circuit:
    """Build the placement/export view of a SKiDL circuit (default circuit when None). Footprints come from KiCad's libraries; a part without a
    footprint is an error, not a guess."""
    import builtins
    from .circuits import skidl
    skidl()
    circ = circ or builtins.default_circuit      # SKiDL's default circuit lives in builtins
    out = Circuit(name)
    for p in circ.parts:
        ref = p.ref
        if not p.footprint:
            raise ValueError(f"{ref} ({p.name}) has no footprint")
        fid = str(p.footprint)
        mpn = str(p.fields.get("MPN") or p.fields.get("mpn") or p.name)
        pins = [PinDef(str(pin.num), str(pin.name), _etype(pin)) for pin in p.pins]
        cat = str(p.fields.get("category") or _category(ref))
        part = PartDef(mpn=mpn, description=str(getattr(p, "description", "") or ""), category=cat, package=fid.split(":")[-1], pins=pins, footprint=fid,
                       value=str(p.value or ""), datasheet_url=str(getattr(p, "datasheet", "") or ""), params={"symbol": _symbol_id(p)})
        out.add(part, ref=ref, value=str(p.value or ""), power_w=float(p.fields.get("PowerW") or 0.0),
                **{k: v for k, v in p.fields.items() if k not in ("MPN", "mpn", "category", "PowerW")})
    for net in circ.nets:
        pins = [f"{pin.part.ref}.{pin.num}" for pin in net.pins]
        if pins and net.name and not net.name.startswith("N$") or len(pins) > 1:
            out.nets[str(net.name)] = pins
    _derive_attrs(out)
    _derive_constraints(out)
    return out


def _derive_attrs(c: Circuit) -> None:
    for net, pins in c.nets.items():
        names, power = [], False
        for pin in pins:
            ref, _, num = pin.partition(".")
            pd = c.components[ref].part.pin(num)
            if pd:
                names.append(pd.name)
                power = power or pd.etype in ("power_in", "power_out") or bool(_PWR_PIN.match(pd.name))
        ground = bool(_GND.match(net)) or any(_GND.match(n) for n in names)
        noisy = any(_SW.match(n) for n in names) or bool(_SW.match(net))
        role = "ground" if ground else "power" if power else "signal"
        c.net_attrs[net] = NetAttr(role=role, noisy=noisy)
    for ref, comp in c.components.items():
        if any(c.net_attrs[n].noisy for n, ps in c.nets.items() if any(p.startswith(ref + ".") for p in ps)):
            comp.part.emi = "aggressor"


def _derive_constraints(c: Circuit) -> None:
    claimed: dict[tuple[str, str], int] = {}
    net_of = c.pin_net_map()
    for ref, comp in c.components.items():
        if comp.part.category == "connector":
            c.constrain(Edge(ref))
        if comp.part.category != "capacitor" or len(comp.part.pins) != 2:
            continue
        nets = [net_of.get(f"{ref}.{p.number}") for p in comp.part.pins]
        pw = [n for n in nets if n and c.net_attrs[n].role == "power"]
        gn = [n for n in nets if n and c.net_attrs[n].role == "ground"]
        if not (pw and gn):
            continue
        cands = []                                  # (ic ref, pin number) power pins on that net, ICs only
        for pin in c.nets[pw[0]]:
            r, _, num = pin.partition(".")
            if c.components[r].part.category == "ic":
                cands.append((r, num))
        if not cands:
            continue
        key = (pw[0], "")
        i = claimed.get(key, 0)
        claimed[key] = i + 1
        r, num = cands[i % len(cands)]
        c.constrain(Decouple(ref, r, f"{r}.{num}"))
    for ref, comp in c.components.items():          # crystals: keep their load caps and the IC close
        if comp.part.category != "crystal":
            continue
        group = {ref}
        for pin in comp.part.pins:
            n = net_of.get(f"{ref}.{pin.number}")
            for q in c.nets.get(n, []):
                r = q.partition(".")[0]
                if c.components[r].part.category in ("capacitor", "ic"):
                    group.add(r)
        c.constrain(Cluster(sorted(group)))
