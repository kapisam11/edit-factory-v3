"""Re-generate visual-aware hooks for all packages and write reports.

This script iterates all `output*/*` packages, runs visual hook generation
and replaces the first script line when a visual hook is available. It then
recreates `title_options.txt` and calls `tools/hook_report.py` to create
`hook_report.json`/`.csv`.
"""
import importlib
import os
import subprocess
from pathlib import Path


def process_package(pkg: Path):
    try:
        root = str(Path(__file__).resolve().parents[1])
        if root not in __import__('sys').path:
            __import__('sys').path.insert(0, root)
        vh = importlib.import_module('tools.visual_hook_generator')
        variants_mod = importlib.import_module('tools.enforce_hook')
        hook = vh.generate_visual_hook(str(pkg))
        if hook:
            script_path = pkg / 'script.txt'
            if script_path.exists():
                txt = script_path.read_text(encoding='utf-8')
                rest = '\n'.join(txt.splitlines()[1:]).lstrip()
                script_path.write_text(hook + '\n' + rest, encoding='utf-8')
        # generate variants and title_options
        try:
            text = (pkg / 'script.txt').read_text(encoding='utf-8')
        except OSError:
            text = ''
        variants = variants_mod.generate_hook_variants(text, n=6)
        with open(pkg / 'title_options.txt', 'w', encoding='utf-8') as tf:
            tf.writelines(f"{v['hook']}\n" for v in variants)
        # run hook_report to produce report files
        subprocess.run([os.sys.executable, 'tools/hook_report.py', str(pkg)], check=False)
        return True
    except Exception:  # noqa: BLE001
        return False


def main(root='.'): 
    rootp = Path(root)
    outs = [p for p in rootp.iterdir() if p.is_dir() and p.name.startswith('output')]
    count = 0
    for o in outs:
        for child in o.iterdir():
            if child.is_dir():
                ok = process_package(child)
                if ok:
                    count += 1
                    print('Processed', child)
    print('Total packages processed:', count)
    try:
        from tools.export_ab_review import collect as export_review_assets

        export_review_assets(root)
    except Exception as e:  # noqa: BLE001
        print('Review export failed:', e)


if __name__ == '__main__':
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
