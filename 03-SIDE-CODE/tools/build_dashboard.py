import json
from pathlib import Path
from datetime import datetime

def build_html_dashboard(root='.'):
    """Generate an HTML dashboard for reviewing hooks across all packages."""
    root_p = Path(root)
    
    # collect all hook reports
    reports = []
    for rpt_path in sorted(root_p.rglob('hook_report.json')):
        try:
            with open(rpt_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            pkg_dir = rpt_path.parent.parent
            thumb = pkg_dir / 'thumbnail.png'
            thumb_url = f"file:///{thumb.resolve()}"
            reports.append({
                'package': pkg_dir.name,
                'package_path': str(pkg_dir),
                'hook_report': data,
                'thumbnail_url': thumb_url,
                'thumbnail_exists': thumb.exists()
            })
        except Exception as e:
            print(f'Error reading {rpt_path}: {e}')
    
    # build HTML
    html_header = '''<!DOCTYPE html>
<html>
<head>
    <title>Hook Review Dashboard</title>
    <meta charset="utf-8">
    <style>
        * {''' + ''' margin: 0; padding: 0; box-sizing: border-box; }
        body {''' + ''' font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f5f7fa; padding: 20px; }
        .header {''' + ''' text-align: center; margin-bottom: 40px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 40px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .header h1 {''' + ''' font-size: 2.5em; margin-bottom: 10px; }
        .header p {''' + ''' font-size: 1.1em; opacity: 0.9; }
        .container {''' + ''' max-width: 1400px; margin: 0 auto; }
        .grid {''' + ''' display: grid; grid-template-columns: repeat(auto-fill, minmax(500px, 1fr)); gap: 20px; }
        .package-card {''' + '''
            background: white; border: 1px solid #e0e0e0; border-radius: 8px;
            padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); transition: all 0.3s;
        }
        .package-card:hover {''' + ''' box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        .package-name {''' + ''' font-size: 0.9em; color: #666; margin-bottom: 8px; font-weight: 500; }
        .thumbnail {''' + ''' width: 100%; height: 250px; object-fit: cover; border-radius: 4px; background: #f0f0f0; margin: 12px 0; }
        .hooks {''' + ''' margin: 15px 0; }
        .hooks h3 {''' + ''' font-size: 1em; color: #333; margin-bottom: 10px; }
        .hook-item {''' + '''
            background: #f9fafb; padding: 12px; margin: 8px 0; border-left: 4px solid #667eea;
            border-radius: 4px; display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;
        }
        .hook-text {''' + ''' flex: 1; }
        .hook-text strong {''' + ''' display: block; color: #222; margin-bottom: 4px; }
        .hook-meta {''' + ''' font-size: 0.85em; color: #666; display: flex; gap: 8px; flex-wrap: wrap; }
        .emotion-label {''' + ''' display: inline-block; background: #e8eaf6; color: #3f51b5; padding: 3px 8px; border-radius: 3px; font-size: 0.8em; font-weight: 500; }
        .hook-score {''' + ''' font-weight: bold; color: #4caf50; font-size: 1.1em; white-space: nowrap; }
        .stats {''' + ''' margin-top: 8px; font-size: 0.85em; color: #999; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎬 Hook Review Dashboard</h1>
            <p>Visual A/B Testing Interface</p>
            <p style="font-size: 0.95em; margin-top: 15px;">Generated: %TIMESTAMP% | Total packages: %TOTAL%</p>
        </div>
        <div class="grid">
'''
    html = html_header
    
    for rep in reports:
        hook_data = rep['hook_report']
        ranked = hook_data.get('ranked_hooks', [])
        
        hooks_html = ''
        for hook in ranked[:5]:  # show top 5
            score = hook.get('score', 0)
            emotion = hook.get('emotion', 'neutral')
            text = hook.get('hook', '')
            hooks_html += f'''            <div class="hook-item">
                <div class="hook-text">
                    <strong>{text}</strong>
                    <div class="hook-meta">
                        <span class="emotion-label">{emotion}</span>
                    </div>
                </div>
                <div class="hook-score">{score:.2f}</div>
            </div>
'''
        
        thumb_img = ''
        if rep['thumbnail_exists']:
            thumb_img = f'<img class="thumbnail" src="{rep["thumbnail_url"]}" alt="Package thumbnail">'
        
        html += f'''        <div class="package-card">
            <div class="package-name">{rep['package']}</div>
            {thumb_img}
            <div class="hooks">
                <h3>Ranked Hooks</h3>
                {hooks_html}
            </div>
            <div class="stats">{len(ranked)} total hooks | Quality: {hook_data.get("top", {}).get("emotion", "N/A")}</div>
        </div>
'''
    
    html += '''        </div>
    </div>
</body>
</html>
'''
    
    # write dashboard
    output = root_p / 'reports' / 'dashboard.html'
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'w', encoding='utf-8') as f:
        html_final = html.replace('%TIMESTAMP%', datetime.now().isoformat()).replace('%TOTAL%', str(len(reports)))
        f.write(html_final)
    
    print(f'✓ Wrote HTML dashboard to {output}')
    return str(output)

if __name__ == '__main__':
    build_html_dashboard('.')
