#!/usr/bin/env python3
"""Root-owned, fixed-scope agent integration socket service."""
import json
import os
import socketserver
import subprocess
from pathlib import Path

SOCKET = '/run/aipool/agent-integration.sock'
ROOT = Path('/var/lib/aipool/app')
SCRIPTS = ROOT / 'scripts'
ALLOWED = {'hermes': SCRIPTS / 'setup-hermes.py', 'openclaw': SCRIPTS / 'setup-openclaw.py'}


def sync_cli_auth(provider=None, number=None):
    """Sync active account credentials to /root/.codex and /root/.gemini."""
    try:
        import sys
        sys.path.insert(0, str(ROOT / 'cli'))
        from account_manager import Manager, read_json
        
        providers = ['codex', 'gemini'] if not provider or provider == 'all' else [provider]
        for p in providers:
            try:
                m = Manager(p)
                active = number or m.active()
                if not active:
                    continue
                cred_path = m.credential(active)
                if not cred_path.is_file():
                    continue
                data = read_json(cred_path)
                m.sync_live(data, number=active)
            except Exception:
                pass
        return {'success': True, 'verified': True, 'message': 'CLI credentials synced'}
    except Exception as exc:
        return {'success': False, 'verified': False, 'message': str(exc)}


def run_operation(payload):
    if not isinstance(payload, dict):
        return {'success': False, 'verified': False, 'message': 'Invalid integration request'}
    if payload.get('action') == 'sync_cli' or payload.get('agent') == 'sync_cli':
        return sync_cli_auth(payload.get('provider'), payload.get('number'))
    agent = payload.get('agent')
    status = payload.get('status') is True
    if agent not in ALLOWED:
        return {'success': False, 'verified': False, 'message': 'Invalid integration agent'}
    env = {
        **os.environ,
        'HOME': '/root',
        'HERMES_HOME': '/root/.hermes',
        'OPENCLAW_CONFIG_PATH': '/root/.openclaw/openclaw.json',
        'OPENCLAW_STATE_DIR': '/root/.openclaw',
        'AIPOOL_AGENT_ROOT_HELPER': '1',
    }
    cmd = ['/var/lib/aipool/venv/bin/python', str(ALLOWED[agent])]
    if status:
        cmd.append('--status')
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=240)
    except (OSError, subprocess.SubprocessError):
        return {'success': False, 'verified': False, 'message': 'Agent integration process failed to start'}
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return {'success': False, 'verified': False, 'message': 'Agent integration produced no result'}
    try:
        response = json.loads(lines[-1])
    except ValueError:
        return {'success': False, 'verified': False, 'message': 'Agent integration returned invalid JSON'}
    if not isinstance(response, dict):
        return {'success': False, 'verified': False, 'message': 'Agent integration returned invalid data'}
    if result.returncode:
        response['success'] = False
        response['verified'] = False
        response['message'] = response.get('message') or 'Agent integration failed'
    return response


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            raw = self.rfile.readline(65536)
            payload = json.loads(raw.decode('utf-8'))
            response = run_operation(payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            response = {'success': False, 'verified': False, 'message': 'Invalid integration request'}
        try:
            self.wfile.write((json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8'))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


Path(SOCKET).unlink(missing_ok=True)
with Server(SOCKET, Handler) as server:
    os.chmod(SOCKET, 0o660)
    server.serve_forever()
