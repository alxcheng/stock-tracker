"""Where code and per-user data live.

An installed copy runs with STOCK_TRACKER_ROOT (the install folder) and
STOCK_TRACKER_DATA (that person's watchlist, positions and settings), so updates
can replace the code without touching anyone's data. A dev checkout keeps its
data next to the code.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

CODE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("STOCK_TRACKER_DATA") or CODE)
ROOT = Path(os.environ["STOCK_TRACKER_ROOT"]) if os.environ.get("STOCK_TRACKER_ROOT") else None
DEFAULTS = CODE / "defaults"
PORT = int(os.environ.get("STOCK_TRACKER_PORT") or 8050)

_version_file = CODE / "VERSION"
VERSION = _version_file.read_text().strip() if _version_file.exists() else "dev"


def ensure_data() -> None:
    """Create the data folder and seed a starter watchlist on first run."""
    DATA.mkdir(parents=True, exist_ok=True)
    for name in ("watchlist.json",):
        if not (DATA / name).exists() and (DEFAULTS / name).exists():
            shutil.copy(DEFAULTS / name, DATA / name)
