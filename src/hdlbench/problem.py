"""A problem = directory with spec.md (what the proposer sees), reference.v (the golden design; never shown), problem.json (metadata). Ports always come from Yosys elaborating the reference."""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import tools


@dataclass
class Problem:
    name: str
    dir: Path
    top: str
    kind: str                                   # 'comb' | 'seq'
    spec_text: str
    reference_files: list[Path]
    clock: str | None = None
    reset: str | None = None
    reset_active_low: bool = True
    visible_vectors: int = 64                   # random vectors the proposer's feedback may show (test-design parameters, set per problem)
    heldout_vectors: int = 512
    visible_seed: int = 1
    heldout_seed: int = 2
    exhaustive_max_input_bits: int = 12         # combinational problems with at most this many input bits are tested exhaustively (split visible/held-out by index parity)
    reset_cycles: int = 3
    formal_depth: int = 40                      # bounded-proof depth for sequential designs when an unbounded proof is not found
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def ports(self) -> dict[str, tuple[str, int]]:
        return ports_of(self.reference_files, self.top)

    @property
    def data_inputs(self) -> dict[str, int]:
        return {n: w for n, (d, w) in self.ports.items() if d == "input" and n not in (self.clock, self.reset)}

    @property
    def outputs(self) -> list[str]:
        return sorted(n for n, (d, _) in self.ports.items() if d == "output")


def ports_of(files: list[Path], top: str) -> dict[str, tuple[str, int]]:
    yosys = tools.which("yosys")
    if not yosys:
        raise RuntimeError("yosys not found")
    with tempfile.TemporaryDirectory(prefix="hb_ports_") as td:
        out = Path(td) / "d.json"
        fl = " ".join(str(Path(p).resolve()) for p in files)
        rc, text = tools.run([yosys, "-q", "-p", f"read_verilog -sv {fl}; hierarchy -top {top}; proc; write_json {out}"])
        if not out.is_file():
            raise RuntimeError(f"Yosys could not elaborate '{top}': {text.strip()[-300:]}")
        mod = json.loads(out.read_text())["modules"][top]
    return {n: (p["direction"], len(p["bits"])) for n, p in mod["ports"].items()}


def load(path: str | Path) -> Problem:
    d = Path(path)
    meta = json.loads((d / "problem.json").read_text())
    known = {k: meta.pop(k) for k in list(meta) if k in Problem.__dataclass_fields__ and k not in ("name", "dir", "spec_text", "reference_files", "extra")}
    refs = [d / f for f in meta.pop("reference_files", ["reference.v"])]
    return Problem(name=meta.pop("name", d.name), dir=d, spec_text=(d / "spec.md").read_text(), reference_files=refs, extra=meta, **known)


def builtin_dir() -> Path:
    return Path(__file__).parent / "problems"


def list_problems(root: Path | None = None) -> list[str]:
    r = root or builtin_dir()
    return sorted(p.name for p in r.iterdir() if (p / "problem.json").is_file())


def get(name: str, root: Path | None = None) -> Problem:
    return load((root or builtin_dir()) / name)
