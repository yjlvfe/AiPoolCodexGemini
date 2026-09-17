import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from canonical_request import CanonicalRequest, wire_payload_bytes
from error_taxonomy import ErrorCode, classify, public_error


class CanonicalTests(unittest.TestCase):
    def test_unknown_fields_and_metadata_survive(self):
        p = {
            'model': 'gpt-5.6-luna',
            'messages': [{'role': 'user', 'content': 'x', 'name': 'n'}],
            'stream': False,
            'provider_extension': {'x': 1},
        }
        out = CanonicalRequest.from_payload(p).to_payload()
        self.assertEqual(out, p)

    def test_wire_measurement_is_bytes(self):
        self.assertEqual(wire_payload_bytes({'x': 'é'}), len('{"x":"é"}'.encode()))

    def test_stream_and_tools_types_are_strict(self):
        with self.assertRaises(ValueError):
            CanonicalRequest.from_payload({'model': 'gpt-test', 'stream': 'false'})
        with self.assertRaises(ValueError):
            CanonicalRequest.from_payload({'model': 'gpt-test', 'tools': {'type': 'function'}})

    def test_taxonomy_no_failover_after_output(self):
        e = classify(status=429, stream_started=True)
        self.assertEqual(e.code, ErrorCode.STREAM_INTERRUPTION)
        self.assertFalse(e.retryable)
        self.assertFalse(e.failover)

    def test_taxonomy_invalid_request_never_failover(self):
        e = classify(status=400)
        self.assertEqual(e.code, ErrorCode.INVALID_REQUEST)
        self.assertFalse(e.failover)

    def test_taxonomy_zero_retry_classifications(self):
        # 1. 5xx is transient provider error
        e500 = classify(status=500)
        self.assertEqual(e500.code, ErrorCode.TRANSIENT_PROVIDER_ERROR)
        self.assertTrue(e500.retryable)
        self.assertFalse(e500.failover)

        # 2. Transient 429 without hard quota details
        e429 = classify(status=429)
        self.assertEqual(e429.code, ErrorCode.TEMP_RATE_LIMIT)
        self.assertTrue(e429.retryable)
        self.assertFalse(e429.failover)

        # 3. Hard quota 429
        e_quota = classify("quota exceeded", status=429)
        self.assertEqual(e_quota.code, ErrorCode.ACCOUNT_QUOTA_EXHAUSTED)
        self.assertTrue(e_quota.failover)

        # 4. Auth failure
        e401 = classify(status=401)
        self.assertEqual(e401.code, ErrorCode.ACCOUNT_AUTH_FAILURE)
        self.assertTrue(e401.failover)

        # 5. All accounts exhausted
        e_all = classify("all accounts are exhausted", status=503)
        self.assertEqual(e_all.code, ErrorCode.ALL_ACCOUNTS_EXHAUSTED)
        self.assertFalse(e_all.retryable)
        self.assertFalse(e_all.failover)


if __name__ == '__main__':
    unittest.main()
