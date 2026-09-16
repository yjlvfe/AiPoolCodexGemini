"""
Web Dashboard Server Pro (Luxury OLED Glass Theme, Custom Animated Dropdowns, Micro-Interactions)
"""
import http.server
import socketserver
import urllib.parse
import json
import time
import os
import socket
from http import cookies
import subprocess
import sys
import secrets
import hashlib
import sqlite3
from pathlib import Path
BRIDGES_DIR = str(Path(__file__).resolve().parents[1] / 'bridges')
if BRIDGES_DIR not in sys.path:
    sys.path.insert(0, BRIDGES_DIR)
SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / 'scripts')
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
from app import PoolManager, TokenAuthManager, is_link_preview_user_agent, normalize_client_ip

PORT = int(os.environ.get('DASHBOARD_PORT', '8444'))
auth_manager = TokenAuthManager()
HTML_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AI Pools Gateway • Access Restricted</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Cairo:wght@600;700;800;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #06080d;
            --card-bg: rgba(13, 17, 26, 0.75);
            --card-border: rgba(255, 255, 255, 0.08);
            --accent-cyan: #00f2fe;
            --accent-purple: #9d4edd;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        body {
            background-color: var(--bg-base);
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(0, 242, 254, 0.08) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(157, 78, 221, 0.08) 0%, transparent 40%);
            color: var(--text-main);
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-card {
            background: var(--card-bg);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid var(--card-border);
            border-radius: 28px;
            padding: 36px 28px;
            width: 100%;
            max-width: 420px;
            text-align: center;
            box-shadow: 0 24px 64px -12px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.1);
            animation: floatUp 0.6s cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes floatUp {
            from { opacity: 0; transform: translateY(24px) scale(0.98); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        .icon-wrap {
            width: 76px;
            height: 76px;
            margin: 0 auto 20px;
            border-radius: 22px;
            background: linear-gradient(135deg, rgba(0, 242, 254, 0.15), rgba(157, 78, 221, 0.15));
            border: 1px solid rgba(255, 255, 255, 0.12);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 34px;
            box-shadow: 0 12px 24px -6px rgba(0, 242, 254, 0.2);
        }
        h1 { font-size: 24px; font-weight: 900; margin-bottom: 8px; letter-spacing: -0.5px; }
        p { font-size: 14px; color: var(--text-muted); line-height: 1.6; margin-bottom: 24px; }
        .hint-badge {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 12px 16px;
            border-radius: 14px;
            font-size: 13px;
            color: #cbd5e1;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="icon-wrap">🛡️</div>
        <h1>AI Pool Gateway</h1>
        <p>Access restricted and secured by encrypted device session.<br>Please request an authentication magic link via Telegram bot.</p>
        <div class="hint-badge">
            <span>🤖 Send /start to</span>
            <strong style="color: var(--accent-cyan);">@YJReportbot</strong>
        </div>
    </div>
</body>
</html>
"""

from pathlib import Path as TemplatePath
HTML_LOGS_TEMPLATE = (TemplatePath(__file__).parent / 'templates/dashboard.html').read_text(encoding='utf-8')


def _safe_json_for_script(value):
    """Encode data for a script block without allowing HTML/script breakout."""
    return (json.dumps(value, ensure_ascii=False)
            .replace('<', '\\u003c')
            .replace('>', '\\u003e')
            .replace('&', '\\u0026')
            .replace('\u2028', '\\u2028')
            .replace('\u2029', '\\u2029'))


def _validated_quota(value):
    """Normalize a quota while preserving zero/None as unlimited."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Quota values must be zero or positive integers")
    try:
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, float):
            if not value.is_integer():
                raise ValueError
            parsed = int(value)
        elif isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
        else:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Quota values must be zero or positive integers") from None
    if parsed < 0:
        raise ValueError("Quota values must be zero or positive integers")
    return parsed or None


def _resolved_legacy_client_id(clients, label):
    value = str(label or "").strip().lower()
    if not value:
        return None
    candidates = {
        str(client.get("id") or "").strip().lower()
        for client in clients
        if str(client.get("id") or "").strip().lower() == value
        or str(client.get("name") or "").strip().lower() == value
    }
    candidates.discard("")
    return next(iter(candidates)) if len(candidates) == 1 else None


def _migrate_legacy_client_counter(db_path, legacy_label, client_id):
    """Move a uniquely owned legacy label counter to its stable key ID."""
    legacy_key = str(legacy_label or '').strip().lower()
    stable_key = str(client_id or '').strip().lower()
    if not legacy_key or not stable_key or legacy_key == stable_key or not os.path.isfile(db_path):
        return
    with sqlite3.connect(db_path, timeout=5) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            legacy = conn.execute(
                "SELECT * FROM client_usage_counters WHERE client_id=?", (legacy_key,)
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return
            raise
        if legacy:
            current = conn.execute(
                "SELECT * FROM client_usage_counters WHERE client_id=?", (stable_key,)
            ).fetchone()
            if current:
                conn.execute(
                    """UPDATE client_usage_counters SET
                       total_tokens=?, prompt_tokens=?, completion_tokens=?, requests=?,
                       codex_tokens=?, gemini_tokens=?, created_at=?, updated_at=?
                       WHERE client_id=?""",
                    (
                        int(current['total_tokens'] or 0) + int(legacy['total_tokens'] or 0),
                        int(current['prompt_tokens'] or 0) + int(legacy['prompt_tokens'] or 0),
                        int(current['completion_tokens'] or 0) + int(legacy['completion_tokens'] or 0),
                        int(current['requests'] or 0) + int(legacy['requests'] or 0),
                        int(current['codex_tokens'] or 0) + int(legacy['codex_tokens'] or 0),
                        int(current['gemini_tokens'] or 0) + int(legacy['gemini_tokens'] or 0),
                        min(float(current['created_at']), float(legacy['created_at'])),
                        max(float(current['updated_at']), float(legacy['updated_at'])),
                        stable_key,
                    ),
                )
                conn.execute("DELETE FROM client_usage_counters WHERE client_id=?", (legacy_key,))
            else:
                conn.execute(
                    "UPDATE client_usage_counters SET client_id=? WHERE client_id=?",
                    (stable_key, legacy_key),
                )
        try:
            events = conn.execute(
                "SELECT id,audit_json FROM request_events WHERE audit_json IS NOT NULL AND audit_json != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            events = []
        for event in events:
            try:
                audit = json.loads(event['audit_json'])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if audit.get('client_id') or str(audit.get('client_label') or '').strip().lower() != legacy_key:
                continue
            audit['client_id'] = stable_key
            conn.execute(
                "UPDATE request_events SET audit_json=? WHERE id=?",
                (json.dumps(audit, ensure_ascii=False), event['id']),
            )
        conn.commit()


GLOBAL_POOL_MANAGER = PoolManager()

def get_settings_status():
    import integrations
    res = {agent: integrations.status(agent) for agent in ('hermes','openclaw')}
    res['version'] = get_system_version()
    return res


def integrate_agent(agent):
    import integrations
    try:
        return integrations.integrate(agent)
    except (OSError, ValueError, TypeError) as exc:
        return {'success':False, 'verified':False, 'message':str(exc)}


def get_cli_tools_status():
    from account_manager import find_codex
    codex={'installed':False,'path':'','version':''}
    try:
        binary=find_codex()
        result=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=10)
        codex={'installed':result.returncode==0,'path':binary,'version':result.stdout.strip()}
    except (OSError,ValueError,subprocess.SubprocessError):
        pass
    return {'codex':codex,'gemini':{'installed':True,'path':'Built-in OAuth','version':'No external CLI required'}}


import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import update_suite
RUNNING_BUILD = update_suite.version()

def get_registry_file() -> Path:
    env_path = os.environ.get("AIPOOL_CLIENT_REGISTRY")
    if env_path:
        p = Path(env_path)
        if p.is_file() or p.parent.is_dir():
            return p
    runtime_path = Path("/var/lib/aipool/runtime/client-identities.json")
    if runtime_path.is_file() or runtime_path.parent.is_dir():
        return runtime_path
    return Path(__file__).resolve().parent / "client-identities.json"


def get_system_version():
    result = update_suite.version()
    result['running_commit'] = RUNNING_BUILD.get('commit')
    result['running_version'] = RUNNING_BUILD.get('version')
    try:
        result['last_update'] = json.loads(update_suite.result_path().read_text())
    except (OSError, ValueError):
        result['last_update'] = None
    return result

class RequestBodyTimeout(ValueError):
    pass


class ProDashboardHandler(http.server.BaseHTTPRequestHandler):
    MAX_BODY_BYTES = 1 * 1024 * 1024

    def log_message(self, format, *args):
        # Mute normal GET logs to prevent terminal spam
        return

    def send_json_response(self, data, status_code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _read_json_body(self):
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
        except (TypeError, ValueError):
            raise ValueError('Invalid Content-Length') from None
        if length < 0 or length > self.MAX_BODY_BYTES:
            raise ValueError('Request body is too large')
        if not length:
            return {}
        connection = getattr(self, 'connection', None)
        try:
            if connection is not None:
                connection.settimeout(30.0)
            raw = self.rfile.read(length)
        except (socket.timeout, TimeoutError):
            self.close_connection = True
            raise RequestBodyTimeout('Request body read timed out') from None
        finally:
            try:
                if connection is not None:
                    connection.settimeout(None)
            except OSError:
                pass
        try:
            value = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Request body must be valid JSON') from None
        if not isinstance(value, dict):
            raise ValueError('Request body must be a JSON object')
        return value

    def _client_ip(self):
        return normalize_client_ip(self.headers, self.client_address[0])

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        client_ip = self._client_ip()

        # Sessions are carried only by the shared HttpOnly cookie.
        cookie_header = self.headers.get("Cookie")
        session_cookie = None
        if cookie_header:
            c = cookies.SimpleCookie()
            try:
                c.load(cookie_header)
                if "yj_aipool_session" in c:
                    session_cookie = c["yj_aipool_session"].value
            except Exception:
                pass

        auth_mgr = TokenAuthManager()
        is_auth = False

        # Allow localhost / loopback internally
        if self.client_address[0] in ('127.0.0.1','::1') and not self.headers.get('X-Forwarded-For') and not self.headers.get('X-Real-IP'):
            is_auth = True
        elif session_cookie and auth_mgr.validate_device(session_cookie, client_ip):
            is_auth = True

        magic_token = qs.get("token", [""])[0] if path == "/auth" else ""
        if magic_token:
            # Existing sessions win and link previews must never consume the token.
            if is_auth:
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if is_link_preview_user_agent(self.headers.get("User-Agent", "")):
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            new_session = auth_mgr.register_device(
                magic_token, client_ip, self.headers.get("User-Agent", "")
            )
            if new_session:
                session = cookies.SimpleCookie()
                session["yj_aipool_session"] = new_session
                morsel = session["yj_aipool_session"]
                morsel["path"] = "/"
                morsel["max-age"] = str(auth_mgr.session_expiry_seconds)
                morsel["httponly"] = True
                morsel["samesite"] = "Lax"
                if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
                    morsel["secure"] = True
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", session.output(header="").strip())
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        sensitive_query_keys = {"session", "session_id", "token"}
        if sensitive_query_keys.intersection(qs) and path != "/auth":
            self.send_response(302)
            self.send_header("Location", path or "/")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # Unauthenticated handling
        if not is_auth:
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            body = HTML_LOGIN_TEMPLATE.encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        pm = GLOBAL_POOL_MANAGER

        if path in ('/aipool/assets/update.js', '/assets/update.js'):
            with open(os.path.join(os.path.dirname(__file__), 'update_ui.js'), 'rb') as handle:
                body = handle.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API: logs and machine-readable gateway metrics
        if path in ("/aipool/api/logs_data", "/api/logs_data", "/aipool/api/report", "/api/report"):
            self.send_json_response(pm.get_usage_logs_report())
            return
        if path in ("/metrics", "/aipool/api/metrics", "/api/metrics"):
            report = pm.get_usage_logs_report()
            metrics = report.get('metrics', {})
            self.send_json_response({'metrics': metrics, 'providers': report.get('providers', {}), 'generated_at': time.time()})
            return

        # API: get prompt on-demand (dual original / processed inspector)
        if path in ("/aipool/api/request_prompt", "/api/request_prompt"):
            req_id = qs.get("id", [""])[0] or qs.get("request_id", [""])[0]
            if not req_id and "?" in path:
                # Fallback for malformed query strings
                from urllib.parse import parse_qs
                req_id = parse_qs(path.split("?", 1)[1]).get("id", [""])[0]
            if req_id and "?" in req_id:
                req_id = req_id.split("?")[0]
            self.send_json_response(pm.get_request_prompt(req_id))
            return

        # API: force refresh
        if path in ("/aipool/api/refresh", "/api/refresh"):
            pm.trigger_instant_refresh()
            self.send_json_response(pm.get_usage_logs_report())
            return

        # API: pool status data
        if path in ("/aipool/api/status", "/api/status"):
            self.send_json_response(pm.get_all_status())
            return

        # API: settings status
        if path in ("/aipool/api/settings/status", "/api/settings/status"):
            st = get_settings_status()
            self.send_json_response(st)
            return

        # API: system version
        if path in ("/aipool/api/settings/version", "/api/settings/version"):
            self.send_json_response(get_system_version())
            return

        # API: app build hash / live reload poll
        if path in ("/aipool/api/build-hash", "/api/build-hash"):
            tmpl_path = Path(__file__).resolve().parent / "templates/dashboard.html"
            mtime = tmpl_path.stat().st_mtime if tmpl_path.exists() else 0
            self.send_json_response({"build_hash": f"{RUNNING_BUILD.get('commit', '')}_{mtime}"})
            return

        # API: tokens management
        if path in ("/aipool/api/tokens/list", "/api/tokens/list"):
            registry_file = get_registry_file()
            try:
                data = json.loads(registry_file.read_text()) if registry_file.is_file() else {"mode": "enforce", "clients": []}
                clients = []
                system_keys = ("hermes", "openclaw", "localtooling")

                # Compute aggregated token usage per client from client_usage_counters and request_events DB
                usage_by_client = {}
                db_path = os.environ.get("AUTH_DB_PATH") or "/var/lib/aipool/runtime/auth.db"
                if os.path.isfile(db_path):
                    try:
                        import sqlite3
                        with sqlite3.connect(db_path, timeout=5) as conn:
                            conn.execute("PRAGMA busy_timeout=10000")
                            conn.execute("PRAGMA journal_mode=WAL")
                            conn.row_factory = sqlite3.Row
                            # First load historical persistent counters
                            try:
                                for r in conn.execute("SELECT client_id, total_tokens FROM client_usage_counters"):
                                    cid = (r["client_id"] or "").lower()
                                    if cid:
                                        usage_by_client[cid] = int(r["total_tokens"] or 0)
                            except Exception:
                                pass
                            # Then add any active request_events not yet rolled into counters (or update)
                            cur = conn.execute("SELECT audit_json, total_tokens FROM request_events WHERE audit_json IS NOT NULL AND audit_json != ''")
                            ev_counts = {}
                            for r in cur.fetchall():
                                try:
                                    aud = json.loads(r["audit_json"])
                                    identity_key = str(aud.get("client_id") or "").strip().lower()
                                    if not identity_key:
                                        identity_key = _resolved_legacy_client_id(
                                            data.get("clients", []), aud.get("client_label")
                                        ) or ""
                                    if identity_key:
                                        toks = int(r["total_tokens"] or 0)
                                        ev_counts[identity_key] = ev_counts.get(identity_key, 0) + toks
                                except Exception:
                                    pass
                            for k, v in ev_counts.items():
                                usage_by_client[k] = max(usage_by_client.get(k, 0), v)
                    except Exception:
                        pass

                for c in data.get("clients", []):
                    # Hide internal system keys from public API page
                    if c.get("id") in system_keys:
                        continue
                    c_name = c.get("name", "")
                    c_id = c.get("id", "")
                    used = usage_by_client.get(c_id.lower())
                    if used is None and _resolved_legacy_client_id(data.get("clients", []), c_name) == c_id.lower():
                        used = usage_by_client.get(c_name.lower(), 0)
                    used = used or 0
                    # Check expired status dynamically
                    expires_at = c.get("expires_at")
                    is_expired = False
                    if expires_at and time.time() > float(expires_at):
                        is_expired = True

                    clients.append({
                        "id": c.get("id"),
                        "name": c_name,
                        "raw_token": c.get("raw_token"),  # Persistent token for copying anytime
                        "enabled": c.get("enabled", True),
                        "expired": is_expired,
                        "expires_at": expires_at,
                        "refill_period": c.get("refill_period"),
                        "refill_period_seconds": c.get("refill_period_seconds"),
                        "refill_unit": c.get("refill_unit"),
                        "refill_val": c.get("refill_val"),
                        "expiration_duration": c.get("expiration_duration"),
                        "expiration_unit": c.get("expiration_unit"),
                        "expiration_val": c.get("expiration_val"),
                        "created_at": c.get("created_at", "Custom Key"),
                        "total_tokens": used,
                        "max_tokens": c.get("max_tokens"),
                        "allowed_providers": c.get("allowed_providers", ["codex", "gemini"]),
                        "allowed_models": c.get("allowed_models", []),
                        "created_at_ts": c.get("created_at_ts"),
                        "updated_at_ts": c.get("updated_at_ts"),
                        "token_limits": c.get("token_limits", {})
                    })
                self.send_json_response({"success": True, "tokens": clients})
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        # API: check for updates from remote GitHub
        if path in ("/aipool/api/settings/check_update", "/api/settings/check_update"):
            self.send_json_response(update_suite.check_updates())
            return

        # API: check CLI tools status
        if path in ("/aipool/api/settings/check_cli", "/api/settings/check_cli"):
            self.send_json_response(get_cli_tools_status())
            return

        if path in ("/aipool/api/tokens/revoke", "/api/tokens/revoke"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=408 if isinstance(exc, RequestBodyTimeout) else 400)
                return
            target_id = str(payload.get("id", "")).strip()
            if target_id in ("hermes", "openclaw", "localtooling"):
                self.send_json_response({"success": False, "message": "Cannot revoke system internal keys"}, status_code=403)
                return
            registry_file = get_registry_file()
            try:
                data = json.loads(registry_file.read_text()) if registry_file.is_file() else {"mode": "enforce", "clients": []}
                clients = [c for c in data.get("clients", []) if c.get("id") != target_id]
                data["clients"] = clients
                registry_file.write_text(json.dumps(data, indent=2))
                self.send_json_response({"success": True, "message": f"Token {target_id} revoked"})
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        # Main view / Single Page Routing: /, /api, /settings
        if path in ("/", "/aipool", "/aipool/", "/api", "/api/", "/settings", "/settings/"):
            pass
        elif not path.startswith("/api/"):
            # Unknown page -> serve app (SPA fallback)
            pass
        # Determine active view from requested URL path for zero-flicker SSR rendering!
        active_view = "dashboard"
        norm_path = path.lower().rstrip("/")
        if norm_path.endswith("/api"):
            active_view = "api"
        elif norm_path.endswith("/settings"):
            active_view = "settings"

        initial_data = pm.get_usage_logs_report()
        initial_json = _safe_json_for_script(initial_data)
        injected_script = f"""<script>
            window.__INITIAL_DATA__ = {initial_json};
            window.__ACTIVE_VIEW__ = "{active_view}";
        </script>"""
        
        # Pre-apply CSS display rules server-side so there is ZERO 1-second flicker
        html_rendered = HTML_LOGS_TEMPLATE
        if active_view == "api":
            html_rendered = html_rendered.replace('id="provider-segmented-bar"', 'id="provider-segmented-bar" style="display:none;"')
            html_rendered = html_rendered.replace('id="pool-overview-section"', 'id="pool-overview-section" style="display:none;"')
            html_rendered = html_rendered.replace('id="stream-section"', 'id="stream-section" style="display:none;"')
            html_rendered = html_rendered.replace('id="api-view-section" style="display: none;', 'id="api-view-section" style="display: flex;')
            html_rendered = html_rendered.replace('id="api-page-toggle-btn" class="settings-top-btn api-nav-btn"', 'id="api-page-toggle-btn" class="settings-top-btn api-nav-btn active"')
        elif active_view == "settings":
            html_rendered = html_rendered.replace('id="provider-segmented-bar"', 'id="provider-segmented-bar" style="display:none;"')
            html_rendered = html_rendered.replace('id="pool-overview-section"', 'id="pool-overview-section" style="display:none;"')
            html_rendered = html_rendered.replace('id="stream-section"', 'id="stream-section" style="display:none;"')
            html_rendered = html_rendered.replace('id="settings-view-section" style="display: none;', 'id="settings-view-section" style="display: flex;')
            html_rendered = html_rendered.replace('id="settings-toggle-btn" class="settings-top-btn"', 'id="settings-toggle-btn" class="settings-top-btn active"')

        html_to_serve = html_rendered.replace("</head>", f"{injected_script}\n</head>")
        html_to_serve = html_to_serve.replace("</body>", '<script src="/aipool/assets/update.js"></script></body>')
        body = html_to_serve.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get('Host'):
            self.send_json_response({'success': False, 'message': 'Cross-origin writes are not allowed'},403)
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        client_ip = self._client_ip()

        # Check session if auth DB exists
        cookie_header = self.headers.get("Cookie")
        session_cookie = None
        if cookie_header:
            c = cookies.SimpleCookie()
            try:
                c.load(cookie_header)
                if "yj_aipool_session" in c:
                    session_cookie = c["yj_aipool_session"].value
            except Exception:
                pass

        effective_session = session_cookie

        auth_mgr = TokenAuthManager()
        is_auth = False
        if self.client_address[0] in ('127.0.0.1','::1') and not self.headers.get('X-Forwarded-For') and not self.headers.get('X-Real-IP'):
            is_auth = True
        elif effective_session and auth_mgr.validate_device(effective_session, client_ip):
            is_auth = True

        if not is_auth:
            self.send_json_response({"error": "Unauthorized"}, status_code=401)
            return

        pm = GLOBAL_POOL_MANAGER

        if path in ("/aipool/api/settings/integrate_hermes", "/api/settings/integrate_hermes"):
            self.send_json_response(integrate_agent("hermes"))
            return

        if path in ("/aipool/api/settings/integrate_openclaw", "/api/settings/integrate_openclaw"):
            self.send_json_response(integrate_agent("openclaw"))
            return



        if path in ("/aipool/api/accounts/switch", "/api/accounts/switch"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=408 if isinstance(exc, RequestBodyTimeout) else 400)
                return
            system = payload.get("system", "")
            try:
                account = int(payload.get("account", 0))
            except (TypeError, ValueError):
                account = 0
            if system not in ("gemini", "codex") or account < 1:
                self.send_json_response({"success": False, "message": "Invalid system or account number."}, status_code=400)
                return
            ok, detail = pm.switch_account(system, account)
            if not ok:
                self.send_json_response({"success": False, "message": detail or "Switch failed."}, status_code=502)
                return
            self.send_json_response({"success": True, "message": detail, "system": system, "account": account})
            return

        if path in ("/aipool/api/accounts/delete", "/api/accounts/delete"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=408 if isinstance(exc, RequestBodyTimeout) else 400)
                return
            system = payload.get("system", "")
            try:
                account = int(payload.get("account", 0))
            except (TypeError, ValueError):
                account = 0
            confirmation = payload.get("confirmation", "")
            if system not in ("gemini", "codex") or account < 1:
                self.send_json_response({"success": False, "message": "Invalid system or account number."}, status_code=400)
                return
            if str(confirmation).strip().lower() != "confirm":
                self.send_json_response({"success": False, "message": "You must type confirm to verify deletion."}, status_code=400)
                return
            ok, detail = pm.delete_account(system, account, confirmation)
            if not ok:
                self.send_json_response({"success": False, "message": detail or "Delete failed."}, status_code=400)
                return
            self.send_json_response({"success": True, "message": detail, "system": system, "account": account})
            return

        if path in ("/aipool/api/settings/update", "/api/settings/update"):
            self.send_json_response({'success': False, 'message': 'Forced remote updates are disabled to protect local modifications and model locking.'}, status_code=403)
            return

        if path in ("/aipool/api/settings/install_cli", "/api/settings/install_cli"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=408 if isinstance(exc, RequestBodyTimeout) else 400)
                return
            tool = payload.get("tool", "")
            if tool not in ("codex", "gemini"):
                self.send_json_response({"success": False, "message": "Invalid tool specified."}, status_code=400)
                return
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_path = os.path.join(base_dir, "scripts", "install-cli-tools.sh")
            try:
                res = subprocess.run(["bash", script_path, "install", tool], capture_output=True, text=True, timeout=120)
                ok = (res.returncode == 0)
                msg = res.stdout.strip() if ok else (res.stderr.strip() or res.stdout.strip())
                self.send_json_response({"success": ok, "message": msg, "status": get_cli_tools_status()})
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        if path in ("/aipool/api/tokens/list", "/api/tokens/list"):
            registry_file = get_registry_file()
            try:
                data = json.loads(registry_file.read_text()) if registry_file.is_file() else {"mode": "enforce", "clients": []}
                clients = []
                for c in data.get("clients", []):
                    clients.append({
                        "id": c.get("id"),
                        "name": c.get("name"),
                        "enabled": c.get("enabled", True),
                        "created_at": c.get("created_at", "System"),
                    })
                self.send_json_response({"success": True, "tokens": clients})
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        if path in ("/aipool/api/tokens/create", "/api/tokens/create"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=408 if isinstance(exc, RequestBodyTimeout) else 400)
                return
            name = str(payload.get("name", "")).strip() or "Unnamed Key"
            new_id = "key_" + secrets.token_hex(4)
            raw_token = "sk-aipool-" + secrets.token_hex(16)
            digest = hashlib.sha256(raw_token.encode()).hexdigest()

            registry_file = get_registry_file()
            try:
                data = json.loads(registry_file.read_text()) if registry_file.is_file() else {"mode": "enforce", "clients": []}
                clients = data.get("clients", [])
                clients.append({
                    "id": new_id,
                    "name": name,
                    "raw_token": raw_token,
                    "enabled": True,
                    "created_at": time.strftime("%Y-%m-%d %H:%M"),
                    "created_at_ts": time.time(),
                    "updated_at_ts": time.time(),
                    "change_history": [{"timestamp": time.time(), "action": "created", "changes": {}}],
                    "sha256": digest
                })
                data["clients"] = clients
                registry_file.write_text(json.dumps(data, indent=2))
                self.send_json_response({
                    "success": True,
                    "token": raw_token,
                    "id": new_id,
                    "name": name
                })
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        if path in ("/aipool/api/tokens/revoke", "/api/tokens/revoke"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=408 if isinstance(exc, RequestBodyTimeout) else 400)
                return
            target_id = str(payload.get("id", "")).strip()
            if target_id in ("hermes", "openclaw", "localtooling"):
                self.send_json_response({"success": False, "message": "Cannot revoke system internal keys"}, status_code=403)
                return
            registry_file = get_registry_file()
            try:
                data = json.loads(registry_file.read_text()) if registry_file.is_file() else {"mode": "enforce", "clients": []}
                clients = [c for c in data.get("clients", []) if c.get("id") != target_id]
                data["clients"] = clients
                registry_file.write_text(json.dumps(data, indent=2))
                self.send_json_response({"success": True, "message": f"Token {target_id} revoked"})
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        if path in ("/aipool/api/tokens/details", "/api/tokens/details"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=408 if isinstance(exc, RequestBodyTimeout) else 400)
                return
            target_id = str(payload.get("id", "")).strip()
            registry_file = get_registry_file()
            try:
                data = json.loads(registry_file.read_text()) if registry_file.is_file() else {"mode": "enforce", "clients": []}
                found_client = None
                for c in data.get("clients", []):
                    if c.get("id") == target_id:
                        found_client = c
                        break
                if not found_client:
                    self.send_json_response({"success": False, "message": "Key not found"}, status_code=404)
                    return

                # Gather usage statistics from request_events DB
                c_name = (found_client.get("name") or "").lower()
                c_id = target_id.lower()
                db_path = os.environ.get("AUTH_DB_PATH") or "/var/lib/aipool/runtime/auth.db"

                total_tokens = 0
                total_requests = 0
                by_provider = {"codex": {"tokens": 0, "requests": 0}, "gemini": {"tokens": 0, "requests": 0}}
                by_model = {}

                now = time.time()
                first_used_ts = None
                counter_created_at = None
                key_recent_requests = []
                if os.path.isfile(db_path):
                    try:
                        import sqlite3
                        with sqlite3.connect(db_path, timeout=5) as conn:
                            conn.execute("PRAGMA busy_timeout=10000")
                            conn.execute("PRAGMA journal_mode=WAL")
                            conn.row_factory = sqlite3.Row
                            cur = conn.execute("SELECT id, model, pool, prompt_tokens, completion_tokens, total_tokens, time_formatted, status, timestamp, audit_json FROM request_events WHERE audit_json IS NOT NULL AND audit_json != '' ORDER BY id DESC")
                            for r in cur.fetchall():
                                try:
                                    aud = json.loads(r["audit_json"])
                                    cl = (aud.get("client_label") or "").lower()
                                    cid = (aud.get("client_id") or "").lower()
                                    is_current_identity = bool(c_id and cid == c_id)
                                    is_legacy_identity = bool(
                                        not cid and cl
                                        and _resolved_legacy_client_id(data.get("clients", []), cl) == c_id
                                    )
                                    if is_current_identity or is_legacy_identity:
                                        toks = int(r["total_tokens"] or 0)
                                        m = str(r["model"] or "unknown")
                                        prov = "codex" if str(r["pool"]).lower() in ("codex", "openai") else "gemini"
                                        total_tokens += toks
                                        total_requests += 1
                                        by_provider[prov]["tokens"] += toks
                                        by_provider[prov]["requests"] += 1
                                        by_model[m] = by_model.get(m, 0) + toks
                                        ts = r["timestamp"]
                                        if isinstance(ts, str):
                                            import datetime
                                            ts = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                                        ts_float = float(ts or 0)
                                        if ts_float > 0:
                                            if first_used_ts is None or ts_float < first_used_ts:
                                                first_used_ts = ts_float
                                        key_recent_requests.append({
                                            "id": r["id"],
                                            "call_num": r["id"],
                                            "model": m,
                                            "provider": prov,
                                            "prompt": r["prompt_tokens"],
                                            "completion": r["completion_tokens"],
                                            "tokens": toks,
                                            "time_formatted": r["time_formatted"],
                                            "status": r["status"] or "OK",
                                            "timestamp": ts_float
                                        })
                                except Exception:
                                    pass
                    except Exception:
                        pass

                # Check persistent client_usage_counters to never lose historical counts
                if os.path.isfile(db_path):
                    try:
                        import sqlite3
                        with sqlite3.connect(db_path, timeout=5) as conn:
                            conn.execute("PRAGMA busy_timeout=10000")
                            conn.execute("PRAGMA journal_mode=WAL")
                            conn.row_factory = sqlite3.Row
                            row_c = conn.execute(
                                "SELECT total_tokens, requests, codex_tokens, gemini_tokens, created_at FROM client_usage_counters "
                                "WHERE client_id=?",
                                (c_id,),
                            ).fetchone()
                            if not row_c and _resolved_legacy_client_id(data.get("clients", []), c_name) == c_id:
                                row_c = conn.execute(
                                    "SELECT total_tokens, requests, codex_tokens, gemini_tokens, created_at FROM client_usage_counters "
                                    "WHERE client_id=?",
                                    (c_name,),
                                ).fetchone()
                            if row_c:
                                counter_created_at = float(row_c["created_at"] or 0) or None
                                p_total = int(row_c["total_tokens"] or 0)
                                p_reqs = int(row_c["requests"] or 0)
                                p_cdx = int(row_c["codex_tokens"] or 0)
                                p_gem = int(row_c["gemini_tokens"] or 0)
                                total_tokens = max(total_tokens, p_total)
                                total_requests = max(total_requests, p_reqs)
                                by_provider["codex"]["tokens"] = max(by_provider["codex"]["tokens"], p_cdx)
                                by_provider["gemini"]["tokens"] = max(by_provider["gemini"]["tokens"], p_gem)
                    except Exception:
                        pass

                # Sort top models by tokens used (or requests)
                top_models = sorted([{"model": m, "tokens": toks} for m, toks in by_model.items()], key=lambda x: x["tokens"], reverse=True)[:3]
                now = time.time()
                created_at_ts = found_client.get("created_at_ts")
                try:
                    created_at_ts = float(created_at_ts) if created_at_ts else None
                except (TypeError, ValueError):
                    created_at_ts = None
                # Refill cycle starts on first use, not registration
                refill_seconds = found_client.get("refill_period_seconds")
                cycle_start_ts = None
                next_refill_ts = None
                refill_remaining_seconds = None
                if refill_seconds:
                    ref_s = float(refill_seconds)
                    base_ts = counter_created_at or first_used_ts or created_at_ts
                    if base_ts:
                        elapsed = max(0, now - base_ts)
                        cycle_num = int(elapsed // ref_s)
                        cycle_start_ts = base_ts + (cycle_num * ref_s)
                        next_refill_ts = cycle_start_ts + ref_s
                        refill_remaining_seconds = max(0, int(next_refill_ts - now))

                cycle_used = 0
                cycle_provider_used = {"codex": 0, "gemini": 0}
                if cycle_start_ts is not None and os.path.isfile(db_path):
                    for req_item in key_recent_requests:
                        if req_item["timestamp"] >= cycle_start_ts:
                            tokens = int(req_item["tokens"] or 0)
                            cycle_used += tokens
                            provider = req_item["provider"]
                            cycle_provider_used[provider] = cycle_provider_used.get(provider, 0) + tokens
                elif not refill_seconds:
                    cycle_used = total_tokens
                    cycle_provider_used = {
                        provider: int(values.get("tokens", 0))
                        for provider, values in by_provider.items()
                    }

                expires_at = found_client.get("expires_at")
                try:
                    expires_at = float(expires_at) if expires_at is not None else None
                except (TypeError, ValueError):
                    expires_at = None
                details_max = found_client.get("max_tokens")
                remaining_tokens = max(0, int(details_max) - cycle_used) if details_max is not None and cycle_start_ts is not None else (max(0, int(details_max) - total_tokens) if details_max is not None else None)
                provider_remaining = {}
                for provider, limit in (found_client.get("token_limits") or {}).items():
                    provider_key = str(provider).lower()
                    used = int(cycle_provider_used.get(provider_key, 0))
                    provider_remaining[provider_key] = {
                        "limit": limit,
                        "used": used,
                        "remaining": max(0, int(limit) - used) if limit is not None else None
                    }
                self.send_json_response({
                    "success": True,
                    "details": {
                        "id": found_client.get("id"), "name": found_client.get("name"),
                        "enabled": found_client.get("enabled", True),
                        "created_at": found_client.get("created_at", "Custom Key"),
                        "created_at_ts": created_at_ts, "updated_at_ts": found_client.get("updated_at_ts"),
                        "allowed_providers": found_client.get("allowed_providers", ["codex", "gemini"]),
                        "allowed_models": found_client.get("allowed_models", []),
                        "token_limits": found_client.get("token_limits", {}), "provider_remaining": provider_remaining, "max_tokens": details_max,
                        "remaining_tokens": remaining_tokens, "cycle_used_tokens": cycle_used,
                        "refill_period": found_client.get("refill_period"), "refill_period_seconds": refill_seconds,
                        "refill_unit": found_client.get("refill_unit"), "refill_val": found_client.get("refill_val"),
                        "first_used_ts": first_used_ts,
                        "cycle_start_ts": cycle_start_ts,
                        "next_refill_ts": next_refill_ts,
                        "refill_remaining_seconds": refill_remaining_seconds,
                        "expiration_duration": found_client.get("expiration_duration"),
                        "expiration_unit": found_client.get("expiration_unit"), "expiration_val": found_client.get("expiration_val"),
                        "expires_at": expires_at, "expired": bool(expires_at and now > expires_at),
                        "expiry_remaining_seconds": max(0, int(expires_at - now)) if expires_at else None,
                        "allowed_models_count": len(found_client.get("allowed_models") or []),
                        "total_tokens": total_tokens, "total_requests": total_requests,
                        "by_provider": by_provider, "top_models": top_models,
                        "analytics_scope": {
                            "total_tokens": "lifetime", "total_requests": "lifetime",
                            "provider_tokens": "lifetime", "provider_requests": "retained",
                            "top_models": "retained"
                        },
                        "recent_requests": key_recent_requests[:100],
                        "timeline": found_client.get("change_history", [])[-20:]
                    }
                })
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        if path in ("/aipool/api/tokens/update", "/api/tokens/update"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=408 if isinstance(exc, RequestBodyTimeout) else 400)
                return
            target_id = str(payload.get("id", "")).strip()
            if target_id in ("hermes", "openclaw", "localtooling"):
                self.send_json_response({"success": False, "message": "Cannot modify system internal keys"}, status_code=403)
                return
            new_name = payload.get("name")
            new_enabled = payload.get("enabled")
            allowed_providers = payload.get("allowed_providers")
            allowed_models = payload.get("allowed_models")
            token_limits = payload.get("token_limits") # e.g. {"codex": 10000000, "gemini": 100000000}
            max_tokens = payload.get("max_tokens")     # e.g. 110000000
            refill_period = payload.get("refill_period") # e.g. "5h", "1d", "1w", "1m" or None
            refill_period_seconds = payload.get("refill_period_seconds")
            expiration_duration = payload.get("expiration_duration") # e.g. "1m", "30d"
            expiration_unit = payload.get("expiration_unit")
            expiration_val = payload.get("expiration_val")
            refill_unit = payload.get("refill_unit")
            refill_val = payload.get("refill_val")
            expires_at = payload.get("expires_at")

            try:
                normalized_token_limits = None
                if token_limits is not None:
                    if not isinstance(token_limits, dict):
                        raise ValueError("token_limits must be an object")
                    normalized_token_limits = {
                        str(key).lower(): _validated_quota(value)
                        for key, value in token_limits.items()
                    }
                normalized_max_tokens = (
                    _validated_quota(max_tokens) if "max_tokens" in payload else None
                )
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=408 if isinstance(exc, RequestBodyTimeout) else 400)
                return

            registry_file = get_registry_file()
            try:
                data = json.loads(registry_file.read_text()) if registry_file.is_file() else {"mode": "enforce", "clients": []}
                found = False
                for c in data.get("clients", []):
                    if c.get("id") == target_id:
                        found = True
                        before = {k: c.get(k) for k in ("name", "enabled", "allowed_providers", "allowed_models", "token_limits", "max_tokens", "refill_unit", "refill_val", "refill_period_seconds", "expiration_unit", "expiration_val", "expiration_duration", "expires_at")}
                        if new_name is not None:
                            clean_n = str(new_name).strip()
                            old_name = str(c.get("name") or "").strip()
                            if clean_n:
                                if clean_n != old_name:
                                    if old_name and _resolved_legacy_client_id(data.get("clients", []), old_name) == target_id.lower():
                                        db_path = os.environ.get("AUTH_DB_PATH") or "/var/lib/aipool/runtime/auth.db"
                                        _migrate_legacy_client_counter(db_path, old_name, target_id)
                                c["name"] = clean_n
                        if new_enabled is not None:
                            c["enabled"] = bool(new_enabled)
                        if allowed_providers is not None:
                            c["allowed_providers"] = [str(p).lower() for p in allowed_providers]
                        if allowed_models is not None:
                            c["allowed_models"] = [str(m).strip() for m in allowed_models if str(m).strip()]
                        if token_limits is not None:
                            c["token_limits"] = normalized_token_limits
                        if "max_tokens" in payload:
                            c["max_tokens"] = normalized_max_tokens
                        if "refill_period" in payload:
                            c["refill_period"] = str(refill_period).strip() if refill_period else None
                        if "refill_period_seconds" in payload:
                            c["refill_period_seconds"] = int(refill_period_seconds) if refill_period_seconds is not None else None
                        if "expiration_duration" in payload:
                            c["expiration_duration"] = str(expiration_duration).strip() if expiration_duration else None
                        if "expiration_unit" in payload:
                            c["expiration_unit"] = str(expiration_unit).strip() if expiration_unit else None
                        if "expiration_val" in payload:
                            c["expiration_val"] = int(expiration_val) if expiration_val is not None else None
                        if "refill_unit" in payload:
                            c["refill_unit"] = str(refill_unit).strip() if refill_unit else None
                        if "refill_val" in payload:
                            c["refill_val"] = int(refill_val) if refill_val is not None else None
                        if "expires_at" in payload:
                            c["expires_at"] = float(expires_at) if expires_at is not None else None
                        if not c.get("created_at_ts"):
                            c["created_at_ts"] = time.time()
                        now_ts = time.time()
                        changes = {}
                        for key, old_value in before.items():
                            new_value = c.get(key)
                            if old_value != new_value:
                                changes[key] = {"old": old_value, "new": new_value}
                        c["updated_at_ts"] = now_ts
                        if changes:
                            c.setdefault("change_history", []).append({"timestamp": now_ts, "action": "updated", "changes": changes})
                            c["change_history"] = c["change_history"][-50:]
                        break
                if not found:
                    self.send_json_response({"success": False, "message": f"Token {target_id} not found"}, status_code=404)
                    return
                registry_file.write_text(json.dumps(data, indent=2))
                self.send_json_response({"success": True, "message": f"Token {target_id} updated successfully"})
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        if path in ("/aipool/api/settings/uninstall", "/api/settings/uninstall"):
            self.send_json_response({"success": False, "message": "Destructive uninstall endpoint has been safely disabled."}, status_code=403)
            return

        self.send_json_response({"error": "Not Found"}, status_code=404)

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def run():
    with ThreadedTCPServer(("127.0.0.1", PORT), ProDashboardHandler) as httpd:
        print(f"Server started on http://127.0.0.1:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run()
