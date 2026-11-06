"""Datasheet numbers a recipe needs (reference voltage, switching frequency, ...), cached per part with their source.

Contract with the supervisor: a recipe asks for named fields; if any are not cached it raises NeedsDatasheet listing the missing fields and where the
datasheet is. Claude reads the (official) datasheet and calls `save` - this is the single LLM touch point for part numbers. Values are never invented."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .paths import data_dir


class NeedsDatasheet(RuntimeError):
    def __init__(self, part: str, missing: dict[str, str], url: str = ""):
        self.part, self.missing, self.url = part, missing, url
        fields = "; ".join(f"{k} ({v})" for k, v in missing.items())
        super().__init__(f"datasheet numbers needed for {part}: {fields}. Datasheet: {url or 'unknown (search the manufacturer site)'}. "
                         f"Read it and store with `sparky save-spec {part} key=value ... --source <url/page>`")


def _file(part: str) -> Path:
    d = data_dir() / "specs"
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^A-Za-z0-9._-]", "_", part) + ".json")


def load(part: str) -> dict:
    f = _file(part)
    return json.loads(f.read_text()) if f.exists() else {}


def save(part: str, values: dict[str, float], source: str) -> Path:
    rec = load(part)
    rec.setdefault("values", {}).update({k: float(v) for k, v in values.items()})
    rec.setdefault("sources", []).append(source)
    f = _file(part)
    f.write_text(json.dumps(rec, indent=1))
    return f


def need(part: str, fields: dict[str, str], url: str = "") -> dict[str, float]:
    """fields: {name: 'what/unit'}. Returns {name: value} or raises NeedsDatasheet for the missing ones."""
    have = load(part).get("values", {})
    missing = {k: v for k, v in fields.items() if k not in have}
    if missing:
        raise NeedsDatasheet(part, missing, url)
    return {k: have[k] for k in fields}
