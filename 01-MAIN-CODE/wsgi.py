"""Production WSGI launcher for the reorganized repository."""
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WEB_DIR = _REPO_ROOT / "02-WEB-FILES"
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

from app.wsgi import app

__all__ = ["app"]
