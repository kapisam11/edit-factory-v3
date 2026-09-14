import csv
import json
from html import escape
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


def _safe_uri(path: Optional[Path]) -> str:
    if not path:
        return ''
    try:
        return path.resolve().as_uri()
    except Exception:
        return str(path)


def _load_report(report_path: Path) -> Dict:
    try:
        return json.loads(report_path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _find_thumbnail(pkg: Path) -> Optional[Path]:
    candidates = [pkg / 'thumbnail.png', pkg / 'thumbnail_vertical.png']
    thumbs = pkg / 'thumbnails'
    if thumbs.exists():
        candidates.extend(sorted(thumbs.glob('*.png')))
        candidates.extend(sorted(thumbs.glob('*.jpg')))
        candidates.extend(sorted(thumbs.glob('*.jpeg')))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _package_records(rootp: Path) -> Iterable[Dict]:
    for report_path in rootp.rglob('hook_report.json'):
        pkg = report_path.parent
        report = _load_report(report_path)
        top = report.get('top') or {}
        thumbnail = _find_thumbnail(pkg)
        yield {
            'package_path': pkg,
            'package_name': pkg.name,
            'package_uri': _safe_uri(pkg),
            'report_path': report_path,
            'report_uri': _safe_uri(report_path),
            'report': report,
            'top_hook': top.get('hook', ''),
            'top_score': top.get('score', ''),
            'top_emotion': top.get('emotion', ''),
            'thumbnail_path': thumbnail,
            'thumbnail_uri': _safe_uri(thumbnail),
            'ranked_hooks': report.get('ranked_hooks') or [],
        }


def _write_csv(path: Path, rows: Iterable[Dict], fieldnames):
    with open(path, 'w', encoding='utf-8', newline='') as cf:
        writer = csv.DictWriter(cf, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _sort_score(record: Dict):
    score = record.get('top_score')
    return score if isinstance(score, (int, float)) else -1


def collect(root='.') -> Tuple[Path, Path, Path]:
    rootp = Path(root)
    out_dir = rootp / 'reports'
    out_dir.mkdir(exist_ok=True)

    package_records = list(_package_records(rootp))

    package_rows = []
    candidate_rows = []
    for record in package_records:
        package_rows.append({
            'package': str(record['package_path']),
            'package_uri': record['package_uri'],
            'top_hook': record['top_hook'],
            'top_score': record['top_score'],
            'top_emotion': record['top_emotion'],
            'thumbnail': record['thumbnail_uri'],
            'report': record['report_uri'],
        })

        vertical_thumb = record['package_path'] / 'thumbnail_vertical.png'
        vertical_uri = _safe_uri(vertical_thumb if vertical_thumb.exists() else None)
        for idx, hook in enumerate(record['ranked_hooks'], start=1):
            candidate_rows.append({
                'package': str(record['package_path']),
                'package_uri': record['package_uri'],
                'package_name': record['package_name'],
                'rank': idx,
                'hook': hook.get('hook', ''),
                'score': hook.get('score', ''),
                'emotion': hook.get('emotion', ''),
                'thumbnail': record['thumbnail_uri'],
                'thumbnail_vertical': vertical_uri,
                'report': record['report_uri'],
            })

    summary_csv = out_dir / 'ab_review.csv'
    _write_csv(summary_csv, package_rows, ['package', 'package_uri', 'top_hook', 'top_score', 'top_emotion', 'thumbnail', 'report'])

    candidates_csv = out_dir / 'ab_review_candidates.csv'
    _write_csv(candidates_csv, candidate_rows, ['package', 'package_uri', 'package_name', 'rank', 'hook', 'score', 'emotion', 'thumbnail', 'thumbnail_vertical', 'report'])

    dashboard_path = out_dir / 'reviewer_dashboard.html'
    dashboard_parts = [
        '<!doctype html>',
        '<html lang="en">',
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>AI Clip Factory Reviewer Dashboard</title>',
        '<style>',
        'body{margin:0;font-family:Segoe UI,Arial,sans-serif;background:#0f1115;color:#f4f6fb;}',
        'header{padding:28px 24px 18px;border-bottom:1px solid #23283a;background:linear-gradient(135deg,#161b26,#0f1115);position:sticky;top:0;z-index:2;}',
        'h1{margin:0 0 8px;font-size:28px;}',
        '.meta{color:#98a2b3;font-size:14px;}',
        '.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:18px;padding:20px;}',
        '.card{background:#151922;border:1px solid #262c3b;border-radius:16px;overflow:hidden;box-shadow:0 14px 32px rgba(0,0,0,.28);}',
        '.thumb{width:100%;aspect-ratio:16/9;object-fit:cover;background:#0b0d12;display:block;}',
        '.body{padding:16px;}',
        '.title{font-size:16px;font-weight:700;line-height:1.3;margin:0 0 8px;}',
        '.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;color:#d7dbe7;font-size:13px;}',
        '.score{padding:4px 8px;border-radius:999px;background:#22304a;color:#dbeafe;white-space:nowrap;}',
        '.hooks{margin:14px 0 0;padding:0;list-style:none;display:grid;gap:8px;}',
        '.hooks li{padding:10px 12px;border-radius:10px;background:#10141d;border:1px solid #22283a;}',
        '.hooks .rank{color:#8aa2d3;font-size:12px;margin-bottom:2px;}',
        '.links{display:flex;flex-wrap:wrap;gap:10px;margin-top:14px;}',
        '.links a{color:#9fd3ff;text-decoration:none;background:#111827;border:1px solid #23314a;border-radius:999px;padding:7px 10px;font-size:12px;}',
        '.links a:hover{text-decoration:underline;}',
        '</style>',
        '</head>',
        '<body>',
        '<header>',
        '<h1>AI Clip Factory Reviewer Dashboard</h1>',
        f'<div class="meta">{len(package_rows)} packages ready for human review and A/B testing.</div>',
        '</header>',
        '<main class="grid">',
    ]

    for record in sorted(package_records, key=_sort_score, reverse=True):
        thumbnail = record['thumbnail_uri']
        hooks_html = []
        for idx, hook in enumerate((record['ranked_hooks'] or [])[:6], start=1):
            hooks_html.append(
                '<li>'
                f'<div class="rank">#{idx} · {escape(str(hook.get("score", "")))} · {escape(str(hook.get("emotion", "")))}</div>'
                f'<div>{escape(str(hook.get("hook", "")))}</div>'
                '</li>'
            )

        dashboard_parts.extend([
            '<section class="card">',
            f'<img class="thumb" src="{thumbnail}" alt="{escape(record["package_name"])} thumbnail">' if thumbnail else '<div class="thumb"></div>',
            '<div class="body">',
            f'<div class="row"><div><div class="title">{escape(record["package_name"])} </div><div>{escape(record["top_hook"] or "No hook found")}</div></div><div class="score">Top score: {escape(str(record["top_score"] if record["top_score"] != "" else "n/a"))}</div></div>',
            '<ul class="hooks">',
            *hooks_html,
            '</ul>',
            '<div class="links">',
            f'<a href="{record["package_uri"]}">Open package</a>',
            f'<a href="{record["report_uri"]}">Open report</a>',
            f'<a href="{thumbnail}">Open thumbnail</a>' if thumbnail else '',
            '</div>',
            '</div>',
            '</section>',
        ])

    dashboard_parts.extend(['</main>', '</body>', '</html>'])
    dashboard_path.write_text('\n'.join(part for part in dashboard_parts if part), encoding='utf-8')

    print('Wrote', summary_csv)
    print('Wrote', candidates_csv)
    print('Wrote', dashboard_path)
    return summary_csv, candidates_csv, dashboard_path


if __name__ == '__main__':
    import sys

    collect(sys.argv[1] if len(sys.argv) > 1 else '.')
