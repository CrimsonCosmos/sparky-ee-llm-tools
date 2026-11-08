"""Run LibreLane on a spec and read back what it produced. LibreLane is run as an external process (`python -m librelane`) with the configured tool environment, in its own run
directory; we never import it. The result carries LibreLane's own metrics untouched: `metric()` returns None for a metric the run did not report, which is NOT the same as zero."""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import toolchain
from .spec import SiliconSpec

ARTIFACT_DIRS = {"gds": "gds", "lef": "lef", "def": "def", "netlist": "nl", "sdc": "sdc", "sdf": "sdf", "spef": "spef", "odb": "odb", "lib": "lib", "spice": "spice"}


@dataclass
class FlowResult:
    ok: bool
    run_dir: Path | None
    seconds: float
    metrics: dict = field(default_factory=dict)
    resolved: dict = field(default_factory=dict)       # LibreLane's resolved configuration (PDK paths, library, corners: the values the run actually used)
    artifacts: dict[str, Path] = field(default_factory=dict)
    log_path: Path | None = None
    failed_step: str | None = None
    errors: list[str] = field(default_factory=list)
    not_run: str | None = None             # why nothing was run (tools missing, ...)

    def metric(self, name: str):
        """A metric LibreLane reported, else None. A metric that is absent was NOT measured: callers must not read it as zero."""
        return self.metrics.get(name)

    @property
    def final(self) -> Path | None:
        return self.run_dir / "final" if self.run_dir else None


def run_flow(spec: SiliconSpec, workdir: str | Path, tag: str = "sparky", timeout: int = 3 * 3600, extra_args: list[str] | None = None) -> FlowResult:
    """Write the configuration into `workdir`, run the flow, parse the run directory."""
    workdir = Path(workdir)
    if not toolchain.available():
        return FlowResult(False, None, 0.0, not_run="the silicon tools are not installed (tools/silicon_setup.sh; docs/SILICON_DEPENDENCIES.md)")
    pdk_root = toolchain.pdk_root()
    if pdk_root is None:
        return FlowResult(False, None, 0.0, not_run="no PDK root configured (PDK_ROOT)")
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = workdir / "config.json"
    cfg.write_text(json.dumps(spec.to_config(), indent=1))
    log = workdir / f"{tag}.log"
    cmd = [toolchain.python(), "-m", "librelane", "--pdk-root", str(pdk_root), "--pdk", spec.pdk, "--run-tag", tag, *(extra_args or []), str(cfg)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=toolchain.env(), cwd=workdir)
        text, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        text, rc = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) + f"\n[ERROR] timed out after {timeout} s", -9
    seconds = time.time() - t0
    log.write_text(text)
    return parse_run(workdir / "runs" / tag, rc, seconds, log, text)


def parse_run(run_dir: Path, rc: int, seconds: float, log: Path | None, text: str = "") -> FlowResult:
    errors = [l.strip() for l in text.splitlines() if re.search(r"\[ERROR\]|\bERROR\b", l)][:10]
    steps = re.findall(r"Running '([^']+)'", text)
    final = run_dir / "final"
    metrics_file = final / "metrics.json"
    metrics = json.loads(metrics_file.read_text()) if metrics_file.is_file() else {}
    artifacts = {}
    for kind, sub in ARTIFACT_DIRS.items():
        d = final / sub
        if d.is_dir():
            found = sorted(p for p in d.iterdir() if p.is_file() or p.is_dir())
            if found:
                artifacts[kind] = found[0] if kind not in ("lib", "spef", "sdf") else d
    ok = rc == 0 and bool(metrics)
    resolved_file = run_dir / "resolved.json"
    resolved = json.loads(resolved_file.read_text()) if resolved_file.is_file() else {}
    return FlowResult(ok, run_dir if run_dir.is_dir() else None, seconds, metrics, resolved, artifacts, log, None if ok else (steps[-1] if steps else None), errors)
