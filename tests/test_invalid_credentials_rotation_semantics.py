import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridges"))

import gemini_bridge
import pool_runtime
from pool_runtime import AccountPool, PoolError


def test_b1_permanent_auth_failure_rotates_once_to_next_healthy_account():
    body = {"model": "gemini-3.8-flash"}
    used = []
    with patch.object(gemini_bridge.AG_POOL, "candidates", return_value=["1", "2"]), \
         patch.object(gemini_bridge.AG_POOL, "credentials", return_value={"token": {"access_token": "x"}}), \
         patch.object(gemini_bridge, "call_antigravity", side_effect=[PoolError("revoked token", 401), {"ok": True}]) as call, \
         patch.object(gemini_bridge, "Manager") as manager, \
         patch.object(gemini_bridge.AG_POOL, "exhausted") as exhausted:
        manager.return_value.active.return_value = "1"
        result = gemini_bridge.call_with_retry(
            body, attempts=2, sleep=lambda _: None,
            on_success=lambda account, rotate: used.append((account, rotate)),
        )
    assert result == {"ok": True}
    assert call.call_count == 2
    assert used == [("2", True)]
    exhausted.assert_called_once_with("1", "gemini-3.8-flash", 60, reason="invalid_credentials")


def test_b2_temporary_provider_failure_does_not_rotate_account():
    body = {"model": "gemini-3.8-flash"}
    with patch.object(gemini_bridge.AG_POOL, "candidates", return_value=["1", "2"]), \
         patch.object(gemini_bridge.AG_POOL, "credentials", return_value={"token": {"access_token": "x"}}), \
         patch.object(gemini_bridge, "call_antigravity", side_effect=PoolError("temporary outage", 503)) as call, \
         patch.object(gemini_bridge, "Manager") as manager:
        manager.return_value.active.return_value = "1"
        with pytest.raises(PoolError) as ctx:
            gemini_bridge.call_with_retry(body, attempts=2, sleep=lambda _: None)
        assert ctx.value.status == 503
    # In Zero-Retry architecture, transient 503 calls provider exactly once and does not rotate
    assert call.call_count == 1


class _FakeManager:
    ag = True

    def __init__(self, provider):
        self.store = Path(".")

    def active(self):
        return "1"

    def ids(self):
        return ["1", "2"]

    def credential(self, number):
        return Path(_CREDENTIALS[number])

    def identity(self, data):
        inner = data["token"]
        return {"identity": data["email"], "email": data["email"], "refresh": inner["refresh_token"]}


_CREDENTIALS = {}


def _pool_with_credentials(tmp_path):
    for number in ("1", "2"):
        path = tmp_path / f"{number}.json"
        path.write_text(
            '{"email":"%s@example.test","token":{"refresh_token":"refresh-%s"}}'
            % (number, number)
        )
        _CREDENTIALS[number] = str(path)
    return AccountPool("gemini")


def test_b3_retired_account_stays_out_after_request_cooldown_expires(tmp_path):
    pool = _pool_with_credentials(tmp_path)
    with patch.object(pool_runtime, "Manager", _FakeManager), \
         patch.dict("os.environ", {"AUTH_DB_PATH": str(tmp_path / "auth.db")}):
        pool.exhausted("1", "model", seconds=1, reason="invalid_credentials")
        assert "1" not in pool.candidates("model", include_cooldown=True)
        with patch.object(pool_runtime.time, "time", return_value=10**12):
            assert "1" not in pool.candidates("model", include_cooldown=True)


def test_b4_silent_refresh_candidates_never_reintroduce_retired_account(tmp_path):
    pool = _pool_with_credentials(tmp_path)
    with patch.object(pool_runtime, "Manager", _FakeManager), \
         patch.dict("os.environ", {"AUTH_DB_PATH": str(tmp_path / "auth.db")}):
        pool.exhausted("1", "model", seconds=1, reason="invalid_credentials")
        with patch.object(pool_runtime.time, "time", return_value=10**12):
            # include_cooldown=True models the bridge/background refresh path.
            assert pool.candidates("model", include_cooldown=True) == ["2"]
