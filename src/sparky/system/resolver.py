"""Interface resolver, independent of any particular recipe.

Recipes declare (META["auto"]) which of their parameters are determined by which port attribute, and expose `ports(policy, **params)`. Using only that:
  * power demand flows upstream: a provider port's "current" parameter = sum of what its consumers draw (x fleet qty x duty) x policy.demand_margin
  * voltage windows flow downstream: a consumer port's "vmin" / "vmax" / "voltage" parameters = the provider's window
and it iterates to a fixpoint, then builds every recipe and checks the links (kinds, windows, capacity, logic levels).
Nothing here names a recipe, a part or a physical constant: numbers come from recipes, the plan, or `Policy`."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..model import Circuit, Port
from ..recipes import Recipe, build_recipe, registry
from ..recipes.ctx import Ctx
from ..recipes.policy import Policy
from .plan import BlockInst, BoardNode, Link, PurchasedItem, SystemPlan

# kind -> {signal on side A : signal on side B}; a UART's tx meets the other side's rx
CROSS = {"uart": {"tx": "rx", "rx": "tx"}}
ATTRS = ("current", "vmin", "vmax", "voltage")


@dataclass
class Issue:
    severity: str
    msg: str


@dataclass
class Resolved:
    plan: SystemPlan
    params: dict[tuple[str, str], dict]
    circuits: dict[tuple[str, str], Circuit]
    links: list[Link]
    items: list[PurchasedItem]
    issues: list[Issue] = field(default_factory=list)
    harness: list[dict] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    demand: dict[str, float] = field(default_factory=dict)
    iterations: int = 0

    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


class Resolver:
    def __init__(self, plan: SystemPlan, policy: Policy | None = None, ctx_factory=None, log=print):
        self.plan, self.policy, self.log = copy.deepcopy(plan), policy or Policy(), log
        self.ctx_factory = ctx_factory or (lambda qty: Ctx(policy=self.policy, qty=qty))
        self.reg: dict[str, Recipe] = registry()
        self.boards = {b.id: b for b in self.plan.boards}
        self.items = {p.id: p for p in self.plan.purchased}
        self.binst: dict[tuple[str, str], BlockInst] = {(b.id, bi.id): bi for b in self.plan.boards for bi in b.blocks}
        self.params = {k: dict(bi.params) for k, bi in self.binst.items()}
        self.auto: set[tuple[tuple[str, str], str]] = set()     # (block, param) currently derived from links (an explicit plan value wins)
        self.links = list(self.plan.links)
        self.issues: list[Issue] = []
        self.decisions: list[str] = []
        self.demand: dict[str, float] = {}

    # ---- endpoints ----------------------------------------------------------------------------
    @staticmethod
    def split(end: str):
        p = end.split(".")
        return ("block", (p[0], p[1]), p[2]) if len(p) == 3 else ("item", p[0], p[1])

    def port_of(self, end: str) -> Port | None:
        kind, key, name = self.split(end)
        if kind == "block":
            r = self.reg.get(self.binst[key].recipe)
            if r is None or r.ports is None:
                return None
            for sp in r.ports(self.policy, **self.params[key]):
                if sp.name == name:
                    return Port(sp.name, sp.net, sp.kind, sp.direction, sp.voltage or 0.0, sp.current_a or 0.0, dict(sp.meta), dict(sp.signals))
            return None
        it = self.items.get(key)
        for pt in (it.ports if it else []):
            if pt["name"] == name:
                meta = {k: v for k, v in pt.items() if k not in ("name", "kind", "direction", "voltage", "current_a", "signals")}
                return Port(name, "", pt["kind"], pt.get("direction", "bidir"), pt.get("voltage", 0.0), pt.get("current_a", 0.0), meta, dict(pt.get("signals") or {}))
        return None

    def node_of(self, end: str) -> tuple[str, int]:
        kind, key, _ = self.split(end)
        return (key[0], self.boards[key[0]].qty) if kind == "block" else (key, self.items[key].qty)

    def duty_of(self, end: str) -> float:
        kind, key, _ = self.split(end)
        return self.boards[key[0]].power_duty if kind == "block" else 1.0

    def _power_pairs(self):
        """(provider end, consumer end) for every power link with one 'out' and one 'in' end."""
        for l in self.links:
            pa, pb = self.port_of(l.a), self.port_of(l.b)
            if pa is None or pb is None or pa.kind != "power" or pb.kind != "power":
                continue
            if pa.direction == "out" and pb.direction == "in":
                yield l.a, l.b
            elif pb.direction == "out" and pa.direction == "in":
                yield l.b, l.a

    def _set(self, key, name, value) -> bool:
        if self.binst[key].params.get(name) not in (None, "auto") and (key, name) not in self.auto:
            return False                                        # explicit value from the plan wins
        self.auto.add((key, name))
        if value is not None and self.params[key].get(name) != value:
            self.params[key][name] = value
            return True
        return False

    # ---- fixpoint -----------------------------------------------------------------------------
    def resolve(self) -> Resolved:
        it = 0
        for it in range(1, 11):
            changed = self._demand_up() | self._windows_down()
            if not changed:
                break
        circuits: dict[tuple[str, str], Circuit] = {}
        for key, bi in self.binst.items():
            try:
                ctx = self.ctx_factory(self.boards[key[0]].qty)
                circuits[key] = build_recipe(bi.recipe, self.params[key], f"{key[0]}.{key[1]}", ctx=ctx)
            except Exception as e:                             # NeedsDatasheet, NoPart, ValueError...: surfaced to the supervisor, never swallowed
                self.issues.append(Issue("error", f"{key[0]}.{key[1]}: cannot build recipe '{bi.recipe}': {e}"))
        res = Resolved(self.plan, self.params, circuits, self.links, list(self.items.values()), self.issues, [], self.decisions, self.demand, it)
        self._validate(res)
        self._harness(res)
        seen: set = set()
        res.issues = [i for i in res.issues if not ((i.severity, i.msg) in seen or seen.add((i.severity, i.msg)))]
        return res

    def _demand_up(self) -> bool:
        changed, demand = False, {}
        for prov, cons in self._power_pairs():
            cport = self.port_of(cons)
            node_p, _ = self.node_of(prov)
            node_c, qty_c = self.node_of(cons)
            mult = qty_c * self.duty_of(cons) if node_p != node_c else 1.0
            demand[prov] = demand.get(prov, 0.0) + cport.current_a * mult
        self.demand = demand
        for prov, amps in demand.items():
            kind, key, pname = self.split(prov)
            if kind != "block":
                continue
            for param, (port, attr) in self.reg[self.binst[key].recipe].auto.items():
                if port == pname and attr == "current":
                    changed |= self._set(key, param, round(amps * self.policy.demand_margin, 6))
        return changed

    def _windows_down(self) -> bool:
        changed = False
        for prov, cons in self._power_pairs():
            kind, key, pname = self.split(cons)
            if kind != "block":
                continue
            pp = self.port_of(prov)
            tol = pp.meta.get("tolerance") or 0.0
            vals = {"vmin": pp.meta.get("vmin") if pp.meta.get("vmin") is not None else pp.voltage * (1 - tol),
                    "vmax": pp.meta.get("vmax") if pp.meta.get("vmax") is not None else pp.voltage * (1 + tol), "voltage": pp.voltage}
            for param, (port, attr) in self.reg[self.binst[key].recipe].auto.items():
                if port == pname and attr in vals and vals[attr]:
                    changed |= self._set(key, param, round(vals[attr], 6))
        return changed

    # ---- validation ---------------------------------------------------------------------------
    def _validate(self, res: Resolved) -> None:
        I = self.issues
        for l in self.links:
            pa, pb = self.port_of(l.a), self.port_of(l.b)
            if pa is None or pb is None:
                I.append(Issue("error", f"link {l.a} <-> {l.b}: unknown port"))
                continue
            if pa.kind != pb.kind and "bundle" not in (pa.kind, pb.kind):
                I.append(Issue("error", f"link {l.a} <-> {l.b}: incompatible kinds {pa.kind}/{pb.kind}"))
                continue
            if pa.kind == "power":
                if pa.direction == pb.direction and pa.direction != "bidir":
                    I.append(Issue("error", f"link {l.a} <-> {l.b}: both ends are power {pa.direction}"))
                    continue
                prov, cons, cend, pend = (pa, pb, l.b, l.a) if pa.direction == "out" else (pb, pa, l.a, l.b)
                vmin, vmax = cons.meta.get("vmin"), cons.meta.get("vmax")
                lo = prov.meta.get("vmin") if prov.meta.get("vmin") is not None else prov.voltage
                hi = prov.meta.get("vmax") if prov.meta.get("vmax") is not None else prov.voltage
                if vmin is not None and vmax is not None and not (vmin - 1e-6 <= lo and hi <= vmax + 1e-6):
                    I.append(Issue("error", f"link {pend} -> {cend}: provider {lo:g}-{hi:g} V outside consumer window {vmin:g}-{vmax:g} V"))
                need, cap = self.demand.get(pend, 0.0), prov.current_a
                if self.split(pend)[0] == "item" and cap:
                    if need > cap * (1 + 1e-4):
                        I.append(Issue("error", f"{pend}: load {need:.3g} A exceeds rated {cap:g} A"))
                    elif need > self.policy.capacity_warn_fraction * cap:
                        I.append(Issue("warning", f"{pend}: load {need:.3g} A is {100 * need / cap:.0f}% of rated {cap:g} A"))
            elif pa.voltage and pb.voltage and pa.kind not in ("can_bus", "bundle", "motor_phase", "ground") and abs(pa.voltage - pb.voltage) > self.policy.logic_level_tolerance_v:
                I.append(Issue("error", f"link {l.a} <-> {l.b}: logic level mismatch {pa.voltage:g} V vs {pb.voltage:g} V (level shifter needed)"))
        for key, c in res.circuits.items():
            for w in c.warnings:
                I.append(Issue("warning", f"{key[0]}.{key[1]}: {w}"))

    def _harness(self, res: Resolved) -> None:
        """Links that leave a board. Wire gauge / connector choice are the designer's job (add a connector_breakout recipe on each side); we report what each link carries."""
        for l in self.links:
            pa, pb = self.port_of(l.a), self.port_of(l.b)
            if pa is None or pb is None or self.node_of(l.a)[0] == self.node_of(l.b)[0]:
                continue
            amps = max(self.demand.get(l.a, 0.0), self.demand.get(l.b, 0.0), pa.current_a, pb.current_a)
            res.harness.append({"from": l.a, "to": l.b, "kind": pa.kind, "current_a": round(amps, 3), "volts": max(pa.voltage, pb.voltage, pa.meta.get("vmax") or 0, pb.meta.get("vmax") or 0),
                                "qty": max(self.node_of(l.a)[1], self.node_of(l.b)[1])})
            for end, port in ((l.a, pa), (l.b, pb)):
                if self.split(end)[0] == "block" and not port.meta.get("external") and self.binst[self.split(end)[1]].recipe != "connector_breakout":
                    res.issues.append(Issue("warning", f"{end}: link leaves the board without a connector; add a connector_breakout recipe for it"))
