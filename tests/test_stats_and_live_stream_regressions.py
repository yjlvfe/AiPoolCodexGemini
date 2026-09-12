import importlib
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'bridges'))

import request_log


class StatsAndLiveStreamRegressionTests(unittest.TestCase):
    def _record_fixture(self, path):
        with patch.dict(os.environ, {
            'AUTH_DB_PATH': str(path),
            'AIPOOL_REQUEST_RETENTION': '0',
        }, clear=False):
            request_log.record('Codex', 'codex-fixture', {
                'input_tokens': 2, 'output_tokens': 3,
            }, request_id='codex-fixture-id')
            request_log.record('gemini', 'gemini-fixture', {
                'input_tokens': 5, 'output_tokens': 7,
            }, request_id='gemini-fixture-id')
            # Historical provider spelling must remain part of Gemini totals.
            request_log.record('Antigravity', 'legacy-gemini-fixture', {
                'input_tokens': 11, 'output_tokens': 13,
            }, request_id='legacy-gemini-fixture-id')
            request_log.record('not-a-pool', 'ignored-fixture', {
                'input_tokens': 100, 'output_tokens': 200,
            }, request_id='invalid-pool-fixture-id')
            return request_log.report(str(path))

    def test_pool_summary_isolation_and_canonical_names(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self._record_fixture(Path(directory) / 'stats.db')

        codex = report['providers']['Codex']
        gemini = report['providers']['Gemini']
        self.assertEqual((codex['total_tokens'], codex['requests']), (5, 1))
        self.assertEqual((gemini['total_tokens'], gemini['requests']), (36, 2))
        self.assertNotIn('Antigravity', report['providers'])
        self.assertNotIn('not-a-pool', report['providers'])
        self.assertEqual(report['total_pool_tokens'], 41)

    def test_rollup_and_detailed_rows_are_disjoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollup.db'
            with patch.dict(os.environ, {
                'AUTH_DB_PATH': str(path),
                'AIPOOL_REQUEST_RETENTION': '1',
            }, clear=False):
                request_log.record('Codex', 'rollup-fixture', {
                    'input_tokens': 2, 'output_tokens': 3,
                }, request_id='rollup-old-id')
                request_log.record('Codex', 'rollup-fixture', {
                    'input_tokens': 7, 'output_tokens': 11,
                }, request_id='rollup-new-id')
                report = request_log.report(str(path))

            with sqlite3.connect(path) as conn:
                detailed = conn.execute("SELECT COUNT(*), COALESCE(SUM(total_tokens), 0) FROM request_events").fetchone()
                rolled = conn.execute("SELECT COALESCE(SUM(requests), 0), COALESCE(SUM(total_tokens), 0) FROM usage_rollups").fetchone()

        self.assertEqual(tuple(detailed), (1, 18))
        self.assertEqual(tuple(rolled), (1, 5))
        self.assertEqual((report['providers']['Codex']['requests'], report['providers']['Codex']['total_tokens']), (2, 23))

    def test_unknown_pool_does_not_fall_back_to_global_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self._record_fixture(Path(directory) / 'invalid.db')

        self.assertNotIn('Unknown', report['providers'])
        self.assertNotIn('not-a-pool', report['providers'])
        self.assertEqual(report['providers']['Codex']['total_tokens'], 5)
        self.assertEqual(report['providers']['Gemini']['total_tokens'], 36)
        self.assertEqual(report['total_pool_tokens'], 41)

    def test_unified_live_stream_contains_both_pools_in_id_order(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self._record_fixture(Path(directory) / 'stream.db')

        recent = report['recent_requests']
        self.assertEqual([item['id'] for item in recent], sorted(
            (item['id'] for item in recent), reverse=True
        ))
        providers = {item['provider'] for item in recent}
        self.assertEqual(providers, {'Codex', 'Gemini'})
        self.assertEqual(
            [item['provider'] for item in recent[:3]],
            ['Gemini', 'Gemini', 'Codex'],
        )

    def test_fresh_event_visibility_does_not_serve_cached_stream(self):
        with patch('threading.Thread.start'):
            app = importlib.import_module('dashboard.app')

        manager = object.__new__(app.PoolManager)
        manager._lock = threading.Lock()
        manager._cached_logs = {
            'recent_requests': [{'id': 'stale'}],
        }
        fresh = {
            'recent_requests': [{'id': 'fresh'}],
        }
        manager._build_logs_report = lambda: fresh

        self.assertIs(manager.get_usage_logs_report(), fresh)


if __name__ == '__main__':
    unittest.main()
