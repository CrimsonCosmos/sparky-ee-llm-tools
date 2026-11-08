"""Autorouting = KiCad + Freerouting, nothing of ours.

    .kicad_pcb --(pcbnew ExportSpecctraDSN)--> .dsn --(java -jar freerouting)--> .ses --(pcbnew ImportSpecctraSES)--> routed .kicad_pcb

Net classes / track widths / clearances come from the board's own KiCad rules; Freerouting honours them. The jar is downloaded once and
verified against a pinned SHA-256. Tool locations are discovered per OS in toolchain.py."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .paths import data_dir
from .toolchain import java, kicad_python

FREEROUTING_VERSION = "2.1.0"
FREEROUTING_URL = f"https://github.com/freerouting/freerouting/releases/download/v{FREEROUTING_VERSION}/freerouting-{FREEROUTING_VERSION}.jar"
FREEROUTING_SHA256 = "2c07d58f75dac03782664081e7a58b41c25400d871a9fcf166a2ea6fe60d5def"


class RoutingError(RuntimeError):
    pass


@dataclass
class RouteResult:
    path: Path
    seconds: float = 0.0
    stats: dict = field(default_factory=dict)       # Freerouting's own end-of-run JSON (incomplete connections, clearance violations, ...)
    log_tail: str = ""


def jar_path() -> Path:
    return Path(os.environ.get("SPARKY_FREEROUTING_JAR") or data_dir() / "tools" / f"freerouting-{FREEROUTING_VERSION}.jar")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_jar() -> Path:
    """Download the pinned release once; refuse any file whose hash differs."""
    p = jar_path()
    if p.is_file() and _sha256(p) == FREEROUTING_SHA256:
        return p
    import requests
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    with requests.get(FREEROUTING_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    if _sha256(tmp) != FREEROUTING_SHA256:
        tmp.unlink(missing_ok=True)
        raise RoutingError("downloaded Freerouting jar does not match the pinned SHA-256; refusing to use it")
    os.replace(tmp, p)
    return p


def available() -> bool:
    return bool(java() and kicad_python())


_DSN = "import sys, pcbnew\nb = pcbnew.LoadBoard(sys.argv[1])\nsys.exit(0 if pcbnew.ExportSpecctraDSN(b, sys.argv[2]) else 3)\n"
_SES = ("import sys, pcbnew\nb = pcbnew.LoadBoard(sys.argv[1])\n"
        "ok = pcbnew.ImportSpecctraSES(b, sys.argv[2])\npcbnew.SaveBoard(sys.argv[3], b)\nsys.exit(0 if ok else 3)\n")


def _py(script: str, *args: str, timeout: int = 300) -> None:
    exe = kicad_python()
    if not exe:
        raise RoutingError("no Python interpreter with KiCad's pcbnew module found (set SPARKY_KICAD_PYTHON)")
    r = subprocess.run([exe, "-c", script, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RoutingError(f"pcbnew step failed (rc={r.returncode}): {(r.stderr or r.stdout).strip()[-600:]}")


def _last_json(text: str) -> dict:
    """Freerouting prints a JSON statistics object at the end of a run."""
    i = text.rfind("\n{")
    if i == -1:
        return {}
    try:
        return json.loads(text[i + 1:])
    except json.JSONDecodeError:
        return {}


def route_pcb(pcb: str | Path, out: str | Path, *, passes: int = 20, timeout: int = 900, via_spans: tuple = (), layers: list[str] | None = None) -> RouteResult:
    """Route every unrouted connection of `pcb` and write the routed board to `out` (existing tracks are kept by KiCad's SES import)."""
    import time
    jv = java()
    if not jv:
        raise RoutingError("Java (21+) is required to run Freerouting; install a JRE or set SPARKY_JAVA")
    jar = ensure_jar()
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="sparky_route_") as td:
        dsn, ses = Path(td) / "board.dsn", Path(td) / "board.ses"
        _py(_DSN, str(Path(pcb).resolve()), str(dsn))
        if via_spans:                                                      # partial-depth vias: offer the fab's padstacks to the autorouter (pcb/hdi.py)
            from .pcb import hdi
            dsn.write_text(hdi.patch_dsn(dsn.read_text(), via_spans, layers or []))
        cmd = [jv, "-Djava.awt.headless=true", "-jar", str(jar), "-de", str(dsn), "-do", str(ses), "-mp", str(passes)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=td, env={**os.environ, "JAVA_TOOL_OPTIONS": ""})
        if not ses.exists():
            raise RoutingError(f"Freerouting produced no .ses (rc={r.returncode}): {(r.stderr or r.stdout).strip()[-600:]}")
        _py(_SES, str(Path(pcb).resolve()), str(ses), str(Path(out).resolve()))
    return RouteResult(Path(out), time.time() - t0, _last_json(r.stdout), (r.stdout or "")[-400:])
