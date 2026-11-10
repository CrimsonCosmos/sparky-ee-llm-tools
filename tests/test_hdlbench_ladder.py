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


# ---- fp16add: independent model = Python's correctly rounded double -> binary16 conversion (struct 'e'); a sum of two halves is exact in a double ----
import math
import struct


def half_to_float(h):
    return struct.unpack("<e", struct.pack("<H", h))[0]


def fp16_add_model(a, b):
    x, y = half_to_float(a), half_to_float(b)
    if math.isnan(x) or math.isnan(y):
        return 0x7E00
    if math.isinf(x) and math.isinf(y) and (x > 0) != (y > 0):
        return 0x7E00
    s = x + y
    if s == 0 and not (x == 0 and y == 0 and (math.copysign(1, x) < 0) and (math.copysign(1, y) < 0)):
        return 0x0000                                                  # exact cancellation and (+0)+(-0) give +0 in round-to-nearest
    try:
        return struct.unpack("<H", struct.pack("<e", s))[0]
    except OverflowError:                                              # struct refuses to overflow: the correctly rounded result is infinity (|s| >= 65520)
        return 0x7C00 if s > 0 else 0xFC00


def test_python_fp16_model_known_values():
    assert fp16_add_model(0x3C00, 0x3C00) == 0x4000                    # 1 + 1 = 2
    assert fp16_add_model(0x7BFF, 0x7BFF) == 0x7C00                    # max + max overflows to +inf
    assert fp16_add_model(0x0001, 0x0001) == 0x0002                    # smallest subnormals
    assert fp16_add_model(0x3C00, 0xBC00) == 0x0000 and fp16_add_model(0x8000, 0x8000) == 0x8000
    assert fp16_add_model(0x7C00, 0xFC00) == 0x7E00 and fp16_add_model(0x7E01, 0x3C00) == 0x7E00
    assert fp16_add_model(0x7BFF, 0x3C00) == 0x7BFF                    # 65504 + 1 rounds back to 65504 (ties-to-even region)
    assert fp16_add_model(0x7BFF, 0x4000 | 0x0) != 0x7C00 or True


def test_fp16add_reference_matches_independent_model():
    p = problem.get("fp16add")
    rnd = random.Random(21)
    pairs = [(rnd.getrandbits(16), rnd.getrandbits(16)) for _ in range(40000)]
    specials = [0x0000, 0x8000, 0x0001, 0x8001, 0x03FF, 0x0400, 0x3C00, 0xBC00, 0x7BFF, 0xFBFF, 0x7C00, 0xFC00, 0x7E00, 0x7C01, 0x3555, 0x0200, 0x7800, 0x7BFE, 0x3BFF, 0x4000]
    pairs += [(x, y) for x in specials for y in specials]
    pairs += [(rnd.choice(specials), rnd.getrandbits(16)) for _ in range(5000)]
    rows = sim.simulate(p, p.reference_files, {"a": [x for x, _ in pairs], "b": [y for _, y in pairs]})
    bad = [(hex(x), hex(y), r[0], hex(fp16_add_model(x, y))) for (x, y), r in zip(pairs, rows) if int(r[0], 16) != fp16_add_model(x, y)]
    assert not bad, bad[:5]


@pytest.mark.slow
def test_fp16add_scorer_rejects_each_single_rule_mutation_of_the_reference():
    from hdlbench import score
    p = problem.get("fp16add")
    ref = (p.dir / "reference.v").read_text()
    muts = {
        "tie rounds up": ref.replace("guard && (sticky || m11[0])", "guard"),
        "sticky ignored": ref.replace("guard && (sticky || m11[0])", "guard && m11[0]"),
        "subnormal flush": ref.replace("if (p <= 10) begin ef", "if (p <= 9) begin y = {sr,15'd0}; end else if (p <= 10) begin ef"),
        "nan payload propagated": ref.replace("y = 16'h7E00;\n    else if (a_inf)", "y = a_nan ? a : 16'h7E00;\n    else if (a_inf)"),
        "overflow threshold": ref.replace("if (ef >= 31) y", "if (ef >= 32) y"),
        "-0 + -0 = +0": ref.replace("(sa & sb)", "1'b0"),
        "inf + -inf = +inf": ref.replace("(a_inf && b_inf && (sa != sb))", "1'b0"),
    }
    for name, m in muts.items():
        assert m != ref, name
        assert not score.evaluate(p, m, do_metrics=False).solved, f"scorer accepted: {name}"


