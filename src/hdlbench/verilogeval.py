"""Import problems in the VerilogEval layout (NVlabs/verilog-eval `dataset_spec-to-rtl`): `<id>_prompt.txt` (spec text naming `TopModule`), `<id>_ref.sv` (golden `RefModule`), `<id>_test.sv`.
We re-use the prompt and the golden design (renamed to TopModule) and score with OUR pipeline (held-out vectors, formal equivalence), not with their testbench. The upstream testbench is not used.

Choices: a port named `clk` is the clock; every other input (including reset) is an ordinary random input - the reference's X outputs before it is reset are don't-care in our comparison.
Sequential imports have formal checking OFF (their initial state is unspecified) and are judged by tests only, which `Score.solved_by` states."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .problem import Problem, load, ports_of


def import_problem(prompt: Path, ref: Path, dest_root: Path) -> Problem:
    pid = prompt.name.removesuffix("_prompt.txt")
    d = Path(dest_root) / pid
    d.mkdir(parents=True, exist_ok=True)
    ref_text = ref.read_text()
    if "module RefModule" not in ref_text:
        raise ValueError(f"{ref.name}: no 'module RefModule' (not a VerilogEval reference)")
    (d / "reference.v").write_text(ref_text.replace("RefModule", "TopModule"))
    (d / "spec.md").write_text(prompt.read_text())
    ports = ports_of([d / "reference.v"], "TopModule")
    seq = "clk" in ports and ports["clk"][0] == "input"
    meta = {"top": "TopModule", "kind": "seq" if seq else "comb", "source": "verilog-eval", "imported_from": ref.name}
    if seq:
        meta.update({"clock": "clk", "formal_mode": "off", "visible_vectors": 64, "heldout_vectors": 512, "reset_cycles": 0})
    (d / "problem.json").write_text(json.dumps(meta, indent=1))
    return load(d)


def import_directory(src: Path, dest_root: Path, limit: int | None = None) -> tuple[list[Problem], list[str]]:
    """Import every `<id>_prompt.txt` with a matching `<id>_ref.sv`. Returns (problems, skipped with reasons)."""
    out, skipped = [], []
    for prompt in sorted(Path(src).glob("*_prompt.txt")):
        ref = prompt.with_name(prompt.name.replace("_prompt.txt", "_ref.sv"))
        if not ref.is_file():
            skipped.append(f"{prompt.name}: no matching _ref.sv")
            continue
        try:
            out.append(import_problem(prompt, ref, dest_root))
        except Exception as e:
            skipped.append(f"{prompt.name}: {e}")
        if limit and len(out) >= limit:
            break
    return out, skipped
