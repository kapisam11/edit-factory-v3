(() => {
    'use strict';

    let enhancementJobId = null;
    let enhancementTerminal = false;
    let healthTimer = null;
    let statusTimer = null;
    let publishingTimer = null;

    function ensureSystemStatus() {
        if (document.getElementById('systemStatus')) return;
        const wrap = document.createElement('div');
        wrap.className = 'system-status-wrap';
        wrap.innerHTML = `
            <div id="systemStatus" class="system-status" role="status" aria-live="polite">
                <span class="system-status-dot"></span>
                <span class="system-status-text">Checking system…</span>
            </div>`;
        document.querySelector('header')?.appendChild(wrap);
    }

    function ensurePublishingPanel() {
        if (document.getElementById('platformPublishingPanel')) return;
        const sidebar = document.querySelector('.sidebar');
        if (!sidebar) return;
        const panel = document.createElement('section');
        panel.id = 'platformPublishingPanel';
        panel.className = 'panel platform-publishing-panel';
        panel.innerHTML =
            '<h2>Platform Publishing</h2>' +
            '<div id="platformPublishingSummary" class="queue-badge">Loading…</div>' +
            '<div id="platformPublishingMeta" class="publishing-meta"></div>' +
            '<div id="platformPublishingDestinations" class="publishing-destinations"></div>' +
            '<h3>Recent errors</h3>' +
            '<div id="platformPublishingErrors" class="publishing-errors"></div>';
        sidebar.insertBefore(panel, sidebar.children[1] || null);
    }

    function addPublishingLine(parent, label, value, className) {
        const line = document.createElement('div');
        if (className) line.className = className;
        const strong = document.createElement('strong');
        strong.textContent = label;
        line.appendChild(strong);
        line.appendChild(document.createTextNode(' ' + String(value === null || value === undefined || value === '' ? '—' : value)));
        parent.appendChild(line);
    }

    function formatPublishingTime(value) {
        if (value === null || value === undefined || value === '') return 'Never';
        const date = new Date(Number(value) * 1000);
        return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleString();
    }

    async function refreshPublishingStatus() {
        const summary = document.getElementById('platformPublishingSummary');
        const meta = document.getElementById('platformPublishingMeta');
        const destinations = document.getElementById('platformPublishingDestinations');
        const errors = document.getElementById('platformPublishingErrors');
        if (!summary || !meta || !destinations || !errors) return;
        try {
            const response = await fetch('/api/publishing/status', { cache: 'no-store' });
            if (!response.ok) throw new Error('publishing status unavailable');
            const data = await response.json();
            if (data.status === 'not_configured') {
                summary.textContent = 'Publisher queue not initialized';
                meta.textContent = 'Run aivf-publish scan to initialize and inspect the destination queue.';
                destinations.replaceChildren();
                errors.replaceChildren();
                summary.classList.remove('error');
                return;
            }
            if (data.status !== 'ok') throw new Error('publishing status unavailable');
            const counts = data.counts_by_state || {};
            const queued = Number(counts.queued || 0) + Number(counts.retryable_failed || 0);
            const active = Number(counts.uploading || 0) + Number(counts.processing_remote || 0);
            const failed = Number(counts.permanent_failed || 0);
            summary.textContent = queued + ' queued/retry · ' + active + ' active · ' +
                Number(counts.published || 0) + ' published · ' + failed + ' failed';
            summary.classList.toggle('error', failed > 0 || data.deadline?.missed_today === true);
            meta.replaceChildren();
            addPublishingLine(meta, 'Last confirmed publication:', formatPublishingTime(data.last_success_epoch));
            const deadlineText = data.deadline?.required
                ? 'Next deadline: ' + formatPublishingTime(data.deadline.next_epoch) +
                    (data.deadline.missed_today ? ' · MISSED' : '')
                : 'Daily deadline alert disabled';
            addPublishingLine(meta, '', deadlineText);
            const diskText = data.disk_free_bytes === null || data.disk_free_bytes === undefined
                ? 'Disk space unknown'
                : (Number(data.disk_free_bytes) / (1024 ** 3)).toFixed(1) + ' GiB free';
            const heartbeatText = data.heartbeat?.updated_at_epoch
                ? 'Heartbeat ' + formatPublishingTime(data.heartbeat.updated_at_epoch) +
                    ' (' + String(data.heartbeat.status || 'unknown') + ')'
                : 'Heartbeat not seen yet';
            addPublishingLine(meta, '', diskText + ' · ' + heartbeatText);
            const tokenValues = (data.token_expiries || []).map(token => {
                const seconds = Number(token.seconds_until_expiry);
                const tokenState = seconds <= 0 ? 'EXPIRED' : (seconds < 86400 ? 'expires soon' : 'valid');
                return String(token.platform) + ':' + String(token.account_id) + ' ' + tokenState;
            });
            addPublishingLine(meta, 'Token expiry:', tokenValues.join(' · ') || 'No managed token expiry is stored');

            destinations.replaceChildren();
            const jobs = (data.destinations || []).slice(0, 12);
            if (!jobs.length) {
                destinations.textContent = 'No destination jobs yet.';
            } else {
                jobs.forEach(job => {
                    const item = document.createElement('div');
                    item.className = 'publishing-job';
                    const heading = document.createElement('div');
                    heading.className = 'publishing-job-heading';
                    const target = document.createElement('strong');
                    target.textContent = '#' + Number(job.job_id) + ' ' + String(job.platform) + ':' + String(job.account_id);
                    const state = document.createElement('span');
                    state.className = 'publishing-state';
                    state.textContent = String(job.state);
                    heading.append(target, state);
                    item.appendChild(heading);
                    addPublishingLine(item, 'Attempt:', Number(job.attempts || 0) + ' / ' + Number(job.max_attempts || 0));
                    if (job.remote_post_id) addPublishingLine(item, 'Remote ID:', String(job.remote_post_id), 'publishing-remote');
                    if (job.remote_url) addPublishingLine(item, 'Remote URL:', String(job.remote_url), 'publishing-remote');
                    if (Number(job.next_attempt_at) > 0 && job.state !== 'published') {
                        addPublishingLine(item, 'Next retry:', formatPublishingTime(job.next_attempt_at), 'publishing-remote');
                    }
                    if (job.last_error) {
                        addPublishingLine(item, String(job.error_code || 'ERROR') + ':', String(job.last_error), 'publishing-error-detail');
                    }
                    destinations.appendChild(item);
                });
            }

            errors.replaceChildren();
            const recentErrors = (data.recent_errors || []).slice(0, 5);
            if (!recentErrors.length) {
                errors.textContent = 'No recent publishing failures.';
            } else {
                recentErrors.forEach(error => {
                    const line = document.createElement('div');
                    line.className = 'publishing-error-detail';
                    line.textContent = '#' + Number(error.job_id) + ' ' + String(error.platform) + ':' +
                        String(error.account_id) + ' — ' + String(error.error_code || error.state) +
                        ': ' + String(error.message || '');
                    errors.appendChild(line);
                });
            }
            if (['degraded', 'blocked_low_disk', 'missed_daily_deadline'].includes(String(data.heartbeat?.status || ''))) {
                summary.classList.add('error');
            }
        } catch (_) {
            summary.textContent = 'Publishing status unavailable';
            summary.classList.add('error');
            meta.textContent = 'Check dashboard authentication and the publisher state database.';
        }
    }

    function ensurePreviewPanel() {
        if (document.getElementById('livePreviewPanel')) return;
        const logsPanel = document.querySelector('.logs-panel');
        if (!logsPanel) return;
        const panel = document.createElement('div');
        panel.id = 'livePreviewPanel';
        panel.className = 'live-preview-panel';
        panel.innerHTML = `
            <h3>Completed Preview</h3>
            <video id="livePreview" controls muted preload="metadata" hidden></video>
            <div id="previewEmpty" class="log-status">A rendered preview will appear here when the current job completes.</div>
            <button id="retryAction" class="btn-secondary retry-action" type="button">Retry current job</button>`;
        logsPanel.appendChild(panel);
        document.getElementById('retryAction')?.addEventListener('click', retryCurrentJob);
    }

    function applyV3Capabilities(capabilities) {
        const mappings = [
            ['enable_ocr', 'ocr', 'OCR analysis'],
            ['enable_object_detection', 'object_detection', 'Object detection'],
            ['enable_diarization', 'diarization', 'Speaker diarization'],
        ];
        mappings.forEach(([id, key, label]) => {
            const input = document.getElementById(id);
            if (!input) return;
            const available = capabilities && capabilities[key] === true;
            input.disabled = !available || document.getElementById('workflow')?.value !== 'v3';
            if (!available) input.checked = false;
            const labelEl = input.closest('.toggle')?.querySelector('.toggle-label');
            if (labelEl) labelEl.textContent = available ? label : (label + ' (not installed)');
        });
    }
    async function refreshHealth() {
        try {
            const res = await fetch('/api/health/details', { cache: 'no-store' });
            const data = await res.json();
            const el = document.getElementById('systemStatus');
            if (!el) return;
            const text = el.querySelector('.system-status-text');
            if (!res.ok || data.status !== 'ok') throw new Error('health check failed');
            text.textContent = `${data.ffmpeg_available ? 'Ready' : 'FFmpeg missing'} · ${data.active_jobs}/${data.max_concurrent_jobs ?? '?'} active`;
            applyV3Capabilities(data.capabilities || {});
            el.title = `Free disk: ${data.disk_free_mb} MB · FFprobe: ${data.ffprobe_available ? 'available' : 'missing'}`;
            el.classList.toggle('error', !data.ffmpeg_available || !data.ffprobe_available);
        } catch (_) {
            const el = document.getElementById('systemStatus');
            if (el) {
                el.querySelector('.system-status-text').textContent = 'Dashboard health unavailable';
                el.classList.add('error');
            }
        }
    }

    async function showPreview(jobId) {
        const video = document.getElementById('livePreview');
        const empty = document.getElementById('previewEmpty');
        if (!video || !empty) return;
        video.hidden = true;
        video.removeAttribute('src');
        empty.textContent = 'Loading rendered preview…';
        empty.className = 'log-status running';
        try {
            const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/preview`, { cache: 'no-store' });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.error || 'Preview unavailable');
            }
            video.src = `/api/jobs/${encodeURIComponent(jobId)}/preview?cacheBust=${Date.now()}`;
            video.hidden = false;
            empty.textContent = 'Preview ready.';
            empty.className = 'log-status done';
        } catch (err) {
            empty.textContent = err.message || 'Preview unavailable.';
            empty.className = 'log-status';
        }
    }

    async function retryCurrentJob() {
        if (!enhancementJobId) return;
        const button = document.getElementById('retryAction');
        if (button) {
            button.disabled = true;
            button.textContent = 'Retrying…';
        }
        try {
            enhancementTerminal = false;
            let res = await fetch(`/api/jobs/${encodeURIComponent(enhancementJobId)}/retry`, {
                method: 'POST',
                headers: { 'Origin': window.location.origin },
            });
            let data = await res.json().catch(() => ({}));
            if (!res.ok && Array.isArray(data.missing_secret_keys) && data.missing_secret_keys.length) {
                const credentials = {};
                for (const key of data.missing_secret_keys) {
                    const value = window.prompt(`Enter the credential required to retry this job (${key}):`);
                    if (value === null || !value.trim()) {
                        throw new Error('Retry cancelled: required credential was not supplied.');
                    }
                    credentials[key] = value.trim();
                }
                res = await fetch(`/api/jobs/${encodeURIComponent(enhancementJobId)}/retry`, {
                    method: 'POST',
                    headers: {
                        'Origin': window.location.origin,
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(credentials),
                });
                data = await res.json().catch(() => ({}));
            }
            if (!res.ok) throw new Error(data.error || `Retry failed (${res.status})`);
            connectToLogs(enhancementJobId);
            if (typeof refreshQueue === 'function') refreshQueue();
            hideRetry();
            const status = document.getElementById('logStatus');
            if (status) {
                status.textContent = data.status === 'running' ? 'Retry running' : 'Retry queued';
                status.className = 'log-status running';
            }
        } catch (err) {
            enhancementTerminal = true;
            if (button) {
                button.disabled = false;
                button.textContent = 'Retry current job';
            }
            if (typeof setPanelMessage === 'function') setPanelMessage(err.message, 'error');
        }
    }

    function showRetry() {
        const button = document.getElementById('retryAction');
        if (!button) return;
        button.disabled = false;
        button.textContent = 'Retry current job';
        button.classList.add('visible');
    }

    function hideRetry() {
        const button = document.getElementById('retryAction');
        if (button) button.classList.remove('visible');
    }

    function wrapGlobalHandlers() {
        if (typeof window.connectToLogs === 'function' && !window.__aivfConnectWrapped) {
            const original = window.connectToLogs;
            window.connectToLogs = function(jobId) {
                enhancementJobId = jobId;
                enhancementTerminal = false;
                hideRetry();
                const video = document.getElementById('livePreview');
                const empty = document.getElementById('previewEmpty');
                if (video) { video.hidden = true; video.removeAttribute('src'); }
                if (empty) {
                    empty.textContent = 'Waiting for this production to complete.';
                    empty.className = 'log-status';
                }
                return original(jobId);
            };
            window.__aivfConnectWrapped = true;
        }

        if (typeof window.checkJobStatus === 'function' && !window.__aivfStatusWrapped) {
            const original = window.checkJobStatus;
            window.checkJobStatus = async function(jobId) {
                enhancementJobId = jobId;
                await original(jobId);

                // The canonical status handler already performed the status request.
                // Reuse its terminal-state DOM update instead of fetching the same
                // endpoint a second time every five seconds.
                const statusEl = document.getElementById('logStatus');
                const className = statusEl?.className || '';
                if (className.includes('done')) {
                    enhancementTerminal = true;
                    hideRetry();
                    await showPreview(jobId);
                } else if (className.includes('error')) {
                    enhancementTerminal = true;
                    showRetry();
                } else {
                    enhancementTerminal = false;
                    hideRetry();
                }
            };
            window.__aivfStatusWrapped = true;
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        ensureSystemStatus();
        ensurePreviewPanel();
        ensurePublishingPanel();
        wrapGlobalHandlers();
        refreshHealth();
        refreshPublishingStatus();
        healthTimer = setInterval(refreshHealth, 10000);
        publishingTimer = setInterval(refreshPublishingStatus, 15000);
        statusTimer = setInterval(() => {
            if (!enhancementTerminal && enhancementJobId && typeof window.checkJobStatus === 'function') {
                window.checkJobStatus(enhancementJobId);
            }
        }, 5000);
    });

    window.addEventListener('beforeunload', () => {
        if (healthTimer) clearInterval(healthTimer);
        if (statusTimer) clearInterval(statusTimer);
        if (publishingTimer) clearInterval(publishingTimer);
    });
})();