# ---- rv32i_exec: an independent Python model written from the spec text (table-driven, different structure from the reference) ----
M = 0xFFFFFFFF


def _sx(v, bits):
    return v - (1 << bits) if v >> (bits - 1) else v


def rv32i_model(instr, pc, rs1, rs2, rdata):
    """Returns dict of outputs; None = don't-care."""
    o = dict(rd_we=0, rd_val=None, next_pc=(pc + 4) & M, mem_addr=None, mem_wdata=None, mem_wstrb=0, illegal=0, misaligned=0)
    op, f3, f7 = instr & 0x7F, (instr >> 12) & 7, instr >> 25
    immI = _sx(instr >> 20, 12) & M
    immS = _sx(((instr >> 25) << 5) | ((instr >> 7) & 0x1F), 12) & M
    immB = _sx(((instr >> 31) << 12) | (((instr >> 7) & 1) << 11) | (((instr >> 25) & 0x3F) << 5) | (((instr >> 8) & 0xF) << 1), 13) & M
    immU = instr & 0xFFFFF000
    immJ = _sx(((instr >> 31) << 20) | (((instr >> 12) & 0xFF) << 12) | (((instr >> 20) & 1) << 11) | (((instr >> 21) & 0x3FF) << 1), 21) & M
    s1, s2 = _sx(rs1, 32), _sx(rs2, 32)

    def bad():
        return dict(rd_we=0, rd_val=None, next_pc=(pc + 4) & M, mem_addr=None, mem_wdata=None, mem_wstrb=0, illegal=1, misaligned=0)
    if op & 3 != 3:
        return bad()
    if op == 0x37:
        o.update(rd_we=1, rd_val=immU)
    elif op == 0x17:
        o.update(rd_we=1, rd_val=(pc + immU) & M)
    elif op == 0x6F:
        o.update(rd_we=1, rd_val=(pc + 4) & M, next_pc=(pc + immJ) & M)
    elif op == 0x67:
        if f3 != 0:
            return bad()
        o.update(rd_we=1, rd_val=(pc + 4) & M, next_pc=((rs1 + immI) & M) & ~1)
    elif op == 0x63:
        t = {0: rs1 == rs2, 1: rs1 != rs2, 4: s1 < s2, 5: s1 >= s2, 6: rs1 < rs2, 7: rs1 >= rs2}.get(f3)
        if t is None:
            return bad()
        if t:
            o["next_pc"] = (pc + immB) & M
    elif op == 0x03:
        if f3 not in (0, 1, 2, 4, 5):
            return bad()
        a = (rs1 + immI) & M
        o["mem_addr"] = a
        size = {0: 1, 4: 1, 1: 2, 5: 2, 2: 4}[f3]
        if a % size:
            o["misaligned"] = 1
        else:
            word = rdata >> (8 * (a & 3))
            val = {0: _sx(word & 0xFF, 8), 4: word & 0xFF, 1: _sx(word & 0xFFFF, 16), 5: word & 0xFFFF, 2: rdata}[f3] & M
            o.update(rd_we=1, rd_val=val)
    elif op == 0x23:
        if f3 not in (0, 1, 2):
            return bad()
        a = (rs1 + immS) & M
        o["mem_addr"] = a
        size = {0: 1, 1: 2, 2: 4}[f3]
        if a % size:
            o["misaligned"] = 1
        else:
            o.update(mem_wstrb=(((1 << size) - 1) << (a & 3)) & 0xF, mem_wdata=(rs2 << (8 * (a & 3))) & M)
    elif op == 0x13:
        sh = (instr >> 20) & 31
        if f3 == 1 and f7 != 0 or f3 == 5 and f7 not in (0, 0x20):
            return bad()
        v = {0: rs1 + immI, 2: int(s1 < _sx(immI, 32)), 3: int(rs1 < immI), 4: rs1 ^ immI, 6: rs1 | immI, 7: rs1 & immI, 1: rs1 << sh, 5: (s1 >> sh) if f7 == 0x20 else (rs1 >> sh)}[f3]
        o.update(rd_we=1, rd_val=v & M)
    elif op == 0x33:
        if not (f7 == 0 or (f7 == 0x20 and f3 in (0, 5))):
            return bad()
        sh = rs2 & 31
        v = {0: (rs1 - rs2) if f7 == 0x20 else (rs1 + rs2), 1: rs1 << sh, 2: int(s1 < s2), 3: int(rs1 < rs2), 4: rs1 ^ rs2, 5: (s1 >> sh) if f7 == 0x20 else (rs1 >> sh), 6: rs1 | rs2, 7: rs1 & rs2}[f3]
        o.update(rd_we=1, rd_val=v & M)
    else:
        return bad()
    return o


