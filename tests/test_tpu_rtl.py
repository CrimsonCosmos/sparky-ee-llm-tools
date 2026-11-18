"""TPU RTL vs the exact integer reference (numpy int32 matmul computed independently of the RTL)."""
import numpy as np
import pytest
from silicon import toolchain
from tpu import rtl

pytestmark = pytest.mark.skipif(not toolchain.which("verilator"), reason="NOT RUN: verilator unavailable")
N, MMAX, KTMAX = 8, 16, 4


VERSIONS = ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v9"]


@pytest.fixture(scope="module", params=VERSIONS)
def sim(request, tmp_path_factory):
    wd = tmp_path_factory.mktemp("tpu_" + request.param)
    return wd, rtl.build(request.param, N, MMAX, KTMAX, wd)


@pytest.fixture(scope="module", params=VERSIONS)
def hsim(request, tmp_path_factory):
    wd = tmp_path_factory.mktemp("tpuh_" + request.param)
    return rtl.build_harness(request.param, N, MMAX, KTMAX, wd)


@pytest.mark.parametrize("m,k,n", [(4, 8, 8), (5, 20, 19), (16, 32, 24), (1, 8, 8), (16, 32, 8), (3, 1, 1)])
def test_random_gemm_bit_exact(sim, m, k, n):
    wd, exe = sim
    if "tpu_v1" not in str(wd):
        pytest.skip("SV testbench lays tiles out in v1 (reverse-row) order; later versions are tested through the C++ harness")
    rng = np.random.default_rng(m * 1000 + k * 10 + n)
    x = rng.integers(-128, 128, (m, k), dtype=np.int8)
    w = rng.integers(-128, 128, (k, n), dtype=np.int8)
    c, _ = rtl.gemm(exe, wd, x, w, N, MMAX)
    assert np.array_equal(c, x.astype(np.int32) @ w.astype(np.int32))


def test_extreme_values_no_overflow(sim):
    wd, exe = sim
    if "tpu_v1" not in str(wd):
        pytest.skip("see above")
    x = np.full((4, 32), -128, np.int8); w = np.full((32, 8), -128, np.int8)
    c, _ = rtl.gemm(exe, wd, x, w, N, MMAX)
    assert (c == 32 * 128 * 128).all()


def test_checker_can_fail(sim):
    """The comparison must detect a wrong result: perturb the reference and expect a mismatch."""
    wd, exe = sim
    if "tpu_v1" not in str(wd):
        pytest.skip("see above")
    rng = np.random.default_rng(5)
    x = rng.integers(-128, 128, (4, 8), dtype=np.int8); w = rng.integers(-128, 128, (8, 8), dtype=np.int8)
    c, _ = rtl.gemm(exe, wd, x, w, N, MMAX)
    bad = x.astype(np.int32) @ w.astype(np.int32); bad[2, 3] += 1
    assert not np.array_equal(c, bad)


@pytest.mark.parametrize("bw,lat", [((1, 1), 0), ((1, 2), 3), ((1, 3), 20), ((3, 4), 7)])
def test_harness_random_shapes_throttled_memory(hsim, bw, lat):
    """Random shapes (edge tiles, M above/below MMAX chunking) with throttled, delayed memory: the shadow-load/swap hazards depend on the timing."""
    s = rtl.Sim(hsim, MMAX, bw, lat)
    rng = np.random.default_rng(100 * bw[0] + bw[1] + lat)
    try:
        for _ in range(25):
            m, k, n = int(rng.integers(1, 40)), int(rng.integers(1, N * KTMAX)), int(rng.integers(1, 40))
            x = rng.integers(-128, 128, (m, k), dtype=np.int8)
            w = rng.integers(-128, 128, (k, n), dtype=np.int8)
            assert np.array_equal(s.gemm(x, w), x.astype(np.int32) @ w.astype(np.int32)), (m, k, n)
    finally:
        s.close()


def test_scaling_n32_v6(tmp_path):
    """The design is parametric in N: bit-exact at N=32 (1024 MACs) with throttled memory, and the cycle model still matches."""
    from tpu import perf
    exe = rtl.build_harness("v6", 32, 64, 4, tmp_path)
    rng = np.random.default_rng(32)
    for bw, lat in [((1, 1), 0), ((1, 2), 9)]:
        s = rtl.Sim(exe, 64, bw, lat)
        core = perf.Core(n=32, mmax=64, bn=bw[0], bd=bw[1], lat=lat, bubble=0)
        try:
            for _ in range(6):
                m, k, n = int(rng.integers(1, 70)), int(rng.integers(1, 32 * 4)), int(rng.integers(1, 100))
                x = rng.integers(-128, 128, (m, k), dtype=np.int8); w = rng.integers(-128, 128, (k, n), dtype=np.int8)
                c0 = s.cycles
                assert np.array_equal(s.gemm(x, w), x.astype(np.int32) @ w.astype(np.int32)), (m, k, n)
                assert s.cycles - c0 == perf.gemm_cycles(core, m, k, n), (bw, lat, m, k, n)
        finally:
            s.close()


