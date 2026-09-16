import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))

import gemini_bridge
import codex_bridge
from pool_runtime import AccountPool, PoolError
from account_manager import Manager


def test_sticky_success_never_changes_active():
    """Test 1: 100 successful requests keep active account sticky."""
    body = {'model': 'gemini-3.8-flash'}
    with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=['2', '3', '4', '1']), \
         patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'x'}}), \
         patch.object(gemini_bridge, 'call_antigravity', return_value={'ok': True}) as call, \
         patch.object(gemini_bridge, 'Manager') as manager, \
         patch.object(gemini_bridge.AG_POOL, 'promote') as promote:
        manager.return_value.active.return_value = '2'
        for _ in range(100):
            res = gemini_bridge.call_with_retry(body, attempts=3, sleep=lambda _: None)
            assert res == {'ok': True}
        assert call.call_count == 100
        # promote is never called because active account remains 2
        promote.assert_not_called()


def test_transient_5xx_retries_same_account_no_promote():
    """Test 2: Transient 5xx retries on the same account and does not promote."""
    body = {'model': 'gemini-3.8-flash'}
    failures = [PoolError('upstream 500', 500), PoolError('upstream 503', 503), {'ok': True}]
    with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=['2', '3', '4', '1']), \
         patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'x'}}), \
         patch.object(gemini_bridge, 'call_antigravity', side_effect=failures) as call, \
         patch.object(gemini_bridge, 'Manager') as manager, \
         patch.object(gemini_bridge.AG_POOL, 'promote') as promote:
        manager.return_value.active.return_value = '2'
        res = gemini_bridge.call_with_retry(body, attempts=3, sleep=lambda _: None)
        assert res == {'ok': True}
        assert call.call_count == 3
        promote.assert_not_called()


def test_network_reset_retries_same_account_no_promote():
    """Test 3: Connection reset stays on same active account."""
    body = {'model': 'gemini-3.8-flash'}
    failures = [ConnectionResetError('connection reset by peer'), {'ok': True}]
    with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=['2', '3', '4', '1']), \
         patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'x'}}), \
         patch.object(gemini_bridge, 'call_antigravity', side_effect=failures) as call, \
         patch.object(gemini_bridge, 'Manager') as manager, \
         patch.object(gemini_bridge.AG_POOL, 'promote') as promote:
        manager.return_value.active.return_value = '2'
        res = gemini_bridge.call_with_retry(body, attempts=3, sleep=lambda _: None)
        assert res == {'ok': True}
        assert call.call_count == 2
        promote.assert_not_called()


def test_temporary_429_retries_same_account_no_promote():
    """Test 4: Transient 429 retries same account and does not promote."""
    body = {'model': 'gemini-3.8-flash'}
    failures = [PoolError('rate limit spike - please slow down', 429), {'ok': True}]
    with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=['2', '3', '4', '1']), \
         patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'x'}}), \
         patch.object(gemini_bridge, 'call_antigravity', side_effect=failures) as call, \
         patch.object(gemini_bridge, 'Manager') as manager, \
         patch.object(gemini_bridge.AG_POOL, 'promote') as promote:
        manager.return_value.active.return_value = '2'
        res = gemini_bridge.call_with_retry(body, attempts=3, sleep=lambda _: None)
        assert res == {'ok': True}
        assert call.call_count == 2
        promote.assert_not_called()


def test_hard_exhaustion_rotates_and_promotes_next():
    """Test 5 & 6: Confirmed hard quota exhaustion rotates and marks promote."""
    body = {'model': 'gemini-3.8-flash'}
    failures = [PoolError('User quota exhausted for today', 429), {'ok': True}]
    used = []
    with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=['2', '3', '4', '1']), \
         patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'x'}}), \
         patch.object(gemini_bridge, 'call_antigravity', side_effect=failures), \
         patch.object(gemini_bridge, 'Manager') as manager, \
         patch.object(gemini_bridge.AG_POOL, 'promote') as promote:
        manager.return_value.active.return_value = '2'
        res = gemini_bridge.call_with_retry(
            body, attempts=3, sleep=lambda _: None,
            on_success=lambda num, should_promote: used.append((num, should_promote))
        )
        assert res == {'ok': True}
        assert used == [('3', True)]


def test_no_bounce_back_after_rotation():
    """Test 7 & 8: Once rotated to account 3, subsequent requests remain on 3."""
    body = {'model': 'gemini-3.8-flash'}
    # Account 2 has hard cooldown persisted
    with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=['3', '4', '1']), \
         patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'x'}}), \
         patch.object(gemini_bridge, 'call_antigravity', return_value={'ok': True}) as call, \
         patch.object(gemini_bridge, 'Manager') as manager, \
         patch.object(gemini_bridge.AG_POOL, 'promote') as promote:
        manager.return_value.active.return_value = '3'
        res = gemini_bridge.call_with_retry(body, attempts=3, sleep=lambda _: None)
        assert res == {'ok': True}
        assert call.call_count == 1
        promote.assert_not_called()
