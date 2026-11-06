"""What a recipe is given: SKiDL, the engineering Policy, and part selection from the real catalogue + KiCad libraries.

A part exists for us only if it is BOTH purchasable (picker/jlcparts data) AND present in KiCad's symbol libraries (pins + footprint), so the netlist
never contains an invented pinout. If nothing qualifies we raise with the need that failed - no silent fallback to a made-up part."""
from __future__ import annotations

import re
from typing import Any

from ..circuits import skidl
from ..circuits.passives import chip_footprint
from ..picker import Catalog, Choice, Need, recipes as needs
from ..picker.catalog import db_path
from .policy import Policy


class NoPart(RuntimeError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class Ctx:
    def __init__(self, catalog: Catalog | None = None, policy: Policy | None = None, qty: int = 1):
        self.s = skidl()
        self.policy = policy or Policy()
        self.qty = qty
        self._catalog = catalog
        self.picks: dict[str, Choice] = {}          # ref -> purchasable choice (BOM / purchasing)

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            if not db_path().exists():
                raise NoPart(f"parts catalogue missing at {db_path()}; run `sparky download-parts`")
            self._catalog = Catalog()
        return self._catalog

    # ---- passives -----------------------------------------------------------------------
    def _tag(self, part: Any, ch: Choice) -> Any:
        part.fields["MPN"], part.fields["LCSC"] = ch.mpn, ch.lcsc
        part.fields["Manufacturer"] = ch.manufacturer
        if ch.datasheet:
            part.fields["Datasheet"] = ch.datasheet
        self.picks[part.ref] = ch
        return part

    def resistor(self, ohms: float, tolerance_pct: float = 1.0):
        ch = self._first(needs.resistor(ohms, tolerance_pct, self.policy.chip_resistor), f"resistor {ohms:g} ohm")
        p = self.s.Part("Device", "R", value=_val(ohms), footprint=chip_footprint("R", ch.package))
        return self._tag(p, ch)

    def capacitor(self, farads: float, working_v: float):
        """Ceramic cap rated >= working_v / policy.cap_voltage_derating."""
        rated = working_v / self.policy.cap_voltage_derating
        ch = self._first(needs.mlcc(farads, rated, self.policy.chip_capacitor), f"MLCC {farads:g} F >= {rated:g} V")
        p = self.s.Part("Device", "C", value=_val(farads), footprint=chip_footprint("C", ch.package))
        return self._tag(p, ch)

    # ---- active parts: need + KiCad symbol --------------------------------------------------
    def ic(self, need: Need, *, symbol_lib: str | None = None, scan: int = 60):
        """First catalogue part (basic/preferred first, then cheapest) that has a KiCad symbol of the same name; returns (SKiDL part, Choice)."""
        from ..kicad_symbols import find_symbols
        for ch in self.catalog.pick(need, qty=self.qty, limit=scan):
            for sym in find_symbols(ch.mpn, lib=symbol_lib, limit=5):
                if _norm(sym.name) == _norm(ch.mpn) or _norm(sym.name).startswith(_norm(ch.mpn)):
                    if not sym.footprint:
                        continue
                    p = self.s.Part(sym.lib, sym.name, footprint=sym.footprint)
                    return self._tag(p, ch), ch
        raise NoPart(f"no purchasable part with a KiCad symbol for: {need}")

    def _first(self, need: Need, what: str) -> Choice:
        got = self.catalog.pick(need, qty=self.qty, limit=1)
        if not got:
            raise NoPart(f"catalogue has no {what}")
        return got[0]


def _val(x: float) -> str:
    from ..units import fmt_si
    return fmt_si(x)
