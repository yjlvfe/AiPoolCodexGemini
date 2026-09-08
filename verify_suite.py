#!/usr/bin/env python3
import urllib.request
import json
import sys
import os

def main():
    base_dir = "/root/Projects/AiPoolCodexGemini"
    
    # 1. Structure Verification
    required_files = [
        "install.sh",
        "README.md",
        "config.env",
        "bridges/gemini_bridge.py",
        "bridges/codex_bridge.py",
        "dashboard/server.py",
        "dashboard/app.py",
        "dashboard/bot_service.py",
        "cli/ag",
        "cli/cx",
        "systemd/ai-gemini-bridge.service",
        "systemd/ai-codex-bridge.service",
        "systemd/ai-dashboard.service",
        "systemd/ai-bot.service"
    ]
    for rel in required_files:
        p = os.path.join(base_dir, rel)
        assert os.path.exists(p), f"Missing file: {p}"
    print("[1/4] Files and structure verified.")

    # 2. Verify Gemini Bridge Port 8123
    try:
        with urllib.request.urlopen("http://127.0.0.1:8123/v1/models", timeout=4) as resp:
            assert resp.status == 200, f"Gemini bridge returned status {resp.status}"
            data = json.load(resp)
            assert "data" in data and len(data["data"]) > 0, "No models found on Gemini bridge"
            print(f"[2/4] Gemini Bridge HTTP 200 OK ({len(data['data'])} models).")
    except Exception as e:
        print(f"Gemini Bridge check failed: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Verify Codex Bridge Port 8124
    try:
        with urllib.request.urlopen("http://127.0.0.1:8124/v1/models", timeout=4) as resp:
            assert resp.status == 200, f"Codex bridge returned status {resp.status}"
            data = json.load(resp)
            assert "data" in data and len(data["data"]) > 0, "No models found on Codex bridge"
            print(f"[3/4] Codex Bridge HTTP 200 OK ({len(data['data'])} models).")
    except Exception as e:
        print(f"Codex Bridge check failed: {e}", file=sys.stderr)
        sys.exit(1)

    # 4. Verify Dashboard Port 8444
    try:
        with urllib.request.urlopen("http://127.0.0.1:8444/api/logs_data", timeout=4) as resp:
            assert resp.status == 200, f"Dashboard returned status {resp.status}"
            data = json.load(resp)
            assert "recent_requests" in data, "No recent_requests in dashboard response"
            print(f"[4/4] Dashboard HTTP 200 OK ({len(data['recent_requests'])} requests).")
    except Exception as e:
        print(f"Dashboard check failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("AI_POOL_SUITE_VERIFIED_SUCCESSFULLY")
    sys.exit(0)

if __name__ == "__main__":
    main()