@pytest.mark.parametrize("version,cores", [("v8", 2), ("v8", 4), ("v9", 3)])
def test_multi_core_bit_exact_and_model(version, cores, tmp_path):
    """K parallel arrays (v8 tpu_multi): bit-exact against numpy and cycle-exact against the closed-form model."""
    from tpu import perf
    exe = rtl.build_multi(version, N, MMAX, KTMAX, cores, tmp_path)
    rng = np.random.default_rng(cores)
    for bw, lat in [((1, 1), 0), ((1, 2), 13)]:
        s = rtl.Sim(exe, MMAX, bw, lat)
        core = perf.Core(n=N, mmax=MMAX, bn=bw[0], bd=bw[1], lat=lat, bubble=0)
        try:
            for _ in range(10):
                m, k, n = int(rng.integers(1, 70)), int(rng.integers(1, N * KTMAX)), int(rng.integers(1, 90))
                x = rng.integers(-128, 128, (m, k), dtype=np.int8); w = rng.integers(-128, 128, (k, n), dtype=np.int8)
                c0 = s.cycles
                assert np.array_equal(s.gemm(x, w), x.astype(np.int32) @ w.astype(np.int32)), (m, k, n)
                got, pred = s.cycles - c0, perf.gemm_cycles(core, m, k, n, cores)
                assert abs(got - pred) <= 1 + (m // MMAX), (cores, bw, lat, m, k, n, got, pred)       # +-1 cycle: multi-core done-pulse alignment
        finally:
            s.close()


@pytest.mark.parametrize("gk", [1, 2, 3, 5])
def test_k_group_partial_sums_v9(gk, tmp_path):
    """v9: with K-groups the core returns one exact int32 partial sum per group of gk K-tiles; their sum is the full GEMM, each equals numpy's partial matmul."""
    exe = rtl.build_harness("v9", N, MMAX, KTMAX, tmp_path)
    rng = np.random.default_rng(gk)
    s = rtl.Sim(exe, MMAX, (1, 1), 0, n=N)
    try:
        for _ in range(10):
            m, k, n = int(rng.integers(1, 70)), int(rng.integers(1, N * KTMAX)), int(rng.integers(1, 40))
            x = rng.integers(-128, 128, (m, k), dtype=np.int8); w = rng.integers(-128, 128, (k, n), dtype=np.int8)
            out = s.gemm(x, w, group_tiles=gk)
            kt = -(-k // N)
            assert out.shape[0] == -(-kt // gk)
            for g in range(out.shape[0]):
                k0, k1 = g * gk * N, min(k, (g + 1) * gk * N)
                assert np.array_equal(out[g], x[:, k0:k1].astype(np.int32) @ w[k0:k1].astype(np.int32)), (m, k, n, g)
            assert np.array_equal(out.sum(axis=0), x.astype(np.int32) @ w.astype(np.int32))
    finally:
        s.close()


def test_multi_core_k_groups_v9(tmp_path):
    """v9 tpu_multi: K-group partial sums are exact per group on every core."""
    exe = rtl.build_multi("v9", N, MMAX, KTMAX, 2, tmp_path)
    rng = np.random.default_rng(99)
    s = rtl.Sim(exe, MMAX, (1, 1), 0, n=N)
    try:
        for _ in range(8):
            m, k, n = int(rng.integers(1, 70)), int(rng.integers(N, N * KTMAX)), int(rng.integers(1, 90))
            x = rng.integers(-128, 128, (m, k), dtype=np.int8); w = rng.integers(-128, 128, (k, n), dtype=np.int8)
            out = s.gemm(x, w, group_tiles=2)
            for g in range(out.shape[0]):
                k0, k1 = g * 2 * N, min(k, (g + 1) * 2 * N)
                assert np.array_equal(out[g], x[:, k0:k1].astype(np.int32) @ w[k0:k1].astype(np.int32)), (m, k, n, g)
    finally:
        s.close()
