#!/usr/bin/env python3
"""Local Gateway & Rotation Bridge for Claude Code / Anthropic models.

Listens on CLAUDE_BRIDGE_PORT (default 8125) and handles Anthropic Messages API
requests (/v1/messages) with automatic account rotation, token management,
and telemetry.
"""
from __future__ import annotations
import http.server
import json
import os
import socketserver
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'cli'))
sys.path.insert(0, str(ROOT / 'bridges'))

from account_manager import Manager, read_json
from pool_runtime import AccountPool
from request_log import record

POOL = AccountPool('claude')
PORT = int(os.environ.get('CLAUDE_BRIDGE_PORT', 8125))


def get_token(credential: dict) -> tuple[str, str]:
    """Extract auth token / api-key and auth header type."""
    if 'claudeAiOauth' in credential:
        oauth = credential['claudeAiOauth']
        return oauth.get('accessToken', ''), 'Bearer'
    if 'primaryApiKey' in credential:
        return credential['primaryApiKey'], 'x-api-key'
    if 'apiKey' in credential:
        return credential['apiKey'], 'x-api-key'
    return '', 'x-api-key'


class ClaudeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        self.close_connection = True
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from client_identity import authorize, is_loopback
        forwarded = self.headers.get('X-Real-IP') or self.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        peer = forwarded if forwarded else self.client_address[0]
        if not is_loopback(peer) and not authorize(self, 'Claude'):
            return
        path = urllib.parse.urlsplit(self.path).path
        if path in ('/v1/models', '/models'):
            models = [
                {"id": "claude-3-7-sonnet-20250219", "object": "model", "owned_by": "anthropic"},
                {"id": "claude-3-5-sonnet-20241022", "object": "model", "owned_by": "anthropic"},
                {"id": "claude-3-5-haiku-20241022", "object": "model", "owned_by": "anthropic"},
                {"id": "claude-opus-4", "object": "model", "owned_by": "anthropic"},
                {"id": "claude-opus-5", "object": "model", "owned_by": "anthropic"}
            ]
            self._send_json({"object": "list", "data": models})
        elif path in ('/', '/health', '/healthz'):
            self._send_json({"status": "ok", "provider": "claude"})
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        from client_identity import authorize
        if not authorize(self, 'Claude'):
            return
        path = urllib.parse.urlsplit(self.path).path
        if path not in ('/v1/messages', '/messages'):
            self._send_json({"error": {"message": f"Unsupported path: {path}", "type": "not_found"}}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self._send_json({"error": {"message": "Invalid Content-Length", "type": "invalid_request"}}, 400)
            return
        if length <= 0:
            self._send_json({"error": {"message": "Empty request body", "type": "invalid_request"}}, 400)
            return

        body_bytes = self.rfile.read(length)
        try:
            payload = json.loads(body_bytes.decode('utf-8'))
        except json.JSONDecodeError:
            self._send_json({"error": {"message": "Invalid JSON body", "type": "invalid_request"}}, 400)
            return

        requested_model = payload.get('model', 'claude-3-7-sonnet-20250219')
        is_stream = payload.get('stream', False)
        
        candidates = POOL.candidates(requested_model)
        if not candidates:
            self._send_json({
                "error": {
                    "message": "No active or available Claude accounts in pool. Run: cc add",
                    "type": "all_accounts_exhausted"
                }
            }, 429)
            return

        upstream_url = os.environ.get("ANTHROPIC_UPSTREAM_URL", "https://api.anthropic.com/v1/messages")
        total_attempts = len(candidates)
        last_status = 502
        last_response_text = ""

        for attempt, number in enumerate(candidates):
            cred = POOL.credentials(number)
            token, auth_type = get_token(cred)
            if not token:
                continue

            # Prepare upstream headers
            headers = {}
            for h, v in self.headers.items():
                hl = h.lower()
                if hl not in ('host', 'content-length', 'authorization', 'x-api-key', 'connection'):
                    headers[h] = v

            if auth_type == 'Bearer':
                headers['Authorization'] = f'Bearer {token}'
            else:
                headers['x-api-key'] = token
                headers['anthropic-version'] = headers.get('anthropic-version', '2023-06-01')

            headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(upstream_url, data=body_bytes, headers=headers, method='POST')

            try:
                res = urllib.request.urlopen(req, timeout=300)
                # Success!
                self.send_response(res.status)
                for k, v in res.headers.items():
                    if k.lower() not in ('connection', 'transfer-encoding', 'content-length'):
                        self.send_header(k, v)
                self.send_header('Connection', 'close')
                self.end_headers()

                # Stream response bytes back directly
                while True:
                    chunk = res.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()

                # Promote successful account
                try:
                    POOL.promote(number)
                except Exception:
                    pass
                return

            except urllib.error.HTTPError as exc:
                last_status = exc.code
                last_response_text = exc.read().decode('utf-8', errors='ignore')
                print(f"[claude-bridge] Account {number} failed with HTTP {exc.code}: {last_response_text[:200]}", file=sys.stderr)
                if exc.code in (401, 403):
                    POOL.exhausted(number, requested_model, 86400, reason='invalid_key')
                    continue
                elif exc.code in (429, 529):
                    # Rate limit or Overloaded
                    POOL.exhausted(number, requested_model, 600, reason='quota_or_overloaded')
                    continue
                else:
                    # Other HTTP error (e.g. 400 Bad Request) - client error, do not failover
                    self.send_response(exc.code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(last_response_text.encode('utf-8'))
                    return

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"[claude-bridge] Network error with account {number}: {exc}", file=sys.stderr)
                last_status = 502
                continue

        # If all candidates exhausted
        self._send_json({
            "error": {
                "message": f"All Claude accounts exhausted. Last status: {last_status}",
                "type": "all_accounts_exhausted",
                "details": last_response_text
            }
        }, 429)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_server(port=PORT):
    server = ThreadedHTTPServer(('127.0.0.1', port), ClaudeHandler)
    print(f"[claude-bridge] Listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == '__main__':
    run_server()
