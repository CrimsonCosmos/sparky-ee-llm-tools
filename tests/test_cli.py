import pytest

from sparky.cli import main
from conftest import needs_kicad_libs


def test_recipes_command_lists_the_shipped_recipes(capsys):
    main(["recipes"])
    out = capsys.readouterr().out
    for name in ("buck", "ldo", "battery_input", "can_node", "mcu_core", "connector_breakout", "mounting_holes"):
        assert name in out
    assert "auto:" in out and "vout(out)" in out                    # ports and derived parameters are shown


@needs_kicad_libs
def test_symbols_command_finds_a_real_symbol(capsys):
    main(["symbols", "TPS5430"])
    assert "Regulator_Switching:TPS5430DDA" in capsys.readouterr().out


def test_mcp_tools_are_listed_with_sparky_names():
    from sparky.mcp_server import TOOLS
    assert set(TOOLS) >= {"sparky_list_recipes", "sparky_search_symbols", "sparky_pick_parts", "sparky_save_spec", "sparky_write_recipe", "sparky_validate_plan",
                          "sparky_build_system", "sparky_build_recipe_board"}
    assert not [n for n in TOOLS if n.startswith("forge")]


def test_write_recipe_validates_and_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_HOME", str(tmp_path))
    from sparky.mcp_server import tool_write_recipe
    from sparky.recipes import registry
    src = ('from sparky.recipes import Param, Result\nMETA = dict(name="demo_r", summary="x")\nPARAMS = [Param("n", "number", 1, "")]\n'
           'def build(ctx, n):\n    return Result()\n')
    assert tool_write_recipe({"name": "demo_r", "source": src})["recipe"]["name"] == "demo_r"
    assert "demo_r" in registry()
    assert "error" in tool_write_recipe({"name": "bad_r", "source": "x = 1\n"})                # no META/build -> rejected, not stored
    assert "bad_r" not in registry()
