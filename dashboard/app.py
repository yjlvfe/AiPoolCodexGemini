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
import sqlite3
import subprocess
import threading
from typing import Dict, Any, Optional

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("AUTH_DB_PATH", os.path.join(_CURRENT_DIR, "auth.db"))
DEFAULT_SESSION_EXPIRY_HOURS = 30 * 24


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
            conn.commit()

    def generate_magic_link(self, base_url: str, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO magic_tokens (token, user_id, created_at, used) VALUES (?, ?, ?, 0)",
                (token, user_id, now)
            )
            conn.execute("DELETE FROM magic_tokens WHERE created_at < ?", (now - 1800,))
            conn.commit()
        return f"{base_url.rstrip('/')}/auth?token={token}"

    def register_device(self, token: str, client_ip: str, user_agent: str) -> Optional[str]:
        if not token:
            return None
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM magic_tokens WHERE token = ?", (token,))
            row = cur.fetchone()
            if not row:
                return None
            if now - row["created_at"] > 1800:
                conn.execute("DELETE FROM magic_tokens WHERE token = ?", (token,))
                conn.commit()
                return None

            consumed = conn.execute(
                "UPDATE magic_tokens SET used = 1 WHERE token = ? AND used = 0 AND created_at >= ?",
                (token, now - 1800),
            )
            if consumed.rowcount != 1:
                return None
            session_id = secrets.token_hex(32)
            expires_at = now + self.session_expiry_seconds
            device_type = device_type_for_user_agent(user_agent)
            conn.execute("""
                INSERT INTO authorized_devices (
                    session_id, user_id, client_ip, user_agent, device_type,
                    created_at, last_seen, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, row["user_id"], client_ip, user_agent, device_type,
                now, now, expires_at,
            ))
            conn.commit()
            return session_id

    def validate_device(self, session_id: str, client_ip: str = "") -> bool:
        if not session_id:
            return False
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM authorized_devices WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
            if not row:
                return False
            if now > row["expires_at"]:
                conn.execute("DELETE FROM authorized_devices WHERE session_id = ?", (session_id,))
                conn.commit()
                return False
            # A magic-link session is bound to the address that redeemed it.
            # This prevents a leaked session cookie from being replayed from a
            # different peer.  Reverse-proxied requests are normalized by the
            # handler before reaching this method.
            if client_ip and not hmac.compare_digest(str(row["client_ip"]), str(client_ip)):
                return False
            # Keep an active trusted device alive for the configured window.
            conn.execute(
                "UPDATE authorized_devices SET last_seen = ?, expires_at = ? WHERE session_id = ?",
                (now, now + self.session_expiry_seconds, session_id),
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
        self._refresh_inflight = False
        self._running = True

        # Start continuous background daemon thread
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
                self._cached_ag = data.get('antigravity')
                self._cached_cdx = data.get('codex')
                self._cached_logs = data.get('logs')
        except (OSError, ValueError, TypeError):
            return

    def _persist_snapshot(self, ag_data, cdx_data, logs_data):
        payload = {'saved_at': time.time(), 'antigravity': ag_data, 'codex': cdx_data, 'logs': logs_data}
        try:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._snapshot_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, self._snapshot_path)
        except (OSError, TypeError, ValueError):
            return

    def _background_poller(self):
        while self._running:
            try:
                self._update_all_background()
            except RuntimeError:
                pass
            except Exception as e:
                print(f"[pool-worker] Polling error: {e}")
            # Poll every 10 seconds asynchronously
            time.sleep(10)

    def _update_all_background(self):
        try:
            import sys
            cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
            if cli_dir not in sys.path:
                sys.path.insert(0, cli_dir)
            from account_reports import pool_report
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as executor:
                ag_future = executor.submit(pool_report, 'antigravity')
                cdx_future = executor.submit(pool_report, 'codex')
                try:
                    ag_data = ag_future.result()
                    cdx_data = cdx_future.result()
                except Exception as exc:
                    print(f"[pool-worker] account refresh failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                    raise
            print(f"[pool-worker] account refresh ok codex={cdx_data.get('pool_metrics', {}).get('healthy_accounts', 0)}", flush=True)

            # Publish account reports before optional analytics. A log/metrics
            # failure must never hide a valid account snapshot.
            logs_data = None
            try:
                logs_data = self._build_logs_report()
            except Exception as exc:
                print(f"[pool-worker] analytics refresh skipped: {type(exc).__name__}", flush=True)

            # Never replace a healthy persisted snapshot with a transient
            # all-failed report. Keep per-provider last-known-good data.
            with self._lock:
                if not (isinstance(ag_data, dict) and ag_data.get('pool_metrics', {}).get('healthy_accounts', 0) == 0 and ag_data.get('accounts')):
                    self._cached_ag = ag_data
                if not (isinstance(cdx_data, dict) and cdx_data.get('pool_metrics', {}).get('healthy_accounts', 0) == 0 and cdx_data.get('accounts')):
                    self._cached_cdx = cdx_data
                if logs_data is not None:
                    logs_data["status"] = {
                        "antigravity": self._cached_ag or ag_data,
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
                        'antigravity': persisted_ag,
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

    def trigger_instant_refresh(self):
        """Return cached data immediately and refresh account reports in background."""
        with self._refresh_lock:
            if self._refresh_inflight:
                return
            self._refresh_inflight = True
        def refresh():
            try:
                self._update_all_background()
            except Exception as e:
                print(f"[pool] refresh error: {e}")
            finally:
                with self._refresh_lock:
                    self._refresh_inflight = False
        threading.Thread(target=refresh, daemon=True).start()

    def get_cached_logs_report(self):
        with self._lock:
            cached = self._cached_logs
        if cached:
            return cached
        return self._build_logs_report()


    def get_usage_logs_report(self) -> Dict[str, Any]:
        # Serve the last snapshot immediately; the worker refreshes it asynchronously.
        return self.get_cached_logs_report()

    def get_request_prompt(self, request_id: str) -> Dict[str, Any]:
        import sys
        bridge_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'bridges')
        if bridge_dir not in sys.path:
            sys.path.insert(0, bridge_dir)
        import request_log
        return request_log.get_request_prompt(self.auth_db, request_id)

    def switch_account(self, system: str, account_num: int) -> tuple:
        if system not in ('antigravity', 'codex') or not isinstance(account_num, int) or account_num < 1:
            return False, 'Invalid system or account number.'
        import sys
        cmd = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli', 'ag' if system == 'antigravity' else 'cx')
        # Dashboard only switches existing slots; interactive enrollment belongs in a terminal.
        store = self.ag_store if system == 'antigravity' else self.codex_store
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
            cached_data = self._cached_ag if system == 'antigravity' else self._cached_cdx
            if cached_data and 'accounts' in cached_data:
                for acc in cached_data['accounts']:
                    acc['is_active'] = (acc.get('account') == account_num)
        return True, detail or f'Switched to account {account_num}'

    def delete_account(self, system: str, account_num: int, confirmation: str = "") -> tuple:
        if confirmation.strip().lower() != 'confirm':
            return False, 'Must type confirm to authorize account deletion.'
        if system not in ('antigravity', 'codex') or not isinstance(account_num, int) or account_num < 1:
            return False, 'Invalid system or account number.'
        import sys
        cmd = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli', 'ag' if system == 'antigravity' else 'cx')
        store = self.ag_store if system == 'antigravity' else self.codex_store
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
            pool_name = 'Antigravity' if system == 'antigravity' else 'Codex'
            with sqlite3.connect(self.auth_db, timeout=5) as conn:
                conn.execute('DELETE FROM account_usage_counters WHERE pool = ? AND account = ?', (pool_name, str(account_num)))
                conn.commit()
        except Exception as exc:
            print(f'[pool] Failed to clear account_usage_counters for {system} #{account_num}: {exc}')

        # Immediate background update to synchronize cache
        self.trigger_instant_refresh()
        return True, f'Account {account_num} deleted successfully.'
    def _get_active_account_instant(self, system: str) -> str:
        try:
            cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
            if cli_dir not in sys.path:
                sys.path.insert(0, cli_dir)
            import account_manager
            mgr = account_manager.Manager('antigravity' if system == 'antigravity' else 'codex')
            return str(mgr.active())
        except Exception:
            return ''

    def _get_account_tokens_map(self) -> Dict[str, Dict[str, int]]:
        tokens = {'antigravity': {}, 'codex': {}}
        try:
            with sqlite3.connect(self.auth_db, timeout=5) as conn:
                for row in conn.execute('SELECT pool, account, total_tokens FROM account_usage_counters').fetchall():
                    p = str(row[0]).lower()
                    acc = str(row[1])
                    tok = int(row[2]) if row[2] else 0
                    if 'anti' in p or 'gem' in p:
                        tokens['antigravity'][acc] = tok
                    else:
                        tokens['codex'][acc] = tok
        except Exception:
            pass
        return tokens

    def _enrich_accounts_data(self, ag_data: dict, cdx_data: dict):
        tok_map = self._get_account_tokens_map()
        ag_active = self._get_active_account_instant('antigravity')
        cdx_active = self._get_active_account_instant('codex')

        if isinstance(ag_data, dict) and 'accounts' in ag_data:
            for acc in ag_data['accounts']:
                acc_num = str(acc.get('account', ''))
                acc['total_tokens'] = tok_map['antigravity'].get(acc_num, 0)
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
                "antigravity": cached_ag,
                "codex": cached_cdx,
                "active_hermes_default": "gemini-3.8-flash",
                "fallback_status": "Disabled (Pure Model Lock)",
                "timestamp": time.time(),
                "refresh_inflight": self._refresh_inflight,
            }
