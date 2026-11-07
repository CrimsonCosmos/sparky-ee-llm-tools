"""KiCad project files around the generated schematic and board: library tables (so footprints and symbols resolve when the project is opened
or checked by kicad-cli) and the project file."""
from __future__ import annotations

from pathlib import Path

from ..model import Circuit
from ..toolchain import kicad_has, kicad_library_dir, kicad_version


def _var(kind: str) -> str:
    v = kicad_version()
    return f"${{KICAD{v[0]}_{kind}_DIR}}" if v else f"${{KICAD_{kind}_DIR}}"


def _libs(circuit: Circuit) -> tuple[list[str], list[str]]:
    sym, fp = set(), set()
    for comp in circuit.components.values():
        s = comp.part.params.get("symbol")
        if s and ":" in s:
            sym.add(s.split(":")[0])
        lid = comp.footprint.lib_id
        if lid and ":" in lid:
            fp.add(lid.split(":")[0])
    return sorted(sym | {"power"}), sorted(fp)


def write_lib_tables(circuit: Circuit, outdir: str | Path) -> dict[str, Path]:
    """sym-lib-table / fp-lib-table naming only the libraries this design uses, by KiCad's own path variables (the standard libraries that ship
    with KiCad; the variables resolve on any machine that has KiCad installed)."""
    out = Path(outdir)
    sym, fp = _libs(circuit)
    sdir, fdir = _var("SYMBOL"), _var("FOOTPRINT")
    st = ["(sym_lib_table", "  (version 7)"] + [f'  (lib (name "{n}")(type "KiCad")(uri "{sdir}/{n}.kicad_sym")(options "")(descr ""))' for n in sym] + [")"]
    ft = ["(fp_lib_table", "  (version 7)"] + [f'  (lib (name "{n}")(type "KiCad")(uri "{fdir}/{n}.pretty")(options "")(descr ""))' for n in fp] + [")"]
    files = {"sym-lib-table": out / "sym-lib-table", "fp-lib-table": out / "fp-lib-table"}
    files["sym-lib-table"].write_text("\n".join(st) + "\n")
    files["fp-lib-table"].write_text("\n".join(ft) + "\n")
    return files
