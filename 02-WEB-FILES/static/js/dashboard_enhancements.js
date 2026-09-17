(() => {
    'use strict';

    let enhancementJobId = null;
    let enhancementTerminal = false;
    let healthTimer = null;
    let statusTimer = null;

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

    async function refreshHealth() {
        try {
            const res = await fetch('/api/health', { cache: 'no-store' });
            const data = await res.json();
            const el = document.getElementById('systemStatus');
            if (!el) return;
            const text = el.querySelector('.system-status-text');
            if (!res.ok || data.status !== 'ok') throw new Error('health check failed');
            text.textContent = `${data.ffmpeg_available ? 'Ready' : 'FFmpeg missing'} · ${data.active_jobs}/${data.max_concurrent_jobs} active`;
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
            const res = await fetch(`/api/jobs/${encodeURIComponent(enhancementJobId)}/retry`, {
                method: 'POST',
                headers: { 'Origin': window.location.origin },
            });
            const data = await res.json().catch(() => ({}));
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
                try {
                    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/status`, { cache: 'no-store' });
                    const data = await res.json();
                    if (data.status === 'done') {
                        enhancementTerminal = true;
                        hideRetry();
                        await showPreview(jobId);
                    } else if (data.status === 'error' || data.status === 'interrupted') {
                        enhancementTerminal = true;
                        showRetry();
                    } else {
                        enhancementTerminal = false;
                        hideRetry();
                    }
                } catch (_) {}
            };
            window.__aivfStatusWrapped = true;
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        ensureSystemStatus();
        ensurePreviewPanel();
        wrapGlobalHandlers();
        refreshHealth();
        healthTimer = setInterval(refreshHealth, 10000);
        statusTimer = setInterval(() => {
            if (!enhancementTerminal && enhancementJobId && typeof window.checkJobStatus === 'function') {
                window.checkJobStatus(enhancementJobId);
            }
        }, 5000);
    });

    window.addEventListener('beforeunload', () => {
        if (healthTimer) clearInterval(healthTimer);
        if (statusTimer) clearInterval(statusTimer);
    });
})();
