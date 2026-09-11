import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.account_manager import Manager, decode_claims

class LegacyAccountStorageTests(unittest.TestCase):
    def test_malformed_jwt_claims_fail_closed(self):
        self.assertEqual(decode_claims('header.%%%invalid%%%.signature'), {})

    def test_remove_deletes_legacy_numbered_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / 'accounts'
            legacy = store / '1'
            legacy.parent.mkdir()
            legacy.write_text('{"tokens": {}}')
            with patch.dict(os.environ, {'CODEX_ACCOUNT_STORE': str(store)}):
                Manager('codex').remove('1')
            self.assertFalse(legacy.exists())


if __name__ == '__main__':
    unittest.main()
