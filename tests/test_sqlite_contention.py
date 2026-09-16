import concurrent.futures
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridges"))
import request_log
from durable_requests import DurableRequestStore


def test_request_log_initializes_wal_once_and_handles_concurrent_writes_reads(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "0")

    def write(index):
        request_log.record(
            "Codex", "contention-model", {"input_tokens": 1, "output_tokens": 1},
            request_id=f"contention-{index}",
        )

    def read(_):
        return request_log.report(str(db))

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(write, range(40)))
        reports = list(pool.map(read, range(40)))

    with request_log._connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert conn.execute("SELECT COUNT(*) FROM request_events").fetchone()[0] == 40
    assert all(report["total_pool_tokens"] == 80 for report in reports)


def test_durable_requests_handles_concurrent_claims_without_locked_errors(tmp_path):
    db = Path(tmp_path) / "recovery.db"
    stores = [DurableRequestStore(db) for _ in range(12)]

    def begin(index):
        return stores[index % len(stores)].begin(
            key=f"key-{index}", request_id=f"request-{index}", provider="Codex",
            model="contention-model", endpoint="/v1/responses", payload={"i": index},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        states = list(pool.map(begin, range(40)))

    assert {state["state"] for state in states} == {"claimed"}
    with stores[0]._connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert conn.execute("SELECT COUNT(*) FROM durable_requests").fetchone()[0] == 40
