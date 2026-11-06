"""Cost = bill of materials from catalogue snapshot prices. PCB fabrication and assembly are NOT estimated here (no honest source for them without a
quote): the report says so and the Gerber/BOM/CPL package is what you send to the fab for a quote."""
from __future__ import annotations

from ..export import bom as bomx
from ..model import Circuit


def build_cost(circuit: Circuit, qty: int = 1) -> dict:
    bom = bomx.bom_cost(circuit)
    return {"bom": bom["total_usd"], "bom_unpriced_lines": bom["lines_unpriced"], "qty": qty, "total": round(bom["total_usd"] * qty, 2),
            "excludes": "PCB fabrication and assembly (get a quote from the fab)"}
