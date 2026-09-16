import os
import tempfile
import threading
import time
from pathlib import Path
import pytest

from bridges.durable_requests import DurableRequestStore
from bridges.model_catalog import DynamicCatalog, CatalogError


def test_durable_requests_lease_expiry_reclaims_stale_in_progress(tmp_path, monkeypatch):
    db_path = tmp_path / "test_durable.db"
    store = DurableRequestStore(db_path)
    store.initialize()

    now = time.time()
    # Insert an in_progress request owned by current (alive) process, but with an expired lease
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO durable_requests
               (request_key, request_id, provider, model, endpoint, payload_json, payload_digest,
                status, attempts, owner_pid, created_at, updated_at, next_attempt_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'in_progress', 1, ?, ?, ?, 0)""",
            ("stale_key", "req-1", "gemini", "gemini-3.8-flash", "v1", "{}", "digest",
             os.getpid(), now - 400, now - 400)
        )
        conn.commit()

    # With default lease expiry of 300s, this row should be reclaimed even though PID is alive
    reclaimed = store.requeue_in_progress()
    assert reclaimed == 1

    with store._connect() as conn:
        row = conn.execute("SELECT status, owner_pid FROM durable_requests WHERE request_key='stale_key'").fetchone()
        assert row["status"] == "pending"
        assert row["owner_pid"] is None


def test_model_catalog_concurrent_fetches_release_lock_during_io(tmp_path):
    cache_path = tmp_path / "catalog_cache.json"
    fetch_started = threading.Event()
    allow_finish = threading.Event()

    def slow_fetcher():
        fetch_started.set()
        allow_finish.wait(timeout=5.0)
        return ["model-a", "model-b"]

    catalog = DynamicCatalog("test_provider", slow_fetcher, cache_path=cache_path, ttl=1.0)

    # Thread 1 starts refresh and hangs in I/O
    t1 = threading.Thread(target=catalog.refresh, kwargs={"force": True})
    t1.start()

    assert fetch_started.wait(timeout=2.0)

    # While Thread 1 is fetching, catalog._lock should NOT be permanently held
    # peek() should be able to acquire the lock immediately without waiting for allow_finish
    peek_done = threading.Event()
    peek_res = []

    def run_peek():
        peek_res.append(catalog.peek())
        peek_done.set()

    t2 = threading.Thread(target=run_peek)
    t2.start()

    assert peek_done.wait(timeout=1.0)
    assert peek_res == [[]]

    # Let fetcher finish
    allow_finish.set()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert catalog.peek() == ["model-a", "model-b"]
