"""SKiDL circuit builders. Importing this package never imports SKiDL; call `skidl()` to get it configured for the local KiCad."""
from __future__ import annotations

import os


def skidl():
    """Import SKiDL configured to read the installed KiCad's symbol libraries (any KiCad version)."""
    from ..toolchain import kicad_library_dir
    d = kicad_library_dir("symbols")
    if d:
        for v in ("", "6", "7", "8", "9", "10"):
            os.environ.setdefault(f"KICAD{v}_SYMBOL_DIR", d)
    import skidl as _s
    _s.set_default_tool(_s.KICAD9)
    return _s


def reset():
    """Start a fresh SKiDL circuit (SKiDL keeps one global default circuit)."""
    import builtins
    skidl()
    builtins.default_circuit.mini_reset()      # clears parts/nets but keeps parsed symbol libraries (reset() re-parses them: ~0.2 s per library per build)
