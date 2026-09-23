"""Shared account rotation, persistent cooldowns, and request logging.

Parity with 9Router accountFallback.js and runtimeConfig.js.
"""
import math
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
    def __init__(self, message, status=503, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def _normalize_provider_name(provider: str) -> str:
    p = str(provider).strip().lower()
    if p in ('gemini', 'antigravity'):
        return 'gemini'
    return p


_POOLS: dict[tuple[str, str], 'AccountPool'] = {}


class AccountPool:
    def __new__(cls, provider):
        p = _normalize_provider_name(provider)
        db_path = str(Path(os.environ.get('AUTH_DB_PATH', '/var/lib/aipool/runtime/auth.db')))
        key = (p, db_path)
        if key not in _POOLS:
            instance = super().__new__(cls)
            _POOLS[key] = instance
        return _POOLS[key]

    def __init__(self, provider):
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        self.provider = _normalize_provider_name(provider)
        self._lock = threading.RLock()
        self.cooldowns = {}
        self._cooldown_reasons = {}
        self._backoff_levels = {}

    def _cooldown_path(self):
        return Path(os.environ.get('AUTH_DB_PATH', '/var/lib/aipool/runtime/auth.db'))

    def _ensure_cooldown_table(self, conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS pool_cooldowns (
            provider TEXT, account TEXT, model TEXT, until_ts REAL,
            reason TEXT, backoff_level INTEGER DEFAULT 0,
            PRIMARY KEY(provider, account, model))''')
        try:
            conn.execute('ALTER TABLE pool_cooldowns ADD COLUMN backoff_level INTEGER DEFAULT 0')
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            pass

    def _load_cooldown_record(self, number, model):
        try:
            path = self._cooldown_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path, timeout=10) as conn:
                conn.execute('PRAGMA busy_timeout=10000')
                conn.execute('PRAGMA journal_mode=WAL')
                self._ensure_cooldown_table(conn)
                row = conn.execute(
                    'SELECT until_ts, reason, backoff_level FROM pool_cooldowns WHERE provider=? AND account=? AND model=?',
                    (self.provider, str(number), model)).fetchone()
            if row:
                until = float(row[0])
                reason = row[1] or ''
                return (until, reason)
            return (0.0, '')
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return (0.0, '')

    def _load_backoff_level(self, number, model):
        try:
            path = self._cooldown_path()
            with sqlite3.connect(path, timeout=10) as conn:
                self._ensure_cooldown_table(conn)
                row = conn.execute(
                    'SELECT backoff_level FROM pool_cooldowns WHERE provider=? AND account=? AND model=?',
                    (self.provider, str(number), model)).fetchone()
            return int(row[0] or 0) if row else 0
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return 0

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

    def _store_cooldown(self, number, model, until_ts, reason='upstream', backoff_level=0):
        try:
            path = self._cooldown_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path, timeout=10) as conn:
                conn.execute('PRAGMA busy_timeout=10000')
                conn.execute('PRAGMA journal_mode=WAL')
                self._ensure_cooldown_table(conn)
                conn.execute('''INSERT OR REPLACE INTO pool_cooldowns
                    (provider, account, model, until_ts, reason, backoff_level) VALUES (?,?,?,?,?,?)''',
                    (self.provider, str(number), model, until_ts, reason, backoff_level))
        except (OSError, sqlite3.Error):
            pass

    def is_cooling_down(self, number, model):
        """Check if account is in cooldown for this model OR account-wide '__all__'."""
        now = time.time()
        local_model = max(
            self.cooldowns.get((number, model), 0.0),
            self.cooldowns.get((str(number), model), 0.0),
        ) if model else 0.0
        rec_model, _ = self._load_cooldown_record(number, model) if model else (0.0, '')

        local_all = max(
            self.cooldowns.get((number, "__all__"), 0.0),
            self.cooldowns.get((str(number), "__all__"), 0.0),
        )
        rec_all, _ = self._load_cooldown_record(number, "__all__")

        effective_until = max(local_model, rec_model, local_all, rec_all)
        return effective_until > now

    def candidates(self, model, include_cooldown=False):
        """Return available candidates. When all are cooling down, returns empty list."""
        manager = Manager(self.provider)
        try:
            active = manager.active()
        except ValueError:
            active = ''
        numbers = manager.ids()
        if active in numbers:
            index = numbers.index(active)
            numbers = numbers[index:] + numbers[:index]
        seen = set()
        healthy_candidates = []
        all_valid_accounts = []
        for number in numbers:
            try:
                data = read_json(manager.credential(number))
                identity = manager.identity(data)
                key = identity.get('identity') or identity.get('refresh')
                if not key or key in seen:
                    continue
                seen.add(key)
                all_valid_accounts.append(number)
                if not self.is_cooling_down(number, model):
                    healthy_candidates.append(number)
            except (OSError, ValueError):
                continue

        # If all accounts are cooling down, return empty candidates to eliminate upstream storm
        if not healthy_candidates:
            return []

        if include_cooldown:
            return all_valid_accounts
        return healthy_candidates

    def get_earliest_retry_after(self, *args, **kwargs):
        """Compute the minimum cooldown remaining across accounts (for HTTP 503 Retry-After)."""
        if len(args) >= 2:
            model = args[1]
        elif len(args) == 1:
            model = args[0]
        else:
            model = kwargs.get('model')
        manager = Manager(self.provider)
        try:
            numbers = manager.ids()
        except Exception:
            numbers = []
        if not numbers:
            return None
        now = time.time()
        untils = []
        for number in numbers:
            local_model = max(
                self.cooldowns.get((number, model), 0.0),
                self.cooldowns.get((str(number), model), 0.0),
            ) if model else 0.0
            rec_model, _ = self._load_cooldown_record(number, model) if model else (0.0, '')
            local_all = max(
                self.cooldowns.get((number, "__all__"), 0.0),
                self.cooldowns.get((str(number), "__all__"), 0.0),
            )
            rec_all, _ = self._load_cooldown_record(number, "__all__")
            effective = max(local_model, rec_model, local_all, rec_all)
            untils.append(effective)

        if not untils:
            return None
        if any(u <= now for u in untils):
            return 0
        min_until = min(untils)
        diff = min_until - now
        return max(1, int(math.ceil(diff)))

    def exhausted(self, number, model, seconds=None, resets_at=None, reason='upstream'):
        """Record account cooldown with precise resets_at or 9Router exponential backoff."""
        now = time.time()
        until = None
        if resets_at is not None:
            try:
                val = float(resets_at)
                until = val if val > 1_000_000_000 else now + max(0.0, val)
            except (ValueError, TypeError):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(str(resets_at).replace('Z', '+00:00'))
                    until = dt.timestamp()
                except Exception:
                    until = None

        if until is None and seconds is not None:
            try:
                sec = float(seconds)
                if sec > 0:
                    until = now + sec
            except (ValueError, TypeError):
                pass

        # 9Router exponential backoff tracking: base 2.0s, double per level, max 300s
        key = (str(number), str(model))
        prev_level = self._load_backoff_level(number, model)
        mem_level = self._backoff_levels.get(key, 0)
        current_level = max(prev_level, mem_level)
        new_level = min(current_level + 1, 15)
        self._backoff_levels[key] = new_level

        if until is None:
            cooldown = min(2.0 * (2 ** max(0, new_level - 1)), 300.0)
            until = now + cooldown

        target_model = model
        if reason in {'invalid_credentials', 'account_failure', 'auth_failure', 'account_locked'}:
            target_model = "__all__"

        with self._lock:
            self.cooldowns[number, target_model] = until
            self.cooldowns[str(number), target_model] = until
            self._cooldown_reasons[number, target_model] = reason
            self._cooldown_reasons[str(number), target_model] = reason
        self._store_cooldown(number, target_model, until, reason, backoff_level=new_level)

    def clear_cooldown(self, *args, **kwargs):
        """Clear cooldown in memory and delete/expire from pool_cooldowns table."""
        if len(args) >= 3:
            number = args[1]
            model = args[2]
        elif len(args) == 2:
            if isinstance(args[0], str) and args[0] in ('gemini', 'antigravity', 'codex', self.provider):
                number = args[1]
                model = kwargs.get('model')
            else:
                number = args[0]
                model = args[1]
        elif len(args) == 1:
            number = args[0]
            model = kwargs.get('model')
        else:
            number = kwargs.get('number') or kwargs.get('account')
            model = kwargs.get('model')
        with self._lock:
            keys_to_remove = []
            for (acc, mod) in list(self.cooldowns.keys()):
                if str(acc) == str(number) and (model is None or mod == model or mod == "__all__"):
                    keys_to_remove.append((acc, mod))
            for key in keys_to_remove:
                self.cooldowns.pop(key, None)
                self._cooldown_reasons.pop(key, None)

            backoff_keys_to_remove = []
            for (acc, mod) in list(self._backoff_levels.keys()):
                if str(acc) == str(number) and (model is None or mod == model or mod == "__all__"):
                    backoff_keys_to_remove.append((acc, mod))
            for key in backoff_keys_to_remove:
                self._backoff_levels.pop(key, None)

        try:
            path = self._cooldown_path()
            with sqlite3.connect(path, timeout=10) as conn:
                conn.execute('PRAGMA busy_timeout=10000')
                self._ensure_cooldown_table(conn)
                if model is None:
                    conn.execute('DELETE FROM pool_cooldowns WHERE provider=? AND account=?',
                                 (self.provider, str(number)))
                else:
                    conn.execute('DELETE FROM pool_cooldowns WHERE provider=? AND account=? AND (model=? OR model="__all__")',
                                 (self.provider, str(number), model))
        except (OSError, sqlite3.Error):
            pass

    manual_reenable = clear_cooldown
    clear_hard_exhaustion = clear_cooldown

    def authoritative_quota_reset(self, number, model, reset_at=None):
        """Record an authoritative provider reset without changing active."""
        until = time.time() if reset_at is None else float(reset_at)
        with self._lock:
            self.cooldowns[number, model] = until
            self.cooldowns[str(number), model] = until
            self._cooldown_reasons[number, model] = 'quota_reset_authoritative'
            self._cooldown_reasons[str(number), model] = 'quota_reset_authoritative'
        self._store_cooldown(number, model, until, 'quota_reset_authoritative')

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
                        manager.sync_live(fresh, number=number)
                    except Exception:
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
                try:
                    data = read_json(manager.credential(number))
                    manager.sync_live(data, number=number)
                except Exception:
                    pass
                return False
            manager.add_or_switch(number, server_verified=True)
            return True


def clear_cooldown(provider: str, account: str | int, model: str | None = None) -> None:
    """Clear account cooldown in memory/DB and reset backoff (matches 9Router clearAccountError)."""
    AccountPool(provider).clear_cooldown(account, model)


clear_account_error = clear_cooldown


def get_earliest_retry_after(provider: str, model: str | None = None) -> int | None:
    """Compute the minimum cooldown remaining across accounts for this provider and model."""
    return AccountPool(provider).get_earliest_retry_after(model)


def hard_account_failure(status, detail=''):
    """Return true for evidence that account fallback / rotation should occur.

    Parity with 9Router checkFallbackError: 401, 403, 409, 429 and quota/rate limit text patterns.
    """
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0
    if status in (401, 403, 409, 429):
        return True
    text = str(detail).strip().lower()
    if status == 400 and ('not supported when using codex' in text or 'resource_exhausted' in text):
        return True
    return any(marker in text for marker in (
        'quota', 'quota_exceeded', 'quota exceeded', 'usage_limit', 'exhausted',
        'monthly_cap', 'monthly limit', 'weekly limit', 'five_hour',
        '5-hour', '5-hour limit', '5h limit',
        'rate_limit', 'rate_limit_exceeded', 'rate limit',
        'too many requests', 'too many requests per account',
        'resource_exhausted', 'resource exhausted',
        'invalid_api_key', 'unauthorized', 'invalid credentials',
        'not supported when using codex', 'validation_required',
        'capacity', 'overloaded', 'model_at_capacity',
        'selected model is at capacity',
    ))


def retry_seconds(headers):
    """Extract retry seconds from headers without rigid 60s floor."""
    from error_taxonomy import parse_retry_seconds
    sec = parse_retry_seconds(headers=headers)
    if sec is not None:
        return max(0, int(math.ceil(sec)))
    return None


from request_log import db_path, initialize, clean_model_name, record, report
