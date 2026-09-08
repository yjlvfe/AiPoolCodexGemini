import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'cli'))

from app import PoolManager


class DashboardManualSwitch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT.parent, prefix='aipool-dashswitch-')
        self.addCleanup(self.tmp.cleanup)
        self.ag_store = Path(self.tmp.name) / 'ag'
        self.cx_store = Path(self.tmp.name) / 'cx'
        self.ag_store.mkdir()
        self.cx_store.mkdir()

    def test_switch_account_invalid_inputs(self):
        with patch.dict(os.environ, {'AG_ACCOUNT_STORE': str(self.ag_store), 'CODEX_ACCOUNT_STORE': str(self.cx_store)}):
            pm = PoolManager()
            ok, msg = pm.switch_account('invalid_system', 1)
            self.assertFalse(ok)
            self.assertIn('Invalid system', msg)

            ok, msg = pm.switch_account('antigravity', -1)
            self.assertFalse(ok)
            self.assertIn('Invalid system or account number', msg)

            ok, msg = pm.switch_account('antigravity', 99)
            self.assertFalse(ok)
            self.assertIn('does not exist', msg)

    def test_switch_account_success(self):
        (self.ag_store / '2').mkdir()
        with patch.dict(os.environ, {'AG_ACCOUNT_STORE': str(self.ag_store), 'CODEX_ACCOUNT_STORE': str(self.cx_store)}):
            pm = PoolManager()
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout='Local switch (no Google round-trip)\nActive Antigravity account: 2', stderr='')
                ok, msg = pm.switch_account('antigravity', 2)
                self.assertTrue(ok)
                self.assertIn('Active Antigravity account: 2', msg)
                mock_run.assert_called_once()
                args = mock_run.call_args[0][0]
                self.assertIn('switch', args)
                self.assertIn('2', args)


if __name__ == '__main__':
    unittest.main()
