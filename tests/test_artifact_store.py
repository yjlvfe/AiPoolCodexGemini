import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from artifact_store import put, get

class ArtifactStoreTests(unittest.TestCase):
    def test_round_trip_and_integrity(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AIPOOL_ARTIFACT_DIR': d}):
            value = {'tool_output': ['keep', {'id': 42}]}
            ref = put('tool_output', value)
            self.assertTrue(ref['ref'].startswith('artifact:tool_output:'))
            self.assertEqual(get(ref['ref']), value)
            self.assertEqual(get(put('tool_output', value)['ref']), value)

    def test_invalid_reference_rejected(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AIPOOL_ARTIFACT_DIR': d}):
            with self.assertRaises(ValueError):
                get('artifact:tool_output:not-a-hash')

if __name__ == '__main__':
    unittest.main()
