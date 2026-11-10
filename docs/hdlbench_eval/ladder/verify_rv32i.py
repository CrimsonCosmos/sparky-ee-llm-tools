import json, random, sys
sys.path.insert(0, "/home/user/sparky-ee-llm-tools/src"); sys.path.insert(0, "/home/user/sparky-ee-llm-tools/tests"); sys.path.insert(0, "/home/user/sparky-ee-llm-tools/src/hdlbench/problems/rv32i_exec")
from pathlib import Path
from hdlbench import problem, sim
import test_hdlbench_ladder as L
import stimulus
p = problem.get("rv32i_exec")
best = max((json.loads(l) for l in open("/home/user/eval/runs/ladder/rv32i_exec.jsonl")), key=lambda r: (r["solved"], r["reward"]))
f = Path("/tmp/rv_best.v"); f.write_text(best["code"])
def run(cols, label):
    n = len(cols["instr"]); rows = sim.simulate(p, [f], cols, 900); outs = p.outputs; bad = []
    for i, r in enumerate(rows):
        got = dict(zip(outs, r)); exp = L.rv32i_model(*(cols[k][i] for k in ("instr", "pc", "rs1", "rs2", "mem_rdata")))
        for k, e in exp.items():
            if e is None: continue
            if "x" in got[k].lower() or int(got[k], 16) != e: bad.append((i, k, got[k], hex(e)))
    print(f"{label}: {n} vectors, mismatches={len(bad)}", bad[:3]); return not bad
ok = True
for seed in (101, 202, 303):                       # seeds the scorer never used
    cols = stimulus.vectors("heldout", seed, 350000); ok &= run(cols, f"structured seed {seed}")
rnd = random.Random(5); n = 300000
cols = {k: [rnd.getrandbits(32) for _ in range(n)] for k in ("instr", "pc", "rs1", "rs2", "mem_rdata")}; ok &= run(cols, "fully random words")
# every (opcode, funct3, funct7-high-bit patterns) combination with random registers
rows = []
for op in range(128):
    for f3 in range(8):
        for f7 in (0, 0x20, 1, 0x7F, 0x40, 0x10):
            for _ in range(8): rows.append(((f7 << 25) | (rnd.getrandbits(5) << 20) | (rnd.getrandbits(5) << 15) | (f3 << 12) | (rnd.getrandbits(5) << 7) | op))
cols = {"instr": rows, "pc": [rnd.getrandbits(32) for _ in rows], "rs1": [stimulus.value(rnd) for _ in rows], "rs2": [stimulus.value(rnd) for _ in rows], "mem_rdata": [rnd.getrandbits(32) for _ in rows]}
ok &= run(cols, "all opcode x funct3 x funct7 patterns")
print("INDEPENDENT CHECK:", "PASS" if ok else "FAIL")
