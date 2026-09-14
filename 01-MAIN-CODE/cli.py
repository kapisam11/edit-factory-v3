"""Repository CLI launcher.

The canonical CLI implementation lives in the web/runtime package under
``02-WEB-FILES/app``. This file keeps ``python 01-MAIN-CODE/cli.py`` and the
installed ``aivf`` command working from the reorganized repository.
"""
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT / "02-WEB-FILES", _REPO_ROOT / "03-SIDE-CODE"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
