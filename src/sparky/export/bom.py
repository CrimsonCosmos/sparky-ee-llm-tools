"""BOM, pick-and-place and purchasing-oriented part lists."""
from __future__ import annotations

import csv
import io
from collections import OrderedDict

from ..model import Circuit
from ..pcb.board import Board


UNASSEMBLED = ("mounting", "testpoint", "fiducial")        # on the board but not parts the assembler places or buys


def _fp_name(c) -> str:
    fp = c.part.footprint
    return fp if isinstance(fp, str) else fp.name


def group_bom(circuit: Circuit) -> list[dict]:
    """One row per (MPN, value, footprint). Prices/links come from the catalogue fields recorded on each component (a snapshot, not a live quote)."""
    groups: "OrderedDict[tuple, dict]" = OrderedDict()
    for ref, c in circuit.components.items():
        mech = c.dnp or c.part.category in UNASSEMBLED
        key = ("__mech__", c.part.mpn) if mech else (c.part.mpn, c.value, _fp_name(c))
        g = groups.setdefault(key, {"refs": [], "comp": c, "dnp": mech})
        g["refs"].append(ref)
    rows = []
    for g in groups.values():
        c, p = g["comp"], g["comp"].part
        f = c.props
        rows.append({
            "refs": sorted(g["refs"], key=lambda r: (r.rstrip("0123456789"), int("".join(ch for ch in r if ch.isdigit()) or 0))),
            "qty": len(g["refs"]), "mpn": p.mpn, "manufacturer": f.get("Manufacturer", ""), "value": c.value, "footprint": _fp_name(c), "package": p.package,
            "description": p.description, "category": p.category, "dnp": g["dnp"], "lcsc": f.get("LCSC", ""),
            "unit_price": f.get("UnitPrice"), "price_qty": f.get("PriceQty"), "stock": f.get("Stock"),
            "source": f.get("Source", "kicad-library"), "datasheet": f.get("Datasheet", p.datasheet_url), "links": {"lcsc": f["Url"]} if f.get("Url") else {},
        })
    rows.sort(key=lambda r: (r["refs"][0].rstrip("0123456789"), r["mpn"]))
    return rows


def bom_csv(circuit: Circuit) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Designator", "Qty", "MPN", "Manufacturer", "Value", "Footprint", "Description", "Unit price USD (catalogue snapshot)",
                "Ext price USD", "Datasheet", "LCSC link", "LCSC #", "Symbol/footprint source"])
    for r in group_bom(circuit):
        up = r["unit_price"]
        w.writerow([",".join(r["refs"]), r["qty"], r["mpn"], r["manufacturer"], r["value"], r["footprint"], r["description"],
                    f"{up:.4f}" if up is not None else "", f"{up * r['qty']:.2f}" if up is not None else "", r["datasheet"],
                    r["links"].get("lcsc", ""), r["lcsc"], r["source"]])
    return out.getvalue()


def bom_jlcpcb(circuit: Circuit) -> str:
    """JLCPCB assembly BOM columns (Comment, Designator, Footprint, LCSC Part #)."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
    for r in group_bom(circuit):
        if r["dnp"]:
            continue
        w.writerow([r["value"] or r["mpn"], ",".join(r["refs"]), r["footprint"], r["lcsc"]])
    return out.getvalue()


def cpl_csv(board: Board, jlc_rows: list | None = None) -> str:
    """Pick-and-place for JLCPCB (column names theirs). Origin = board bottom-left, y up, mm.
    Without `jlc_rows` the values follow KiCad's own position file (`kicad-cli pcb export pos`, checked in tests). With the rows of
    `jlc_rotation` (Fabrication-Toolkit's table) JLCPCB's library orientation and bottom-face rule are applied. Either way verify polarised parts in the fab's preview."""
    from .jlc_rotation import correct, lookup
    H = board.height
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer"])
    for ref, comp in board.circuit.components.items():
        if comp.dnp or comp.part.category in UNASSEMBLED:
            continue
        pl = board.place[ref]
        x, y, rot = pl.x, H - pl.y, pl.rot % 360
        if jlc_rows is not None:
            fp = comp.footprint.lib_id or comp.footprint.name
            x, y, rot = correct(x, y, pl.rot, pl.side == "bottom", lookup(jlc_rows, fp))
        w.writerow([ref, comp.value, comp.part.package, f"{x:.3f}mm", f"{y:.3f}mm", f"{rot:g}", pl.side.capitalize()])
    return out.getvalue()


def cpl_generic(board: Board) -> str:
    H = board.height
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Ref", "Val", "MPN", "Package", "PosX", "PosY", "Rot", "Side"])
    for ref, comp in board.circuit.components.items():
        pl = board.place[ref]
        w.writerow([ref, comp.value, comp.part.mpn, comp.part.package, f"{pl.x:.3f}", f"{H - pl.y:.3f}", f"{pl.rot % 360:g}", pl.side])
    return out.getvalue()


def bom_cost(circuit: Circuit, boards: int = 1) -> dict:
    rows = group_bom(circuit)
    total = 0.0
    unknown = 0
    for r in rows:
        if r["unit_price"] is None:
            unknown += 1
            continue
        total += r["unit_price"] * r["qty"] * boards
    return {"lines": len(rows), "parts": sum(r["qty"] for r in rows), "total_usd": round(total, 2), "lines_unpriced": unknown,
            "estimated": True}      # catalogue snapshot prices, not live quotes
