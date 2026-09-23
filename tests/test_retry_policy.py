import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))

from retry_policy import (
    DEFAULT_PROVIDER_ATTEMPTS,
    MIN_PROVIDER_ATTEMPTS,
    MAX_PROVIDER_ATTEMPTS,
    provider_attempts,
    retry_delay,
    wait_before_retry,
    should_transport_retry,
    transport_retry_delay,
)


class RetryPolicyTests(unittest.TestCase):
    def test_provider_attempts_defaults_to_two(self):
        """In 9Router parity, default attempts on same account is 2 (1 retry)."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(provider_attempts(), 2)

    def test_provider_attempts_bounds(self):
        self.assertEqual(provider_attempts(-10), 1)
        self.assertEqual(provider_attempts(0), 1)
        self.assertEqual(provider_attempts(2), 2)
        self.assertEqual(provider_attempts(4), 4)
        self.assertEqual(provider_attempts(100), 5)
        self.assertEqual(MIN_PROVIDER_ATTEMPTS, 1)
        self.assertEqual(DEFAULT_PROVIDER_ATTEMPTS, 2)
        self.assertEqual(MAX_PROVIDER_ATTEMPTS, 5)

    def test_provider_attempts_environment_override(self):
        with patch.dict(os.environ, {'AIPOOL_PROVIDER_MAX_ATTEMPTS': '3'}):
            self.assertEqual(provider_attempts(), 3)
        with patch.dict(os.environ, {'AIPOOL_PROVIDER_MAX_ATTEMPTS': '100'}):
            self.assertEqual(provider_attempts(), 5)

    def test_transport_retry_for_transient_errors(self):
        # 502, 503, 504 allow up to 2 attempts (attempt 1 retries, attempt 2 stops)
        self.assertTrue(should_transport_retry(502, 1))
        self.assertFalse(should_transport_retry(502, 2))
        self.assertTrue(should_transport_retry(503, 1))
        self.assertFalse(should_transport_retry(503, 2))
        self.assertTrue(should_transport_retry(504, 1))
        self.assertFalse(should_transport_retry(504, 2))

    def test_transport_retry_for_network_errors(self):
        self.assertTrue(should_transport_retry(ConnectionError("reset"), 1))
        self.assertTrue(should_transport_retry(TimeoutError("timeout"), 1))
        self.assertTrue(should_transport_retry(0, 1))
        self.assertTrue(should_transport_retry(None, 1))
        self.assertFalse(should_transport_retry(ConnectionError("reset"), 2))

    def test_transport_retry_fast_fails_for_quota_and_429(self):
        # 429 and 409 must have 0 transport retries to fail fast to account failover
        self.assertFalse(should_transport_retry(429, 1))
        self.assertFalse(should_transport_retry(409, 1))
        self.assertFalse(should_transport_retry(401, 1))
        self.assertFalse(should_transport_retry(403, 1))
        self.assertFalse(should_transport_retry(400, 1))

    def test_transport_retry_delay(self):
        self.assertEqual(transport_retry_delay(502, 1), 2.0)
        self.assertEqual(transport_retry_delay(503, 1), 2.0)
        self.assertEqual(transport_retry_delay(504, 1), 2.0)
        self.assertEqual(transport_retry_delay(429, 1), 0.0)

    def test_retry_delay(self):
        self.assertEqual(retry_delay(1), 2.0)
        self.assertEqual(retry_delay(1, retry_after=5.5), 5.5)


if __name__ == '__main__':
    unittest.main()
