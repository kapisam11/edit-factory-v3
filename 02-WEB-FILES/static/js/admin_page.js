(() => {
    'use strict';

    let lastSnapshot = null;
    let refreshBusy = false;
    let lastLogs = [];
    let previousLogIds = new Set();
    let firstLogPoll = true;
    let logsPaused = false;
    let logRefreshBusy = false;

    const message = document.getElementById('adminDiagnosticsMessage');
    const logMessage = document.getElementById('adminLogMessage');
    const logFeed = document.getElementById('adminLogFeed');
    const logSearch = document.getElementById('adminLogSearch');
    const logSource = document.getElementById('adminLogSource');
    const logLevel = document.getElementById('adminLogLevel');

    function make(tag, cls, text) {
        const element = document.createElement(tag);
        if (cls) element.className = cls;
        if (text !== undefined && text !== null) element.textContent = String(text);
        return element;
    }

    function setMessage(target, text, kind) {
        target.textContent = text || '';
        target.className = 'log-status ' + (kind || '');
    }

    function statusPill(value, good) {
        return make('span', 'status-pill ' + (good ? 'status-good' : 'status-warn'), value);
    }

    function formatDuration(seconds) {
        let value = Math.max(0, Number(seconds) || 0);
        const days = Math.floor(value / 86400);
        value %= 86400;
        const hours = Math.floor(value / 3600);
        value %= 3600;
        const minutes = Math.floor(value / 60);
        const secs = Math.floor(value % 60);
        if (days) return days + 'd ' + hours + 'h';
        if (hours) return hours + 'h ' + minutes + 'm';
        if (minutes) return minutes + 'm ' + secs + 's';
        return secs + 's';
    }

    function formatTimestamp(value) {
        if (!value) return 'Unknown time';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
    }

    function formatRelativeTime(value) {
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return '';
        const seconds = Math.round((Date.now() - parsed.getTime()) / 1000);
        if (seconds < 60) return Math.max(0, seconds) + 's ago';
        if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
        if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
        return Math.floor(seconds / 86400) + 'd ago';
    }

    function humanSource(value) {
        return ({
            'job-log': 'JOB OUTPUT',
            'lifecycle': 'JOB LIFECYCLE',
            'audit': 'AUDIT',
        })[String(value)] || String(value || 'SYSTEM').toUpperCase();
    }

    function normalizedLevel(value) {
        const level = String(value || 'INFO').toUpperCase();
        if (level === 'WARN') return 'WARNING';
        if (['ERROR', 'WARNING', 'INFO', 'DEBUG'].includes(level)) return level;
        return 'INFO';
    }

    function currentVisibleLogs() {
        const query = String(logSearch.value || '').trim().toLowerCase();
        const source = logSource.value;
        const level = logLevel.value;
        return lastLogs.filter(item => {
            const itemLevel = normalizedLevel(item.level);
            const searchable = [
                item.timestamp, item.level, item.source, item.job_id, item.topic,
                item.actor, item.action, item.result, item.message, item.id,
            ].map(value => String(value || '')).join(' ').toLowerCase();
            return (!query || searchable.includes(query))
                && (source === 'all' || String(item.source) === source)
                && (level === 'all' || itemLevel === level);
        });
    }

    function makeLogEntry(item) {
        const level = normalizedLevel(item.level);
        const entry = make('article', 'ops-log-entry ops-level-' + level.toLowerCase());
        const left = make('div', 'ops-log-entry-gutter');
        left.appendChild(make('span', 'ops-severity-mark', level === 'ERROR' ? '!' : level === 'WARNING' ? '△' : '·'));

        const main = make('div', 'ops-log-entry-main');
        const top = make('div', 'ops-log-entry-top');
        const source = make('span', 'ops-source-tag source-' + String(item.source || 'system').replace(/[^a-z0-9_-]/gi, ''), humanSource(item.source));
        const severity = make('span', 'ops-level-tag level-' + level.toLowerCase(), level);
        top.append(source, severity);
        if (item.action && item.source !== 'audit') top.appendChild(make('span', 'ops-event-tag', item.action));

        const body = make('p', 'ops-log-message', item.message || 'No message');
        const meta = make('div', 'ops-log-entry-meta');
        const fullTime = make('time', 'ops-time-absolute', formatTimestamp(item.timestamp));
        if (item.timestamp) fullTime.dateTime = String(item.timestamp);
        meta.appendChild(fullTime);
        const relative = formatRelativeTime(item.timestamp);
        if (relative) meta.appendChild(make('span', 'ops-time-relative', relative));
        if (item.topic) meta.appendChild(make('span', 'ops-meta-topic', item.topic));
        if (item.actor) meta.appendChild(make('span', 'ops-meta-actor', 'Actor: ' + item.actor));
        if (item.result) meta.appendChild(make('span', 'ops-meta-result', 'Result: ' + item.result));

        if (item.job_id) {
            const jobLink = make('a', 'ops-job-link', 'Open job ↗');
            jobLink.href = '/jobs/' + encodeURIComponent(String(item.job_id));
            jobLink.title = 'Open the job detail view';
            meta.appendChild(jobLink);
        }
        main.append(top, body, meta);
        entry.append(left, main);
        return entry;
    }

    function renderLogs() {
        const wasNearBottom = logFeed.scrollHeight - logFeed.scrollTop - logFeed.clientHeight < 90;
        const visible = currentVisibleLogs();
        logFeed.replaceChildren();
        if (!visible.length) {
            const emptyText = lastLogs.length ? 'No events match the current filters.' : 'No persisted job output or audit events are available yet.';
            logFeed.appendChild(make('div', 'ops-log-empty', emptyText));
        } else {
            visible.forEach(item => logFeed.appendChild(makeLogEntry(item)));
        }

        const errors = lastLogs.filter(item => normalizedLevel(item.level) === 'ERROR').length;
        const warnings = lastLogs.filter(item => normalizedLevel(item.level) === 'WARNING').length;
        document.getElementById('adminLogEvents').textContent = String(lastLogs.length);
        document.getElementById('adminLogErrors').textContent = String(errors);
        document.getElementById('adminLogWarnings').textContent = String(warnings);
        document.getElementById('adminLogVisible').textContent = visible.length + ' visible event' + (visible.length === 1 ? '' : 's');
        if (wasNearBottom && document.getElementById('adminLogAutoscroll')?.checked !== false) {
            logFeed.scrollTop = logFeed.scrollHeight;
        }
        return visible;
    }

    async function refreshLogs() {
        if (logRefreshBusy) return;
        logRefreshBusy = true;
        const connection = document.getElementById('adminLogConnection');
        try {
            const response = await fetch('/api/admin/logs?limit=300', { cache: 'no-store' });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (response.status === 403 || response.status === 404) {
                    connection.textContent = 'Locked';
                    connection.className = 'status-pill status-warn';
                    setMessage(logMessage, 'The advanced console is locked or your admin unlock expired. Unlock again from the V3 badge.', 'error');
                } else {
                    setMessage(logMessage, payload.error || 'Could not load the operations log.', 'error');
                }
                throw new Error(payload.error || 'Activity log unavailable (' + response.status + ')');
            }
            const newIds = new Set((Array.isArray(payload.logs) ? payload.logs : []).map(item => String(item.id)));
            let newlyAdded = 0;
            if (!firstLogPoll) {
                for (const id of newIds) if (!previousLogIds.has(id)) newlyAdded += 1;
            }
            firstLogPoll = false;
            previousLogIds = newIds;
            lastLogs = Array.isArray(payload.logs) ? payload.logs : [];
            document.getElementById('adminLogNew').textContent = String(newlyAdded);
            const generated = payload.generated_at ? new Date(payload.generated_at) : new Date();
            document.getElementById('adminLogUpdated').textContent = 'Updated ' + (Number.isNaN(generated.getTime()) ? new Date().toLocaleTimeString() : generated.toLocaleTimeString());
            connection.innerHTML = '<span class="live-dot"></span> ' + (logsPaused ? 'Paused' : 'Live');
            connection.className = 'status-pill ' + (logsPaused ? 'status-warn' : 'status-good');
            setMessage(logMessage, payload.retention_note || 'Activity log refreshed.', '');
            renderLogs();
        } catch (error) {
            connection.textContent = 'Unavailable';
            connection.className = 'status-pill status-warn';
            if (!logMessage.textContent) setMessage(logMessage, error instanceof Error ? error.message : 'Activity log unavailable.', 'error');
        } finally {
            logRefreshBusy = false;
        }
    }

    async function refresh() {
        if (refreshBusy) return;
        refreshBusy = true;
        try {
            const response = await fetch('/api/admin/diagnostics', { cache: 'no-store' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (response.status === 403 || response.status === 404) {
                    setMessage(message, 'The advanced console is locked or your admin session expired. Unlock it again from the V3 badge.', 'error');
                } else {
                    setMessage(message, data.error || 'Diagnostics could not be loaded.', 'error');
                }
                throw new Error(data.error || 'Diagnostics unavailable (' + response.status + ')');
            }
            lastSnapshot = data;
            document.getElementById('adminOverallStatus').textContent = String(data.status || 'unknown').toUpperCase();
            document.getElementById('adminGeneratedAt').textContent = 'Checked ' + formatTimestamp(data.generated_at);
            document.getElementById('adminDbStatus').textContent = String(data.database?.integrity_check || 'unknown').toUpperCase();
            document.getElementById('adminDbJobs').textContent = 'Jobs recorded: ' + Number(data.database?.jobs_total || 0);
            document.getElementById('adminDiskFree').textContent = Number(data.storage?.free_mb || 0).toLocaleString() + ' MB';
            document.getElementById('adminDiskTotal').textContent = Number(data.storage?.total_mb || 0).toLocaleString() + ' MB total';
            document.getElementById('adminActiveJobs').textContent = String(data.queue?.active_processes ?? '—');
            document.getElementById('adminUptime').textContent = formatDuration(data.runtime?.uptime_seconds);
            document.getElementById('adminHttpRequests').textContent = Number(data.telemetry?.http_requests_total || 0).toLocaleString();
            document.getElementById('adminHttpErrors').textContent = Number(data.telemetry?.http_5xx_total || 0).toLocaleString();
            document.getElementById('adminRequestP95').textContent = Number(data.telemetry?.request_p95_ms || 0).toLocaleString() + ' ms';
            document.getElementById('adminPackageCount').textContent = data.storage?.output_packages === null ? 'Unavailable' : String(data.storage?.output_packages ?? 0);

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
                ['Process memory', data.runtime?.memory_rss_mb === null || data.runtime?.memory_rss_mb === undefined ? 'Unavailable' : Number(data.runtime.memory_rss_mb).toLocaleString() + ' MB RSS', data.runtime?.memory_rss_mb !== null && data.runtime?.memory_rss_mb !== undefined],
                ['Runtime', String(data.runtime?.python || 'unknown') + ' · ' + String(data.runtime?.platform || 'unknown'), true],
                ['Environment', data.environment || 'unknown', true],
            ];
            checks.forEach(([label,value,good]) => {
                const row = make('div','diagnostic-check-row');
                row.append(make('span','diagnostic-check-label',label),statusPill(value,good));
                runtime.appendChild(row);
            });

            const jobCounts = document.getElementById('adminJobCounts');
            jobCounts.replaceChildren();
            if (!Object.keys(counts).length) {
                const empty = make('p','empty-table','No jobs have been recorded.');
                empty.style.gridColumn = '1 / -1';
                jobCounts.appendChild(empty);
            }
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
                const tr = make('tr');
                const td = make('td','empty-table','No failed or interrupted jobs in the recent failure window.');
                td.colSpan = 4;
                tr.appendChild(td);
                failures.appendChild(tr);
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
                        make('td','',formatTimestamp(item.created_at || item.timestamp)),
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
            setMessage(message, data.security_note || 'Diagnostics loaded. Secret values are not included.', 'done');
        } catch (error) {
            document.getElementById('adminRuntimePill').textContent = 'Unavailable';
            document.getElementById('adminRuntimePill').className = 'status-pill status-warn';
            if (!message.textContent) setMessage(message, error instanceof Error ? error.message : 'Diagnostics unavailable.', 'error');
        } finally {
            refreshBusy = false;
        }
    }

    function downloadFile(filename, contents, type) {
        const blob = new Blob([contents], { type });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
    }

    function csvCell(value) {
        let safe = String(value ?? '');
        // Prefix spreadsheet formula characters to avoid CSV formula injection.
        if (/^[=+@\-\t\r]/.test(safe)) safe = "'" + safe;
        return '"' + safe.replace(/"/g, '""') + '"';
    }

    document.getElementById('refreshDiagnostics').addEventListener('click', refresh);
    document.getElementById('downloadDiagnostics').addEventListener('click', () => {
        if (!lastSnapshot) {
            setMessage(message, 'Load diagnostics before downloading a report.', 'error');
            return;
        }
        downloadFile(
            'edit-factory-diagnostics-' + new Date().toISOString().replace(/[:.]/g,'-') + '.json',
            JSON.stringify(lastSnapshot, null, 2),
            'application/json',
        );
    });

    document.getElementById('refreshLogs').addEventListener('click', refreshLogs);
    document.getElementById('toggleLogLive').addEventListener('click', event => {
        logsPaused = !logsPaused;
        event.currentTarget.textContent = logsPaused ? 'Resume live' : 'Pause live';
        event.currentTarget.setAttribute('aria-pressed', String(logsPaused));
        document.getElementById('adminLogConnection').innerHTML = '<span class="live-dot"></span> ' + (logsPaused ? 'Paused' : 'Live');
        document.getElementById('adminLogConnection').className = 'status-pill ' + (logsPaused ? 'status-warn' : 'status-good');
        if (!logsPaused) refreshLogs();
    });
    [logSearch, logSource, logLevel].forEach(control => {
        control.addEventListener(control === logSearch ? 'input' : 'change', renderLogs);
    });
    document.getElementById('clearLogFilters').addEventListener('click', () => {
        logSearch.value = '';
        logSource.value = 'all';
        logLevel.value = 'all';
        renderLogs();
    });
    document.getElementById('exportLogsCsv').addEventListener('click', () => {
        const visible = currentVisibleLogs();
        const columns = ['timestamp','level','source','job_id','topic','actor','action','result','message'];
        const rows = [columns.map(csvCell).join(',')];
        visible.forEach(item => rows.push(columns.map(key => csvCell(item[key])).join(',')));
        downloadFile(
            'edit-factory-operations-log-' + new Date().toISOString().replace(/[:.]/g,'-') + '.csv',
            rows.join('\r\n'),
            'text/csv;charset=utf-8',
        );
        setMessage(logMessage, 'Exported ' + visible.length + ' visible log events. Filters did not modify stored records.', 'done');
    });
    document.getElementById('copyVisibleLogs').addEventListener('click', async () => {
        const visible = currentVisibleLogs();
        const text = visible.map(item => [
            '[' + item.timestamp + ']',
            '[' + normalizedLevel(item.level) + ']',
            '[' + item.source + ']',
            item.job_id ? 'job=' + item.job_id : '',
            item.actor ? 'actor=' + item.actor : '',
            item.message,
        ].filter(Boolean).join(' ')).join('\n');
        try {
            await navigator.clipboard.writeText(text);
            setMessage(logMessage, 'Copied ' + visible.length + ' visible log events.', 'done');
        } catch (_) {
            setMessage(logMessage, 'Clipboard access was blocked by the browser. Use Export CSV instead.', 'error');
        }
    });

    refresh();
    refreshLogs();
    window.setInterval(refresh, 20000);
    window.setInterval(() => {
        if (!logsPaused) refreshLogs();
    }, 5000);
})();