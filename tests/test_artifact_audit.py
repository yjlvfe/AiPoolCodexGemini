import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
import request_audit
from artifact_store import get

class Handler:
    headers = {'User-Agent': 'test'}
    client_address = ('127.0.0.1', 1)
    path = '/v1/chat/completions'

class ArtifactAuditTests(unittest.TestCase):
    def test_large_tool_output_gets_retrievable_reference(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(__import__('os').environ, {'AIPOOL_ARTIFACT_DIR': d}):
            value = 'x' * 5000
            audit = request_audit.capture(Handler(), {'messages': [{'role': 'tool', 'content': value}]})
            self.assertEqual(len(audit['artifact_refs']), 1)
            self.assertEqual(get(audit['artifact_refs'][0]['ref']), value)

if __name__ == '__main__':
    unittest.main()
