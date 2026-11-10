"""Macros for hierarchical flows: a hardened block (our own flow result) or a ready-made macro from the PDK (e.g. the OpenRAM-generated SRAMs in `sky130_sram_macros`) described as a LibreLane `MACROS` entry.
Nothing here is hard-coded physics: views, sizes and corners come from the files themselves (LEF SIZE, Liberty file names), and placements are inputs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .flow import FlowResult
from .spec import SiliconSpec

_CORNER = re.compile(r"(?P<tt>tt|ss|ff)[_-]?(?P<temp>n?\d+C)[_-]?(?P<v>\d+[pv]\d+V?)", re.I)      # e.g. tt_025C_1v80
_CORNER_PVT = re.compile(r"(?P<tt>TT|SS|FF)_(?P<v>\d+p\d+)V_(?P<temp>n?\d+)C", re.I)               # e.g. TT_1p8V_25C (OpenRAM naming)


@dataclass
class MacroView:
    name: str                                   # module / cell name as instantiated by the parent RTL
    gds: list[Path]
    lef: list[Path]
    nl: list[Path] = field(default_factory=list)            # netlist or Verilog model used as the black box in the parent's synthesis
    lib: dict[str, list[Path]] = field(default_factory=dict)  # corner wildcard -> Liberty files
    spef: dict[str, list[Path]] = field(default_factory=dict)
    spice: list[Path] = field(default_factory=list)
    size_um: tuple[float, float] | None = None              # read from the LEF
    source: str = ""

    def entry(self, instances: dict[str, tuple[float, float, str]]) -> dict:
        d: dict = {"gds": [str(p) for p in self.gds], "lef": [str(p) for p in self.lef], "instances": {i: {"location": [x, y], "orientation": o} for i, (x, y, o) in instances.items()}}
        if self.nl:
            d["nl"] = [str(p) for p in self.nl]
        if self.lib:
            d["lib"] = {k: [str(p) for p in v] for k, v in self.lib.items()}
        if self.spef:
            d["spef"] = {k: [str(p) for p in v] for k, v in self.spef.items()}
        if self.spice:
            d["spice"] = [str(p) for p in self.spice]
        return d


def lef_size(lef: Path) -> tuple[float, float] | None:
    m = re.search(r"^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", lef.read_text(), re.M)
    return (float(m.group(1)), float(m.group(2))) if m else None


def lef_power_pins(lef: Path) -> tuple[list[str], list[str]]:
    """(power pins, ground pins) of a macro, from the LEF `USE POWER` / `USE GROUND` declarations."""
    power, ground, name = [], [], None
    for line in lef.read_text().splitlines():
        m = re.match(r"\s*PIN\s+(\S+)", line)
        if m:
            name = m.group(1)
        u = re.match(r"\s*USE\s+(POWER|GROUND)\s*;", line)
        if u and name:
            (power if u.group(1) == "POWER" else ground).append(name)
    return power, ground


def lef_power_pin_extent(lef: Path) -> dict[str, float]:
    """For each power/ground pin rectangle: a horizontal pin (wider than tall) is crossed by VERTICAL parent straps, so their pitch must be <= half its length; a vertical pin needs HORIZONTAL straps with
    pitch <= half its length. Returns the binding length per direction: {"v": shortest horizontal-pin length, "h": shortest vertical-pin length} (a key is absent when no pin needs that direction)."""
    out: dict[str, float] = {}
    text = lef.read_text()
    for blk in re.finditer(r"PIN\s+(\S+)(.*?)END\s+\1", text, re.S):
        if re.search(r"USE\s+(POWER|GROUND)", blk.group(2)):
            for r in re.finditer(r"RECT\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", blk.group(2)):
                x0, y0, x1, y1 = map(float, r.groups())
                w, h = x1 - x0, y1 - y0
                key, length = ("v", w) if w >= h else ("h", h)
                out[key] = min(out.get(key, length), length)
    if not out:
        raise ValueError(f"no power pin geometry in {lef}")
    return out


def from_flow(result: FlowResult, name: str | None = None) -> MacroView:
    """A macro from a finished (hardened) flow run: its GDS/LEF/netlist/Liberty/SPEF/SPICE."""
    if not result.ok:
        raise ValueError("the flow did not finish; nothing to use as a macro")
    a = result.artifacts
    name = name or result.resolved["DESIGN_NAME"]
    lib: dict[str, list[Path]] = {}
    for corner_dir in sorted(p for p in a["lib"].iterdir() if p.is_dir()):
        files = sorted(corner_dir.glob("*.lib"))
        m = _CORNER.search(corner_dir.name)
        if files and m and corner_dir.name.startswith("nom_"):
            lib[f"*_{m.group(0)}"] = files
    spef = {f"{c.name}_*": sorted(c.glob("*.spef")) for c in sorted(a["spef"].iterdir()) if c.is_dir() and list(c.glob("*.spef"))}
    return MacroView(name, [a["gds"]], [a["lef"]], [a["netlist"]], lib, spef, [a["spice"]] if "spice" in a else [], lef_size(a["lef"]), f"hardened by our flow ({result.run_dir})")


def from_pdk(name: str, pdk_root: Path, pdk: str, library: str) -> MacroView:
    """A ready-made macro from the PDK's libs.ref (LEF/GDS/Liberty/Verilog/SPICE). Liberty files are mapped to timing corners by their file names (a TT corner is required)."""
    base = None
    for cand in sorted(Path(pdk_root).rglob(f"{pdk}/libs.ref/{library}")):
        base = cand
        break
    if base is None:
        raise FileNotFoundError(f"{library} not found under {pdk_root} for {pdk}")
    def pick(sub: str, suffix: str) -> list[Path]:
        return sorted(p for p in (base / sub).glob(f"{name}*{suffix}")) if (base / sub).is_dir() else []
    lef, gds = pick("lef", ".lef"), pick("gds", ".gds")
    if not lef or not gds:
        raise FileNotFoundError(f"macro {name}: LEF/GDS not found in {base}")
    lib: dict[str, list[Path]] = {}
    for f in pick("lib", ".lib"):
        m = _CORNER_PVT.search(f.name)
        if m:                                                       # normalise to the flow's corner naming: <proc>_<temp 3 digits>C_<V>v<frac>, e.g. tt_025C_1v80
            volts = m.group("v").split("p")
            frac = (volts[1] + "0")[:2]
            lib[f"*_{m.group('tt').lower()}_{m.group('temp').zfill(3)}C_{volts[0]}v{frac}"] = [f]
            continue
        m = _CORNER.search(f.name)
        if m:
            lib[f"*_{m.group(0).lower()}"] = [f]
    if not any(k.startswith("*_tt") for k in lib):
        raise FileNotFoundError(f"macro {name}: no TT Liberty file found in {base}/lib")
    note = ""
    if len(lib) == 1:                                       # one characterised corner only: the flow analyses several, so this single view is used for ALL of them (an approximation, stated in `source`)
        lib = {"*": next(iter(lib.values()))}
        note = "; only ONE Liberty corner exists, used for every corner (timing of the macro at other corners is NOT characterised)"
    return MacroView(name, gds[:1], lef[:1], pick("verilog", ".v")[:1], lib, {}, pick("spice", ".spice")[:1], lef_size(lef[0]), f"PDK library {library}{note}")


