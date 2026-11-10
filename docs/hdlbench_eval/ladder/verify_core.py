"""Independent check of the best rv32i_core against my Python ISS: fresh programs (seeds the scorer never used), different reset times, plus programs with backward branches / random JALR."""
import json, random, sys, tempfile
sys.path.insert(0, "/home/user/sparky-ee-llm-tools/src"); sys.path.insert(0, "/home/user/sparky-ee-llm-tools/tests"); sys.path.insert(0, "/home/user/sparky-ee-llm-tools/src/hdlbench/problems/rv32i_core")
from pathlib import Path
from hdlbench import problem, sim, tools
import test_hdlbench_ladder as L
import stimulus
p = problem.get("rv32i_core")
best = max((json.loads(l) for l in open("/home/user/eval/runs/ladder/rv32i_core.jsonl")), key=lambda r: (r["solved"], r["reward"]))
f = Path("/tmp/core_best.v"); f.write_text(best["code"])
tb = (p.dir / "tb.v").read_text().replace("(i == 700)", "(i == `RESET_AT)")
iv, vvp = tools.which("iverilog"), tools.which("vvp")
def run_one(words, reset_at, cycles=1200):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td); (td / "tb.v").write_text(tb); (td / "prog.hex").write_text("\n".join(f"{w:08x}" for w in words) + "\n")
        rc, t = tools.run([iv, "-g2012", f"-DCYCLES={cycles}", f"-DRESET_AT={reset_at}", "-o", str(td / "s"), str(td / "tb.v"), str(f)], td, 120); assert rc == 0, t
        tools.run([vvp, str(td / "s")], td, 120)
        return [l.split() for l in (td / "out.txt").read_text().splitlines()]
def iss(words, cycles, reset_at):
    imem = list(words); dmem = [0] * 1024; x = [0] * 32; pc = 0; tr = []
    for step in range(cycles + 1):
        instr = imem[(pc >> 2) & 255]; rs1, rs2 = x[(instr >> 15) & 31], x[(instr >> 20) & 31]
        op = instr & 0x7F; a = (rs1 + (L._sx(instr >> 20, 12) if op == 3 else 0)) & L.M
        o = L.rv32i_model(instr, pc, rs1, rs2, dmem[(a >> 2) & 1023])
        if step > 0: tr.append([pc, o["mem_addr"], o["mem_wdata"], o["mem_wstrb"]])
        rst = step == reset_at + 1
        if o["rd_we"] and ((instr >> 7) & 31) and not rst: x[(instr >> 7) & 31] = o["rd_val"]
        if o["mem_wstrb"]:
            ad, d, s = o["mem_addr"], o["mem_wdata"], o["mem_wstrb"]; w = dmem[(ad >> 2) & 1023]
            for ln in range(4):
                if s >> ln & 1: w = (w & ~(0xFF << 8 * ln)) | (d & (0xFF << 8 * ln))
            dmem[(ad >> 2) & 1023] = w
        pc = o["next_pc"]
        if rst: pc, x = 0, [0] * 32
    return tr
def compare(words, reset_at, label):
    rows = run_one(words, reset_at); exp = iss(words, 1200, reset_at); bad = 0
    for c, (r, e) in enumerate(zip(rows, exp)):
        ok = int(r[0], 16) == e[0] and int(r[3], 16) == e[3] and (e[1] is None or int(r[1], 16) == e[1]) and (e[3] == 0 or int(r[2], 16) == e[2])
        if not ok:
            bad += 1
            if bad <= 2: print("  MISMATCH", label, "cycle", c, r, e)
    return bad == 0
ok = True; n = 0
rnd = random.Random(777)
for seed in (11, 12, 13):
    for k, words in enumerate(stimulus.programs("heldout", seed, 12)):
        ok &= compare(words, rnd.choice([5, 150, 333, 700, 999, 1150]), f"seed{seed}/{k}"); n += 1
# programs with backward branches/jumps and heavy memory traffic (not like the scorer's forward-only programs)
def word2(r):
    w = stimulus.word(r)
    if (w & 0x7F) == 0x63 and r.random() < 0.5: w ^= (1 << 31)             # flip the branch sign bit: backward branches
    return w
for k in range(30):
    r = random.Random(5000 + k); words = [word2(r) for _ in range(256)]
    ok &= compare(words, r.choice([3, 400, 777]), f"backward/{k}"); n += 1
print(f"programs checked: {n}")
print("INDEPENDENT CHECK:", "PASS" if ok else "FAIL")
