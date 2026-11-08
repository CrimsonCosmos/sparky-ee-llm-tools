"""Finding and describing the silicon tools. Nothing is bundled: the tools come from the LibreLane tool distribution (Nix store extracted from its published image, or a Docker /
Nix install), described by an environment file written by `tools/silicon_setup.sh`:

    $SPARKY_SILICON_ENV (default <data_dir>/silicon/env.json) = {"PATH": [dirs...], "PYTHONPATH": [...], "PDK_ROOT": "...", "CIEL_DATA_SOURCE": "..."}

Without that file the tools are looked up on PATH. `report()` returns what was found and each tool's version; a missing tool is a missing tool, never a silent skip."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

TOOLS = ("openroad", "yosys", "magic", "netgen", "klayout", "verilator", "iverilog", "sta")
_VERSION_ARGS = {"openroad": ["-version"], "yosys": ["-V"], "magic": ["--version"], "netgen": ["-batch", "quit"], "klayout": ["-v"], "verilator": ["--version"],
                 "iverilog": ["-V"], "sta": ["-version"]}


def env_file() -> Path:
    from sparky.paths import data_dir
    return Path(os.environ.get("SPARKY_SILICON_ENV") or data_dir() / "silicon" / "env.json")


@lru_cache(maxsize=1)
def _config() -> dict:
    f = env_file()
    return json.loads(f.read_text()) if f.is_file() else {}


def clear_cache() -> None:
    _config.cache_clear()
    which.cache_clear()


def env() -> dict[str, str]:
    """Environment for running the tools: the process environment with the configured directories prepended."""
    cfg = _config()
    e = dict(os.environ)
    if cfg.get("PATH"):
        e["PATH"] = os.pathsep.join(cfg["PATH"] + [e.get("PATH", "")])
    if cfg.get("PYTHONPATH"):
        e["PYTHONPATH"] = os.pathsep.join(cfg["PYTHONPATH"] + ([e["PYTHONPATH"]] if e.get("PYTHONPATH") else []))
    for k in ("PDK_ROOT", "CIEL_DATA_SOURCE", "CIEL_HOME"):
        if cfg.get(k):
            e[k] = cfg[k]
    e.setdefault("LANG", "C.UTF-8")
    e.setdefault("LC_ALL", "C.UTF-8")
    return e


@lru_cache(maxsize=None)
def which(tool: str) -> str | None:
    return shutil.which(tool, path=env()["PATH"])


def pdk_root() -> Path | None:
    p = _config().get("PDK_ROOT") or os.environ.get("PDK_ROOT")
    return Path(p) if p and Path(p).is_dir() else None


def python() -> str | None:
    """The interpreter that has LibreLane (the tool distribution's own Python, else the first `python3` on the configured PATH that can import it)."""
    for cand in (which("python3"), shutil.which("python3")):
        if cand and subprocess.run([cand, "-c", "import librelane"], capture_output=True, env=env()).returncode == 0:
            return cand
    return None


def version(tool: str) -> str | None:
    exe = which(tool)
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *_VERSION_ARGS[tool]], capture_output=True, text=True, timeout=30, env=env(), input="")
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout + r.stderr).strip().splitlines()
    return out[0][:100] if out else "present (no version output)"


def librelane_version() -> str | None:
    py = python()
    if not py:
        return None
    r = subprocess.run([py, "-m", "librelane", "--version"], capture_output=True, text=True, timeout=60, env=env())
    return (r.stdout.strip().splitlines() or [None])[0]


def report() -> dict[str, str | None]:
    out: dict[str, str | None] = {"librelane": librelane_version()}
    for t in TOOLS:
        out[t] = version(t)
    out["PDK root"] = str(pdk_root()) if pdk_root() else None
    return out


def available() -> bool:
    """True when LibreLane and every tool it drives can be run (the flow needs all of them)."""
    r = report()
    return all(r[k] for k in ("librelane", "openroad", "yosys", "magic", "netgen", "klayout", "verilator", "sta"))