def with_macros(spec: SiliconSpec, placements: dict[str, tuple[MacroView, dict[str, tuple[float, float, str]]]], power_net: str, ground_net: str) -> SiliconSpec:
    """Copy of `spec` whose configuration carries the macros: {module name: (view, {instance path: (x_um, y_um, orientation)})}. The parent's supply net names are inputs (they come from the PDK's standard-cell
    library, e.g. VPWR/VGND for sky130_fd_sc_hd); each macro's own supply pins are read from its LEF and wired to them through PDN_MACRO_CONNECTIONS."""
    import dataclasses
    extra = dict(spec.extra)
    extra["MACROS"] = {m.name: m.entry(inst) for m, inst in placements.values()}
    conns = []
    for m, inst in placements.values():
        power, ground = lef_power_pins(m.lef[0])
        if not power or not ground:
            raise ValueError(f"macro {m.name}: no USE POWER/GROUND pins in its LEF")
        conns += [f"{i} {power_net} {ground_net} {power[0]} {ground[0]}" for i in inst]          # LibreLane order: instance, power NET, ground NET, power PIN, ground PIN
    extra["PDN_MACRO_CONNECTIONS"] = conns
    # a parent strap must cross each macro supply pin: pitch <= half of the pin length in that direction (a derived geometric requirement, not a tuned value); user overrides in spec.extra win
    need = [lef_power_pin_extent(m.lef[0]) for m, _ in placements.values()]
    for d, var in (("h", "PDN_HPITCH"), ("v", "PDN_VPITCH")):
        lens = [n[d] for n in need if d in n]
        if lens:
            extra.setdefault(var, float(int(min(lens) / 2)))
    return dataclasses.replace(spec, extra=extra)


