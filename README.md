# AiPool

<p align="center">
  <a href="README.md"><b>English</b></a> •
  <a href="README.ar.md"><b>العربية</b></a>
</p>

> **Current release:** `v2.2.4` — **Stable / Production Ready** (2026-09-15). Claude tool schema repair and multi-account gateway stability; verified by 164/164 tests.

> [!WARNING]
> **⚠️ DISCLAIMER:**
> This tool automates multi-account session management and quota failover for upstream AI providers (including **Gemini / Google Antigravity** and **OpenAI Codex**). Using third-party pooling or reverse-engineering internal APIs may violate provider Terms of Service and could result in account restriction, suspension, or permanent termination. Use strictly at your own discretion and risk.

**AiPool** is the ultimate enterprise-grade autonomous rotation gateway and high-availability dashboard for multi-account AI pools. It pools multiple **ChatGPT / OpenAI Codex** and **Google Gemini / Antigravity (Claude & GPT)** accounts into a single, seamless, self-healing OpenAI-compatible API.

---

## 🌟 Why AiPool? (Why You Need It)

If you actively use AI coding agents, autonomous frameworks, or LLM applications (Hermes, OpenClaw, Claude Code, Cline, Cursor, Roo Code, Aider), you hit provider rate limits (`HTTP 429`) and daily/weekly quota ceilings constantly. 

**AiPool completely eliminates AI downtime:**
- 🔄 **Autonomous Zero-Downtime Rotation**: When an account exhausts its tokens or hits a 429 limit, AiPool instantly rotates to the next available account without failing the user request.
- 💰 **Pool Free & Paid Accounts Together**: Combine multiple free and paid accounts into a unified, massive collective quota.
- ⚡ **100% OpenAI API Compatible**: Plug-and-play with any AI tool, SDK, or framework simply by setting `OPENAI_BASE_URL` and `OPENAI_API_KEY`.
- 🔐 **Granular API Key Management**: Generate scoped Bearer tokens for friends, team members, or specific tools with model whitelisting, provider restrictions, and lifetime token quotas.
- 📱 **Mobile-First Responsive Dashboard**: Real-time telemetry, live token counters, health indicators, and instant inspection of requests from your phone or desktop.
- 🛡️ **Strict Pool & Privacy Isolation**: Prompt scrubbing, zero cross-provider leakage (Codex never pollutes Gemini), and robust access control.

---

## 🚀 Key Features

1. **Smart Quota & Tier Awareness**:
   - Automatically detects account tiers (Free vs Pro/Ultra).
   - Dynamically tracks 5-hour rolling windows (Paid) and Weekly quotas (Free & Paid) without broken metrics.
   - Intelligently ranks and prioritizes active, ready accounts (`ACTIVE`, `READY`, `COOLING`, `OUT`).

2. **Full Spectrum Client Telemetry**:
   - Deep inspection identifying client agents (Hermes, OpenClaw, Codex CLI, Antigravity, Claude Code, Cursor, Cline, Roo Code, Aider, Postman, cURL) in real time.
   - Dedicated `/api` live operations stream isolating public API key traffic with emerald badges.

3. **Persistent Lifetime Accounting**:
   - High-performance SQLite engine (`client_usage_counters`) ensuring token consumption records never reset or lose data across restarts or log rotations.

4. **Streamlined OAuth & CLI Onboarding**:
   - Built-in `ag add` OAuth onboarding with adaptive mobile formatting and direct `Ctrl + Click` desktop browser launch.
   - Full support for standard free accounts defaulting to canonical `aicode-consumers` project.

---

## ⚡ Architecture & Endpoints

| Provider | Service | Internal Endpoint | Public Proxy Route | Purpose |
| --- | --- | --- | --- | --- |
| `codex` | `codex.service` | `http://127.0.0.1:8124/v1` | `/v1/chat/completions` | Codex and OpenAI-compatible completions |
| `gemini` | `gemini.service` | `http://127.0.0.1:8123/v1` | `/gemini/v1/chat/completions` | Gemini and Claude/GPT completion requests |
| `dashboard` | `dashboard.service` | `http://127.0.0.1:8444` | `/AiPool` / `/AiPool/API` | Full management dashboard, API control, and stream |

---

## Key Features

1. **Autonomous Account Rotation**: Immediate, zero-downtime failover to another pool account upon rate-limit (429) or token exhaustion.
2. **Opt-Out Model Control**: Keys and accounts enable all available models by default with clean model count badges (`Allowed: X / Y`).
3. **Audit & Safety**: Prompts and completions are tracked in SQLite (`request_events`), scrubbed of secrets, and inspectable via the UI.
4. **Mobile-First Responsive UI**: Stacked cards, copy-to-clipboard buttons, live countdowns, and clean segmented navigations.

