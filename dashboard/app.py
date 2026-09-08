"""
AI Pool Dashboard & Proxy Core (Asynchronous Background Worker Engine)
"""
import os
import json
import time
import datetime
import secrets
import sqlite3
import subprocess
import threading
import re
from typing import Dict, Any, Optional, List

DB_PATH = "/root/Projects/AiPoolCodexGemini/dashboard/auth.db"
HERMES_STATE_DB = "/root/.hermes/state.db"

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
        self.ag_store = "/root/.antigravity-accounts"
        self.codex_store = "/root/.codex-accounts"
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
        # 1. Antigravity
        try:
            res_ag = subprocess.run(["agusage"], capture_output=True, text=True, timeout=25)
            raw_ag = res_ag.stdout.strip()
        except Exception:
            raw_ag = ""

        active_ag = 1
        active_file_ag = os.path.join(self.ag_store, "active")
        if os.path.exists(active_file_ag):
            try:
                with open(active_file_ag) as f: active_ag = int(f.read().strip())
            except Exception: pass

        parsed_ag = parse_agusage(raw_ag)
        for a in parsed_ag:
            a["is_active"] = (a["account"] == active_ag)
        tot_ag = len(parsed_ag)
        g_5h = sum(a["gemini"]["5h_pct"] for a in parsed_ag)
        g_wk = sum(a["gemini"]["wk_pct"] for a in parsed_ag)
        c_5h = sum(a["claude"]["5h_pct"] for a in parsed_ag)
        c_wk = sum(a["claude"]["wk_pct"] for a in parsed_ag)
        max_ag = tot_ag * 100 if tot_ag else 100

        ag_data = {
            "type": "antigravity",
            "active_account": active_ag,
            "port": 8123,
            "pool_metrics": {
                "total_accounts": tot_ag,
                "gemini": {
                    "5h_pool_pct": round(g_5h / tot_ag) if tot_ag else 0,
                    "5h_pool_total": g_5h,
                    "5h_pool_max": max_ag,
                    "wk_pool_pct": round(g_wk / tot_ag) if tot_ag else 0,
                    "wk_pool_total": g_wk,
                    "wk_pool_max": max_ag
                },
                "claude": {
                    "5h_pool_pct": round(c_5h / tot_ag) if tot_ag else 0,
                    "5h_pool_total": c_5h,
                    "5h_pool_max": max_ag,
                    "wk_pool_pct": round(c_wk / tot_ag) if tot_ag else 0,
                    "wk_pool_total": c_wk,
                    "wk_pool_max": max_ag
                }
            },
            "accounts": parsed_ag,
            "raw_usage": raw_ag
        }

        # 2. Codex
        cdx_accounts = []
        raw_cdx = ""
        try:
            # Query in parallel directly to avoid cusage serial locks
            from concurrent.futures import ThreadPoolExecutor
            import tempfile

            def query_single_codex(n, active_acc):
                with tempfile.TemporaryDirectory() as td:
                    auth_src = f"/root/.codex-accounts/{n}/auth.json"
                    if not os.path.exists(auth_src):
                        return None
                    auth_dst = os.path.join(td, "auth.json")
                    cfg_dst = os.path.join(td, "config.toml")
                    with open(auth_src, "rb") as f:
                        open(auth_dst, "wb").write(f.read())
                    open(cfg_dst, "w").write('cli_auth_credentials_store = "file"\n')
                    env = os.environ.copy()
                    env["CODEX_HOME"] = td
                    try:
                        p = subprocess.run(["/usr/local/libexec/codex-account-query", "usage"], env=env, capture_output=True, text=True, timeout=5)
                        if p.returncode == 0:
                            data = json.loads(p.stdout)
                            h5 = next((w for w in data.get("windows", []) if w.get("windowDurationMins") == 300), {})
                            wk = next((w for w in data.get("windows", []) if w.get("windowDurationMins") == 10080), {})
                            
                            def fmt_duration(reset_ts):
                                if not reset_ts: return "N/A"
                                diff = max(0, int(reset_ts - time.time()))
                                d, rem = divmod(diff, 86400)
                                h, m = divmod(rem, 3600)
                                m = m // 60
                                if d > 0: return f"{d}d {h}h"
                                elif h > 0: return f"{h}h {m}m"
                                else: return f"{m}m"

                            return {
                                "account": n,
                                "is_active": (n == active_acc),
                                "email": data.get("email", f"Account {n}"),
                                "plan": data.get("planType", "plus"),
                                "5h_pct": max(0, min(100, 100 - h5.get("usedPercent", 0))),
                                "5h_reset": fmt_duration(h5.get("resetsAt")),
                                "wk_pct": max(0, min(100, 100 - wk.get("usedPercent", 0))),
                                "wk_reset": fmt_duration(wk.get("resetsAt")),
                            }
                    except Exception:
                        pass
                return None

            active_cdx = 1
            active_file_cdx = os.path.join(self.codex_store, "active")
            if os.path.exists(active_file_cdx):
                try:
                    with open(active_file_cdx) as f: active_cdx = int(f.read().strip())
                except Exception: pass

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(query_single_codex, n, active_cdx) for n in [1, 2, 3]]
                cdx_accounts = [f.result() for f in futures if f.result() is not None]
        except Exception:
            cdx_accounts = []

        if not cdx_accounts:
            # Fallback to parse_cusage if needed
            try:
                res_cdx = subprocess.run(["cusage"], capture_output=True, text=True, timeout=5)
                raw_cdx = res_cdx.stdout.strip()
                cdx_accounts = parse_cusage(raw_cdx)
            except Exception:
                raw_cdx = ""

        parsed_cdx = cdx_accounts
        tot_cdx = len(parsed_cdx)
        h5_sum = sum(a["5h_pct"] for a in parsed_cdx)
        wk_sum = sum(a["wk_pct"] for a in parsed_cdx)
        max_cdx = tot_cdx * 100 if tot_cdx else 100

        cdx_data = {
            "type": "codex",
            "active_account": active_cdx,
            "port": 8124,
            "pool_metrics": {
                "total_accounts": tot_cdx,
                "5h_pool_pct": round(h5_sum / tot_cdx) if tot_cdx else 0,
                "5h_pool_total": h5_sum,
                "5h_pool_max": max_cdx,
                "wk_pool_pct": round(wk_sum / tot_cdx) if tot_cdx else 0,
                "wk_pool_total": wk_sum,
                "wk_pool_max": max_cdx
            },
            "accounts": parsed_cdx,
            "raw_usage": raw_cdx
        }

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
        models_by_provider = {"Antigravity": {}, "Codex": {}}
        ag_total = 0
        ag_calls = 0
        cdx_total = 0
        cdx_calls = 0

        if os.path.exists(self.state_db):
            try:
                conn = sqlite3.connect(self.state_db, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT model, billing_provider,
                           SUM(api_call_count) as calls,
                           SUM(input_tokens) as prompt_tokens,
                           SUM(output_tokens) as completion_tokens,
                           MAX(last_seen) as last_ts
                    FROM session_model_usage
                    GROUP BY model, billing_provider
                """)
                for r in cur.fetchall():
                    raw_m = r["model"] or ""
                    m = raw_m.lower()
                    bp = (r["billing_provider"] or "").lower()

                    provider = None
                    if bp == "openai-codex" or any(x in m for x in ["astra", "luna", "sol", "terra", "gpt-6", "gpt-5.6"]):
                        provider = "Codex"
                    elif any(x in m for x in ["gemini-3", "claude-sonnet", "claude-opus", "gpt-oss"]):
                        provider = "Antigravity"

                    if provider:
                        p_tok = r["prompt_tokens"] or 0
                        c_tok = r["completion_tokens"] or 0
                        t_tok = p_tok + c_tok
                        calls = r["calls"] or 0
                        last_ts = r["last_ts"] or 0

                        if provider == "Antigravity":
                            ag_total += t_tok
                            ag_calls += calls
                        else:
                            cdx_total += t_tok
                            cdx_calls += calls

                        dt_str = "--"
                        if last_ts:
                            try: dt_str = datetime.datetime.fromtimestamp(last_ts).strftime("%b %d, %Y - %I:%M:%S %p")
                            except Exception: pass

                        tg = models_by_provider[provider]
                        if raw_m not in tg:
                            tg[raw_m] = {
                                "model": raw_m,
                                "provider": provider,
                                "requests_count": 0,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "total_tokens": 0,
                                "avg_tokens_per_request": 0,
                                "last_used": dt_str,
                                "last_ts": last_ts
                            }
                        tg[raw_m]["requests_count"] += calls
                        tg[raw_m]["prompt_tokens"] += p_tok
                        tg[raw_m]["completion_tokens"] += c_tok
                        tg[raw_m]["total_tokens"] += t_tok
                        if tg[raw_m]["requests_count"] > 0:
                            tg[raw_m]["avg_tokens_per_request"] = round(tg[raw_m]["total_tokens"] / tg[raw_m]["requests_count"])
                        if last_ts > tg[raw_m]["last_ts"]:
                            tg[raw_m]["last_ts"] = last_ts
                            tg[raw_m]["last_used"] = dt_str
                conn.close()
            except Exception: pass

        # Also merge requests from auth.db (request_events table: Gateway + OpenClaw + Codex bridges)
        auth_db_path = "/root/Projects/AiPoolCodexGemini/dashboard/auth.db"
        if os.path.exists(auth_db_path):
            try:
                c_auth = sqlite3.connect(auth_db_path, timeout=5)
                c_auth.row_factory = sqlite3.Row
                cur_auth = c_auth.cursor()
                cur_auth.execute("""
                    SELECT model, pool,
                           count(*) as calls,
                           sum(prompt_tokens) as p_tok,
                           sum(completion_tokens) as c_tok,
                           sum(total_tokens) as t_tok,
                           max(timestamp) as last_ts
                    FROM request_events
                    GROUP BY model, pool
                """)
                for r in cur_auth.fetchall():
                    raw_m = r["model"] or ""
                    m = raw_m.lower()
                    pool_name = r["pool"] or ""
                    provider = None
                    if "codex" in pool_name.lower() or any(x in m for x in ["astra", "luna", "sol", "terra", "codex", "gpt-6", "gpt-5"]):
                        provider = "Codex"
                    elif "antigravity" in pool_name.lower() or any(x in m for x in ["gemini", "claude", "gpt-oss"]):
                        provider = "Antigravity"
                    
                    if provider:
                        calls = r["calls"] or 0
                        p_tok = r["p_tok"] or 0
                        c_tok = r["c_tok"] or 0
                        t_tok = r["t_tok"] or (p_tok + c_tok)
                        last_ts = r["last_ts"] or 0

                        if provider == "Antigravity":
                            ag_total += t_tok
                            ag_calls += calls
                        else:
                            cdx_total += t_tok
                            cdx_calls += calls

                        dt_str = "--"
                        if last_ts:
                            try: dt_str = datetime.datetime.fromtimestamp(last_ts).strftime("%b %d, %Y - %I:%M:%S %p")
                            except Exception: pass

                        tg = models_by_provider[provider]
                        if raw_m not in tg:
                            tg[raw_m] = {
                                "model": raw_m,
                                "provider": provider,
                                "requests_count": 0,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "total_tokens": 0,
                                "avg_tokens_per_request": 0,
                                "last_used": dt_str,
                                "last_ts": last_ts
                            }
                        tg[raw_m]["requests_count"] += calls
                        tg[raw_m]["prompt_tokens"] += p_tok
                        tg[raw_m]["completion_tokens"] += c_tok
                        tg[raw_m]["total_tokens"] += t_tok
                        if tg[raw_m]["requests_count"] > 0:
                            tg[raw_m]["avg_tokens_per_request"] = round(tg[raw_m]["total_tokens"] / tg[raw_m]["requests_count"])
                        if last_ts > tg[raw_m]["last_ts"]:
                            tg[raw_m]["last_ts"] = last_ts
                            tg[raw_m]["last_used"] = dt_str
                c_auth.close()
            except Exception as e:
                print("Error reading request_events in _build_logs_report:", e)

        # Tier ranking helper: Lower index = better model
        def get_model_tier_rank(m_name: str, prov: str) -> int:
            m = m_name.lower()
            if prov == "Codex":
                # Order: gpt-6-astra (best) -> gpt-6-astra-pro -> gpt-5.6-luna -> gpt-5.4-mini
                if "astra" in m: return 1
                if "luna" in m: return 2
                if "sol" in m: return 3
                if "mini" in m: return 4
                return 10
            else:
                # Antigravity: gemini-3.8-flash (best) -> gemini-3.1-pro -> claude-sonnet-4-6 -> claude-opus -> gpt-oss
                if "3.8" in m or "flash" in m: return 1
                if "3.1" in m or "pro" in m: return 2
                if "sonnet" in m: return 3
                if "opus" in m: return 4
                if "gpt-oss" in m: return 5
                return 10

        sorted_codex = dict(sorted(
            models_by_provider["Codex"].items(),
            key=lambda x: (get_model_tier_rank(x[0], "Codex"), -x[1]["total_tokens"])
        ))
        sorted_ag = dict(sorted(
            models_by_provider["Antigravity"].items(),
            key=lambda x: (get_model_tier_rank(x[0], "Antigravity"), -x[1]["total_tokens"])
        ))

        all_models = list(sorted_ag.values()) + list(sorted_codex.values())
        all_models.sort(key=lambda x: x["total_tokens"], reverse=True)
        top_model = all_models[0]["model"] if all_models else "None"

        # Query real INDIVIDUAL API calls from request_events DB table (OpenClaw + Gateway + Hermes)
        individual_requests = []
        try:
            auth_db = "/root/Projects/AiPoolCodexGemini/dashboard/auth.db"
            if os.path.exists(auth_db):
                conn = sqlite3.connect(auth_db, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT model, pool, prompt_tokens, completion_tokens, total_tokens, time_formatted
                    FROM request_events
                    ORDER BY id DESC
                    LIMIT 100
                """)
                for r in cur.fetchall():
                    individual_requests.append({
                        "model": r["model"],
                        "provider": r["pool"],
                        "pool": r["pool"],
                        "tokens": r["total_tokens"],
                        "prompt": r["prompt_tokens"],
                        "completion": r["completion_tokens"],
                        "time_formatted": r["time_formatted"]
                    })
                conn.close()
        except Exception as e:
            print("Error reading request_events from auth.db:", e)

        # Also fallback/merge with Hermes log if request_events has few items
        log_path = "/root/.hermes/logs/agent.log"
        if len(individual_requests) < 100 and os.path.exists(log_path):
            try:
                # Read last lines of agent.log
                with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                    log_lines = lf.readlines()[-4000:]
                
                # Pattern: 2026-09-08 13:55:21,308 INFO [...] agent.conversation_loop: API call #44: model=gpt-6-astra provider=openai-codex in=88509 out=1784 total=90293
                api_call_regex = re.compile(
                    r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}),\d+\s+INFO\s+\[.*?\]\s+agent\.conversation_loop:\s+API call\s+#(\d+):\s+model=([\w.-]+)\s+provider=([\w.-]+)\s+in=(\d+)\s+out=(\d+)\s+total=(\d+)'
                )
                parsed_calls = []
                for line in log_lines:
                    match = api_call_regex.search(line)
                    if match:
                        date_str, call_num, model_name, prov_name, inp_t, out_t, tot_t = match.groups()
                        # Filter to only pool models
                        mn = model_name.lower()
                        is_codex = "astra" in mn or "luna" in mn or "sol" in mn or "mini" in mn or "codex" in mn
                        is_ag = "gemini" in mn or "claude" in mn or "gpt-oss" in mn
                        if not (is_codex or is_ag):
                            continue

                        provider_tag = "Codex" if is_codex else "Antigravity"
                        try:
                            dt_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                            # Format: MM/DD HH:MM (24-hour, no year)
                            formatted_dt = dt_obj.strftime("%m/%d %H:%M")
                        except Exception:
                            formatted_dt = date_str

                        parsed_calls.append({
                            "model": model_name,
                            "provider": provider_tag,
                            "tokens": int(tot_t),
                            "prompt": int(inp_t),
                            "completion": int(out_t),
                            "call_num": call_num,
                            "time_formatted": formatted_dt
                        })
                
                # Newest calls first, up to 100 individual calls
                individual_requests = parsed_calls[::-1][:100]
            except Exception as e:
                print("Error parsing agent.log for individual calls:", e)

        # Fallback to sessions if log parse is empty
        if not individual_requests and os.path.exists(self.state_db):
            try:
                conn = sqlite3.connect(self.state_db, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT s.id, s.model, s.input_tokens, s.output_tokens, s.last_activity_at,
                           (s.input_tokens + s.output_tokens) as total_tokens
                    FROM sessions s
                    WHERE s.model IS NOT NULL
                      AND (s.model LIKE '%astra%' OR s.model LIKE '%gemini%' OR s.model LIKE '%claude%' OR s.model LIKE '%luna%' OR s.model LIKE '%gpt-oss%')
                    ORDER BY s.last_activity_at DESC
                    LIMIT 100
                """)
                for r in cur.fetchall():
                    raw_model = r["model"] or ""
                    m = raw_model.lower()
                    provider = "Codex" if any(x in m for x in ["astra", "luna", "sol", "terra", "codex", "gpt-6", "gpt-5.6"]) else "Antigravity"
                    t_tok = r["total_tokens"] or 0
                    p_tok = r["input_tokens"] or 0
                    c_tok = r["output_tokens"] or 0
                    last_ts = r["last_activity_at"] or 0

                    dt_str = "--"
                    if last_ts:
                        try:
                            dt_str = datetime.datetime.fromtimestamp(last_ts).strftime("%b %d, %Y - %I:%M:%S %p")
                        except Exception: pass

                    individual_requests.append({
                        "model": raw_model,
                        "provider": provider,
                        "tokens": t_tok,
                        "prompt": p_tok,
                        "completion": c_tok,
                        "time_formatted": dt_str
                    })
                conn.close()
            except Exception as e:
                print("Error reading sessions from state.db:", e)

        return {
            "top_model": top_model,
            "total_pool_tokens": ag_total + cdx_total,
            "status": self.get_all_status(),
            "providers": {
                "Antigravity": {
                    "total_tokens": ag_total,
                    "requests": ag_calls,
                    "models": sorted_ag
                },
                "Codex": {
                    "total_tokens": cdx_total,
                    "requests": cdx_calls,
                    "models": sorted_codex
                }
            },
            "recent_requests": individual_requests
        }

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
        with self._lock:
            cached = dict(self._cached_logs or {})
        # Always fetch fresh real individual requests so OpenClaw and Gateway logs show up with 0 delay
        try:
            auth_db = "/root/Projects/AiPoolCodexGemini/dashboard/auth.db"
            if os.path.exists(auth_db):
                conn = sqlite3.connect(auth_db, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT model, pool, prompt_tokens, completion_tokens, total_tokens, time_formatted
                    FROM request_events
                    ORDER BY id DESC
                    LIMIT 100
                """)
                fresh_reqs = []
                for r in cur.fetchall():
                    fresh_reqs.append({
                        "model": r["model"],
                        "provider": r["pool"],
                        "pool": r["pool"],
                        "tokens": r["total_tokens"],
                        "prompt": r["prompt_tokens"],
                        "completion": r["completion_tokens"],
                        "time_formatted": r["time_formatted"]
                    })
                conn.close()
                if fresh_reqs:
                    cached["recent_requests"] = fresh_reqs
        except Exception as e:
            print("Error updating fresh requests in get_usage_logs_report:", e)
        return cached

    def switch_account(self, system: str, account_num: int) -> bool:
        cmd = f"/usr/local/bin/ag{account_num}" if system == "antigravity" else f"/usr/local/bin/c{account_num}"
        if os.path.exists(cmd):
            res = subprocess.run([cmd], capture_output=True, text=True)
            # update in background immediately
            threading.Thread(target=self._update_all_background, daemon=True).start()
            return res.returncode == 0
        return False

    def get_all_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "antigravity": self._cached_ag or {},
                "codex": self._cached_cdx or {},
                "active_hermes_default": "gemini-3.8-flash-tiered",
                "fallback_status": "Disabled (Pure Model Lock)",
                "timestamp": time.time()
            }
