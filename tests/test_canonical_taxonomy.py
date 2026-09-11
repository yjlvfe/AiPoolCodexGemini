import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from canonical_request import CanonicalRequest, wire_payload_bytes
from error_taxonomy import ErrorCode, classify

class CanonicalTests(unittest.TestCase):
    def test_unknown_fields_and_metadata_survive(self):
        p = {'model':'gpt-5.6-luna','messages':[{'role':'user','content':'x','name':'n'}],
             'stream':False,'provider_extension':{'x':1}}
        out = CanonicalRequest.from_payload(p).to_payload()
        self.assertEqual(out, p)

    def test_wire_measurement_is_bytes(self):
        self.assertEqual(wire_payload_bytes({'x':'é'}), len('{"x":"é"}'.encode()))

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

if __name__ == '__main__':
    unittest.main()
