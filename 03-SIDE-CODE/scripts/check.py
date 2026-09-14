import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = [
    "cli_v2.py",
    "web_app_v2.py",
    "ai_video_factory/knowledge_v2.py",
]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


if __name__ == "__main__":
    run([sys.executable, "-m", "compileall", *TARGETS])
    run([sys.executable, "-m", "mypy", "--strict", "--follow-imports=silent", *TARGETS])
    print("All checks passed.")
