import json
from pathlib import Path
import csv


def find_hook_reports(root: Path):
    for p in root.rglob('hook_report.json'):
        yield p


def summarize(root: str = '.'):
    rootp = Path(root)
    out_folder = rootp / 'reports'
    out_folder.mkdir(exist_ok=True)
    rows = []
    for rpt in find_hook_reports(rootp):
        try:
            j = json.loads(rpt.read_text(encoding='utf-8'))
        except Exception:
            continue
        pkg = j.get('package') or str(rpt.parent)
        top = j.get('top') or {}
        ranked = j.get('ranked_hooks') or []
        rows.append({
            'package': pkg,
            'top_hook': top.get('hook'),
            'top_score': top.get('score'),
            'top_emotion': top.get('emotion'),
            'total_candidates': j.get('total_candidates', len(ranked)),
            'all_hooks': json.dumps(ranked, ensure_ascii=False),
        })
    # write CSV
    csv_path = out_folder / 'hooks_summary.csv'
    with open(csv_path, 'w', encoding='utf-8', newline='') as cf:
        writer = csv.DictWriter(cf, fieldnames=['package','top_hook','top_score','top_emotion','total_candidates','all_hooks'])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    # write JSON
    json_path = out_folder / 'hooks_summary.json'
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Wrote', csv_path, 'and', json_path)
    return csv_path, json_path


if __name__ == '__main__':
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    summarize(root)
