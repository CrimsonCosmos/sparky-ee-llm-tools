"""Deterministic parametric part picker over the open JLCPCB/LCSC catalogue (CDFER/jlcpcb-parts-database, MIT; built daily from
yaqwsx/jlcparts). No part data lives in this repository: the SQLite file is downloaded once (~400 MB) and queried read-only.

A `Need` states electrical requirements using the database's own attribute names; `Catalog.pick()` filters, ranks (basic parts first,
then price at the order quantity, then stock) and returns real, orderable parts with LCSC SKU, stock, price tiers, datasheet and links."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DB_URL = "https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components.sqlite3"
DB_NAME = "jlcpcb-components.sqlite3"


def db_path() -> Path:
    from ..paths import data_dir
    return Path(os.environ.get("SPARKY_PARTS_DB") or data_dir() / DB_NAME)


def ensure_db(path: Path | None = None, url: str = DB_URL, progress=None) -> Path:
    """Download (resumable) the catalogue if it is not present. Returns the path."""
    path = path or db_path()
    import requests
    raw = {"Accept-Encoding": "identity"}      # a compressed transfer reports the compressed length, which never equals the file size (nor can it be resumed by byte range)
    head = requests.head(url, allow_redirects=True, timeout=60, headers=raw)
    total = int(head.headers.get("content-length", 0))
    have = path.stat().st_size if path.exists() else 0
    if total and have == total:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, headers={**raw, **({"Range": f"bytes={have}-"} if have and total and have < total else {})}, timeout=60) as r:
        r.raise_for_status()
        mode = "ab" if r.status_code == 206 else "wb"
        with open(path, mode) as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                if progress:
                    progress(f.tell(), total)
    if total and path.stat().st_size != total:
        raise IOError(f"incomplete download: {path.stat().st_size} of {total} bytes")
    return path


@lru_cache(maxsize=1)
def _ureg():
    import pint
    return pint.UnitRegistry()


def quantity(text: str):
    """'10kΩ' / '680mV@5A' / '4.7uH' -> pint Quantity (the part after '@' is a test condition and is ignored); None if not numeric."""
    t = str(text).split("@")[0].replace("℃", "degC").replace("µ", "u").replace("Ω", "ohm").replace("±", "").strip()
    if not t or "~" in t:
        return None
    try:
        return _ureg().Quantity(t)
    except Exception:
        return None


def _value(text: str, unit: str) -> float | None:
    q = quantity(text)
    if q is None:
        return None
    try:
        return float(q.to(unit).magnitude)
    except Exception:
        return None


def parse_prices(s: str) -> list[tuple[int, float]]:
    """'1-49:0.0887,50-149:0.0707,6000-:0.0451' -> [(1, .0887), (50, .0707), (6000, .0451)]"""
    out = []
    for part in (s or "").split(","):
        m = re.match(r"\s*(\d+)-\d*:([\d.]+)", part)
        if m:
            out.append((int(m.group(1)), float(m.group(2))))
    return sorted(out)


def price_at(tiers: list[tuple[int, float]], qty: int) -> float | None:
    best = None
    for q, p in tiers:
        if qty >= q:
            best = p
    return best if best is not None else (tiers[0][1] if tiers else None)


@dataclass
class Need:
    """What the design needs. Attribute names are the catalogue's own (see picker.recipes for the common ones)."""
    subcategory: str                                   # SQL LIKE pattern, e.g. 'MOSFETs', 'Power Inductors'
    category: str | None = None
    package: tuple[str, ...] | None = None             # allowed packages (exact), e.g. ('0402', '0603')
    equals: dict[str, str] = field(default_factory=dict)        # attribute == text (e.g. {"Type": "N-Channel"})
    near: dict[str, tuple[str, float]] = field(default_factory=dict)  # attribute ~= value within relative tolerance: ("100nF", 0.01)
    minimum: dict[str, str] = field(default_factory=dict)       # attribute >= quantity, e.g. {"Current - Saturation(Isat)": "5A"}
    maximum: dict[str, str] = field(default_factory=dict)       # attribute <= quantity, e.g. {"RDS(on)": "10mohm"}
    covers: dict[str, tuple[str, str]] = field(default_factory=dict)   # range attribute "a~b" must cover [lo, hi], e.g. {"Voltage - Supply": ("9V", "17V")}
    text: str | None = None                            # free-text FTS match (MPN / description)
    basic_only: bool = False
    min_stock: int = 100


