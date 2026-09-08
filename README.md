# AiPoolCodexGemini

Local multi-account gateways for Codex/ChatGPT and Antigravity, a shared request dashboard, account commands, and optional Hermes/OpenClaw provider registration.

## Supported setup

- **Full installation:** Linux with Python 3.10+, Git, `python3-venv`, pip and a working **systemd user session**. Install as your normal user on a desktop; do not use `sudo ./install.sh`.
- **Windows:** use Linux under WSL2 with systemd enabled. Native Windows services are not supported or tested.
- **macOS/Termux:** full background-service installation is not supported by this release. No universal-device claim is made.
- The dashboard is responsive; browser checks cover widths 320, 390, 768 and 1440 pixels.
- Codex enrollment/usage requires the **official Codex CLI**. Antigravity OAuth is built in; no Hermes plugin, Google editor, or separate `agy` tool is required.

## Install

For Debian/Ubuntu, install prerequisites through the OS package manager first:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip

git clone https://github.com/yjlvfe/AiPoolCodexGemini.git
cd AiPoolCodexGemini
bash install.sh
```

The installer creates a project-local `.venv`, installs requirements, installs commands to `~/.local/bin` for ordinary users (`/usr/local/bin` for root), and starts systemd user services. If instructed, add your command directory to PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Without a systemd user session you can install commands only:

```bash
bash install.sh --cli-only
```

The optional `--prefix "/your/chosen prefix"` is remembered for updates. Moving the clone requires running the installer again so shortcuts and units point at the new location. Never copy another machine's `config.env` blindly.

Default endpoints:
- Dashboard: `http://127.0.0.1:8444`
- Antigravity Chat Completions: `http://127.0.0.1:8123/v1/chat/completions`
- Codex Responses: `http://127.0.0.1:8124/v1/responses`
- Codex Chat Completions adapter: `http://127.0.0.1:8124/v1/chat/completions`

Configure optional ports and Telegram credentials in `config.env` using `config.env.example`. Do not commit that file. Telegram is optional: an unconfigured bot is skipped. Use your own bot, not a token copied from a repository.

## Add accounts

Install the official Codex CLI if missing. The optional helper requires an existing supported Node.js/npm installation and installs into your user's prefix:

```bash
bash scripts/install-cli-tools.sh
```

Then authenticate each identity separately:

```bash
c add
c add
ag add
ag add
```

- Codex uses device authorization by default. If device authorization is unavailable on the account, try `c add --browser` on a local desktop.
- Each Codex enrollment uses a fresh isolated `CODEX_HOME`, not the currently active login.
- Google uses OAuth state and PKCE, account selection, and provider validation before publishing a slot. On SSH, open the displayed URL locally and paste the **complete redirect URL** back into the terminal if localhost cannot reach the VPS. Keep that URL private.
- Browser consent/account choice must be completed by you. A disabled account, provider restriction or failed consent is not a successful addition.
- Duplicate identities are rejected without publishing another slot. Failed enrollment preserves the active credentials.
- Existing credential files can be imported with `c add /path/to/auth.json` or `ag add /path/to/antigravity-oauth-token`. Do not send these files to anyone.

Commands:

```bash
clist
aglist
c1
c2
c 3
ag1
ag2
ag 3
cusage
agusage
cusage --short
agusage --short
```

An unregistered numbered slot starts enrollment. `c clean` / `ag clean` only report duplicates; they do not silently delete accounts. `c rm N` / `ag rm N` archive a non-active slot. Unknown usage is displayed as unavailable rather than a made-up percentage.

## Automatic account failover

Requests **sent through the local gateway** use the active valid account first. On exhausted quota/rate limiting or account-specific authorization failure, the pool tries another distinct stored account. The successful account is promoted through the **same account manager used by `cN` and `agN`**.

