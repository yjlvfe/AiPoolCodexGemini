import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))

from durable_requests import DurableRequestStore, request_key
import gemini_bridge
from pool_runtime import PoolError


class RecoveryTests(unittest.TestCase):
    def test_provider_outage_reaches_twentieth_attempt(self):
        body = {'model': 'gemini-3.8-flash', 'request': {'contents': []}}
        failures = [PoolError('provider unavailable', 503) for _ in range(19)]
        failures.append({'response': {'candidates': []}})
        with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=[1]), \
             patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'test'}}), \
             patch.object(gemini_bridge.AG_POOL, 'exhausted'), \
             patch.object(gemini_bridge, 'call_antigravity', side_effect=failures) as call, \
             patch.object(gemini_bridge.time, 'sleep') as sleeper:
            result = gemini_bridge.call_with_retry(body, attempts=20)
        self.assertEqual(call.call_count, 20)
        self.assertEqual(sleeper.call_count, 19)
        self.assertEqual(result, {'response': {'candidates': []}})

    def test_invalid_request_is_not_replayed(self):
        body = {'model': 'gemini-3.8-flash', 'request': {'contents': []}}
        with patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=[1]), \
             patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'test'}}), \
             patch.object(gemini_bridge, 'call_antigravity', side_effect=PoolError('bad request', 400)) as call:
            with self.assertRaises(PoolError):
                gemini_bridge.call_with_retry(body, attempts=20)
        self.assertEqual(call.call_count, 1)

    def test_request_survives_process_restart_and_replays_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recovery.db'
            payload = {'model': 'gemini-3.8-flash', 'messages': [{'role': 'user', 'content': 'continue'}]}
            key = request_key('Antigravity', '/v1/chat/completions', payload)
            first = DurableRequestStore(path)
            first.begin(
                key=key, request_id='req-1', provider='Antigravity', model=payload['model'],
                endpoint='/v1/chat/completions', payload=payload,
            )
            with sqlite3.connect(path) as connection:
                connection.execute(
                    'UPDATE durable_requests SET owner_pid=? WHERE request_key=?',
                    (os.getpid() + 1_000_000, key),
                )

            second = DurableRequestStore(path)
            self.assertEqual(second.requeue_in_progress(), 1)
            pending = second.pending()
            self.assertEqual(len(pending), 1)
            self.assertIsNotNone(second.claim_pending(key))
            response = {'response': {'candidates': [{'content': {'parts': [{'text': 'done'}]}}]}}
            second.complete(key, response)

            replay = DurableRequestStore(path).get(key)
            self.assertEqual(replay['status'], 'completed')
            self.assertEqual(json.loads(replay['response_json']), response)
            self.assertIsNone(replay['payload_json'])

    def test_http_replays_request_completed_by_recovery_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / 'recovery.db'
            registry_path = root / 'registry.json'
            registry_path.write_text(json.dumps({'mode': 'enforce', 'clients': []}))
            payload = {
                'model': 'gemini-3.8-flash',
                'messages': [{'role': 'user', 'content': 'continue'}],
                'stream': False,
            }
            response = {
                'response': {
                    'candidates': [{'content': {'parts': [{'text': 'recovered'}]}}]
                }
            }
            idempotency_key = 'restart-replay-1'
            key = request_key('Antigravity', '/v1/chat/completions', payload, idempotency_key)

            with patch.dict('os.environ', {
                'AUTH_DB_PATH': str(db_path),
                'AIPOOL_RECOVERY_DB': str(db_path),
                'AIPOOL_CLIENT_REGISTRY': str(registry_path),
            }):
                store = DurableRequestStore(db_path)
                store.begin(
                    key=key, request_id='restart-1', provider='Antigravity',
                    model=payload['model'], endpoint='/v1/chat/completions', payload=payload,
                )
                with sqlite3.connect(db_path) as connection:
                    connection.execute(
                        'UPDATE durable_requests SET owner_pid=? WHERE request_key=?',
                        (os.getpid() + 1_000_000, key),
                    )
                with patch.object(gemini_bridge, 'call_with_retry', return_value=response):
                    worker = threading.Thread(
                        target=gemini_bridge._recovery_worker,
                        args=(store,), daemon=True,
                    )
                    worker.start()
                    deadline = time.time() + 3
                    while time.time() < deadline:
                        row = store.get(key)
                        if row and row['status'] == 'completed':
                            break
                        time.sleep(0.02)
                self.assertEqual(store.get(key)['status'], 'completed')

                server = ThreadingHTTPServer(('127.0.0.1', 0), gemini_bridge.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    address = f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
                    request = urllib.request.Request(
                        address,
                        data=json.dumps(payload).encode('utf-8'),
                        headers={
                            'Content-Type': 'application/json',
                            'Idempotency-Key': idempotency_key,
                        },
                    )
                    with patch.object(
                        gemini_bridge.Handler, '_call_with_retry',
                        side_effect=AssertionError('replayed request contacted provider'),
                    ):
                        with urllib.request.urlopen(request, timeout=5) as result:
                            replay = json.load(result)
                    self.assertEqual(replay['choices'][0]['message']['content'], 'recovered')
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_idempotency_key_cannot_change_durable_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recovery.db'
            store = DurableRequestStore(path)
            key = 'fixed-idempotency-key'
            store.begin(key=key, request_id='req-1', provider='Antigravity',
                        model='gemini-3.8-flash', endpoint='/v1/chat/completions',
                        payload={'model': 'gemini-3.8-flash', 'messages': [{'content': 'one'}]})
            with self.assertRaises(ValueError):
                store.begin(key=key, request_id='req-2', provider='Antigravity',
                            model='gemini-3.8-flash', endpoint='/v1/chat/completions',
                            payload={'model': 'gemini-3.8-flash', 'messages': [{'content': 'two'}]})

            same = {'model': 'gemini-3.8-flash', 'messages': [{'content': 'one'}]}
            self.assertEqual(store.begin(
                key='active-key', request_id='req-3', provider='Antigravity',
                model='gemini-3.8-flash', endpoint='/v1/chat/completions', payload=same,
            )['state'], 'claimed')
            self.assertEqual(store.begin(
                key='active-key', request_id='req-4', provider='Antigravity',
                model='gemini-3.8-flash', endpoint='/v1/chat/completions', payload=same,
            )['state'], 'in_progress')
            self.assertEqual(store.get('active-key')['status'], 'in_progress')

            store.complete(key, {'candidates': []})
            with self.assertRaises(ValueError):
                store.begin(key=key, request_id='req-5', provider='Antigravity',
                            model='gemini-3.8-flash', endpoint='/v1/chat/completions',
                            payload={'model': 'gemini-3.8-flash', 'messages': [{'content': 'two'}]})


if __name__ == '__main__':
    unittest.main()
