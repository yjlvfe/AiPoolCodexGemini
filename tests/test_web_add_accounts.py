import base64
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'cli'))
sys.path.insert(0, str(ROOT / 'dashboard'))

from dashboard.app import PoolManager
import dashboard.server as server


def make_jwt(payload):
    part = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f'header.{part}.signature'


class MockHTTPResponse:
    def __init__(self, data, status=200):
        self._data = json.dumps(data).encode('utf-8') if isinstance(data, (dict, list)) else data
        self.status = status

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class WebAddAccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.codex_store = self.root_path / 'codex-accounts'
        self.codex_store.mkdir(parents=True, exist_ok=True)
        self.ag_store = self.root_path / 'ag-accounts'
        self.ag_store.mkdir(parents=True, exist_ok=True)

        self.env_patcher = patch.dict(os.environ, {
            'CODEX_ACCOUNT_STORE': str(self.codex_store),
            'AG_ACCOUNT_STORE': str(self.ag_store),
            'AIPOOL_DISABLE_BACKGROUND_POLLER': '1',
            'AIPOOL_POOL_SNAPSHOT': str(self.root_path / 'snapshot.json'),
        })
        self.env_patcher.start()

        self.pm = PoolManager.__new__(PoolManager)
        self.pm._oauth_sessions = {}
        self.pm._oauth_sessions_lock = __import__('threading').Lock()
        self.pm._refresh_lock = __import__('threading').Lock()
        self.pm._refresh_done = __import__('threading').Event()
        self.pm._refresh_inflight = False
        self.pm._refresh_state = 'idle'
        self.pm._refresh_error = None
        self.pm._refresh_started_at = None
        self.pm._refresh_completed_at = None
        self.pm._lock = __import__('threading').Lock()
        self.pm.trigger_instant_refresh = MagicMock()

    def tearDown(self):
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def test_start_oauth_codex_device_code(self):
        mock_resp = {
            'user_code': 'ABCD-EFGH',
            'device_auth_id': 'dev_auth_123',
            'interval': 5,
            'expires_in': 900,
            'verification_uri': 'https://auth.openai.com/codex/device',
        }
        with patch('urllib.request.urlopen', return_value=MockHTTPResponse(mock_resp)):
            res = self.pm.start_oauth('codex', 'device_code')

        self.assertTrue(res['success'])
        self.assertEqual(res['flow'], 'device_code')
        self.assertEqual(res['user_code'], 'ABCD-EFGH')
        self.assertEqual(res['device_auth_id'], 'dev_auth_123')
        self.assertEqual(res['verification_uri'], 'https://auth.openai.com/codex/device')
        self.assertEqual(res['interval'], 5)
        self.assertIn(res['session_id'], self.pm._oauth_sessions)

    def test_start_oauth_codex_auth_url(self):
        res = self.pm.start_oauth('codex', 'auth_url')
        self.assertTrue(res['success'])
        self.assertEqual(res['flow'], 'auth_url')
        self.assertIn('https://auth.openai.com/oauth/authorize?', res['auth_url'])

        parsed = urllib.parse.urlparse(res['auth_url'])
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs['client_id'][0], 'app_EMoamEEZ73f0CkXaXp7hrann')
        self.assertEqual(qs['redirect_uri'][0], 'http://localhost:1455/auth/callback')
        self.assertEqual(qs['code_challenge_method'][0], 'S256')
        self.assertIn('offline_access', qs['scope'][0])
        self.assertEqual(qs['state'][0], res['state'])

    def test_start_oauth_gemini_auth_url(self):
        res = self.pm.start_oauth('gemini', 'auth_url')
        self.assertTrue(res['success'])
        self.assertEqual(res['flow'], 'auth_url')
        self.assertIn('https://accounts.google.com/o/oauth2/v2/auth?', res['auth_url'])

        parsed = urllib.parse.urlparse(res['auth_url'])
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs['client_id'][0], '1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com')
        self.assertEqual(qs['redirect_uri'][0], 'http://localhost:8085/oauth2callback')
        self.assertEqual(qs['code_challenge_method'][0], 'S256')
        self.assertIn('https://www.googleapis.com/auth/cloud-platform', qs['scope'][0])
        self.assertEqual(qs['state'][0], res['state'])

    def test_start_oauth_invalid_inputs(self):
        res = self.pm.start_oauth('unknown_prov', 'auth_url')
        self.assertFalse(res['success'])
        self.assertIn('Unsupported provider', res['message'])

        res = self.pm.start_oauth('gemini', 'device_code')
        self.assertFalse(res['success'])
        self.assertIn('Gemini only supports auth_url', res['message'])

        res = self.pm.start_oauth('codex', 'invalid_flow')
        self.assertFalse(res['success'])
        self.assertIn('device_code or auth_url', res['message'])

    def test_poll_oauth_pending_then_completed_codex_device_code(self):
        # 1. Start device code session
        start_resp = {
            'user_code': 'WXYZ-1234',
            'device_auth_id': 'dev_auth_999',
            'interval': 5,
            'expires_in': 900,
        }
        with patch('urllib.request.urlopen', return_value=MockHTTPResponse(start_resp)):
            init_res = self.pm.start_oauth('codex', 'device_code')
        session_id = init_res['session_id']

        # 2. Poll while pending (HTTP 400 error from OpenAI with authorization_pending)
        err_body = json.dumps({'error': {'code': 'authorization_pending', 'message': 'Device auth is pending'}}).encode()
        http_pending_err = urllib.error.HTTPError(
            'https://auth.openai.com/api/accounts/deviceauth/token', 400, 'Pending', {}, io.BytesIO(err_body)
        )
        with patch('urllib.request.urlopen', side_effect=http_pending_err):
            poll_res = self.pm.poll_oauth(session_id)
        self.assertEqual(poll_res['status'], 'pending')

        # 3. Poll when complete
        auth_code_resp = {
            'authorization_code': 'open_ai_auth_code_789',
            'code_verifier': 'verifier_789',
        }
        id_token_jwt = make_jwt({
            'https://api.openai.com/auth': {'chatgpt_account_id': 'chatgpt_acc_999'},
            'email': 'developer@codex.ai',
        })
        token_exchange_resp = {
            'access_token': 'access_token_999',
            'refresh_token': 'refresh_token_999',
            'id_token': id_token_jwt,
        }

        def fake_urlopen(req, timeout=15):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if 'deviceauth/token' in url:
                return MockHTTPResponse(auth_code_resp)
            if 'oauth/token' in url:
                return MockHTTPResponse(token_exchange_resp)
            raise AssertionError(f'Unexpected URL in test: {url}')

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            complete_res = self.pm.poll_oauth(session_id)

        self.assertEqual(complete_res['status'], 'completed')
        self.assertEqual(complete_res['account'], 1)
        self.assertEqual(complete_res['email'], 'developer@codex.ai')

        # Verify disk persistence for slot 1
        slot_dir = self.codex_store / '1'
        self.assertTrue(slot_dir.is_dir())
        auth_file = slot_dir / 'auth.json'
        self.assertTrue(auth_file.is_file())
        saved_auth = json.loads(auth_file.read_text())
        self.assertEqual(saved_auth['tokens']['access_token'], 'access_token_999')
        self.assertEqual(saved_auth['tokens']['refresh_token'], 'refresh_token_999')
        self.assertEqual(saved_auth['email'], 'developer@codex.ai')
        self.assertEqual((self.codex_store / 'active').read_text().strip(), '1')

        # 4. Subsequent poll returns completed status from memory cache
        cached_res = self.pm.poll_oauth(session_id)
        self.assertEqual(cached_res['status'], 'completed')
        self.assertEqual(cached_res['account'], 1)

    def test_callback_oauth_codex_with_url_or_code(self):
        # 1. Start Codex auth_url session
        start_res = self.pm.start_oauth('codex', 'auth_url')
        session_id = start_res['session_id']
        state = start_res['state']

        id_token_jwt = make_jwt({
            'https://api.openai.com/auth': {'chatgpt_account_id': 'chatgpt_acc_111'},
            'email': 'linkuser@codex.ai',
        })
        token_exchange_resp = {
            'access_token': 'link_access_token_111',
            'refresh_token': 'link_refresh_token_111',
            'id_token': id_token_jwt,
        }

        # Test pasting full redirect URL
        redirect_url = f'http://localhost:1455/auth/callback?code=codex_code_456&state={state}'

        with patch('urllib.request.urlopen', return_value=MockHTTPResponse(token_exchange_resp)):
            cb_res = self.pm.callback_oauth(session_id, redirect_url)

        self.assertTrue(cb_res['success'])
        self.assertEqual(cb_res['status'], 'completed')
        self.assertEqual(cb_res['account'], 1)
        self.assertEqual(cb_res['email'], 'linkuser@codex.ai')

        # Verify disk persistence for slot 1
        slot_dir = self.codex_store / '1'
        self.assertTrue(slot_dir.is_dir())
        auth_file = slot_dir / 'auth.json'
        self.assertTrue(auth_file.is_file())
        saved_auth = json.loads(auth_file.read_text())
        self.assertEqual(saved_auth['email'], 'linkuser@codex.ai')

    def test_callback_oauth_gemini_antigravity(self):
        # 1. Start Gemini auth_url session
        start_res = self.pm.start_oauth('gemini', 'auth_url')
        session_id = start_res['session_id']
        state = start_res['state']

        google_token_resp = {
            'access_token': 'google_access_token_777',
            'refresh_token': 'google_refresh_token_777',
            'expires_in': 3600,
            'token_type': 'Bearer',
        }
        google_userinfo_resp = {
            'email': 'gemini_dev@gmail.com',
            'verified_email': True,
        }

        def fake_urlopen(req, timeout=15):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if 'oauth2.googleapis.com/token' in url:
                return MockHTTPResponse(google_token_resp)
            if 'googleapis.com/oauth2/v1/userinfo' in url:
                return MockHTTPResponse(google_userinfo_resp)
            raise AssertionError(f'Unexpected URL in test: {url}')

        redirect_url = f'http://localhost:8085/oauth2callback?code=4/0Aean999&state={state}'

        with patch('urllib.request.urlopen', side_effect=fake_urlopen), \
             patch('ag_provider.project_id', return_value='my-gemini-cloud-project'):
            cb_res = self.pm.callback_oauth(session_id, redirect_url)

        self.assertTrue(cb_res['success'])
        self.assertEqual(cb_res['status'], 'completed')
        self.assertEqual(cb_res['account'], 1)
        self.assertEqual(cb_res['email'], 'gemini_dev@gmail.com')
        self.assertEqual(cb_res['project_id'], 'my-gemini-cloud-project')

        # Verify disk persistence in AG_ACCOUNT_STORE
        slot_dir = self.ag_store / '1'
        self.assertTrue(slot_dir.is_dir())
        token_file = slot_dir / 'antigravity-oauth-token'
        self.assertTrue(token_file.is_file())
        saved_data = json.loads(token_file.read_text())
        self.assertEqual(saved_data['email'], 'gemini_dev@gmail.com')
        self.assertEqual(saved_data['project_id'], 'my-gemini-cloud-project')
        self.assertEqual(saved_data['token']['refresh_token'], 'google_refresh_token_777')
        self.assertEqual((self.ag_store / 'active').read_text().strip(), '1')

    def test_callback_state_mismatch_rejected(self):
        start_res = self.pm.start_oauth('gemini', 'auth_url')
        session_id = start_res['session_id']
        wrong_url = 'http://localhost:8085/oauth2callback?code=4/0Aean999&state=WRONG_STATE'
        res = self.pm.callback_oauth(session_id, wrong_url)
        self.assertFalse(res['success'])
        self.assertIn('state mismatch', res['message'])

    def test_cancel_oauth_cleans_up_session(self):
        start_res = self.pm.start_oauth('codex', 'auth_url')
        session_id = start_res['session_id']
        self.assertIn(session_id, self.pm._oauth_sessions)

        cancel_res = self.pm.cancel_oauth(session_id)
        self.assertTrue(cancel_res['success'])
        self.assertNotIn(session_id, self.pm._oauth_sessions)

        poll_res = self.pm.poll_oauth(session_id)
        self.assertEqual(poll_res['status'], 'failed')

    def test_http_endpoints_via_server_handler(self):
        # Patch GLOBAL_POOL_MANAGER
        with patch.object(server, 'GLOBAL_POOL_MANAGER', self.pm):
            # Test POST /api/accounts/oauth/start
            def make_post_handler(path, payload):
                body = json.dumps(payload).encode()
                handler = server.ProDashboardHandler.__new__(server.ProDashboardHandler)
                handler.path = path
                handler.command = 'POST'
                handler.headers = {'Content-Length': str(len(body)), 'Content-Type': 'application/json'}
                handler.rfile = io.BytesIO(body)
                handler.wfile = io.BytesIO()
                handler.client_address = ('127.0.0.1', 54321)
                handler.status_code = None
                handler.response_headers = {}

                def send_response(code, message=None):
                    handler.status_code = code
                def send_header(name, value):
                    handler.response_headers[name] = value
                def end_headers():
                    pass

                handler.send_response = send_response
                handler.send_header = send_header
                handler.end_headers = end_headers
                return handler

            def make_get_handler(path):
                handler = server.ProDashboardHandler.__new__(server.ProDashboardHandler)
                handler.path = path
                handler.command = 'GET'
                handler.headers = {}
                handler.rfile = io.BytesIO(b'')
                handler.wfile = io.BytesIO()
                handler.client_address = ('127.0.0.1', 54321)
                handler.status_code = None
                handler.response_headers = {}

                def send_response(code, message=None):
                    handler.status_code = code
                def send_header(name, value):
                    handler.response_headers[name] = value
                def end_headers():
                    pass

                handler.send_response = send_response
                handler.send_header = send_header
                handler.end_headers = end_headers
                return handler

            # 1. POST /api/accounts/oauth/start
            handler_start = make_post_handler('/api/accounts/oauth/start', {'provider': 'codex', 'flow': 'auth_url'})
            handler_start.do_POST()
            self.assertEqual(handler_start.status_code, 200)
            res_start = json.loads(handler_start.wfile.getvalue())
            self.assertTrue(res_start['success'])
            session_id = res_start['session_id']

            # 2. GET /api/accounts/oauth/poll
            handler_poll = make_get_handler(f'/api/accounts/oauth/poll?session_id={session_id}')
            handler_poll.do_GET()
            self.assertEqual(handler_poll.status_code, 200)
            res_poll = json.loads(handler_poll.wfile.getvalue())
            self.assertEqual(res_poll['status'], 'pending')

            # 3. POST /api/accounts/oauth/cancel
            handler_cancel = make_post_handler('/api/accounts/oauth/cancel', {'session_id': session_id})
            handler_cancel.do_POST()
            self.assertEqual(handler_cancel.status_code, 200)
            res_cancel = json.loads(handler_cancel.wfile.getvalue())
            self.assertTrue(res_cancel['success'])


if __name__ == '__main__':
    unittest.main()
