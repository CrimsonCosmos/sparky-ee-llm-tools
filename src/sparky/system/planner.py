"""Supervisor LLM: turns a one-line request into a SystemPlan by recursive decomposition.

The same SUPERVISOR_PROMPT is used (a) when this program drives Claude Code headlessly and
(b) as the instructions shipped in CLAUDE.md for the MCP workflow where Claude Code itself is the
supervisor and calls the deterministic tools. Claude never emits netlists or coordinates.
"""
from __future__ import annotations

import json

from ..recipes import describe as describe_recipes, registry
from ..llm.base import LLM, LLMUnavailable, extract_json
from ..llm.claude_code import RESEARCH_TOOLS
from .plan import SystemPlan

SUPERVISOR_PROMPT = """\
You are the supervising electrical-systems engineer of an automated design pipeline. The user gives you a goal
("build me X, budget $Y"). You do NOT draw schematics, netlists, placements or routes: deterministic tools do that
(recipes = parametric SKiDL circuits built from KiCad libraries and a real parts catalogue, a placer, Freerouting, KiCad DRC, Gerber export). Your job is to
decompose the goal into subsystems, decide what to BUILD and what to BUY, and describe how the parts connect.

## BUY vs BUILD policy (important)
Unless the user explicitly asks for something to be designed from scratch, or building is NECESSARY to fit the budget,
or no suitable product exists, you must NOT build every subassembly yourself. Where it makes sense, BUY it:
  * If a commercial off-the-shelf product with the exact required specs exists (power supplies, DC-DC converters,
    battery packs / BMS / chargers, motor drivers / ESCs / servo-actuator modules, compute modules and dev kits, IMUs,
    cameras, radios, displays, sensors, cables / harnesses / connectors) plan it as a `purchased` item with its spec,
    a realistic unit cost and a source URL (research it with web search; set cost_is_estimate=false only if you saw a live price).
  * Build a custom board (recipes) only for application-specific glue, when no bought part meets the spec, when buying would
    exceed the budget, or when the build cost is clearly lower at the required quantity.
  * For every recipe instance you plan to build that has a plausible purchasable equivalent, attach `buy_alternative`
    {title, unit_cost_usd, url, supplier, spec}; the pipeline then compares the real computed build cost against it and
    picks the cheaper/safer option (set prefer="build" or "buy" only when the user's request or the budget forces it).
  * Tight budget -> buy proven modules for low-volume items and build in volume where the BOM is cheap; loose budget ->
    prefer proven off-the-shelf parts for reliability and time-to-first-prototype. Always keep a running cost estimate
    and stay under the budget; say so in `assumptions` if you had to trade capability for cost.

## How to work
1. Restate the requirements and list explicit assumptions (voltages, currents, bus types, quantities, environment).
2. Decompose recursively: system -> subsystems -> components, until every leaf is either a recipe from the catalog
   or a purchased item. Quantities matter: a board used in 28 joints is one `boards` entry with qty 28.
3. Describe interfaces ONLY as `links` between ports (see schema). Power links carry no numbers: the resolver computes
   current demand upstream (qty x duty) and voltage windows downstream and fills every parameter a recipe lists under auto_params.
   You choose KiCad symbols/footprints (parts are searched with `sparky symbols <name>`), connectors and wire. Supply facts about purchased items in their `ports` (voltage, rated current, kind, signals).
   `power_duty` (0..1) on a board is the fraction of its rated current drawn simultaneously across the fleet.
4. Port kinds: power, ground, can_logic, can_bus, uart, i2c, spi, pwm, analog, gpio, motor_phase, bundle.
   Each recipe lists its ports; recipes that take a `signals` parameter (e.g. mcu_core) create one port per signal you name.
5. If no recipe fits, WRITE ONE: a Python file in `<data>/recipes/` (see docs/RECIPES.md) built from a datasheet's typical application circuit,
   using KiCad symbols and the ctx helpers; it is cached and reused. Datasheet numbers a recipe needs are requested explicitly
   (NeedsDatasheet) - read the official datasheet and store them with `sparky save-spec`. Never invent MPNs, pinouts or numbers.
6. Output ONLY one JSON object (SystemPlan), no prose.
"""

