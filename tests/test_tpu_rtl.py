"""TPU RTL vs the exact integer reference (numpy int32 matmul computed independently of the RTL)."""
import numpy as np
import pytest
from silicon import toolchain
from tpu import rtl

pytestmark = pytest.mark.skipif(not toolchain.which("verilator"), reason="NOT RUN: verilator unavailable")
N, MMAX, KTMAX = 8, 16, 4


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    wd = tmp_path_factory.mktemp("tpu_v1")
    return wd, rtl.build("v1", N, MMAX, KTMAX, wd)


@pytest.mark.parametrize("m,k,n", [(4, 8, 8), (5, 20, 19), (16, 32, 24), (1, 8, 8), (16, 32, 8), (3, 1, 1)])
def test_random_gemm_bit_exact(sim, m, k, n):
    wd, exe = sim
    rng = np.random.default_rng(m * 1000 + k * 10 + n)
    x = rng.integers(-128, 128, (m, k), dtype=np.int8)
    w = rng.integers(-128, 128, (k, n), dtype=np.int8)
    c, _ = rtl.gemm(exe, wd, x, w, N, MMAX)
    assert np.array_equal(c, x.astype(np.int32) @ w.astype(np.int32))


def test_extreme_values_no_overflow(sim):
    wd, exe = sim
    x = np.full((4, 32), -128, np.int8); w = np.full((32, 8), -128, np.int8)
    c, _ = rtl.gemm(exe, wd, x, w, N, MMAX)
    assert (c == 32 * 128 * 128).all()


def test_checker_can_fail(sim):
    """The comparison must detect a wrong result: perturb the reference and expect a mismatch."""
    wd, exe = sim
    rng = np.random.default_rng(5)
    x = rng.integers(-128, 128, (4, 8), dtype=np.int8); w = rng.integers(-128, 128, (8, 8), dtype=np.int8)
    c, _ = rtl.gemm(exe, wd, x, w, N, MMAX)
    bad = x.astype(np.int32) @ w.astype(np.int32); bad[2, 3] += 1
    assert not np.array_equal(c, bad)
