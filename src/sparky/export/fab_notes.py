"""FAB_NOTES.md: the numbers a fab's own manufacturability review starts from, gathered from what we built (stackup, minimum features, drill table, via spans, lamination
sequence). The review itself is the fab's: no open-source tool encodes a fab's process, and the fab's capability tables are theirs. This file is an RFQ attachment, not a pass."""
from __future__ import annotations

import warnings
from pathlib import Path

from ..pcb.rules import dielectric_gaps


def drill_table(gerber_dir: Path) -> list[tuple[str, float, int]]:
    """(file, hole diameter mm, hole count) for every Excellon file in the plot directory, read independently with gerbonara."""
    from gerbonara import ExcellonFile
    rows = []
    for f in sorted(gerber_dir.glob("*.drl")):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ex = ExcellonFile.open(str(f))
        sizes: dict[float, int] = {}
        for o in ex.objects:
            d = round(getattr(getattr(o, "aperture", None), "diameter", 0.0) or 0.0, 4)
            sizes[d] = sizes.get(d, 0) + 1
        rows += [(f.name, d, n) for d, n in sorted(sizes.items())]
    return rows


def render(board, gerber_dir: Path, project: str, lamination: list[str] | None = None) -> str:
    r = board.rules
    names = board.layers
    gaps = dielectric_gaps(r)
    L = [f"# Fabrication notes: {project}", "",
         "Gathered from the design for the fab's manufacturability review. **Not a verification**: the fab applies its own process limits.", "",
         f"- Board {board.width} x {board.height} mm, {len(names)} copper layers, finished thickness {r.thickness_mm:g} mm, copper {r.copper_oz_outer:g} oz outer / {r.copper_oz_inner:g} oz inner.",
         f"- Smallest features designed to: track {r.min_track:g} mm, clearance {r.clearance:g} mm, drill {r.min_drill:g} mm, annular ring {r.min_annular:g} mm, "
         f"copper-to-edge {r.edge_clearance:g} mm, copper-to-hole {r.hole_clearance:g} mm. Profile `{r.profile}`; dielectric er {r.dielectric_er:g}.", "",
         "## Stackup (top to bottom)", "", "| Layer | Dielectric below (mm) |", "|--|--|"]
    L += [f"| {n} | {gaps[i]:.4f} |" if i < len(gaps) else f"| {n} | - |" for i, n in enumerate(names)]
    if r.via_spans:
        L += ["", "## Partial-depth vias", "", "| Span | Kind | Pad (mm) | Drill (mm) | Min ring (mm) | Max depth:drill | Stackable |", "|--|--|--|--|--|--|--|"]
        L += [f"| {names[sp.first]} - {names[sp.last]} | {sp.kind} | {sp.dia:g} | {sp.drill:g} | {sp.annular:g} | {sp.max_aspect:g} | {'yes' if sp.stackable else 'no'} |" for sp in r.via_spans]
        L += ["", f"Via-in-pad (filled and capped, IPC-4761 type VII) assumed available: {'yes' if r.via_in_pad else 'no'}."]
        if lamination:
            L += ["", "Derived process order (for the fab to confirm):"] + [f"{i}. {t}" for i, t in enumerate(lamination, 1)]
    rows = drill_table(gerber_dir) if gerber_dir.is_dir() else []
    if rows:
        L += ["", "## Drill table (from the Excellon files)", "", "| File | Hole (mm) | Count |", "|--|--|--|"] + [f"| {f} | {d:g} | {n} |" for f, d, n in rows]
    L += ["", "Also supplied for the fab's tools: Gerber + Excellon (`gerber/`), IPC-2581 (`*.ipc2581.xml`), ODB++ (`test/board.odb`), IPC-D-356 e-test netlist (`test/`)."]
    return "\n".join(L) + "\n"
