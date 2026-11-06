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


def select(files: list[str]) -> list[str] | None:
    tests: set[str] = set()
    for f in files:
        p = Path(f)
        if p.parts[:1] == ("tests",) and p.suffix == ".py" and p.name.startswith("test_"):
            tests.add(f)
        elif p.parts[:2] == ("src", "sparky"):
            area = p.parts[2].removesuffix(".py") if len(p.parts) > 2 else ""
            hits = sorted((ROOT / "tests").glob(f"test_{area}*.py"))
            if not hits:
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
