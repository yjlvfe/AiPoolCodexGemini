import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.account_manager import Manager, decode_claims, gemini_cli_token

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

    def test_gemini_cli_token_formatting(self):
        data = {
            'token': {
                'access_token': 'test-access',
                'refresh_token': 'test-refresh',
                'token_type': 'Bearer',
                'expiry': 1790109222779,
            },
            'email': 'user@example.com',
            'project_id': 'aicode-consumers',
        }
        res = gemini_cli_token(data)
        self.assertEqual(res['auth_method'], 'consumer')
        self.assertEqual(res['project_id'], 'aicode-consumers')
        self.assertEqual(res['token']['access_token'], 'test-access')
        self.assertEqual(res['token']['refresh_token'], 'test-refresh')
        self.assertIn('T', res['token']['expiry'])


if __name__ == '__main__':
    unittest.main()
