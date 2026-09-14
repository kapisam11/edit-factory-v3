"""Example: Using the VideoDirector programmatically.

Run this after installing requirements:
    pip install -r requirements.txt
"""
import os
from ai_video_factory import VideoDirector

# Configuration
TOPIC = "Minecraft betrayal on SMP"
RAW_VIDEO = "my_gameplay.mp4"  # set to None for package-only mode
OUT_ROOT = "output"
TARGET_SECONDS = 45

# Optional API keys
GROQ_KEY = os.getenv("GROQ_API_KEY")
MODEL_KEY = os.getenv("OPENAI_API_KEY")

def main():
    print("=" * 50)
    print("AI Video Factory — Director Example")
    print("=" * 50)

    # Initialize director
    director = VideoDirector(out_root=OUT_ROOT, model_key=MODEL_KEY)

    # Run full production pipeline
    pkg = director.produce(
        topic=TOPIC,
        raw_video=RAW_VIDEO if os.path.exists(RAW_VIDEO) else None,
        use_groq=bool(GROQ_KEY),
        groq_key=GROQ_KEY,
        target_seconds=TARGET_SECONDS,
        skip_qc=False,
    )

    print("\n✅ Production complete!")
    print(f"Package: {pkg}")
    print("\nFiles generated:")
    for f in sorted(os.listdir(pkg)):
        print(f"  {f}")


if __name__ == "__main__":
    main()
