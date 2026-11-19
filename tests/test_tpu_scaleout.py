"""The scale-out projection composed from validated components must reproduce the MEASURED multi-chip co-simulation (reference = the json written by tools/tpu_cluster.py, not the model)."""
import json
from pathlib import Path
import pytest
from tpu import perf, scaleout

RES = Path(__file__).resolve().parents[1] / "results" / "tpu"


@pytest.mark.parametrize("fname,model,tp", [("cluster_smollm2_135m_tp3.json", "smollm2-135m", 3), ("cluster_smollm2_360m_tp5.json", "smollm2-360m", 5)])
def test_projection_matches_measured_cluster(fname, model, tp):
    f = RES / fname
    if not f.exists():
        pytest.skip("NOT RUN: measured cluster result not present")
    r = json.loads(f.read_text())
    chip = scaleout.Chip(n=16, cores=r["cores_per_chip"])
    link = scaleout.Link(bytes_per_cycle=r["link_bytes_per_cycle"])
    # decode: ctx grows from prefill+1 ... ; measured cycles/token averages the decode steps after the first token
    n_dec = r["decode_tokens"] - 1
    total = 0
    for i in range(n_dec):
        s = scaleout.decode_step(chip, link, scaleout.MODELS[model], tp, 1, 1, r["prefill"] + i + 1)
        total += s["step_cycles"]
    meas = r["cluster"]["decode_cycles_per_token"] * n_dec
    assert abs(total - meas) <= 0.01 * meas, (total, meas)
