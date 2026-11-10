"""Independent verification of a ladder level: take the BEST logged attempt's code, re-score it in a fresh process with the default config, and check the circuit against an independent model.
usage: verify.py RUN PROBLEM"""
import json, random, sys, binascii
sys.path.insert(0, "/home/user/sparky-ee-llm-tools/src"); sys.path.insert(0, "/home/user/sparky-ee-llm-tools/tests")
from pathlib import Path
from hdlbench import problem, sim, score
import test_hdlbench_ladder as L
run, name = sys.argv[1], sys.argv[2]
log = Path(f"/home/user/eval/runs/{run}/{name}.jsonl")
rs = [json.loads(l) for l in log.open()]
claimed = max(rs, key=lambda r: (r["solved"], r["reward"]))
p = problem.get(name)
fresh = score.evaluate(p, claimed["code"])
f = Path(f"/tmp/verify_{name}.v"); f.write_text(claimed["code"])
ok = None
try:
    if name == "crc16":
        rnd = random.Random(11); v = {"crc": [rnd.getrandbits(16) for _ in range(5000)], "data": [rnd.getrandbits(8) for _ in range(5000)]}
        rows = sim.simulate(p, [f], v); ok = all(int(r[0], 16) == binascii.crc_hqx(bytes([d]), c) for r, c, d in zip(rows, v["crc"], v["data"]))
    elif name == "shift32":
        rnd = random.Random(12); n = 6000; v = {"x": [rnd.getrandbits(32) for _ in range(n)], "sh": [rnd.getrandbits(5) for _ in range(n)], "mode": [rnd.getrandbits(2) for _ in range(n)]}
        rows = sim.simulate(p, [f], v); ok = True
        for r, x, sh, m in zip(rows, v["x"], v["sh"], v["mode"]):
            s = x - (1 << 32) if x >> 31 else x
            e = [(x << sh) & L.M32, x >> sh, (s >> sh) & L.M32, ((x << sh) | (x >> (32 - sh))) & L.M32][m]
            ok &= int(r[0], 16) == e
    elif name == "div8":
        v = {"a": [a for a in range(256) for b in range(256)], "b": [b for a in range(256) for b in range(256)]}
        rows = sim.simulate(p, [f], v, 120); outs = p.outputs; ok = True
        for r, a, b in zip(rows, v["a"], v["b"]):
            d = dict(zip(outs, [int(x, 16) for x in r])); ok &= (d["q"], d["r"]) == ((255, a) if b == 0 else (a // b, a % b))
    elif name == "sbox":
        sb = L.aes_sbox_table(); rows = sim.simulate(p, [f], {"x": list(range(256))}); ok = [int(r[0], 16) for r in rows] == sb
except Exception as e:
    ok = f"error {e}"
m = fresh.metrics
print(f"{name}: attempts={len(rs)} claimed best reward={claimed['reward']:.4f} | FRESH rescore reward={fresh.reward:.4f} solved={fresh.solved} by={fresh.solved_by} | independent check={ok} | luts={m.luts if m else None} depth={m.depth if m else None} other={m.other if m else None}")
print("   solved attempts:", sum(r["solved"] for r in rs), " warnings:", fresh.warnings)
