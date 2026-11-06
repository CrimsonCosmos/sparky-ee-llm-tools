"""Recipes: a circuit function is a plain Python FILE (SKiDL + pure equations + part picking), discovered from directories - not a list in code.

Search path: this package's shipped recipes, then `$SPARKY_RECIPES` (os.pathsep-separated), then `<data_dir>/recipes`. Claude writes a new recipe
into the data dir when no existing one fits (from a datasheet typical-application circuit); it is then cached and reusable like a researched part.

A recipe module defines:
    META   = dict(name=..., summary=..., category=..., auto={"param": ("port", "current|vmin|vmax|voltage")})    # params the resolver fills from the links on that port
    PARAMS = [Param(...), ...]
    def build(ctx: Ctx, **params) -> Result        # build parts/nets with ctx.s (SKiDL); return declared ports
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Param:
    name: str
    type: str = "number"
    default: Any = None
    doc: str = ""
    required: bool = False


@dataclass
class PortSpec:
    name: str
    net: str
    kind: str                       # power | ground | can_bus | can_logic | uart | i2c | spi | pwm | analog | gpio | motor_phase ...
    direction: str = "bidir"
    voltage: float = 0.0
    current_a: float = 0.0
    signals: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    ports: list[PortSpec] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    calcs: dict[str, str] = field(default_factory=dict)


@dataclass
class Recipe:
    name: str
    summary: str
    params: list[Param]
    build: Callable[..., Result]
    category: str = "general"
    auto: dict = field(default_factory=dict)      # {param: (port, attribute)}: param is determined by that port attribute (current | vmin | vmax | voltage)
    ports: Callable[..., list] | None = None      # ports(policy, **params) -> [PortSpec]: interface from parameters alone (missing values allowed)
    path: str = ""

    def describe(self) -> dict:
        from .policy import Policy
        ports = [{"name": p.name, "kind": p.kind, "direction": p.direction} for p in self.ports(Policy())] if self.ports else []
        return {"name": self.name, "summary": self.summary, "ports": ports, "category": self.category, "auto_params": {k: list(v) for k, v in self.auto.items()}, "source": self.path,
                "params": [{"name": p.name, "type": p.type, "default": p.default, "doc": p.doc, "required": p.required} for p in self.params]}


def search_path() -> list[Path]:
    from ..paths import data_dir
    dirs = [Path(__file__).parent]
    dirs += [Path(p) for p in os.environ.get("SPARKY_RECIPES", "").split(os.pathsep) if p]
    dirs.append(data_dir() / "recipes")
    return [d for d in dirs if d.is_dir()]


def _load_file(path: Path) -> Recipe | None:
    spec = importlib.util.spec_from_file_location(f"sparky_recipe_{path.stem}_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    meta = getattr(mod, "META", None)
    if not meta or not hasattr(mod, "build"):
        return None
    return Recipe(meta["name"], meta.get("summary", ""), list(getattr(mod, "PARAMS", [])), mod.build, meta.get("category", "general"),
                  dict(meta.get("auto", {})), getattr(mod, "ports", None), str(path))


def registry() -> dict[str, Recipe]:
    """name -> Recipe; later search-path entries override earlier ones (user recipes win over shipped ones)."""
    out: dict[str, Recipe] = {}
    for d in search_path():
        for f in sorted(d.glob("*.py")):
            if f.name.startswith("_") or f.stem in ("ctx", "policy"):
                continue
            r = _load_file(f)
            if r:
                out[r.name] = r
    return out


def describe() -> list[dict]:
    return [r.describe() for r in registry().values()]


def build_recipe(name: str, params: dict | None = None, instance: str | None = None, ctx: Any = None):
    """Run a recipe in a fresh SKiDL circuit and return the placement/export view with its declared ports attached."""
    from ..circuits import reset
    from ..design import from_skidl
    from ..model import Port
    from .ctx import Ctx
    reg = registry()
    if name not in reg:
        raise KeyError(f"unknown recipe '{name}'. Known: {sorted(reg)}")
    r = reg[name]
    given, kw = dict(params or {}), {}
    for p in r.params:
        if p.name in given and given[p.name] not in (None, "auto"):
            kw[p.name] = given.pop(p.name)
        elif p.required and p.default is None:
            raise ValueError(f"recipe {name}: missing required param '{p.name}'")
        else:
            kw[p.name] = p.default
            given.pop(p.name, None)
    if given:
        raise ValueError(f"recipe {name}: unknown params {sorted(given)}")
    reset()
    ctx = ctx or Ctx()
    res = r.build(ctx, **kw)
    c = from_skidl(name=instance or name)
    for pt in res.ports:
        c.ports[pt.name] = Port(pt.name, pt.net, pt.kind, pt.direction, pt.voltage, pt.current_a, dict(pt.meta), dict(pt.signals))
    c.notes += res.notes
    c.warnings += res.warnings
    c.calcs.update(res.calcs)
    for comp in c.components.values():
        comp.block = name
    return c
