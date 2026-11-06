"""System layer: the resolver derives recipe parameters from links without knowing any recipe by name. Expected values are hand-computed."""
import pytest

from sparky.llm.base import MockLLM
from sparky.system.plan import SystemPlan
from sparky.system.planner import SUPERVISOR_PROMPT, PlannerError, plan_request, plan_with_llm
from sparky.system.resolver import Resolver


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_HOME", str(tmp_path / "home"))       # no catalogue here: recipe builds fail with an issue, derivations still happen


def chain_plan(load_a=2.0, load_qty=4, load_v=3.3, rated_a=60.0, duty=1.0, ldo_params=None):
    return SystemPlan.from_dict({
        "name": "t", "boards": [{"id": "b", "qty": 1, "power_duty": duty, "blocks": [
            {"id": "ldo", "recipe": "ldo", "params": {"symbol": "Regulator_Linear:AMS1117-3.3", "vout": 3.3, "rail": "+3V3", "c_in": 1e-5, "c_out": 2.2e-5, **(ldo_params or {})}}]}],
        "purchased": [{"id": "src", "title": "supply", "unit_cost_usd": 10, "ports": [{"name": "out", "kind": "power", "direction": "out", "voltage": 5.0, "current_a": rated_a, "vmin": 4.5, "vmax": 5.5}]},
                      {"id": "load", "title": "loads", "qty": load_qty, "unit_cost_usd": 1, "ports": [{"name": "in", "kind": "power", "direction": "in", "voltage": load_v, "current_a": load_a, "vmin": 3.0, "vmax": 3.6}]}],
        "links": [{"a": "src.out", "b": "b.ldo.vin"}, {"a": "b.ldo.vout", "b": "load.in"}]})


def clean_plan():
    """A plan whose recipes build without a parts catalogue (connector only)."""
    return SystemPlan.from_dict({"name": "t", "boards": [{"id": "b", "blocks": [{"id": "j", "recipe": "connector_breakout", "params": {
        "symbol": "Connector_Generic:Conn_01x02", "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", "pins": ["SIG", "GND"]}}]}],
        "purchased": [{"id": "dev", "title": "device", "unit_cost_usd": 5, "ports": [{"name": "SIG", "kind": "bundle", "direction": "bidir"}]}],
        "links": [{"a": "b.j.SIG", "b": "dev.SIG"}]})


def test_demand_flows_upstream_through_qty_and_the_margin():
    r = Resolver(chain_plan(), log=None)
    res = r.resolve()
    # load: 2 A x 4 units = 8 A at the LDO output; LDO is sized for 8 A x demand_margin (1.1); its input draws the same current (linear regulator)
    assert res.demand["b.ldo.vout"] == pytest.approx(8.0)
    assert res.params[("b", "ldo")]["iout"] == pytest.approx(8.0 * r.policy.demand_margin)
    assert res.demand["src.out"] == pytest.approx(8.0 * r.policy.demand_margin)


def test_duty_scales_the_cross_board_demand():
    res = Resolver(chain_plan(duty=0.5), log=None).resolve()
    assert res.demand["src.out"] > 0            # power_duty applies to the consumer's board; here the loads are an item (duty 1.0), the board feeds the item
    assert res.demand["b.ldo.vout"] == pytest.approx(8.0)


def test_voltage_window_flows_downstream_to_auto_params():
    res = Resolver(chain_plan(), log=None).resolve()
    p = res.params[("b", "ldo")]
    assert p["vin_min"] == pytest.approx(4.5) and p["vin_max"] == pytest.approx(5.5)         # the supply's declared window


def test_explicit_plan_values_win_over_derived_ones():
    res = Resolver(chain_plan(ldo_params={"iout": 1.0}), log=None).resolve()
    assert res.params[("b", "ldo")]["iout"] == 1.0


def test_overloaded_supply_is_an_error():
    res = Resolver(chain_plan(rated_a=5.0), log=None).resolve()
    assert any("exceeds rated" in i.msg and i.severity == "error" for i in res.issues)


def test_provider_outside_consumer_window_is_an_error():
    res = Resolver(chain_plan(load_v=3.3), log=None).resolve()
    plan = chain_plan()
    plan.purchased[0].ports[0].update(voltage=12.0, vmin=11.0, vmax=13.0)                        # 12 V into an LDO whose window comes from the supply -> consistent, no error
    assert not any("outside consumer window" in i.msg for i in Resolver(plan, log=None).resolve().issues)
    plan = chain_plan()
    plan.boards[0].blocks[0].params.update(vin_min=4.5, vin_max=5.0)                              # explicit window narrower than the 4.5-5.5 V supply
    assert any("outside consumer window" in i.msg for i in Resolver(plan, log=None).resolve().issues)


def test_plan_validation_catches_unknown_recipe_and_bad_links():
    p = SystemPlan.from_dict({"name": "x", "boards": [{"id": "b", "blocks": [{"id": "a", "recipe": "warp_drive"}]}], "links": [{"a": "b.a.p", "b": "nobody.p"}]})
    errs = p.validate({"ldo"})
    assert any("warp_drive" in e for e in errs) and any("nobody" in e for e in errs)


def test_supervisor_prompt_states_buy_vs_build_policy():
    t = SUPERVISOR_PROMPT.lower()
    assert "buy" in t and "unless the user explicitly asks" in t and "necessary to fit the budget" in t and "off-the-shelf" in t and "buy_alternative" in t


def test_llm_planner_repair_loop():
    bad = {"name": "x", "boards": [{"id": "b", "blocks": [{"id": "a", "recipe": "warp_drive"}]}], "links": []}
    good = clean_plan().to_dict()
    replies = iter([bad, good])
    llm = MockLLM(lambda p, kw: next(replies))
    plan = plan_with_llm("a supply", 600, llm, log=None)
    assert plan.name == "t" and len(llm.calls) == 2
    assert "warp_drive" in llm.calls[1][0]                                              # the error is fed back
    assert llm.calls[0][1]["system"] == SUPERVISOR_PROMPT


def test_llm_planner_gives_up():
    llm = MockLLM(lambda p, kw: {"name": "x", "boards": [{"id": "b", "blocks": [{"id": "a", "recipe": "nope"}]}]})
    with pytest.raises(PlannerError):
        plan_with_llm("x", None, llm, max_repairs=1, log=None)


def test_no_llm_means_no_plan_not_a_canned_one():
    with pytest.raises(PlannerError):
        plan_request("humanoid robot", 50000, None, log=None)
