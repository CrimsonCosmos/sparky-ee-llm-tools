"""Sources of candidate designs. The loop only needs `propose(prompt, n) -> list[str]`; usage (tokens) is tracked where the source reports it so budgets can be enforced."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Protocol


class Proposer(Protocol):
    usage: dict

    def propose(self, prompt: str, n: int) -> list[str]: ...


_FENCE = re.compile(r"```(?:verilog|systemverilog|sv|v)?\s*\n(.*?)```", re.S | re.I)


def extract_verilog(text: str) -> str:
    """The Verilog in a model reply: the last fenced block that contains a module, else the whole text if it contains one, else ''."""
    blocks = [b for b in _FENCE.findall(text) if re.search(r"\bmodule\b", b)]
    if blocks:
        return blocks[-1].strip() + "\n"
    return text.strip() + "\n" if re.search(r"\bmodule\b", text) else ""


@dataclass
class ScriptedProposer:
    """Deterministic source for tests and replay: a list of designs (cycled) or a function (prompt, n) -> list[str]."""
    source: list[str] | Callable[[str, int], list[str]]
    usage: dict = field(default_factory=lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0})
    prompts: list[str] = field(default_factory=list)
    _i: int = 0

    def propose(self, prompt: str, n: int) -> list[str]:
        self.usage["calls"] += 1
        self.prompts.append(prompt)
        if callable(self.source):
            return self.source(prompt, n)
        out = []
        for _ in range(n):
            out.append(self.source[min(self._i, len(self.source) - 1)])
            self._i += 1
        return out


@dataclass
class CommandProposer:
    """Runs an external command per candidate: the prompt goes to its stdin, the reply (text containing Verilog) comes from stdout. Lets any CLI/model be used without code changes."""
    cmd: list[str]
    timeout: int = 600
    usage: dict = field(default_factory=lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0})

    def propose(self, prompt: str, n: int) -> list[str]:
        out = []
        for _ in range(n):
            self.usage["calls"] += 1
            r = subprocess.run(self.cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout)
            out.append(extract_verilog(r.stdout))
        return out


@dataclass
class AnthropicProposer:
    """Anthropic Messages API (optional dependency `anthropic`). The model id has no default: pass it or set HDLBENCH_MODEL. Needs ANTHROPIC_API_KEY. Token usage is accumulated for the loop's budget."""
    model: str | None = None
    max_tokens: int = 4096
    temperature: float = 1.0
    usage: dict = field(default_factory=lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0})

    def propose(self, prompt: str, n: int) -> list[str]:
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("the 'anthropic' package is not installed (pip install anthropic): LLM proposer NOT RUN") from e
        model = self.model or os.environ.get("HDLBENCH_MODEL")
        if not model:
            raise RuntimeError("no model id: pass model=... or set HDLBENCH_MODEL")
        client = anthropic.Anthropic()
        out = []
        for _ in range(n):
            r = client.messages.create(model=model, max_tokens=self.max_tokens, temperature=self.temperature, messages=[{"role": "user", "content": prompt}])
            self.usage["calls"] += 1
            self.usage["input_tokens"] += r.usage.input_tokens
            self.usage["output_tokens"] += r.usage.output_tokens
            out.append(extract_verilog("".join(getattr(b, "text", "") for b in r.content)))
        return out
