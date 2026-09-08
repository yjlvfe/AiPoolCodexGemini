#!/usr/bin/env python3
"""
AiPoolCodexGemini - Automatic Hermes Agent Integration Script
Safely updates ~/.hermes/config.yaml with Gemini and Codex local pool gateways.
"""

import os
import sys
import yaml

CONFIG_PATH = os.path.expanduser("~/.hermes/config.yaml")

GEMINI_PROVIDER = {
    "base_url": "http://127.0.0.1:8123/v1",
    "api_key": "dummy-pool-key",
    "models": [
        {"id": "gemini-3.8-flash-tiered", "display_name": "Gemini 3.8 Flash (Multi-Pool)", "context_window": 1048576},
        {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6 (Via Gemini Pool)", "context_window": 200000},
        {"id": "claude-opus-4-6-thinking", "display_name": "Claude Opus 4.6 Thinking (Via Gemini Pool)", "context_window": 200000}
    ]
}

CODEX_PROVIDER = {
    "base_url": "http://127.0.0.1:8124/v1",
    "api_key": "dummy-pool-key",
    "models": [
        {"id": "gpt-6-astra", "display_name": "GPT-6 Astra (Multi-Pool)", "context_window": 128000},
        {"id": "gpt-5.6-luna", "display_name": "GPT-5.6 Luna (Multi-Pool)", "context_window": 128000},
        {"id": "gpt-5.6-sol", "display_name": "GPT-5.6 Sol (Multi-Pool)", "context_window": 128000}
    ]
}

def main():
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ لم يتم العثور على ملف إعدادات هيرمس في: {CONFIG_PATH}")
        print("تأكد من تثبيت هيرمس أولاً أو تشغيل 'hermes setup'.")
        sys.exit(1)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"❌ خطأ أثناء قراءة {CONFIG_PATH}: {e}")
        sys.exit(1)

    if "custom_providers" not in data or not isinstance(data["custom_providers"], dict):
        data["custom_providers"] = {}

    data["custom_providers"]["gemini"] = GEMINI_PROVIDER
    data["custom_providers"]["codex"] = CODEX_PROVIDER

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        print("==========================================================")
        print(" ✅ تم ربط مزودات الذكاء الاصطناعي بنجاح في Hermes Agent!")
        print("==========================================================")
        print(" 🟢 مزود Gemini: custom:gemini/gemini-3.8-flash-tiered")
        print(" 🟣 مزود Codex:  custom:codex/gpt-6-astra")
        print("----------------------------------------------------------")
        print(" للاستخدام الفوري في هيرمس:")
        print(" hermes model gemini-3.8-flash-tiered")
        print(" أو")
        print(" hermes model gpt-6-astra")
        print("==========================================================")
    except Exception as e:
        print(f"❌ فشل حفظ الإعدادات: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
