import sys
import threading
import time
import types

from dashboard.app import PoolManager


def manager_for_refresh(update):
    manager = PoolManager.__new__(PoolManager)
    manager._refresh_lock = threading.Lock()
    manager._refresh_done = threading.Event()
    manager._refresh_inflight = False
    manager._refresh_state = 'idle'
    manager._refresh_error = None
    manager._refresh_started_at = None
    manager._refresh_completed_at = None
    manager._update_all_background = update
    return manager


def test_refresh_waits_for_fresh_snapshot_and_reports_completed():
    calls = []
    manager = manager_for_refresh(lambda: calls.append('fresh'))

    result = manager.trigger_instant_refresh(timeout=1)

    assert calls == ['fresh']
    assert result['state'] == 'completed'
    assert result['inflight'] is False
    assert result['error'] is None
    assert result['completed_at'] >= result['started_at']


def test_instant_refresh_returns_while_provider_is_hung(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def hung_provider(_name):
        started.set()
        release.wait(2)
        raise TimeoutError('provider timeout')

    monkeypatch.setitem(sys.modules, 'account_reports', types.SimpleNamespace(pool_report=hung_provider))
    manager = PoolManager.__new__(PoolManager)
    manager._lock = threading.Lock()
    manager._refresh_lock = threading.Lock()
    manager._refresh_done = threading.Event()
    manager._refresh_inflight = False
    manager._refresh_state = 'idle'
    manager._refresh_error = None
    manager._refresh_started_at = None
    manager._refresh_completed_at = None

    started_at = time.monotonic()
    result = manager.trigger_instant_refresh(timeout=0.02)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.5
    assert result['state'] == 'pending'
    assert result['inflight'] is True
    assert started.wait(1)

    release.set()
    deadline = time.time() + 1
    while manager._refresh_inflight and time.time() < deadline:
        time.sleep(0.01)
    assert manager._refresh_state == 'failed'
    assert 'TimeoutError' in manager._refresh_error


def test_refresh_timeout_reports_pending_then_failure_without_overlap():
    started = threading.Event()
    release = threading.Event()

    def slow_failure():
        started.set()
        release.wait(1)
        raise TimeoutError('provider timeout')

    manager = manager_for_refresh(slow_failure)
    first = manager.trigger_instant_refresh(timeout=0.01)
    assert first['state'] == 'pending'
    assert first['inflight'] is True
    assert started.wait(1)

    second = manager.trigger_instant_refresh(timeout=0.01)
    assert second['state'] == 'pending'
    assert second['inflight'] is True

    release.set()
    deadline = time.time() + 1
    while manager._refresh_inflight and time.time() < deadline:
        time.sleep(0.01)
    assert manager._refresh_state == 'failed'
    assert manager._refresh_error is not None
    assert 'TimeoutError' in manager._refresh_error
