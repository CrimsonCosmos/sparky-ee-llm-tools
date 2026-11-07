"""KiCad's own ERC on the generated schematic and board<->schematic parity. Each check is shown to be able to fail."""
import pytest

from conftest import mcu_circuit, needs_kicad_libs
from sparky import kicad_backend, toolchain
from sparky.export import kicad_sch
from sparky.export.kicad import to_kicad_pcb
from sparky.export.kicad_project import write_lib_tables
from sparky.pcb.placement import PlaceOptions, place
from sparky.pcb.rules import Rules

pytestmark = [needs_kicad_libs, pytest.mark.skipif(not toolchain.kicad_has("sch_erc"), reason="kicad-cli with sch erc missing")]


def _project(tmp_path, circuit):
    sch = tmp_path / "p.kicad_sch"
    txt = kicad_sch.to_kicad_sch(circuit)
    sch.write_text(txt)
    write_lib_tables(circuit, tmp_path)
    board = place(circuit, Rules(layers=2), PlaceOptions(seed=1))
    pcb = to_kicad_pcb(board)
    (tmp_path / "p.kicad_pcb").write_text(pcb)
    return sch, tmp_path / "p.kicad_pcb", board


def test_generated_schematic_has_no_erc_errors_or_warnings(tmp_path):
    sch, _, _ = _project(tmp_path, mcu_circuit())
    erc = kicad_backend.run_erc(sch)
    assert erc.tool == "kicad-cli sch erc"
    assert not erc.violations, erc.violations


def test_erc_can_fail_without_the_power_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(kicad_sch, "_power_flags", lambda *a, **k: [])
    sch, _, _ = _project(tmp_path, mcu_circuit())
    erc = kicad_backend.run_erc(sch)
    assert {v["type"] for v in erc.errors} == {"power_pin_not_driven"}


def test_board_and_schematic_agree(tmp_path):
    _, pcb, board = _project(tmp_path, mcu_circuit())
    rep = kicad_backend.run_drc(pcb, refill=False, rules=board.rules)
    assert not rep.footprint_errors, rep.footprint_errors


def test_parity_notices_a_net_swapped_on_the_board(tmp_path):
    _, pcb, board = _project(tmp_path, mcu_circuit())
    text = pcb.read_text()
    first_c1_net = text.index('(footprint "Capacitor_SMD')
    seg = text[first_c1_net:]
    k = seg.index('(net ')
    kk = seg.index(')', seg.index('"', k + 10) + 1)
    edited = text[:first_c1_net] + seg[:k] + '(net 1 "GND")' + seg[kk + 1:]
    pcb.write_text(edited)
    rep = kicad_backend.run_drc(pcb, refill=False, rules=board.rules)
    assert rep.footprint_errors, "parity must object to a pad moved to another net"
