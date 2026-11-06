"""Per-user data locations (cross-platform via platformdirs). SPARKY_HOME overrides everything."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    if os.environ.get("SPARKY_HOME"):
        d = Path(os.environ["SPARKY_HOME"])
    else:
        from platformdirs import user_data_dir
        d = Path(user_data_dir("sparky"))
    d.mkdir(parents=True, exist_ok=True)
    return d
