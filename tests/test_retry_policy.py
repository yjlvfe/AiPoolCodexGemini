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
)


class RetryPolicyTests(unittest.TestCase):
    def test_provider_attempts_always_returns_one_in_zero_retry(self):
        """In zero-retry architecture, provider_attempts always returns 1."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(provider_attempts(), 1)

    def test_provider_attempts_ignores_large_attempt_requests(self):
        self.assertEqual(provider_attempts(-10), 1)
        self.assertEqual(provider_attempts(0), 1)
        self.assertEqual(provider_attempts(2), 1)
        self.assertEqual(provider_attempts(5), 1)
        self.assertEqual(provider_attempts(100), 1)
        self.assertEqual(MIN_PROVIDER_ATTEMPTS, 1)
        self.assertEqual(DEFAULT_PROVIDER_ATTEMPTS, 1)
        self.assertEqual(MAX_PROVIDER_ATTEMPTS, 1)

    def test_provider_attempts_ignores_environment_override_to_maintain_zero_retry(self):
        with patch.dict(os.environ, {'AIPOOL_PROVIDER_MAX_ATTEMPTS': '7'}):
            self.assertEqual(provider_attempts(), 1)

    def test_retry_delay_is_zero(self):
        self.assertEqual(retry_delay(0), 0.0)
        self.assertEqual(retry_delay(1), 0.0)
        self.assertEqual(retry_delay(10), 0.0)

    def test_wait_before_retry_is_noop(self):
        # Should execute immediately without sleeping
        wait_before_retry(5)


if __name__ == '__main__':
    unittest.main()
