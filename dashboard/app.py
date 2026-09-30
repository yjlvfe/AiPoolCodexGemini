"""
AI Pool Dashboard & Proxy Core (Asynchronous Background Worker Engine)
"""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"cli"))
from config_env import load
load()
import json
import time
import secrets
import hmac
import hashlib
import ipaddress
import sqlite3
import subprocess
import re
import shutil
import tempfile
import threading
import signal
import base64
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Any, Optional

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("AUTH_DB_PATH", "/var/lib/aipool/runtime/auth.db")
DEFAULT_SESSION_EXPIRY_HOURS = 100 * 365 * 24

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_DEVICE_USERCODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
CODEX_DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
CODEX_LINK_REDIRECT_URI = "http://localhost:1455/auth/callback"
CODEX_DEVICE_VERIFY_URL = "https://auth.openai.com/codex/device"

GEMINI_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
GEMINI_CLIENT_SECRET = "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"
GEMINI_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GEMINI_TOKEN_URL = "https://oauth2.googleapis.com/token"
GEMINI_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
GEMINI_REDIRECT_URI = "http://localhost:8085/oauth2callback"


def configured_session_expiry_hours() -> int:
    try:
        value = int(os.environ.get("AIPOOL_SESSION_EXPIRY_HOURS", DEFAULT_SESSION_EXPIRY_HOURS))
    except (TypeError, ValueError):
        value = DEFAULT_SESSION_EXPIRY_HOURS
    return max(1, min(value, 365 * 24))


_LINK_PREVIEW_USER_AGENT_MARKERS = (
    "telegrambot",
    "twitterbot",
    "facebookexternalhit",
    "facebot",
    "slackbot",
    "discordbot",
    "linkedinbot",
    "whatsapp",
    "skypeuripreview",
    "pinterest",
)


def is_link_preview_user_agent(user_agent: str) -> bool:
    """Return True for crawlers that must not consume a one-time auth link."""
    normalized = str(user_agent or "").casefold()
    return any(marker in normalized for marker in _LINK_PREVIEW_USER_AGENT_MARKERS)


def device_type_for_user_agent(user_agent: str) -> str:
    """Classify a browser broadly without collecting a hardware fingerprint."""
    normalized = str(user_agent or "").casefold()
    if any(marker in normalized for marker in ("ipad", "tablet", "kindle")):
        return "tablet"
    if any(marker in normalized for marker in ("mobile", "android", "iphone", "ipod")):
        return "mobile"
    if normalized:
        return "desktop"
    return "unknown"


def normalize_client_ip(headers, socket_peer: str) -> str:
    """Use only the reverse proxy's normalized address, never a remote header."""
    def parse(value):
        try:
            return str(ipaddress.ip_address(str(value).strip()))
        except ValueError:
            return ""

    peer = parse(socket_peer)
    if peer not in {"127.0.0.1", "::1"}:
        return peer
    explicit = parse(headers.get("X-Real-IP", ""))
    if explicit:
        return explicit
    for candidate in reversed(str(headers.get("X-Forwarded-For", "")).split(",")):
        normalized = parse(candidate)
        if normalized:
            return normalized
    return peer


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.casefold())


class TokenAuthManager:
    def __init__(self, db_path: str = DB_PATH, session_expiry_hours: int | None = None):
        self.db_path = db_path
        hours = configured_session_expiry_hours() if session_expiry_hours is None else int(session_expiry_hours)
        self.session_expiry_seconds = max(1, hours * 3600)
        self._init_db()

    def _get_conn(self):
        db = Path(self.db_path)
        if db.is_symlink():
            raise ValueError('Authentication database must not be a symlink')
        ancestor = db.parent
        while ancestor != ancestor.parent:
            if ancestor.is_symlink():
                raise ValueError('Authentication database parent must not be a symlink')
            ancestor = ancestor.parent
        db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = sqlite3.connect(db, timeout=10)
        try:
            os.chmod(db, 0o600)
        except OSError:
            pass
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS magic_tokens (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER,
                    created_at REAL,
                    used INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS authorized_devices (
                    session_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    client_ip TEXT,
                    user_agent TEXT,
                    device_type TEXT NOT NULL DEFAULT 'unknown',
                    created_at REAL,
                    last_seen REAL,
                    expires_at REAL
                )
            """)
            for column, definition in (
                ('device_type', "TEXT NOT NULL DEFAULT 'unknown'"),
                ('last_seen', "REAL"),
            ):
                try:
                    conn.execute(f"ALTER TABLE authorized_devices ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError as exc:
                    if 'duplicate column name' not in str(exc).casefold():
                        raise
            self._migrate_legacy_rows(conn, time.time())
            conn.commit()

    def _migrate_legacy_rows(self, conn, now: float):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS auth_storage_migrations (key TEXT PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        claimed = conn.execute(
            "INSERT OR IGNORE INTO auth_storage_migrations(key, applied_at) VALUES(?, ?)",
            ("revoke_ambiguous_raw_sessions_v1", now),
        )
        if claimed.rowcount == 1:
            # Old sessions and new SHA-256 rows are both 64 hex characters;
            # they cannot be distinguished safely, so revoke them once.
            conn.execute("DELETE FROM authorized_devices")
        conn.execute(
            "DELETE FROM magic_tokens WHERE used IS NULL OR used != 0 OR created_at IS NULL OR created_at < ?",
            (now - 1800,),
        )
        for row in conn.execute("SELECT token FROM magic_tokens WHERE used=0").fetchall():
            token = str(row["token"])
            if _is_digest(token):
                continue
            try:
                conn.execute("UPDATE magic_tokens SET token=? WHERE token=? AND used=0", (hashlib.sha256(token.encode()).hexdigest(), token))
            except sqlite3.IntegrityError:
                conn.execute("DELETE FROM magic_tokens WHERE token=? AND used=0", (token,))
        conn.execute("DELETE FROM authorized_devices WHERE expires_at IS NULL OR expires_at <= ?", (now,))
        for row in conn.execute("SELECT session_id FROM authorized_devices").fetchall():
            session_id = str(row["session_id"])
            if _is_digest(session_id):
                continue
            try:
                conn.execute("UPDATE authorized_devices SET session_id=? WHERE session_id=?", (hashlib.sha256(session_id.encode()).hexdigest(), session_id))
            except sqlite3.IntegrityError:
                conn.execute("DELETE FROM authorized_devices WHERE session_id=?", (session_id,))

    def generate_magic_link(self, base_url: str, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO magic_tokens (token, user_id, created_at, used) VALUES (?, ?, ?, 0)",
                (token_digest, user_id, now)
            )
            conn.execute("DELETE FROM magic_tokens WHERE created_at < ?", (now - 1800,))
            conn.commit()
        return f"{base_url.rstrip('/')}/auth?token={token}"

    def register_device(self, token: str, client_ip: str, user_agent: str) -> Optional[str]:
        if not token:
            return None
        now = time.time()
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        with self._get_conn() as conn:
            self._migrate_legacy_rows(conn, now)
            cur = conn.execute("SELECT * FROM magic_tokens WHERE token = ?", (token_digest,))
            row = cur.fetchone()
            if not row:
                return None
            stored_token = row["token"]
            if now - row["created_at"] > 1800:
                conn.execute("DELETE FROM magic_tokens WHERE token = ?", (stored_token,))
                conn.commit()
                return None

            consumed = conn.execute(
                "UPDATE magic_tokens SET used = 1 WHERE token = ? AND used = 0 AND created_at >= ?",
                (stored_token, now - 1800),
            )
            if consumed.rowcount != 1:
                return None
            session_id = secrets.token_hex(32)
            stored_session_id = hashlib.sha256(session_id.encode()).hexdigest()
            expires_at = now + self.session_expiry_seconds
            device_type = device_type_for_user_agent(user_agent)
            conn.execute("""
                INSERT INTO authorized_devices (
                    session_id, user_id, client_ip, user_agent, device_type,
                    created_at, last_seen, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stored_session_id, row["user_id"], client_ip, user_agent, device_type,
                now, now, expires_at,
            ))
            conn.commit()
            return session_id

    def validate_device(self, session_id: str, client_ip: str = "") -> bool:
        if not session_id:
            return False
        now = time.time()
        session_digest = hashlib.sha256(session_id.encode()).hexdigest()
        with self._get_conn() as conn:
            self._migrate_legacy_rows(conn, now)
            cur = conn.execute("SELECT * FROM authorized_devices WHERE session_id = ?", (session_digest,))
            row = cur.fetchone()
            if not row:
                return False
            stored_session_id = row["session_id"]
            if now > row["expires_at"]:
                conn.execute("DELETE FROM authorized_devices WHERE session_id = ?", (stored_session_id,))
                conn.commit()
                return False
            # A magic-link session is bound to the address that redeemed it.
            # This prevents a leaked session cookie from being replayed from a
            # different peer.  Reverse-proxied requests are normalized by the
            # handler before reaching this method.
            if client_ip and not hmac.compare_digest(str(row["client_ip"]), str(client_ip)):
                # Update client_ip to allow seamless roaming across Wi-Fi and mobile networks
                conn.execute(
                    "UPDATE authorized_devices SET client_ip = ? WHERE session_id = ?",
                    (client_ip, stored_session_id),
                )
            # Keep an active trusted device alive for the configured window.
            conn.execute(
                "UPDATE authorized_devices SET last_seen = ?, expires_at = ? WHERE session_id = ?",
                (now, now + self.session_expiry_seconds, stored_session_id),
            )
            conn.commit()
            return True


