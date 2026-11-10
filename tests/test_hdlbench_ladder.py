"""Reference designs of the harder 'ladder' problems against independent Python models (never against themselves)."""
import binascii
import random

import pytest

from hdlbench import problem, sim, tools

pytestmark = pytest.mark.skipif(not (tools.which("yosys") and tools.which("iverilog")), reason="yosys/iverilog not available")
M32 = 0xFFFFFFFF


def run(name, v):
    p = problem.get(name)
    rows = sim.simulate(p, p.reference_files, v)
    return [dict(zip(p.outputs, [int(x, 16) for x in r])) for r in rows]


def test_crc16_equals_binascii_crc_hqx():
    rnd = random.Random(1)
    v = {"crc": [rnd.getrandbits(16) for _ in range(2000)], "data": [rnd.getrandbits(8) for _ in range(2000)]}
    for o, c, d in zip(run("crc16", v), v["crc"], v["data"]):
        assert o["next"] == binascii.crc_hqx(bytes([d]), c)


def test_shift32_matches_python_semantics():
    rnd = random.Random(2)
    n = 3000
    v = {"x": [rnd.getrandbits(32) for _ in range(n)], "sh": [rnd.getrandbits(5) for _ in range(n)], "mode": [rnd.getrandbits(2) for _ in range(n)]}
    for o, x, sh, m in zip(run("shift32", v), v["x"], v["sh"], v["mode"]):
        signed = x - (1 << 32) if x >> 31 else x
        exp = [(x << sh) & M32, x >> sh, (signed >> sh) & M32, ((x << sh) | (x >> (32 - sh))) & M32][m]
        assert o["y"] == exp, (hex(x), sh, m)


def test_div8_exhaustive_against_python():
    p = problem.get("div8")
    v = {"a": [a for a in range(256) for b in range(256)], "b": [b for a in range(256) for b in range(256)]}
    for o, a, b in zip(run("div8", v), v["a"], v["b"]):
        assert (o["q"], o["r"]) == ((0xFF, a) if b == 0 else (a // b, a % b))


def aes_sbox_table():
    """Independent construction: log/antilog tables over generator 3, then the affine step - the textbook route (not the reference's square-and-multiply)."""
    exp, log, x = [0] * 256, [0] * 256, 1
    for i in range(255):
        exp[i], log[x] = x, i
        x ^= ((x << 1) ^ (0x11B if x & 0x80 else 0)) & 0xFF            # x * 3 = x ^ xtime(x)
        x &= 0xFF
    sb = []
    for a in range(256):
        b = 0 if a == 0 else exp[(255 - log[a]) % 255]
        r = b
        for k in range(1, 5):
            r ^= ((b << k) | (b >> (8 - k))) & 0xFF
        sb.append(r ^ 0x63)
    return sb


def test_python_sbox_model_matches_fips197_known_values():
    sb = aes_sbox_table()
    assert (sb[0x00], sb[0x01], sb[0x53], sb[0x10], sb[0xFF]) == (0x63, 0x7C, 0xED, 0xCA, 0x16)       # published S-box entries


def test_sbox_reference_equals_independent_table_for_all_inputs():
    sb = aes_sbox_table()
    out = run("sbox", {"x": list(range(256))})
    assert [o["y"] for o in out] == sb
