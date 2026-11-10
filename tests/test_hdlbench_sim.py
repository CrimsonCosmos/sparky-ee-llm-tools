"""hdlbench problems + simulator. The reference designs are checked against INDEPENDENT Python models (never against themselves); the SHA-256 round model is itself checked against hashlib."""
import hashlib
import struct

import pytest

from hdlbench import problem, sim, tools

pytestmark = pytest.mark.skipif(not (tools.which("yosys") and tools.which("iverilog")), reason="yosys/iverilog not available")

M = 0xFFFFFFFF


def rotr(x, n):
    return ((x >> n) | (x << (32 - n))) & M


K = [int(x, 16) for x in """428a2f98 71374491 b5c0fbcf e9b5dba5 3956c25b 59f111f1 923f82a4 ab1c5ed5 d807aa98 12835b01 243185be 550c7dc3 72be5d74 80deb1fe 9bdc06a7 c19bf174
e49b69c1 efbe4786 0fc19dc6 240ca1cc 2de92c6f 4a7484aa 5cb0a9dc 76f988da 983e5152 a831c66d b00327c8 bf597fc7 c6e00bf3 d5a79147 06ca6351 14292967
27b70a85 2e1b2138 4d2c6dfc 53380d13 650a7354 766a0abb 81c2c92e 92722c85 a2bfe8a1 a81a664b c24b8b70 c76c51a3 d192e819 d6990624 f40e3585 106aa070
19a4c116 1e376c08 2748774c 34b0bcb5 391c0cb3 4ed8aa4a 5b9cca4f 682e6ff3 748f82ee 78a5636f 84c87814 8cc70208 90befffa a4506ceb bef9a3f7 c67178f2""".split()]
H0 = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]


def sha_round(s, k, w):
    a, b, c, d, e, f, g, h = s
    s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
    ch = (e & f) ^ (~e & M & g)
    t1 = (h + s1 + ch + k + w) & M
    s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
    maj = (a & b) ^ (a & c) ^ (b & c)
    return [(t1 + (s0 + maj)) & M, a, b, c, (d + t1) & M, e, f, g]


def sha256_one_block(block):
    w = list(struct.unpack(">16I", block))
    for i in range(16, 64):
        s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)
        s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10)
        w.append((w[i - 16] + s0 + w[i - 7] + s1) & M)
    s = list(H0)
    for i in range(64):
        s = sha_round(s, K[i], w[i])
    return b"".join(struct.pack(">I", (x + y) & M) for x, y in zip(s, H0))


def test_python_sha_round_model_reproduces_hashlib():
    block = b"abc" + b"\x80" + b"\x00" * 52 + struct.pack(">Q", 24)
    assert sha256_one_block(block) == hashlib.sha256(b"abc").digest()


def _expected_comb(name, v, i):
    g = lambda n: v[n][i]
    if name == "add8":
        t = g("a") + g("b") + g("cin")
        return {"sum": t & 0xFF, "cout": t >> 8}
    if name == "popcount8":
        return {"count": bin(g("in")).count("1")}
    if name == "sort4":
        s = sorted([g("a0"), g("a1"), g("a2"), g("a3")])
        return {f"s{j}": s[j] for j in range(4)}
    if name == "mulc45":
        return {"y": g("x") * 45}
    if name == "prienc8":
        x = g("in")
        return {"valid": int(x != 0)} | ({"idx": x.bit_length() - 1} if x else {})
    if name == "sha256_round":
        r = sha_round([g(n) for n in "abcdefgh"], g("k"), g("w"))
        return {f"n{n}": r[j] for j, n in enumerate("abcdefgh")}
    raise KeyError(name)


def _seq_model(name, v, n, reset_cycles_state=None):
    """Per-vector expected outputs (sampled after the rising edge that consumes the vector); state is reset before the first vector and whenever the vector's rst_n is 0 (synchronous reset)."""
    out = []
    rst = v.get("rst_n", [1] * n)
    if name == "gray4":
        c = 0
        for i in range(n):
            if not rst[i]:
                c = 0
            elif v["en"][i]:
                c = (c + 1) & 15
            out.append({"q": c ^ (c >> 1)})
    elif name == "lfsr8":
        q = 1
        for i in range(n):
            if not rst[i]:
                q = 1
            elif v["en"][i]:
                q = ((q << 1) & 0xFF) | (((q >> 7) ^ (q >> 5) ^ (q >> 4) ^ (q >> 3)) & 1)
            out.append({"q": q})
    elif name == "seqdet":
        hist = []
        for i in range(n):
            if not rst[i]:
                hist = []
                out.append({"hit": 0})
                continue
            hit = int(len(hist) >= 3 and hist[-3:] + [v["d"][i]] == [1, 0, 1, 1])
            hist.append(v["d"][i])
            out.append({"hit": hit})
    return out


