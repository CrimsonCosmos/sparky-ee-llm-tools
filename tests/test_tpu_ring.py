"""The RTL ring all-gather must deliver every chip's vector to every other chip unchanged, and its cycle count must follow perf.ring_allgather_cycles exactly."""
import numpy as np
import pytest
from silicon import toolchain
from tpu import perf, rtl

pytestmark = pytest.mark.skipif(not toolchain.which("verilator"), reason="NOT RUN: verilator unavailable")


@pytest.mark.parametrize("chips,w", [(2, 16), (3, 8), (4, 16), (6, 16)])
def test_ring_allgather_exact_data_and_cycles(chips, w, tmp_path):
    exe = rtl.build_ring(chips, w, 256, tmp_path)
    rng = np.random.default_rng(chips * 100 + w)
    for nb in (1, 2, 3, 5, 12):
        vecs = [rng.integers(0, 256, nb * w, dtype=np.uint8) for _ in range(chips)]
        gathered, cyc = rtl.ring_allgather(exe, vecs, w)
        for c in range(chips):
            srcs = [s for s in range(chips) if s != c]
            for i, s in enumerate(srcs):
                assert np.array_equal(gathered[c][i], vecs[s]), (chips, w, nb, c, s)
        assert cyc == perf.ring_allgather_cycles(chips, nb * w, w), (chips, w, nb, cyc)


def test_ring_checker_can_fail(tmp_path):
    exe = rtl.build_ring(3, 8, 64, tmp_path)
    vecs = [np.arange(16, dtype=np.uint8) + 17 * c for c in range(3)]
    gathered, _ = rtl.ring_allgather(exe, vecs, 8)
    wrong = vecs[1].copy(); wrong[3] ^= 1
    assert not np.array_equal(gathered[0][0], wrong)        # chip 0 receives chip 1's vector; a one-bit change must be detected
