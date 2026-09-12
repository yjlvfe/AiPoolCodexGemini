# AiPool

**AiPool 2.1.7** is a local gateway and operations dashboard for two independent account pools:

- **Codex** accounts and models
- **Gemini** accounts and models

AiPool presents provider-compatible HTTP endpoints, selects an authenticated account for each request, rotates accounts when a provider returns a quota or rate-limit failure, records usage, and exposes the same operational state through a web dashboard and command-line tools.

AiPool is not a model router that silently changes providers. A request sent to one provider remains in that provider's pool.

## What AiPool Provides

- OpenAI-compatible local endpoints for Codex and Gemini traffic.
- Separate account storage, health checks, cooldowns, and usage totals for each provider.
- Same-model account rotation on HTTP 429, quota exhaustion, and account authentication failures.
- Persistent request history, token usage, latency, and classified error metrics.
- A dashboard for pool status, account status, analytics, and a unified live operations stream.
- Official setup scripts for Hermes Agent and OpenClaw.
- Official `install.sh` and `update.sh` entry points for systemd deployments.

## Official Providers and Services

Only the following provider identifiers and systemd services are part of the current AiPool interface.

| Provider | Service | Local endpoint | Purpose |
| --- | --- | --- | --- |
| `codex` | `codex.service` | `http://127.0.0.1:8124/v1` | Codex Responses and compatible chat requests |
| `gemini` | `gemini.service` | `http://127.0.0.1:8123/v1` | Gemini-compatible chat and completion requests |
| Dashboard | `dashboard.service` | `http://127.0.0.1:8444` | Pool control, analytics, settings, and live operations |

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

Use the installed CLI tools to add accounts to their corresponding pool:

```bash
# Add or import a Codex account.
c add

# Add or import a Gemini account.
ag add

# List accounts without a provider round trip.
c list
ag list

# Query current provider usage.
c usage
ag usage

# Switch an existing account slot.
c switch 1
ag switch 1
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
