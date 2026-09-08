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

    def test_ui_switch_contract(self):
        server_py = (ROOT / 'dashboard/server.py').read_text()
        # Verify switch buttons are hidden by default
        self.assertIn('.account-switch-btn {\n            display: none !important;', server_py)
        # Verify switch buttons are revealed on .selected
        self.assertIn('.account-item-pill.selected .account-switch-btn {\n            display: inline-flex !important;', server_py)
        # Verify clicking account does not trigger switchAccount
        self.assertNotIn('btn.click()', server_py)
        # Verify switchAccount updates DOM live immediately
        self.assertIn('// Live immediate DOM update: mark account active and refresh UI without reload', server_py)
        self.assertIn('renderAccountsList();', server_py)

    def test_settings_toggle_preserves_grid_layout(self):
        server_py = (ROOT / 'dashboard/server.py').read_text()
        # Ensure providerBar display reset does not force 'flex' which breaks the 2-column grid
        self.assertNotIn("providerBar.style.display = 'flex'", server_py)
        self.assertIn("providerBar.style.display = '';", server_py)

    def test_resolve_api_url_proxy_compatibility(self):
        server_py = (ROOT / 'dashboard/server.py').read_text()
        # Verify resolveApiUrl and apiHeaders exist
        self.assertIn('function resolveApiUrl(subpath)', server_py)
        self.assertIn('function apiHeaders(extraHeaders = {})', server_py)
        # Verify settings endpoints use resolveApiUrl
        self.assertIn("fetch(resolveApiUrl('/api/settings/status')", server_py)
        self.assertIn("fetch(resolveApiUrl('/api/settings/check_cli')", server_py)
        self.assertIn("fetch(resolveApiUrl('/api/settings/install_cli')", server_py)
        self.assertIn("fetch(resolveApiUrl(endpoint)", server_py)
        self.assertIn("fetch(resolveApiUrl('/api/settings/uninstall')", server_py)
        self.assertIn("fetch(resolveApiUrl('/api/accounts/switch')", server_py)


if __name__ == '__main__':
    unittest.main()
