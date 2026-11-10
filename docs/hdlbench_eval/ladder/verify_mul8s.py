"""Independent cycle-accurate check of mul8s from the written spec (own Python model, own stimulus; not the scorer's reference, vectors or formal)."""
import json, random, sys
sys.path.insert(0, "/home/user/sparky-ee-llm-tools/src")
from pathlib import Path
from hdlbench import problem, sim
p = problem.get("mul8s")
code = [json.loads(l) for l in open("/home/user/eval/runs/ladder/mul8s.jsonl")]
best = max(code, key=lambda r: (r["solved"], r["reward"]))["code"]
f = Path("/tmp/mul8s_best.v"); f.write_text(best)

def model(cols, n):
    busy = done = 0; pv = 0; cnt = 0; ca = cb = 0; out = []
    for i in range(n):
        if not cols["rst_n"][i]:
            busy = done = 0; pv = 0
        else:
            done = 0
            if busy:
                cnt += 1
                if cnt == 8:
                    busy = 0; done = 1; pv = ca * cb
            elif cols["start"][i]:
                busy = 1; cnt = 0; ca, cb = cols["a"][i], cols["b"][i]
        out.append({"busy": busy, "done": done, "p": pv})
    return out

def check(cols, label):
    n = len(cols["a"])
    rows = sim.simulate(p, [f], cols, 600)
    exp = model(cols, n)
    bad = 0
    for i, (r, e) in enumerate(zip(rows, exp)):
        got = dict(zip(p.outputs, r))
        if any("x" in v.lower() for v in got.values()) or {k: int(v, 16) for k, v in got.items()} != e:
            bad += 1
            if bad <= 3: print("  MISMATCH", label, "cycle", i, got, e)
    print(f"{label}: {n} cycles, mismatches={bad}")
    return bad == 0

ok = True
# 1) every operand pair as its own operation (start for 1 cycle, then 11 idle cycles), reset first
n_ops = 65536
a = [0]; b = [0]; st = [0]; rs = [0]
for x in range(256):
    for y in range(256):
        a += [x] + [0] * 11; b += [y] + [0] * 11; st += [1] + [0] * 11; rs += [1] * 12
ok &= check({"a": a, "b": b, "start": st, "rst_n": rs}, "exhaustive operand pairs")
# 2) random schedules: start 50%, reset 1%, back-to-back and during busy
rnd = random.Random(2024); n = 400000
cols = {"a": [rnd.getrandbits(8) for _ in range(n)], "b": [rnd.getrandbits(8) for _ in range(n)], "start": [int(rnd.random() < 0.5) for _ in range(n)], "rst_n": [int(rnd.random() >= 0.01) for _ in range(n)]}
cols["rst_n"][0] = 0
ok &= check(cols, "random schedules with mid-run resets")
# 3) start held high, reset pulses at every phase of an operation
cols = {"a": [37] * 3000, "b": [201] * 3000, "start": [1] * 3000, "rst_n": [0 if (i % 13) == (i // 13) % 13 else 1 for i in range(3000)]}
ok &= check(cols, "start held high, reset at every phase")
print("INDEPENDENT CHECK:", "PASS" if ok else "FAIL")
