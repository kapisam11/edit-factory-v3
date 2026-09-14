import json
import csv
from pathlib import Path

def export_crowdsource_csv(root='.'):
    """Export A/B review CSV with direct thumbnail links for crowdsourcing."""
    root_p = Path(root)
    rows = []
    
    for rpt in sorted(root_p.rglob('hook_report.json')):
        try:
            with open(rpt, 'r', encoding='utf-8') as f:
                data = json.load(f)
            pkg_dir = rpt.parent.parent
            thumb = pkg_dir / 'thumbnail.png'
            thumb_url = f"file:///{thumb.resolve()}"
            
            ranked = data.get('ranked_hooks', [])
            top_hooks = [h.get('hook', '') for h in ranked[:3]]
            top_scores = [h.get('score', 0) for h in ranked[:3]]
            
            rows.append({
                'package_name': pkg_dir.name,
                'package_path': str(pkg_dir),
                'thumbnail_link': thumb_url,
                'hook_1': top_hooks[0] if len(top_hooks) > 0 else '',
                'hook_1_score': f"{top_scores[0]:.2f}" if len(top_scores) > 0 else '',
                'hook_2': top_hooks[1] if len(top_hooks) > 1 else '',
                'hook_2_score': f"{top_scores[1]:.2f}" if len(top_scores) > 1 else '',
                'hook_3': top_hooks[2] if len(top_hooks) > 2 else '',
                'hook_3_score': f"{top_scores[2]:.2f}" if len(top_scores) > 2 else '',
                'emotion': data.get('top', {}).get('emotion', ''),
            })
        except Exception as e:
            print(f'Error reading {rpt}: {e}')
    
    # write CSV
    output = root_p / 'reports' / 'ab_review_crowdsource.csv'
    output.parent.mkdir(parents=True, exist_ok=True)
    
    if rows:
        with open(output, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        print(f'✓ Wrote crowdsourcing CSV to {output}')
        print(f'  - {len(rows)} packages included')
        print('  - Direct thumbnail links (file://) for offline/external review')
    
    return str(output)

if __name__ == '__main__':
    export_crowdsource_csv('.')