@dataclass
class Choice:
    lcsc: str
    mpn: str
    manufacturer: str
    package: str
    library_type: str
    stock: int
    unit_price: float | None
    price_tiers: list[tuple[int, float]]
    datasheet: str
    description: str
    attributes: dict[str, str]
    category: str = ""
    subcategory: str = ""
    preferred: bool = False

    @property
    def url(self) -> str:
        return f"https://www.lcsc.com/product-detail/{self.lcsc}.html"


class Catalog:
    def __init__(self, path: str | Path | None = None):
        p = Path(path) if path else db_path()
        if not p.exists():
            raise FileNotFoundError(f"parts catalogue not found at {p}; run `sparky parts download` (about 400 MB)")
        self.conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def pick(self, need: Need, qty: int = 1, limit: int = 5) -> list[Choice]:
        where, args = ["j.stock >= ?"], [need.min_stock]
        where.append("j.subcategory LIKE ?")
        args.append(need.subcategory)
        if need.category:
            where.append("j.category LIKE ?")
            args.append(need.category)
        if need.package:
            where.append("j.package IN (%s)" % ",".join("?" * len(need.package)))
            args += list(need.package)
        if need.basic_only:
            where.append("(j.library_type = 'base' OR j.preferred = 1)")
        for attr, val in need.equals.items():
            where.append("json_extract(j.attributes, ?) = ?")
            args += [f'$."{attr}"', val]
        if need.text:
            where.append("(j.mfr LIKE ? OR j.description LIKE ?)")
            args += [f"%{need.text}%", f"%{need.text}%"]
        sql = ("SELECT j.lcsc, j.mfr, j.manufacturer, j.package, j.library_type, j.stock, j.price, j.datasheet, j.description, "
               "j.attributes, j.category, j.subcategory, j.preferred FROM jlc_components j WHERE " + " AND ".join(where))
        rows = self.conn.execute(sql, args).fetchall()
        out: list[Choice] = []
        for r in rows:
            try:
                attrs = json.loads(r["attributes"] or "{}")
            except json.JSONDecodeError:
                continue
            if not self._ok(attrs, need):
                continue
            tiers = parse_prices(r["price"])
            out.append(Choice(f"C{r['lcsc']}", r["mfr"], r["manufacturer"] or "", r["package"], r["library_type"], r["stock"], price_at(tiers, qty),
                              tiers, r["datasheet"] or "", r["description"] or "", attrs, r["category"], r["subcategory"], bool(r["preferred"])))
        # basic parts avoid JLCPCB's extra-part fee, preferred next; then cheapest at the order quantity; then deepest stock
        out.sort(key=lambda c: (0 if c.library_type == "base" else 1 if c.preferred else 2,
                                c.unit_price if c.unit_price is not None else 1e9, -c.stock))
        return out[:limit]

    @staticmethod
    def _ok(attrs: dict[str, str], need: Need) -> bool:
        for attr, (target, tol) in need.near.items():
            if attr not in attrs:
                return False
            q = quantity(target)
            have = _value(attrs[attr], str(q.units)) if q is not None else None
            if have is None or q is None or abs(have - q.magnitude) > abs(q.magnitude) * tol:
                return False
        for attr, (lo, hi) in need.covers.items():
            m = str(attrs.get(attr, "")).split("~")
            if len(m) != 2:
                return False
            a, b = quantity(m[0]), quantity(m[1])
            ql, qh = quantity(lo), quantity(hi)
            if None in (a, b, ql, qh):
                return False
            try:
                if float(a.to(ql.units).magnitude) > ql.magnitude + 1e-9 or float(b.to(qh.units).magnitude) < qh.magnitude - 1e-9:
                    return False
            except Exception:
                return False
        for attr, bound in need.minimum.items():
            q = quantity(bound)
            have = _value(attrs.get(attr, ""), str(q.units)) if q is not None else None
            if have is None or have < q.magnitude * (1 - 1e-9):
                return False
        for attr, bound in need.maximum.items():
            q = quantity(bound)
            have = _value(attrs.get(attr, ""), str(q.units)) if q is not None else None
            if have is None or have > q.magnitude * (1 + 1e-9):
                return False
        return True
