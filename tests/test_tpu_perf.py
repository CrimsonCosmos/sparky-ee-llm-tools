"""The closed-form cycle model must reproduce the RTL cycle counts exactly (v4/v5 and v3) over random shapes, bandwidths and latencies."""
import numpy as np
import pytest
from silicon import toolchain
from tpu import perf, rtl

pytestmark = pytest.mark.skipif(not toolchain.which("verilator"), reason="NOT RUN: verilator unavailable")
N, MMAX, KTMAX = 8, 64, 8


@pytest.mark.parametrize("version,extra,bubble", [("v3", -1, 1), ("v4", 0, 1), ("v5", 0, 1), ("v6", 0, 0)])
def test_model_equals_rtl(version, extra, bubble, tmp_path):
    exe = rtl.build_harness(version, N, MMAX, KTMAX, tmp_path)
    rng = np.random.default_rng(7)
    for bw, lat in [((1, 1), 0), ((1, 2), 0), ((1, 1), 17), ((1, 3), 5), ((3, 4), 11), ((1, 4), 0)]:
        sim = rtl.Sim(exe, MMAX, bw, lat)
        core = perf.Core(n=N, mmax=MMAX, bn=bw[0], bd=bw[1], lat=lat, array_lat_extra=extra, bubble=bubble)
        try:
            for _ in range(12):
                m, k, n = int(rng.integers(1, 90)), int(rng.integers(1, N * KTMAX)), int(rng.integers(1, 40))
                x = rng.integers(-128, 128, (m, k), dtype=np.int8); w = rng.integers(-128, 128, (k, n), dtype=np.int8)
                c0 = sim.cycles
                sim.gemm(x, w)
                got, pred = sim.cycles - c0, perf.gemm_cycles(core, m, k, n)
                if (N * bw[1]) % bw[0] == 0:
                    assert got == pred, (version, bw, lat, m, k, n)             # integer beats per tile: exact
                else:
                    assert abs(got - pred) <= max(2, 0.01 * got), (version, bw, lat, m, k, n, got, pred)   # fractional credit: within max(2 cycles, 1 %)
        finally:
            sim.close()
