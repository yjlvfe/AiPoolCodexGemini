import logging
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridges"))
from model_circuit_breaker import (
    ModelCircuitBreaker,
    ModelUnavailableError,
    ProbeLease,
)


class ModelCircuitBreakerTests(unittest.TestCase):
    def test_failure_opens_and_fast_rejects_same_model(self):
        now = [100.0]
        breaker = ModelCircuitBreaker(default_ttl=30, clock=lambda: now[0])
        lease = breaker.before_request("gemini", "gemini-2")
        self.assertIsInstance(lease, ProbeLease)
        state = breaker.record_failure(lease, "provider_404")
        self.assertEqual((state.provider, state.model, state.until, state.reason),
                         ("gemini", "gemini-2", 130.0, "provider_404"))
        with self.assertRaises(ModelUnavailableError) as caught:
            breaker.before_request("gemini", "gemini-2")
        self.assertEqual(caught.exception.code, "MODEL_UNAVAILABLE")
        self.assertEqual(caught.exception.until, 130.0)

    def test_retry_after_overrides_default_ttl(self):
        breaker = ModelCircuitBreaker(default_ttl=30, clock=lambda: 100.0)
        lease = breaker.before_request("codex", "o3")
        state = breaker.record_failure(lease, "rate_limit", retry_after=90)
        self.assertEqual(state.until, 190.0)

    def test_expiry_allows_exactly_one_probe(self):
        now = [100.0]
        breaker = ModelCircuitBreaker(default_ttl=10, clock=lambda: now[0])
        breaker.record_failure(breaker.before_request("gemini", "m1"), "down")
        now[0] = 110.0
        first = breaker.before_request("gemini", "m1")
        self.assertIsInstance(first, ProbeLease)
        with self.assertRaises(ModelUnavailableError):
            breaker.before_request("gemini", "m1")
        breaker.record_success(first)
        self.assertIsNone(breaker.before_request("gemini", "m1"))

    def test_failed_probe_reopens_and_success_closes(self):
        now = [0.0]
        breaker = ModelCircuitBreaker(default_ttl=5, clock=lambda: now[0])
        first = breaker.before_request("codex", "a")
        breaker.record_failure(first, "down")
        now[0] = 5.0
        probe = breaker.before_request("codex", "a")
        state = breaker.record_failure(probe, "still_down", retry_after=20)
        self.assertEqual(state.until, 25.0)
        now[0] = 25.0
        probe = breaker.before_request("codex", "a")
        breaker.record_success(probe)
        self.assertIsNone(breaker.before_request("codex", "a"))

    def test_provider_and_model_keys_are_isolated(self):
        breaker = ModelCircuitBreaker(default_ttl=30, clock=lambda: 100.0)
        breaker.record_failure(breaker.before_request("gemini", "m1"), "down")
        gemini_other = breaker.before_request("gemini", "m2")
        codex_other = breaker.before_request("codex", "m1")
        breaker.record_success(gemini_other)
        breaker.record_success(codex_other)

    def test_stale_lease_cannot_change_new_probe(self):
        now = [0.0]
        breaker = ModelCircuitBreaker(default_ttl=1, clock=lambda: now[0])
        lease = breaker.before_request("gemini", "m")
        breaker.record_failure(lease, "one")
        now[0] = 1
        probe = breaker.before_request("gemini", "m")
        with self.assertRaises(ValueError):
            breaker.record_success(lease)
        breaker.record_success(probe)

    def test_logs_required_events(self):
        logger = logging.getLogger("breaker-test")
        with self.assertLogs(logger, level="INFO") as logs:
            breaker = ModelCircuitBreaker(default_ttl=1, logger=logger, clock=lambda: 0.0)
            lease = breaker.before_request("gemini", "m")
            breaker.record_failure(lease, "down")
            with self.assertRaises(ModelUnavailableError):
                breaker.before_request("gemini", "m")
        text = "\n".join(logs.output)
        self.assertIn("MODEL_BREAKER_PROBE", text)
        self.assertIn("MODEL_BREAKER_OPEN", text)
        self.assertIn("MODEL_BREAKER_FAST_REJECT", text)


if __name__ == "__main__":
    unittest.main()
