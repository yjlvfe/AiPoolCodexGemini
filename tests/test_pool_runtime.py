import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from pool_runtime import AccountPool


class PoolRuntimeTests(unittest.TestCase):
    def test_expiry_normalizes_seconds_milliseconds_and_invalid_values(self):
        pool = AccountPool('antigravity')
        self.assertEqual(pool._expiry_seconds({'expires_at': 1_700_000_000}, {}), 1_700_000_000)
        self.assertEqual(pool._expiry_seconds({'expires_at': 1_700_000_000_000}, {}), 1_700_000_000)
        self.assertEqual(pool._expiry_seconds({'expires_at': 'not-a-time'}, {}), 0.0)

    def test_cooldown_persists_across_pool_instances(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'pool.db')
            with patch.dict(os.environ, {'AUTH_DB_PATH': db}):
                first = AccountPool('codex')
                first.exhausted(7, 'gpt-5.6-luna', seconds=60, reason='quota')
                second = AccountPool('codex')
                self.assertGreater(second._load_cooldown(7, 'gpt-5.6-luna'), 0)
                row = sqlite3.connect(db).execute(
                    'SELECT reason FROM pool_cooldowns WHERE provider=? AND account=?',
                    ('codex', '7')).fetchone()
                self.assertEqual(row[0], 'quota')


if __name__ == '__main__':
    unittest.main()
