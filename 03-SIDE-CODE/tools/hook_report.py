import argparse
import json
from pathlib import Path

from typing import List

# lightweight report: read title_options.txt and assign simple scores/emotions

def score_hook(hook: str, text: str):
    lw = text.lower()
    score = 0
    emotion = 'neutral'
    if any(k in lw for k in ['betray', 'betrayal', 'betrayed']):
        emotion = 'dramatic'
    if '?' in hook or any(q in hook.lower() for q in ['why', 'who', 'what']):
        emotion = 'question'
        score += 1
    # length penalty: prefer 2-4 words
    words = hook.split()
    if 2 <= len(words) <= 4:
        score += 2
    else:
        score -= 1
    # keyword boost
    for kw, boost in [('final', 2), ('hidden', 1.5), ('shocking', 2), ('lost', 1.5), ('betray', 2)]:
        if kw in hook.lower():
            score += boost
    return {'hook': hook, 'score': score, 'emotion': emotion}


def load_context(script_path: Path) -> str:
    if not script_path.exists():
        return ''
    return script_path.read_text(encoding='utf-8')


def main(argv: List[str]):
    p = argparse.ArgumentParser()
    p.add_argument('package', help='package folder or path to script.txt')
    p.add_argument('--top', type=int, default=5, help='how many to show')
    args = p.parse_args(argv[1:])
    pkg = Path(args.package)
    if pkg.is_dir():
        script = pkg / 'script.txt'
        titles = pkg / 'title_options.txt'
    else:
        script = pkg
        titles = Path(str(pkg) + '.titles')
    context = load_context(script)
    if not titles.exists():
        print('No title_options.txt found in package. Run apply_polish first.')
        return 2
    lines = [l.strip() for l in titles.read_text(encoding='utf-8').splitlines() if l.strip()]
    scored = [score_hook(l, context) for l in lines]
    scored.sort(key=lambda x: x['score'], reverse=True)
    for s in scored[: args.top]:
        print(f"{s['hook']}  — score: {s['score']:.1f}  (emotion: {s['emotion']})")

    # write a compact JSON report into the package for downstream tooling
    try:
        report = {
            'package': str(pkg),
            'total_candidates': len(scored),
            'ranked_hooks': scored,
            'top': scored[0] if scored else None,
        }
        out_path = (pkg if pkg.is_dir() else Path(pkg).parent) / 'hook_report.json'
        out_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('Wrote hook report to', out_path)
        # also write a simple CSV for human reviewers
        try:
            csv_path = (pkg if pkg.is_dir() else Path(pkg).parent) / 'hook_report.csv'
            with open(csv_path, 'w', encoding='utf-8') as cf:
                cf.write('hook,score,emotion\n')
                for r in scored:
                    hook = r['hook'].replace('"', '""')
                    cf.write(f'"{hook}",{r["score"]},{r["emotion"]}\n')
            print('Wrote hook CSV to', csv_path)
        except Exception as e:
            print('Failed to write CSV:', e)
    except Exception as e:
        print('Failed to write hook report:', e)
    return 0


if __name__ == '__main__':
    import sys

    raise SystemExit(main(sys.argv))