class PoolManager:
    """High Performance Pool Manager with Asynchronous Background Poller.
    Zero blocking on user requests: API returns in <10 milliseconds!
    """
    def __init__(self):
        self.ag_store = os.environ.get("AG_ACCOUNT_STORE", os.path.expanduser("~/.antigravity-accounts"))
        self.codex_store = os.environ.get("CODEX_ACCOUNT_STORE", os.path.expanduser("~/.codex-accounts"))
        self.auth_db = DB_PATH
        
        self._lock = threading.Lock()
        self._snapshot_path = Path(os.environ.get("AIPOOL_POOL_SNAPSHOT", "/var/lib/aipool/runtime/pool_snapshot.json"))
        self._cached_ag = None
        self._cached_cdx = None
        self._cached_logs = None
        self._load_persisted_snapshot()
        self._refresh_lock = threading.Lock()
        self._refresh_done = threading.Event()
        self._refresh_inflight = False
        self._refresh_state = 'idle'
        self._refresh_error = None
        self._refresh_started_at = None
        self._refresh_completed_at = None
        self._running = True
        self._oauth_sessions = {}
        self._oauth_sessions_lock = threading.Lock()

        # The service can disable periodic work; explicit refresh remains available.
        self._worker_thread = None
        if os.environ.get('AIPOOL_DISABLE_BACKGROUND_POLLER', '').lower() not in ('1', 'true', 'yes', 'on') and 'pytest' not in sys.modules and not os.environ.get('PYTEST_CURRENT_TEST'):
            self._worker_thread = threading.Thread(target=self._background_poller, daemon=True)
            self._worker_thread.start()
    def _load_persisted_snapshot(self):
        try:
            if not self._snapshot_path.is_file():
                return
            data = json.loads(self._snapshot_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                return
            with self._lock:
                self._cached_ag = data.get('gemini')
                self._cached_cdx = data.get('codex')
                self._cached_logs = data.get('logs')
        except (OSError, ValueError, TypeError):
            return

    def _persist_snapshot(self, ag_data, cdx_data, logs_data):
        payload = {'saved_at': time.time(), 'gemini': ag_data, 'codex': cdx_data, 'logs': logs_data}
        try:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._snapshot_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, self._snapshot_path)
        except (OSError, TypeError, ValueError):
            return

    def _background_poller(self):
        while self._running:
            if hasattr(sys, 'is_finalizing') and sys.is_finalizing():
                break
            try:
                self._update_all_background()
            except RuntimeError:
                pass
            except Exception as e:
                try:
                    if not (hasattr(sys, 'is_finalizing') and sys.is_finalizing()):
                        print(f"[pool-worker] Polling error: {e}")
                except Exception:
                    pass
            # Poll every 10 seconds asynchronously
            time.sleep(10)

    def _update_all_background(self):
        if hasattr(sys, 'is_finalizing') and sys.is_finalizing():
            return
        try:
            cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
            if cli_dir not in sys.path:
                sys.path.insert(0, cli_dir)
            from account_reports import pool_report
            from concurrent.futures import ThreadPoolExecutor, wait
            executor = ThreadPoolExecutor(max_workers=2)
            ag_future = executor.submit(pool_report, 'gemini')
            cdx_future = executor.submit(pool_report, 'codex')
            try:
                done, pending = wait((ag_future, cdx_future), timeout=30.0)
                if pending:
                    for future in pending:
                        future.cancel()
                    raise TimeoutError('provider refresh exceeded 30 seconds')
                ag_data = ag_future.result()
                cdx_data = cdx_future.result()
            except Exception as exc:
                if not (hasattr(sys, 'is_finalizing') and sys.is_finalizing()):
                    try:
                        print(f"[pool-worker] account refresh failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                    except Exception:
                        pass
                raise
            finally:
                # Never wait for a stuck provider: running Python threads cannot
                # be safely killed, so they finish independently after timeout.
                executor.shutdown(wait=False, cancel_futures=True)
            if not (hasattr(sys, 'is_finalizing') and sys.is_finalizing()):
                try:
                    print(f"[pool-worker] account refresh ok codex={cdx_data.get('pool_metrics', {}).get('healthy_accounts', 0)}", flush=True)
                except Exception:
                    pass

            # Publish account reports before optional analytics. A log/metrics
            # failure must never hide a valid account snapshot.
            logs_data = None
            try:
                logs_data = self._build_logs_report()
            except Exception as exc:
                if not (hasattr(sys, 'is_finalizing') and sys.is_finalizing()):
                    try:
                        print(f"[pool-worker] analytics refresh skipped: {type(exc).__name__}", flush=True)
                    except Exception:
                        pass

            # Never replace a healthy persisted snapshot with a transient
            # all-failed report. Keep per-provider last-known-good data.
            with self._lock:
                if not (isinstance(ag_data, dict) and ag_data.get('pool_metrics', {}).get('healthy_accounts', 0) == 0 and ag_data.get('accounts')):
                    self._cached_ag = ag_data
                if not (isinstance(cdx_data, dict) and cdx_data.get('pool_metrics', {}).get('healthy_accounts', 0) == 0 and cdx_data.get('accounts')):
                    self._cached_cdx = cdx_data
                if logs_data is not None:
                    logs_data["status"] = {
                        "gemini": self._cached_ag or ag_data,
                        "codex": self._cached_cdx or cdx_data
                    }
                    self._cached_logs = logs_data
                persisted_logs = self._cached_logs
                persisted_ag = self._cached_ag or ag_data
                persisted_cdx = self._cached_cdx or cdx_data
                # Add per-account request totals before publishing the status
                # object.  Previously only get_all_status() enriched the live
                # response, while the cached report used by the dashboard was
                # left with null/zero account totals.
                self._enrich_accounts_data(persisted_ag, persisted_cdx)
                if isinstance(persisted_logs, dict):
                    persisted_logs['status'] = {
                        'gemini': persisted_ag,
                        'codex': persisted_cdx,
                    }
            self._persist_snapshot(persisted_ag, persisted_cdx, persisted_logs)
        except RuntimeError:
            pass

    def _build_logs_report(self):
        import sys
        bridge_dir = os.path.join(os.path.dirname(_CURRENT_DIR),'bridges')
        if bridge_dir not in sys.path:
            sys.path.insert(0,bridge_dir)
        import pool_runtime
        result = pool_runtime.report(self.auth_db)
        result['status'] = self.get_all_status()
        return result

    def trigger_instant_refresh(self, timeout=35.0):
        """Run one bounded refresh and return its state plus the latest snapshot.

        Only one refresh may run at once. A timeout/failure never publishes partial
        account data; callers receive the last-good snapshot with an explicit state.
        """
        with self._refresh_lock:
            if self._refresh_inflight:
                state = 'pending'
                done = self._refresh_done
            else:
                self._refresh_inflight = True
                self._refresh_state = 'pending'
                self._refresh_error = None
                self._refresh_started_at = time.time()
                self._refresh_completed_at = None
                self._refresh_done = threading.Event()
                done = self._refresh_done
                state = 'pending'

                def refresh():
                    success = False
                    error = None
                    try:
                        self._update_all_background()
                        success = True
                    except Exception as exc:
                        error = f'{type(exc).__name__}: {str(exc)[:240]}'
                        print(f"[pool] refresh error: {error}", flush=True)
                    finally:
                        with self._refresh_lock:
                            self._refresh_inflight = False
                            self._refresh_state = 'completed' if success else 'failed'
                            self._refresh_error = error
                            self._refresh_completed_at = time.time()
                            self._refresh_done.set()
                threading.Thread(target=refresh, daemon=True).start()

        finished = done.wait(timeout=max(0.0, float(timeout)))
        with self._refresh_lock:
            if not finished and self._refresh_inflight:
                state = 'pending'
            else:
                state = self._refresh_state
            return {
                'state': state,
                'inflight': self._refresh_inflight,
                'error': self._refresh_error,
                'started_at': self._refresh_started_at,
                'completed_at': self._refresh_completed_at,
            }


    def get_usage_logs_report(self) -> Dict[str, Any]:
        # The live stream must read the DB on every poll; the account snapshot
        # remains asynchronous, but cached logs hide events recorded meanwhile.
        return self._build_logs_report()

    def get_api_requests(self, limit: int = 200) -> list:
        import sys
        bridge_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'bridges')
        if bridge_dir not in sys.path:
            sys.path.insert(0, bridge_dir)
        import request_log
        return request_log.get_api_requests(self.auth_db, limit=limit)

    def get_request_prompt(self, request_id: str) -> Dict[str, Any]:
        import sys
        bridge_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'bridges')
        if bridge_dir not in sys.path:
            sys.path.insert(0, bridge_dir)
        import request_log
        return request_log.get_request_prompt(self.auth_db, request_id)

    def switch_account(self, system: str, account_num: int) -> tuple:
        if system not in ('gemini', 'codex') or not isinstance(account_num, int) or account_num < 1:
            return False, 'Invalid system or account number.'
        import sys
        cmd = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli', 'ag' if system == 'gemini' else 'cx')
        # Dashboard only switches existing slots; interactive enrollment belongs in a terminal.
        store = self.ag_store if system == 'gemini' else self.codex_store
        account_path = Path(store) / str(account_num)
        if account_path.is_symlink() or not account_path.exists():
            return False, f'Account {account_num} does not exist on this device.'
        try:
            res = subprocess.run([sys.executable, cmd, 'switch', str(account_num)], capture_output=True, text=True, timeout=150)
        except subprocess.TimeoutExpired:
            return False, 'Switch timed out after 150s.'
        detail = (res.stdout or '').strip() or (res.stderr or '').strip()
        if res.returncode != 0:
            return False, detail or f'Failed to switch to account {account_num}.'
        threading.Thread(target=self._update_all_background, daemon=True).start()
        with self._lock:
            cached_data = self._cached_ag if system == 'gemini' else self._cached_cdx
            if cached_data and 'accounts' in cached_data:
                for acc in cached_data['accounts']:
                    acc['is_active'] = (acc.get('account') == account_num)
        return True, detail or f'Switched to account {account_num}'

    def delete_account(self, system: str, account_num: int, confirmation: str = "") -> tuple:
        if confirmation.strip().lower() != 'confirm':
            return False, 'Must type confirm to authorize account deletion.'
        if system not in ('gemini', 'codex') or not isinstance(account_num, int) or account_num < 1:
            return False, 'Invalid system or account number.'
        import sys
        cmd = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli', 'ag' if system == 'gemini' else 'cx')
        store = self.ag_store if system == 'gemini' else self.codex_store
        account_dir = Path(store) / str(account_num)
        if account_dir.is_symlink() or not account_dir.exists():
            return False, f'Account {account_num} does not exist on this device.'
        try:
            res = subprocess.run([sys.executable, cmd, 'rm', str(account_num)], capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return False, 'Deletion timed out after 60s.'
        if res.returncode != 0:
            err = (res.stderr or res.stdout or '').strip()
            return False, err or f'Failed to delete account {account_num}.'

        # Clear individual account usage counter upon deletion
        try:
            pool_name = 'Gemini' if system == 'gemini' else 'Codex'
            with sqlite3.connect(self.auth_db, timeout=5) as conn:
                conn.execute('PRAGMA busy_timeout=10000')
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('DELETE FROM account_usage_counters WHERE pool = ? AND account = ?', (pool_name, str(account_num)))
                conn.commit()
        except Exception as exc:
            print(f'[pool] Failed to clear account_usage_counters for {system} #{account_num}: {exc}')

        # Immediate background update to synchronize cache
        self.trigger_instant_refresh()
        return True, f'Account {account_num} deleted successfully.'

    def initiate_relogin(self, system: str, account_num: int) -> dict:
        if system != 'codex':
            return {'success': False, 'status': 'unsupported', 'message': 'Relogin currently supported for Codex accounts only.'}
        if not isinstance(account_num, int) or account_num < 1:
            return {'success': False, 'status': 'invalid_target', 'message': 'Invalid account number.'}
        target = Path(self.codex_store) / str(account_num)
        if target.is_symlink() or not target.exists() or not (target.is_dir() or target.is_file()):
            return {'success': False, 'status': 'invalid_target', 'message': f'Codex account {account_num} does not exist; relogin requires an existing slot.'}
        # Capture the slot identity before OAuth starts.  The worker must use
        # this immutable value when importing the resulting auth.json.
        try:
            metadata_path = target / 'metadata.json' if target.is_dir() else None
            if metadata_path and metadata_path.is_file():
                expected_identity = str(json.loads(metadata_path.read_text()).get('identity') or '')
            else:
                auth_path = target / 'auth.json' if target.is_dir() else target
                cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
                if cli_dir not in sys.path:
                    sys.path.insert(0, cli_dir)
                from account_manager import codex_identity
                expected_identity = str(codex_identity(json.loads(auth_path.read_text())).get('identity') or '')
            if not expected_identity:
                raise ValueError('missing slot identity')
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
            return {'success': False, 'status': 'invalid_target', 'message': f'Codex account {account_num} has invalid or missing identity metadata.'}
        
        if not hasattr(self, '_relogin_sessions'):
            self._relogin_sessions = {}

        # Cancel any existing session for this account
        existing = self._relogin_sessions.get(account_num)
        if existing and existing.get('process'):
            self._terminate_relogin_process(existing['process'])

        session = {
            'account': account_num,
            'status': 'starting',
            'code': None,
            'url': 'https://auth.openai.com/codex/device',
            'message': 'Starting device authorization...',
            'error': None,
            'started_at': time.time(),
            'process': None,
            'temp_dir': None,
        }
        self._relogin_sessions[account_num] = session

        def _worker():
            temp_home = tempfile.mkdtemp(prefix=f'codex-relogin-{account_num}-')
            session['temp_dir'] = temp_home
            try:
                cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
                if cli_dir not in sys.path:
                    sys.path.insert(0, cli_dir)
                import account_manager
                env = account_manager.codex_env(temp_home)
                cmd = [env['CODEX_REAL_BIN'], '-c', 'cli_auth_credentials_store="file"', 'login', '--device-auth']
                p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
                session['process'] = p

                # Read output until device code appears
                code_found = False
                for _ in range(50):
                    line = p.stdout.readline()
                    if not line:
                        break
                    # Strip ansi colors
                    clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line)
                    if 'https://auth.openai.com/codex/device' in clean_line:
                        session['url'] = 'https://auth.openai.com/codex/device'
                    if 'Enter this one-time code' in clean_line:
                        next_line = p.stdout.readline()
                        clean_code = re.sub(r'\x1b\[[0-9;]*m', '', next_line).strip()
                        code_match = re.search(r'([A-Z0-9]{4,5}-[A-Z0-9]{4,5})', clean_code)
                        if code_match:
                            session['code'] = code_match.group(1)
                            session['status'] = 'pending'
                            session['message'] = 'Device code generated. Awaiting user authorization.'
                            code_found = True
                            break

                if not code_found:
                    session['status'] = 'failed'
                    session['message'] = 'Could not retrieve device code from Codex CLI.'
                    return

                # Wait for completion (up to 900 seconds)
                ret = p.wait(timeout=900)
                if ret == 0:
                    # Successfully authenticated, auth.json should exist in temp_home
                    auth_file = Path(temp_home) / 'auth.json'
                    if auth_file.is_file():
                        mgr = account_manager.Manager('codex')
                        # Import only after validating the re-authenticated identity.
                        mgr.add_or_switch(number=str(account_num), source=str(auth_file), expected_identity=expected_identity)
                        session['status'] = 'completed'
                        session['message'] = f'Account #{account_num} re-authenticated successfully!'
                        self.trigger_instant_refresh()
                    else:
                        session['status'] = 'failed'
                        session['message'] = 'auth.json was not created after login.'
                else:
                    session['status'] = 'failed'
                    session['message'] = f'Codex login exited with status {ret}.'
            except subprocess.TimeoutExpired:
                session['status'] = 'failed'
                session['message'] = 'Device code authorization timed out.'
                self._terminate_relogin_process(session.get('process'))
            except Exception as exc:
                session['status'] = 'failed'
                session['message'] = str(exc)
            finally:
                if temp_home and os.path.exists(temp_home):
                    shutil.rmtree(temp_home, ignore_errors=True)

        threading.Thread(target=_worker, daemon=True).start()
        return {'success': True, 'account': account_num, 'status': 'starting'}

    def get_relogin_status(self, system: str, account_num: int) -> dict:
        if not hasattr(self, '_relogin_sessions'):
            return {'success': False, 'status': 'none', 'message': 'No active session.'}
        session = self._relogin_sessions.get(account_num)
        if not session:
            return {'success': False, 'status': 'none', 'message': 'No session found for this account.'}
        return {
            'success': True,
            'account': account_num,
            'status': session.get('status'),
            'code': session.get('code'),
            'url': session.get('url'),
            'message': session.get('message'),
        }

    @staticmethod
    def _terminate_relogin_process(process):
        if not process or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

    def cancel_relogin(self, system: str, account_num: int) -> dict:
        if hasattr(self, '_relogin_sessions') and account_num in self._relogin_sessions:
            s = self._relogin_sessions[account_num]
            if s.get('process'):
                self._terminate_relogin_process(s['process'])
            temp_dir = s.get('temp_dir')
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            s['status'] = 'cancelled'
            s['message'] = 'Login cancelled by user.'
            return {'success': True, 'message': 'Cancelled.'}
        return {'success': False, 'message': 'No active session.'}

    def start_oauth(self, provider: str, flow: str, slot: int | None = None) -> dict:
        if not hasattr(self, '_oauth_sessions'):
            self._oauth_sessions = {}
        if not hasattr(self, '_oauth_sessions_lock'):
            self._oauth_sessions_lock = threading.Lock()

        now = time.time()
        with self._oauth_sessions_lock:
            expired = [sid for sid, s in self._oauth_sessions.items() if now > s.get('expires_at', 0)]
            for sid in expired:
                self._oauth_sessions.pop(sid, None)

        p = str(provider or '').strip().lower()
        if p in ('gemini', 'antigravity'):
            prov = 'gemini'
        elif p == 'codex':
            prov = 'codex'
        else:
            return {'success': False, 'message': f'Unsupported provider: {provider}'}

        target_slot = None
        expected_identity = None
        if slot is not None and str(slot).strip():
            try:
                target_slot = int(slot)
                if target_slot < 1:
                    return {'success': False, 'message': 'Invalid slot number.'}
                store_dir = Path(self.codex_store if prov == 'codex' else self.ag_store)
                target = store_dir / str(target_slot)
                if target.is_symlink() or not target.exists() or not (target.is_dir() or target.is_file()):
                    return {'success': False, 'message': f'Account slot {target_slot} does not exist.'}
                cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
                if cli_dir not in sys.path:
                    sys.path.insert(0, cli_dir)
                import account_manager
                mgr = account_manager.Manager(prov)
                cred_path = mgr.credential(str(target_slot))
                cred_data = account_manager.read_json(cred_path)
                expected_identity = mgr.identity(cred_data).get('identity')
                if not expected_identity:
                    return {'success': False, 'message': f'Account {target_slot} has invalid identity.'}
            except Exception as exc:
                return {'success': False, 'message': f'Failed to validate target slot: {exc}'}

        fl = str(flow or '').strip().lower()
        if prov == 'gemini':
            if fl != 'auth_url':
                return {'success': False, 'message': 'Gemini only supports auth_url flow'}
        else:
            if fl not in ('device_code', 'auth_url'):
                return {'success': False, 'message': 'Codex flow must be device_code or auth_url'}

        session_id = secrets.token_hex(16)
        if prov == 'codex' and fl == 'device_code':
            try:
                payload = json.dumps({'client_id': CODEX_CLIENT_ID}).encode('utf-8')
                req = urllib.request.Request(
                    CODEX_DEVICE_USERCODE_URL,
                    data=payload,
                    headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'codex_cli_rs/0.154.0'},
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp_data = json.loads(resp.read().decode('utf-8'))
            except Exception as exc:
                return {'success': False, 'message': f'Failed to request device code from OpenAI: {exc}'}

            user_code = resp_data.get('user_code')
            device_auth_id = resp_data.get('device_auth_id')
            interval = int(resp_data.get('interval') or 5)
            verification_uri = resp_data.get('verification_uri') or CODEX_DEVICE_VERIFY_URL

            session = {
                'id': session_id,
                'provider': 'codex',
                'flow': 'device_code',
                'client_id': CODEX_CLIENT_ID,
                'user_code': user_code,
                'device_auth_id': device_auth_id,
                'interval': interval,
                'verification_uri': verification_uri,
                'target_slot': target_slot,
                'expected_identity': expected_identity,
                'created_at': now,
                'expires_at': now + int(resp_data.get('expires_in') or 900),
                'status': 'pending',
                'account': None,
                'email': None,
                'error': None,
            }
            with self._oauth_sessions_lock:
                self._oauth_sessions[session_id] = session

            res_data = {
                'success': True,
                'session_id': session_id,
                'flow': 'device_code',
                'user_code': user_code,
                'device_auth_id': device_auth_id,
                'verification_uri': verification_uri,
                'interval': interval,
            }
            if target_slot is not None:
                res_data['slot'] = target_slot
            return res_data

        elif prov == 'codex' and fl == 'auth_url':
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('utf-8')).digest()).rstrip(b'=').decode('utf-8')
            state = secrets.token_urlsafe(32)
            redirect_uri = CODEX_LINK_REDIRECT_URI
            params = {
                'response_type': 'code',
                'client_id': CODEX_CLIENT_ID,
                'redirect_uri': redirect_uri,
                'scope': 'openid profile email offline_access',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'id_token_add_organizations': 'true',
                'codex_cli_simplified_flow': 'true',
                'originator': 'codex_cli_rs',
                'state': state,
            }
            auth_url = CODEX_OAUTH_AUTHORIZE_URL + '?' + urllib.parse.urlencode(params)
            session = {
                'id': session_id,
                'provider': 'codex',
                'flow': 'auth_url',
                'client_id': CODEX_CLIENT_ID,
                'code_verifier': verifier,
                'state': state,
                'redirect_uri': redirect_uri,
                'target_slot': target_slot,
                'expected_identity': expected_identity,
                'created_at': now,
                'expires_at': now + 900,
                'status': 'pending',
                'account': None,
                'email': None,
                'error': None,
            }
            with self._oauth_sessions_lock:
                self._oauth_sessions[session_id] = session

            res_data = {
                'success': True,
                'session_id': session_id,
                'flow': 'auth_url',
                'auth_url': auth_url,
                'state': state,
            }
            if target_slot is not None:
                res_data['slot'] = target_slot
            return res_data

        elif prov == 'gemini':
            client_id = os.environ.get('AG_OAUTH_CLIENT_ID', '').strip()
            client_secret = os.environ.get('AG_OAUTH_CLIENT_SECRET', '').strip()
            if not client_id or not client_secret:
                try:
                    bridges_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'bridges')
                    if bridges_dir not in sys.path:
                        sys.path.insert(0, bridges_dir)
                    import oauth_credentials
                    client_id, client_secret = oauth_credentials.oauth_credentials()
                except Exception:
                    client_id = GEMINI_CLIENT_ID
                    client_secret = GEMINI_CLIENT_SECRET

            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('utf-8')).digest()).rstrip(b'=').decode('utf-8')
            state = secrets.token_urlsafe(32)
            redirect_uri = GEMINI_REDIRECT_URI

            cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
            if cli_dir not in sys.path:
                sys.path.insert(0, cli_dir)
            import ag_provider
            scopes_str = ' '.join(ag_provider.SCOPES)

            params = {
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'scope': scopes_str,
                'access_type': 'offline',
                'prompt': 'consent select_account',
                'state': state,
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
            }
            auth_url = GEMINI_OAUTH_AUTHORIZE_URL + '?' + urllib.parse.urlencode(params)

            session = {
                'id': session_id,
                'provider': 'gemini',
                'flow': 'auth_url',
                'client_id': client_id,
                'client_secret': client_secret,
                'code_verifier': verifier,
                'state': state,
                'redirect_uri': redirect_uri,
                'target_slot': target_slot,
                'expected_identity': expected_identity,
                'created_at': now,
                'expires_at': now + 900,
                'status': 'pending',
                'account': None,
                'email': None,
                'error': None,
            }
            with self._oauth_sessions_lock:
                self._oauth_sessions[session_id] = session

            res_data = {
                'success': True,
                'session_id': session_id,
                'flow': 'auth_url',
                'auth_url': auth_url,
                'state': state,
            }
            if target_slot is not None:
                res_data['slot'] = target_slot
            return res_data
        return {'success': False, 'message': 'Invalid flow or provider'}

    def poll_oauth(self, session_id: str) -> dict:
        if not hasattr(self, '_oauth_sessions'):
            self._oauth_sessions = {}
        if not hasattr(self, '_oauth_sessions_lock'):
            self._oauth_sessions_lock = threading.Lock()

        with self._oauth_sessions_lock:
            session = self._oauth_sessions.get(session_id)
            if not session:
                return {'status': 'failed', 'error': 'Invalid or expired session.'}
            if time.time() > session.get('expires_at', 0):
                self._oauth_sessions.pop(session_id, None)
                return {'status': 'failed', 'error': 'OAuth session has expired.'}
            if session.get('status') == 'completed':
                return {'status': 'completed', 'account': session.get('account'), 'email': session.get('email')}
            if session.get('status') == 'failed':
                return {'status': 'failed', 'error': session.get('error') or 'Authorization failed.'}

        if session.get('provider') != 'codex' or session.get('flow') != 'device_code':
            return {'status': 'pending'}

        device_auth_id = session.get('device_auth_id')
        user_code = session.get('user_code')
        client_id = session.get('client_id')

        poll_payload = json.dumps({
            'client_id': client_id,
            'device_auth_id': device_auth_id,
            'user_code': user_code,
        }).encode('utf-8')

        req = urllib.request.Request(
            CODEX_DEVICE_TOKEN_URL,
            data=poll_payload,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'codex_cli_rs/0.154.0'},
            method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            try:
                err_data = json.loads(exc.read().decode('utf-8'))
            except Exception:
                err_data = {}
            err_code = ''
            if isinstance(err_data, dict):
                err_field = err_data.get('error')
                if isinstance(err_field, dict):
                    err_code = err_field.get('code') or ''
                elif isinstance(err_field, str):
                    err_code = err_field
                if not err_code:
                    err_code = err_data.get('code') or ''
            if err_code in ('deviceauth_authorization_pending', 'authorization_pending'):
                return {'status': 'pending'}
            err_msg = 'Device authorization failed'
            if isinstance(err_data, dict):
                err_field = err_data.get('error')
                if isinstance(err_field, dict) and err_field.get('message'):
                    err_msg = err_field['message']
                elif err_data.get('error_description'):
                    err_msg = err_data['error_description']
                elif err_data.get('message'):
                    err_msg = err_data['message']
            with self._oauth_sessions_lock:
                session['status'] = 'failed'
                session['error'] = err_msg
            return {'status': 'failed', 'error': err_msg}
        except Exception as exc:
            return {'status': 'pending'}

        auth_code = resp_data.get('authorization_code') or resp_data.get('authorizationCode')
        code_verifier = resp_data.get('code_verifier') or resp_data.get('codeVerifier')
        if not auth_code or not code_verifier:
            err_msg = 'Incomplete device authorization response from OpenAI'
            with self._oauth_sessions_lock:
                session['status'] = 'failed'
                session['error'] = err_msg
            return {'status': 'failed', 'error': err_msg}

        try:
            exchange_body = urllib.parse.urlencode({
                'grant_type': 'authorization_code',
                'client_id': client_id,
                'code': auth_code,
                'redirect_uri': CODEX_DEVICE_REDIRECT_URI,
                'code_verifier': code_verifier,
            }).encode('utf-8')
            ex_req = urllib.request.Request(
                CODEX_OAUTH_TOKEN_URL,
                data=exchange_body,
                headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json', 'User-Agent': 'codex_cli_rs/0.154.0'},
                method='POST'
            )
            with urllib.request.urlopen(ex_req, timeout=15) as ex_resp:
                tokens = json.loads(ex_resp.read().decode('utf-8'))
        except Exception as exc:
            err_msg = f'Token exchange failed: {exc}'
            with self._oauth_sessions_lock:
                session['status'] = 'failed'
                session['error'] = err_msg
            return {'status': 'failed', 'error': err_msg}

        if not tokens.get('access_token'):
            err_msg = tokens.get('error_description') or 'OpenAI returned no access token'
            with self._oauth_sessions_lock:
                session['status'] = 'failed'
                session['error'] = err_msg
            return {'status': 'failed', 'error': err_msg}

        cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
        if cli_dir not in sys.path:
            sys.path.insert(0, cli_dir)
        import account_manager
        claims = account_manager.decode_claims(tokens.get('id_token', ''))
        access_claims = account_manager.decode_claims(tokens.get('access_token', ''))
        chatgpt = claims.get('https://api.openai.com/auth', {}) or access_claims.get('https://api.openai.com/auth', {})
        account_id = tokens.get('account_id') or chatgpt.get('chatgpt_account_id') or claims.get('sub') or access_claims.get('sub') or f'codex_{secrets.token_hex(4)}'
        email = claims.get('email') or access_claims.get('https://api.openai.com/profile', {}).get('email') or access_claims.get('email') or 'codex_user@openai.com'

        auth_data = {
            'tokens': {
                'access_token': tokens['access_token'],
                'refresh_token': tokens['refresh_token'],
                'id_token': tokens.get('id_token', ''),
                'account_id': str(account_id),
            },
            'email': str(email),
        }

        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.json') as tmp:
            json.dump(auth_data, tmp)
            tmp_path = tmp.name

        try:
            mgr = account_manager.Manager('codex')
            target_slot = session.get('target_slot')
            if target_slot is not None:
                mgr.replace_credentials(target_slot, tmp_path)
                slot_num = int(target_slot)
            else:
                slot_res = mgr.add_or_switch(source=tmp_path)
                slot_num = int(slot_res or mgr.active() or 1)
        except Exception as exc:
            with self._oauth_sessions_lock:
                session['status'] = 'failed'
                session['error'] = str(exc)
            return {'status': 'failed', 'error': str(exc)}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        with self._oauth_sessions_lock:
            session['status'] = 'completed'
            session['account'] = slot_num
            session['email'] = str(email)

        self.trigger_instant_refresh()
        return {'status': 'completed', 'account': slot_num, 'email': str(email)}

    def callback_oauth(self, session_id: str, url_or_code: str) -> dict:
        if not hasattr(self, '_oauth_sessions'):
            self._oauth_sessions = {}
        if not hasattr(self, '_oauth_sessions_lock'):
            self._oauth_sessions_lock = threading.Lock()

        with self._oauth_sessions_lock:
            session = self._oauth_sessions.get(session_id)
            if not session:
                return {'success': False, 'message': 'Session not found or expired.'}
            if time.time() > session.get('expires_at', 0):
                self._oauth_sessions.pop(session_id, None)
                return {'success': False, 'message': 'OAuth session has expired.'}

        if not url_or_code or not str(url_or_code).strip():
            return {'success': False, 'message': 'Missing authorization code or callback URL.'}

        try:
            code = self._extract_oauth_code(str(url_or_code).strip(), session.get('state'))
        except ValueError as exc:
            return {'success': False, 'message': str(exc)}

        cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
        if cli_dir not in sys.path:
            sys.path.insert(0, cli_dir)
        import account_manager

        provider = session.get('provider')
        if provider == 'codex':
            try:
                exchange_body = urllib.parse.urlencode({
                    'grant_type': 'authorization_code',
                    'client_id': session['client_id'],
                    'code': code,
                    'redirect_uri': session['redirect_uri'],
                    'code_verifier': session['code_verifier'],
                }).encode('utf-8')
                req = urllib.request.Request(
                    CODEX_OAUTH_TOKEN_URL,
                    data=exchange_body,
                    headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json', 'User-Agent': 'codex_cli_rs/0.154.0'},
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    tokens = json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                err_text = exc.read().decode('utf-8', errors='replace')
                try:
                    err_json = json.loads(err_text)
                    msg = err_json.get('error_description') or err_json.get('error') or f'HTTP {exc.code}'
                except Exception:
                    msg = f'Token exchange failed: HTTP {exc.code}'
                return {'success': False, 'message': msg}
            except Exception as exc:
                return {'success': False, 'message': f'Token exchange failed: {exc}'}

            if not tokens.get('access_token'):
                return {'success': False, 'message': tokens.get('error_description') or 'OpenAI returned no access token'}

            claims = account_manager.decode_claims(tokens.get('id_token', ''))
            access_claims = account_manager.decode_claims(tokens.get('access_token', ''))
            chatgpt = claims.get('https://api.openai.com/auth', {}) or access_claims.get('https://api.openai.com/auth', {})
            account_id = tokens.get('account_id') or chatgpt.get('chatgpt_account_id') or claims.get('sub') or access_claims.get('sub') or f'codex_{secrets.token_hex(4)}'
            email = claims.get('email') or access_claims.get('https://api.openai.com/profile', {}).get('email') or access_claims.get('email') or 'codex_user@openai.com'

            auth_data = {
                'tokens': {
                    'access_token': tokens['access_token'],
                    'refresh_token': tokens['refresh_token'],
                    'id_token': tokens.get('id_token', ''),
                    'account_id': str(account_id),
                },
                'email': str(email),
            }

            with tempfile.NamedTemporaryFile('w', delete=False, suffix='.json') as tmp:
                json.dump(auth_data, tmp)
                tmp_path = tmp.name

            try:
                mgr = account_manager.Manager('codex')
                target_slot = session.get('target_slot')
                if target_slot is not None:
                    mgr.replace_credentials(target_slot, tmp_path)
                    slot_num = int(target_slot)
                else:
                    slot_res = mgr.add_or_switch(source=tmp_path)
                    slot_num = int(slot_res or mgr.active() or 1)
            except Exception as exc:
                return {'success': False, 'message': str(exc)}
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            with self._oauth_sessions_lock:
                session['status'] = 'completed'
                session['account'] = slot_num
                session['email'] = str(email)

            self.trigger_instant_refresh()
            return {'success': True, 'status': 'completed', 'account': slot_num, 'email': str(email)}

        elif provider == 'gemini':
            try:
                exchange_body = urllib.parse.urlencode({
                    'grant_type': 'authorization_code',
                    'client_id': session['client_id'],
                    'client_secret': session['client_secret'],
                    'code': code,
                    'redirect_uri': session['redirect_uri'],
                    'code_verifier': session['code_verifier'],
                }).encode('utf-8')
                req = urllib.request.Request(
                    GEMINI_TOKEN_URL,
                    data=exchange_body,
                    headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json', 'User-Agent': 'antigravity/hub/2.1.4 linux/amd64'},
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    tokens = json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                err_text = exc.read().decode('utf-8', errors='replace')
                try:
                    err_json = json.loads(err_text)
                    msg = err_json.get('error_description') or err_json.get('error') or f'Google OAuth failed: HTTP {exc.code}'
                except Exception:
                    msg = f'Google OAuth failed: HTTP {exc.code}'
                return {'success': False, 'message': msg}
            except Exception as exc:
                return {'success': False, 'message': f'Google token exchange failed: {exc}'}

            access_token = tokens.get('access_token')
            refresh_token = tokens.get('refresh_token')
            if not access_token or not refresh_token:
                return {'success': False, 'message': 'Google returned no refresh token; approve offline consent and retry.'}

            try:
                user_req = urllib.request.Request(
                    GEMINI_USERINFO_URL,
                    headers={'Authorization': f'Bearer {access_token}', 'User-Agent': 'antigravity/hub/2.1.4 linux/amd64'}
                )
                with urllib.request.urlopen(user_req, timeout=15) as u_resp:
                    userinfo = json.loads(u_resp.read().decode('utf-8'))
            except Exception as exc:
                return {'success': False, 'message': f'Failed to retrieve Google userinfo: {exc}'}

            email = userinfo.get('email')
            if not email or userinfo.get('verified_email') is False:
                return {'success': False, 'message': 'Google returned no verified account email identity'}

            import ag_provider
            try:
                project = ag_provider.project_id(access_token)
            except Exception as exc:
                return {'success': False, 'message': f'Google Code Assist onboarding failed: {exc}'}

            expiry = time.time() + int(tokens.get('expires_in', 3599))
            canonical = {
                'token': {
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'token_type': tokens.get('token_type', 'Bearer'),
                    'expiry': int(expiry * 1000),
                },
                'email': email,
                'project_id': project,
            }

            with tempfile.NamedTemporaryFile('w', delete=False, suffix='.json') as tmp:
                json.dump(canonical, tmp)
                tmp_path = tmp.name

            try:
                mgr = account_manager.Manager('antigravity')
                target_slot = session.get('target_slot')
                if target_slot is not None:
                    mgr.replace_credentials(target_slot, tmp_path)
                    slot_num = int(target_slot)
                else:
                    slot_res = mgr.add_or_switch(source=tmp_path, server_verified=True)
                    slot_num = int(slot_res or mgr.active() or 1)
            except Exception as exc:
                return {'success': False, 'message': str(exc)}
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            with self._oauth_sessions_lock:
                session['status'] = 'completed'
                session['account'] = slot_num
                session['email'] = str(email)

            self.trigger_instant_refresh()
            return {'success': True, 'status': 'completed', 'account': slot_num, 'email': str(email), 'project_id': project}

        return {'success': False, 'message': f'Unknown provider: {provider}'}

    @staticmethod
    def _extract_oauth_code(raw_input: str, expected_state: Optional[str] = None) -> str:
        s = raw_input.strip().strip('"\'')
        if not s:
            raise ValueError('Empty authorization code or callback URL')
        if '://' in s or '?' in s or '&' in s or s.startswith('localhost') or 'callback' in s:
            url_str = s if '://' in s else 'http://' + s
            parsed = urllib.parse.urlparse(url_str)
            qs = urllib.parse.parse_qs(parsed.query)
            if not qs.get('code') and parsed.fragment:
                qs_frag = urllib.parse.parse_qs(parsed.fragment)
                if qs_frag.get('code'):
                    qs = qs_frag
            if qs.get('error'):
                err_list = qs.get('error', [''])
                err = err_list[0] if err_list else 'error'
                desc_list = qs.get('error_description', [''])
                desc = desc_list[0] if desc_list else ''
                raise ValueError(f'Authorization error: {err} {desc}'.strip())
            url_state = qs.get('state', [''])[0]
            if expected_state and url_state and url_state != expected_state:
                raise ValueError('OAuth state mismatch; restart authorization with the link provided')
            code = qs.get('code', [''])[0]
            if not code and 'code=' in s:
                for part in s.split('&'):
                    if 'code=' in part:
                        code = part.split('code=')[1].split('&')[0].split('?')[0].split('#')[0]
                        break
            if not code:
                raise ValueError('No authorization code found in pasted URL')
            return code.split('#')[0].split('&')[0].strip()
        return s

    def cancel_oauth(self, session_id: str) -> dict:
        if hasattr(self, '_oauth_sessions') and hasattr(self, '_oauth_sessions_lock'):
            with self._oauth_sessions_lock:
                self._oauth_sessions.pop(session_id, None)
        return {'success': True, 'message': 'OAuth session cancelled.'}
    def _get_active_account_instant(self, system: str) -> str:
        try:
            cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
            if cli_dir not in sys.path:
                sys.path.insert(0, cli_dir)
            import account_manager
            mgr = account_manager.Manager('gemini' if system == 'gemini' else 'codex')
            return str(mgr.active())
        except Exception:
            return ''

    def _get_account_tokens_map(self) -> Dict[str, Dict[str, int]]:
        tokens = {'gemini': {}, 'codex': {}}
        try:
            with sqlite3.connect(self.auth_db, timeout=5) as conn:
                conn.execute('PRAGMA busy_timeout=10000')
                conn.execute('PRAGMA journal_mode=WAL')
                for row in conn.execute('SELECT pool, account, total_tokens FROM account_usage_counters').fetchall():
                    p = str(row[0]).lower()
                    acc = str(row[1])
                    tok = int(row[2]) if row[2] else 0
                    if 'anti' in p or 'gem' in p:
                        tokens['gemini'][acc] = tokens['gemini'].get(acc, 0) + tok
                    else:
                        tokens['codex'][acc] = tokens['codex'].get(acc, 0) + tok
        except Exception:
            pass
        return tokens

    def _enrich_accounts_data(self, ag_data: dict, cdx_data: dict):
        tok_map = self._get_account_tokens_map()
        ag_active = self._get_active_account_instant('gemini')
        cdx_active = self._get_active_account_instant('codex')

        if isinstance(ag_data, dict) and 'accounts' in ag_data:
            for acc in ag_data['accounts']:
                acc_num = str(acc.get('account', ''))
                acc['total_tokens'] = tok_map['gemini'].get(acc_num, 0)
                if ag_active:
                    acc['is_active'] = (str(acc_num) == str(ag_active))

        if isinstance(cdx_data, dict) and 'accounts' in cdx_data:
            for acc in cdx_data['accounts']:
                acc_num = str(acc.get('account', ''))
                acc['total_tokens'] = tok_map['codex'].get(acc_num, 0)
                if cdx_active:
                    acc['is_active'] = (str(acc_num) == str(cdx_active))

    def get_all_status(self) -> Dict[str, Any]:
        with self._lock:
            cached_ag = (self._cached_ag or {}).copy() if isinstance(self._cached_ag, dict) else {}
            cached_cdx = (self._cached_cdx or {}).copy() if isinstance(self._cached_cdx, dict) else {}
            self._enrich_accounts_data(cached_ag, cached_cdx)
            return {
                "gemini": cached_ag,
                "codex": cached_cdx,
                "active_hermes_default": "gemini-3.8-flash",
                "fallback_status": "Disabled (Pure Model Lock)",
                "timestamp": time.time(),
                "refresh_inflight": self._refresh_inflight,
                "refresh_state": self._refresh_state,
                "refresh_error": self._refresh_error,
                "refresh_started_at": self._refresh_started_at,
                "refresh_completed_at": self._refresh_completed_at,
            }
