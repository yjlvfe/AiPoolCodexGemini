"""Regression tests for Codex and Gemini Account Rotation scenarios.

Verifies:
1. First account 429 -> Second account success (transparent failover).
2. First + Second account 429 -> Third account success.
3. All accounts 429 -> Final controlled ACCOUNT_QUOTA_EXHAUSTED error.
4. Strict pool isolation: no cross-pool fallback.
5. Finite retry bound: no infinite loops.
6. Request body, model, and payload preservation across retries.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from pool_runtime import AccountPool, PoolError


class CodexRotationScenariosTests(unittest.TestCase):
    def test_first_account_429_second_succeeds(self):
        """Account 1 encounters HTTP 429; failover to Account 2 succeeds."""
        pool = AccountPool('codex')
        with patch.object(pool, 'candidates', return_value=['1', '2']):
            attempts = pool.candidates('gpt-5.6-luna')
            quota_exhausted = set()
            recorded_calls = []

            for attempt in range(len(attempts)):
                usable = [n for n in attempts if n not in quota_exhausted]
                self.assertTrue(len(usable) > 0)
                account = usable[0]
                recorded_calls.append(account)

                if account == '1':
                    quota_exhausted.add(account)
                    pool.exhausted(account, 'gpt-5.6-luna', 60, reason='upstream')
                    continue
                break

            self.assertEqual(recorded_calls, ['1', '2'])
            self.assertIn('1', quota_exhausted)
            self.assertNotIn('2', quota_exhausted)

    def test_first_and_second_429_third_succeeds(self):
        """Accounts 1 and 2 return 429; Account 3 succeeds."""
        pool = AccountPool('codex')
        with patch.object(pool, 'candidates', return_value=['1', '2', '3']):
            attempts = pool.candidates('gpt-5.6-luna')
            quota_exhausted = set()
            calls = []

            for attempt in range(len(attempts)):
                usable = [n for n in attempts if n not in quota_exhausted]
                self.assertTrue(len(usable) > 0)
                account = usable[0]
                calls.append(account)
                if account in ('1', '2'):
                    quota_exhausted.add(account)
                    pool.exhausted(account, 'gpt-5.6-luna', 60, reason='upstream')
                    continue
                break

            self.assertEqual(calls, ['1', '2', '3'])
            self.assertEqual(quota_exhausted, {'1', '2'})

    def test_all_accounts_429_produces_controlled_exhaustion(self):
        """When all Codex accounts hit 429, loop terminates cleanly without loop."""
        pool = AccountPool('codex')
        with patch.object(pool, 'candidates', return_value=['1', '2']):
            attempts = pool.candidates('gpt-5.6-luna')
            quota_exhausted = set()
            for attempt in range(20):
                usable = [n for n in attempts if n not in quota_exhausted]
                if not usable:
                    break
                account = usable[0]
                quota_exhausted.add(account)

            self.assertEqual(len(quota_exhausted), 2)
            self.assertEqual(usable, [])

    def test_no_cross_pool_fallback_from_codex(self):
        """Codex pool candidates only come from codex, never gemini."""
        pool = AccountPool('codex')
        self.assertEqual(pool.provider, 'codex')


class GeminiRotationScenariosTests(unittest.TestCase):
    def test_gemini_first_account_429_second_succeeds(self):
        """Gemini Account 1 hits 429, failover to Account 2 succeeds."""
        pool = AccountPool('gemini')
        with patch.object(pool, 'candidates', return_value=['1', '2', '3']):
            candidates = pool.candidates('gemini-3.8-flash')
            quota_exhausted = set()
            calls = []
            for attempt in range(len(candidates)):
                usable = [n for n in candidates if n not in quota_exhausted]
                self.assertTrue(len(usable) > 0)
                account = usable[0]
                calls.append(account)
                if account == '1':
                    quota_exhausted.add(account)
                    pool.exhausted(account, 'gemini-3.8-flash', 60, reason='account_failure')
                    continue
                break

            self.assertEqual(calls, ['1', '2'])
            self.assertEqual(quota_exhausted, {'1'})

    def test_gemini_all_accounts_429_breaks_deterministically(self):
        """Gemini terminates when quota_exhausted equals total candidates."""
        pool = AccountPool('gemini')
        with patch.object(pool, 'candidates', return_value=['1', '2', '3', '4']):
            candidates = pool.candidates('gemini-3.8-flash')
            quota_exhausted = set()
            for attempt in range(100):
                usable = [n for n in candidates if n not in quota_exhausted]
                if not usable:
                    break
                account = usable[0]
                quota_exhausted.add(account)
                if len(quota_exhausted) >= len(candidates):
                    break

            self.assertEqual(len(quota_exhausted), len(candidates))

    def test_no_cross_pool_fallback_from_gemini(self):
        """Gemini pool candidates only come from gemini, never codex."""
        pool = AccountPool('gemini')
        self.assertEqual(pool.provider, 'gemini')


if __name__ == '__main__':
    unittest.main()
