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
        """One ceramic cap of NOMINAL value `farads` (as a datasheet specifies), rated >= working_v / policy.cap_voltage_derating. For capacitance computed
        from equations (where usable capacitance matters) use `capacitors`."""
        rated = working_v / self.policy.cap_voltage_derating
        ch = self._first(needs.mlcc(farads, rated, self.policy.chip_capacitor), f"MLCC {farads:g} F >= {rated:g} V")
        p = self.s.Part("Device", "C", value=_val(farads), footprint=chip_footprint("C", ch.package))
        return self._tag(p, ch)

    def capacitors(self, total_f: float, working_v: float) -> list:
        """Standard-value MLCCs in parallel giving >= total_f of USABLE capacitance at working_v: the fewest identical parts (<= policy.max_parallel_caps)
        whose nominal value is in [need/n, window*need/n], rated >= working_v / derating."""
        pol = self.policy
        nominal = total_f / pol.mlcc_effective_fraction
        rated = working_v / pol.cap_voltage_derating
        for n in range(1, pol.max_parallel_caps + 1):
            low = nominal / n
            ch = self.catalog.pick(needs.mlcc_between(low, low * pol.cap_value_window, rated, pol.chip_capacitor), qty=self.qty * n, limit=1)
            if ch:
                farads = _parse_f(ch[0].attributes["Capacitance"])
                return [self._tag(self.s.Part("Device", "C", value=_val(farads), footprint=chip_footprint("C", ch[0].package)), ch[0]) for _ in range(n)]
        raise NoPart(f"catalogue has no MLCC combination for {total_f:g} F usable at {working_v:g} V (nominal {nominal:g} F, rated >= {rated:g} V, <= {pol.max_parallel_caps} parts)")

    def electrolytics(self, total_f: float, working_v: float) -> list:
        """Aluminium electrolytics in parallel: fewest identical parts (<= policy.max_parallel_caps) with nominal >= total_f, rated >= working_v / derating,
        and a package that maps to a KiCad footprint."""
        from ..picker.footprints import electrolytic as fp_for
        pol = self.policy
        rated = working_v / pol.cap_voltage_derating
        for n in range(1, pol.max_parallel_caps + 1):
            low = total_f / n
            for ch in self.catalog.pick(needs.electrolytic(low, rated, low * pol.cap_value_window), qty=self.qty * n, limit=20):
                fp = fp_for(ch.package)
                if fp:
                    farads = _parse_f(ch.attributes["Capacitance"])
                    return [self._tag(self.s.Part("Device", "C_Polarized", value=_val(farads), footprint=fp), ch) for _ in range(n)]
        raise NoPart(f"catalogue has no electrolytic combination for {total_f:g} F at {working_v:g} V with a known KiCad footprint")

    def tvs(self, standoff_min: float):
        """Unidirectional TVS by parametric search (stand-off just above the working voltage); generic diode symbol, footprint from the package via the KiCad library."""
        from ..picker.footprints import diode as fp_for
        for ch in self.catalog.pick(needs.tvs(standoff_min, unidirectional=True), qty=self.qty, limit=30):
            fp = fp_for(ch.package)
            if fp:
                return self._tag(self.s.Part("Device", "D_Zener", value=ch.mpn, footprint=fp), ch)
        raise NoPart(f"catalogue has no unidirectional TVS with stand-off >= {standoff_min:g} V in a package with a KiCad footprint")

    def fuse(self, amps: float, volts: float):
        from ..circuits.passives import chip_footprint
        need = needs.fuse(amps * self.policy.fuse_margin, volts, self.policy.chip_fuse)
        ch = self._first(need, f"fuse >= {amps * self.policy.fuse_margin:g} A, {volts:g} V DC")
        return self._tag(self.s.Part("Device", "Fuse", value=f"{ch.attributes.get('Current Rating', '')}", footprint=chip_footprint("F", ch.package)), ch)

    def generic(self, symbol: str, footprint: str, **fields):
        """A part whose symbol and footprint the DESIGNER names (connectors, headers, mounting holes): both must exist in KiCad's libraries."""
        from ..kicad_symbols import info
        from ..footprints import kicad_mod_path
        if info(symbol) is None:
            raise NoPart(f"KiCad symbol '{symbol}' not found")
        if kicad_mod_path(footprint) is None:
            raise NoPart(f"KiCad footprint '{footprint}' not found")
        lib, name = symbol.split(":", 1)
        p = self.s.Part(lib, name, footprint=footprint)
        for k, v in fields.items():
            p.fields[k] = v
        return p

    def inductor(self, henries: float, isat_min: float, footprint: str | None = None):
        """Inductor with L >= design (within the picker's window) and Isat >= isat_min. Chip-code packages map to KiCad footprints by name; other
        packages need an explicit KiCad `footprint` (the designer's choice) - we do not guess a land pattern."""
        need = needs.inductor(henries, isat_min)
        if footprint is None:
            need.package = self.policy.chip_inductor
        ch = self._first(need, f"inductor {henries:g} H, Isat >= {isat_min:g} A" + ("" if footprint else " in chip packages (else pass footprint=)"))
        fp = footprint or chip_footprint("L", ch.package)
        p = self.s.Part("Device", "L", value=_val(henries), footprint=fp)
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



def _parse_f(text: str) -> float:
    from ..picker.catalog import quantity
    return float(quantity(text).to("farad").magnitude)
