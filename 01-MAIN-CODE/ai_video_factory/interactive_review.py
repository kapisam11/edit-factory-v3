"""Interactive CLI reviewer for plan/script and edit_beats.

Supports optional local model prediction via `ollama` (if installed) or
remote model via `model_adapter`. Presents suggested rewrites and allows
the user to accept, edit, or adjust edit_plan durations/labels.
"""
import os
import json
import shutil
import subprocess
from typing import Optional

from . import story
from . import model_adapter


def _call_local_model(prompt: str, model_name: str = "llama2") -> str:
    """Try to call `ollama predict <model>` if available. Returns empty on failure."""
    if not shutil.which("ollama"):
        return ""
    try:
        proc = subprocess.run(["ollama", "predict", model_name, prompt], capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            return proc.stdout or ""
    except Exception:
        return ""
    return ""


def interactive_review(pkg_dir: str, use_model: bool = False, model_key: Optional[str] = None, prefer_local: bool = True) -> dict:
    plan_path = os.path.join(pkg_dir, "plan.json")
    if not os.path.exists(plan_path):
        raise FileNotFoundError("plan.json not found in package")

    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    script = plan.get("script", "")
    emotion = plan.get("emotion", "dramatic")

    suggestion = ""
    if use_model:
        # try local model first
        if prefer_local:
            suggestion = _call_local_model(f"Rewrite this script for a short: {script}", model_name=os.environ.get("LOCAL_MODEL_NAME", "llama2"))
        if not suggestion:
            suggestion = model_adapter.call_model(f"Rewrite this script for a short: {script}", api_key=model_key)

    if not suggestion:
        suggestion = story.rewrite_script_emotional(script, emotion)

    print("\n--- Current script ---")
    print(script)
    print("\n--- Suggested rewrite ---")
    print(suggestion)

    # accept/edit flow
    while True:
        ans = input("Accept suggested script? (y)es/(e)dit/(k)eep current: ").strip().lower()
        if ans in ("y", "yes", ""):
            plan["script"] = suggestion
            break
        if ans.startswith("k"):
            # keep current
            break
        if ans.startswith("e"):
            print("Enter new script lines. End with a single '.' on its own line.")
            lines = []
            while True:
                ln = input()
                if ln.strip() == ".":
                    break
                lines.append(ln)
            plan["script"] = "\n".join(lines)
            break

    # Edit edit_plan interactively
    edit_plan = plan.get("edit_plan", [])
    if edit_plan:
        print("\nCurrent edit plan:")
        for i, seg in enumerate(edit_plan):
            dur = seg[0]
            lbl = seg[1] if len(seg) > 1 else ""
            print(f"[{i}] {dur}s - {lbl}")

        while True:
            cmd = input("(e)dit index, (i)nsert after index, (d)elete index, (s)ave done: ").strip().lower()
            if cmd.startswith("s"):
                break
            if not cmd:
                continue
            parts = cmd.split()
            op = parts[0]
            if op == "e" and len(parts) >= 2:
                try:
                    idx = int(parts[1])
                    if 0 <= idx < len(edit_plan):
                        newdur = input(f"New duration for [{idx}] (current {edit_plan[idx][0]}): ").strip()
                        if newdur:
                            try:
                                nd = float(newdur)
                                edit_plan[idx] = (round(nd, 2), edit_plan[idx][1])
                            except Exception:
                                print("Invalid number")
                        newlbl = input(f"New label for [{idx}] (current '{edit_plan[idx][1]}'): ").strip()
                        if newlbl:
                            edit_plan[idx] = (edit_plan[idx][0], newlbl)
                except Exception:
                    print("Invalid index")
            elif op == "i" and len(parts) >= 2:
                try:
                    idx = int(parts[1])
                    nd = input("Insert duration (s): ").strip()
                    nl = input("Insert label: ").strip()
                    edit_plan.insert(min(len(edit_plan), idx+1), (float(nd), nl))
                except Exception:
                    print("Invalid input")
            elif op == "d" and len(parts) >= 2:
                try:
                    idx = int(parts[1])
                    if 0 <= idx < len(edit_plan):
                        edit_plan.pop(idx)
                except Exception:
                    print("Invalid index")
            else:
                print("Commands: e <i>, i <i>, d <i>, s")

        plan["edit_plan"] = edit_plan

    # save changes
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)

    print("Saved updated plan.json")
    return plan
