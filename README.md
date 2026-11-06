# sparky

**Claude Code + deterministic EDA.** Describe a system (rails, buses, loads, budget); get KiCad boards (schematic netlist, placed and routed PCB,
Gerber/drill/IPC-2581/STEP), a BOM with sourcing data, and a shopping list. Claude supplies system-design judgement only (decompose, buy-vs-build,
read a datasheet when a number is missing). Everything else is deterministic, open-source and token-free.

```
request ─► supervisor (Claude Code) ─► SystemPlan JSON: boards = recipe instances, purchased items, links between ports
              ▼
        RESOLVER   demand flows upstream, voltage windows downstream, through each recipe's declared `auto` parameters (knows no recipe by name)
              ▼
        RECIPES    plain Python files: SKiDL circuit + datasheet design procedure; symbols/footprints from KiCad's reviewed libraries,
                   passives/discretes picked from the open JLCPCB/LCSC catalogue; ngspice check of the power stage
              ▼
        PCB        our placer (heat/EMI/decoupling) ─► Freerouting ─► KiCad DRC (authoritative) ─► kicad-cli Gerber/drill/IPC-2581/STEP
              ▼
        OUTPUT     boards/<id>/ (fab zip, BOM, CPL, .kicad_pcb, REPORT.md), SYSTEM.md, purchase/shopping_list.md
```

Design rules (docs/DESIGN.md, CLAUDE.md): adopt open source instead of building; **no hardcoded engineering values** (spec / `Policy` / datasheet / library);
**a confidently wrong test system is worse than none** (every check validated against an independent reference); ask "would a real engineer do this?"

## Quick start
```bash
pip install -e '.[dev]'          # python >= 3.13
sparky doctor                    # KiCad >= 8 (kicad-cli + pcbnew), Java (Freerouting, auto-downloaded and hash-pinned), ngspice
sparky download-parts            # ~400 MB JLCPCB/LCSC catalogue (resumable)
sparky recipes                   # circuit builders, their ports and parameters
sparky symbols TPS5430           # search KiCad symbols (pins + default footprint)
sparky board battery_input --params '{...}' --out out/bat      # one recipe -> routed board package
sparky build-plan plan.json --out out/system                    # resolve + build every board + shopping list
sparky --offline build-plan plan.json --no-pcb                  # resolve + cost only
```
As an MCP server for Claude Code: `claude mcp add sparky -- python -m sparky.mcp_server` (tools `sparky_*`; supervisor instructions in CLAUDE.md).

## Recipes, parts and datasheet numbers
* A **recipe** is a file (docs/RECIPES.md). Shipped: `buck`, `ldo`, `battery_input`, `can_node`, `mcu_core`, `connector_breakout`, `mounting_holes`.
  Claude writes new ones (e.g. a motor driver from a vendor reference design) with `sparky_write_recipe`; they are cached in `<data>/recipes/`.
* ICs: the designer names the KiCad symbol; discretes: parametric catalogue pick, footprint found in KiCad's library by package.
  The catalogue is a spec check and sourcing source, never a silent substitute. Vendor imports (`libsrc`, easyeda2kicad) are a flagged fallback.
* Datasheet numbers (Vref, fsw, bypass values...) are requested explicitly (`NeedsDatasheet`) and stored with their source: `sparky save-spec`.

## Example
`examples/can_node/`: a 12 V -> buck -> STM32F103 + CAN transceiver board, planned and built from recipes with datasheet numbers taken from the official
datasheets (sources recorded in `specs/*.json`). `pytest -m slow tests/test_e2e_system.py` builds it (place, Freerouting, KiCad DRC, package, shopping list).

## What it does NOT do (stated in every report)
Loop stability, EMC and enclosure thermals are not verified (the buck power stage is checked in ngspice against the design equations). PCB fabrication and
assembly are not priced (catalogue prices are snapshots). Mechanical design, live distributor APIs and automatic purchasing are out of scope
(`mechanical.py`, `sourcing.py` explain how they could be done). Not yet: BGA, differential-pair/length matching, double-sided assembly.
Thermal / IR-drop / EMC adapters (pcb-analysis, kicad-irdrop) are in `sparky/analysis/` and being wired in (docs/NEXT.md).

## Tests
Fast suite (`pytest`, ~10 s) runs on every push, scoped by changed files (`tools/scoped_tests.py`). Integration (`pytest -m slow`: place + Freerouting + KiCad DRC)
runs on demand / nightly. `docs/DEPENDENCIES.md` lists pinned tool versions.
