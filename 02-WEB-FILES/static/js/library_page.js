(() => {
    'use strict';

    const grid = document.getElementById('libraryGrid');
    const search = document.getElementById('librarySearch');
    const filter = document.getElementById('libraryTypeFilter');
    const updated = document.getElementById('libraryUpdated');
    const message = document.getElementById('libraryMessage');
    let packages = [];

    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined && text !== null) element.textContent = String(text);
        return element;
    }

    function render() {
        const query = String(search.value || '').trim().toLowerCase();
        const mode = filter.value;
        const filtered = packages.filter(pkg => {
            const hasVideo = Boolean(pkg.has_video);
            const needsReview = !pkg.v3_readiness || !['ready','pass','complete'].includes(String(pkg.v3_readiness).toLowerCase());
            const searchable = [pkg.name, pkg.script_preview, pkg.v3_readiness].map(v => String(v || '')).join(' ').toLowerCase();
            return (!query || searchable.includes(query)) &&
                (mode === 'all' || (mode === 'video' && hasVideo) || (mode === 'package' && !hasVideo) || (mode === 'review' && needsReview));
        });
        grid.replaceChildren();
        if (!filtered.length) {
            const empty = node('div','empty-state',packages.length ? 'No packages match your search.' : 'No generated packages yet. Create your first video from the Studio page.');
            grid.appendChild(empty);
            return;
        }
        filtered.forEach(pkg => {
            const card = node('article','library-card');
            const thumb = node('div','library-thumb');
            if (pkg.thumbnail) {
                const img = node('img');
                img.src = String(pkg.thumbnail);
                img.alt = 'Thumbnail for ' + String(pkg.name || 'package');
                img.loading = 'lazy';
                img.onerror = () => { thumb.replaceChildren(node('span','library-thumb-placeholder','Preview unavailable')); };
                thumb.appendChild(img);
            } else {
                thumb.appendChild(node('span','library-thumb-placeholder', pkg.has_video ? 'Video available' : 'Package assets'));
            }
            const body = node('div','library-card-body');
            const meta = node('div','library-card-meta');
            meta.appendChild(node('span', pkg.has_video ? 'badge video' : 'badge', pkg.has_video ? 'Rendered video' : 'Package only'));
            if (pkg.v3_readiness) meta.appendChild(node('span','status-pill status-' + String(pkg.v3_readiness).toLowerCase().replace(/[^a-z0-9_-]/g,''), pkg.v3_readiness));
            const title = node('h3','library-card-title',pkg.name || 'Untitled package');
            const created = node('p','library-card-date','Created ' + String(pkg.created || 'unknown'));
            const preview = node('p','library-card-preview',pkg.script_preview || 'No script preview available for this package.');
            const actions = node('div','library-card-actions');
            const review = node('a','btn-primary page-primary-link','Open editorial review');
            review.href = '/editorial-review/' + encodeURIComponent(String(pkg.name || ''));
            const copy = node('button','btn-secondary','Copy package name');
            copy.type = 'button';
            copy.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(String(pkg.name || ''));
                    copy.textContent = 'Copied';
                } catch (_) {
                    copy.textContent = String(pkg.name || '');
                }
            });
            actions.append(review, copy);
            body.append(meta,title,created,preview,actions);
            card.append(thumb,body);
            grid.appendChild(card);
        });
    }

    async function refresh() {
        updated.textContent = 'Loading…';
        try {
            const response = await fetch('/api/packages', { cache: 'no-store' });
            const payload = await response.json().catch(() => []);
            if (!response.ok) throw new Error(payload.error || 'Could not load packages (' + response.status + ')');
            packages = Array.isArray(payload) ? payload : [];
            document.getElementById('libraryTotal').textContent = String(packages.length);
            document.getElementById('libraryVideos').textContent = String(packages.filter(p => p.has_video).length);
            document.getElementById('libraryReview').textContent = String(packages.filter(p => !p.v3_readiness || !['ready','pass','complete'].includes(String(p.v3_readiness).toLowerCase())).length);
            updated.textContent = 'Updated ' + new Date().toLocaleTimeString();
            message.textContent = '';
            message.className = 'log-status';
            render();
        } catch (error) {
            updated.textContent = 'Unavailable';
            message.textContent = error instanceof Error ? error.message : 'Could not load packages.';
            message.className = 'log-status error';
            grid.replaceChildren(node('div','empty-state','Could not load the library. Confirm the dashboard is running and you are signed in.'));
        }
    }

    document.getElementById('refreshLibrary').addEventListener('click', refresh);
    search.addEventListener('input', render);
    filter.addEventListener('change', render);
    refresh();
    window.setInterval(refresh, 20000);
})();