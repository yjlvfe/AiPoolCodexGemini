import base64
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli'))


class GoogleOAuth(unittest.TestCase):
    def module(self):
        path = ROOT / 'cli/ag_provider.py'
        self.assertTrue(path.is_file(), 'OAuth must be self-contained in the project')
        spec = importlib.util.spec_from_file_location('test_ag_provider', path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_authorization_selects_account_and_checks_state_pkce(self):
        m = self.module()
        from urllib.parse import parse_qs, urlparse
        url, state, verifier = m.authorization('http://localhost:51121/oauth-callback')
        qs = parse_qs(urlparse(url).query)
        self.assertEqual(qs['prompt'], ['consent select_account'])
        self.assertGreaterEqual(len(state), 32)
        self.assertEqual(qs['code_challenge_method'], ['S256'])
        self.assertEqual(qs['code_challenge'][0], base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode())
        self.assertEqual(m.callback_code('http://localhost:51121/oauth-callback?state=' + state + '&code=fixture', state, 'http://localhost:51121/oauth-callback'), 'fixture')
        for bad in ('http://localhost:51121/oauth-callback?state=wrong&code=fixture', 'http://evil.test/oauth-callback?state=' + state + '&code=fixture'):
            with self.assertRaises(ValueError):
                m.callback_code(bad, state, 'http://localhost:51121/oauth-callback')

    def test_flat_and_nested_import_validation_fetches_real_identity(self):
        m = self.module()
        replies = [{'access_token':'fresh-fixture','expires_in':3600}, {'email':'verified@example.test'}, {'cloudaicompanionProject':'project-fixture'}]
        with patch.object(m, 'request', side_effect=replies):
            data, derived, meta = m.validate({'refresh_token':'fixture-refresh'})
        self.assertEqual(data['token']['refresh_token'], 'fixture-refresh')
        self.assertEqual(meta['identity'], 'verified@example.test')
        self.assertEqual(derived['project_id'], 'project-fixture')

    def test_no_refresh_token_rejected_before_network(self):
        m = self.module()
        with patch.object(m, 'request') as req:
            with self.assertRaises(ValueError):
                m.validate({'token': {'access_token':'fixture'}})
            req.assert_not_called()
