import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))

import gemini_bridge
from pool_runtime import AccountPool


class _FakeManager:
    active_value = '2'
    lock = threading.RLock()

    def __init__(self, provider):
        self.provider = provider

    def active(self):
        return type(self).active_value

    @contextmanager
    def locked(self):
        with type(self).lock:
            yield

    def add_or_switch(self, number, server_verified=False):
        assert server_verified is True
        type(self).active_value = number

    @classmethod
    def manual_switch(cls, number):
        with cls.lock:
            cls.active_value = number


def test_d1_concurrent_hard_failures_have_one_logical_promotion():
    """Two stale completions from 2 may not both promote past account 3."""
    _FakeManager.active_value = '2'
    pool = AccountPool('gemini')
    barrier = threading.Barrier(2)

    def promote():
        barrier.wait()
        return pool.promote('3', expected_current='2')

    with patch('pool_runtime.Manager', _FakeManager):
        results = [None, None]
        threads = [threading.Thread(target=lambda i=i: results.__setitem__(i, promote())) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert sorted(results) == [False, True]
    assert _FakeManager.active_value == '3'


def test_d2_stale_completion_cannot_overwrite_newer_transition():
    _FakeManager.active_value = '2'
    pool = AccountPool('gemini')
    with patch('pool_runtime.Manager', _FakeManager):
        assert pool.promote('3', expected_current='2') is True
        assert pool.promote('4', expected_current='2') is False
    assert _FakeManager.active_value == '3'


def test_d3_manual_switch_wins_over_stale_request_completion():
    _FakeManager.active_value = '2'
    pool = AccountPool('gemini')
    _FakeManager.manual_switch('7')
    with patch('pool_runtime.Manager', _FakeManager):
        assert pool.promote('3', expected_current='2') is False
    assert _FakeManager.active_value == '7'


def test_d4_gemini_success_passes_request_generation_to_promotion():
    handler = object.__new__(gemini_bridge.Handler)

    def run_callback(body, attempts=None, on_success=None, **kwargs):
        on_success('3', True)
        return {'ok': True}

    with patch.object(gemini_bridge, 'Manager') as manager, \
         patch.object(gemini_bridge, 'call_with_retry', side_effect=run_callback), \
         patch.object(gemini_bridge.AG_POOL, 'promote') as promote:
        manager.return_value.active.return_value = '2'
        assert handler._call_with_retry({'model': 'gemini-3.8-flash'}) == {'ok': True}

    promote.assert_called_once_with('3', expected_current='2')


def test_d5_promote_without_expected_current_remains_backward_compatible():
    _FakeManager.active_value = '2'
    pool = AccountPool('gemini')
    with patch('pool_runtime.Manager', _FakeManager):
        assert pool.promote('3') is True
    assert _FakeManager.active_value == '3'
