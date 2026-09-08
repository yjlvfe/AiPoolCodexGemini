# 🚀 AiPoolCodexGemini Suite

**Autonomous Multi-Account AI Pool & Failover Gateway Suite**

A unified, self-contained architecture for orchestrating multiple **Gemini** (Antigravity) and **Codex** (ChatGPT) accounts. Provides local high-speed OpenAI-compatible gateways (`/v1/chat/completions`), dynamic automatic account failover on rate limits (HTTP 429), a real-time web dashboard, and seamless one-click integrations for **Hermes Agent** and **OpenClaw**.

---

## 🌟 Key Architecture & Features

1. **Gemini Gateway (Port `8123`):**
   - OpenAI-compatible endpoint: `http://127.0.0.1:8123/v1`
   - Autonomous dynamic failover across all connected Google accounts (`1..N`) on HTTP 429.
   - Built-in token auto-refresh via Google OAuth.
   - 1:1 model mapping (`gemini-3.8-flash-tiered`, `gemini-2.5-pro`, `claude-sonnet-4-6`, etc.).

2. **Codex / ChatGPT Gateway (Port `8124`):**
   - OpenAI-compatible endpoint: `http://127.0.0.1:8124/v1`
   - Instant switching across discounted and shared ChatGPT Plus accounts.
   - Exact quota tracking (5-hour window, weekly quota, and active account identification).
   - Strict 1:1 model naming (`gpt-6-astra`, `gpt-5.6-luna`, `gpt-5.6-sol`).

3. **Web Dashboard & Settings (Port `8444`):**
   - Modern OLED glassmorphism UI with real-time stats, pool capacity meters, and operation streams.
   - Independent **Settings** section in top header for instant one-click provider linking.
   - Localhost direct access support and secure 24-hour token authentication via Telegram bot (@YJReportbot).

4. **One-Click Tool Integrations:**
   - Auto-links local pool gateways into **Hermes Agent** (`~/.hermes/config.yaml`).
   - Auto-links local pool gateways into **OpenClaw** (`~/.openclaw/openclaw.json`).
   - Non-destructive: appends pool providers while preserving all existing configurations.

---

## 📥 Installation

Clone the repository and run the unified installer:

```bash
git clone https://github.com/yjlvfe/AiPoolCodexGemini.git
cd AiPoolCodexGemini
chmod +x install.sh
./install.sh
```

Upon completion, all four systemd services will start automatically, and the dashboard URL will be displayed:
👉 **Dashboard:** `http://localhost:8444` (or `http://127.0.0.1:8444`)

---

## 💻 CLI Commands & Usage

### 1. Codex / ChatGPT Accounts:
- **List accounts & active status:**
  ```bash
  cx
  ```
- **Add new account (interactive device authorization):**
  ```bash
  c add
  # or
  cx add
  ```
- **Switch active account:**
  ```bash
  c 1
  c 2
  cx 3
  # or directly by number shortcut
  c1
  c2
  ```
- **Inspect quotas & reset windows:**
  ```bash
  cusage
  ```

### 2. Gemini / Antigravity Accounts:
- **List accounts & active status:**
  ```bash
  ag
  ```
- **Add new account (interactive OAuth enrollment):**
  ```bash
  ag add
  ```
- **Switch active account:**
  ```bash
  ag 1
  ag 2
  # or directly by number shortcut
  ag1
  ag2
  ```
- **Inspect quotas & remaining capacity:**
  ```bash
  agusage
  ```

---

## 🔗 One-Click Tool Integrations

Link your local gateways to your agents without manual configuration:

### Link to Hermes Agent:
```bash
./setup-hermes.sh
```
*Safely appends `gemini` and `codex` custom providers into `~/.hermes/config.yaml` with backup.*

### Link to OpenClaw:
```bash
./setup-openclaw.sh
```
*Safely appends `gemini_pool` and `codex_pool` providers into `~/.openclaw/openclaw.json` with backup.*

*(Both integrations can also be triggered with a single click from the Dashboard Settings page).*

---

## 🗑️ Uninstalling

To cleanly stop all background services, disable systemd units, and remove all CLI shortcuts from `/usr/local/bin`:

```bash
./uninstall.sh
```
*(Your account tokens and credentials remain safely preserved in `~/.antigravity-accounts` and `~/.codex-accounts`).*

---

## 🛡️ Security & Privacy
- Zero leakage: credentials, OAuth tokens, and database files are strictly excluded via `.gitignore`.
- No forced fallbacks: models are pinned 1:1 to their real names to prevent unintended substitutions.
- Session safety: Localhost access is supported directly; external access is guarded by 24h authenticated sessions.
