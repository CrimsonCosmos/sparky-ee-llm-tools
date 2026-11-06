"""sparky command line."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _llm(args):
    from .llm.base import OfflineLLM, get_llm
    if getattr(args, "offline", False):
        return None
    try:
        llm = get_llm(getattr(args, "llm", None))
        return None if isinstance(llm, OfflineLLM) else llm
    except Exception:
        return None


def cmd_recipes(a):
    from .recipes import describe
    for r in describe():
        print(f"{r['name']:<20} {r['summary'][:110]}")
        print(f"    ports: {', '.join(p['name'] + '(' + p['direction'] + ')' for p in r['ports'])}   auto: {r['auto_params']}")
        for p in r["params"]:
            print(f"    {p['name']:<22} {p['type']:<7} {'required' if p['required'] else 'default=' + repr(p['default']):<18} {p['doc']}")


def cmd_symbols(a):
    from .kicad_symbols import find_symbols
    for s in find_symbols(a.query, limit=a.limit):
        print(f"{s.lib_id:<48} fp={s.footprint or '-':<50} {s.description[:60]}")


def cmd_save_spec(a):
    from . import datasheet
    vals = {k: float(v) for k, v in (x.split("=", 1) for x in a.values)}
    print(datasheet.save(a.part, vals, a.source))


def cmd_board(a):
    from .export.package import write_package
    from .pcb.flow import BoardSpec, build_board
    from .recipes import build_recipe
    c = build_recipe(a.recipe, json.loads(a.params or "{}"))
    res = build_board(c, BoardSpec(layers=a.layers, copper_oz=a.copper_oz))
    out = Path(a.out)
    write_package(res, out, a.recipe)
    print(f"{'OK' if res.success else 'INCOMPLETE'}: {res.board.width}x{res.board.height} mm, {res.board.rules.layers}L, DRC {res.drc.summary()}; files in {out}")
    for k, v in c.calcs.items():
        print("  calc", k, ":", v)


def cmd_plan(a):
    from .system.planner import plan_request
    plan = plan_request(a.request, a.budget, _llm(a))
    Path(a.out).write_text(plan.to_json())
    print(f"plan written to {a.out} ({len(plan.boards)} board types, {len(plan.purchased)} purchased items, {len(plan.links)} links)")


def _build(plan, a):
    from .purchase.plan import build_purchase_files
    from .system.build import build_system
    sb = build_system(plan, a.out, build_pcbs=not a.no_pcb, pcb_time_s=a.pcb_time)
    build_purchase_files(sb)
    c = sb.cost
    print("\n=== SYSTEM BUILD COMPLETE ===")
    print(f"output: {a.out}")
    print(f"cost (excl. PCB fab + assembly): boards' parts ${c['boards_usd']:,.0f} + purchased ${c['purchased_usd']:,.0f} + contingency ${c['contingency_usd']:,.0f} = ${c['total_usd']:,.0f}"
          + (f" vs budget ${c['budget_usd']:,.0f} -> {'WITHIN' if c['within_budget'] else 'OVER'} budget" if c.get("budget_usd") else ""))
    print(f"read: {a.out}/SYSTEM.md, {a.out}/purchase/shopping_list.md, per-board packages under {a.out}/boards/")


def cmd_build(a):
    from .system.planner import plan_request
    plan = plan_request(a.request, a.budget, _llm(a))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "plan.json").write_text(plan.to_json())
    _build(plan, a)


def cmd_build_plan(a):
    from .system.plan import SystemPlan
    plan = SystemPlan.from_dict(json.loads(Path(a.plan).read_text()))
    if a.budget:
        plan.budget_usd = a.budget
    _build(plan, a)


def cmd_pick(a):
    """sparky pick inductor henries=4.7e-6 isat_min=4.7 [--qty 10]  (needs live in sparky.picker.recipes)"""
    from .picker import Catalog, recipes
    fn = getattr(recipes, a.need, None)
    if fn is None:
        raise SystemExit(f"unknown need '{a.need}'. Available: " + ", ".join(n for n in dir(recipes) if not n.startswith("_") and callable(getattr(recipes, n))
                                                                           and getattr(getattr(recipes, n), "__module__", "") == recipes.__name__))
    kw = {k: float(v) for k, v in (x.split("=", 1) for x in a.args)}
    for c in Catalog().pick(fn(**kw), qty=a.qty, limit=a.limit):
        print(f"{c.lcsc:10} {c.mpn:24} {c.package:14} {'basic' if c.library_type == 'base' else 'pref ' if c.preferred else '     '} stock={c.stock:<8} ${c.unit_price}  {c.url}")


def cmd_download_parts(a):
    from .picker import ensure_db
    p = ensure_db(progress=lambda done, total: print(f"\r{done / 1e6:.0f}/{total / 1e6:.0f} MB", end="", flush=True))
    print(f"\n{p}")


def cmd_mcp(a):
    from .mcp_server import serve
    serve()


def cmd_doctor(a):
    from . import toolchain
    from .picker.catalog import db_path
    for k, v in toolchain.report().items():
        print(f"{k:<24} {v or 'MISSING'}")
    print(f"{'parts catalogue':<24} {db_path() if db_path().exists() else 'MISSING (sparky download-parts)'}")
    from .analysis import emc, irdrop, thermal
    for name, ok, hint in (("IR drop (kicad-irdrop)", irdrop.available(), "pip install into the KiCad Python"),
                           ("thermal (pcb-analysis)", thermal.available(), "needs pcb-analysis + its compiled kernels"),
                           ("EMC (pcb-analysis)", emc.available(), "needs pcb-analysis + its compiled kernels")):
        print(f"{name:<24} {'ok' if ok else 'MISSING (' + hint + '; see docs/DEPENDENCIES.md)'}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sparky", description="Claude Code + deterministic EDA: system description -> KiCad boards -> fab files -> shopping list")
    ap.add_argument("--offline", action="store_true", help="never call an LLM")
    ap.add_argument("--llm", choices=["claude-code", "anthropic", "offline"], help="LLM backend")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("recipes", help="list recipes (circuit builders), their ports and parameters"); s.set_defaults(fn=cmd_recipes)
    s = sub.add_parser("symbols", help="search KiCad's symbol libraries"); s.add_argument("query"); s.add_argument("--limit", type=int, default=15); s.set_defaults(fn=cmd_symbols)
    s = sub.add_parser("save-spec", help="store datasheet numbers a recipe asked for"); s.add_argument("part"); s.add_argument("values", nargs="+", help="name=value (SI units)")
    s.add_argument("--source", required=True, help="datasheet URL/page the numbers came from"); s.set_defaults(fn=cmd_save_spec)
    s = sub.add_parser("board", help="build one recipe into a routed board package")
    s.add_argument("recipe"); s.add_argument("--params"); s.add_argument("--layers", type=int); s.add_argument("--copper-oz", type=float, default=1.0)
    s.add_argument("--out", default="sparky_out/board"); s.set_defaults(fn=cmd_board)
    s = sub.add_parser("plan"); s.add_argument("request"); s.add_argument("--budget", type=float); s.add_argument("--out", default="plan.json"); s.set_defaults(fn=cmd_plan)
    for name, fn in (("build", cmd_build), ("build-plan", cmd_build_plan)):
        s = sub.add_parser(name)
        s.add_argument("request" if name == "build" else "plan")
        s.add_argument("--budget", type=float); s.add_argument("--out", default="sparky_out/system")
        s.add_argument("--no-pcb", action="store_true", help="resolve/cost only; skip placement+routing"); s.add_argument("--pcb-time", type=float, default=900.0)
        s.set_defaults(fn=fn)
    s = sub.add_parser("pick", help="deterministic part picker over the open JLCPCB/LCSC catalogue")
    s.add_argument("need"); s.add_argument("args", nargs="*", help="key=value requirement parameters"); s.add_argument("--qty", type=int, default=1)
    s.add_argument("--limit", type=int, default=5); s.set_defaults(fn=cmd_pick)
    s = sub.add_parser("download-parts", help="download the parts catalogue (~400 MB, resumable)"); s.set_defaults(fn=cmd_download_parts)
    s = sub.add_parser("mcp"); s.set_defaults(fn=cmd_mcp)
    s = sub.add_parser("doctor"); s.set_defaults(fn=cmd_doctor)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
