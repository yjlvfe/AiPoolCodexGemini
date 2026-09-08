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
import datetime
import secrets
import sqlite3
import subprocess
import shutil
import threading
import re
from typing import Dict, Any, Optional, List

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("AUTH_DB_PATH", os.path.join(_CURRENT_DIR, "auth.db"))
HERMES_STATE_DB = os.environ.get("HERMES_STATE_DB", os.path.expanduser("~/.hermes/state.db"))

class TokenAuthManager:
    def __init__(self, db_path: str = DB_PATH, session_expiry_hours: int = 24):
        self.db_path = db_path
        self.session_expiry_seconds = session_expiry_hours * 3600
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
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
                    created_at REAL,
                    expires_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model TEXT,
                    pool TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    timestamp REAL,
                    time_formatted TEXT
                )
            """)
            conn.commit()

    def generate_magic_link(self, base_url: str, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO magic_tokens (token, user_id, created_at, used) VALUES (?, ?, ?, 0)",
                (token, user_id, now)
            )
            conn.execute("DELETE FROM magic_tokens WHERE created_at < ?", (now - 7200,))
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
            if now - row["created_at"] > 7200:
                conn.execute("DELETE FROM magic_tokens WHERE token = ?", (token,))
                conn.commit()
                return None

            conn.execute("UPDATE magic_tokens SET used = 1 WHERE token = ?", (token,))
            session_id = secrets.token_hex(32)
            expires_at = now + self.session_expiry_seconds
            conn.execute("""
                INSERT INTO authorized_devices (session_id, user_id, client_ip, user_agent, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, row["user_id"], client_ip, user_agent, now, expires_at))
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
            return True

    def record_request_event(self, model: str, pool: str, prompt_tok: int, comp_tok: int, tot_tok: int):
        now = time.time()
        # Pure numeric 24-hour format: MM/DD HH:MM
        dt_str = datetime.datetime.fromtimestamp(now).strftime("%m/%d %H:%M")
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO request_events 
                   (model, pool, prompt_tokens, completion_tokens, total_tokens, timestamp, time_formatted)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (model, pool, prompt_tok, comp_tok, tot_tok, now, dt_str)
            )
            # Keep table bounded to last 500 records
            conn.execute("DELETE FROM request_events WHERE id NOT IN (SELECT id FROM request_events ORDER BY id DESC LIMIT 500)")
            conn.commit()

def parse_agusage(raw: str) -> List[Dict[str, Any]]:
    accounts = []
    blocks = re.split(r'(Account\s+\d+.*?)(?=(?:Account\s+\d+)|$)', raw, flags=re.DOTALL)
    for b in blocks:
        b = b.strip()
        if not b.startswith("Account"):
            continue
        header = b.splitlines()[0]
        acc_num = int(re.search(r'Account\s+(\d+)', header).group(1))
        is_active = "[ACTIVE]" in header
        email_m = re.search(r'Email:\s*([^\s\n]+)', b)
        email = email_m.group(1) if email_m else "Unknown"

        gem_5h = re.search(r'Gemini Models.*?5-hour:.*?\]\s*(\d+)%\s*left.*?Reset in:\s*([^\n]+)', b, re.DOTALL)
        gem_wk = re.search(r'Gemini Models.*?Weekly:.*?\]\s*(\d+)%\s*left.*?Reset in:\s*([^\n]+)', b, re.DOTALL)
        cld_5h = re.search(r'Claude and GPT models.*?5-hour:.*?\]\s*(\d+)%\s*left.*?Reset in:\s*([^\n]+)', b, re.DOTALL)
        cld_wk = re.search(r'Claude and GPT models.*?Weekly:.*?\]\s*(\d+)%\s*left.*?Reset in:\s*([^\n]+)', b, re.DOTALL)

        accounts.append({
            "account": acc_num,
            "is_active": is_active,
            "email": email,
            "gemini": {
                "5h_pct": int(gem_5h.group(1)) if gem_5h else 0,
                "5h_reset": gem_5h.group(2).strip() if gem_5h else "",
                "wk_pct": int(gem_wk.group(1)) if gem_wk else 0,
                "wk_reset": gem_wk.group(2).strip() if gem_wk else ""
            },
            "claude": {
                "5h_pct": int(cld_5h.group(1)) if cld_5h else 0,
                "5h_reset": cld_5h.group(2).strip() if cld_5h else "",
                "wk_pct": int(cld_wk.group(1)) if cld_wk else 0,
                "wk_reset": cld_wk.group(2).strip() if cld_wk else ""
            }
        })
    return accounts

