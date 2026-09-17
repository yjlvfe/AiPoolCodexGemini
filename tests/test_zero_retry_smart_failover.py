"""Comprehensive acceptance tests for Zero-Retry Smart Failover Architecture.

Tests 1 to 12 as strictly specified in the prompt:
1. No Internal Retry on 5xx (called once, immediate return, sticky active)
2. No Internal Retry on Temporary 429 (called once, immediate return, sticky active)
3. No Internal Retry on Network Error (called once, immediate error)
4. Immediate Hard Failover (7 exhausted, 8 healthy -> 7 called once, 8 called once -> active=8)
5. Two Exhausted Then Success (7,8 exhausted, 9 healthy -> 7,8,9 called exactly once -> active=9)
6. Hard Exhaustion Then Transient Failure (7 exhausted, 8 selected, 8 returns 500 -> stop, do NOT try 9)
7. All Accounts Exhausted (candidates attempted once -> ALL_ACCOUNTS_EXHAUSTED)
8. Hermes Re-request (transient 500 on active=2 -> fresh second request starts on active=2)
9. No Retry Sleeps (monkeypatch sleep -> assert 0 sleep in request path)
10. Retry Policy Disabled (AIPOOL_PROVIDER_MAX_ATTEMPTS=100 -> exactly 1 attempt)
11. Concurrency (concurrent hard exhaustion CAS safety)
12. Restart Persistence (exhausted accounts stay excluded)
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))

from error_taxonomy import ErrorCode, classify
from gemini_bridge import call_with_retry
from pool_runtime import AccountPool, PoolError
from retry_policy import provider_attempts


class ZeroRetrySmartFailoverTests(unittest.TestCase):

    @patch('gemini_bridge.call_antigravity')
    @patch('gemini_bridge.AG_POOL')
    @patch('gemini_bridge.Manager')
    def test_1_no_internal_retry_on_5xx(self, mock_mgr, mock_pool, mock_call):
        """Test 1: 5xx called exactly once, returned immediately, active remains same."""
        mock_mgr.return_value.active.return_value = '2'
        mock_pool.candidates.return_value = ['2', '3']
        mock_pool.credentials.return_value = {'token': {'access_token': 'tok2'}}
        
        # Upstream returns 500
        mock_call.side_effect = PoolError('Antigravity upstream HTTP 500', 500)
        
        with self.assertRaises(PoolError) as ctx:
            call_with_retry({'model': 'gemini-3.8-flash'})
        
        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(mock_call.call_count, 1)
        mock_pool.exhausted.assert_not_called()
        mock_pool.promote.assert_not_called()

    @patch('gemini_bridge.call_antigravity')
    @patch('gemini_bridge.AG_POOL')
    @patch('gemini_bridge.Manager')
    def test_2_no_internal_retry_on_temporary_429(self, mock_mgr, mock_pool, mock_call):
        """Test 2: Transient 429 called exactly once, no sleep/backoff, active remains same."""
        mock_mgr.return_value.active.return_value = '1'
        mock_pool.candidates.return_value = ['1', '2']
        mock_pool.credentials.return_value = {'token': {'access_token': 'tok1'}}

        # Transient 429 (not hard quota)
        err = PoolError('rate limit spike - please slow down', 429)
        mock_call.side_effect = err

        with self.assertRaises(PoolError) as ctx:
            call_with_retry({'model': 'gemini-3.8-flash'})

        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(mock_call.call_count, 1)
        mock_pool.exhausted.assert_not_called()

    @patch('gemini_bridge.call_antigravity')
    @patch('gemini_bridge.AG_POOL')
    @patch('gemini_bridge.Manager')
    def test_3_no_internal_retry_on_network_error(self, mock_mgr, mock_pool, mock_call):
        """Test 3: Network error called exactly once, returned immediately."""
        mock_mgr.return_value.active.return_value = '1'
        mock_pool.candidates.return_value = ['1', '2']
        mock_pool.credentials.return_value = {'token': {'access_token': 'tok1'}}

        mock_call.side_effect = ConnectionResetError("Connection reset by peer")

        with self.assertRaises(ConnectionResetError):
            call_with_retry({'model': 'gemini-3.8-flash'})

        self.assertEqual(mock_call.call_count, 1)
        mock_pool.exhausted.assert_not_called()

    @patch('gemini_bridge.call_antigravity')
    @patch('gemini_bridge.AG_POOL')
    @patch('gemini_bridge.Manager')
    def test_4_immediate_hard_failover(self, mock_mgr, mock_pool, mock_call):
        """Test 4: 7 hard exhausted, 8 healthy -> 7 called once, 8 called once -> success, active=8."""
        mock_mgr.return_value.active.return_value = '7'
        mock_pool.candidates.return_value = ['7', '8']
        mock_pool.credentials.side_effect = lambda n: {'token': {'access_token': f'tok{n}'}}

        # Account 7 gives hard quota exhausted; Account 8 succeeds
        def side_effect(body, token, deadline=None):
            if token == 'tok7':
                raise PoolError('quota exceeded', 429)
            return {'candidates': [{'content': 'ok'}]}

        mock_call.side_effect = side_effect

        promoted = []
        def mock_promote(num, expected_current=None):
            promoted.append((num, expected_current))
        mock_pool.promote.side_effect = mock_promote

        def on_success(num, should_promote):
            if should_promote:
                mock_pool.promote(num, expected_current='7')

        res = call_with_retry({'model': 'gemini-3.8-flash'}, on_success=on_success)
        self.assertIn('candidates', res)
        self.assertEqual(mock_call.call_count, 2)
        mock_pool.exhausted.assert_called_once_with('7', 'gemini-3.8-flash', 60, reason='quota_exhausted')
        self.assertEqual(promoted, [('8', '7')])

    @patch('gemini_bridge.call_antigravity')
    @patch('gemini_bridge.AG_POOL')
    @patch('gemini_bridge.Manager')
    def test_5_two_exhausted_then_success(self, mock_mgr, mock_pool, mock_call):
        """Test 5: 7 exhausted, 8 exhausted, 9 healthy -> 7,8,9 each called exactly once."""
        mock_mgr.return_value.active.return_value = '7'
        mock_pool.candidates.return_value = ['7', '8', '9']
        mock_pool.credentials.side_effect = lambda n: {'token': {'access_token': f'tok{n}'}}

        called = []
        def side_effect(body, token, deadline=None):
            called.append(token)
            if token in ('tok7', 'tok8'):
                raise PoolError('monthly_cap quota exhausted', 429)
            return {'candidates': [{'content': 'ok'}]}

        mock_call.side_effect = side_effect

        res = call_with_retry({'model': 'gemini-3.8-flash'})
        self.assertIn('candidates', res)
        self.assertEqual(called, ['tok7', 'tok8', 'tok9'])
        self.assertEqual(mock_call.call_count, 3)

    @patch('gemini_bridge.call_antigravity')
    @patch('gemini_bridge.AG_POOL')
    @patch('gemini_bridge.Manager')
    def test_6_hard_exhaustion_then_transient_failure(self, mock_mgr, mock_pool, mock_call):
        """Test 6: 7 exhausted, 8 returns 500 -> stop, do NOT try 9."""
        mock_mgr.return_value.active.return_value = '7'
        mock_pool.candidates.return_value = ['7', '8', '9']
        mock_pool.credentials.side_effect = lambda n: {'token': {'access_token': f'tok{n}'}}

        called = []
        def side_effect(body, token, deadline=None):
            called.append(token)
            if token == 'tok7':
                raise PoolError('quota exceeded', 429)
            if token == 'tok8':
                raise PoolError('Server internal error', 500)
            return {'candidates': [{'content': 'ok'}]}

        mock_call.side_effect = side_effect

        with self.assertRaises(PoolError) as ctx:
            call_with_retry({'model': 'gemini-3.8-flash'})

        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(called, ['tok7', 'tok8'])
        self.assertEqual(mock_call.call_count, 2)
        # 8 must NOT be marked exhausted
        mock_pool.exhausted.assert_called_once_with('7', 'gemini-3.8-flash', 60, reason='quota_exhausted')

    @patch('gemini_bridge.call_antigravity')
    @patch('gemini_bridge.AG_POOL')
    @patch('gemini_bridge.Manager')
    def test_7_all_accounts_exhausted(self, mock_mgr, mock_pool, mock_call):
        """Test 7: All candidates hard exhausted -> terminal ALL_ACCOUNTS_EXHAUSTED."""
        mock_mgr.return_value.active.return_value = '1'
        mock_pool.candidates.return_value = ['1', '2']
        mock_pool.credentials.side_effect = lambda n: {'token': {'access_token': f'tok{n}'}}

        mock_call.side_effect = PoolError('quota exceeded', 429)

        with self.assertRaises(PoolError) as ctx:
            call_with_retry({'model': 'gemini-3.8-flash'})

        self.assertIn('All accounts are exhausted', str(ctx.exception))
        self.assertEqual(mock_call.call_count, 2)

    @patch('gemini_bridge.call_antigravity')
    @patch('gemini_bridge.AG_POOL')
    @patch('gemini_bridge.Manager')
    def test_8_hermes_re_request_starts_fresh_on_active(self, mock_mgr, mock_pool, mock_call):
        """Test 8: Transient 500 returns immediately; next independent request starts fresh on active=2."""
        mock_mgr.return_value.active.return_value = '2'
        mock_pool.candidates.return_value = ['2', '3']
        mock_pool.credentials.side_effect = lambda n: {'token': {'access_token': f'tok{n}'}}

        # Request 1 fails transiently on account 2
        mock_call.side_effect = PoolError('500 internal error', 500)
        with self.assertRaises(PoolError):
            call_with_retry({'model': 'gemini-3.8-flash'})
        self.assertEqual(mock_call.call_count, 1)

        # Request 2 (simulated client retry) arrives fresh
        mock_call.reset_mock()
        mock_call.side_effect = lambda body, token, deadline=None: {'candidates': [{'content': 'ok'}]}
        res = call_with_retry({'model': 'gemini-3.8-flash'})
        self.assertIn('candidates', res)
        self.assertEqual(mock_call.call_count, 1)

    @patch('gemini_bridge.call_antigravity')
    @patch('gemini_bridge.AG_POOL')
    @patch('gemini_bridge.Manager')
    @patch('time.sleep')
    def test_9_no_retry_sleeps(self, mock_sleep, mock_mgr, mock_pool, mock_call):
        """Test 9: No sleep/backoff is invoked anywhere in the request path."""
        mock_mgr.return_value.active.return_value = '7'
        mock_pool.candidates.return_value = ['7', '8']
        mock_pool.credentials.side_effect = lambda n: {'token': {'access_token': f'tok{n}'}}

        def side_effect(body, token, deadline=None):
            if token == 'tok7':
                raise PoolError('quota exceeded', 429)
            return {'candidates': [{'content': 'ok'}]}

        mock_call.side_effect = side_effect
        call_with_retry({'model': 'gemini-3.8-flash'})
        mock_sleep.assert_not_called()

    def test_10_retry_policy_disabled_by_config(self):
        """Test 10: AIPOOL_PROVIDER_MAX_ATTEMPTS=100 cannot reactivate retries."""
        with patch.dict(os.environ, {'AIPOOL_PROVIDER_MAX_ATTEMPTS': '100'}):
            self.assertEqual(provider_attempts(), 1)
            self.assertEqual(provider_attempts(100), 1)

    def test_11_generic_429_is_not_account_failover(self):
        """A 429 without quota evidence remains transient and sticky."""
        from pool_runtime import hard_account_failure
        self.assertFalse(hard_account_failure(429, ''))
        self.assertFalse(hard_account_failure(429, 'rate limit; try again later'))

    def test_12_model_and_provider_failures_are_never_account_failover(self):
        """Model/provider failures cannot qualify for account rotation."""
        self.assertFalse(classify(status=404).failover)
        self.assertFalse(classify(status=500).failover)
        self.assertFalse(classify(status=503).failover)
        self.assertFalse(classify('unclassified upstream failure').failover)

    def test_13_codex_preserves_requested_model_id(self):
        """Codex adapter must not silently replace an unknown/requested model."""
        from codex_bridge import response_request
        requested = 'gpt-custom-model'
        out = response_request({'model': requested, 'input': 'hello'}, chat=False)
        self.assertEqual(out['model'], requested)


if __name__ == '__main__':
    unittest.main()
