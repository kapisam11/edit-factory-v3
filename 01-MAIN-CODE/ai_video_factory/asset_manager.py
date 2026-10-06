"""AI Video Factory — central asset and runtime dependency management."""
import hashlib
import json
import os
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .production_guardrails import GuardrailError, safe_filename, validate_path_inside


class AssetManager:
    """Central manager for reusable media assets."""

    def __init__(self, root_dir: str = "assets"):
        self.root = Path(root_dir)
        self._ensure_structure()

    def _ensure_structure(self):
        for subdir in ["music/dramatic", "music/emotional", "music/intense",
                       "sfx", "fonts", "overlays"]:
            (self.root / subdir).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _component(value: str, label: str) -> str:
        component = str(value or "").strip()
        if not component or component in {".", ".."} or "/" in component or "\\" in component or ":" in component:
            raise ValueError(f"invalid asset {label}")
        return component

    def _safe_asset_path(self, *parts: str) -> Path:
        validated = [self._component(part, "path component") for part in parts]
        candidate = self.root.joinpath(*validated)
        return validate_path_inside(self.root, candidate)

    def resolve(self, uri: str) -> Optional[Path]:
        if not uri.startswith("assets://"):
            p = Path(uri)
            return p if p.exists() else None
        raw = uri[len("assets://"):].strip("/")
        parts = [part for part in raw.split("/") if part]
        if not parts:
            return None
        try:
            path = self._safe_asset_path(*parts)
        except (ValueError, GuardrailError):
            return None
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
        if not src.is_file():
            raise FileNotFoundError(src)
        category = self._component(category, "category")
        sub = self._component(subcategory, "subcategory") if subcategory else None
        dest_dir = self._safe_asset_path(category, *( [sub] if sub else [] ))
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = safe_filename(src.name, fallback="asset")
        dest = validate_path_inside(self.root, dest_dir / filename)
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            for index in range(2, 10000):
                candidate = dest.with_name(f"{stem}-{index}{suffix}")
                if not candidate.exists():
                    dest = candidate
                    break
            else:
                raise RuntimeError("unable to allocate a collision-free asset filename")
        shutil.copy2(src, dest)
        if metadata:
            dest.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        uri_parts = [category] + ([sub] if sub else []) + [dest.name]
        return "assets://" + "/".join(uri_parts)

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


def _configured_runtime_digest(name: str) -> str:
    return os.environ.get(
        "AIVF_RUNTIME_ASSET_SHA256_" + name.upper(),
        "",
    ).strip().lower()


@dataclass(frozen=True)
class RuntimeAssetSpec:
    name: str
    url: str
    relative_path: str
    sha256: str


# Heavy model files stay external to the Python wheel. They are downloaded only
# in explicit development/test mode and must always have a SHA-256 digest.
# checked when a digest is supplied, and reused by all production runs.
RUNTIME_ASSETS = (
    RuntimeAssetSpec(
        "mobilenet_ssd_config",
        "https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/bb17b6c3eef36d80be441ae8e5339be66e8e3b7a/deploy.prototxt",
        ".models/mobilenet_ssd/deploy.prototxt",
        sha256=_configured_runtime_digest("mobilenet_ssd_config"),
    ),
    RuntimeAssetSpec(
        "mobilenet_ssd_weights",
        "https://github.com/chuanqi305/MobileNet-SSD/raw/bb17b6c3eef36d80be441ae8e5339be66e8e3b7a/mobilenet_iter_73000.caffemodel",
        ".models/mobilenet_ssd/mobilenet.caffemodel",
        sha256=_configured_runtime_digest("mobilenet_ssd_weights"),
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


def _expected_sha256(spec: RuntimeAssetSpec) -> str:
    return str(spec.sha256).strip().lower()


def verify_runtime_asset(spec: RuntimeAssetSpec, root: Optional[Path] = None) -> bool:
    path = runtime_asset_path(spec, root)
    expected = _expected_sha256(spec)
    if not path.exists() or path.stat().st_size == 0 or not expected:
        return False
    return _sha256(path).lower() == expected


def _runtime_asset_download_allowed() -> bool:
    environment = os.environ.get("AIVF_ENV", "production").strip().lower()
    return os.environ.get("AIVF_ALLOW_RUNTIME_ASSET_NETWORK", "0").strip() == "1" and environment in {"development", "test"}


def install_runtime_assets(*, root: Optional[Path] = None, download_missing: bool = False,
                           timeout: int = 120) -> Dict[str, Dict[str, object]]:
    """Resolve model assets and optionally download any missing files.

    No network access occurs unless ``download_missing`` is explicitly true.
    """
    root = root or _project_root()
    result: Dict[str, Dict[str, object]] = {}
    for spec in RUNTIME_ASSETS:
        path = runtime_asset_path(spec, root)
        expected_sha256 = _expected_sha256(spec)
        if download_missing:
            if not _runtime_asset_download_allowed():
                raise GuardrailError(
                    "runtime asset downloads are disabled outside explicit development/test mode"
                )
            if not expected_sha256:
                raise GuardrailError(
                    f"runtime asset {spec.name} requires a SHA-256 digest"
                )
        if not verify_runtime_asset(spec, root) and download_missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            request = urllib.request.Request(spec.url, headers={"User-Agent": "edit-factory-v3"})
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
            "verified": bool(expected_sha256) and verify_runtime_asset(spec, root),
            "expected_sha256": expected_sha256,
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
