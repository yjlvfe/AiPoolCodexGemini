#!/usr/bin/env python3
"""
AiPoolCodexGemini - Automatic OpenClaw Integration Script
Safely updates ~/.openclaw/openclaw.json with Gemini and Codex local pool gateways.
"""

import os
import sys
import json

CONFIG_PATH = os.path.expanduser("~/.openclaw/openclaw.json")

GEMINI_PROVIDER = {
    "baseUrl": "http://127.0.0.1:8123/v1",
    "apiKey": "pool-key",
    "models": ["gemini-3.8-flash-tiered", "gemini-2.5-pro", "claude-sonnet-4-6"]
}

CODEX_PROVIDER = {
    "baseUrl": "http://127.0.0.1:8124/v1",
    "apiKey": "pool-key",
    "models": ["gpt-6-astra", "gpt-5.6-luna", "gpt-5.6-sol"]
}

def main():
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ OpenClaw config file not found at: {CONFIG_PATH}")
        print("Please ensure OpenClaw is installed first.")
        sys.exit(1)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Error reading {CONFIG_PATH}: {e}")
        sys.exit(1)

    if "models" not in data or not isinstance(data["models"], dict):
        data["models"] = {}

    if "providers" not in data["models"] or not isinstance(data["models"]["providers"], dict):
        data["models"]["providers"] = {}

    data["models"]["providers"]["gemini_pool"] = GEMINI_PROVIDER
    data["models"]["providers"]["codex_pool"] = CODEX_PROVIDER

    # Backup original config
    backup_path = CONFIG_PATH + ".bak"
    try:
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print("==========================================================")
        print(" ✅ AI Pool Gateways successfully linked in OpenClaw!")
        print("==========================================================")
        print(" 🟢 Gemini Provider: gemini_pool/gemini-3.8-flash-tiered")
        print(" 🟣 Codex Provider:  codex_pool/gpt-6-astra")
        print("==========================================================")
    except Exception as e:
        print(f"❌ Failed to save config: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
