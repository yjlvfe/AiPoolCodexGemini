# Portable account management repair

## Immediate execution priority
1. Deploy and verify live same-model account failover in both pools, using the canonical Manager shared by cN/agN commands. Prove actual Codex credential reads from slot/auth.json (running legacy bridge misreads the directory).
2. Verify Codex and Antigravity request events in the shared database, API and actual dashboard DOM. Do not infer success from tests alone.
3. Only then resume agent integration, installation and release work. No 100% readiness claim before workstation validation.

## Added acceptance: agent integration, failover and logs
- Dashboard integration must locate the installed Hermes/OpenClaw configuration, add this suite's providers without replacing unrelated providers, and read back the exact written target before success. No model/provider selection is changed without explicit authorization.
- Quota exhaustion must advance to another distinct usable account for the exact requested model, with bounded retries and no cross-model fallback. Test both bridges against controlled local upstream servers, including exhausted pools and a partial stream.
- Both Codex and Antigravity requests must write to the shared local request-events schema. The dashboard must display their actual request/token data; missing usage is not estimated or invented.
- Tests, deployment and GitHub publication remain separate milestones. Browser OAuth completion on the user's desktop remains a user acceptance step.

## Scope and acceptance
- Linux root VPS and ordinary Linux users; arbitrary clone paths, including spaces.
- One transactional add/import/switch pipeline per provider; no new slot or active-credential mutation on failed/cancelled/duplicate enrollment.
- No implicit import of the currently logged-in identity on `add`.
- Existing tokens, provider/model configuration, sessions and accounting databases are preserved. No account cleanup runs automatically.
- Project-local dependencies only. Codex login needs the official Codex CLI. Antigravity OAuth is built in and does not depend on a Hermes plugin or an editor named Antigravity.
- Same installer for initial deployment and update; atomic replacement of shortcuts without following old symlinks.
- Real Git revision in dashboard, explicit update/no-change/failure, and runtime restart verification before success.
- Regression tests in isolated disk-backed HOME/prefix with fake upstream/CLI fixtures (not claimed as real authentication). Live usage checks separately.
- Publish to origin/main and verify remote revision. Desktop OAuth completion remains user acceptance, not a claim of universal compatibility.

## Findings (source inspection and live reproduction)
1. Installer redirection follows existing `agN` symlinks and overwrites the switcher with an infinite self-exec (observed on VPS).
2. Copies of the Antigravity switcher resolve the bridge relative to `/usr/local`, and onboarding imports a `/root/.hermes` plugin absent on clean hosts.
3. Codex first login unconditionally copies nonexistent live auth and active marker; jq is also an undeclared dependency.
4. Usage wrappers send unsupported `--usage` and hide all failures.
5. Imports publish credentials before validation; duplicate removal is destructive and accepts unchecked paths.
6. OAuth has no state/PKCE, no overall timeout, and silently changes to an unrelated CLI on failure.
7. Dashboard enumerates only Codex slots 1, 2, 3. Codex bridge reads numbered files while CLI writes numbered directories.
8. Updater masks fetch/restart errors and can stash/pop conflicts silently. UI announces success even after network failure.

## Progress
- [x] Inspect entry points, persistence, installer, updater and live shortcut corruption.
- [x] Private backups outside the repository before source, database and agent-config changes.
- [x] Self-contained transactional enrollment/import/switch, duplicate protection and failure rollback tests.
- [x] Atomic shortcut installation; actual unprivileged Linux install/reinstall/uninstall with empty HOME and spaced paths.
- [x] Live Codex and Antigravity quota failover: select account 1, send an actual inference request, receive `POOL_OK` from account 2 with the exact requested model.
- [x] Codex directory credential reader, SSE preamble errors and empty final-output reconstruction repaired; shared observed-usage logs verified in DB/API/DOM.
- [x] Migrate misplaced new log rows from a stale clone override without deleting legacy records.
- [x] Agent Settings handlers write/re-read the real configs; native Hermes parser and OpenClaw validation pass; model choices and pre-existing fallbacks remain unchanged.
- [x] Browser responsive/no-overflow checks at 320/390/768/1440; failed-update and lost-connection UI checks.
- [x] Real local Git fast-forward/no-op/dirty/fetch-failure regressions.
- [ ] Final unified install/restart and release-tree regression run.
- [ ] Push origin/main, verify the remote revision, and exercise the real dashboard update path.

## Explicit remaining acceptance on another machine
- Fresh Google consent and Codex authentication require the user's browser/account action; they have not been completed on the user's workstation by these tests.
- Full services require Linux/systemd; native Windows/macOS are not certified. WSL2 is the documented Windows route, not a tested native Windows installer.
- Request logs cover traffic through this suite only. No historical request counts are invented for traffic that bypassed the gateways.
- This is implementation, regression testing and self-review by the primary agent, not an independent audit or a 100% compatibility guarantee.