The bridges listen on loopback by default. If a reverse proxy or another host must access them, configure authentication and network exposure explicitly before changing the bind address.

## Account Pools Architecture

AiPool maintains two independent account pools.

### Codex Pool

`codex.service` accepts a request for a literal Codex model ID, loads eligible Codex accounts, refreshes credentials when required, sends the request to the Codex upstream, and records the result under the `Codex` pool.

### Gemini Pool

`gemini.service` accepts a request for a literal Gemini model ID, loads eligible Gemini accounts, refreshes OAuth credentials when required, sends the request to the Gemini upstream, and records the result under the `Gemini` pool.

### Account Slots and State

Each pool contains numbered account slots with provider credentials, identity metadata, and an active-account marker. The bridge and CLI use the same pool state, so switching an account in the CLI is reflected in the dashboard.

Per-account and per-model cooldowns are persisted in the runtime database. Usage counters are maintained separately for Codex and Gemini accounts. Provider model catalogs are discovered from the live bridge rather than relying on a fixed README model list.

## Account Rotation and Quota Handling

Rotation is bounded, same-model failover within the current provider pool:

1. AiPool sends the request to an eligible account in the requested provider pool.
2. On HTTP 429, quota exhaustion, or an account authentication failure, AiPool marks that account and model as temporarily unavailable.
3. The cooldown uses the provider's `Retry-After` value when available and is persisted for subsequent requests.
4. AiPool retries the same logical request with another account from the same pool.
5. The successful account may become the active account for subsequent requests.

Transient provider and network failures use the bounded retry policy as well. The default provider retry budget is 20 attempts, and the configured policy cannot reduce the minimum below 20. Once a streamed response has started, AiPool does not replay the request after a later stream failure.

### Strict Pool Separation

Pool separation is an invariant:

- A Codex request uses only the **Codex Pool**.
- A Gemini request uses only the **Gemini Pool**.
- Codex never falls back to Gemini.
- Gemini never falls back to Codex.
- AiPool does not silently substitute a different model or provider when the requested pool is exhausted.

If every eligible account for the requested provider and model is unavailable, the request fails with an error. This is intentional and prevents hidden provider changes, unexpected billing, and incompatible request or response behavior.

## Dashboard and Analytics

Open the dashboard at:

```text
http://127.0.0.1:8444
```

The dashboard provides:

- Separate Codex and Gemini pool views.
- Active account, total account, healthy account, and quota-window information.
- Per-account token totals and provider usage summaries.
- Request counts, prompt tokens, completion tokens, total tokens, model usage, latency, and classified errors.
- Detailed recent request records with provider, pool, model, account, status, timestamp, and request ID.
- On-demand request inspection with bounded, redacted request data.
- Settings status for the Hermes and OpenClaw integrations.
- Version and update status.

Detailed request records are retained according to `AIPOOL_REQUEST_RETENTION`; older usage is consolidated into daily rollups so analytics remain durable without keeping an unbounded detailed log.

### Unified Live Operations Stream

The dashboard's **Unified Live Operations Stream** displays recent operations from both official pools in one chronological view. Each operation remains labeled with its provider and pool, so the unified view is an observation surface, not a routing layer.

The stream can show:

- Provider and pool: `Codex` or `Gemini`.
- Requested model and account slot.
- Request status and error classification.
- Prompt, completion, and total token usage when supplied by the provider.
- Request ID, timestamp, and latency-related details where available.

The stream reads current request data from the shared runtime database on each dashboard refresh. It does not replace the separate Codex and Gemini analytics totals.

## Runtime State

The canonical runtime database is:

```text
/var/lib/aipool/runtime/auth.db
```

This SQLite database stores dashboard authentication state, request history, usage rollups, per-account usage counters, and provider/account/model cooldowns. Keep it private and include it in operational backups. It is runtime state, not source code, and must not be committed to the repository.

The managed deployment also uses the following canonical locations:

```text
/var/lib/aipool/app       # deployed AiPool application files
/var/lib/aipool/venv      # service Python environment
/var/lib/aipool/runtime   # runtime database and snapshots
/var/lib/aipool/accounts  # deployment account storage when explicitly configured
```

Account stores can be selected explicitly with `CODEX_ACCOUNT_STORE` and `AG_ACCOUNT_STORE`. Do not place credentials in the repository or in a public configuration file.

## Installation

### Requirements

- Linux with systemd for the official service installation.
- Python 3.10 or newer.
- Git, `python3-venv`, and `python3-pip`.
- Node.js and npm only when the official Codex CLI is not already installed. Gemini OAuth support is built into AiPool.

On Ubuntu or Debian:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

Install a supported Node.js LTS before installation if `codex` is not already available on `PATH`.

### Official Installation Method

Use the repository's root installer. Do not replace it with ad hoc service files or independent shell installers.