@pytest.mark.parametrize("name", ["add8", "popcount8", "sort4", "mulc45", "prienc8", "sha256_round"])
def test_comb_references_match_independent_python_models(name):
    p = problem.get(name)
    for which in ("visible", "heldout"):
        v = sim.vectors(p, which)
        rows = sim.simulate(p, p.reference_files, v)
        n = len(rows)
        assert n == len(next(iter(v.values())))
        for i, row in enumerate(rows):
            exp = _expected_comb(name, v, i)
            got = dict(zip(p.outputs, row))
            for o, val in exp.items():
                assert int(got[o], 16) == val, f"{name} {which} vector {i} output {o}"


@pytest.mark.parametrize("name", ["gray4", "lfsr8", "seqdet"])
def test_seq_references_match_independent_python_models(name):
    p = problem.get(name)
    v = sim.vectors(p, "heldout")
    rows = sim.simulate(p, p.reference_files, v)
    exp = _seq_model(name, v, len(rows))
    for i, (row, e) in enumerate(zip(rows, exp)):
        got = dict(zip(p.outputs, row))
        for o, val in e.items():
            assert int(got[o], 16) == val, f"{name} vector {i}"


def test_visible_and_heldout_sets_are_different_and_exhaustive_when_small():
    p = problem.get("popcount8")                                     # 8 input bits -> exhaustive: random visible subset, the rest held out
    a, b = sim.vectors(p, "visible")["in"], sim.vectors(p, "heldout")["in"]
    assert sorted(a + b) == list(range(256)) and not set(a) & set(b)
    assert len(a) == p.visible_vectors and len({v & 1 for v in a}) == 2 and len({v >> 7 for v in a}) == 2      # the visible subset is not confined to one half-space
    p = problem.get("add8")                                          # 17 bits -> random, different seeds, held-out has the directed corners
    va, vb = sim.vectors(p, "visible"), sim.vectors(p, "heldout")
    assert va["a"] != vb["a"][: len(va["a"])]
    assert 0xFF in vb["a"] and 0 in vb["b"]


def test_compare_x_semantics_and_failing_vector_report(tmp_path):
    p = problem.get("prienc8")
    v = {"in": [0, 1, 2, 16, 17, 128, 255, 64]}                       # explicit vectors, including 16 and the reference's don't-care case (0)
    ref = sim.simulate(p, p.reference_files, v)
    # candidate that is correct but resolves the reference's don't-care idx (in == 0) to 0: still passes
    good = tmp_path / "good.v"
    good.write_text("module prienc8(input [7:0] in, output reg [2:0] idx, output valid); assign valid = |in; integer i;"
                    " always @* begin idx = 0; for (i = 0; i < 8; i = i + 1) if (in[i]) idx = i; end endmodule")
    ok, tot, bad = sim.compare(p, v, ref, sim.simulate(p, [good], v))
    assert ok == tot and not bad
    # candidate that is off by one for one input: mismatch reported with the vector, expected and actual values
    wrong = tmp_path / "wrong.v"
    wrong.write_text("module prienc8(input [7:0] in, output reg [2:0] idx, output valid); assign valid = |in; integer i;"
                     " always @* begin idx = 0; for (i = 0; i < 8; i = i + 1) if (in[i]) idx = (in == 8'd16) ? 3 : i; end endmodule")
    ok, tot, bad = sim.compare(p, v, ref, sim.simulate(p, [wrong], v))
    assert ok == tot - 1 and bad[0].inputs["in"] == 16 and bad[0].expected["idx"] == "4" and bad[0].got["idx"] == "3"
    # candidate driving X where the reference is defined fails
    xs = tmp_path / "xs.v"
    xs.write_text("module prienc8(input [7:0] in, output [2:0] idx, output valid); assign valid = |in; assign idx = 3'bxxx; endmodule")
    ok, tot, bad = sim.compare(p, v, ref, sim.simulate(p, [xs], v))
    assert ok < tot


def test_a_candidate_that_does_not_compile_raises_with_the_tool_message(tmp_path):
    p = problem.get("add8")
    bad = tmp_path / "bad.v"
    bad.write_text("module add8(input [7:0] a, output x); assign x = ; endmodule")
    with pytest.raises(RuntimeError, match="iverilog"):
        sim.simulate(p, [bad], sim.vectors(p, "visible"))