- The requested model ID is preserved exactly. No cross-provider or cross-model fallback.
- Attempts are bounded by available slots, with an in-process per-model cooldown.
- Codex quota failures inside SSE preamble events are checked before committing downstream output.
- Once response output has begun, a failure is surfaced; the request is not silently replayed with a different account.
- If all accounts fail, the gateway returns an error. It cannot create quota or bypass provider restrictions.
- Requests sent directly from an agent to an upstream provider bypass this pool, its failover, and its dashboard logs.

## Connect agents

Use Dashboard Settings or:

```bash
bash setup-hermes.sh
bash setup-openclaw.sh
```

Registration backs up the actual target file, adds `gemini-pool` and `codex-pool`, and verifies readback. It repairs the malformed provider structures written by older versions without replacing unrelated providers. Hermes supports `HERMES_HOME`/`HERMES_CONFIG`; OpenClaw supports its config/state environment overrides. Keep these aligned with the agent instance you intend to configure.

**Registration does not select a provider/model or restart your agent.** Select the newly registered pool provider and its exact model using your agent's normal model picker, then start a fresh request/reload that agent as appropriate. A verified configuration is not proof that an already-running conversation has adopted it. Existing model choices and fallback settings are not rewritten.

## Shared request logs

Both bridges write observed request events to `dashboard/auth.db` by default (override with `AUTH_DB_PATH`). The dashboard reads that **same database**. A stale absolute override to an old clone can make new logs appear missing; correct it or migrate the database before switching paths.

Each observed event records provider/pool, requested model, account, timestamp, outcome and actual provider-reported token usage. Missing usage is `null`/`—`, not zero. Requests, prompts, OAuth credentials and response text are not stored in this request log. Legacy records are retained; a migration cannot reconstruct Codex requests that were never recorded.

## Updates and version identity

```bash
bash update.sh
```

Or use **Settings → Update Suite from GitHub**. The updater fast-forwards `origin/main` only; it refuses dirty tracked files or a divergent/wrong branch instead of discarding local work. It uses the same installer as a fresh installation.

The UI displays the release version **and actual Git commit**, distinguishes source from the running dashboard, shows the last update outcome, and verifies the running build before announcing success. A no-op says **Already up to date**; it does not invent a new version. Update status is stored privately in `.git/aipool-update.json`.

For older clones: back up local changes, then `git pull --ff-only` and `bash install.sh` once to obtain the corrected updater. If Git reports conflicts or divergent history, resolve those deliberately; do not reset account data to make an update pass.

```bash
systemctl --user status ai-codex-bridge ai-gemini-bridge ai-dashboard
journalctl --user -u ai-codex-bridge -u ai-gemini-bridge -n 80 --no-pager
```

## Uninstall

```bash
bash uninstall.sh
```

Only this clone's managed shortcuts/services are removed. Accounts, config and databases are retained. Do not delete `.codex-accounts`, `.antigravity-accounts` or the dashboard database unless you explicitly intend to remove that data.

## Verification and release limits

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests use isolated disk-backed homes, synthetic identities and local HTTP upstreams. They cover account rollback/deduplication, quota failover/SSE errors, shared logs, configuration integration and real local Git updates. Optional installed-agent checks are skipped when those agents are unavailable. They do **not** impersonate completed browser consent.

The current repair was additionally exercised with real provider requests after `c1` and `ag1`, automatic promotion to account 2 in both pools, matching dashboard logs, an unprivileged Linux CLI install, native agent configuration validation, and DOM browser checks. See `docs/REPAIR_PLAN.md` for the validation boundary. Complete fresh Codex and Google enrollment on your own workstation before treating that environment as accepted.

## Security

Gateway listeners are local by default. Keep them behind a trusted local boundary; do not expose them as unauthenticated public APIs. Use HTTPS and authenticated access for a remotely exposed dashboard. A direct localhost dashboard session is trusted; forwarded requests do not qualify for that bypass.

The working tree excludes credential files, databases and local evidence. **`.gitignore` does not erase secrets from Git history.** Older versions contained a hard-coded Telegram bot token; if you used that token, revoke/rotate it with BotFather and put only your replacement in `config.env`. This release removes the hard-coded token; it does not rewrite repository history or rotate anyone's credentials automatically.
