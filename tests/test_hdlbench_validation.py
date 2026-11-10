"""Validation of the scorer against an INDEPENDENT ground truth: for small combinational problems the exhaustive simulation of every input is the truth. For every mutant of the reference, the formal verdict
and the full scorer must agree with it - a scorer that calls a wrong mutant solved (or a right one unsolved) fails here."""
import pytest

from hdlbench import formal, mutate, problem, score, sim, tools

pytestmark = pytest.mark.skipif(not (tools.which("yosys") and tools.which("iverilog") and tools.which("sby")), reason="yosys/iverilog/sby not available")


CFG = score.RewardConfig(formal_timeout=10)


def exhaustive_equal(p, path):
    n = sum(p.data_inputs.values())
    vec_all = problem_vectors_all(p, n)
    ref = sim.simulate(p, p.reference_files, vec_all, 20)
    got = sim.simulate(p, [path], vec_all, 20)           # a mutant that loops forever times out -> treated as 'does not run'
    ok, tot, _ = sim.compare(p, vec_all, ref, got, limit=0)
    return ok == tot


def problem_vectors_all(p, n):
    cols, shift = {}, 0
    for name, w in p.data_inputs.items():
        cols[name] = [(v >> shift) & ((1 << w) - 1) for v in range(1 << n)]
        shift += w
    return cols


SHIFT_ADD = "module mulc45(input [7:0] x, output [13:0] y); assign y = (x << 5) + (x << 3) + (x << 2) + x; endmodule"      # an equivalent alternative to mutate (45 = 32+8+4+1)


@pytest.mark.parametrize("name,count,seed_text", [pytest.param("popcount8", 14, None, marks=pytest.mark.slow), ("prienc8", 14, None), ("mulc45", 6, None), ("mulc45", 8, SHIFT_ADD), pytest.param("sort4", 8, None, marks=pytest.mark.slow)])
def test_formal_and_scorer_agree_with_exhaustive_simulation_on_mutants(name, count, seed_text, tmp_path):
    p = problem.get(name)
    text = seed_text or (p.dir / "reference.v").read_text()
    muts = mutate.mutants(text, count, seed=7)
    assert len(muts) >= 3
    equal_seen = wrong_seen = unknown = 0
    for i, m in enumerate(muts):
        f = tmp_path / f"m{i}.v"
        f.write_text(m)
        try:
            truth = exhaustive_equal(p, f)
        except RuntimeError:                                        # mutant does not even compile: the scorer must say so too
            s = score.evaluate(p, m, CFG, do_formal=False, do_metrics=False)
            assert not s.solved and s.reward < 0.15
            continue
        r = formal._comb(p, p.reference_files, [f], timeout=10)          # the SAT proof itself (not the exhaustive fallback in check())
        if r.status == formal.UNKNOWN:
            unknown += 1
            assert formal.check(p, [f], timeout=10).status == (formal.EQUIVALENT if truth else formal.DIFFERENT)         # fallback must agree with the truth too
            continue
        if truth:
            equal_seen += 1
            assert r.status == formal.EQUIVALENT, f"{name} mutant {i}: truth says equal, formal says {r.status}"
            assert score.evaluate(p, m, CFG).solved
        else:
            wrong_seen += 1
            assert r.status == formal.DIFFERENT, f"{name} mutant {i}: truth says different, formal says {r.status}"
            s = score.evaluate(p, m, CFG)
            assert not s.solved and s.reward < 0.95
    assert wrong_seen >= 2, "the mutants must include genuinely wrong designs decided by SAT, or this test proves nothing"
