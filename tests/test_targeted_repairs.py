import hashlib
import importlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridges"))
sys.path.insert(0, str(ROOT / "dashboard"))

import client_identity
import request_log
from app import TokenAuthManager


class FakeGatewayHandler:
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}"}
        self.client_address: tuple[str, int] = ("198.51.100.20", 12345)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = {}
        self.close_connection = False

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass


def _dashboard_post(monkeypatch, tmp_path, path, payload):
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "usage.db"))
    monkeypatch.setenv("AIPOOL_POOL_SNAPSHOT", str(tmp_path / "pool-snapshot.json"))
    server = importlib.import_module("server")

    class CaptureHandler(server.ProDashboardHandler):
        def __init__(self):
            self.path = path
            body = json.dumps(payload).encode()
            self.headers = {"Content-Length": str(len(body)), "Host": "localhost"}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 12345)
            self.status = None
            self.response_headers = {}

        def send_response(self, status, message=None):
            self.status = status

        def send_header(self, name, value):
            self.response_headers[name] = value

        def end_headers(self):
            pass

    handler = CaptureHandler()
    handler.do_POST()
    return handler.status, json.loads(handler.wfile.getvalue())


def _dashboard_get(monkeypatch, tmp_path, path, auth_manager, headers=None, remote=True):
    monkeypatch.setenv("AUTH_DB_PATH", str(auth_manager.db_path))
    monkeypatch.setenv("AIPOOL_POOL_SNAPSHOT", str(tmp_path / "pool-snapshot.json"))
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "TokenAuthManager", lambda: auth_manager)

    class CaptureHandler(server.ProDashboardHandler):
        def __init__(self):
            self.path = path
            self.headers = dict(headers or {})
            self.wfile = io.BytesIO()
            self.client_address = (("198.51.100.20" if remote else "127.0.0.1"), 12345)
            self.status = None
            self.response_headers = {}

        def send_response(self, status, message=None):
            self.status = status

        def send_header(self, name, value):
            self.response_headers[name] = value

        def end_headers(self):
            pass

    handler = CaptureHandler()
    handler.do_GET()
    return handler


def _client(name, key_id, secret, **extra):
    return {
        "name": name,
        "id": key_id,
        "enabled": True,
        "sha256": hashlib.sha256(secret.encode()).hexdigest(),
        **extra,
    }