```bash
git clone https://github.com/yjlvfe/AiPoolCodexGemini.git
cd AiPoolCodexGemini
cp config.env.example config.env
# Edit config.env only for local, non-secret settings.
sudo bash install.sh
```

`install.sh` is the supported shell entry point and delegates to `scripts/manage_install.py`. The installer reconciles the Python environment and CLI wrappers, creates or refreshes the official systemd units, enables and starts the services, retires recognized legacy unit names after the new units are active, and checks that the dashboard responds.

The generated service units use the canonical deployment paths listed above. When the source checkout is not already the managed deployment checkout, stage deployable source files before the first service start:

```bash
sudo python3 scripts/aipool_nonroot_migration.py \
  --apply \
  --dest /var/lib/aipool/app \
  --owner aipool

sudo python3 scripts/aipool_nonroot_migration.py \
  --verify \
  --dest /var/lib/aipool/app

sudo bash install.sh
```

The migration helper writes a manifest and verifies copied file hashes. It excludes runtime databases, client identity files, private OAuth material, and generated dashboard artifacts.

### Configure Accounts

Use the installed CLI tools to enroll accounts into their corresponding pools:

```bash
# Enroll a Codex account:
c add

# Enroll a Gemini / Antigravity account (OAuth):
ag add
```

#### 🔑 Enrolling Gemini / Antigravity via OAuth:
When you run `ag add`:
1. The CLI will display a secure Google OAuth authorization URL.
2. Copy the URL and open it in your browser to sign in with your Google account.
3. After granting permissions, the browser will attempt to redirect to `http://localhost:...` (which may show a connection error if running on a remote server/VPS).
4. **Copy the entire redirected URL** from your browser address bar and paste it back into your terminal prompt to complete authentication.

```bash
# List accounts without an external API call:
c list
ag list

# Check real-time quotas and token limits:
c usage
ag usage

# Fast switch active account slot:
c switch 1   # or alias: c1
ag switch 1  # or alias: ag1
```

Convenience commands such as `c1`, `ag1`, `clist`, `aglist`, `cusage`, and `agusage` are also installed. Account enrollment and credential changes belong in the CLI; the dashboard is intended for monitoring and controlled switching of existing slots.

## Updating

### Official Update Method

Run the repository's update entry point from a clean `main` checkout:

```bash
sudo bash update.sh
```

`update.sh` delegates to `scripts/update_suite.py`. The updater:

1. Fetches `origin/main`.
2. Requires the local checkout to be clean and already on `main`.
3. Performs a fast-forward-only update.
4. Re-runs the idempotent installer.
5. Reconciles service units and installed CLI tools.
6. Re-applies and verifies installed Hermes and OpenClaw integrations when their CLIs and configurations are present.
7. Verifies the installed source revision.

The updater does not discard local tracked changes. Back up or commit local changes before updating. Account credentials and runtime database state are kept outside the deployable source inventory.

If you maintain a separate administrative checkout, update the checkout that backs `/var/lib/aipool/app`, or re-run the migration helper and its manifest verification before restarting services. Do not point the official units at an unverified legacy checkout.

For a CLI-only maintenance operation on a host without active system services:

```bash
bash update.sh --cli-only
```

Use the normal update command for the supported three-service deployment.

## Migration from Legacy Versions

Migration is designed to make the current names and paths authoritative without presenting historical services as supported interfaces.

### Service Name Cleanup

During installation, AiPool writes and activates these official units:

```text
codex.service
gemini.service
dashboard.service
```

Only after the new units are active, the installer searches both system and user systemd scopes and stops, disables, removes, and reloads recognized historical unit names. Historical names are migration inputs only; they are not supported service names:

```text
ai-codex.service
ai-codex-bridge.service
aipool-codex.service
aipool-codex-bridge.service
ai-gemini.service
ai-gemini-bridge.service
aipool-gemini.service
aipool-gemini-bridge.service
ai-dashboard.service
ai-bot.service
aipool-dashboard.service
```

The installer is idempotent. Running `sudo bash install.sh` again leaves the three official units in place and does not reactivate retired aliases.

### Legacy Deployment and Runtime Paths

The current services use `/var/lib/aipool/app`, `/var/lib/aipool/venv`, and `/var/lib/aipool/runtime`. A legacy checkout or manually managed service path is not used by the official units after installation.

The safe migration sequence is:

1. Back up account stores and `/var/lib/aipool/runtime/auth.db`.
2. Stage only deployable source files into `/var/lib/aipool/app` with `scripts/aipool_nonroot_migration.py`.
3. Verify the generated migration manifest.
4. Run `sudo bash install.sh` and wait for all three official services to become active.
5. Run the health checks in the next section.
6. Remove obsolete source directories only after the new services and data checks pass.