SCHEMA = """{
 "name": "short_snake_case", "request": "...", "budget_usd": 50000,
 "assumptions": ["..."],
 "boards": [ { "id": "joint", "title": "...", "qty": 28, "layers": 4, "copper_oz": 2.0, "power_duty": 0.15, "notes": "why custom",
     "blocks": [ {"id": "mcu", "recipe": "mcu_core", "params": {"symbol": "MCU_ST_STM32F1:STM32F103C8Tx", "vdd": 3.3, "signals": {"UART_TX": {"function": "USART2_TX", "kind": "uart"}}}, "buy_alternative": null, "prefer": "auto"} ] } ],
 "purchased": [ { "id": "battery", "title": "...", "category": "battery|power|actuator|compute|sensor|mechanical|harness|module", "qty": 1,
     "unit_cost_usd": 1400, "cost_is_estimate": true, "spec": "...", "url": "https://...", "supplier": "...",
     "ports": [ {"name": "out", "kind": "power", "direction": "out", "voltage": 48, "current_a": 60, "vmin": 39, "vmax": 54.6} ] } ],
 "links": [ {"a": "battery.out", "b": "joint.bus.src"}, {"a": "joint.mcu.pwm_drv", "b": "joint.drv.pwm"} ]
}"""


def catalog_text() -> str:
    return json.dumps(describe_recipes(), indent=1)


def planning_prompt(request: str, budget: float | None, feedback: str = "") -> str:
    p = (f"REQUEST: {request}\nBUDGET (USD): {budget if budget is not None else 'not specified'}\n\n"
         f"RECIPE CATALOG (deterministic circuit builders; params listed under auto_params are derived by the resolver from links):\n{catalog_text()}\n\n"
         f"PLAN SCHEMA:\n{SCHEMA}\n\n"
         f"Link endpoint format: `board.block.port` for board recipe instances (port names are in each recipe's `ports`) and `item.port` for purchased items.\n")
    if feedback:
        p += f"\nYour previous plan failed validation; fix exactly these problems and return the full corrected plan:\n{feedback}\n"
    return p


class PlannerError(RuntimeError):
    pass


def _dry_run(plan: SystemPlan) -> list[str]:
    from .resolver import Resolver
    try:
        res = Resolver(plan, log=None).resolve()
    except Exception as e:
        return [f"resolution failed: {e}"]
    return [i.msg for i in res.issues if i.severity == "error"]


def plan_with_llm(request: str, budget: float | None, llm: LLM, max_repairs: int = 3, log=print) -> SystemPlan:
    feedback = ""
    for attempt in range(max_repairs + 1):
        res = llm.run(planning_prompt(request, budget, feedback), system=SUPERVISOR_PROMPT, tools=RESEARCH_TOOLS, json_output=True,
                      timeout=1800, budget_usd=5.0)
        data = res.data if isinstance(res.data, dict) else extract_json(res.text)
        try:
            plan = SystemPlan.from_dict(data)
            plan.request = plan.request or request
            if plan.budget_usd is None:
                plan.budget_usd = budget
        except Exception as e:
            feedback = f"plan JSON did not match the schema: {e}"
            continue
        errs = plan.validate(set(registry())) or _dry_run(plan)
        if not errs:
            return plan
        feedback = "; ".join(errs[:12])
        if log:
            log(f"[planner] attempt {attempt + 1} rejected: {feedback[:300]}")
    raise PlannerError(f"supervisor could not produce a valid plan after {max_repairs + 1} attempts: {feedback}")


def plan_request(request: str, budget: float | None, llm: LLM | None, log=print) -> SystemPlan:
    """Plan with the supervisor LLM (Claude Code headless). There is no built-in fallback: a plan is a system-design judgement."""
    if llm is None:
        raise PlannerError("no LLM backend is available: install/login to Claude Code (`claude`) or set ANTHROPIC_API_KEY, "
                           "or write the SystemPlan JSON yourself and run `sparky build-system`.")
    try:
        return plan_with_llm(request, budget, llm, log=log)
    except LLMUnavailable as e:
        raise PlannerError(f"LLM unavailable: {e}") from e
