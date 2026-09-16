import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))

from retry_policy import DEFAULT_PROVIDER_ATTEMPTS, MIN_PROVIDER_ATTEMPTS, provider_attempts, retry_delay


class RetryPolicyTests(unittest.TestCase):
    def test_provider_attempts_uses_reasonable_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(provider_attempts(), 3)

    def test_provider_attempts_clamps_to_one_through_one_hundred(self):
        self.assertEqual(provider_attempts(-10), 1)
        self.assertEqual(provider_attempts(0), 1)
        self.assertEqual(provider_attempts(2), 2)
        self.assertEqual(provider_attempts(5), 5)
        self.assertEqual(provider_attempts(101), 100)
        self.assertEqual(MIN_PROVIDER_ATTEMPTS, 1)
        self.assertEqual(DEFAULT_PROVIDER_ATTEMPTS, 3)

    def test_provider_attempts_honors_environment_override(self):
        with patch.dict(os.environ, {'AIPOOL_PROVIDER_MAX_ATTEMPTS': '7'}):
            self.assertEqual(provider_attempts(), 7)

    def test_invalid_attempts_fall_back_to_default(self):
        self.assertEqual(provider_attempts('not-a-number'), 3)
        with patch.dict(os.environ, {'AIPOOL_PROVIDER_MAX_ATTEMPTS': 'not-a-number'}):
            self.assertEqual(provider_attempts(), 3)

    def test_retry_delay_stays_bounded_for_large_attempts(self):
        with patch.dict(os.environ, {'AIPOOL_RETRY_BASE_DELAY': '1', 'AIPOOL_RETRY_MAX_DELAY': '5'}):
            self.assertEqual(retry_delay(10), 5.0)
            self.assertEqual(retry_delay(10**100), 5.0)
            self.assertEqual(retry_delay(-1), 1.0)


if __name__ == '__main__':
    unittest.main()
