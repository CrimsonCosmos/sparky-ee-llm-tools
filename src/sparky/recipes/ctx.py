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
        self.unsourced: list[str] = []            # parts with no catalogue match (no price/stock known)
        self.unreviewed: list[str] = []           # parts whose symbol/footprint came from a vendor import, not KiCad's reviewed libraries
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

    # ---- active parts: KiCad's reviewed library is the source of symbol + footprint ---------------------
    def ic(self, symbol: str, need: Need | None = None):
        """`symbol` = KiCad 'Library:Name' chosen by the designer (search: `sparky symbols <name>`). Pins and footprint come from KiCad's library.
        Sourcing/price come from the catalogue by that part name; `need` is a SPEC CHECK on the sourced part's catalogue attributes (voltage,
        current, ...) - a part that fails it is an error, not silently replaced. Returns (SKiDL part, Choice | None)."""
        from ..kicad_symbols import info
        si = info(symbol)
        if si is None:
            raise NoPart(f"KiCad symbol '{symbol}' not found; search with `sparky symbols <name>`, or import the vendor part explicitly with ctx.ic_import(lcsc)")
        if not si.footprint:
            raise NoPart(f"KiCad symbol '{symbol}' has no default footprint; pass the footprint explicitly")
        p = self.s.Part(si.lib, si.name, footprint=si.footprint)
        got = self.catalog.pick(Need("%", text=si.name, min_stock=1), qty=self.qty, limit=1) if self._has_catalog() else []
        if got:
            if need is not None and not Catalog._ok(got[0].attributes, need):
                raise NoPart(f"{si.name} ({got[0].mpn}) does not meet the requirement {need}; catalogue says {got[0].attributes}")
            return self._tag(p, got[0]), got[0]
        p.fields["MPN"] = si.name
        self.unsourced.append(si.name)
        return p, None

    def ic_import(self, lcsc: str):
        """Fallback when KiCad has no symbol: the vendor's own record for this exact part (libsrc). Flagged UNREVIEWED in the part's fields."""
        from ..libsrc import ensure_part
        pf = ensure_part(lcsc)
        p = self.s.Part(str(pf.symbol_file), pf.symbol_name, footprint=str(pf.footprint_file))
        p.fields["Source"] = "vendor-import-unreviewed"
        self.unreviewed.append(f"{pf.symbol_name} ({lcsc})")
        return p

    def _has_catalog(self) -> bool:
        return self._catalog is not None or db_path().exists()

    def _first(self, need: Need, what: str) -> Choice:
        got = self.catalog.pick(need, qty=self.qty, limit=1)
        if not got:
            raise NoPart(f"catalogue has no {what}")
        return got[0]


def _val(x: float) -> str:
    from ..units import fmt_si
    return fmt_si(x)

