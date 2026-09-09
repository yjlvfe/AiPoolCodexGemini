# AiPoolCodexGemini

A high-performance, local multi-account proxy gateway and dashboard for OpenAI Codex/ChatGPT and Google Gemini/Antigravity APIs. Features instant same-model quota failover, unified token usage tracking, and 1,000,000 token context window support.

---

## Features

- **Instant Same-Model Failover**: Automatically rotates to the next available account when quota is exhausted (HTTP 429 / Quota Exceeded) without altering the requested model or losing conversational state.
- **1:1 Native Model Compatibility**:
  - **Codex / ChatGPT**: Full support for official models (`gpt-6-astra`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.5`, `gpt-5.4-mini`).
  - **Gemini / Antigravity**: Clean official identifiers (`gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.1-pro`, `claude-opus-4-6`, `claude-sonnet-4-6`).
  - Full **1,000,000 token context window** across all models.
- **Unified Web Dashboard**: Live responsive interface showing account statuses, aggregated pool metrics, and a shared chronological request stream.
- **Security & Authentication**: Secured access via temporary 30-minute Telegram Magic Links via dedicated bot integration.
- **Native Agent Integration**: Ready-to-use setup scripts for **Hermes Agent** and **OpenClaw**.

---

## Default Endpoints

| Service | Address & Port | Protocol / Purpose |
| :--- | :--- | :--- |
| **Web Dashboard** | `http://127.0.0.1:8444` | Management UI, Token Analytics, Request Logs |
| **Gemini Bridge** | `http://127.0.0.1:8123/v1` | OpenAI-compatible Chat Completions API |
| **Codex Bridge** | `http://127.0.0.1:8124/v1` | OpenAI-compatible Chat Completions API |

---

## Installation

### 1. Prerequisites (Ubuntu / Debian)
```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

### 2. Clone Repository
```bash
git clone https://github.com/yjlvfe/AiPoolCodexGemini.git
cd AiPoolCodexGemini
```

### 3. Environment Configuration (Optional)
```bash
cp config.env.example config.env
# Configure Telegram Bot credentials and custom ports if needed
```

### 4. Run Installer
```bash
bash install.sh
```
*The installer automatically configures the local `.venv`, installs dependencies, and deploys systemd user services.*

### 5. Verify Services
```bash
systemctl --user status ai-codex-bridge ai-gemini-bridge ai-dashboard
```

---

## Account Management

### Add Accounts
```bash
# Add ChatGPT / Codex Account
c add

# Add Gemini / Antigravity Account
ag add
```

### Quick Commands
```bash
# List accounts and status
clist
aglist

# Check quota and token usage
cusage
agusage

# Switch active account manually
c1       # Switch to Codex Account #1
c2       # Switch to Codex Account #2
ag1      # Switch to Gemini Account #1
ag2      # Switch to Gemini Account #2
```

---

## Agent Integration

Integrate directly with autonomous agents using the provided automation scripts:

```bash
# Register Gemini & ChatGPT providers with Hermes Agent
python3 scripts/setup-hermes.py

# Register with OpenClaw
python3 scripts/setup-openclaw.py
```

---

## Updates

Update to the latest release seamlessly:
```bash
bash update.sh
```
*Or update directly via the Web Dashboard under **Settings → Update Suite from GitHub**.*

---

## License

This project is licensed under the **MIT License**.

Copyright (c) 2026 **Youssef Al-Qahtany** ([@YJLVFE](https://github.com/yjlvfe)). All rights reserved.
