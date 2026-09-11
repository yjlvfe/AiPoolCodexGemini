import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from error_taxonomy import ErrorCode, classify
from stream_state import StreamLifecycle, StreamState

class FaultInjectionTests(unittest.TestCase):
    def test_provider_faults_classify_without_failover_after_stream(self):
        cases = [
            (401, ErrorCode.ACCOUNT_AUTH_FAILURE, True),
            (429, ErrorCode.ACCOUNT_QUOTA_EXHAUSTED, True),
            (503, ErrorCode.PROVIDER_OUTAGE, True),
            (400, ErrorCode.INVALID_REQUEST, False),
        ]
        for status, code, failover in cases:
            got = classify(status=status)
            self.assertEqual(got.code, code)
            self.assertEqual(got.failover, failover)
        stream = classify(status=503, stream_started=True)
        self.assertEqual(stream.code, ErrorCode.STREAM_INTERRUPTION)
        self.assertFalse(stream.retryable)
        self.assertFalse(stream.failover)

    def test_disconnect_state_is_terminal(self):
        lifecycle = StreamLifecycle()
        lifecycle.begin()
        lifecycle.observe()
        lifecycle.interrupt()
        self.assertEqual(lifecycle.state, StreamState.INTERRUPTED)
        self.assertFalse(lifecycle.can_failover)
        with self.assertRaises(RuntimeError):
            lifecycle.observe()

if __name__ == '__main__':
    unittest.main()
