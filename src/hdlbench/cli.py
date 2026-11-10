"""Command line: python -m hdlbench <command> ... (list, tools, score, baseline, run, mutate-baseline, import-verilogeval, bitstream)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import baselines, hil, loop, problem, proposers, tools, verilogeval
from .score import RewardConfig, evaluate


def _problem(a) -> problem.Problem:
    root = Path(a.root) if getattr(a, "root", None) else None
    cand = Path(a.problem)
    return problem.load(cand) if (cand / "problem.json").is_file() else problem.get(a.problem, root)


def _proposer(a):
    kind = a.proposer
    if kind == "anthropic":
        return proposers.AnthropicProposer(model=a.model)
    if kind.startswith("command:"):
        import shlex
        return proposers.CommandProposer(shlex.split(kind[len("command:"):]))
    if kind.startswith("file:"):
        texts = [Path(x).read_text() for x in kind[len("file:"):].split(",")]
        return proposers.ScriptedProposer(texts)
    raise SystemExit(f"unknown proposer '{kind}' (anthropic | command:<cmd> | file:<a.v,b.v>)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hdlbench", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("tools")
    s = sub.add_parser("score"); s.add_argument("problem"); s.add_argument("file"); s.add_argument("--no-formal", action="store_true"); s.add_argument("--json", action="store_true"); s.add_argument("--root"); s.add_argument("--log", help="append a JSON record of this attempt (code, stages, reward, cost) to this file")
    b = sub.add_parser("baseline"); b.add_argument("problems", nargs="*"); b.add_argument("--root")
    r = sub.add_parser("run"); r.add_argument("problem"); r.add_argument("--proposer", required=True); r.add_argument("--model"); r.add_argument("--iterations", type=int, default=10)
    r.add_argument("--candidates", type=int, default=4); r.add_argument("--max-candidates", type=int, default=100); r.add_argument("--max-seconds", type=float); r.add_argument("--max-tokens", type=int)
    r.add_argument("--strategy", choices=["refine", "population"], default="refine"); r.add_argument("--stop-when-solved", action="store_true"); r.add_argument("--archive"); r.add_argument("--root")
    m = sub.add_parser("mutate-baseline"); m.add_argument("problem"); m.add_argument("--start", help="Verilog file to mutate (default: the reference)"); m.add_argument("--iterations", type=int, default=5); m.add_argument("--root")
    i = sub.add_parser("import-verilogeval"); i.add_argument("src"); i.add_argument("dest"); i.add_argument("--limit", type=int)
    cv = sub.add_parser("coverage"); cv.add_argument("problem"); cv.add_argument("--file", help="design to measure (default: the reference)"); cv.add_argument("--set", choices=["visible", "heldout"], default="heldout"); cv.add_argument("--root")
    h = sub.add_parser("bitstream"); h.add_argument("problem"); h.add_argument("file"); h.add_argument("--out", required=True); h.add_argument("--device", required=True, help="nextpnr-ice40 device, e.g. hx8k / up5k"); h.add_argument("--package", required=True)
    h.add_argument("--pcf"); h.add_argument("--load", action="store_true", help="program an attached board (needs openFPGALoader/iceprog)"); h.add_argument("--root")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for n in problem.list_problems():
            p = problem.get(n)
            print(f"{n:14s} {p.kind}")
    elif a.cmd == "tools":
        for k, v in tools.report().items():
            print(f"{k:16s} {v or 'NOT FOUND'}")
        print(f"{'programmer':16s} {hil.programmer() or 'NOT FOUND (no board loading)'}")
    elif a.cmd == "score":
        p = _problem(a)
        sc = evaluate(p, Path(a.file).read_text(), RewardConfig(), do_formal=not a.no_formal)
        if a.log:
            from .archive import Archive
            Archive(a.log).add(sum(1 for _ in open(a.log)) if Path(a.log).is_file() else 0, None, Path(a.file).read_text(), sc)
        print(json.dumps({"reward": sc.reward, "solved": sc.solved, "solved_by": sc.solved_by, "stages": {k: [v.status, v.fraction, v.detail] for k, v in sc.stages.items()}, "ratios": sc.ratios}, indent=1) if a.json else sc.feedback + f"\nFINAL: reward {sc.reward:.4f} {'SOLVED' if sc.solved else 'NOT SOLVED'}")
        return 0 if sc.solved else 1
    elif a.cmd == "baseline":
        print(baselines.markdown(baselines.reference_rows(a.problems or None, Path(a.root) if a.root else None)))
    elif a.cmd == "run":
        p = _problem(a)
        res = loop.run(p, _proposer(a), loop.Budget(a.iterations, a.max_candidates, a.max_seconds, a.max_tokens),
                       loop.LoopConfig(strategy=a.strategy, candidates_per_iteration=a.candidates, optimize=not a.stop_when_solved), a.archive)
        print(res.summary())
        if res.best:
            print(res.best.code)
        return 0 if res.solved else 1
    elif a.cmd == "coverage":
        from . import coverage
        p = _problem(a)
        c = coverage.measure(p, [Path(a.file)] if a.file else None, a.set)
        print(c.text()); [print("  never hit:", u) for u in c.uncovered]; [print("  note:", n) for n in c.notes]
    elif a.cmd == "mutate-baseline":
        p = _problem(a)
        start = Path(a.start).read_text() if a.start else "\n".join(Path(f).read_text() for f in p.reference_files)
        res = baselines.mutation_search(p, start, a.iterations)
        print(res.summary())
    elif a.cmd == "import-verilogeval":
        probs, skipped = verilogeval.import_directory(Path(a.src), Path(a.dest), a.limit)
        print(f"imported {len(probs)} problems into {a.dest}")
        for sk in skipped:
            print("skipped:", sk)
    elif a.cmd == "bitstream":
        p = _problem(a)
        sc = evaluate(p, Path(a.file).read_text(), RewardConfig(), do_metrics=False)
        if not sc.solved:
            print("refusing to build a bitstream for a design that is not solved:\n" + sc.feedback)
            return 1
        res = hil.build_bitstream([Path(a.file)], p.top, Path(a.out), "--" + a.device.lstrip("-"), a.package, Path(a.pcf) if a.pcf else None)
        print(res.stage, res.detail)
        if res.ok and a.load:
            ld = hil.load(res.bitstream)
            print(ld.stage, ld.detail)
            return 0 if ld.ok else 1
        return 0 if res.ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
