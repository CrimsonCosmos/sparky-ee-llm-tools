"""Full system build of examples/can_node with the REAL catalogue (skipped when it is not downloaded): resolver -> recipes -> placement -> Freerouting -> KiCad DRC
-> package -> shopping list. Expected values are hand-checked: buck load = (50 mA STM32 + 17 mA SN65HVD230) x 1.1 margin."""
import json
import shutil
from pathlib import Path

import pytest

from sparky import routing
from sparky.picker.catalog import db_path
from sparky.toolchain import kicad_has

EX = Path(__file__).resolve().parents[1] / "examples" / "can_node"
pytestmark = [pytest.mark.slow, pytest.mark.skipif(not db_path().exists() or not routing.available() or not kicad_has("pcb_drc"),
                                                    reason="needs the downloaded parts catalogue, KiCad >= 8, Java and pcbnew")]


def test_can_node_system_builds_and_the_netlist_matches_the_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "specs").mkdir(parents=True)
    for f in (EX / "specs").glob("*.json"):
        shutil.copy(f, tmp_path / "home" / "specs" / f.name)
    from sparky.purchase.plan import build_purchase_files
    from sparky.system.build import build_system
    from sparky.system.plan import SystemPlan
    plan = SystemPlan.from_dict(json.loads((EX / "plan.json").read_text()))
    sb = build_system(plan, tmp_path / "out", log=None, pcb_time_s=300)
    build_purchase_files(sb)
    assert not [i for i in sb.resolved.issues + sb.issues if i.severity == "error"], [i.msg for i in sb.resolved.issues + sb.issues]
    assert sb.resolved.params[("ctrl", "buck")]["iout"] == pytest.approx((0.050 + 0.017) * 1.1)
    res = sb.boards["ctrl"]["result"]
    assert res.success and not res.drc.errors and not res.drc.unconnected
    net = (tmp_path / "out" / "boards" / "ctrl" / "ctrl.net").read_text()
    assert 'name "mcu.CAN_TX"' in net and 'name "can.CANH"' in net
    assert (tmp_path / "out" / "purchase" / "shopping_list.md").exists()
    assert "NRST capacitor NOT fitted" in (tmp_path / "out" / "SYSTEM.md").read_text()          # unspecified values are warned about, not invented
