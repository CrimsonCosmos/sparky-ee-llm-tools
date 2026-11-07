"""Run only the tests relevant to the files changed since a git ref (default: HEAD~1).

  python tools/scoped_tests.py [BASE_REF] [-- extra pytest args]

Mapping is by package area: src/sparky/<area>/... or <area>.py -> tests/test_<area>*.py.
Changes outside a known area (pyproject, conftest, model.py ...) fall back to the whole FAST suite.
Slow/integration tests are never selected here; run them explicitly with `pytest -m slow`."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def changed(base: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", base, "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l.strip()]


# source area (src/sparky/<area>[.py]) -> test files that exercise it (globs under tests/). Areas not listed here are shared core: they run the whole fast suite.
AREA_TESTS = {
    "recipes": ["test_recipe_*.py", "test_cli.py", "test_system.py"], "circuits": ["test_recipe_*.py", "test_design.py"], "design": ["test_design.py", "test_recipe_*.py"],
    "picker": ["test_picker*.py", "test_recipe_*.py"], "datasheet": ["test_recipe_*.py"], "equations": ["test_eq_*.py", "test_recipe_*.py"],
    "verify": ["test_spice_*.py", "test_recipe_buck.py"], "analysis": ["test_irdrop_*.py", "test_thermal.py", "test_emc.py"], "krt": ["test_krt*.py"],
    "constraints": ["test_krt*.py", "test_design.py"], "system": ["test_system.py", "test_cli.py"], "purchase": ["test_system.py"], "libsrc": ["test_libsrc.py"],
    "routing": ["test_routing.py"], "kicad_backend": ["test_kicad_drc_json.py"], "kicad_symbols": ["test_kicad_sch.py", "test_cli.py"], "mcp_server": ["test_cli.py"],
    "cli": ["test_cli.py"], "export": ["test_kicad_sch.py", "test_flow_kicad.py", "test_two_sided.py"], "pcb": ["test_two_sided.py", "test_planes_stackup.py", "test_net_classes.py", "test_routing.py"], "mechanical": [], "sourcing": [],
}


def select(files: list[str]) -> list[str] | None:
    tests: set[str] = set()
    for f in files:
        p = Path(f)
        if p.parts[:1] == ("tests",) and p.suffix == ".py" and p.name.startswith("test_"):
            tests.add(f)
        elif p.parts[:2] == ("src", "sparky"):
            area = p.parts[2].removesuffix(".py") if len(p.parts) > 2 else ""
            pats = AREA_TESTS.get(area)
            if pats is None:
                pats = [f"test_{area}*.py"]
            hits = sorted({h for pat in pats for h in (ROOT / "tests").glob(pat)})
            if not hits and area not in AREA_TESTS:
                return None                      # unknown area / shared core -> whole fast suite
            tests.update(str(h.relative_to(ROOT)) for h in hits)
        elif p.suffix in (".md", ".txt") or p.parts[:1] in (("docs",), ("examples",)):
            continue
        else:
            return None
    return sorted(tests)


def main() -> int:
    args = sys.argv[1:]
    extra = args[args.index("--") + 1:] if "--" in args else []
    base = args[0] if args and args[0] != "--" else "HEAD~1"
    sel = select(changed(base))
    if sel == []:
        print("no code changes -> no tests")
        return 0
    cmd = [sys.executable, "-m", "pytest", *(sel or []), *extra]
    print("running:", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
