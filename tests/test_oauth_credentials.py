import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from oauth_credentials import oauth_credentials


class OAuthCredentialsTests(unittest.TestCase):
    def test_environment_wins(self):
        with patch.dict(os.environ, {'AG_OAUTH_CLIENT_ID': 'env-id',
                                     'AG_OAUTH_CLIENT_SECRET': 'env-secret',
                                     'AIPOOL_OAUTH_CLIENT_FILE': '/nonexistent/file.json'}):
            client, secret = oauth_credentials()
            self.assertEqual((client, secret), ('env-id', 'env-secret'))

    def test_private_file_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'client.json'
            path.write_text(json.dumps({'client_id': 'file-id', 'client_secret': 'file-secret'}))
            path.chmod(0o600)
            with patch.dict(os.environ, {'AG_OAUTH_CLIENT_ID': '', 'AG_OAUTH_CLIENT_SECRET': '',
                                         'AIPOOL_OAUTH_CLIENT_FILE': str(path)}):
                client, secret = oauth_credentials()
                self.assertEqual((client, secret), ('file-id', 'file-secret'))

    def test_world_readable_private_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'client.json'
            path.write_text(json.dumps({'client_id': 'file-id', 'client_secret': 'file-secret'}))
            path.chmod(0o644)
            with patch.dict(os.environ, {'AG_OAUTH_CLIENT_ID': '', 'AG_OAUTH_CLIENT_SECRET': '',
                                         'AIPOOL_OAUTH_CLIENT_FILE': str(path)}):
                with self.assertRaisesRegex(RuntimeError, 'mode 0600'):
                    oauth_credentials()

    def test_missing_credentials_raise(self):
        with patch.dict(os.environ, {'AG_OAUTH_CLIENT_ID': '', 'AG_OAUTH_CLIENT_SECRET': '',
                                     'AIPOOL_OAUTH_CLIENT_FILE': '/nonexistent/client.json'}):
            with self.assertRaises(RuntimeError):
                oauth_credentials()

    def test_private_file_never_committed(self):
        # Regression: the credentials file must not be tracked by git.
        import subprocess
        root = Path(__file__).resolve().parents[1]
        tracked = subprocess.run(
            ['git', '-C', str(root), 'ls-files'], capture_output=True, text=True).stdout
        self.assertNotIn('bridges/oauth-client.private.json', tracked)


if __name__ == '__main__':
    unittest.main()
