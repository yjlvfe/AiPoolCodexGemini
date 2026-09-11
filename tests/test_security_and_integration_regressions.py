import sys
import tempfile
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bridges'))
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))

from artifact_store import get
from app import TokenAuthManager, device_type_for_user_agent, is_link_preview_user_agent
import client_identity
import codex_bridge
import integrations


class SecurityAndIntegrationRegressionTests(unittest.TestCase):
    def test_magic_link_is_single_use(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = TokenAuthManager(str(Path(directory) / 'auth.db'))
            link = auth.generate_magic_link('https://example.test', 7)
            token = link.split('token=', 1)[1]
            first = auth.register_device(token, '127.0.0.1', 'test')
            second = auth.register_device(token, '127.0.0.1', 'test')
            self.assertIsNotNone(first)
            self.assertIsNone(second)

    def test_link_preview_crawlers_cannot_consume_magic_links(self):
        self.assertTrue(is_link_preview_user_agent('TelegramBot (like TwitterBot)'))
        self.assertTrue(is_link_preview_user_agent('facebookexternalhit/1.1'))
        self.assertFalse(is_link_preview_user_agent('Mozilla/5.0 (Linux; Android 14) Chrome/128'))

    def test_device_session_persists_metadata_and_renews(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = TokenAuthManager(str(Path(directory) / 'auth.db'), session_expiry_hours=24 * 30)
            token = auth.generate_magic_link('https://example.test', 7).split('token=', 1)[1]
            session = auth.register_device(token, 'proxied', 'Mozilla/5.0 (Linux; Android 14) Mobile Chrome/128')
            self.assertIsNotNone(session)
            with sqlite3.connect(Path(directory) / 'auth.db') as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute('SELECT * FROM authorized_devices WHERE session_id = ?', (session,)).fetchone()
                self.assertEqual(row['device_type'], 'mobile')
                self.assertGreater(row['expires_at'] - row['created_at'], 29 * 24 * 3600)
            self.assertTrue(auth.validate_device(session, 'proxied'))

    def test_device_type_classification_does_not_use_hardware_identity(self):
        self.assertEqual(device_type_for_user_agent('Mozilla/5.0 (iPad) Safari'), 'tablet')
        self.assertEqual(device_type_for_user_agent('Mozilla/5.0 (Windows NT 10.0) Chrome/128'), 'desktop')
        self.assertEqual(device_type_for_user_agent(''), 'unknown')

    def test_magic_link_session_is_bound_to_redeeming_peer(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = TokenAuthManager(str(Path(directory) / 'auth.db'))
            link = auth.generate_magic_link('https://example.test', 7)
            token = link.split('token=', 1)[1]
            session = auth.register_device(token, '127.0.0.1', 'test')
            self.assertIsNotNone(session)
            self.assertTrue(auth.validate_device(session, '127.0.0.1'))
            self.assertFalse(auth.validate_device(session, '192.0.2.10'))

    def test_artifact_digest_must_be_hex(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            'os.environ', {'AIPOOL_ARTIFACT_DIR': directory}
        ):
            with self.assertRaises(ValueError):
                get('artifact:tool_output:' + ('/' * 64))

    def test_hermes_integration_installs_retry_floor_and_preserves_stronger_value(self):
        merged = integrations.merged('hermes', {'providers': {}, 'agent': {'api_max_retries': 3}})
        self.assertEqual(merged['agent']['api_max_retries'], integrations.HERMES_MIN_API_RETRIES)
        stronger = integrations.merged('hermes', {'providers': {}, 'agent': {'api_max_retries': 40}})
        self.assertEqual(stronger['agent']['api_max_retries'], 40)

    def test_codex_bridge_retries_provider_outage_before_output(self):
        class Upstream(BaseHTTPRequestHandler):
            calls = 0

            def log_message(self, *args):
                pass

            def do_POST(self):
                type(self).calls += 1
                if type(self).calls < 20:
                    self.send_response(503)
                    self.end_headers()
                    return
                events = [
                    {'type': 'response.created'},
                    {'type': 'response.output_text.delta', 'delta': 'ok'},
                    {'type': 'response.completed', 'response': {
                        'id': 'resp-test', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'ok'}]}],
                    }},
                ]
                body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        bridge = ThreadingHTTPServer(('127.0.0.1', 0), codex_bridge.CodexHandler)
        bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
        bridge_thread.start()
        try:
            payload = {'model': 'gpt-5.6-luna', 'input': 'hello', 'stream': False}
            recorded = threading.Event()
            real_record = codex_bridge.record

            def record_and_signal(*args, **kwargs):
                try:
                    return real_record(*args, **kwargs)
                finally:
                    recorded.set()

            with tempfile.TemporaryDirectory() as directory, patch.dict(
                'os.environ', {'AUTH_DB_PATH': str(Path(directory) / 'audit.db')}
            ), patch.object(codex_bridge, 'UPSTREAM_URL', f'http://127.0.0.1:{server.server_port}/v1/responses'), \
                    patch.object(codex_bridge.POOL, 'candidates', return_value=[1]), \
                    patch.object(codex_bridge.POOL, 'credentials', return_value={'tokens': {'access_token': 'test'}}), \
                    patch.object(codex_bridge.POOL, 'exhausted'), \
                    patch.object(codex_bridge.POOL, 'promote'), \
                    patch.object(client_identity, 'authorize', return_value=True), \
                    patch.object(codex_bridge, 'record', side_effect=record_and_signal), \
                    patch.object(codex_bridge.time, 'sleep') as sleeper:
                request = Request(
                    f'http://127.0.0.1:{bridge.server_port}/v1/responses',
                    data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json'},
                )
                with urlopen(request, timeout=5) as response:
                    result = json.load(response)
                self.assertTrue(recorded.wait(2))
            self.assertEqual(Upstream.calls, 20)
            self.assertEqual(result['id'], 'resp-test')
            self.assertEqual(sleeper.call_count, 19)
        finally:
            bridge.shutdown()
            bridge.server_close()
            bridge_thread.join(timeout=2)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
