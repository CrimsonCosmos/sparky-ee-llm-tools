import math

import pytest

from sparky.pcb.planes import plane_indices
from sparky.pcb.rules import OZ_CU_MM, rules_for


@pytest.mark.parametrize("n", [4, 6, 8, 10, 16, 32])
def test_every_inner_signal_layer_has_a_reference_plane_neighbour(n):
    planes = set(plane_indices(n))
    assert {1, n - 2} <= planes and 0 not in planes and n - 1 not in planes             # planes inside, outer layers stay for parts
    for i in range(1, n - 1):
        assert i in planes or (i - 1) in planes or (i + 1) in planes, (n, i)


def test_four_layer_keeps_the_classic_two_inner_planes():
    assert plane_indices(4) == [1, 2]


@pytest.mark.parametrize("n", [2, 4, 8, 16, 32])
def test_board_thickness_and_drill_follow_from_layer_count(n):
    r = rules_for(n, "generic")
    copper = (2 * r.copper_oz_outer + (n - 2) * r.copper_oz_inner) * OZ_CU_MM
    need = copper + 2 * r.mask_thickness_mm + (n - 1) * r.min_dielectric_mm
    assert r.thickness_mm >= need - 1e-9                                                  # the stackup physically fits
    assert r.thickness_mm / r.min_drill <= r.max_aspect_ratio + 1e-6                      # drilled holes stay within the aspect-ratio limit
    assert r.via_dia >= r.via_drill + 2 * r.min_annular - 1e-9


def test_a_32_layer_board_is_much_thicker_than_1_6_mm():
    r = rules_for(32, "generic")
    # copper 2*35 + 30*17.5 = 595 um ; mask 20 um ; 31 dielectrics x 100 um = 3.1 mm  -> 3.715 mm ; drill >= 0.372 mm
    assert r.thickness_mm == pytest.approx(3.715, abs=0.002) and r.min_drill >= 0.37
