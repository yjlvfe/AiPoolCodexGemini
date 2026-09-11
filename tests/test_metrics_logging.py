import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
import request_log


class MetricsLoggingTests(unittest.TestCase):
    def test_boolean_or_negative_usage_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'invalid-usage.db')
            with patch.dict(os.environ, {'AUTH_DB_PATH': db}, clear=False):
                request_log.record('Codex', 'invalid-model', {'input_tokens': True, 'output_tokens': -1, 'total_tokens': 0}, request_id='invalid-1')
                with sqlite3.connect(db) as conn:
                    row = conn.execute('SELECT prompt_tokens, completion_tokens, total_tokens, usage_known FROM request_events').fetchone()
                self.assertEqual(row, (None, None, 0, 0))

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'metrics.db')
            audit = {
                'request_id': 'req-metrics-1',
                'wire_input_bytes': 1200,
                'wire_provider_bytes': 950,
                'latency_ms': 42.5,
            }
            with patch.dict(os.environ, {'AUTH_DB_PATH': path}):
                request_log.record(
                    'Codex', 'gpt-5.6-luna',
                    {'input_tokens': 8, 'output_tokens': 5, 'total_tokens': 13},
                    account=1, status='OK', audit=audit,
                )
            with sqlite3.connect(path) as connection:
                columns = {row[1] for row in connection.execute('PRAGMA table_info(request_events)')}
            self.assertTrue({'wire_input_bytes', 'wire_provider_bytes', 'latency_ms', 'error_code'} <= columns)

    def test_report_exposes_metrics_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'metrics.db')
            request_log.initialize(path)
            report = request_log.report(path)
            self.assertEqual(set(report['metrics']), {
                'wire_input_bytes', 'wire_provider_bytes', 'avg_latency_ms', 'classified_errors'
            })


if __name__ == '__main__':
    unittest.main()