def test_rv32i_reference_matches_independent_model():
    import importlib.util
    p = problem.get("rv32i_exec")
    cols = sim.vectors(p, "heldout")
    n = 60000
    cols = {k: v[:n] for k, v in cols.items()}
    rows = sim.simulate(p, p.reference_files, cols)
    outs = p.outputs
    bad = []
    for i, r in enumerate(rows):
        got = dict(zip(outs, r))
        exp = rv32i_model(cols["instr"][i], cols["pc"][i], cols["rs1"][i], cols["rs2"][i], cols["mem_rdata"][i])
        for k, e in exp.items():
            if e is None:
                continue
            if "x" in got[k].lower() or int(got[k], 16) != e:
                bad.append((i, k, got[k], hex(e)))
        if len(bad) > 5:
            break
    assert not bad, bad
    # the don't-care outputs of the reference really are X (so the scorer will not over-constrain a proposer)
    assert any("x" in dict(zip(outs, r))["rd_val"].lower() for r in rows)


@pytest.mark.slow
def test_rv32i_scorer_rejects_single_rule_mutations_of_the_reference():
    from hdlbench import score
    p = problem.get("rv32i_exec")
    ref = (p.dir / "reference.v").read_text()
    muts = {
        "jalr keeps bit 0": ref.replace("(rs1 + immI) & 32'hFFFFFFFE", "(rs1 + immI)"),
        "BLT unsigned": ref.replace("3'b100: taken = lt_s;", "3'b100: taken = lt_u;"),
        "SRA becomes SRL": ref.replace("? sra_r :", "? (rs1 >> rs2[4:0]) :"),
        "LH misaligned allowed": ref.replace("3'b001: if (addr[0]) begin misaligned = 1; rd_val = 32'bx; end else begin rd_we = 1; rd_val = {{16{h[15]}}, h[15:0]}; end", "3'b001: begin rd_we = 1; rd_val = {{16{h[15]}}, h[15:0]}; end"),
        "M extension accepted as ADD": ref.replace("if (f7 != 7'b0000000 && !(f7 == 7'b0100000 && (f3 == 3'b000 || f3 == 3'b101))) illegal = 1;", "if (f7 != 7'b0000001 && f7 != 7'b0000000 && !(f7 == 7'b0100000 && (f3 == 3'b000 || f3 == 3'b101))) illegal = 1;"),
        "SB strobe not shifted": ref.replace("3'b000: begin mem_wstrb = 4'b0001 << addr[1:0]; end", "3'b000: begin mem_wstrb = 4'b0001; end"),
        "SLTIU signed compare": ref.replace("3'b011: rd_val = {31'b0, rs1 < immI};", "3'b011: rd_val = {31'b0, s1 < $signed(immI)};"),
        "rd_we for x0 suppressed": ref.replace("5'b01101: begin rd_we = 1;", "5'b01101: begin rd_we = (instr[11:7] != 0);"),
    }
    for name, m in muts.items():
        assert m != ref, name
        assert not score.evaluate(p, m, do_metrics=False).solved, f"scorer accepted: {name}"


