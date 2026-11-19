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


@pytest.mark.parametrize("version", ["v12", "v13"])
@pytest.mark.parametrize("chips,w", [(2, 16), (3, 16), (4, 16), (5, 8), (8, 16)])
def test_ring_allreduce_matches_reference_order_and_cycles(version, chips, w, tmp_path):
    exe = rtl.build_ring_ar(chips, w, 64, tmp_path, version=version)
    rng = np.random.default_rng(chips * 10 + w)
    for nbc in (1, 2, 5, 16):
        n = chips * nbc * (w // 4)
        vecs = [(rng.standard_normal(n) * rng.choice([1.0, 1e3, 1e-3], n)).astype(np.float32) for _ in range(chips)]
        res, cyc = rtl.ring_allreduce(exe, vecs, w)
        ref = rtl.ring_allreduce_reference(vecs)
        for r in res:
            assert np.array_equal(r.view(np.uint32), ref.view(np.uint32)), (chips, w, nbc)       # every chip holds the identical, order-exact result
        assert cyc == perf.ring_allreduce_cycles(chips, n * 4, w, version), (version, chips, w, nbc, cyc)
        assert np.allclose(res[0], np.sum(vecs, axis=0, dtype=np.float64), rtol=1e-5, atol=1e-3 * np.abs(vecs).max())    # and it IS the sum (independent float64 check)
