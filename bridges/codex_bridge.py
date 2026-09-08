"""
Codex Gateway Pool Bridge Service (Port 8124)
Provides standard OpenAI-compatible API bridge for Codex/ChatGPT backend.
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import urllib.parse
import json
import os
import sys
import glob
import sqlite3
import datetime

HOST = "127.0.0.1"
PORT = 8124
UPSTREAM_URL = "https://chatgpt.com/backend-api/codex"
ACCOUNTS_DIR = os.environ.get("CODEX_ACCOUNT_STORE", os.path.expanduser("~/.codex-accounts"))
ACTIVE_FILE = os.path.join(ACCOUNTS_DIR, "active")
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("AUTH_DB_PATH", os.path.join(_BASE_DIR, "dashboard", "auth.db"))

_active_account = None

def get_registered_accounts():
    files = glob.glob(os.path.join(ACCOUNTS_DIR, "*"))
    accs = []
    for f in files:
        b = os.path.basename(f)
        if b.isdigit():
            accs.append(int(b))
    accs.sort()
    return accs if accs else [1]

def get_active_account_num():
    global _active_account
    if _active_account is not None:
        return _active_account
    if os.path.exists(ACTIVE_FILE):
        try:
            with open(ACTIVE_FILE, "r") as f:
                c = f.read().strip()
                if c.isdigit():
                    _active_account = int(c)
                    return _active_account
        except Exception:
            pass
    _active_account = 1
    return 1

def get_account_token(acc_num):
    acc_path = os.path.join(ACCOUNTS_DIR, str(acc_num))
    if not os.path.exists(acc_path):
        return None
    try:
        with open(acc_path, "r") as f:
            data = json.load(f)
            return data.get("access_token") or data.get("token")
    except Exception as e:
        print(f"[codex-bridge] Error reading account {acc_num}: {e}", file=sys.stderr)
        return None

def record_token_metrics(model, prompt_tokens, completion_tokens, status="OK"):
    try:
        total_tokens = prompt_tokens + completion_tokens
        now_str = datetime.datetime.now().strftime("%m/%d %H:%M")
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO request_events (provider, model, prompt_tokens, completion_tokens, total_tokens, status, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("Codex", model, prompt_tokens, completion_tokens, total_tokens, status, now_str))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[codex-bridge] Failed to record request metrics: {e}", file=sys.stderr)

def switch_active_account(target_acc):
    global _active_account
    _active_account = target_acc
    try:
        with open(ACTIVE_FILE, "w") as f:
            f.write(str(target_acc) + "\n")
        print(f"[codex-bridge] Switched active account to {target_acc}", file=sys.stderr)
    except Exception as e:
        print(f"[codex-bridge] Failed to update active account: {e}", file=sys.stderr)

class CodexHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path in ["/v1/models", "/models"]:
            models = [
                {"id": "gpt-6-astra", "object": "model", "owned_by": "openai-codex"},
                {"id": "gpt-5.6-luna", "object": "model", "owned_by": "openai-codex"},
                {"id": "gpt-5.6-sol", "object": "model", "owned_by": "openai-codex"},
                {"id": "gpt-5.4-mini", "object": "model", "owned_by": "openai-codex"}
            ]
            resp = json.dumps({"object": "list", "data": models}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return
        elif self.path in ["/health", "/"]:
            resp = b'{"status": "ok", "service": "codex-bridge"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        global _active_account
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)

        try:
            req_data = json.loads(body.decode("utf-8"))
        except Exception:
            req_data = {}

        model = req_data.get("model", "gpt-6-astra")
        path = self.path
        target_path = "/responses" if "responses" in path else path

        accounts = get_registered_accounts()
        current_acc = get_active_account_num()
        if current_acc not in accounts:
            current_acc = accounts[0]

        last_resp = None
        last_status = 500
        last_body = b""

        for i in range(len(accounts)):
            acc = accounts[(accounts.index(current_acc) + i) % len(accounts)]
            token = get_account_token(acc)
            if not token:
                continue

            upstream_req = urllib.request.Request(
                f"{UPSTREAM_URL}{target_path}",
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Codex/0.153.4"
                }
            )

            try:
                with urllib.request.urlopen(upstream_req, timeout=120) as resp:
                    last_status = resp.status
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        if k.lower() not in ["transfer-encoding", "content-length"]:
                            self.send_header(k, v)
                    self.end_headers()

                    streamed_bytes = bytearray()
                    while True:
                        chunk = resp.read(4096)
                        if not chunk:
                            break
                        streamed_bytes.extend(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()

                    try:
                        resp_text = streamed_bytes.decode("utf-8", errors="ignore")
                        p_tok = 0
                        c_tok = 0
                        for line in resp_text.splitlines():
                            if line.startswith("data: ") and not line.startswith("data: [DONE]"):
                                try:
                                    chunk_json = json.loads(line[6:])
                                    usage = chunk_json.get("usage")
                                    if usage:
                                        p_tok = usage.get("prompt_tokens", p_tok)
                                        c_tok = usage.get("completion_tokens", c_tok)
                                except Exception:
                                    pass

                        if p_tok == 0 and c_tok == 0:
                            p_tok = len(body) // 4
                            c_tok = len(streamed_bytes) // 4

                        record_token_metrics(model, p_tok, c_tok, status="OK")
                    except Exception as e:
                        print(f"[codex-bridge] Metrics parse error: {e}", file=sys.stderr)

                    if acc != current_acc:
                        switch_active_account(acc)
                    return

            except urllib.error.HTTPError as e:
                last_status = e.code
                last_body = e.read()
                print(f"[codex-bridge] Account {acc} returned HTTP {e.code} for model {model}", file=sys.stderr)

                if e.code in [429, 403, 503] or b"rate_limit" in last_body or b"Usage limit" in last_body:
                    print(f"[codex-bridge] Rate limit hit on Account {acc}! Auto-failing over to next account...", file=sys.stderr)
                    continue
                else:
                    break

            except Exception as e:
                print(f"[codex-bridge] Account {acc} error: {e}", file=sys.stderr)
                continue

        self.send_response(last_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(last_body or json.dumps({"error": f"All {len(accounts)} accounts in Codex pool exhausted."}).encode("utf-8"))

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def run_codex_bridge():
    server = ThreadingHTTPServer((HOST, PORT), CodexHandler)
    print(f"[codex-bridge] Listening on http://{HOST}:{PORT} -> {UPSTREAM_URL}", file=sys.stderr)
    server.serve_forever()

if __name__ == "__main__":
    run_codex_bridge()
