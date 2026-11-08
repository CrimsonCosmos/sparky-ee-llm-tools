"""What a designer states about a chip block: RTL, clock, PDK and floorplan intent, turned into a LibreLane configuration. No default clock period, utilisation or die size: those
are design decisions the caller makes (or leaves to LibreLane's own defaults by not setting them)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .pdk import profile


@dataclass
class SiliconSpec:
    design_name: str                              # top module
    verilog_files: list[Path]
    clock_port: str
    clock_period_ns: float
    pdk: str
    die_area_um: tuple[float, float, float, float] | None = None   # x0 y0 x1 y1; None = size from `core_utilization_pct` (or LibreLane's default)
    core_utilization_pct: float | None = None
    extra: dict = field(default_factory=dict)     # further LibreLane variables the designer sets knowingly (names as in LibreLane's reference)

    def validate(self) -> None:
        profile(self.pdk)
        missing = [str(p) for p in self.verilog_files if not Path(p).is_file()]
        if missing:
            raise FileNotFoundError(f"RTL file(s) not found: {', '.join(missing)}")
        if not self.verilog_files:
            raise ValueError("no RTL files")
        if self.clock_period_ns <= 0:
            raise ValueError("clock period must be positive")
        if self.die_area_um is not None and self.core_utilization_pct is not None:
            raise ValueError("give a die area or a core utilisation, not both")

    def to_config(self) -> dict:
        self.validate()
        cfg: dict = {"DESIGN_NAME": self.design_name, "VERILOG_FILES": [str(Path(p).resolve()) for p in self.verilog_files],
                     "CLOCK_PORT": self.clock_port, "CLOCK_PERIOD": self.clock_period_ns}
        if self.die_area_um is not None:
            cfg.update({"FP_SIZING": "absolute", "DIE_AREA": list(self.die_area_um)})
        elif self.core_utilization_pct is not None:
            cfg.update({"FP_SIZING": "relative", "FP_CORE_UTIL": self.core_utilization_pct})
        cfg.update(self.extra)
        return cfg
