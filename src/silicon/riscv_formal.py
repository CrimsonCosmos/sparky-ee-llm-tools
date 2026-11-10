"""ISA-level formal verification of a RISC-V core with riscv-formal (YosysHQ, ISC licence): its generated SymbiYosys checks compare every retired instruction (reported on the RVFI port) against the ISA semantics.
This adapter prepares a core directory from a riscv-formal checkout, selects checks, runs them with sby, and reports PASS/FAIL/ERROR per check. Needs a riscv-formal checkout (RISCV_FORMAL env var or `root`),
sby and an SMT solver (z3 from PyPI works: `solver z3`). Without them the result is NOT RUN. Proofs are bounded (the checks' own depth), as the project documents."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import toolchain


@dataclass
class CheckResult:
    name: str
    status: str                    # pass | fail | error | timeout
    seconds: float
    detail: str = ""


@dataclass
class RvfResult:
    checks: list[CheckResult] = field(default_factory=list)
    not_run: str = ""

    @property
    def all_pass(self) -> bool:
        return not self.not_run and bool(self.checks) and all(c.status == "pass" for c in self.checks)

    def by_name(self) -> dict[str, str]:
        return {c.name: c.status for c in self.checks}


def find_root(root: str | Path | None = None) -> Path | None:
    for cand in (root, os.environ.get("RISCV_FORMAL")):
        if cand and (Path(cand) / "checks" / "genchecks.py").is_file():
            return Path(cand)
    return None


def prepare(workdir: Path, root: Path, core: str, core_files: dict[str, Path], checks: list[str], bug: str | None = None, solver: str = "z3") -> Path:
    """Copy the checkout to `workdir`, add the core's source files, and write a configuration that keeps only `checks` (regexes matched from the start of the check name). Returns the core directory."""
    tree = workdir / "rvf"
    if tree.exists():
        shutil.rmtree(tree)
    shutil.copytree(root, tree, ignore=shutil.ignore_patterns(".git"))
    cdir = tree / "cores" / core
    for name, src in core_files.items():
        shutil.copy2(src, cdir / name)
    cfg = (cdir / "checks.cfg").read_text()
    if bug:
        cfg = cfg.replace("PICORV32_TESTBUG_NONE", f"PICORV32_TESTBUG_{bug}")
    cfg = re.sub(r"(\[options\]\n)", r"\1solver " + solver + "\n", cfg, count=1)
    cfg += "\n[filter-checks]\n" + "".join(f"+ {c}$\n" for c in checks) + "- .*\n"
    (cdir / "checks.cfg").write_text(cfg)
    return cdir


def run(workdir: str | Path, core: str, core_files: dict[str, Path], checks: list[str], root: str | Path | None = None, bug: str | None = None, solver: str = "z3", timeout: int = 900) -> RvfResult:
    r = find_root(root)
    if r is None:
        return RvfResult(not_run="no riscv-formal checkout (set RISCV_FORMAL or pass root; https://github.com/YosysHQ/riscv-formal)")
    env = toolchain.env()
    env["PATH"] = env.get("PATH", "") + os.pathsep + str(Path(sys.executable).parent)
    sby = toolchain.which("sby")
    if not sby or (solver not in ("bmc3", "btormc") and not shutil.which(solver, path=env["PATH"])):
        return RvfResult(not_run=f"need sby and the SMT solver '{solver}' (pip install z3-solver)")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    cdir = prepare(workdir, r, core, core_files, checks, bug, solver)
    gen = subprocess.run([sys.executable, "-I", "../../checks/genchecks.py"], cwd=cdir, capture_output=True, text=True, env=env)
    if gen.returncode != 0:
        return RvfResult(not_run=f"genchecks failed: {(gen.stderr or gen.stdout).strip()[-300:]}")
    out = RvfResult()
    for sby_file in sorted((cdir / "checks").glob("*.sby")):
        t0 = time.time()
        try:
            p = subprocess.run([sby, "-f", sby_file.name], cwd=cdir / "checks", capture_output=True, text=True, timeout=timeout, env=env)
            text = p.stdout + p.stderr
            status = "pass" if "DONE (PASS" in text else "fail" if "DONE (FAIL" in text else "error"
            detail = "" if status == "pass" else text.strip()[-300:]
        except subprocess.TimeoutExpired:
            status, detail = "timeout", f"no verdict within {timeout} s"
        out.checks.append(CheckResult(sby_file.stem, status, time.time() - t0, detail))
    return out
