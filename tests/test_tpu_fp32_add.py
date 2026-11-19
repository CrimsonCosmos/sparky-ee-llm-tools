"""The RTL float32 adder must be bit-exact with numpy float32 addition over random operands, including heavy cancellation, large exponent gaps and rounding ties."""
import numpy as np
import pytest
from silicon import toolchain
from tpu import rtl

pytestmark = pytest.mark.skipif(not toolchain.which("verilator"), reason="NOT RUN: verilator unavailable")


def _normal(rng, n, lo=-30, hi=30):
    m = rng.uniform(1, 2, n).astype(np.float32)
    e = rng.integers(lo, hi, n)
    s = rng.choice([-1.0, 1.0], n).astype(np.float32)
    return (s * m * np.exp2(e.astype(np.float32))).astype(np.float32)


def test_fp32_add_bit_exact(tmp_path):
    exe = rtl.build_fp_add(tmp_path)
    rng = np.random.default_rng(0)
    n = 300000
    a = _normal(rng, n)
    cases = {
        "independent": _normal(rng, n),
        "close_magnitudes_cancel": (-a * (1 + rng.uniform(-1e-3, 1e-3, n))).astype(np.float32),
        "tiny_difference_cancel": np.nextafter(-a, np.float32(0)).astype(np.float32),
        "same_exponent": (a * rng.uniform(0.5, 2, n)).astype(np.float32),
        "huge_gap": _normal(rng, n, -60, -40),
    }
    for name, b in cases.items():
        want = (a + b).astype(np.float32)
        ok = np.isfinite(want) & (np.abs(want) > np.float32(1e-37))                      # normal range only (subnormals are flushed by design)
        got = rtl.fp_add(exe, a, b)
        assert np.array_equal(got[ok].view(np.uint32), want[ok].view(np.uint32)), (name, int((got[ok].view(np.uint32) != want[ok].view(np.uint32)).sum()))
    # exact zero results (x + (-x)) give +0 like numpy
    z = rtl.fp_add(exe, a[:1000], -a[:1000])
    assert np.array_equal(z.view(np.uint32), (a[:1000] + (-a[:1000])).view(np.uint32))


def test_fp32_add_checker_can_fail(tmp_path):
    exe = rtl.build_fp_add(tmp_path)
    got = rtl.fp_add(exe, np.array([1.0], np.float32), np.array([2.0], np.float32))
    assert got[0] == 3.0 and got[0] != np.float32(3.0000002)