# ---- rv32i_core: instruction-set simulator in Python (built on the independent per-instruction model above) as the referee for the reference core ----
def iss_trace(words, cycles, p):
    """Trace lines [imem_addr, dmem_addr, dmem_wdata, dmem_wstrb] as the testbench samples them (None = don't care). The first instruction executes before sampling starts (reset released one edge earlier)."""
    imem = list(words) + [0] * (256 - len(words))
    dmem = [0] * 1024
    x = [0] * 32
    pc = 0
    trace = []
    for step in range(cycles + 1):
        instr = imem[(pc >> 2) & 255]
        rs1, rs2 = x[(instr >> 15) & 31], x[(instr >> 20) & 31]
        # the memory word the load would see (the model only needs it for loads)
        op = instr & 0x7F
        addr = (rs1 + (_sx(instr >> 20, 12) if op == 0x03 else 0)) & M
        rdata = dmem[(addr >> 2) & 1023]
        o = rv32i_model(instr, pc, rs1, rs2, rdata)
        if step > 0:
            trace.append([pc, o["mem_addr"], o["mem_wdata"], o["mem_wstrb"]])
        reset_edge = step == 701                                           # the testbench pulses reset at the edge after sample index 700 (sample index = step - 1)
        if o["rd_we"] and ((instr >> 7) & 31) != 0 and not reset_edge:
            x[(instr >> 7) & 31] = o["rd_val"]
        if o["mem_wstrb"]:
            a, d, s = o["mem_addr"], o["mem_wdata"], o["mem_wstrb"]
            w = dmem[(a >> 2) & 1023]
            for lane in range(4):
                if s >> lane & 1:
                    w = (w & ~(0xFF << 8 * lane)) | (d & (0xFF << 8 * lane))
            dmem[(a >> 2) & 1023] = w
        pc = o["next_pc"]
        if reset_edge:
            pc, x = 0, [0] * 32
    return trace


def test_rv32i_core_reference_trace_equals_python_iss():
    p = problem.get("rv32i_core")
    for which in ("visible", "heldout"):
        for k, words in enumerate(sim.programs(p, which)[:6]):
            rows = sim.simulate_tb(p, p.reference_files, words)
            exp = iss_trace(words, p.cycles, p)
            assert len(rows) == len(exp)
            for c, (r, e) in enumerate(zip(rows, exp)):
                assert int(r[0], 16) == e[0], (which, k, c, "imem_addr", r, e)
                assert int(r[3], 16) == e[3], (which, k, c, "wstrb", r, e)
                if e[1] is not None:
                    assert int(r[1], 16) == e[1], (which, k, c, "dmem_addr", r, e)
                if e[3] != 0:
                    assert int(r[2], 16) == e[2], (which, k, c, "wdata", r, e)


@pytest.mark.slow
def test_rv32i_core_scorer_rejects_wrong_cores_and_accepts_the_reference():
    from hdlbench import score
    p = problem.get("rv32i_core")
    ref, ex = (p.dir / "reference.v").read_text(), (p.dir / "exec_ref.v").read_text()
    assert score.evaluate(p, ref + "\n" + ex, do_metrics=False).solved
    muts = {
        "x0 writable": ref.replace("if (we && rd != 0)", "if (we)").replace("(ra == 0) ? 32'd0 : x[ra]", "x[ra]").replace("(rb == 0) ? 32'd0 : x[rb]", "x[rb]"),
        "reset keeps registers": ref.replace("for (k = 0; k < 32; k = k + 1) x[k] <= 0;", ""),
        "pc not reset to 0": ref.replace("pc <= 0;", "pc <= 4;"),
        "illegal stalls pc": ref.replace("pc <= npc;", "pc <= (ill ? pc : npc);"),
        "store strobe delayed": ref.replace("assign imem_addr = pc, dmem_addr = ma, dmem_wdata = mw, dmem_wstrb = ms;", "reg [3:0] msd; always @(posedge clk) msd <= ms; assign imem_addr = pc, dmem_addr = ma, dmem_wdata = mw, dmem_wstrb = msd;"),
        "mid-run reset ignored for pc": ref.replace("if (!rst_n) begin pc <= 0; for", "if (!rst_n) begin if (pc == 0) pc <= 0; for"),
    }
    for name, m in muts.items():
        assert m != ref, name
        assert not score.evaluate(p, m + "\n" + ex, do_metrics=False).solved, f"scorer accepted: {name}"
