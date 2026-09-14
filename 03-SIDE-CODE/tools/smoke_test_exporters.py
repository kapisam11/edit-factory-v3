from __future__ import annotations

import os
import sys
import tempfile
import types
import xml.etree.ElementTree as ET
from pathlib import Path

# Ensure the repository root is importable when running this script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai_video_factory.nle_project as nle_project
import ai_video_factory.psd_utils as psd_utils


class _FakeImage:
    def __init__(self, label: str):
        self.label = label

    def save(self, path: str) -> None:
        Path(path).write_bytes(f"fake:{self.label}".encode("utf-8"))


class _FakeLayer:
    def __init__(self, name: str, visible: bool = True, has_image: bool = True):
        self.name = name
        self.visible = visible
        self._has_image = has_image

    def topil(self):
        return _FakeImage(self.name) if self._has_image else None


class _FakePSD:
    def descendants(self):
        return [
            _FakeLayer("Visible One"),
            _FakeLayer("Hidden Two", visible=False),
            _FakeLayer("Visible Three"),
        ]

    def composite(self):
        return _FakeImage("composite")


class _FakePSDImage:
    @staticmethod
    def open(_path: str):
        return _FakePSD()


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="aivf_smoke_", dir=os.getcwd()))
    seq_files = []
    for index in range(2):
        clip_path = temp_root / f"clip_{index}.mp4"
        clip_path.write_bytes(b"")
        seq_files.append(str(clip_path))

    original_get_duration = nle_project._get_duration
    nle_project._get_duration = lambda _path: 1.5
    try:
        fcpxml_path = nle_project.export_fcpxml(str(temp_root), seq_files)
        premiere_path = nle_project.export_premiere_xml(str(temp_root), seq_files)
    finally:
        nle_project._get_duration = original_get_duration

    assert Path(fcpxml_path).exists(), fcpxml_path
    assert Path(premiere_path).exists(), premiere_path

    fcpxml_root = ET.parse(fcpxml_path).getroot()
    premiere_root = ET.parse(premiere_path).getroot()
    assert fcpxml_root.tag == "fcpxml"
    assert premiere_root.tag == "xmeml"

    fake_module = types.ModuleType("psd_tools")
    fake_module.PSDImage = _FakePSDImage
    sys.modules["psd_tools"] = fake_module

    extracted = psd_utils.extract_layers("dummy.psd", str(temp_root / "layers"))
    composite = psd_utils.composite_psd("dummy.psd", str(temp_root / "composite.png"))

    assert len(extracted) == 2, extracted
    assert Path(composite).exists(), composite

    print("SMOKE_OK")
    print(f"fcpxml={fcpxml_path}")
    print(f"premiere={premiere_path}")
    print(f"layers={len(extracted)}")
    print(f"composite={composite}")


if __name__ == "__main__":
    main()
