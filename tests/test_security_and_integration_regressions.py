import sys
import tempfile
import json
import hashlib
import sqlite3
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bridges'))
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))

from artifact_store import get
from app import TokenAuthManager, device_type_for_user_agent, is_link_preview_user_agent, normalize_client_ip
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

    def test_magic_and_session_secrets_are_hashed_at_rest_with_legacy_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'auth.db'
            auth = TokenAuthManager(str(db))
            token = auth.generate_magic_link('https://example.test', 7).split('token=', 1)[1]
            with sqlite3.connect(db) as conn:
                stored_token = conn.execute('SELECT token FROM magic_tokens').fetchone()[0]
            self.assertNotEqual(stored_token, token)
            self.assertEqual(stored_token, hashlib.sha256(token.encode()).hexdigest())

            session = auth.register_device(token, 'proxied', 'Mobile Safari')
            with sqlite3.connect(db) as conn:
                stored_session = conn.execute('SELECT session_id FROM authorized_devices').fetchone()[0]
            self.assertEqual(stored_session, hashlib.sha256(session.encode()).hexdigest())
            self.assertTrue(auth.validate_device(session, 'proxied'))

    def test_live_legacy_rows_migrate_without_reactivating_dead_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'auth.db'
            TokenAuthManager(str(db))
            now = time.time()
            with sqlite3.connect(db) as conn:
                conn.executemany(
                    'INSERT INTO magic_tokens(token,user_id,created_at,used) VALUES(?,?,?,?)',
                    [
                        ('legacy-live', 7, now, 0),
                        ('legacy-expired', 7, now - 1801, 0),
                        ('legacy-used', 7, now, 1),
                    ],
                )
                conn.executemany(
                    'INSERT INTO authorized_devices(session_id,user_id,client_ip,user_agent,device_type,created_at,last_seen,expires_at) VALUES(?,?,?,?,?,?,?,?)',
                    [
                        ('legacy-session', 7, '203.0.113.9', 'test', 'desktop', now, now, now + 3600),
                        ('legacy-expired-session', 7, '203.0.113.9', 'test', 'desktop', now - 3601, now - 3601, now - 1),
                    ],
                )
            auth = TokenAuthManager(str(db))
            with sqlite3.connect(db) as conn:
                tokens = {row[0] for row in conn.execute('SELECT token FROM magic_tokens')}
                sessions = {row[0] for row in conn.execute('SELECT session_id FROM authorized_devices')}
            self.assertIn(hashlib.sha256(b'legacy-live').hexdigest(), tokens)
            self.assertNotIn('legacy-live', tokens)
            self.assertNotIn('legacy-expired', tokens)
            self.assertNotIn('legacy-used', tokens)
            self.assertIn(hashlib.sha256(b'legacy-session').hexdigest(), sessions)
            self.assertNotIn('legacy-session', sessions)
            self.assertNotIn('legacy-expired-session', sessions)
            self.assertIsNotNone(auth.register_device('legacy-live', '203.0.113.9', 'test'))
            self.assertIsNone(auth.register_device('legacy-expired', '203.0.113.9', 'test'))
            self.assertIsNone(auth.register_device('legacy-used', '203.0.113.9', 'test'))
            self.assertTrue(auth.validate_device('legacy-session', '203.0.113.9'))

    def test_pre_hash_hex_session_is_revoked_once_when_storage_is_upgraded(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'auth.db'
            TokenAuthManager(str(db))
            now = time.time()
            with sqlite3.connect(db) as conn:
                conn.execute('DROP TABLE IF EXISTS auth_storage_migrations')
                conn.execute(
                    'INSERT INTO authorized_devices(session_id,user_id,client_ip,user_agent,device_type,created_at,last_seen,expires_at) VALUES(?,?,?,?,?,?,?,?)',
                    ('a' * 64, 7, '203.0.113.9', 'test', 'desktop', now, now, now + 3600),
                )
            TokenAuthManager(str(db))
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM authorized_devices').fetchone()[0], 0)

    def test_client_ip_normalization_trusts_nginx_headers_not_spoofed_remote_headers(self):
        self.assertEqual(
            normalize_client_ip({'X-Real-IP': '203.0.113.9', 'X-Forwarded-For': '198.51.100.7'}, '127.0.0.1'),
            '203.0.113.9',
        )
        self.assertEqual(
            normalize_client_ip({'X-Forwarded-For': '198.51.100.7, 203.0.113.9'}, '127.0.0.1'),
            '203.0.113.9',
        )
        self.assertEqual(
            normalize_client_ip({'X-Real-IP': '198.51.100.7'}, '192.0.2.44'),
            '192.0.2.44',
        )

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
                stored_session = hashlib.sha256(session.encode()).hexdigest()
                row = conn.execute('SELECT * FROM authorized_devices WHERE session_id = ?', (stored_session,)).fetchone()
                self.assertEqual(row['device_type'], 'mobile')
                self.assertGreater(row['expires_at'] - row['created_at'], 29 * 24 * 3600)
            self.assertTrue(auth.validate_device(session, 'proxied'))

    def test_device_type_classification_does_not_use_hardware_identity(self):
        self.assertEqual(device_type_for_user_agent('Mozilla/5.0 (iPad) Safari'), 'tablet')
        self.assertEqual(device_type_for_user_agent('Mozilla/5.0 (Windows NT 10.0) Chrome/128'), 'desktop')
        self.assertEqual(device_type_for_user_agent(''), 'unknown')

    def test_magic_link_session_roams_across_client_ips_seamlessly(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = TokenAuthManager(str(Path(directory) / 'auth.db'))
            link = auth.generate_magic_link('https://example.test', 7)
            token = link.split('token=', 1)[1]
            session = auth.register_device(token, '127.0.0.1', 'test')
            self.assertIsNotNone(session)
            self.assertTrue(auth.validate_device(session, '127.0.0.1'))
            self.assertTrue(auth.validate_device(session, '192.0.2.10'))
            with auth._get_conn() as conn:
                row = conn.execute("SELECT client_ip FROM authorized_devices WHERE session_id=?", (hashlib.sha256(session.encode()).hexdigest(),)).fetchone()
                self.assertEqual(row["client_ip"], "192.0.2.10")

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

    def test_integration_provider_urls_preserve_configured_bridge_ports(self):
        with patch.dict('os.environ', {'AG_BRIDGE_PORT': '9123', 'CODEX_BRIDGE_PORT': '9124'}):
            providers = integrations.providers('hermes', {'gemini': ['gemini-model'], 'codex': ['codex-model']})

        self.assertEqual(providers['gemini']['api'], 'http://127.0.0.1:9123/v1')
        self.assertEqual(providers['codex']['api'], 'http://127.0.0.1:9124/v1')

    def test_codex_bridge_returns_immediately_on_provider_outage(self):
        class Upstream(BaseHTTPRequestHandler):
            calls = 0

            def log_message(self, *args):
                pass

            def do_POST(self):
                type(self).calls += 1
                self.send_response(503)
                self.end_headers()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        bridge = ThreadingHTTPServer(('127.0.0.1', 0), codex_bridge.CodexHandler)
        bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
        bridge_thread.start()
        try:
            payload = {'model': 'gpt-5.6-luna', 'input': 'hello', 'stream': False}
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory, patch.dict(
                'os.environ', {
                    'AUTH_DB_PATH': str(Path(directory) / 'audit.db'),
                    'AIPOOL_PROVIDER_MAX_ATTEMPTS': '20',
                }
            ), patch.object(codex_bridge, 'UPSTREAM_URL', f'http://127.0.0.1:{server.server_port}/v1/responses'), \
                    patch.object(codex_bridge.POOL, 'candidates', return_value=[1]), \
                    patch.object(codex_bridge.POOL, 'credentials', return_value={'tokens': {'access_token': 'test'}}), \
                    patch.object(codex_bridge.POOL, 'exhausted'), \
                    patch.object(codex_bridge.POOL, 'promote'), \
                    patch.object(client_identity, 'authorize', return_value=True), \
                    patch.object(codex_bridge.time, 'sleep') as sleeper:
                request = Request(
                    f'http://127.0.0.1:{bridge.server_port}/v1/responses',
                    data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json'},
                )
                try:
                    with urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 503)
                except urllib.error.HTTPError as err:
                    self.assertEqual(err.code, 503)
            self.assertEqual(Upstream.calls, 1)
            self.assertEqual(sleeper.call_count, 0)
        finally:
            bridge.shutdown()
            bridge.server_close()
            bridge_thread.join(timeout=2)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
