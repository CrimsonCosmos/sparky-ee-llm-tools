"""Insertion loss: dielectric part against the closed forms, scaling laws, roughness, and the budget check (conductor loss of stripline is a literature formula and is only
checked for its physics, not independently validated)."""
import math

import pytest

from sparky.analysis import diffpair, loss, lines
from sparky.constants import C0_M_PER_S, NP_TO_DB
from sparky.constraints import LossBudget
from sparky.export.kicad import layer_z_mm
from sparky.pcb.rules import rules_for

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not (lines.available() and diffpair.available()), reason="scikit-rf and atlc needed")]

R = rules_for(4, "jlcpcb-4l")
TINY_RHO = 1e-14                    # makes the conductor lossless so only the dielectric is left


def test_stripline_dielectric_loss_is_pi_f_sqrt_er_tand_over_c_at_the_reference_frequency():
    f = R.dielectric_f_ref_hz
    a = loss.stripline_alpha_db_per_mm(0.15, 0.18, 0.18, 0.035, R, f, rho=TINY_RHO)
    hand = math.pi * f * math.sqrt(R.dielectric_er) * R.dielectric_tan_d / C0_M_PER_S * NP_TO_DB * 1e-3        # exact for TEM in a homogeneous lossy dielectric
    assert a == pytest.approx(hand, rel=0.01)


def test_microstrip_dielectric_loss_matches_pozars_expression():
    f = R.dielectric_f_ref_hz
    w, h, t = 0.3, 0.2, 0.035
    a = loss.microstrip_alpha_db_per_mm(w, h, t, R, f, rho=TINY_RHO)
    u = w / h
    er_eff = (R.dielectric_er + 1) / 2 + (R.dielectric_er - 1) / 2 * (1 + 12 / u) ** -0.5                    # Hammerstad
    hand = math.pi * f / C0_M_PER_S * R.dielectric_er / math.sqrt(er_eff) * (er_eff - 1) / (R.dielectric_er - 1) * R.dielectric_tan_d * NP_TO_DB * 1e-3   # Pozar eq. 3.30
    assert a == pytest.approx(hand, rel=0.08)


def test_loss_grows_with_frequency_and_falls_with_wider_copper_and_rises_with_roughness():
    lo, hi = loss.stripline_alpha_db_per_mm(0.15, 0.18, 0.18, 0.035, R, 1e9), loss.stripline_alpha_db_per_mm(0.15, 0.18, 0.18, 0.035, R, 10e9)
    assert hi > 5 * lo
    assert loss.stripline_alpha_db_per_mm(0.30, 0.18, 0.18, 0.035, R, 5e9) < loss.stripline_alpha_db_per_mm(0.10, 0.18, 0.18, 0.035, R, 5e9)
    from dataclasses import replace
    rough = replace(R, copper_roughness_rms_m=1.5e-6)
    assert loss.microstrip_alpha_db_per_mm(0.3, 0.2, 0.035, rough, 5e9) > loss.microstrip_alpha_db_per_mm(0.3, 0.2, 0.035, R, 5e9)


def _seg(net, layer, length, w=0.15):
    return f'(segment (start 0 0) (end {length} 0) (width {w}) (layer "{layer}") (net "{net}"))'


def test_net_loss_is_linear_in_length_and_vias_add_their_input():
    z = layer_z_mm(R, 4)
    a = loss.net_loss_db(f'(kicad_pcb {_seg("N", "In1.Cu", 10)})', R, z, 5e9)["N"]
    b = loss.net_loss_db(f'(kicad_pcb {_seg("N", "In1.Cu", 20)})', R, z, 5e9)["N"]
    assert b == pytest.approx(2 * a, rel=1e-9)
    via = '(via (at 1 1) (size 0.5) (drill 0.25) (layers "F.Cu" "B.Cu") (net "N"))'
    c = loss.net_loss_db(f'(kicad_pcb {_seg("N", "In1.Cu", 10)} {via} {via})', R, z, 5e9, via_loss_db_each=0.1)["N"]
    assert c == pytest.approx(a + 0.2)


def test_budget_row_says_yes_no_and_not_verified():
    z = layer_z_mm(R, 4)
    txt = f'(kicad_pcb {_seg("N", "In1.Cu", 50)})'
    got = loss.net_loss_db(txt, R, z, 5e9)["N"]
    ok = loss.check(txt, [LossBudget(["N"], got * 1.1, 5e9)], R, z)[0]
    bad = loss.check(txt, [LossBudget(["N"], got * 0.9, 5e9)], R, z)[0]
    assert ok["ok"] is True and bad["ok"] is False and ok["losses_db"][0] == pytest.approx(got, abs=1e-3)
    assert loss.check(txt, [LossBudget(["MISSING"], 1.0, 5e9)], R, z)[0]["ok"] is False                      # an unrouted net has no loss number: never a pass
