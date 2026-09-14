"""AI Video Factory — NLE Export v2.

Exports edit plans to professional NLE formats:
- DaVinci Resolve: .fcpxml (Final Cut XML, which Resolve imports)
- Adobe Premiere Pro: .xml sequence
- CapCut: JSON project (simplified)

This is the killer feature: let human editors finish your auto-edit.
"""
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def export_fcpxml(
    package_dir: str,
    clips: List[str],
    plan: Optional[Dict] = None,
    output_name: str = "resolve_import.fcpxml",
) -> str:
    """Export a Final Cut Pro XML that DaVinci Resolve can import.

    Args:
        package_dir: The package directory
        clips: List of clip file paths (must be absolute)
        plan: The edit plan with timing info
        output_name: Output filename
    """
    output_path = os.path.join(package_dir, output_name)

    # FCPXML structure
    root = ET.Element("fcpxml", version="1.9")
    resources = ET.SubElement(root, "resources")
    format_el = ET.SubElement(resources, "format", {
        "id": "r1",
        "name": "FFVideoFormat1080x1920p30",
        "frameDuration": "1/30s",
        "width": "1080",
        "height": "1920",
    })

    # Add each clip as an asset
    asset_ids = {}
    for i, clip_path in enumerate(clips):
        aid = f"r{i+2}"
        asset_ids[clip_path] = aid
        asset = ET.SubElement(resources, "asset", {
            "id": aid,
            "name": os.path.basename(clip_path),
            "src": f"file://{os.path.abspath(clip_path)}",
            "hasVideo": "1",
            "hasAudio": "1",
            "duration": "1s",  # Will be auto-detected by Resolve
        })

    # Library > Event > Project > Sequence
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": "AI Video Factory Export"})
    project = ET.SubElement(event, "project", {"name": "Auto Edit"})
    sequence = ET.SubElement(project, "sequence", {"duration": "30s", "format": "r1"})
    spine = ET.SubElement(sequence, "spine")

    # Add clips to timeline
    current_time = 0.0
    for i, clip_path in enumerate(clips):
        duration = 2.0  # Default, Resolve will auto-adjust
        if plan and i < len(plan.get("edit_plan", [])):
            duration = plan["edit_plan"][i].get("duration", 2.0)

        asset_ref = asset_ids.get(clip_path, "r2")
        clip_el = ET.SubElement(spine, "clip", {
            "name": os.path.basename(clip_path),
            "offset": f"{current_time}s",
            "duration": f"{duration}s",
            "start": "0s",
        })
        video = ET.SubElement(clip_el, "video")
        ET.SubElement(video, "asset-clip", {
            "ref": asset_ref,
            "duration": f"{duration}s",
        })
        current_time += duration

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def export_premiere_xml(
    package_dir: str,
    clips: List[str],
    plan: Optional[Dict] = None,
    output_name: str = "premiere_import.xml",
) -> str:
    """Export an Adobe Premiere Pro sequence XML.

    This is a simplified format. For full compatibility, use
    Adobe's official SDK or a library like `pymiere`.
    """
    output_path = os.path.join(package_dir, output_name)

    root = ET.Element("PremiereData", Version="3")
    project = ET.SubElement(root, "Project")
    seq = ET.SubElement(project, "Sequence", {"Name": "AI Video Factory Edit"})
    media = ET.SubElement(seq, "Media")
    video = ET.SubElement(media, "Video")
    track = ET.SubElement(video, "Track", {"Type": "Video", "TrackIndex": "1"})

    current_time = 0
    for i, clip_path in enumerate(clips):
        duration = 60  # frames at 30fps = 2s
        if plan and i < len(plan.get("edit_plan", [])):
            duration = int(plan["edit_plan"][i].get("duration", 2.0) * 30)

        clip_el = ET.SubElement(track, "ClipItem", {
            "Id": f"clip_{i}",
            "Name": os.path.basename(clip_path),
            "Start": str(current_time),
            "End": str(current_time + duration),
            "In": "0",
            "Out": str(duration),
        })
        file_el = ET.SubElement(clip_el, "File", {"Id": f"file_{i}"})
        path_el = ET.SubElement(file_el, "Path")
        path_el.text = os.path.abspath(clip_path)
        current_time += duration

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def export_capcut_json(
    package_dir: str,
    clips: List[str],
    plan: Optional[Dict] = None,
    output_name: str = "capcut_import.json",
) -> str:
    """Export a simplified CapCut-compatible JSON project.

    CapCut's format is proprietary and changes frequently.
    This generates a basic structure that can be imported
    or used as a reference for manual reconstruction.
    """
    output_path = os.path.join(package_dir, output_name)

    tracks = []
    current_time = 0.0
    for i, clip_path in enumerate(clips):
        duration = 2.0
        if plan and i < len(plan.get("edit_plan", [])):
            duration = plan["edit_plan"][i].get("duration", 2.0)

        tracks.append({
            "id": f"clip_{i}",
            "type": "video",
            "source": os.path.abspath(clip_path),
            "start_time": current_time,
            "duration": duration,
            "effects": plan["edit_plan"][i].get("label", "") if plan and i < len(plan.get("edit_plan", [])) else "",
        })
        current_time += duration

    project = {
        "project_name": "AI Video Factory Export",
        "created_at": datetime.now().isoformat(),
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "tracks": tracks,
        "notes": "Import clips manually in CapCut using these timings.",
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(project, f, indent=2)
    return output_path


def export_all_nle_formats(
    package_dir: str,
    clips: List[str],
    plan: Optional[Dict] = None,
) -> Dict[str, str]:
    """Export to all NLE formats at once."""
    return {
        "resolve": export_fcpxml(package_dir, clips, plan),
        "premiere": export_premiere_xml(package_dir, clips, plan),
        "capcut": export_capcut_json(package_dir, clips, plan),
    }
