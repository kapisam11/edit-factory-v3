"""Run a host-specific FFmpeg hardware capability smoke test."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .hardware import choose_encoder, ffmpeg_preset_for, hardware_capability_report


def encode_smoke(output: str | Path, duration: float = 2.0) -> dict[str, Any]:
    target=Path(output)
    target.parent.mkdir(parents=True,exist_ok=True)
    encoder=choose_encoder()
    preset=ffmpeg_preset_for(encoder)
    command=["ffmpeg","-y","-f","lavfi","-i",f"testsrc2=size=640x360:rate=30:duration={float(duration):.2f}",
             "-an","-c:v",encoder]
    if "preset" in preset: command += ["-preset",preset["preset"]]
    if "crf" in preset: command += ["-crf",preset["crf"]]
    if "bitrate" in preset: command += ["-b:v",preset["bitrate"]]
    command.append(str(target))
    result=subprocess.run(command,capture_output=True,text=True,timeout=max(30,int(duration*20)),check=False)
    return {
        "encoder":encoder,
        "success":result.returncode==0 and target.is_file() and target.stat().st_size>0,
        "returncode":result.returncode,
        "stderr":(result.stderr or "")[-2000:],
        "output":str(target),
    }


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--encode",action="store_true")
    parser.add_argument("--output",default=os.path.join(tempfile.gettempdir(),"aivf-hardware-smoke.mp4"))
    args=parser.parse_args(argv)
    report=hardware_capability_report()
    if args.encode:
        report["encode_smoke"]=encode_smoke(args.output)
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if not args.encode or report["encode_smoke"]["success"] else 1


if __name__=="__main__":
    raise SystemExit(main())

__all__=["encode_smoke","main"]
