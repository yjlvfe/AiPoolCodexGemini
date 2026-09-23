"""Phase D Foundation Parity Unit Tests (D.1 - D.4).

Verifies 9Router behavioral parity in error_taxonomy, retry_policy,
pool_runtime, and model_circuit_breaker.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))

from error_taxonomy import (
    ErrorCode,
    classify,
    classify_http_status,
    classify_payload,
    parse_retry_seconds,
    _FAILOVER,
)
from retry_policy import (
    should_transport_retry,
    transport_retry_delay,
    provider_attempts,
    retry_delay,
)
from pool_runtime import (
    AccountPool,
    clear_cooldown,
    get_earliest_retry_after,
    hard_account_failure,
)
from model_circuit_breaker import (
    ModelCircuitBreaker,
    ModelUnavailableError,
    ProbeLease,
)


class TestPhaseD1ErrorTaxonomy(unittest.TestCase):
    def test_antigravity_409_conflict_is_quota_exhausted(self):
        err = classify(status=409)
        self.assertEqual(err.code, ErrorCode.ACCOUNT_QUOTA_EXHAUSTED)
        self.assertTrue(err.failover)

    def test_text_patterns_for_capacity_and_quota(self):
        patterns = [
            "rate limit",
            "too many requests",
            "quota exceeded",
            "capacity",
            "overloaded",
            "model_at_capacity",
            "selected model is at capacity",
        ]
        for p in patterns:
            err = classify(f"Error: {p}")
            self.assertEqual(err.code, ErrorCode.ACCOUNT_QUOTA_EXHAUSTED, f"Failed for {p}")
            self.assertTrue(err.failover)

    def test_failover_set_includes_temp_rate_limit(self):
        self.assertIn(ErrorCode.TEMP_RATE_LIMIT, _FAILOVER)
        err = classify(status=429)
        self.assertEqual(err.code, ErrorCode.TEMP_RATE_LIMIT)
        self.assertTrue(err.failover)

    def test_parse_retry_seconds_formats(self):
        now = 1700000000.0
        # 1. Retry-After header
        sec = parse_retry_seconds(headers={"Retry-After": "45"})
        self.assertEqual(sec, 45.0)

        # 2. resets_at epoch timestamp
        sec = parse_retry_seconds(payload={"resets_at": now + 65.0}, now=now)
        self.assertAlmostEqual(sec, 65.0, places=1)

        # 3. resets_in_seconds
        sec = parse_retry_seconds(payload={"error": {"resets_in_seconds": 12.5}})
        self.assertEqual(sec, 12.5)

        # 4. Antigravity 'reset after 2h7m23s'
        sec = parse_retry_seconds(text="Your quota will reset after 2h7m23s")
        self.assertEqual(sec, 2 * 3600 + 7 * 60 + 23)

        # 5. Antigravity 'reset after 30s'
        sec = parse_retry_seconds(text="reset after 30s")
        self.assertEqual(sec, 30.0)

        # 6. Antigravity 'reset after 5m 10s'
        sec = parse_retry_seconds(text="reset after 5m 10s")
        self.assertEqual(sec, 310.0)

    def test_classify_http_status_and_payload(self):
        c1 = classify_http_status(status=429, headers={"Retry-After": "15"}, body="rate limit")
        self.assertEqual(c1.code, ErrorCode.ACCOUNT_QUOTA_EXHAUSTED)
        self.assertEqual(c1.retry_after, 15)
        self.assertEqual(c1.retry_seconds, 15.0)
        self.assertTrue(c1.failover)

        c2 = classify_payload(payload={"error": {"message": "Resource exhausted", "resets_in_seconds": 20}})
        self.assertEqual(c2.code, ErrorCode.ACCOUNT_QUOTA_EXHAUSTED)
        self.assertEqual(c2.retry_after, 20)
        self.assertEqual(c2.retry_seconds, 20.0)


class TestPhaseD2RetryPolicy(unittest.TestCase):
    def test_layer1_transport_retry_allowance(self):
        # 502, 503, 504 allow attempt 1 (retry), but not attempt 2 (max 2 attempts)
        for st in (502, 503, 504):
            self.assertTrue(should_transport_retry(st, 1), f"Failed for {st} attempt 1")
            self.assertFalse(should_transport_retry(st, 2), f"Failed for {st} attempt 2")
            self.assertEqual(transport_retry_delay(st, 1), 2.0)

    def test_network_errors_retry(self):
        self.assertTrue(should_transport_retry(ConnectionError(), 1))
        self.assertFalse(should_transport_retry(ConnectionError(), 2))
        self.assertTrue(should_transport_retry(TimeoutError(), 1))
        self.assertEqual(transport_retry_delay(None, 1), 2.0)

    def test_429_quota_zero_transport_retry(self):
        # 429 and 409 must fast-fail to Level 3 account failover
        self.assertFalse(should_transport_retry(429, 1))
        self.assertFalse(should_transport_retry(409, 1))
        self.assertEqual(transport_retry_delay(429, 1), 0.0)


class TestPhaseD3PoolRuntime(unittest.TestCase):
    def test_per_model_and_account_wide_cooldowns(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'auth.db')}):
            pool = AccountPool("codex")
            # 1. Model-specific cooldown
            pool.exhausted("1", "o3", seconds=100, reason="quota_exhausted")
            self.assertTrue(pool.is_cooling_down("1", "o3"))
            self.assertFalse(pool.is_cooling_down("1", "gpt-4o"))

            # 2. Account-wide cooldown "__all__"
            pool.exhausted("2", "o3", seconds=100, reason="invalid_credentials")
            self.assertTrue(pool.is_cooling_down("2", "o3"))
            self.assertTrue(pool.is_cooling_down("2", "gpt-4o"))
            self.assertTrue(pool.is_cooling_down("2", "__all__"))

    def test_exponential_backoff_and_reset(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'auth.db')}):
            pool = AccountPool("codex")
            # Level 1: 2.0s
            t0 = time.time()
            pool.exhausted("1", "m1")
            until1, _ = pool._load_cooldown_record("1", "m1")
            self.assertAlmostEqual(until1 - t0, 2.0, delta=0.5)

            # Level 2: 4.0s
            pool.exhausted("1", "m1")
            until2, _ = pool._load_cooldown_record("1", "m1")
            self.assertAlmostEqual(until2 - t0, 4.0, delta=0.5)

            # Clear cooldown resets backoff
            clear_cooldown("codex", "1", "m1")
            self.assertFalse(pool.is_cooling_down("1", "m1"))
            self.assertEqual(pool._load_backoff_level("1", "m1"), 0)

            # Re-exhaustion starts back at Level 1 (2.0s)
            t1 = time.time()
            pool.exhausted("1", "m1")
            until3, _ = pool._load_cooldown_record("1", "m1")
            self.assertAlmostEqual(until3 - t1, 2.0, delta=0.5)

    def test_abolish_include_cooldown_storm_and_earliest_retry(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AUTH_DB_PATH': str(Path(d) / 'auth.db')}):
            pool = AccountPool("codex")
            mock_mgr = MagicMock()
            mock_mgr.active.return_value = "1"
            mock_mgr.ids.return_value = ["1", "2"]
            mock_mgr.credential.side_effect = lambda n: Path(d) / f"{n}.json"
            mock_mgr.identity.side_effect = lambda data: {"identity": data.get("id")}

            with patch('pool_runtime.Manager', return_value=mock_mgr), \
                 patch('pool_runtime.read_json', side_effect=lambda p: {"id": Path(p).stem}):

                # Account 1 cooling (until +20s), Account 2 healthy
                pool.exhausted("1", "m1", seconds=20)
                self.assertEqual(pool.candidates("m1", include_cooldown=False), ["2"])
                self.assertEqual(pool.candidates("m1", include_cooldown=True), ["1", "2"])

                # Now both accounts cooling
                pool.exhausted("2", "m1", seconds=50)
                # When all accounts are cooling down, candidates MUST return empty []
                self.assertEqual(pool.candidates("m1", include_cooldown=False), [])
                self.assertEqual(pool.candidates("m1", include_cooldown=True), [])

                # get_earliest_retry_after should find min remaining (~20s)
                retry_after = get_earliest_retry_after("codex", "m1")
                self.assertIsNotNone(retry_after)
                self.assertTrue(18 <= retry_after <= 21)


class TestPhaseD4ModelCircuitBreaker(unittest.TestCase):
    def test_deglobalized_per_account_circuit_breaker(self):
        now = [100.0]
        breaker = ModelCircuitBreaker(default_ttl=30, clock=lambda: now[0])

        # Account 1 probe fails
        lease1 = breaker.before_request("gemini", "gemini-2", account_id="acc1")
        self.assertIsInstance(lease1, ProbeLease)
        breaker.record_failure(lease1, "rate_limit")

        # Account 1 is now tripped
        with self.assertRaises(ModelUnavailableError):
            breaker.before_request("gemini", "gemini-2", account_id="acc1")

        # Account 2 is healthy and can proceed
        lease2 = breaker.before_request("gemini", "gemini-2", account_id="acc2")
        self.assertIsInstance(lease2, ProbeLease)
        breaker.record_success(lease2)

        # all_accounts check: acc2 is healthy, so does NOT fast-reject
        lease3 = breaker.before_request("gemini", "gemini-2", all_accounts=["acc1", "acc2"])
        self.assertIsNone(lease3)

        # When ALL candidate accounts are tripped:
        breaker_all = ModelCircuitBreaker(default_ttl=30, clock=lambda: now[0])
        b_l1 = breaker_all.before_request("gemini", "gemini-2", account_id="acc1")
        self.assertIsInstance(b_l1, ProbeLease)
        breaker_all.record_failure(b_l1, "rate_limit")

        b_l2 = breaker_all.before_request("gemini", "gemini-2", account_id="acc2")
        self.assertIsInstance(b_l2, ProbeLease)
        breaker_all.record_failure(b_l2, "rate_limit")

        # Now all candidate accounts are tripped -> fast reject!
        with self.assertRaises(ModelUnavailableError):
            breaker_all.before_request("gemini", "gemini-2", all_accounts=["acc1", "acc2"])


if __name__ == '__main__':
    unittest.main()
