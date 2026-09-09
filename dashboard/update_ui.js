/* Build identity is read from Git and the running process, never invented. */
function buildLabel(data, running = false) {
    const version = running ? data.running_version : data.version;
    return `v${version || '1.9.1'}`;
}

function updateMessage(title, detail = '', state = 'info', showDots = false) {
    const box = document.getElementById('update-result-box');
    box.hidden = false;
    box.dataset.state = state;
    box.style.borderColor = state === 'success' ? '#10b981' : state === 'error' ? '#ef4444' : '#38bdf8';
    document.getElementById('update-result-title').textContent = title;
    document.getElementById('update-result-detail').textContent = detail;
    const dots = document.getElementById('update-loading-dots');
    if (dots) {
        dots.style.display = showDots ? 'inline-flex' : 'none';
        dots.style.color = state === 'success' ? '#10b981' : state === 'error' ? '#ef4444' : '#38bdf8';
    }
}

async function checkRemoteUpdates() {
    const btn = document.getElementById('btn-check-update');
    const icon = document.getElementById('check-icon');
    const text = document.getElementById('check-btn-text');
    const originalText = text.textContent;
    btn.disabled = true;
    if (icon) icon.className = 'spin-icon';
    text.innerHTML = 'Checking <span class="bouncing-dots" style="color: currentColor;"><span></span><span></span><span></span></span>';
    updateMessage('Checking GitHub origin/main for new releases…', 'Fetching latest git refs from remote repository without modifying local working tree.', 'info', true);

    try {
        const res = await fetch('/aipool/api/settings/check_update', { cache: 'no-store', credentials: 'same-origin' });
        if (!res.ok) throw new Error(`Check returned HTTP ${res.status}`);
        const data = await res.json();

        if (data.error) {
            updateMessage('Update check encountered an error', data.error, 'error', false);
            return;
        }

        if (data.has_update) {
            const verStr = data.remote_version ? `v${data.remote_version}` : 'Newer build';
            updateMessage(
                `🎉 New Update Available: ${verStr} (${data.behind_count} commit${data.behind_count > 1 ? 's' : ''} behind)`,
                `Remote commit: ${data.remote_commit ? data.remote_commit.slice(0, 8) : 'unknown'}\n\nRecent commits on origin/main:\n${data.commits_preview || 'No preview available'}\n\nReady to install! Click 'Update from GitHub' below.`,
                'info',
                false
            );
            const updateBtn = document.getElementById('btn-update-suite');
            if (updateBtn) {
                updateBtn.style.animation = 'pulse 2s infinite';
            }
        } else {
            updateMessage(
                '✨ System is Up to Date!',
                `Running revision: ${data.local_commit ? data.local_commit.slice(0, 8) : 'unknown'}\nRemote revision: ${data.remote_commit ? data.remote_commit.slice(0, 8) : 'unknown'}\nYour suite is running the absolute latest verified code from origin/main.`,
                'success',
                false
            );
        }
    } catch (err) {
        updateMessage('Failed to check for updates', err.message || 'Network error', 'error', false);
    } finally {
        btn.disabled = false;
        if (icon) icon.className = '';
        text.textContent = originalText;
    }
}

