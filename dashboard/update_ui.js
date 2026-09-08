/* Build identity is read from Git and the running process, never invented. */
function buildLabel(data, running = false) {
    const version = running ? data.running_version : data.version;
    const commit = running ? data.running_commit : data.commit;
    return `v${version || 'unknown'} · ${commit ? commit.slice(0, 8) : 'unknown'}`;
}
function updateMessage(title, detail = '', state = 'info') {
    const box = document.getElementById('update-result-box');
    box.hidden = false;
    box.dataset.state = state;
    box.style.borderColor = state === 'success' ? '#10b981' : state === 'error' ? '#ef4444' : '#38bdf8';
    document.getElementById('update-result-title').textContent = title;
    document.getElementById('update-result-detail').textContent = detail;
}
async function readBuild() {
    const response = await fetch('/aipool/api/settings/version', {cache:'no-store', credentials:'same-origin'});
    if (!response.ok) throw new Error(`Version check returned HTTP ${response.status}`);
    const data = await response.json();
    if (!data.running_commit) throw new Error('The running build could not be identified');
    return data;
}
function renderBuild(data) {
    document.getElementById('system-version-pill').textContent = buildLabel(data, true);
    document.getElementById('settings-installed-version').textContent = buildLabel(data, true);
    document.getElementById('settings-source-version').textContent = buildLabel(data) + (data.dirty ? ' — local changes' : '');
    document.getElementById('settings-commit-date').textContent = data.commit_date || 'Unknown';
    document.getElementById('settings-commit-message').textContent = data.commit_msg || '';
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
    button.disabled = true;
    button.textContent = 'Updating…';
    updateMessage('Fetching and installing…', 'The result will include the exact before/after builds.');
    try {
        const response = await fetch('/aipool/api/settings/update', {method:'POST', credentials:'same-origin'});
        if (!response.ok) throw new Error(`Update request returned HTTP ${response.status}`);
        const result = await response.json();
        if (!result.success) {
            updateMessage('Update failed — not completed', result.message || 'Unknown error', 'error');
            return;
        }
        const before = buildLabel(result.version_before);
        const after = buildLabel(result.version_after);
        updateMessage('Files installed; verifying the running build…', `${before} → ${after}\n${result.message || ''}`);
        await verifyRunningBuild(result.version_after.commit);
        updateMessage(result.updated ? 'Update completed and running build verified' : 'Already up to date — no new version was installed', `${before} → ${after}\n${result.message || ''}`, 'success');
        button.textContent = result.updated ? 'Update verified' : 'Already up to date';
        await loadBuildInfo();
    } catch (error) {
        updateMessage('Update result not confirmed', `${error.message}\nConnection loss is not evidence of success. Use Re-check version to inspect the running build and last update status.`, 'error');
    } finally {
        button.disabled = false;
        if (button.textContent === 'Updating…') button.textContent = 'Update Suite from GitHub';
    }
}
loadBuildInfo();
