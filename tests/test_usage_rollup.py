import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
import request_log


class UsageRollupTests(unittest.TestCase):
    def test_old_rows_roll_into_compact_totals(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'usage.db')
            with patch.dict(os.environ, {'AUTH_DB_PATH': db, 'AIPOOL_REQUEST_RETENTION': '300'}, clear=False):
                for i in range(305):
                    request_log.record('Codex', 'rollup-model',
                                       {'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5},
                                       request_id=f'rollup-{i}')
                with sqlite3.connect(db) as conn:
                    detailed = conn.execute('SELECT count(*), COALESCE(sum(total_tokens),0) FROM request_events').fetchone()
                    rolled = conn.execute('SELECT COALESCE(sum(requests),0), COALESCE(sum(total_tokens),0) FROM usage_rollups').fetchone()
                self.assertEqual(detailed[0], 300)
                self.assertEqual(rolled, (5, 25))
                self.assertEqual(detailed[1] + rolled[1], 1525)

    def test_metrics_survive_rollup(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'metrics.db')
            with patch.dict(os.environ, {'AUTH_DB_PATH': db, 'AIPOOL_REQUEST_RETENTION': '1'}, clear=False):
                request_log.record('Codex', 'metrics-model', {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}, request_id='metrics-1', audit={'latency_ms': 10})
                request_log.record('Codex', 'metrics-model', {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}, request_id='metrics-2', status='FAILED', error_code='UPSTREAM', audit={'latency_ms': 30})
                metrics = request_log.report(db)['metrics']
                self.assertEqual(metrics['avg_latency_ms'], 20.0)
                self.assertEqual(metrics['classified_errors'], 1)

        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'usage.db')
            with patch.dict(os.environ, {'AUTH_DB_PATH': db, 'AIPOOL_REQUEST_RETENTION': '1'}, clear=False):
                for i in range(3):
                    request_log.record('Codex', 'report-model',
                                       {'input_tokens': 4, 'output_tokens': 6, 'total_tokens': 10},
                                       request_id=f'report-{i}')
                report = request_log.report(db)
                self.assertEqual(report['providers']['Codex']['total_tokens'], 30)
                self.assertEqual(report['providers']['Codex']['requests'], 3)


if __name__ == '__main__':
    unittest.main()
