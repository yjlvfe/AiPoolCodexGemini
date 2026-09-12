"""
Web Dashboard Server Pro (Luxury OLED Glass Theme, Custom Animated Dropdowns, Micro-Interactions)
"""
import http.server
import socketserver
import urllib.parse
import json
import time
import os
from http import cookies
import subprocess
import sys
import secrets
import hashlib
from pathlib import Path
BRIDGES_DIR = str(Path(__file__).resolve().parents[1] / 'bridges')
if BRIDGES_DIR not in sys.path:
    sys.path.insert(0, BRIDGES_DIR)
SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / 'scripts')
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
from app import PoolManager, TokenAuthManager, is_link_preview_user_agent

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
    return {'codex':codex,'antigravity':{'installed':True,'path':'Built-in OAuth','version':'No external CLI required'}}


import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import update_suite
RUNNING_BUILD = update_suite.version()


def get_system_version():
    result = update_suite.version()
    result['running_commit'] = RUNNING_BUILD.get('commit')
    result['running_version'] = RUNNING_BUILD.get('version')
    try:
        result['last_update'] = json.loads(update_suite.result_path().read_text())
    except (OSError, ValueError):
        result['last_update'] = None
    return result

class ProDashboardHandler(http.server.BaseHTTPRequestHandler):
    MAX_BODY_BYTES = 1 * 1024 * 1024

    def log_message(self, format, *args):
        # Mute normal GET logs to prevent terminal spam
        return

    def send_json_response(self, data, status_code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
        except (TypeError, ValueError):
            raise ValueError('Invalid Content-Length') from None
        if length < 0 or length > self.MAX_BODY_BYTES:
            raise ValueError('Request body is too large')
        if not length:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Request body must be valid JSON') from None
        if not isinstance(value, dict):
            raise ValueError('Request body must be a JSON object')
        return value

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        client_ip = self.client_address[0]
        # Forwarded requests never qualify for the direct-local maintenance bypass.
        if self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP"):
            client_ip = "proxied"
        if "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()
        user_agent = self.headers.get("User-Agent", "")

        # Session check via Cookie, Header, or Query param
        cookie_header = self.headers.get("Cookie")
        session_cookie = None
        if cookie_header:
            c = cookies.SimpleCookie()
            try:
                c.load(cookie_header)
                if "yj_aipool_session" in c:
                    session_cookie = c["yj_aipool_session"].value
            except Exception: pass

        header_session = self.headers.get("X-Session-ID")
        query_session = qs.get("session_id", [None])[0]
        cookie_session = qs.get("session", [None])[0]
        effective_session = session_cookie or header_session or query_session or cookie_session

        auth_mgr = TokenAuthManager()
        is_auth = False
        new_session_id = None

        # Allow localhost / loopback internally
        if self.client_address[0] in ('127.0.0.1','::1') and not self.headers.get('X-Forwarded-For') and not self.headers.get('X-Real-IP'):
            is_auth = True

        # Check existing 24h session FIRST before query tokens
        if not is_auth and effective_session:
            if auth_mgr.validate_device(effective_session, client_ip):
                is_auth = True

        # Check URL query token (e.g. ?token=...)
        if not is_auth and "token" in qs and qs["token"]:
            # Telegram and social crawlers fetch links for previews.  They must
            # not consume a single-use token or bind it to a bot user-agent.
            if is_link_preview_user_agent(user_agent):
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            t = qs["token"][0]
            new_session_id = auth_mgr.register_device(t, client_ip, user_agent)
            if new_session_id:
                is_auth = True
                effective_session = new_session_id

        # Unauthenticated handling
        if not is_auth:
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_LOGIN_TEMPLATE.encode("utf-8"))
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

        # API: tokens management
        if path in ("/aipool/api/tokens/list", "/api/tokens/list"):
            registry_file = Path(__file__).resolve().parent / "client-identities.json"
            try:
                data = json.loads(registry_file.read_text())
                clients = []
                system_keys = ("hermes", "openclaw", "localtooling")

                # Compute aggregated token usage per client from request_events DB
                usage_by_client = {}
                db_path = os.environ.get("AUTH_DB_PATH") or str(Path(__file__).resolve().parents[1] / "bridges/auth.db")
                if os.path.isfile(db_path):
                    try:
                        import sqlite3
                        with sqlite3.connect(db_path, timeout=5) as conn:
                            conn.row_factory = sqlite3.Row
                            cur = conn.execute("SELECT audit_json, total_tokens FROM request_events WHERE audit_json IS NOT NULL AND audit_json != ''")
                            for r in cur.fetchall():
                                try:
                                    aud = json.loads(r["audit_json"])
                                    cl = aud.get("client_label")
                                    if cl:
                                        toks = int(r["total_tokens"] or 0)
                                        usage_by_client[cl.lower()] = usage_by_client.get(cl.lower(), 0) + toks
                                except Exception:
                                    pass
                    except Exception:
                        pass

                for c in data.get("clients", []):
                    # Hide internal system keys from public API page
                    if c.get("id") in system_keys:
                        continue
                    c_name = c.get("name", "")
                    c_id = c.get("id", "")
                    used = usage_by_client.get(c_name.lower(), 0) or usage_by_client.get(c_id.lower(), 0)
                    clients.append({
                        "id": c.get("id"),
                        "name": c_name,
                        "raw_token": c.get("raw_token"),  # Persistent token for copying anytime
                        "enabled": c.get("enabled", True),
                        "created_at": c.get("created_at", "Custom Key"),
                        "total_tokens": used,
                        "max_tokens": c.get("max_tokens"),
                        "allowed_providers": c.get("allowed_providers", ["codex", "gemini"]),
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
                self.send_json_response({"success": False, "message": str(exc)}, status_code=400)
                return
            target_id = str(payload.get("id", "")).strip()
            if target_id in ("hermes", "openclaw", "localtooling"):
                self.send_json_response({"success": False, "message": "Cannot revoke system internal keys"}, status_code=403)
                return
            registry_file = Path(__file__).resolve().parent / "client-identities.json"
            try:
                data = json.loads(registry_file.read_text())
                clients = [c for c in data.get("clients", []) if c.get("id") != target_id]
                data["clients"] = clients
                registry_file.write_text(json.dumps(data, indent=2))
                alt_dest = Path("/var/lib/aipool/app/dashboard/client-identities.json")
                if alt_dest.parent.is_dir() and alt_dest.resolve() != registry_file.resolve():
                    alt_dest.write_text(json.dumps(data, indent=2))
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
        sess_val = effective_session or new_session_id or ""
        session_json = _safe_json_for_script(sess_val)
        injected_script = f"""<script>
            window.__INITIAL_DATA__ = {initial_json};
            window.__SESSION_ID__ = {session_json};
            window.__ACTIVE_VIEW__ = "{active_view}";
            if ({session_json}) {{ 
                try {{ 
                    localStorage.setItem('yj_aipool_session', {session_json}); 
                    document.cookie = "yj_aipool_session=" + {session_json} + "; path=/; max-age=31536000; SameSite=Lax";
                }} catch(e){{}} 
            }}
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
        session_to_persist = new_session_id or session_cookie
        if session_to_persist:
            max_age = max(1, int(auth_mgr.session_expiry_seconds))
            is_secure = "Secure" if (self.headers.get("X-Forwarded-Proto") == "https") else ""
            cookie_val = f"yj_aipool_session={session_to_persist}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"
            if is_secure:
                cookie_val += "; Secure"
            self.send_header("Set-Cookie", cookie_val)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get('Host'):
            self.send_json_response({'success': False, 'message': 'Cross-origin writes are not allowed'},403)
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        client_ip = self.client_address[0]
        # Forwarded requests never qualify for the direct-local maintenance bypass.
        if self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP"):
            client_ip = "proxied"
        if "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()

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

        header_session = self.headers.get("X-Session-ID")
        effective_session = session_cookie or header_session

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
                self.send_json_response({"success": False, "message": str(exc)}, status_code=400)
                return
            system = payload.get("system", "")
            try:
                account = int(payload.get("account", 0))
            except (TypeError, ValueError):
                account = 0
            if system not in ("antigravity", "codex") or account < 1:
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
                self.send_json_response({"success": False, "message": str(exc)}, status_code=400)
                return
            system = payload.get("system", "")
            try:
                account = int(payload.get("account", 0))
            except (TypeError, ValueError):
                account = 0
            confirmation = payload.get("confirmation", "")
            if system not in ("antigravity", "codex") or account < 1:
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
                self.send_json_response({"success": False, "message": str(exc)}, status_code=400)
                return
            tool = payload.get("tool", "")
            if tool not in ("codex", "antigravity"):
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
            registry_file = Path(__file__).resolve().parent / "client-identities.json"
            try:
                data = json.loads(registry_file.read_text())
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
                self.send_json_response({"success": False, "message": str(exc)}, status_code=400)
                return
            name = str(payload.get("name", "")).strip() or "Unnamed Key"
            new_id = "key_" + secrets.token_hex(4)
            raw_token = "sk-aipool-" + secrets.token_hex(16)
            digest = hashlib.sha256(raw_token.encode()).hexdigest()

            registry_file = Path(__file__).resolve().parent / "client-identities.json"
            try:
                data = json.loads(registry_file.read_text()) if registry_file.is_file() else {"mode": "enforce", "clients": []}
                clients = data.get("clients", [])
                clients.append({
                    "id": new_id,
                    "name": name,
                    "raw_token": raw_token,
                    "enabled": True,
                    "created_at": time.strftime("%Y-%m-%d %H:%M"),
                    "sha256": digest
                })
                data["clients"] = clients
                registry_file.write_text(json.dumps(data, indent=2))
                # Sync to runtime /var/lib/aipool/app if deployed
                alt_dest = Path("/var/lib/aipool/app/dashboard/client-identities.json")
                if alt_dest.parent.is_dir() and alt_dest.resolve() != registry_file.resolve():
                    alt_dest.write_text(json.dumps(data, indent=2))
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
                self.send_json_response({"success": False, "message": str(exc)}, status_code=400)
                return
            target_id = str(payload.get("id", "")).strip()
            if target_id in ("hermes", "openclaw", "localtooling"):
                self.send_json_response({"success": False, "message": "Cannot revoke system internal keys"}, status_code=403)
                return
            registry_file = Path(__file__).resolve().parent / "client-identities.json"
            try:
                data = json.loads(registry_file.read_text())
                clients = [c for c in data.get("clients", []) if c.get("id") != target_id]
                data["clients"] = clients
                registry_file.write_text(json.dumps(data, indent=2))
                alt_dest = Path("/var/lib/aipool/app/dashboard/client-identities.json")
                if alt_dest.parent.is_dir() and alt_dest.resolve() != registry_file.resolve():
                    alt_dest.write_text(json.dumps(data, indent=2))
                self.send_json_response({"success": True, "message": f"Token {target_id} revoked"})
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        if path in ("/aipool/api/tokens/details", "/api/tokens/details"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=400)
                return
            target_id = str(payload.get("id", "")).strip()
            registry_file = Path(__file__).resolve().parent / "client-identities.json"
            try:
                data = json.loads(registry_file.read_text())
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
                db_path = os.environ.get("AUTH_DB_PATH") or str(Path(__file__).resolve().parents[1] / "bridges/auth.db")

                total_tokens = 0
                total_requests = 0
                by_provider = {"codex": {"tokens": 0, "requests": 0}, "gemini": {"tokens": 0, "requests": 0}}
                by_model = {}

                if os.path.isfile(db_path):
                    try:
                        import sqlite3
                        with sqlite3.connect(db_path, timeout=5) as conn:
                            conn.row_factory = sqlite3.Row
                            cur = conn.execute("SELECT audit_json, total_tokens, pool, model FROM request_events WHERE audit_json IS NOT NULL AND audit_json != ''")
                            for r in cur.fetchall():
                                try:
                                    aud = json.loads(r["audit_json"])
                                    cl = (aud.get("client_label") or "").lower()
                                    if cl and (cl == c_name or cl == c_id):
                                        toks = int(r["total_tokens"] or 0)
                                        m = str(r["model"] or "unknown")
                                        prov = "codex" if str(r["pool"]).lower() in ("codex", "openai") else "gemini"
                                        total_tokens += toks
                                        total_requests += 1
                                        by_provider[prov]["tokens"] += toks
                                        by_provider[prov]["requests"] += 1
                                        by_model[m] = by_model.get(m, 0) + toks
                                except Exception:
                                    pass
                    except Exception:
                        pass

                # Sort top models by tokens used (or requests)
                top_models = sorted([{"model": m, "tokens": toks} for m, toks in by_model.items()], key=lambda x: x["tokens"], reverse=True)[:3]

                self.send_json_response({
                    "success": True,
                    "details": {
                        "id": found_client.get("id"),
                        "name": found_client.get("name"),
                        "enabled": found_client.get("enabled", True),
                        "created_at": found_client.get("created_at", "Custom Key"),
                        "allowed_providers": found_client.get("allowed_providers", ["codex", "gemini"]),
                        "allowed_models": found_client.get("allowed_models", []),
                        "token_limits": found_client.get("token_limits", {}),
                        "max_tokens": found_client.get("max_tokens"),
                        "total_tokens": total_tokens,
                        "total_requests": total_requests,
                        "by_provider": by_provider,
                        "top_models": top_models
                    }
                })
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        if path in ("/aipool/api/tokens/update", "/api/tokens/update"):
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_json_response({"success": False, "message": str(exc)}, status_code=400)
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

            registry_file = Path(__file__).resolve().parent / "client-identities.json"
            try:
                data = json.loads(registry_file.read_text())
                found = False
                for c in data.get("clients", []):
                    if c.get("id") == target_id:
                        found = True
                        if new_name is not None:
                            clean_n = str(new_name).strip()
                            if clean_n:
                                c["name"] = clean_n
                        if new_enabled is not None:
                            c["enabled"] = bool(new_enabled)
                        if allowed_providers is not None:
                            c["allowed_providers"] = [str(p).lower() for p in allowed_providers]
                        if allowed_models is not None:
                            c["allowed_models"] = [str(m).strip() for m in allowed_models if str(m).strip()]
                        if token_limits is not None:
                            c["token_limits"] = {str(k).lower(): (int(v) if v is not None else None) for k, v in token_limits.items()}
                        if "max_tokens" in payload:
                            c["max_tokens"] = int(max_tokens) if max_tokens is not None else None
                        break
                if not found:
                    self.send_json_response({"success": False, "message": f"Token {target_id} not found"}, status_code=404)
                    return
                registry_file.write_text(json.dumps(data, indent=2))
                alt_dest = Path("/var/lib/aipool/app/dashboard/client-identities.json")
                if alt_dest.parent.is_dir() and alt_dest.resolve() != registry_file.resolve():
                    alt_dest.write_text(json.dumps(data, indent=2))
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