def relax_max_transition(view: MacroView, max_transition_ns: float, outdir: Path) -> MacroView:
    """Copy of a macro whose Liberty `max_transition` limits are raised to `max_transition_ns`. Needed when a macro's pin limit is tighter than any driver in the library can meet (OpenRAM's SRAM input pins state
    0.04 ns; the flow's repair step then stops with RSZ-0090 'best achievable transition time is X'). The value is the caller's engineering decision (take it from that message); the relaxation is recorded in `source`
    and the original Liberty is untouched. Timing sign-off that uses this macro is valid only as far as the relaxed limit is acceptable for the macro's real inputs."""
    import dataclasses
    outdir.mkdir(parents=True, exist_ok=True)
    new_lib: dict[str, list[Path]] = {}
    for k, files in view.lib.items():
        out = []
        for f in files:
            text = re.sub(r"(max_transition\s*:\s*)[\d.]+", lambda m: f"{m.group(1)}{max_transition_ns:g}", f.read_text())
            dst = outdir / f.name
            dst.write_text(text)
            out.append(dst)
        new_lib[k] = out
    return dataclasses.replace(view, lib=new_lib, source=view.source + f"; Liberty max_transition relaxed to {max_transition_ns:g} ns (see relax_max_transition)")


def prune_gds_to_top(view: MacroView, outdir: Path) -> MacroView:
    """Copy of a macro whose GDS keeps only the macro's top cell and what it references. Vendor GDS (e.g. OpenRAM's) often contains extra unreferenced top-level cells; the final merged layout then has several
    top cells and KLayout's DRC/XOR refuse it ('multiple top cells'). Uses KLayout's Python API inside the silicon tool environment; the original file is untouched."""
    import dataclasses
    import subprocess

    from . import toolchain
    outdir.mkdir(parents=True, exist_ok=True)
    code = ("import sys, klayout.db as kdb\nly = kdb.Layout(); ly.read(sys.argv[1]); keep = ly.cell(sys.argv[3])\n"
            "if keep is None: sys.exit('no cell ' + sys.argv[3])\n"
            "for c in [c for c in ly.top_cells() if c.cell_index() != keep.cell_index()]: c.prune_cell()\n"
            "ly.write(sys.argv[2]); print(len(ly.top_cells()), ly.cells())\n")
    out = []
    for g in view.gds:
        dst = outdir / g.name
        r = subprocess.run([toolchain.python(), "-c", code, str(g), str(dst), view.name], capture_output=True, text=True, env=toolchain.env())
        if r.returncode != 0 or not dst.is_file():
            raise RuntimeError(f"GDS pruning failed: {(r.stderr or r.stdout).strip()[-300:]}")
        out.append(dst)
    return dataclasses.replace(view, gds=out, source=view.source + "; GDS pruned to its top cell")


def openram_flow_extra() -> dict:
    """LibreLane settings for a parent design that contains an OpenRAM-generated SRAM macro (as shipped in the PDK), found necessary by running the flow:
      * Magic cannot read OpenRAM's GDS (unknown layers) and then emits many top cells, which breaks every later step that consumes Magic's output. Those steps are substituted out: Magic stream-out/LEF/DRC/
        spice extraction, Netgen LVS, XOR, the flow's own KLayout DRC (it takes Magic's GDS) and the render.
      * Consequence, stated plainly: for such a design the flow's own DRC and LVS do NOT run. Use `signoff.rerun_klayout_drc(result, gds=<final/klayout_gds file>)` on the KLayout-streamed GDS; LVS stays NOT RUN."""
    skip = ["KLayout.Render", "Magic.StreamOut", "Magic.WriteLEF", "Magic.DRC", "Checker.MagicDRC", "Magic.SpiceExtraction", "Netgen.LVS", "Checker.LVS", "KLayout.XOR", "Checker.XOR",
            "KLayout.DRC", "Checker.KLayoutDRC", "Odb.CheckDesignAntennaProperties"]
    return {"meta": {"version": 3, "flow": "Classic", "substituting_steps": {s: None for s in skip}}}
