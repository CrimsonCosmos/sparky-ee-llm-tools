"""Human-readable design report with an honest manufacturing-readiness checklist."""
from __future__ import annotations

from ..pcb.flow import BuildResult
from . import bom as bomx


def render_report(res: BuildResult, project: str, extra: dict) -> str:
    b, c, drc, an = res.board, res.board.circuit, res.drc, res.analysis
    rows = bomx.group_bom(c)
    cost = bomx.bom_cost(c)
    L = [f"# {project} - design report", ""]
    L += ["## Summary", "",
          f"- Board: **{b.width:.1f} x {b.height:.1f} mm**, **{b.rules.layers} layers** ({', '.join(b.layers)}), fab profile `{b.rules.profile}`",
          f"- Components: {len(c.components)} ({sum(1 for r in rows)} BOM lines), nets: {len(c.nets)}",
          f"- Planes: {b.plane_nets or 'none'}; pours: {list(b.pour_nets) or 'none'}; routed by Freerouting, checked and filled by KiCad {drc.version or ''}",
          f"- KiCad DRC: **{'CLEAN' if drc.clean else 'ERRORS'}** ({drc.summary()})",
          f"- BOM cost (1 board, catalogue snapshot; excludes PCB fab + assembly): **${cost['total_usd']:.2f}** (snapshot prices - refresh before ordering), unpriced lines: {cost['lines_unpriced']}",
          ""]
    L += ["", "## Layout checks (geometric)", ""]
    for d in an.get("decoupling", []):
        L.append(f"- Decoupling {d['cap']} -> {d['ic_pin']}: {d['dist_mm']} mm (limit {d['limit_mm']}) {'OK' if d['ok'] else '**FAR**'}")
    for d in an.get("loops", []):
        L.append(f"- Hot loop `{d['loop']}`: hull area {d['area_mm2']} mm^2 ({', '.join(d['members'])})")
    L += ["", "## Warnings", ""]
    allw = list(dict.fromkeys(list(b.warnings) + list(c.warnings) + [f"{v['type']}: {v['description']}" for v in drc.warnings][:20]))
    L += [f"- {w}" for w in allw] or ["- none"]
    if drc.errors:
        L += ["", "## DRC errors", ""] + [f"- {v['type']}: {v['description']}" for v in drc.errors[:40]]
    L += ["", "## Design calculations (from the recipes)", ""]
    for k, v in c.calcs.items():
        L.append(f"- `{k}`: {v}")
    L += ["", "## Bill of materials", "", "| Qty | Designators | MPN | Value | Package | Snapshot $ | Symbol/footprint source |", "|--|--|--|--|--|--|--|"]
    for r in rows:
        up = f"{r['unit_price']:.3f}" if r["unit_price"] is not None else "?"
        L.append(f"| {r['qty']} | {','.join(r['refs'][:6])}{'...' if len(r['refs']) > 6 else ''} | {r['mpn']} | {r['value']} | {r['package']} | {up} | {r['source']} |")
    unrev = [r['mpn'] for r in rows if r['source'] != 'kicad-library']
    L += ["", "## Manufacturing-readiness checklist", "",
          "The software verifies geometry (DRC), connectivity and format validity. It cannot replace these human checks:", ""]
    L += [f"- [{'x' if drc.clean else ' '}] DRC clean and every net connected (automated)",
          f"- [{' ' if unrev else 'x'}] Symbols/footprints come from KiCad's reviewed libraries" + (f" - **vendor-imported, UNREVIEWED: {', '.join(unrev)}** (check pinout and land pattern against the datasheet)" if unrev else ""),
          "- [ ] Schematic reviewed by an engineer (recipe calculations are listed above; loop stability and EMC are not verified)",
          "- [ ] High-current nets reviewed (ampacity warnings above); thermal estimate is a 2-D model, not a substitute for testing",
          "- [ ] Gerbers opened in an independent viewer (KiCad/Gerbv) and the fab's online DFM check passed",
          "- [ ] Prices/stock refreshed from suppliers before ordering (catalogue prices are a snapshot)"]
    for k, v in (extra or {}).items():
        L += ["", f"## {k}", "", str(v)]
    return "\n".join(L) + "\n"


def kicad_drc_section(rep) -> str:
    """Markdown body for the 'KiCad DRC (authoritative)' section (rep = kicad_backend.DrcReport)."""
    L = [f"KiCad {rep.version or '?'} DRC (zones refilled by KiCad's own filler): **{'CLEAN' if rep.clean else 'VIOLATIONS'}** - {rep.summary()}.",
         "Full machine-readable result: `kicad_drc.json`."]
    if rep.by_type or rep.unconnected:
        L += ["", "| Type | Severity | Count |", "|--|--|--|"]
        counts: dict[tuple[str, str], int] = {}
        for v in rep.violations + rep.unconnected:
            counts[(v["type"], v["severity"])] = counts.get((v["type"], v["severity"]), 0) + 1
        for (t, sev), n in sorted(counts.items(), key=lambda kv: (kv[0][1] != "error", -kv[1])):
            L.append(f"| {t} | {sev} | {n} |")
        for v in (rep.errors + rep.unconnected)[:15]:
            it = v["items"][0] if v["items"] else {}
            L.append(f"\n- {v['type']}: {v['description']} @ ({it.get('x', '?')}, {it.get('y', '?')}) {it.get('desc', '')}")
    return "\n".join(L)