def parse_cusage(raw: str) -> List[Dict[str, Any]]:
    accounts = []
    blocks = re.split(r'(Account\s+\d+.*?)(?=(?:Account\s+\d+)|$)', raw, flags=re.DOTALL)
    for b in blocks:
        b = b.strip()
        if not b.startswith("Account"):
            continue
        header = b.splitlines()[0]
        acc_num = int(re.search(r'Account\s+(\d+)', header).group(1))
        is_active = "[ACTIVE]" in header
        email_m = re.search(r'Email:\s*([^\s\n]+)', b)
        email = email_m.group(1) if email_m else "Unknown"
        plan_m = re.search(r'Plan:\s*([^\n]+)', b)
        plan = plan_m.group(1).strip() if plan_m else "ChatGPT Plus"
        h5 = re.search(r'5-hour:.*?\]\s*(\d+)%\s*left.*?Reset in:\s*([^\n]+)', b, re.DOTALL)
        wk = re.search(r'Weekly:.*?\]\s*(\d+)%\s*left.*?Reset in:\s*([^\n]+)', b, re.DOTALL)

        accounts.append({
            "account": acc_num,
            "is_active": is_active,
            "email": email,
            "plan": plan,
            "5h_pct": int(h5.group(1)) if h5 else 0,
            "5h_reset": h5.group(2).strip() if h5 else "",
            "wk_pct": int(wk.group(1)) if wk else 0,
            "wk_reset": wk.group(2).strip() if wk else ""
        })
    return accounts

class PoolManager:
    """High Performance Pool Manager with Asynchronous Background Poller.
    Zero blocking on user requests: API returns in <10 milliseconds!
    """
    def __init__(self):
        self.ag_store = os.environ.get("AG_ACCOUNT_STORE", os.path.expanduser("~/.antigravity-accounts"))
        self.codex_store = os.environ.get("CODEX_ACCOUNT_STORE", os.path.expanduser("~/.codex-accounts"))
        self.state_db = HERMES_STATE_DB
        self.auth_db = DB_PATH
        
        self._lock = threading.Lock()
        self._cached_ag = None
        self._cached_cdx = None
        self._cached_logs = None
        self._running = True

        # Start continuous background daemon thread
        self._worker_thread = threading.Thread(target=self._background_poller, daemon=True)
        self._worker_thread.start()

    def _background_poller(self):
        while self._running:
            try:
                self._update_all_background()
            except Exception as e:
                print(f"[pool-worker] Polling error: {e}")
            # Poll every 10 seconds asynchronously
            time.sleep(10)

    def _update_all_background(self):
        import sys
        cli_dir = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli')
        if cli_dir not in sys.path:
            sys.path.insert(0, cli_dir)
        from account_reports import pool_report
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as executor:
            ag_future = executor.submit(pool_report, 'antigravity')
            cdx_future = executor.submit(pool_report, 'codex')
            ag_data, cdx_data = ag_future.result(), cdx_future.result()

        # 3. Logs & Analytics
        logs_data = self._build_logs_report()

        with self._lock:
            self._cached_ag = ag_data
            self._cached_cdx = cdx_data
            if logs_data:
                logs_data["status"] = {
                    "antigravity": ag_data,
                    "codex": cdx_data
                }
            self._cached_logs = logs_data

    def _build_logs_report(self):
        import sys
        bridge_dir = os.path.join(os.path.dirname(_CURRENT_DIR),'bridges')
        if bridge_dir not in sys.path:
            sys.path.insert(0,bridge_dir)
        import pool_runtime
        result = pool_runtime.report(self.auth_db)
        result['status'] = self.get_all_status()
        return result

    # INSTANT RESPONSE METHODS (<1 millisecond):
    def get_antigravity_status(self) -> Dict[str, Any]:
        with self._lock:
            return self._cached_ag or {}

    def get_codex_status(self) -> Dict[str, Any]:
        with self._lock:
            return self._cached_cdx or {}

    def trigger_instant_refresh(self):
        # Refresh logs and DB queries exclusively for logs, no account quota checks
        try:
            new_logs = self._build_logs_report()
            with self._lock:
                self._cached_logs = new_logs
        except Exception as e:
            print(f"[pool] refresh error: {e}")

    def get_usage_logs_report(self) -> Dict[str, Any]:
        # Only actual requests recorded by the pool bridges; no session/log estimates.
        return self._build_logs_report()

    def switch_account(self, system: str, account_num: int) -> bool:
        if system not in ('antigravity', 'codex') or not isinstance(account_num, int) or account_num < 1:
            return False
        import sys
        cmd = os.path.join(os.path.dirname(_CURRENT_DIR), 'cli', 'ag' if system == 'antigravity' else 'cx')
        # Dashboard only switches existing slots; interactive enrollment belongs in a terminal.
        store = self.ag_store if system == 'antigravity' else self.codex_store
        if not os.path.exists(os.path.join(store, str(account_num))):
            return False
        res = subprocess.run([sys.executable, cmd, 'switch', str(account_num)], capture_output=True, text=True, timeout=150)
        threading.Thread(target=self._update_all_background, daemon=True).start()
        return res.returncode == 0

    def get_all_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "antigravity": self._cached_ag or {},
                "codex": self._cached_cdx or {},
                "active_hermes_default": "gemini-3.8-flash-tiered",
                "fallback_status": "Disabled (Pure Model Lock)",
                "timestamp": time.time()
            }
