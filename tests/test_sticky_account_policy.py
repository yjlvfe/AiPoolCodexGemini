import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))

import gemini_bridge
from pool_runtime import PoolError


def test_gemini_transient_retries_stay_on_active_account():
    body = {'model': 'gemini-3.8-flash'}
    failures = [PoolError('temporary outage', 503), PoolError('temporary 429', 429), {'ok': True}]
    used = []
    with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=['1', '2']), \
         patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'x'}}), \
         patch.object(gemini_bridge, 'call_antigravity', side_effect=failures) as call, \
         patch.object(gemini_bridge, 'Manager') as manager, \
         patch.object(gemini_bridge.time, 'sleep'), \
         patch.object(gemini_bridge.AG_POOL, 'promote') as promote:
        manager.return_value.active.return_value = '1'
        result = gemini_bridge.call_with_retry(body, attempts=3, sleep=lambda _: None,
                                               on_success=lambda number, should_promote: used.append((number, should_promote)))
    assert result == {'ok': True}
    assert call.call_count == 3
    assert used == [('1', False)]
    promote.assert_not_called()


def test_gemini_hard_failure_rotates_and_promotes_only_after_success():
    body = {'model': 'gemini-3.8-flash'}
    with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=['1', '2']), \
         patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'x'}}), \
         patch.object(gemini_bridge, 'call_antigravity', side_effect=[PoolError('invalid credentials', 401), {'ok': True}]), \
         patch.object(gemini_bridge, 'Manager') as manager, \
         patch.object(gemini_bridge.time, 'sleep'), \
         patch.object(gemini_bridge.AG_POOL, 'promote') as promote:
        manager.return_value.active.return_value = '1'
        selected = []
        result = gemini_bridge.call_with_retry(
            body, attempts=2, sleep=lambda _: None,
            on_success=lambda number, should_promote: selected.append((number, should_promote)),
        )
    assert result == {'ok': True}
    assert selected == [('2', True)]
    promote.assert_not_called()
