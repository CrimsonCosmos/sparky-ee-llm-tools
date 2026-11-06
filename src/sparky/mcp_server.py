"""MCP server (stdio, JSON-RPC 2.0, no third-party dependency).

Lets Claude Code act as the supervisor: it decomposes the request itself (following the
instructions in CLAUDE.md / planner.SUPERVISOR_PROMPT) and calls these deterministic tools.
Register with:  claude mcp add sparky -- python -m sparky.mcp_server
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

PROTOCOL = "2024-11-05"


def _outdir(name: str) -> Path:
    base = Path(os.environ.get("SPARKY_OUT") or Path.cwd() / "sparky_out")
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def tool_list_recipes(args: dict):
    from .recipes import describe
    return describe()


def tool_search_symbols(args: dict):
    from .kicad_symbols import find_symbols
    return [{"symbol": s.lib_id, "footprint": s.footprint, "description": s.description, "pins": s.pins} for s in find_symbols(args["query"], limit=int(args.get("limit", 15)))]


def tool_pick_parts(args: dict):
    from .picker import Catalog, recipes
    fn = getattr(recipes, args["need"])
    out = Catalog().pick(fn(**{k: float(v) for k, v in (args.get("params") or {}).items()}), qty=int(args.get("qty", 1)), limit=int(args.get("limit", 5)))
    return [{"lcsc": c.lcsc, "mpn": c.mpn, "package": c.package, "stock": c.stock, "unit_price": c.unit_price, "url": c.url, "datasheet": c.datasheet,
             "attributes": c.attributes} for c in out]


def tool_save_spec(args: dict):
    from . import datasheet
    return str(datasheet.save(args["part"], args["values"], args["source"]))


def tool_write_recipe(args: dict):
    """Validate (import + META/PARAMS/build present) and store a recipe file authored by the supervisor, so it is cached and reusable."""
    import re
    from .paths import data_dir
    from .recipes import _load_file
    name = args["name"]
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        return {"error": "name must be snake_case"}
    d = data_dir() / "recipes"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.py"
    f.write_text(args["source"])
    try:
        r = _load_file(f)
        if r is None or r.name != name:
            raise ValueError("file must define META (with name == file name), PARAMS and build(ctx, **params)")
    except Exception as e:
        f.unlink(missing_ok=True)
        return {"error": f"{type(e).__name__}: {e}"}
    return {"stored": str(f), "recipe": r.describe()}


def tool_validate_plan(args: dict):
    from .recipes import registry
    from .system.plan import SystemPlan
    from .system.resolver import Resolver
    plan = SystemPlan.from_dict(args["plan"] if isinstance(args["plan"], dict) else json.loads(args["plan"]))
    errs = plan.validate(set(registry()))
    if errs:
        return {"valid": False, "errors": errs}
    res = Resolver(plan, log=None).resolve()
    return {"valid": not res.errors(), "issues": [{"severity": i.severity, "msg": i.msg} for i in res.issues], "demand_a": res.demand,
            "recipe_params": {f"{k[0]}.{k[1]}": v for k, v in res.params.items()}}


def tool_build_system(args: dict):
    from .purchase.plan import build_purchase_files
    from .system.build import build_system
    from .system.plan import SystemPlan
    plan = SystemPlan.from_dict(args["plan"] if isinstance(args["plan"], dict) else json.loads(args["plan"]))
    out = _outdir(plan.name)
    sb = build_system(plan, out, log=lambda s: print(s, file=sys.stderr), build_pcbs=bool(args.get("build_pcbs", True)), pcb_time_s=float(args.get("pcb_time_s", 900)))
    build_purchase_files(sb)
    return {"output_dir": str(out), "cost": sb.cost, "decisions": sb.resolved.decisions,
            "issues": [{"severity": i.severity, "msg": i.msg} for i in sb.resolved.issues + sb.issues],
            "boards": {b: {"files": sorted(str(p) for p in (i.get("files") or {}).values())[:6], "cost": i["cost"],
                           "drc": (i["result"].drc.summary() if "result" in i else "not built")} for b, i in sb.boards.items()},
            "reports": [str(out / "SYSTEM.md"), str(out / "purchase" / "shopping_list.md")]}


def tool_build_recipe_board(args: dict):
    from .export.package import write_package
    from .pcb.flow import BoardSpec, build_board
    from .recipes import build_recipe
    c = build_recipe(args["recipe"], args.get("params") or {})
    res = build_board(c, BoardSpec(layers=args.get("layers"), copper_oz=float(args.get("copper_oz", 1.0))), log=lambda s: print(s, file=sys.stderr))
    out = _outdir(f"recipe_{args['recipe']}")
    files = write_package(res, out, args["recipe"])
    return {"output_dir": str(out), "success": res.success, "drc": res.drc.summary(), "board_mm": [res.board.width, res.board.height],
            "layers": res.board.rules.layers, "calcs": c.calcs, "warnings": list(dict.fromkeys(res.board.warnings + c.warnings))[:15], "fab_zip": str(files["fab.zip"])}


_OBJ = {"type": "object", "properties": {}}
TOOLS = {
    "sparky_list_recipes": (tool_list_recipes, "List recipes (circuit builders) with ports, parameters and which parameters the resolver derives. Call first when planning.", _OBJ),
    "sparky_search_symbols": (tool_search_symbols, "Search KiCad's symbol libraries (name/keywords/description); returns library id and default footprint.",
                              {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}),
    "sparky_pick_parts": (tool_pick_parts, "Parametric search of the JLCPCB/LCSC catalogue (stock, price, LCSC id, datasheet) for a named need, e.g. need=inductor params={henries:4.7e-6,isat_min:4}.",
                          {"type": "object", "properties": {"need": {"type": "string"}, "params": {"type": "object"}, "qty": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["need"]}),
    "sparky_save_spec": (tool_save_spec, "Store datasheet numbers a recipe requested (NeedsDatasheet), with their source. Read the OFFICIAL datasheet; never guess.",
                         {"type": "object", "properties": {"part": {"type": "string"}, "values": {"type": "object"}, "source": {"type": "string"}}, "required": ["part", "values", "source"]}),
    "sparky_write_recipe": (tool_write_recipe, "Author a NEW recipe (Python file: META, PARAMS, optional ports(), build(ctx, **params)) when none fits; validated and cached. See docs/RECIPES.md.",
                            {"type": "object", "properties": {"name": {"type": "string"}, "source": {"type": "string"}}, "required": ["name", "source"]}),
    "sparky_validate_plan": (tool_validate_plan, "Validate a SystemPlan JSON and run the interface resolver (builds each recipe; no PCB work). Returns issues, demand, derived parameters.",
                             {"type": "object", "properties": {"plan": {"type": ["object", "string"]}}, "required": ["plan"]}),
    "sparky_build_system": (tool_build_system, "Resolve + build every board (place, Freerouting, KiCad DRC, Gerber/BOM/STEP), cost roll-up, buy-vs-build, shopping list. Slow (minutes).",
                            {"type": "object", "properties": {"plan": {"type": ["object", "string"]}, "build_pcbs": {"type": "boolean"}, "pcb_time_s": {"type": "number"}}, "required": ["plan"]}),
    "sparky_build_recipe_board": (tool_build_recipe_board, "Build ONE recipe with params into a finished fab-ready board.",
                                  {"type": "object", "properties": {"recipe": {"type": "string"}, "params": {"type": "object"}, "layers": {"type": "integer"}, "copper_oz": {"type": "number"}}, "required": ["recipe"]}),
}


def _reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(req: dict):
    m, id_, p = req.get("method"), req.get("id"), req.get("params") or {}
    if m == "initialize":
        return {"protocolVersion": p.get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}},
                "serverInfo": {"name": "sparky", "version": "0.1.0"},
                "instructions": "Deterministic EDA pipelines. Decompose requests into a SystemPlan (see CLAUDE.md), validate with sparky_validate_plan, build with sparky_build_system."}
    if m == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (_, d, s) in TOOLS.items()]}
    if m == "tools/call":
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in TOOLS:
            raise KeyError(f"unknown tool {name}")
        try:
            out = TOOLS[name][0](args)
            return {"content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)}]}
        except Exception as e:
            return {"isError": True, "content": [{"type": "text", "text": f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"}]}
    if m in ("ping",):
        return {}
    return None


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in req:                       # notification
            continue
        try:
            res = handle(req)
            if res is None:
                _reply(req["id"], error={"code": -32601, "message": f"method not found: {req.get('method')}"})
            else:
                _reply(req["id"], res)
        except Exception as e:
            _reply(req["id"], error={"code": -32000, "message": str(e)})


if __name__ == "__main__":
    serve()