async function readBuild() {
    const response = await fetch('/aipool/api/settings/version', {cache:'no-store', credentials:'same-origin'});
    if (!response.ok) throw new Error(`Version check returned HTTP ${response.status}`);
    const data = await response.json();
    if (!data.running_commit) throw new Error('The running build could not be identified');
    return data;
}
function renderBuild(data) {
    const pillHeader = document.getElementById('system-version-pill');
    if (pillHeader) pillHeader.textContent = buildLabel(data, true);
    const instEl = document.getElementById('settings-installed-version');
    if (instEl) instEl.textContent = buildLabel(data, true);
    const srcEl = document.getElementById('settings-source-version');
    if (srcEl) srcEl.textContent = buildLabel(data) + (data.dirty ? ' — local changes' : '');
    const dateEl = document.getElementById('settings-commit-date');
    if (dateEl) dateEl.textContent = data.commit_date || 'Unknown';
    const msgEl = document.getElementById('settings-commit-message');
    if (msgEl) msgEl.textContent = data.commit_msg || '';
    const updatePill = document.getElementById('update-status-pill');
    if (updatePill) {
        if (data.dirty) {
            updatePill.textContent = 'Local Changes';
            updatePill.style.background = 'rgba(245, 158, 11, 0.15)';
            updatePill.style.color = '#fbbf24';
            updatePill.style.borderColor = 'rgba(245, 158, 11, 0.3)';
        } else {
            updatePill.textContent = 'Live ' + (data.running_version || data.version || 'v1.8');
            updatePill.style.background = 'rgba(56, 189, 248, 0.15)';
            updatePill.style.color = '#38bdf8';
            updatePill.style.borderColor = 'rgba(56, 189, 248, 0.3)';
        }
    }
}
async function loadBuildInfo() {
    try {
        const data = await readBuild();
        renderBuild(data);
        const last = data.last_update;
        if (last && last.finished_at) {
            const date = new Date(last.finished_at * 1000);
            const pad = n => String(n).padStart(2, '0');
            const stamp = `${pad(date.getMonth()+1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
            document.getElementById('settings-last-update').textContent = `${stamp} — ${last.status}`;
        }
    } catch (error) {
        document.getElementById('system-version-pill').textContent = 'Version unavailable';
    }
}
async function verifyRunningBuild(target) {
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 1500));
        try {
            const data = await readBuild();
            if (data.running_commit === target && !data.dirty) { renderBuild(data); return data; }
        } catch (error) { /* Temporary outage is not success. */ }
    }
    throw new Error('Files may be installed, but the new dashboard process was not verified. Check service status; no success has been assumed.');
}
async function triggerUpdate() {
    if (!confirm('Fetch origin/main, install the new build and verify the running dashboard? Local edits will never be discarded.')) return;
    const button = document.getElementById('btn-update-suite');
    const updateText = document.getElementById('update-btn-text');
    const updateIcon = document.getElementById('update-btn-icon');
    button.disabled = true;
    if (updateIcon) updateIcon.className = 'spin-icon';
    if (updateText) updateText.innerHTML = 'Updating <span class="bouncing-dots" style="color: currentColor;"><span></span><span></span><span></span></span>';
    updateMessage('Fetching and installing build…', 'Pulling changes and rebuilding services; live telemetry will confirm active execution.', 'info', true);
    try {
        const response = await fetch('/aipool/api/settings/update', {method:'POST', credentials:'same-origin'});
        if (!response.ok) throw new Error(`Update request returned HTTP ${response.status}`);
        const result = await response.json();
        if (!result.success) {
            updateMessage('Update failed — not completed', result.message || 'Unknown error', 'error', false);
            return;
        }
        const before = buildLabel(result.version_before);
        const after = buildLabel(result.version_after);
        updateMessage('Files installed; verifying the running build…', `${before} → ${after}\n${result.message || ''}`, 'info', true);
        await verifyRunningBuild(result.version_after.commit);
        updateMessage(result.updated ? 'Update completed and running build verified' : 'Already up to date — no new version was installed', `${before} → ${after}\n${result.message || ''}`, 'success', false);
        if (updateText) updateText.textContent = result.updated ? 'Update verified' : 'Already up to date';
        await loadBuildInfo();
    } catch (error) {
        updateMessage('Update result not confirmed', `${error.message}\nConnection loss is not evidence of success. Use Re-check version to inspect the running build and last update status.`, 'error', false);
    } finally {
        button.disabled = false;
        if (updateIcon) updateIcon.className = '';
        if (updateText && updateText.innerHTML.includes('Updating')) updateText.textContent = 'Update from GitHub';
    }
}
loadBuildInfo();
