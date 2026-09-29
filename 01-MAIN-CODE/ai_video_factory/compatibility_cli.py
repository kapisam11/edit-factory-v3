"""CLI for probe-based media compatibility checks."""
from __future__ import annotations
import argparse
import json
from .compatibility_matrix import run_matrix

def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("media",nargs="+")
    args=parser.parse_args(argv)
    report=run_matrix(args.media)
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if report["failed"]==0 else 1

if __name__=="__main__":
    raise SystemExit(main())