def test_usage_accounting_uses_stable_key_id_for_duplicate_labels(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")

    request_log.record(
        "Codex",
        "gpt-test",
        {"input_tokens": 3, "output_tokens": 7, "total_tokens": 10},
        request_id="key-a-usage",
        audit={"client_id": "key-a", "client_label": "Shared Label"},
    )
    request_log.record(
        "Gemini",
        "gemini-test",
        {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10},
        request_id="key-b-usage",
        audit={"client_id": "key-b", "client_label": "Shared Label"},
    )

    with sqlite3.connect(db) as conn:
        counters = dict(conn.execute(
            "SELECT client_id, total_tokens FROM client_usage_counters ORDER BY client_id"
        ))

    assert counters == {"key-a": 10, "key-b": 10}


def test_usage_accounting_adopts_unique_legacy_label_counter(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    registry.write_text(json.dumps({
        "mode": "enforce",
        "clients": [_client("Legacy Label", "key-a", "secret-a")],
    }))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    request_log.initialize(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO client_usage_counters(client_id,total_tokens,prompt_tokens,completion_tokens,requests,codex_tokens,gemini_tokens,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("legacy label", 9, 9, 0, 1, 9, 0, 100.0, 100.0),
        )
        conn.commit()

    request_log.record(
        "Codex",
        "gpt-test",
        {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        request_id="stable-adoption",
        audit={"client_id": "key-a", "client_label": "Legacy Label"},
    )

    with sqlite3.connect(db) as conn:
        counters = dict(conn.execute(
            "SELECT client_id, total_tokens FROM client_usage_counters ORDER BY client_id"
        ))
    assert counters == {"key-a": 10}


def test_usage_accounting_merges_legacy_counter_into_existing_stable_id(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    registry.write_text(json.dumps({
        "mode": "enforce",
        "clients": [_client("Legacy Label", "key-a", "secret-a")],
    }))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    request_log.initialize(str(db))
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO client_usage_counters(client_id,total_tokens,prompt_tokens,completion_tokens,requests,codex_tokens,gemini_tokens,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ("legacy label", 9, 9, 0, 1, 9, 0, 100.0, 100.0),
                ("key-a", 4, 4, 0, 1, 4, 0, 200.0, 200.0),
            ],
        )
        conn.commit()

    request_log.record(
        "Codex",
        "gpt-test",
        {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        request_id="stable-merge",
        audit={"client_id": "key-a", "client_label": "Legacy Label"},
    )

    with sqlite3.connect(db) as conn:
        counters = conn.execute(
            "SELECT client_id,total_tokens,requests FROM client_usage_counters"
        ).fetchall()
    assert counters == [("key-a", 14, 3)]


def test_legacy_counter_is_not_recreated_and_double_counted_during_retention(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    registry.write_text(json.dumps({
        "mode": "enforce",
        "clients": [_client("Legacy Label", "key-a", "secret-a")],
    }))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")

    request_log.record(
        "Codex", "legacy-model",
        {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        request_id="legacy-one",
        audit={"client_label": "Legacy Label"},
    )
    request_log.record(
        "Codex", "stable-model",
        {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        request_id="stable-two",
        audit={"client_id": "key-a", "client_label": "Legacy Label"},
    )
    for index in range(300):
        request_log.record(
            "Gemini", "background-model",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"legacy-retention-background-{index}",
        )
    request_log.record(
        "Codex", "stable-model",
        {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        request_id="stable-three",
        audit={"client_id": "key-a", "client_label": "Legacy Label"},
    )

    with sqlite3.connect(db) as conn:
        counters = conn.execute(
            "SELECT client_id,total_tokens FROM client_usage_counters WHERE client_id IN (?, ?)",
            ("key-a", "legacy label"),
        ).fetchall()
    assert counters == [("key-a", 3)]


def test_dashboard_rename_migrates_unique_legacy_counter_to_stable_id(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    client = _client("Legacy Label", "key-a", "secret-a", max_tokens=10)
    registry.write_text(json.dumps({"mode": "enforce", "clients": [client]}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    request_log.initialize(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO client_usage_counters(client_id,total_tokens,prompt_tokens,completion_tokens,requests,codex_tokens,gemini_tokens,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("legacy label", 10, 10, 0, 1, 10, 0, 100.0, 100.0),
        )
        conn.execute(
            "INSERT INTO request_events(model,pool,prompt_tokens,completion_tokens,total_tokens,timestamp,time_formatted,request_id,audit_json) VALUES(?,?,?,?,?,?,?,?,?)",
            ("legacy-model", "Codex", 0, 0, 0, 100.0, "", "legacy-before-rename", json.dumps({"client_label": "Legacy Label"})),
        )
        conn.commit()

    status, response = _dashboard_post(
        monkeypatch,
        tmp_path,
        "/api/tokens/update",
        {"id": "key-a", "name": "Renamed Key"},
    )

    assert status == 200
    assert response["success"] is True
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT client_id, total_tokens FROM client_usage_counters"
        ).fetchall() == [("key-a", 10)]
        migrated_audit = json.loads(conn.execute(
            "SELECT audit_json FROM request_events WHERE request_id=?",
            ("legacy-before-rename",),
        ).fetchone()[0])
        assert migrated_audit["client_id"] == "key-a"
    handler = FakeGatewayHandler("secret-a")
    assert client_identity.authorize(handler, "Codex") is False
    assert handler.status == 429


def test_rename_never_migrates_a_counter_owned_by_another_stable_id(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    clients = [
        _client("key-b", "key-a", "secret-a"),
        _client("Second Key", "key-b", "secret-b"),
    ]
    registry.write_text(json.dumps({"mode": "enforce", "clients": clients}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    request_log.initialize(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO client_usage_counters(client_id,total_tokens,prompt_tokens,completion_tokens,requests,codex_tokens,gemini_tokens,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("key-b", 10, 10, 0, 1, 10, 0, 100.0, 100.0),
        )
        conn.commit()

    status, response = _dashboard_post(
        monkeypatch,
        tmp_path,
        "/api/tokens/update",
        {"id": "key-a", "name": "Renamed Key"},
    )

    assert status == 200
    assert response["success"] is True
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT client_id,total_tokens FROM client_usage_counters"
        ).fetchall() == [("key-b", 10)]


def test_quota_usage_survives_rename_without_leaking_to_same_label_key(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    key_a = _client("Original A", "key-a", "secret-a", max_tokens=10)
    key_b = _client("Renamed A", "key-b", "secret-b", max_tokens=10)
    registry.write_text(json.dumps({"mode": "enforce", "clients": [key_a, key_b]}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))

    request_log.record(
        "Codex",
        "gpt-test",
        {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10},
        request_id="rename-usage",
        audit={"client_id": "key-a", "client_label": "Original A"},
    )
    key_a["name"] = "Renamed A"
    registry.write_text(json.dumps({"mode": "enforce", "clients": [key_a, key_b]}))

    handler_a = FakeGatewayHandler("secret-a")
    assert client_identity.authorize(handler_a, "Codex") is False
    assert handler_a.status == 429

    handler_b = FakeGatewayHandler("secret-b")
    assert client_identity.authorize(handler_b, "Codex") is True
    assert handler_b.status is None


def test_token_list_keeps_duplicate_labels_accounted_by_stable_id(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    clients = [
        _client("Shared Label", "key-a", "secret-a"),
        _client("Shared Label", "key-b", "secret-b"),
    ]
    registry.write_text(json.dumps({"mode": "enforce", "clients": clients}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    for key_id, tokens in (("key-a", 7), ("key-b", 11)):
        request_log.record(
            "Codex",
            "gpt-test",
            {"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens},
            request_id=f"list-{key_id}",
            audit={"client_id": key_id, "client_label": "Shared Label"},
        )

    auth = TokenAuthManager(str(db))
    handler = _dashboard_get(monkeypatch, tmp_path, "/api/tokens/list", auth, remote=False)
    response = json.loads(handler.wfile.getvalue())
    usage = {item["id"]: item["total_tokens"] for item in response["tokens"]}

    assert handler.status == 200
    assert usage == {"key-a": 7, "key-b": 11}


def test_legacy_label_matching_another_key_id_is_not_attributed(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    clients = [
        _client("key-b", "key-a", "secret-a", max_tokens=10),
        _client("Second Key", "key-b", "secret-b", max_tokens=10),
    ]
    registry.write_text(json.dumps({"mode": "enforce", "clients": clients}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    request_log.record(
        "Codex", "legacy-model",
        {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
        request_id="id-label-collision",
        audit={"client_label": "key-b"},
    )
    for index in range(300):
        request_log.record(
            "Gemini", "background-model",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"id-label-collision-background-{index}",
        )

    handler = FakeGatewayHandler("secret-b")
    assert client_identity.authorize(handler, "Codex") is True
    assert handler.status is None
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT total_tokens FROM client_usage_counters WHERE client_id=?", ("key-b",)
        ).fetchone() is None


def test_ambiguous_legacy_label_usage_is_not_assigned_to_duplicate_keys(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    clients = [
        _client("Shared Label", "key-a", "secret-a", max_tokens=10),
        _client("Shared Label", "key-b", "secret-b", max_tokens=10),
    ]
    registry.write_text(json.dumps({"mode": "enforce", "clients": clients}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    request_log.record(
        "Codex",
        "legacy-model",
        {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
        request_id="ambiguous-legacy-label",
        audit={"client_label": "Shared Label"},
    )

    assert client_identity.authorize(FakeGatewayHandler("secret-a"), "Codex") is True
    assert client_identity.authorize(FakeGatewayHandler("secret-b"), "Codex") is True

    auth = TokenAuthManager(str(db))
    handler = _dashboard_get(monkeypatch, tmp_path, "/api/tokens/list", auth, remote=False)
    response = json.loads(handler.wfile.getvalue())
    usage = {item["id"]: item["total_tokens"] for item in response["tokens"]}
    assert usage == {"key-a": 0, "key-b": 0}

    status, details = _dashboard_post(
        monkeypatch,
        tmp_path,
        "/api/tokens/details",
        {"id": "key-a"},
    )
    assert status == 200
    assert details["details"]["total_tokens"] == 0
    assert details["details"]["total_requests"] == 0


@pytest.mark.parametrize(
    ("used_tokens", "expected_allowed"),
    [(349, True), (350, False), (351, False)],
)
def test_refill_quota_boundaries_survive_detail_retention(
    tmp_path, monkeypatch, used_tokens, expected_allowed
):
    db = tmp_path / f"usage-{used_tokens}.db"
    registry = tmp_path / f"clients-{used_tokens}.json"
    client = _client(
        "Refill Key",
        "refill-key",
        "refill-secret",
        max_tokens=350,
        refill_period_seconds=3600,
    )
    registry.write_text(json.dumps({"mode": "enforce", "clients": [client]}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")

    for index in range(5):
        request_log.record(
            "Gemini",
            "background-model",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"background-{used_tokens}-{index}",
        )
    for index in range(used_tokens):
        request_log.record(
            "Codex",
            "gpt-test",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"refill-{used_tokens}-{index}",
            audit={"client_id": "refill-key", "client_label": "Refill Key"},
        )

    with sqlite3.connect(db) as conn:
        retained = conn.execute("SELECT COUNT(*) FROM request_events").fetchone()[0]
        rolled = conn.execute("SELECT COALESCE(SUM(requests), 0) FROM usage_rollups").fetchone()[0]
    assert retained == used_tokens
    assert rolled == 5

    handler = FakeGatewayHandler("refill-secret")
    assert client_identity.authorize(handler, "Codex") is expected_allowed
    assert handler.status == (None if expected_allowed else 429)


def test_refill_quota_starts_clean_cycle_after_window_boundary(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    client = _client(
        "Refill Key",
        "refill-key",
        "refill-secret",
        max_tokens=350,
        refill_period_seconds=3600,
    )
    registry.write_text(json.dumps({"mode": "enforce", "clients": [client]}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")
    started_at = 1_700_000_000.0
    monkeypatch.setattr(request_log.time, "time", lambda: started_at)

    for index in range(400):
        request_log.record(
            "Codex",
            "gpt-test",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"cycle-one-{index}",
            audit={"client_id": "refill-key", "client_label": "Refill Key"},
        )

    monkeypatch.setattr(request_log.time, "time", lambda: started_at + 3601)
    handler = FakeGatewayHandler("refill-secret")
    assert client_identity.authorize(handler, "Codex") is True
    assert handler.status is None


def test_refill_cycle_anchor_survives_first_event_retention(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    client = _client(
        "Refill Key",
        "refill-key",
        "refill-secret",
        max_tokens=1,
        refill_period_seconds=100,
        token_limits={"codex": 1},
    )
    registry.write_text(json.dumps({"mode": "enforce", "clients": [client]}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")

    monkeypatch.setattr(request_log.time, "time", lambda: 100.0)
    request_log.record(
        "Codex", "gpt-test",
        {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        request_id="cycle-anchor-first",
        audit={"client_id": "refill-key", "client_label": "Refill Key"},
    )
    monkeypatch.setattr(request_log.time, "time", lambda: 310.0)
    for index in range(301):
        request_log.record(
            "Gemini", "background-model",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"cycle-anchor-background-{index}",
        )
    monkeypatch.setattr(request_log.time, "time", lambda: 320.0)
    request_log.record(
        "Codex", "gpt-test",
        {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        request_id="cycle-anchor-current",
        audit={"client_id": "refill-key", "client_label": "Refill Key"},
    )

    monkeypatch.setattr(client_identity.time, "time", lambda: 401.0)
    handler = FakeGatewayHandler("refill-secret")
    assert client_identity.authorize(handler, "Codex") is True
    assert handler.status is None

    status, response = _dashboard_post(
        monkeypatch, tmp_path, "/api/tokens/details", {"id": "refill-key"}
    )
    details = response["details"]
    assert status == 200
    assert details["cycle_used_tokens"] == 0
    assert details["remaining_tokens"] == 1
    assert details["provider_remaining"]["codex"] == {
        "limit": 1, "used": 0, "remaining": 1,
    }


def test_key_analytics_marks_retained_breakdowns_without_claiming_lifetime(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    client = _client("Analytics Key", "analytics-key", "analytics-secret")
    registry.write_text(json.dumps({"mode": "enforce", "clients": [client]}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")

    for index in range(305):
        request_log.record(
            "Codex" if index % 2 == 0 else "Gemini",
            "rolled-model" if index < 5 else "retained-model",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"analytics-{index}",
            audit={"client_id": "analytics-key", "client_label": "Analytics Key"},
        )

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT total_tokens, requests FROM client_usage_counters WHERE client_id=?",
            ("analytics-key",),
        ).fetchone() == (305, 305)

    status, response = _dashboard_post(
        monkeypatch, tmp_path, "/api/tokens/details", {"id": "analytics-key"}
    )
    details = response["details"]

    assert status == 200
    assert details["total_requests"] == 305, details
    assert details["total_tokens"] == 305
    assert sum(item["requests"] for item in details["by_provider"].values()) == 300
    assert details["analytics_scope"] == {
        "total_tokens": "lifetime",
        "total_requests": "lifetime",
        "provider_tokens": "lifetime",
        "provider_requests": "retained",
        "top_models": "retained",
    }
    assert {item["model"] for item in details["top_models"]} == {"retained-model"}


def test_key_details_include_all_retained_refill_events_beyond_500(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    client = _client(
        "Analytics Key", "analytics-key", "analytics-secret",
        max_tokens=501, refill_period_seconds=3600,
    )
    registry.write_text(json.dumps({"mode": "enforce", "clients": [client]}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")

    request_log.initialize(str(db))
    now = time.time()
    audit_json = json.dumps({"client_id": "analytics-key", "client_label": "Analytics Key"})
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO request_events(model,pool,prompt_tokens,completion_tokens,total_tokens,timestamp,time_formatted,request_id,audit_json) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ("current-cycle-model", "Codex", 1, 0, 1, now, "", f"analytics-refill-{index}", audit_json)
                for index in range(501)
            ],
        )
        conn.execute(
            "INSERT INTO client_usage_counters(client_id,total_tokens,prompt_tokens,completion_tokens,requests,codex_tokens,gemini_tokens,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("analytics-key", 501, 501, 0, 501, 501, 0, now, now),
        )
        conn.commit()

    status, response = _dashboard_post(
        monkeypatch, tmp_path, "/api/tokens/details", {"id": "analytics-key"}
    )
    details = response["details"]
    assert status == 200
    assert details["cycle_used_tokens"] == 501
    assert details["remaining_tokens"] == 0
    assert details["by_provider"]["codex"]["requests"] == 501
    assert details["top_models"] == [{"model": "current-cycle-model", "tokens": 501}]


def test_key_analytics_ui_labels_retained_breakdowns_truthfully():
    html = (ROOT / "dashboard" / "templates" / "dashboard.html").read_text()
    assert "USAGE BY PROVIDER</div>" in html
    assert html.count("lifetime tokens</span>") == 2
    assert html.count("retained requests</span>") == 2
    assert "TOP RETAINED MODELS" in html
    assert 'id="details-top-models-section"' in html
    assert 'id="details-top-models-container"' in html
    assert "d.cycle_used_tokens ?? d.total_tokens ?? 0" in html
    assert "d.cycle_used_tokens || d.total_tokens" not in html


def test_valid_magic_link_registers_device_sets_cookie_and_redirects_cleanly(tmp_path, monkeypatch):
    auth = TokenAuthManager(str(tmp_path / "auth.db"), session_expiry_hours=24)
    link = auth.generate_magic_link("https://example.test", 7)
    token = link.split("token=", 1)[1]

    handler = _dashboard_get(
        monkeypatch,
        tmp_path,
        f"/auth?token={token}",
        auth,
        headers={"User-Agent": "Mozilla/5.0", "X-Forwarded-Proto": "https"},
    )

    cookie = handler.response_headers["Set-Cookie"]
    assert handler.status == 303
    assert handler.response_headers["Location"] == "/"
    assert "yj_aipool_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Path=/" in cookie
    assert "Max-Age=86400" in cookie
    assert "Secure" in cookie
    assert token not in handler.wfile.getvalue().decode()
    assert token not in handler.response_headers["Location"]


def test_invalid_magic_link_is_unauthorized_without_token_echo(tmp_path, monkeypatch):
    auth = TokenAuthManager(str(tmp_path / "auth.db"))
    token = "invalid-secret-token"
    handler = _dashboard_get(
        monkeypatch,
        tmp_path,
        f"/auth?token={token}",
        auth,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    assert handler.status == 401
    assert token not in handler.wfile.getvalue().decode()


def test_expired_magic_link_is_unauthorized(tmp_path, monkeypatch):
    auth = TokenAuthManager(str(tmp_path / "auth.db"))
    started_at = 1_700_000_000.0
    monkeypatch.setattr(sys.modules["app"].time, "time", lambda: started_at)
    token = auth.generate_magic_link("https://example.test", 7).split("token=", 1)[1]
    monkeypatch.setattr(sys.modules["app"].time, "time", lambda: started_at + 1801)

    handler = _dashboard_get(
        monkeypatch,
        tmp_path,
        f"/auth?token={token}",
        auth,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    assert handler.status == 401


def test_existing_session_precedes_magic_token_and_localhost_stays_trusted(tmp_path, monkeypatch):
    auth = TokenAuthManager(str(tmp_path / "auth.db"))
    first = auth.generate_magic_link("https://example.test", 7).split("token=", 1)[1]
    session = auth.register_device(first, "198.51.100.20", "Mozilla/5.0")
    unused = auth.generate_magic_link("https://example.test", 7).split("token=", 1)[1]

    handler = _dashboard_get(
        monkeypatch,
        tmp_path,
        f"/auth?token={unused}",
        auth,
        headers={"Cookie": f"yj_aipool_session={session}", "User-Agent": "Mozilla/5.0"},
    )
    assert handler.status == 303
    assert handler.response_headers["Location"] == "/"
    assert auth.register_device(unused, "198.51.100.20", "Mozilla/5.0") is not None

    local = _dashboard_get(monkeypatch, tmp_path, "/api/build-hash", auth, remote=False)
    assert local.status == 200


def test_refill_retention_fails_closed_when_registry_is_malformed(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    client = _client(
        "Refill Key",
        "refill-key",
        "refill-secret",
        max_tokens=350,
        refill_period_seconds=3600,
    )
    registry.write_text("{malformed")
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")

    for index in range(400):
        request_log.record(
            "Codex",
            "gpt-test",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"malformed-registry-{index}",
            audit={"client_id": "refill-key", "client_label": "Refill Key"},
        )

    registry.write_text(json.dumps({"mode": "enforce", "clients": [client]}))
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM request_events").fetchone()[0] == 400

    handler = FakeGatewayHandler("refill-secret")
    assert client_identity.authorize(handler, "Codex") is False
    assert handler.status == 429


def test_refill_retention_fails_closed_when_registry_path_is_missing(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "missing-clients.json"
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")

    for index in range(400):
        request_log.record(
            "Codex",
            "gpt-test",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"missing-registry-{index}",
            audit={"client_id": "refill-key", "client_label": "Refill Key"},
        )

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM request_events").fetchone()[0] == 400


@pytest.mark.parametrize("invalid_registry", [None, []])
def test_top_level_malformed_registry_fails_remote_closed_but_keeps_loopback(tmp_path, monkeypatch, invalid_registry):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    registry.write_text(json.dumps(invalid_registry))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))

    remote = FakeGatewayHandler("anything")
    assert client_identity.authorize(remote, "Codex") is False
    assert remote.status == 401

    local = FakeGatewayHandler("anything")
    local.client_address = ("127.0.0.1", 12345)
    assert client_identity.authorize(local, "Codex") is True
    assert local.status is None


@pytest.mark.parametrize("bad_limit", ["not-a-number", -1, 1.5, True])
def test_malformed_existing_registry_quota_fails_closed(tmp_path, monkeypatch, bad_limit):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    client = _client("Bad Quota", "bad-quota", "bad-secret", max_tokens=bad_limit)
    registry.write_text(json.dumps({"mode": "enforce", "clients": [client]}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))

    handler = FakeGatewayHandler("bad-secret")
    assert client_identity.authorize(handler, "Codex") is False
    assert handler.status == 500


def test_refill_retention_fails_closed_when_registry_contains_non_objects(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    registry = tmp_path / "clients.json"
    valid_client = _client(
        "Refill Key", "refill-key", "refill-secret",
        max_tokens=350, refill_period_seconds=3600,
    )
    registry.write_text(json.dumps({"mode": "enforce", "clients": [valid_client, None]}))
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))
    monkeypatch.setenv("AIPOOL_REQUEST_RETENTION", "300")

    for index in range(400):
        request_log.record(
            "Codex", "gpt-test",
            {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            request_id=f"invalid-client-entry-{index}",
            audit={"client_id": "refill-key", "client_label": "Refill Key"},
        )

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM request_events").fetchone()[0] == 400

    handler = FakeGatewayHandler("refill-secret")
    assert client_identity.authorize(handler, "Codex") is False
    assert handler.status in (401, 429)


def test_link_preview_does_not_consume_magic_token(tmp_path, monkeypatch):
    auth = TokenAuthManager(str(tmp_path / "auth.db"))
    token = auth.generate_magic_link("https://example.test", 7).split("token=", 1)[1]

    handler = _dashboard_get(
        monkeypatch,
        tmp_path,
        f"/auth?token={token}",
        auth,
        headers={"User-Agent": "TelegramBot (like TwitterBot)"},
    )

    assert handler.status == 204
    assert auth.register_device(token, "198.51.100.20", "Mozilla/5.0") is not None


def test_frontend_quota_parser_rejects_negative_and_invalid_values():
    html = (ROOT / "dashboard" / "templates" / "dashboard.html").read_text()
    start = html.index("        function parseTokenShorthand(val) {")
    end = html.index("        function updateQuotaPreview(kind) {", start)
    function_source = html[start:end]
    script = function_source + """
const values = ['-5M', '-1', 'invalid', '0', '1.5M', '25'].map(parseTokenShorthand);
console.log(JSON.stringify(values.map(value => Number.isNaN(value) ? 'invalid' : value)));
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == ["invalid", "invalid", "invalid", None, 1_500_000, 25]
    assert "Quota values must be zero or positive numbers" in html


@pytest.mark.parametrize(
    "payload",
    [
        {"max_tokens": -1},
        {"max_tokens": "-5M"},
        {"max_tokens": "invalid"},
        {"token_limits": {"codex": -1}},
        {"token_limits": {"gemini": "invalid"}},
    ],
)
def test_backend_rejects_invalid_quotas_without_changing_registry(tmp_path, monkeypatch, payload):
    registry = tmp_path / "clients.json"
    original = _client(
        "Quota Key",
        "quota-key",
        "quota-secret",
        max_tokens=100,
        token_limits={"codex": 50},
    )
    registry.write_text(json.dumps({"mode": "enforce", "clients": [original]}))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))

    status, response = _dashboard_post(
        monkeypatch,
        tmp_path,
        "/api/tokens/update",
        {"id": "quota-key", **payload},
    )

    saved = json.loads(registry.read_text())["clients"][0]
    assert status == 400
    assert response["success"] is False
    assert saved["max_tokens"] == 100
    assert saved["token_limits"] == {"codex": 50}


def test_backend_keeps_zero_unlimited_and_accepts_positive_integer_quotas(tmp_path, monkeypatch):
    registry = tmp_path / "clients.json"
    original = _client("Quota Key", "quota-key", "quota-secret", max_tokens=100)
    registry.write_text(json.dumps({"mode": "enforce", "clients": [original]}))
    monkeypatch.setenv("AIPOOL_CLIENT_REGISTRY", str(registry))

    zero_status, _ = _dashboard_post(
        monkeypatch,
        tmp_path,
        "/api/tokens/update",
        {"id": "quota-key", "max_tokens": 0},
    )
    assert zero_status == 200
    assert json.loads(registry.read_text())["clients"][0]["max_tokens"] is None

    positive_status, _ = _dashboard_post(
        monkeypatch,
        tmp_path,
        "/api/tokens/update",
        {
            "id": "quota-key",
            "max_tokens": 1_500_000,
            "token_limits": {"codex": 25},
        },
    )
    saved = json.loads(registry.read_text())["clients"][0]
    assert positive_status == 200
    assert saved["max_tokens"] == 1_500_000
    assert saved["token_limits"] == {"codex": 25}


def test_dashboard_has_no_actionable_moa_runtime_path():
    html = (ROOT / "dashboard" / "templates" / "dashboard.html").read_text()
    assert "/api/mixture/switch" not in html
    assert "switchMoaAggregator" not in html
    assert "currentProvider === 'Mixture'" not in html
    assert "Active MoA" not in html
    assert "switchProvider('Codex')" in html
    assert "switchProvider('Gemini')" in html
    assert "['Codex', 'Gemini'].includes(prov)" in html


def test_historical_mixture_reporting_remains_available(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    request_log.record(
        "Mixture",
        "legacy-moa-model",
        {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        request_id="historical-mixture",
    )

    report = request_log.report(str(db))
    assert report["providers"]["Mixture"]["requests"] == 1
    assert report["providers"]["Mixture"]["total_tokens"] == 5
