"""AI Video Factory — central asset and runtime dependency management."""
import hashlib
import json
import os
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


class AssetManager:
    """Central manager for reusable media assets."""

    def __init__(self, root_dir: str = "assets"):
        self.root = Path(root_dir)
        self._ensure_structure()

    def _ensure_structure(self):
        for subdir in ["music/dramatic", "music/emotional", "music/intense",
                       "sfx", "fonts", "overlays"]:
            (self.root / subdir).mkdir(parents=True, exist_ok=True)

    def resolve(self, uri: str) -> Optional[Path]:
        if not uri.startswith("assets://"):
            p = Path(uri)
            return p if p.exists() else None
        parts = uri.replace("assets://", "").split("/")
        path = self.root
        for part in parts:
            path = path / part
        return path if path.exists() else None

    def list_assets(self, category: str, subcategory: Optional[str] = None) -> List[Dict]:
        base = self.root / category
        if subcategory:
            base = base / subcategory
        if not base.exists():
            return []
        results = []
        for f in base.iterdir():
            if f.suffix in (".mp3", ".wav", ".ogg", ".ttf", ".otf", ".png", ".jpg", ".jpeg"):
                meta_file = f.with_suffix(".json")
                meta = {}
                if meta_file.exists():
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                results.append({
                    "name": f.name,
                    "path": str(f),
                    "uri": f"assets://{category}/{subcategory or ''}/{f.name}".replace("//", "/"),
                    "size_bytes": f.stat().st_size,
                    **meta,
                })
        return results

    def import_asset(self, source_path: str, category: str, subcategory: Optional[str] = None,
                     metadata: Optional[Dict] = None) -> str:
        src = Path(source_path)
        dest_dir = self.root / category
        if subcategory:
            dest_dir = dest_dir / subcategory
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        if metadata:
            dest.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return f"assets://{category}/{subcategory or ''}/{src.name}".replace("//", "/")

    def get_random_music(self, mood: str) -> Optional[str]:
        tracks = self.list_assets("music", mood)
        if not tracks:
            return None
        import random
        return random.choice(tracks)["uri"]

    def get_sfx(self, name: str) -> Optional[str]:
        sfx_dir = self.root / "sfx"
        for ext in (".mp3", ".wav", ".ogg"):
            candidate = sfx_dir / f"{name}{ext}"
            if candidate.exists():
                return f"assets://sfx/{candidate.name}"
        return None


@dataclass(frozen=True)
class RuntimeAssetSpec:
    name: str
    url: str
    relative_path: str
    sha256: Optional[str] = None


# Heavy model files stay external to the Python wheel. They are downloaded once,
# checked when a digest is supplied, and reused by all production runs.
RUNTIME_ASSETS = (
    RuntimeAssetSpec(
        "mobilenet_ssd_config",
        "https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt",
        ".models/mobilenet_ssd/deploy.prototxt",
    ),
    RuntimeAssetSpec(
        "mobilenet_ssd_weights",
        "https://github.com/chuanqi305/MobileNet-SSD/raw/master/mobilenet_iter_73000.caffemodel",
        ".models/mobilenet_ssd/mobilenet.caffemodel",
    ),
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def runtime_asset_path(spec: RuntimeAssetSpec, root: Optional[Path] = None) -> Path:
    return (root or _project_root()) / spec.relative_path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_runtime_asset(spec: RuntimeAssetSpec, root: Optional[Path] = None) -> bool:
    path = runtime_asset_path(spec, root)
    if not path.exists() or path.stat().st_size == 0:
        return False
    return spec.sha256 is None or _sha256(path).lower() == spec.sha256.lower()


def install_runtime_assets(*, root: Optional[Path] = None, download_missing: bool = False,
                           timeout: int = 120) -> Dict[str, Dict[str, object]]:
    """Resolve model assets and optionally download any missing files.

    No network access occurs unless ``download_missing`` is explicitly true.
    """
    root = root or _project_root()
    result: Dict[str, Dict[str, object]] = {}
    for spec in RUNTIME_ASSETS:
        path = runtime_asset_path(spec, root)
        if not verify_runtime_asset(spec, root) and download_missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            request = urllib.request.Request(spec.url, headers={"User-Agent": "edit-factory-v2"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response, tmp.open("wb") as handle:
                    shutil.copyfileobj(response, handle, length=1024 * 1024)
                tmp.replace(path)
            finally:
                if tmp.exists():
                    tmp.unlink()
        result[spec.name] = {
            "available": verify_runtime_asset(spec, root),
            "path": str(path),
            "url": spec.url,
            "verified": spec.sha256 is not None and verify_runtime_asset(spec, root),
        }
    return result


def resolve_binary(name: str) -> Optional[str]:
    env_name = "EDIT_FACTORY_" + name.upper()
    configured = os.environ.get(env_name)
    if configured and os.path.isfile(configured):
        return configured
    system = shutil.which(name)
    if system:
        return system
    bundled = _project_root() / ".tools" / "ffmpeg" / "bin" / name
    return str(bundled) if bundled.is_file() else None


__all__ = ["AssetManager", "RuntimeAssetSpec", "RUNTIME_ASSETS", "install_runtime_assets", "resolve_binary"]
