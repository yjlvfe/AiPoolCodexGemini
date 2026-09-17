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
        self._cooldown_reasons = {}

    def _cooldown_path(self):
        return Path(os.environ.get('AUTH_DB_PATH', '/var/lib/aipool/runtime/auth.db'))

    def _ensure_cooldown_table(self, conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS pool_cooldowns (
            provider TEXT, account TEXT, model TEXT, until_ts REAL,
            reason TEXT, PRIMARY KEY(provider, account, model))''')

    def _load_cooldown_record(self, number, model):
        try:
            path = self._cooldown_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path, timeout=10) as conn:
                conn.execute('PRAGMA busy_timeout=10000')
                conn.execute('PRAGMA journal_mode=WAL')
                self._ensure_cooldown_table(conn)
                row = conn.execute(
                    'SELECT until_ts, reason FROM pool_cooldowns WHERE provider=? AND account=? AND model=?',
                    (self.provider, str(number), model)).fetchone()
            return (float(row[0]), row[1] or '') if row else (0.0, '')
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return 0.0, ''

    def _load_cooldown(self, number, model):
        return self._load_cooldown_record(number, model)[0]

    @staticmethod
    def _hard_reason(reason):
        return reason in {
            'quota', 'quota_exhausted', 'invalid_credentials',
            'account_failure', 'hard_exhaustion', 'upstream',
        }

    @staticmethod
    def _authoritative_reset_reason(reason):
        return reason == 'quota_reset_authoritative'

    def _store_cooldown(self, number, model, until_ts, reason='upstream'):
        try:
            path = self._cooldown_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path, timeout=10) as conn:
                conn.execute('PRAGMA busy_timeout=10000')
                conn.execute('PRAGMA journal_mode=WAL')
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
                persisted_until, persisted_reason = self._load_cooldown_record(number, model)
                # Hard exhaustion is a durable exclusion.  Only an explicit
                # authoritative reset record may make it eligible again.
                if self._hard_reason(persisted_reason):
                    continue
                if self._authoritative_reset_reason(persisted_reason) and persisted_until > time.time():
                    continue
                local_reason = getattr(self, '_cooldown_reasons', {}).get((number, model), '')
                if self._hard_reason(local_reason):
                    continue
                if not include_cooldown and local_until > time.time():
                    continue
                result.append(number)
            except (OSError, ValueError):
                continue
        return result

    def exhausted(self, number, model, seconds=60, reason='upstream'):
        # Hard failures are not generic cooldowns: retry-after is advisory and
        # must not resurrect the account.  Reset eligibility is granted only by
        # authoritative_quota_reset() or manual_reenable().
        until = 0 if self._hard_reason(reason) else time.time() + min(max(seconds, 1), 3600)
        with self._lock:
            self.cooldowns[number, model] = until
            self._cooldown_reasons[number, model] = reason
        self._store_cooldown(number, model, until, reason)

    def authoritative_quota_reset(self, number, model, reset_at=None):
        """Record an authoritative provider reset without changing active."""
        until = time.time() if reset_at is None else float(reset_at)
        with self._lock:
            self.cooldowns[number, model] = until
            self._cooldown_reasons[number, model] = 'quota_reset_authoritative'
        self._store_cooldown(number, model, until, 'quota_reset_authoritative')

    def manual_reenable(self, number, model=None):
        """Explicitly clear durable hard-exhaustion state for an account."""
        with self._lock:
            keys = [(number, model)] if model is not None else [
                key for key in self.cooldowns if key[0] == number
            ]
            for key in keys:
                self.cooldowns.pop(key, None)
                self._cooldown_reasons.pop(key, None)
        try:
            path = self._cooldown_path()
            with sqlite3.connect(path, timeout=10) as conn:
                if model is None:
                    conn.execute('DELETE FROM pool_cooldowns WHERE provider=? AND account=?',
                                 (self.provider, str(number)))
                else:
                    conn.execute('DELETE FROM pool_cooldowns WHERE provider=? AND account=? AND model=?',
                                 (self.provider, str(number), model))
        except (OSError, sqlite3.Error):
            pass

    clear_hard_exhaustion = manual_reenable

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
                        try:
                            atomic_bytes(manager.live, encoded(fresh))
                        except OSError:
                            pass
            return fresh

    def promote(self, number, expected_current=None):
        """Promote atomically, optionally only from an observed active slot.

        The expected-current check must be inside the store lock: a request may
        finish after a newer request or a manual switch has already changed the
        active marker.
        """
        manager = Manager(self.provider)
        with manager.locked():
            current = manager.active()
            if expected_current is not None and current != expected_current:
                return False
            if current == number:
                return False
            manager.add_or_switch(number, server_verified=True)
            return True


def hard_account_failure(status, detail=''):
    """Return true only for evidence that retiring the active account is safe."""
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    if status in (401, 403):
        return True
    if status != 429:
        return False
    text = str(detail).strip().lower()
    # Explicit hard quota markers only. Generic/empty 429 evidence is
    # transient: account rotation is permitted only for the canonical
    # ACCOUNT_QUOTA_EXHAUSTED condition.
    if not text:
        return False
    return any(marker in text for marker in (
        'quota', 'quota_exceeded', 'usage_limit', 'exhausted',
        'monthly_cap', 'monthly limit', 'weekly limit', 'five_hour',
        'invalid_api_key', 'unauthorized', 'invalid credentials',
        'too many requests per account'
    ))


def retry_seconds(headers):
    try:
        value = float(headers.get('Retry-After', '60'))
        return max(0, int(value))
    except (AttributeError, TypeError, ValueError):
        return 60


from request_log import db_path, initialize, clean_model_name, record, report
