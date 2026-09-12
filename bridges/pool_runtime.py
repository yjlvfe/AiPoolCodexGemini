"""Shared account rotation, persistent cooldowns, and request logging."""
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli'))
from account_manager import Manager, read_json, atomic_bytes, encoded, decode_claims, ag_module


class PoolError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class AccountPool:
    def __init__(self, provider):
        self.provider = provider
        self._lock = threading.RLock()
        self.cooldowns = {}

    def _cooldown_path(self):
        return Path(os.environ.get('AUTH_DB_PATH', '/var/lib/aipool/runtime/auth.db'))

    def _ensure_cooldown_table(self, conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS pool_cooldowns (
            provider TEXT, account TEXT, model TEXT, until_ts REAL,
            reason TEXT, PRIMARY KEY(provider, account, model))''')

    def _load_cooldown(self, number, model):
        try:
            path = self._cooldown_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path, timeout=10) as conn:
                self._ensure_cooldown_table(conn)
                row = conn.execute(
                    'SELECT until_ts FROM pool_cooldowns WHERE provider=? AND account=? AND model=?',
                    (self.provider, str(number), model)).fetchone()
            return float(row[0]) if row else 0.0
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return 0.0

    def _store_cooldown(self, number, model, until_ts, reason='upstream'):
        try:
            path = self._cooldown_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path, timeout=10) as conn:
                self._ensure_cooldown_table(conn)
                conn.execute('''INSERT OR REPLACE INTO pool_cooldowns
                    (provider, account, model, until_ts, reason) VALUES (?,?,?,?,?)''',
                    (self.provider, str(number), model, until_ts, reason))
        except (OSError, sqlite3.Error):
            pass

    def candidates(self, model, include_cooldown=False):
        manager = Manager(self.provider)
        try:
            active = manager.active()
        except ValueError:
            # A damaged active marker must not hide every otherwise readable
            # account; candidate ordering simply falls back to slot order.
            active = ''
        numbers = manager.ids()
        if active in numbers:
            index = numbers.index(active)
            numbers = numbers[index:] + numbers[:index]
        seen, result = set(), []
        for number in numbers:
            try:
                data = read_json(manager.credential(number))
                identity = manager.identity(data)
                key = identity.get('identity') or identity.get('refresh')
                if not key or key in seen:
                    continue
                seen.add(key)
                local_until = self.cooldowns.get((number, model), 0)
                persisted_until = self._load_cooldown(number, model)
                if not include_cooldown and max(local_until, persisted_until) > time.time():
                    continue
                result.append(number)
            except (OSError, ValueError):
                continue
        return result

    def exhausted(self, number, model, seconds=60, reason='upstream'):
        until = time.time() + min(max(seconds, 1), 3600)
        with self._lock:
            self.cooldowns[number, model] = until
        self._store_cooldown(number, model, until, reason)

    def credentials(self, number):
        with self._lock:
            return self._credentials(number)

    @staticmethod
    def _expiry_seconds(inner, outer):
        raw = inner.get('expires_at') or inner.get('expiry') or inner.get('expiry_date') or outer.get('expiry_date')
        try:
            value = float(raw or 0)
        except (TypeError, ValueError):
            return 0.0
        return value / 1000.0 if value > 100_000_000_000 else value

    def _credentials(self, number):
        manager = Manager(self.provider)
        with manager.locked():
            path = manager.credential(number)
            data = read_json(path)
            original = path.read_bytes()
            if manager.active() == number and manager.live.is_file():
                live = read_json(manager.live)
                if manager.identity(live).get('identity') == manager.identity(data).get('identity'):
                    data = live
            if manager.ag:
                inner = data.get('token', data)
                expiry = self._expiry_seconds(inner, data)
                if not isinstance(inner.get('access_token'), str) or not inner.get('access_token') or expiry < time.time() + 60 or not data.get('project_id'):
                    fresh, _, _ = ag_module().validate(data)
                else:
                    fresh = data
            else:
                fresh = data
                expiry = decode_claims(data['tokens']['access_token']).get('exp')
                if isinstance(expiry, (int, float)) and expiry < time.time() + 60:
                    from usage_format import inspect
                    if inspect(manager, number).get('status') != 'OK':
                        raise PoolError('Account refresh failed', 401)
                    fresh = read_json(path)
            if fresh != data:
                with manager.locked():
                    if path.read_bytes() == original:
                        atomic_bytes(path, encoded(fresh))
                    if manager.active() == number:
                        atomic_bytes(manager.live, encoded(fresh))
            return fresh

    def promote(self, number):
        manager = Manager(self.provider)
        if manager.active() == number:
            return
        manager.add_or_switch(number, server_verified=True)


def retry_seconds(headers):
    try:
        value = float(headers.get('Retry-After', '60'))
        return max(0, int(value))
    except (AttributeError, TypeError, ValueError):
        return 60


from request_log import db_path, initialize, clean_model_name, record, report
