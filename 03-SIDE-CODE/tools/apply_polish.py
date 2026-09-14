"""Run polish steps on a package: enforce strong hook and run thresholds dry-run."""
import subprocess
import sys
from pathlib import Path


def run_cmd(args):
    print(">", " ".join(args))
    res = subprocess.run(args, capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print(res.stderr)
    return res.returncode


def main(pkg_path: str):
    pkg = Path(pkg_path)
    if not pkg.exists():
        print("Package not found:", pkg)
        return 2

    # 1) enforce strong hook and replace if weak
    print("1) Enforcing strong hook (strict + replace)")
    rc = run_cmd(
        [sys.executable, "tools/enforce_hook.py", str(pkg / "script.txt"), "--strict", "--replace"]
    )
    if rc != 0:
        print("Hook enforcement returned", rc)

    # 1b) generate A/B hook variants and save to title_options.txt
    print("\n1b) Generating A/B hook variants")
    try:
        import importlib
        root = str(Path(__file__).resolve().parents[1])
        if root not in sys.path:
            sys.path.insert(0, root)
        eh = importlib.import_module("tools.enforce_hook")
        script_text = (pkg / "script.txt").read_text(encoding="utf-8")
        variants = eh.generate_hook_variants(script_text, n=6)
        out_path = pkg / "title_options.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            for v in variants:
                f.write(v["hook"] + "\n")
        print("Wrote", len(variants), "hook variants to", out_path)
    except Exception as e:
        print("Hook variant generation failed:", e)

    # 2) thresholds dry-run: default
    print("\n2) Running thresholds dry-run (default)")
    run_cmd([sys.executable, "tools/thresholds_dry_run.py", str(pkg)])

    # 3) thresholds dry-run: lenient
    print("\n3) Running thresholds dry-run (lenient)")
    run_cmd(
        [
            sys.executable,
            "tools/thresholds_dry_run.py",
            str(pkg),
            "--min-motion",
            "0.01",
            "--motion-thresholds",
            '{"thumbnail": 0.005, "clip": 0.01}',
        ]
    )

    print("\nPolish complete. See visuals_pruned_preview.json files in package folder for previews.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/apply_polish.py <package_dir>")
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1]))
