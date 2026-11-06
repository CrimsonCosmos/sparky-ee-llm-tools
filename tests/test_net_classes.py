"""Net classes from declared current / voltage. IPC-2221 hand values (external layer, 1 oz, 20 C rise): computed from the standard's formula
I = 0.048 * dT^0.44 * A^0.725 (A in mil^2); spacing from IPC-2221 table B2 (uncoated external conductors, sea level)."""
import math

import pytest

from sparky.model import Circuit, NetAttr
from sparky.pcb.rules import Rules, net_classes_for, spacing_for_voltage, trace_width_for_current


def test_width_matches_the_ipc2221_formula_by_hand():
    # 2 A at 20 C rise: A = (2 / (0.048 * 20^0.44))^(1/0.725) mil^2, thickness 1 oz = 1.378 mil, width = A / thickness
    area_mil2 = (2.0 / (0.048 * 20 ** 0.44)) ** (1 / 0.725)
    assert trace_width_for_current(2.0, 20.0, 1.0) == pytest.approx(area_mil2 / 1.378 * 0.0254, rel=1e-9)
    assert trace_width_for_current(2.0, 20.0, 1.0) == pytest.approx(0.513, abs=0.002)        # the widely quoted ~0.5 mm for 2 A, 1 oz, 20 C external


def test_more_copper_means_narrower_track_for_the_same_current():
    assert trace_width_for_current(2.0, 20.0, 2.0) == pytest.approx(trace_width_for_current(2.0, 20.0, 1.0) / 2, rel=1e-9)


def test_classes_group_nets_and_leave_signals_alone():
    c = Circuit("t")
    c.net_attrs = {"P1": NetAttr(current_a=2.0), "P2": NetAttr(current_a=2.0), "SIG": NetAttr(), "HV": NetAttr(voltage=48.0), "LOW": NetAttr(current_a=0.05)}
    cls = net_classes_for(c, Rules(layers=2))
    by_net = {n: k for k in cls for n in k["nets"]}
    assert set(by_net) == {"P1", "P2", "HV"}                                                # SIG and the tiny-current LOW net stay in the default class
    assert by_net["P1"] is by_net["P2"] and by_net["P1"]["track_width"] == pytest.approx(math.ceil(0.5133 / 0.001) * 0.001, abs=0.0011)
    assert by_net["HV"]["clearance"] == spacing_for_voltage(48.0) == 0.6                     # IPC-2221 B2: 31-50 V -> 0.6 mm
