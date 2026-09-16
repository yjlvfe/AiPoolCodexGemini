import json
import sqlite3
import time
from bridges import request_log


def test_api_request_isolation_and_retention(tmp_path, monkeypatch):
    db = tmp_path / "test_api_log.db"
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "5")
    monkeypatch.setenv("AIPOOL_API_REQUEST_RETENTION", "10")

    # 1. Record 3 API key requests
    for i in range(3):
        request_log.record(
            "Gemini",
            "gemini-3.8-flash",
            {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            request_id=f"api-req-{i}",
            audit={
                "client_id": "key_mohamed",
                "client_label": "Mohamed",
                "authenticated_client": "Mohamed",
            }
        )

    # 2. Flood with 20 local system requests (Hermes)
    for i in range(20):
        request_log.record(
            "Codex",
            "gpt-5.6-luna",
            {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10},
            request_id=f"local-hermes-{i}",
            audit={
                "client_id": "hermes",
                "client_label": "Hermes Agent",
            }
        )

    # Verify:
    # - In general request_events table: rolled down to AIPOOL_REQUEST_RETENTION = 5
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        general_count = conn.execute("SELECT count(*) FROM request_events").fetchone()[0]
        assert general_count == 5

        # - In isolated api_request_events table: all 3 API requests are intact and preserved!
        api_events = conn.execute("SELECT request_id, model FROM api_request_events ORDER BY id ASC").fetchall()
        assert len(api_events) == 3
        assert [r["request_id"] for r in api_events] == ["api-req-0", "api-req-1", "api-req-2"]

    # 3. Test get_api_requests helper
    fetched = request_log.get_api_requests(str(db))
    assert len(fetched) == 3
    assert fetched[0]["request_id"] == "api-req-2"
    assert fetched[0]["audit"]["client_label"] == "Mohamed"
