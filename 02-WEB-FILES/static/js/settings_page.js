(() => {
    'use strict';

    const form = document.getElementById('settingsPageForm');
    const canManage = form?.dataset.canManage === 'true';
    const message = document.getElementById('settingsPageMessage');
    const saveButton = document.getElementById('settingsSaveButton');
    const secretIds = [
        ['spGroqKey','groq_key'],
        ['spModelKey','model_key'],
        ['spElevenLabsKey','elevenlabs_key'],
        ['spDiarizationToken','diarization_token'],
    ];

    function setMessage(text, type) {
        message.textContent = text || '';
        message.className = 'log-status ' + (type || '');
    }

    function safeGet(id) { return document.getElementById(id); }

    async function requestSettings() {
        const response = await fetch('/api/settings', { cache: 'no-store' });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || 'Settings could not be loaded.');
        safeGet('spTargetSeconds').value = payload.default_target_seconds ?? 45;
        safeGet('spWorkflow').value = payload.default_workflow || 'default';
        safeGet('spPlatform').value = payload.default_v3_platform || 'youtube_shorts';
        safeGet('spAudience').value = payload.default_v3_audience || 'general short-form viewers';
        safeGet('spBpm').value = payload.default_v3_bpm ?? 120;
        safeGet('spSkipQc').checked = Boolean(payload.default_skip_qc);
        safeGet('spUseGroq').checked = Boolean(payload.default_use_groq);
        safeGet('spConcurrency').textContent = String(payload.max_concurrent_jobs ?? 'Unknown');
        safeGet('spUploadLimit').textContent = String(payload.max_upload_mb ?? 'Unknown') + ' MB';
        const status = safeGet('spSecretStatus');
        status.replaceChildren();
        secretIds.forEach(([, key]) => {
            const line = document.createElement('div');
            line.className = 'secret-status-item';
            const label = document.createElement('span');
            label.textContent = key.replaceAll('_',' ');
            const pill = document.createElement('strong');
            pill.className = payload[key + '_configured'] ? 'status-pill status-good' : 'status-pill status-muted';
            pill.textContent = payload[key + '_configured'] ? 'Configured' : 'Not configured';
            line.append(label,pill);
            status.appendChild(line);
        });
        if (!canManage) {
            form.querySelectorAll('input, select, button[type="submit"]').forEach(control => { control.disabled = true; });
        }
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!canManage || saveButton.disabled) return;
        const settings = {
            default_target_seconds: Number(safeGet('spTargetSeconds').value),
            default_workflow: safeGet('spWorkflow').value,
            default_v3_platform: safeGet('spPlatform').value,
            default_v3_audience: safeGet('spAudience').value.trim(),
            default_v3_bpm: Number.parseInt(safeGet('spBpm').value,10),
            default_skip_qc: safeGet('spSkipQc').checked,
            default_use_groq: safeGet('spUseGroq').checked,
        };
        secretIds.forEach(([id,key]) => {
            const value = safeGet(id).value.trim();
            if (value) settings[key] = value;
        });
        saveButton.disabled = true;
        saveButton.textContent = 'Saving…';
        setMessage('', '');
        try {
            const response = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings),
                cache: 'no-store',
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || 'Settings could not be saved.');
            secretIds.forEach(([id]) => { safeGet(id).value = ''; });
            await requestSettings();
            setMessage('Settings saved. Runtime API keys are held in memory and were not returned to the browser.', 'done');
        } catch (error) {
            setMessage(error instanceof Error ? error.message : 'Settings could not be saved.', 'error');
        } finally {
            saveButton.disabled = !canManage;
            saveButton.textContent = 'Save settings';
        }
    });

    requestSettings().catch(error => {
        setMessage(error instanceof Error ? error.message : 'Settings could not be loaded.', 'error');
        safeGet('spSecretStatus').textContent = 'Settings API unavailable.';
    });
})();