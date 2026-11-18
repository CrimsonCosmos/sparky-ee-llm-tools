"""Fusing q/k/v and gate/up projections into single GEMMs must not change a single logit (numpy integer backend, real SmolLM2 weights)."""
from pathlib import Path
import numpy as np
import pytest
from tpu import llm, quant

D = Path("/home/user/data/smollm2")
pytestmark = pytest.mark.skipif(not (D / "model.safetensors").exists(), reason="NOT RUN: SmolLM2 weights not downloaded (tools/tpu_fetch_model.py)")


@pytest.mark.parametrize("group_cols", [0, 288])
def test_fused_projections_identical(group_cols):
    ids = np.array([1, 450, 4343, 338, 278, 7483, 310, 3444])
    outs = []
    for fuse in (False, True):
        m = quant.QuantLlama(D); m.fuse = fuse; m.group_cols = group_cols; m.group_attn = True
        outs.append(m.forward(ids, m.new_cache(), 0))
    assert np.array_equal(outs[0], outs[1])
    # the check must be able to fail: a different model input does change the logits
    m = quant.QuantLlama(D); other = m.forward(ids[::-1].copy(), m.new_cache(), 0)
    assert not np.array_equal(outs[0], other)
