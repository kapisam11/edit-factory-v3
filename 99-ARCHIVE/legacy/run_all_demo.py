"""Run demo: generate thumbnails, SRT, and voiceover for a package folder.

Usage:
    python run_all_demo.py <package_dir>

If no package_dir is provided, the script picks the most recent folder under `output/`.
"""
import sys
import os
import glob

def find_latest_package(root="output"):
    paths = glob.glob(os.path.join(root, "*_*"))
    if not paths:
        return None
    paths.sort(key=os.path.getmtime, reverse=True)
    return paths[0]


def main():
    pkg = None
    if len(sys.argv) > 1:
        pkg = sys.argv[1]
    else:
        pkg = find_latest_package()

    if not pkg or not os.path.isdir(pkg):
        print("No package folder found. Create one first with cli.py")
        sys.exit(1)

    print("Using package:", pkg)

    # thumbnails
    try:
        from ai_video_factory.thumbnail import make_thumbnail_variants
        thumb_dir = os.path.join(pkg, "thumbnails")
        os.makedirs(thumb_dir, exist_ok=True)
        title_path = os.path.join(pkg, "title_options.txt")
        subject = "" 
        if os.path.exists(title_path):
            with open(title_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
                if lines:
                    subject = lines[0]
        if not subject:
            subject = os.path.basename(pkg)
        thumbs = make_thumbnail_variants(subject, thumb_dir, count=4)
        print("Thumbnails created:")
        for t in thumbs:
            print(" -", t)
    except Exception as e:
        print("Thumbnail generation failed:", e)

    # script -> srt
    try:
        from ai_video_factory.subtitle_tools import script_to_srt
        script_path = os.path.join(pkg, "script.txt")
        if os.path.exists(script_path):
            srt_out = os.path.join(pkg, "script.srt")
            script_to_srt(open(script_path, "r", encoding="utf-8").read(), srt_out)
            print("SRT written:", srt_out)
        else:
            print("No script.txt found in package")
    except Exception as e:
        print("SRT generation failed:", e)

    # TTS
    try:
        from ai_video_factory.tts import generate_voiceover
        script_path = os.path.join(pkg, "script.txt")
        if os.path.exists(script_path):
            vo_out = os.path.join(pkg, "voiceover.wav")
            text = open(script_path, "r", encoding="utf-8").read()
            generate_voiceover(text, vo_out)
            print("Voiceover saved:", vo_out)
        else:
            print("No script.txt found for TTS")
    except Exception as e:
        print("TTS generation failed:", e)


if __name__ == "__main__":
    main()
