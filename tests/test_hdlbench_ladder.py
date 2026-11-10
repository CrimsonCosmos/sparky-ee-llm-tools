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


# ---- sequential multi-cycle problem: the scorer must reject near misses and accept a different correct architecture ----
def _mul_ref():
    return (problem.get("mul8s").dir / "reference.v").read_text()


MUL_ALT = """module mul8s(input clk, input rst_n, input start, input [7:0] a, input [7:0] b, output reg busy, output reg done, output reg [15:0] p);
  reg [3:0] n; reg [7:0] ma, mb; reg [15:0] r;
  wire [15:0] nr = (r << 1) + (mb[7] ? {8'b0, ma} : 16'd0);
  always @(posedge clk) begin
    if (!rst_n) begin busy <= 0; done <= 0; p <= 0; n <= 0; ma <= 0; mb <= 0; r <= 0; end
    else begin
      done <= 0;
      if (!busy) begin if (start) begin busy <= 1; n <= 0; ma <= a; mb <= b; r <= 0; end end
      else begin r <= nr; mb <= mb << 1; n <= n + 1; if (n == 4'd7) begin busy <= 0; done <= 1; p <= nr; end end
    end
  end
endmodule"""


@pytest.mark.slow
def test_mul8s_accepts_a_different_architecture_and_rejects_near_misses():
    from hdlbench import score
    p = problem.get("mul8s")
    ref = _mul_ref()
    assert score.evaluate(p, MUL_ALT).solved
    mutants = {
        "latency off by one": ref.replace("cnt == 4'd7", "cnt == 4'd6"),
        "partial product visible on p": ref.replace("if (cnt == 4'd7) begin busy <= 0; done <= 1; p <= nacc; end", "p <= nacc; if (cnt == 4'd7) begin busy <= 0; done <= 1; end"),
        "reset ignored while busy": ref.replace("if (!rst_n) begin", "if (!rst_n && !busy) begin"),
        "start accepted while busy restarts": ref.replace("      if (!busy) begin\n        if (start)", "      if (start) begin busy <= 1; cnt <= 0; acc <= 0; ra <= {8'b0, a}; rb <= b; end else if (!busy) begin\n        if (0)"),
        "rare operand pair (201,77) wrong": ref.replace("ra <= {8'b0, a}; rb <= b; end", "ra <= {8'b0, a}; rb <= b; end\n        if (start && a == 8'd201 && b == 8'd77) begin ra <= 16'd7; end"),     # random simulation alone would likely miss this; the bounded proof covers all operands
        "reset does not clear p": ref.replace("busy <= 0; done <= 0; p <= 0; cnt", "busy <= 0; done <= 0; cnt"),
    }
    for name, m in mutants.items():
        assert m != ref, name
        assert not score.evaluate(p, m).solved, f"scorer accepted the wrong design: {name}"
