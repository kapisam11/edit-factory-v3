"""Export project interchange formats for human polish.

Provides minimal Final Cut Pro XML (fcpxml) and Premiere XML (XMEML) exporters
that reference the rough-cut segment files so a human editor can open and
polish them quickly.
"""
import os
import xml.etree.ElementTree as ET
from typing import List
from .edit_automation import _get_duration


def _duration_frames(path: str, fps: int = 30) -> int:
    try:
        dur = _get_duration(path)
        return int(round(dur * fps))
    except Exception:
        return 0


def export_fcpxml(package_dir: str, seq_files: List[str], out_name: str = "sequence.fcpxml", fps: int = 30) -> str:
    root = ET.Element("fcpxml", version="1.8")
    resources = ET.SubElement(root, "resources")
    # add assets
    for idx, p in enumerate(seq_files, start=1):
        asset = ET.SubElement(resources, "asset", id=f"r{idx}", src=f"file://{os.path.abspath(p)}")

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event")
    project = ET.SubElement(event, "project")
    sequence = ET.SubElement(project, "sequence")
    spine = ET.SubElement(sequence, "spine")

    for idx, p in enumerate(seq_files, start=1):
        dur_frames = _duration_frames(p, fps=fps)
        clip = ET.SubElement(spine, "asset-clip", name=os.path.basename(p), ref=f"r{idx}", offset="0s", duration=f"{dur_frames}/{fps}s")

    tree = ET.ElementTree(root)
    out_path = os.path.join(package_dir, out_name)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def export_premiere_xml(package_dir: str, seq_files: List[str], out_name: str = "sequence_premiere.xml", fps: int = 30) -> str:
    # create a simple XMEML structure
    xmeml = ET.Element("xmeml", version="4")
    sequence = ET.SubElement(xmeml, "sequence")
    clips = ET.SubElement(sequence, "media")
    for p in seq_files:
        clipitem = ET.SubElement(clips, "clipitem")
        file_el = ET.SubElement(clipitem, "file")
        pathurl = ET.SubElement(file_el, "pathurl")
        pathurl.text = f"file://{os.path.abspath(p)}"
        dur = ET.SubElement(clipitem, "duration")
        dur.text = str(_duration_frames(p, fps=fps))

    tree = ET.ElementTree(xmeml)
    out_path = os.path.join(package_dir, out_name)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
