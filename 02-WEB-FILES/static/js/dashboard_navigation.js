(() => {
    'use strict';

    const trigger = document.getElementById('brandAdminTrigger');
    const modal = document.getElementById('adminUnlockModal');
    const closeButton = document.getElementById('adminUnlockClose');
    const form = document.getElementById('adminUnlockForm');
    const codeInput = document.getElementById('adminUnlockCode');
    const message = document.getElementById('adminUnlockMessage');
    const submitButton = document.getElementById('adminUnlockSubmit');

    if (!trigger || !modal || !form || !codeInput || !message || !submitButton) return;

    let clickCount = 0;
    let clickTimer = null;

    function openUnlock() {
        modal.style.display = 'block';
        modal.setAttribute('aria-hidden', 'false');
        codeInput.value = '';
        message.textContent = '';
        message.className = 'log-status';
        window.setTimeout(() => codeInput.focus(), 0);
    }

    function closeUnlock() {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
        trigger.focus();
    }

    function registerClick() {
        clickCount += 1;
        if (clickTimer) window.clearTimeout(clickTimer);
        if (clickCount >= 5) {
            clickCount = 0;
            openUnlock();
            return;
        }
        clickTimer = window.setTimeout(() => { clickCount = 0; }, 3000);
    }

    trigger.addEventListener('click', registerClick);
    trigger.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            registerClick();
        }
    });
    closeButton?.addEventListener('click', closeUnlock);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeUnlock();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && modal.style.display === 'block') closeUnlock();
    });

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (submitButton.disabled) return;
        submitButton.disabled = true;
        submitButton.textContent = 'Checking…';
        message.textContent = '';
        message.className = 'log-status';
        try {
            const response = await fetch('/api/admin/unlock', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: codeInput.value }),
                cache: 'no-store',
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || (response.status === 403 ? 'Admin role required.' : 'Unlock failed.'));
            window.location.assign('/admin');
        } catch (error) {
            message.textContent = error instanceof Error ? error.message : 'Unlock failed.';
            message.className = 'log-status error';
            codeInput.select();
        } finally {
            submitButton.disabled = false;
            submitButton.textContent = 'Unlock console';
        }
    });
})();