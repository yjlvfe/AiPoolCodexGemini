#!/usr/bin/env python3
"""
AiPoolCodexGemini - Automatic Hermes Agent Integration Script
Safely updates ~/.hermes/config.yaml with Gemini and Codex local pool gateways.
"""

import os
import sys
import shutil

CONFIG_PATH = os.path.expanduser("~/.hermes/config.yaml")

GEMINI_PROVIDER = {
    "base_url": "http://127.0.0.1:8123/v1",
    "api_key": "pool-key",
    "models": [
        {"id": "gemini-3.8-flash-tiered", "display_name": "Gemini 3.8 Flash Tiered", "context_window": 1048576},
        {"id": "gemini-2.5-pro", "display_name": "Gemini 2.5 Pro", "context_window": 1048576},
        {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6", "context_window": 200000}
    ]
}

CODEX_PROVIDER = {
    "base_url": "http://127.0.0.1:8124/v1",
    "api_key": "pool-key",
    "models": [
        {"id": "gpt-6-astra", "display_name": "ChatGPT Astra", "context_window": 200000},
        {"id": "gpt-5.6-luna", "display_name": "ChatGPT Luna", "context_window": 200000},
        {"id": "gpt-5.6-sol", "display_name": "ChatGPT Sol", "context_window": 200000}
    ]
}

def main():
    try:
        import yaml
    except ImportError:
        print("❌ Error: PyYAML is required. Run: pip install pyyaml")
        sys.exit(1)

    if not os.path.exists(CONFIG_PATH):
        print(f"❌ Hermes config file not found at: {CONFIG_PATH}")
        print("Please ensure Hermes is installed or run 'hermes setup' first.")
        sys.exit(1)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"❌ Error reading {CONFIG_PATH}: {e}")
        sys.exit(1)

    if "custom_providers" not in data or not isinstance(data["custom_providers"], dict):
        data["custom_providers"] = {}

    data["custom_providers"]["gemini"] = GEMINI_PROVIDER
    data["custom_providers"]["codex"] = CODEX_PROVIDER

    # Backup original config
    backup_path = CONFIG_PATH + ".bak"
    try:
        shutil.copyfile(CONFIG_PATH, backup_path)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print("==========================================================")
        print(" ✅ AI Pool Gateways successfully linked in Hermes Agent!")
        print("==========================================================")
        print(" 🟢 Gemini Provider: custom:gemini/gemini-3.8-flash-tiered")
        print(" 🟣 Codex Provider:  custom:codex/gpt-6-astra")
        print("----------------------------------------------------------")
        print(" To test immediately in Hermes:")
        print("   hermes model custom:gemini/gemini-3.8-flash-tiered")
        print(" or")
        print("   hermes model custom:codex/gpt-6-astra")
        print("==========================================================")
    except Exception as e:
        print(f"❌ Failed to save config: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
