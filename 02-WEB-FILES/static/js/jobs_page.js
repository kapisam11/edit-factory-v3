(() => {
    'use strict';

    const table = document.getElementById('jobsTable');
    const search = document.getElementById('jobSearch');
    const statusFilter = document.getElementById('jobStatusFilter');
    const updated = document.getElementById('jobsUpdated');
    const message = document.getElementById('jobsMessage');
    let jobs = [];
    let refreshBusy = false;

    function escapeText(value) {
        return String(value ?? '').replace(/[&<>"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[char]));
    }

    function formatDate(value) {
        if (!value) return '—';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
    }

    function statusClass(value) {
        const safe = String(value || 'unknown').toLowerCase().replace(/[^a-z0-9_-]/g, '');
        return 'status-pill status-' + safe;
    }

    function render() {
        const query = String(search.value || '').trim().toLowerCase();
        const selected = statusFilter.value;
        const filtered = jobs.filter(job => {
            const searchable = [job.topic, job.id, job.step, job.status].map(v => String(v || '')).join(' ').toLowerCase();
            return (!query || searchable.includes(query)) && (selected === 'all' || String(job.status) === selected);
        });
        if (!filtered.length) {
            table.innerHTML = '<tr><td colspan="5" class="empty-table">No jobs match these filters.</td></tr>';
            return;
        }
        table.replaceChildren();
        filtered.forEach(job => {
            const tr = document.createElement('tr');
            const production = document.createElement('td');
            const name = document.createElement('strong');
            name.className = 'table-main-text';
            name.textContent = String(job.topic || 'Untitled production');
            const id = document.createElement('small');
            id.className = 'table-secondary-text';
            id.textContent = 'ID ' + String(job.id || 'unknown');
            production.append(name, id);

            const status = document.createElement('td');
            const pill = document.createElement('span');
            pill.className = statusClass(job.status);
            pill.textContent = String(job.status || 'unknown');
            status.appendChild(pill);

            const step = document.createElement('td');
            step.textContent = String(job.step || '—');
            const time = document.createElement('td');
            time.textContent = formatDate(job.updated_at || job.created_at);
            const actions = document.createElement('td');
            actions.className = 'table-actions';
            const link = document.createElement('a');
            link.className = 'btn-sm';
            link.href = '/jobs/' + encodeURIComponent(String(job.id || ''));
            link.textContent = 'Open';
            actions.appendChild(link);
            tr.append(production, status, step, time, actions);
            table.appendChild(tr);
        });
    }

    async function refresh() {
        if (refreshBusy) return;
        refreshBusy = true;
        updated.textContent = 'Refreshing…';
        try {
            const [jobsResponse, queueResponse] = await Promise.all([
                fetch('/api/jobs', { cache: 'no-store' }),
                fetch('/api/queue/status', { cache: 'no-store' }),
            ]);
            const jobsPayload = await jobsResponse.json().catch(() => []);
            const queue = await queueResponse.json().catch(() => ({}));
            if (!jobsResponse.ok) throw new Error(jobsPayload.error || 'Could not load jobs (' + jobsResponse.status + ')');
            if (!queueResponse.ok) throw new Error(queue.error || 'Could not load queue (' + queueResponse.status + ')');
            jobs = Array.isArray(jobsPayload) ? jobsPayload : Object.values(jobsPayload || {});
            document.getElementById('metricTotal').textContent = String(jobs.length);
            document.getElementById('metricRunning').textContent = String(jobs.filter(j => j.status === 'running').length);
            document.getElementById('metricQueued').textContent = String(Number(queue.queued ?? jobs.filter(j => j.status === 'queued').length));
            document.getElementById('metricFailed').textContent = String(jobs.filter(j => ['error','interrupted'].includes(j.status)).length);
            updated.textContent = 'Updated ' + new Date().toLocaleTimeString();
            message.textContent = '';
            message.className = 'log-status';
            render();
        } catch (error) {
            updated.textContent = 'Connection issue';
            message.textContent = error instanceof Error ? error.message : 'Could not refresh jobs.';
            message.className = 'log-status error';
            if (!jobs.length) table.innerHTML = '<tr><td colspan="5" class="empty-table">Jobs could not be loaded. Check that the dashboard is running and you are signed in.</td></tr>';
        } finally {
            refreshBusy = false;
        }
    }

    document.getElementById('refreshJobs').addEventListener('click', refresh);
    search.addEventListener('input', render);
    statusFilter.addEventListener('change', render);
    refresh();
    window.setInterval(refresh, 10000);
})();