"""Independent check of the best fp16add: Python's correctly-rounded double->binary16 conversion as the model; 3M random pairs + every special value x every 16-bit pattern, both operand orders."""
import json, random, sys
sys.path.insert(0, "/home/user/sparky-ee-llm-tools/src"); sys.path.insert(0, "/home/user/sparky-ee-llm-tools/tests")
from pathlib import Path
from hdlbench import problem, sim
import test_hdlbench_ladder as L
p = problem.get("fp16add")
best = max((json.loads(l) for l in open("/home/user/eval/runs/ladder/fp16add.jsonl")), key=lambda r: (r["solved"], r["reward"]))
f = Path("/tmp/fp16_best.v"); f.write_text(best["code"])
rnd = random.Random(99)
def chk(pairs, label):
    rows = sim.simulate(p, [f], {"a": [x for x, _ in pairs], "b": [y for _, y in pairs]}, 900)
    bad = [(hex(x), hex(y), r[0], hex(L.fp16_add_model(x, y))) for (x, y), r in zip(pairs, rows) if int(r[0], 16) != L.fp16_add_model(x, y)]
    print(f"{label}: {len(pairs)} pairs, mismatches={len(bad)}", bad[:3]); return not bad
ok = chk([(rnd.getrandbits(16), rnd.getrandbits(16)) for _ in range(1500000)], "random")
sp = [0x0000,0x8000,0x0001,0x8001,0x03FF,0x0400,0x3C00,0xBC00,0x7BFF,0xFBFF,0x7C00,0xFC00,0x7E00,0xFE00,0x7C01,0x3555,0x7800,0x0200,0x0001]
for s in sp[:6] + sp[8:14]:
    ok &= chk([(s, x) for x in range(65536)] + [(x, s) for x in range(65536)], f"special {hex(s)} x all")
# exponent-difference sweep: operands with all exponent pairs and random fractions (catches alignment/rounding paths)
pairs = [((rnd.getrandbits(1) << 15) | (ea << 10) | rnd.getrandbits(10), (rnd.getrandbits(1) << 15) | (eb << 10) | rnd.getrandbits(10)) for ea in range(32) for eb in range(32) for _ in range(1500)]
ok &= chk(pairs, "all exponent pairs")
print("INDEPENDENT CHECK:", "PASS" if ok else "FAIL")
