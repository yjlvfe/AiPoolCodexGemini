"""Small SQLite-backed request journal used to recover bridge restarts.

The journal deliberately stores only requests which are still in flight and a
short-lived provider response cache.  It is not a conversation database: its
purpose is to close the crash window between accepting a request and returning
the provider response to the caller.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TTL_SECONDS = 15 * 60
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _path() -> Path:
    configured = os.environ.get("AIPOOL_RECOVERY_DB")
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("AUTH_DB_PATH", ROOT / "dashboard/auth.db"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_key(provider: str, endpoint: str, payload: Any, supplied_id: str | None = None, *, client_scope: str = "local") -> str:
    """Return a stable key only for an explicit client retry contract.

    Without an idempotency header, two identical requests are independent
    conversations and must never be implicitly replayed or deduplicated.
    """
    identity = str(supplied_id or "").strip()
    if not identity:
        return uuid.uuid4().hex
    material: list[Any] = [provider, endpoint, client_scope, identity]
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


class DurableRequestStore:
    """Crash-safe state for accepted provider requests."""

    def __init__(self, path: str | os.PathLike[str] | None = None, *, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.path = Path(path) if path else _path()
        self.ttl_seconds = max(int(ttl_seconds), 1)

    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise ValueError("Recovery database must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS durable_requests (
                    request_key TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    payload_json TEXT,
                    payload_digest TEXT,
                    response_json TEXT,
                    response_status INTEGER,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    owner_pid INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT
                )"""
            )
            # Existing installations predate payload binding.  Add the column
            # in place so a reused Idempotency-Key cannot silently replace the
            # durable request with a different body.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(durable_requests)")}
            if "payload_digest" not in columns:
                conn.execute("ALTER TABLE durable_requests ADD COLUMN payload_digest TEXT")
                conn.execute(
                    "UPDATE durable_requests SET payload_digest=? WHERE payload_json IS NOT NULL",
                    ("legacy-unbound",),
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS durable_requests_pending "
                "ON durable_requests(status, next_attempt_at, updated_at)"
            )
            conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def _cleanup(self, conn: sqlite3.Connection, now: float) -> None:
        conn.execute(
            "DELETE FROM durable_requests WHERE status IN ('completed', 'failed') AND updated_at < ?",
            (now - self.ttl_seconds,),
        )

    def begin(
        self, *, key: str, request_id: str, provider: str, model: str, endpoint: str, payload: Any,
    ) -> dict[str, Any]:
        """Claim a request or return its current durable state.

        ``new`` and ``pending`` are claimed by this process.  ``completed`` is
        replayable; ``in_progress`` belongs to another worker and must not be
        sent to the provider a second time concurrently.
        """
        self.initialize()
        now = time.time()
        payload_json = _json(payload)
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup(conn, now)
            row = conn.execute(
                "SELECT * FROM durable_requests WHERE request_key = ?", (key,)
            ).fetchone()
            if row is not None and row["payload_digest"] not in (None, payload_digest):
                if row["payload_digest"] == "legacy-unbound":
                    # Old rows have no trustworthy fingerprint.  Bind them to
                    # the first retry instead of rejecting recovery forever.
                    conn.execute(
                        "UPDATE durable_requests SET payload_digest=? WHERE request_key=?",
                        (payload_digest, key),
                    )
                else:
                    raise ValueError("Idempotency-Key is already bound to a different request")
            if row is not None and row["status"] == "completed":
                return {"state": "completed", **self._row(row)}
            if row is not None and row["status"] == "in_progress":
                return {"state": "in_progress", **self._row(row)}
            if row is None:
                conn.execute(
                    """INSERT INTO durable_requests
                       (request_key, request_id, provider, model, endpoint, payload_json, payload_digest,
                        status, attempts, owner_pid, created_at, updated_at, next_attempt_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'in_progress', 1, ?, ?, ?, 0)""",
                    (key, request_id, provider, model, endpoint, payload_json,
                     payload_digest, os.getpid(), now, now),
                )
            else:
                conn.execute(
                    """UPDATE durable_requests
                       SET request_id=?, provider=?, model=?, endpoint=?,
                           response_json=NULL, response_status=NULL, status='in_progress',
                           attempts=attempts+1, owner_pid=?, updated_at=?, next_attempt_at=0,
                           last_error=NULL, payload_json=COALESCE(payload_json, ?),
                           payload_digest=COALESCE(payload_digest, ?)
                       WHERE request_key=?""",
                    (request_id, provider, model, endpoint, os.getpid(), now,
                     payload_json, payload_digest, key),
                )
            conn.commit()
        return {"state": "claimed", "request_key": key}

    @staticmethod
    def _owner_alive(owner_pid: Any) -> bool:
        try:
            pid = int(owner_pid)
            if pid <= 0:
                return False
            os.kill(pid, 0)
            return True
        except PermissionError:
            # The process exists but belongs to another user; do not steal it.
            return True
        except (OSError, TypeError, ValueError):
            return False

    def requeue_in_progress(self) -> int:
        """Make work owned by a process that no longer exists recoverable.

        A service restart can briefly overlap with the old process.  Requeueing
        every ``in_progress`` row unconditionally would let both processes call
        the provider for the same request.  Only rows whose owner PID is dead
        (or missing from a legacy row) are eligible for recovery.
        """
        self.initialize()
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT request_key, owner_pid FROM durable_requests WHERE status='in_progress'"
            ).fetchall()
            reclaimed = 0
            for row in rows:
                if self._owner_alive(row["owner_pid"]):
                    continue
                cur = conn.execute(
                    """UPDATE durable_requests
                       SET status='pending', owner_pid=NULL, updated_at=?, next_attempt_at=0,
                           last_error=COALESCE(last_error, 'bridge restarted during request')
                       WHERE request_key=? AND status='in_progress'""",
                    (now, row["request_key"]),
                )
                reclaimed += cur.rowcount
            conn.commit()
            return reclaimed

    def pending(self, limit: int = 8) -> list[dict[str, Any]]:
        self.initialize()
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM durable_requests
                   WHERE status='pending' AND next_attempt_at <= ?
                   ORDER BY updated_at ASC LIMIT ?""",
                (now, max(int(limit), 1)),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_pending(self, key: str) -> dict[str, Any] | None:
        self.initialize()
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """UPDATE durable_requests
                   SET status='in_progress', owner_pid=?, attempts=attempts+1,
                       updated_at=?, next_attempt_at=0
                   WHERE request_key=? AND status='pending' AND next_attempt_at <= ?""",
                (os.getpid(), now, key, now),
            )
            row = conn.execute("SELECT * FROM durable_requests WHERE request_key=?", (key,)).fetchone()
            conn.commit()
        return self._row(row) if cur.rowcount else None

    def complete(self, key: str, response: Any, response_status: int = 200) -> None:
        self.initialize()
        now = time.time()
        response_json = _json(response)
        if len(response_json.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError("provider response is too large to cache for recovery")
        with self._connect() as conn:
            conn.execute(
                """UPDATE durable_requests
                   SET status='completed', response_json=?, response_status=?,
                       payload_json=NULL, owner_pid=NULL, updated_at=?, next_attempt_at=0,
                       last_error=NULL
                   WHERE request_key=?""",
                (response_json, int(response_status), now, key),
            )
            conn.commit()

    def fail(self, key: str, error: str, *, retryable: bool = True, delay: float = 5.0) -> None:
        self.initialize()
        now = time.time()
        status = "pending" if retryable else "failed"
        with self._connect() as conn:
            conn.execute(
                """UPDATE durable_requests
                   SET status=?, owner_pid=NULL, updated_at=?, next_attempt_at=?, last_error=?
                   WHERE request_key=?""",
                (status, now, now + max(float(delay), 0.0) if retryable else 0.0,
                 str(error)[:500], key),
            )
            conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM durable_requests WHERE request_key=?", (key,)).fetchone()
        return self._row(row)
