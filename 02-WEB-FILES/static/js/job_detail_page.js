(() => {
    'use strict';

    const jobId = String(window.AIVF_JOB_ID || '');
    const logBox = document.getElementById('jobLog');
    const connection = document.getElementById('logConnection');
    const message = document.getElementById('jobDetailMessage');
    const statusPill = document.getElementById('detailStatus');
    const step = document.getElementById('detailStep');
    const updated = document.getElementById('detailUpdated');
    const errorBox = document.getElementById('detailError');
    let eventSource = null;
    let statusTimer = null;
    let lastMessageAt = 0;

    function addLog(entry) {
        if (!entry || typeof entry !== 'object') return;
        if (logBox.querySelector('.empty-table')) logBox.replaceChildren();
        const line = document.createElement('div');
        line.className = 'admin-log-line';
        const meta = document.createElement('span');
        meta.className = 'admin-log-meta';
        meta.textContent = [entry.time || entry.created_at || '', entry.level || 'INFO'].filter(Boolean).join(' · ');
        const text = document.createElement('p');
        text.textContent = String(entry.msg || entry.message || '');
        line.append(meta, text);
        logBox.appendChild(line);
        logBox.scrollTop = logBox.scrollHeight;
        lastMessageAt = Date.now();
    }

    async function refreshStatus() {
        try {
            const response = await fetch('/api/jobs/' + encodeURIComponent(jobId) + '/status', { cache: 'no-store' });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || 'Status request failed.');
            statusPill.textContent = String(payload.status || 'unknown');
            statusPill.className = 'status-pill status-' + String(payload.status || 'unknown').replace(/[^a-z0-9_-]/gi, '');
            step.textContent = String(payload.step || '—');
            updated.textContent = String(payload.updated_at || new Date().toLocaleString());
            if (payload.error) {
                errorBox.textContent = String(payload.error);
                errorBox.classList.remove('is-hidden');
            }
            if (['done','error','cancelled','interrupted'].includes(payload.status)) {
                if (eventSource) eventSource.close();
                eventSource = null;
                if (statusTimer) window.clearInterval(statusTimer);
                connection.textContent = 'Job finished';
                connection.className = 'status-pill';
            }
        } catch (error) {
            message.textContent = error instanceof Error ? error.message : 'Status request failed.';
            message.className = 'log-status error';
        }
    }

    function connect() {
        if (!jobId || !window.EventSource) {
            connection.textContent = 'Live logs unavailable';
            return;
        }
        eventSource = new EventSource('/api/jobs/' + encodeURIComponent(jobId) + '/logs/stream');
        eventSource.onopen = () => {
            connection.textContent = 'Live';
            connection.className = 'status-pill status-good';
        };
        eventSource.onmessage = event => {
            try { addLog(JSON.parse(event.data)); } catch (_) { addLog({ msg: event.data }); }
        };
        eventSource.onerror = () => {
            connection.textContent = lastMessageAt ? 'Reconnecting' : 'Stream unavailable';
            connection.className = 'status-pill status-warn';
        };
    }

    document.getElementById('refreshJobDetail').addEventListener('click', refreshStatus);
    connect();
    refreshStatus();
    statusTimer = window.setInterval(refreshStatus, 5000);
    window.addEventListener('beforeunload', () => {
        if (eventSource) eventSource.close();
        if (statusTimer) window.clearInterval(statusTimer);
    });
})();