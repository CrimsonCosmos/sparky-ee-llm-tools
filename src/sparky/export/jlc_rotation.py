"""JLCPCB placement-file corrections. KiCad and JLCPCB's component library orient the same footprint differently, and JLCPCB does not mirror bottom parts as
KiCad does. The correction table is the community's, not ours: bennymeg/Fabrication-Toolkit's `plugins/transformations.csv` (Apache-2.0), fetched at a pinned
commit into the data directory (never vendored), with the rule it documents in `plugins/process.py`:
    rotation = (rot  or  180 - rot for a bottom part) + table rotation of the first row whose regex matches the footprint name, modulo 360
    position  = KiCad position + the row's (dx, dy) rotated by the part's rotation (bottom parts mirror the y term).
We read that code, we did not run it, and the result is NOT checked against JLCPCB's own viewer: verify polarised parts in their placement preview."""
from __future__ import annotations

import csv
import math
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..paths import data_dir

SOURCE_REPO = "bennymeg/Fabrication-Toolkit"
SOURCE_COMMIT = "7f627b6be76081b962161b9eb9fe3a326a18c915"            # pinned: the table is data, so a silent upstream change must not alter a build
SOURCE_URL = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_COMMIT}/plugins/transformations.csv"


@dataclass(frozen=True)
class Row:
    pattern: str
    rotation: float
    dx: float
    dy: float


def cache_path() -> Path:
    return data_dir() / f"jlc-transformations-{SOURCE_COMMIT[:10]}.csv"


def fetch(path: Path | None = None, url: str = SOURCE_URL) -> Path:
    p = path or cache_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=60) as r:             # nosec - fixed https URL
            p.write_bytes(r.read())
    return p


def parse(text: str) -> list[Row]:
    rows = []
    for i, rec in enumerate(csv.reader(text.splitlines())):
        if i == 0 or not rec:
            continue
        rec = (rec + ["0", "0", "0"])[:4]
        try:
            rows.append(Row(rec[0], float(rec[1] or 0), float(rec[2] or 0), float(rec[3] or 0)))
        except ValueError as e:
            raise ValueError(f"transformations row {i + 1}: {rec}") from e
    return rows


def lookup(rows: list[Row], footprint: str) -> Row | None:
    """First row whose regex matches. A pattern containing ':' is matched against the full 'Lib:Name'; otherwise against the name after the colon."""
    name = footprint.split(":", 1)[-1]
    for r in rows:
        if re.search(r.pattern, footprint if ":" in r.pattern else name):
            return r
    return None


def correct(x_mm: float, y_mm: float, rot_deg: float, bottom: bool, row: Row | None) -> tuple[float, float, float]:
    """(x, y, rotation) as JLCPCB wants them. x, y in the placement file's frame (y up), rot as KiCad stores it."""
    dx, dy = (row.dx, row.dy) if row else (0.0, 0.0)
    s, c = math.sin(math.radians(rot_deg)), math.cos(math.radians(rot_deg))
    if bottom:
        ox, oy = dx * c + dy * s, dx * s - dy * c
        rot = 180.0 - rot_deg
    else:
        ox, oy = dx * c - dy * s, dx * s + dy * c
        rot = rot_deg
    return x_mm + ox, y_mm + oy, (rot + (row.rotation if row else 0.0)) % 360.0
