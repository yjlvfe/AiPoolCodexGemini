import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from pool_runtime import AccountPool


def _pool_with_account(db):
    pool = AccountPool('codex')
    manager = type('ManagerStub', (), {
        'active': lambda self: '1',
        'ids': lambda self: ['1'],
        'credential': lambda self, number: Path('/tmp/account.json'),
        'identity': lambda self, data: {'identity': 'identity-1'},
    })
    return pool, manager


def test_a1_hard_exhaustion_is_recorded_without_a_generic_expiry():
    with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'pool.db')}):
        pool, manager = _pool_with_account(d)
        with patch('pool_runtime.Manager', return_value=manager()), patch('pool_runtime.read_json', return_value={}):
            pool.exhausted('1', 'model', seconds=1, reason='quota_exhausted')
            until, reason = pool._load_cooldown_record('1', 'model')
        assert until == 0
        assert reason == 'quota_exhausted'


def test_a2_hard_exhaustion_stays_excluded_after_generic_cooldown_expires():
    with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'pool.db')}):
        pool, manager = _pool_with_account(d)
        with patch('pool_runtime.Manager', return_value=manager()), patch('pool_runtime.read_json', return_value={}):
            pool.exhausted('1', 'model', seconds=1, reason='quota_exhausted')
            assert pool.candidates('model') == []


def test_a3_restart_does_not_resurrect_hard_exhaustion():
    with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'pool.db')}):
        with patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'pool.db')}):
            first, manager = _pool_with_account(d)
            with patch('pool_runtime.Manager', return_value=manager()), patch('pool_runtime.read_json', return_value={}):
                first.exhausted('1', 'model', seconds=1, reason='quota_exhausted')
                restarted = AccountPool('codex')
                assert restarted.candidates('model') == []


def test_a4_authoritative_reset_time_restores_eligibility():
    with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'pool.db')}):
        pool, manager = _pool_with_account(d)
        with patch('pool_runtime.Manager', return_value=manager()), patch('pool_runtime.read_json', return_value={}):
            pool.exhausted('1', 'model', seconds=1, reason='quota_exhausted')
            pool.authoritative_quota_reset('1', 'model', reset_at=0)
            assert pool.candidates('model') == ['1']


def test_a5_regular_candidate_polling_cannot_restore_hard_exhaustion():
    with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'pool.db')}):
        pool, manager = _pool_with_account(d)
        with patch('pool_runtime.Manager', return_value=manager()), patch('pool_runtime.read_json', return_value={}):
            pool.exhausted('1', 'model', seconds=0, reason='quota_exhausted')
            assert [pool.candidates('model') for _ in range(3)] == [[], [], []]


def test_a6_reset_changes_candidates_but_never_active_account():
    with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'pool.db')}):
        pool, manager = _pool_with_account(d)
        with patch('pool_runtime.Manager', return_value=manager()), patch('pool_runtime.read_json', return_value={}):
            pool.exhausted('1', 'model', reason='quota_exhausted')
            pool.authoritative_quota_reset('1', 'model', reset_at=0)
            assert pool.candidates('model') == ['1']
            assert manager().active() == '1'