The migration helper deliberately does not delete old source directories, account stores, databases, private credentials, or generated artifacts. This prevents an upgrade from destroying recoverable state. Review and remove obsolete files separately after taking a backup.

Legacy numbered account files remain readable. When an account is switched or rewritten, the CLI converts the slot to the current directory-based account layout transactionally. Use `c list`, `ag list`, `c usage`, and `ag usage` after migration to confirm both pools.

The runtime database initializer also upgrades compatible older SQLite schemas and normalizes old internal model suffixes for reporting. It does not move a database from an arbitrary legacy path automatically; preserve or deliberately migrate the database to `/var/lib/aipool/runtime/auth.db` before starting the official services.

### Legacy Integration Entries

Running the official integration setup scripts removes known obsolete AiPool provider identities from the relevant provider map, adds the current `codex` and `gemini` entries, preserves unrelated agent settings, and verifies the result by reading the configuration back. Historical integration keys are migration data, not supported provider IDs.

## Hermes Agent and OpenClaw Integrations

AiPool configures the two official provider IDs, `codex` and `gemini`, in the agent's existing configuration. The setup scripts do not create a guessed profile and do not overwrite unrelated settings.

### Hermes Agent

```bash
bash setup-hermes.sh
```

The script delegates to `scripts/setup-hermes.py` and updates the Hermes provider map in the existing YAML configuration. By default it reads:

```text
~/.hermes/config.yaml
```

Use `HERMES_HOME` or `AIPOOL_HERMES_CONFIG` when the configuration is stored elsewhere. The script reads live model catalogs from the two local bridges, configures the correct transport for each provider, enforces the configured retry floor, and verifies the written provider entries with the Hermes CLI or a guarded readback.

### OpenClaw

```bash
bash setup-openclaw.sh
```

The script delegates to `scripts/setup-openclaw.py` and updates the provider map in the existing OpenClaw JSON or JSON5 configuration. By default it reads:

```text
~/.openclaw/openclaw.json
```

Use `OPENCLAW_CONFIG_PATH`, `OPENCLAW_STATE_DIR`, or `AIPOOL_OPENCLAW_CONFIG` when the configuration is stored elsewhere. The script configures `models.providers.codex` and `models.providers.gemini`, removes known obsolete AiPool provider identities, validates the resulting configuration, and verifies provider readback.

To check integration status without changing configuration:

```bash
python3 scripts/setup-hermes.py --status
python3 scripts/setup-openclaw.py --status
```

The integrations expose the two pools to the agent. They do not create a cross-pool fallback policy. Select the provider and model explicitly in the agent when required.

## Verification and Health Checks

### Service State

```bash
sudo systemctl is-active codex.service gemini.service dashboard.service
sudo systemctl is-enabled codex.service gemini.service dashboard.service
sudo systemctl --no-pager --full status codex.service gemini.service dashboard.service
```

### Bridge Health

```bash
curl -fsS http://127.0.0.1:8124/healthz
curl -fsS http://127.0.0.1:8123/healthz
```

Each bridge should return a JSON object with `status` set to `ok`.

### Live Model Catalogs

```bash
curl -fsS http://127.0.0.1:8124/v1/models
curl -fsS http://127.0.0.1:8123/v1/models
```

These calls confirm that the bridges are serving their current provider model catalogs. Use the returned literal model IDs in requests and agent configuration.

### Dashboard and Runtime Database

```bash
curl -fsS http://127.0.0.1:8444/api/settings/version
curl -fsS http://127.0.0.1:8444/api/status
curl -fsS http://127.0.0.1:8444/api/metrics
sudo test -f /var/lib/aipool/runtime/auth.db
```

For a SQLite integrity check when the `sqlite3` command is unavailable:

```bash
sudo python3 - <<'PY'
import sqlite3

path = "/var/lib/aipool/runtime/auth.db"
with sqlite3.connect(path) as connection:
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
print(result)
assert result == "ok"
PY
```

### Account and Pool Checks

```bash
c list
ag list
c usage
ag usage
```

For an explicit authenticated smoke test, use a model returned by the corresponding `/v1/models` endpoint:

```bash
python3 scripts/live_pool_probe.py codex <codex-model-id>
python3 scripts/live_pool_probe.py gemini <gemini-model-id>
```

The probe sends a small request to only the selected provider, expects the response marker `POOL_OK`, and verifies that the request event is written to the shared runtime database. Run it once per provider, never with a model from the other pool.

If a check fails, inspect the relevant journal without exposing credentials:

```bash
sudo journalctl -u codex.service -n 100 --no-pager
sudo journalctl -u gemini.service -n 100 --no-pager
sudo journalctl -u dashboard.service -n 100 --no-pager
```

## License

AiPool is distributed under the MIT License. See [LICENSE](LICENSE).
