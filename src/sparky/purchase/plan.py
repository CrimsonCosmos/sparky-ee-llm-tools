"""Shopping list: every part of every board (x quantity + spares) with an LCSC link where the catalogue identified the part, else a search URL.
The user buys; automatic purchasing is out of scope. Prices are catalogue snapshots, not quotes."""
from __future__ import annotations

import csv
import html
import io
import json
import math
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote_plus

from ..export.bom import group_bom
from ..recipes.policy import Policy

SEARCH_URL = "https://www.lcsc.com/search?q={q}"      # fallback when the catalogue has no entry for the MPN


def order_qty(qty: int, category: str, pol: Policy) -> int:
    if category in ("mounting", "testpoint"):
        return qty
    if category in ("resistor", "capacitor", "led", "ferrite", "diode"):
        return qty + max(pol.spare_passive_min, math.ceil(qty * pol.spare_passive_fraction))
    return qty + max(pol.spare_other_min, math.ceil(qty * pol.spare_other_fraction))


def aggregate(sb, pol: Policy) -> list[dict]:
    rows: "OrderedDict[tuple, dict]" = OrderedDict()
    for bid, info in sb.boards.items():
        qty_boards = next(b.qty for b in sb.resolved.plan.boards if b.id == bid)
        for r in group_bom(info["circuit"]):
            e = rows.setdefault((r["mpn"], r["value"], r["footprint"]), {**{k: r[k] for k in ("mpn", "manufacturer", "value", "description", "category", "footprint",
                                                                                              "lcsc", "unit_price", "source", "datasheet")}, "qty": 0, "used_on": []})
            e["qty"] += r["qty"] * qty_boards
            e["used_on"].append(f"{bid} x{r['qty']}")
    out = list(rows.values())
    for e in out:
        e["qty_order"] = order_qty(e["qty"], e["category"], pol)
        e["url"] = f"https://www.lcsc.com/product-detail/{e['lcsc']}.html" if e["lcsc"] else SEARCH_URL.format(q=quote_plus(e["mpn"]))
        e["direct"] = bool(e["lcsc"])
    return out


def build_purchase_files(sb, outdir: str | Path | None = None, policy: Policy | None = None) -> dict:
    pol = policy or Policy()
    out = Path(outdir) if outdir else sb.out / "purchase"
    out.mkdir(parents=True, exist_ok=True)
    rows = aggregate(sb, pol)
    items = sb.resolved.plan.purchased
    parts_total = sum((r["unit_price"] or 0.0) * r["qty_order"] for r in rows)
    plan = {"parts": [{**{k: v for k, v in r.items()}, "ext_price_usd": round((r["unit_price"] or 0.0) * r["qty_order"], 2)} for r in rows],
            "items": [{"id": p.id, "title": p.title, "category": p.category, "qty": p.qty, "unit_cost_usd": p.unit_cost_usd, "ext_usd": round(p.unit_cost_usd * p.qty, 2),
                       "spec": p.spec, "url": p.url, "supplier": p.supplier, "estimate": p.cost_is_estimate, "needs_manual_sourcing": not bool(p.url)} for p in items],
            "totals": {"parts_usd_snapshot": round(parts_total, 2), "purchased_items_usd": round(sum(p.unit_cost_usd * p.qty for p in items), 2), "lines": len(rows),
                       "unpriced_lines": sum(1 for r in rows if r["unit_price"] is None)}}
    (out / "purchase_plan.json").write_text(json.dumps(plan, indent=1))
    files = {"plan": out / "purchase_plan.json"}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["LCSC Part Number", "Manufacturer Part Number", "Quantity", "Customer Reference"])
    for r in rows:
        if r["lcsc"]:
            w.writerow([r["lcsc"], r["mpn"], r["qty_order"], ",".join(r["used_on"])[:60]])
    (out / "cart_lcsc.csv").write_text(buf.getvalue())
    files["cart_lcsc"] = out / "cart_lcsc.csv"
    md = ["# Shopping list", "", "Every line has a link; 'search' links open a search for the exact MPN - verify the match before ordering. "
          "Prices are catalogue snapshots, not live quotes.", "", "## Electronic components", "",
          "| Qty (order) | MPN | Value | Description | Link | Unit USD | Ext USD | Symbol/footprint source |", "|--|--|--|--|--|--|--|--|"]
    for r in plan["parts"]:
        up = f"{r['unit_price']:.4f}" if r["unit_price"] is not None else "?"
        md.append(f"| {r['qty_order']} ({r['qty']} needed) | {r['mpn']} | {r['value']} | {r['description'][:50]} | [{'product' if r['direct'] else 'search'}]({r['url']}) | {up} | {r['ext_price_usd']:.2f} | {r['source']} |")
    md += ["", f"Components subtotal (snapshot unit prices, includes spares): **${plan['totals']['parts_usd_snapshot']:,.2f}** ({plan['totals']['unpriced_lines']} unpriced lines)", "",
           "## Purchased assemblies / modules / mechanical", "", "| Qty | Item | Category | Unit USD | Ext USD | Source |", "|--|--|--|--|--|--|"]
    for it in plan["items"]:
        src = f"[{it['supplier'] or 'link'}]({it['url']})" if it["url"] else "**needs manual sourcing**"
        md.append(f"| {it['qty']} | {it['title']} | {it['category']} | {it['unit_cost_usd']:,.2f}{'*' if it['estimate'] else ''} | {it['ext_usd']:,.2f} | {src} |")
    md += ["", "\\* planning estimate, not a live quote."]
    (out / "shopping_list.md").write_text("\n".join(md) + "\n")
    files["shopping_list"] = out / "shopping_list.md"
    return {"files": files, "plan": plan}
