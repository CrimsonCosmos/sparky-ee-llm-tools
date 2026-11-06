import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("scoped_tests", Path(__file__).resolve().parents[1] / "tools" / "scoped_tests.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


def test_a_recipe_change_runs_only_recipe_related_tests():
    sel = st.select(["src/sparky/recipes/buck.py"])
    assert sel and all(("recipe" in t or t.endswith(("test_cli.py", "test_system.py"))) for t in sel) and "tests/test_flow_kicad.py" not in sel


def test_shared_core_and_unknown_areas_run_everything():
    assert st.select(["src/sparky/model.py"]) is None and st.select(["pyproject.toml"]) is None


def test_docs_only_change_runs_nothing():
    assert st.select(["docs/NEXT.md", "README.md"]) == []
