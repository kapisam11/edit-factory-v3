(() => {
    'use strict';

    let lastSnapshot = null;
    let refreshBusy = false;
    const message = document.getElementById('adminDiagnosticsMessage');

    function make(tag, cls, text) {
        const element = document.createElement(tag);
        if (cls) element.className = cls;
        if (text !== undefined && text !== null) element.textContent = String(text);
        return element;
    }
    function setMessage(text, kind) {
        message.textContent = text || '';
        message.className = 'log-status ' + (kind || '');
    }
    function statusPill(value, good) {
        return make('span', 'status-pill ' + (good ? 'status-good' : 'status-warn'), value);
    }
    function clearAndAdd(parent, element) { parent.replaceChildren(element); }

    async function refresh() {
        if (refreshBusy) return;
        refreshBusy = true;
        document.getElementById('adminOverallStatus').textContent = 'Checking…';
        try {
            const response = await fetch('/api/admin/diagnostics', { cache: 'no-store' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (response.status === 403 || response.status === 404) {
                    setMessage('The advanced console is locked or your admin session expired. Unlock it again from the V3 badge.', 'error');
                } else {
                    setMessage(data.error || 'Diagnostics could not be loaded.', 'error');
                }
                throw new Error(data.error || 'Diagnostics unavailable (' + response.status + ')');
            }
            lastSnapshot = data;
            document.getElementById('adminOverallStatus').textContent = String(data.status || 'unknown').toUpperCase();
            document.getElementById('adminGeneratedAt').textContent = 'Generated ' + String(data.generated_at || 'unknown');
            document.getElementById('adminDbStatus').textContent = String(data.database?.integrity_check || 'unknown').toUpperCase();
            document.getElementById('adminDbJobs').textContent = 'Jobs recorded: ' + Number(data.database?.jobs_total || 0);
            document.getElementById('adminDiskFree').textContent = Number(data.storage?.free_mb || 0).toLocaleString() + ' MB';
            document.getElementById('adminDiskTotal').textContent = Number(data.storage?.total_mb || 0).toLocaleString() + ' MB total';
            document.getElementById('adminActiveJobs').textContent = String(data.queue?.active_processes ?? '—');
            const counts = data.queue?.counts_by_status || {};
            document.getElementById('adminQueueSummary').textContent = Object.keys(counts).length
                ? Object.keys(counts).sort().map(key => key + ': ' + Number(counts[key])).join(' · ')
                : 'No job records';
            const runtime = document.getElementById('adminRuntimeChecks');
            runtime.replaceChildren();
            const checks = [
                ['SQLite integrity', data.database?.integrity_check || 'unavailable', data.database?.status === 'ok'],
                ['FFmpeg executable', data.media?.ffmpeg ? 'Available' : 'Missing', Boolean(data.media?.ffmpeg)],
                ['FFprobe executable', data.media?.ffprobe ? 'Available' : 'Missing', Boolean(data.media?.ffprobe)],
                ['Output directory', data.storage?.output_packages === null ? 'Unavailable' : String(data.storage?.output_packages ?? 0) + ' packages', data.storage?.output_packages !== null],
                ['Runtime', String(data.runtime?.python || 'unknown') + ' · ' + String(data.runtime?.platform || 'unknown'), true],
                ['Environment', data.environment || 'unknown', true],
            ];
            checks.forEach(([label,value,good]) => {
                const row = make('div','diagnostic-check-row');
                const left = make('span','diagnostic-check-label',label);
                const right = statusPill(value,good);
                row.append(left,right);
                runtime.appendChild(row);
            });
            const jobCounts = document.getElementById('adminJobCounts');
            jobCounts.replaceChildren();
            if (!Object.keys(counts).length) jobCounts.appendChild(make('p','empty-table','No jobs have been recorded.'));
            Object.keys(counts).sort().forEach(key => {
                const card = make('div','state-count-card');
                card.append(make('span','state-count-label',key),make('strong','state-count-value',counts[key]));
                jobCounts.appendChild(card);
            });
            const failures = document.getElementById('adminFailures');
            failures.replaceChildren();
            const failureList = Array.isArray(data.recent_failures) ? data.recent_failures : [];
            document.getElementById('adminFailureCount').textContent = failureList.length + ' records';
            if (!failureList.length) {
                failures.appendChild(make('tr','', ''));
                failures.firstChild.appendChild(make('td','empty-table','No failed or interrupted jobs in the recent failure window.'));
                failures.firstChild.firstChild.colSpan = 4;
            } else {
                failureList.forEach(item => {
                    const tr = make('tr');
                    tr.append(
                        make('td','',String(item.topic || 'Untitled') + ' · ' + String(item.id || '')),
                        make('td','',String(item.status || '') + ' / ' + String(item.step || '')),
                        make('td','',String(item.updated_at || '—')),
                        make('td','error-cell',String(item.error || 'No error detail')),
                    );
                    failures.appendChild(tr);
                });
            }
            const audit = document.getElementById('adminAudit');
            audit.replaceChildren();
            const events = Array.isArray(data.audit_events) ? data.audit_events : [];
            document.getElementById('adminAuditCount').textContent = events.length + ' events';
            if (!events.length) {
                const tr = make('tr');
                const td = make('td','empty-table','No audit events are available in this window.');
                td.colSpan = 5;
                tr.appendChild(td);
                audit.appendChild(tr);
            } else {
                events.slice().reverse().forEach(item => {
                    const tr = make('tr');
                    tr.append(
                        make('td','',String(item.created_at || item.timestamp || '—')),
                        make('td','',String(item.action || '—')),
                        make('td','',String(item.principal || item.actor || '—')),
                        make('td','',String(item.resource || '') + (item.resource_id ? ' / ' + String(item.resource_id) : '')),
                        make('td','',String(item.result || 'recorded')),
                    );
                    audit.appendChild(tr);
                });
            }
            document.getElementById('adminRuntimePill').textContent = data.status === 'ok' ? 'Healthy' : 'Degraded';
            document.getElementById('adminRuntimePill').className = 'status-pill ' + (data.status === 'ok' ? 'status-good' : 'status-warn');
            setMessage(data.security_note || 'Diagnostics loaded. Secret values are not included.', 'done');
        } catch (error) {
            document.getElementById('adminRuntimePill').textContent = 'Unavailable';
            document.getElementById('adminRuntimePill').className = 'status-pill status-warn';
            if (!message.textContent) setMessage(error instanceof Error ? error.message : 'Diagnostics unavailable.', 'error');
        } finally {
            refreshBusy = false;
        }
    }

    document.getElementById('refreshDiagnostics').addEventListener('click', refresh);
    document.getElementById('downloadDiagnostics').addEventListener('click', () => {
        if (!lastSnapshot) {
            setMessage('Load diagnostics before downloading a snapshot.', 'error');
            return;
        }
        const blob = new Blob([JSON.stringify(lastSnapshot, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = 'edit-factory-diagnostics-' + new Date().toISOString().replace(/[:.]/g,'-') + '.json';
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
    });
    refresh();
    window.setInterval(refresh, 20000);
})();