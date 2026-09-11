import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
import request_log


class UsagePersistenceTests(unittest.TestCase):
    def test_history_survives_reinitialization_without_retention(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'usage.db')
            with patch.dict(os.environ, {'AUTH_DB_PATH': db}, clear=False):
                request_log.record('Codex', 'test-model',
                                   {'input_tokens': 11, 'output_tokens': 7, 'total_tokens': 18},
                                   request_id='durable-1')
                request_log.initialize(db)
                with sqlite3.connect(db) as conn:
                    row = conn.execute(
                        "SELECT total_tokens FROM request_events WHERE request_id=?",
                        ('durable-1',)).fetchone()
                self.assertEqual(row[0], 18)

    def test_explicit_retention_is_the_only_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'usage.db')
            with patch.dict(os.environ, {'AUTH_DB_PATH': db, 'AIPOOL_REQUEST_RETENTION': '1'}, clear=False):
                for i in range(3):
                    request_log.record('Codex', 'test-model',
                                       {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2},
                                       request_id=f'retention-{i}')
                with sqlite3.connect(db) as conn:
                    self.assertEqual(conn.execute('SELECT count(*) FROM request_events').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
