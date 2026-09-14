"""Install optional tools: yt-dlp (pip) and a static ffmpeg build for Windows.

Usage: run this script from the repo root. It will:
 - pip install yt-dlp into the current Python environment
 - download the latest BtbN FFmpeg Windows release zip and extract to .tools/ffmpeg

The script is conservative and prints progress. It does not modify system PATH permanently.
"""
import os
import sys
import subprocess
import shutil
import requests
import zipfile
import io


def pip_install(package: str) -> bool:
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", package], check=True, timeout=600)
        return True
    except Exception as e:
        print("pip install failed:", e)
        return False


def download_and_extract_ffmpeg(dest_dir: str) -> bool:
    """Download latest Windows ffmpeg zip from BtbN releases and extract bin to dest_dir."""
    api = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
    try:
        r = requests.get(api, timeout=20)
        r.raise_for_status()
        data = r.json()
        assets = data.get("assets", [])
        candidate = None
        for a in assets:
            name = a.get("name", "")
            if "win64" in name and name.endswith(".zip"):
                candidate = a.get("browser_download_url")
                break
        if not candidate:
            # fallback: try any zip
            for a in assets:
                if a.get("name", "").endswith(".zip"):
                    candidate = a.get("browser_download_url")
                    break
        if not candidate:
            print("No suitable ffmpeg asset found in release")
            return False
        print("Downloading ffmpeg from", candidate)
        rr = requests.get(candidate, stream=True, timeout=120)
        rr.raise_for_status()
        zdata = io.BytesIO(rr.content)
        with zipfile.ZipFile(zdata) as zf:
            # extract files that look like ffmpeg executables
            members = [m for m in zf.namelist() if m.lower().endswith("ffmpeg.exe") or m.lower().endswith("ffprobe.exe") or "/bin/" in m.replace('\\', '/')]
            if not members:
                members = zf.namelist()
            os.makedirs(dest_dir, exist_ok=True)
            zf.extractall(dest_dir)
        print("ffmpeg extracted to", dest_dir)
        return True
    except Exception as e:
        print("ffmpeg download/extract failed:", e)
        return False


def main():
    root = os.getcwd()
    tools_dir = os.path.join(root, ".tools")
    ff_dir = os.path.join(tools_dir, "ffmpeg")

    print("Installing yt-dlp via pip...")
    ok = pip_install("yt-dlp")
    print("yt-dlp installed:", ok)

    if shutil.which("ffmpeg"):
        print("ffmpeg already on PATH; skipping download")
    else:
        print("Downloading static ffmpeg build into .tools/ffmpeg...")
        ok2 = download_and_extract_ffmpeg(ff_dir)
        print("ffmpeg available:", ok2)

    print("Installer finished. To use the downloaded ffmpeg, add its bin folder to PATH or point tools to:")
    print(ff_dir)


if __name__ == '__main__':
    main()
