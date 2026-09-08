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
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'bridges'))

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
        # Verify normal content is hidden when account is selected (all data disappears)
        self.assertIn('.account-item-pill.selected .account-normal-content {', server_py)
        self.assertIn('display: none !important;', server_py)
        # Verify actions bar is revealed when selected with 50/50 equal split
        self.assertIn('.account-item-pill.selected .account-actions-bar {', server_py)
        self.assertIn('flex: 1 1 50%;', server_py)
        # Verify clicking account does not trigger switchAccount directly
        self.assertNotIn('btn.click()', server_py)
        # Verify switchAccount updates DOM live immediately
        self.assertIn('// Live immediate DOM update: mark account active and refresh UI without reload', server_py)
        self.assertIn('renderAccountsList();', server_py)

    def test_delete_account_backend_and_modal_contract(self):
        with tempfile.TemporaryDirectory() as td:
            ag_store = Path(td) / '.antigravity-accounts'
            cdx_store = Path(td) / '.codex-accounts'
            (ag_store / '1').mkdir(parents=True)
            (ag_store / '2').mkdir(parents=True)
            (ag_store / 'active').write_text('1\n')
            (cdx_store / '1').mkdir(parents=True)
            (cdx_store / 'active').write_text('1\n')

            pm = PoolManager()
            pm.ag_store = str(ag_store)
            pm.codex_store = str(cdx_store)
            pm._cached_ag = {'accounts': [{'account': 1, 'is_active': True}, {'account': 2, 'is_active': False}], 'pool_metrics': {'total_accounts': 2}}

            # Reject without 'confirm'
            ok, msg = pm.delete_account('antigravity', 2, 'wrong')
            self.assertFalse(ok)
            self.assertIn('confirm', msg)

            # Reject active account
            ok, msg = pm.delete_account('antigravity', 1, 'confirm')
            self.assertFalse(ok)
            self.assertIn('active account', msg)

            # Success non-active account
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout='Account 2 archived', stderr='')
                ok, msg = pm.delete_account('antigravity', 2, 'confirm')
                self.assertTrue(ok)
                self.assertIn('deleted successfully', msg)
                self.assertEqual(len(pm._cached_ag['accounts']), 1)
                self.assertEqual(pm._cached_ag['accounts'][0]['account'], 1)

        server_py = (ROOT / 'dashboard/server.py').read_text()
        # Verify delete modal HTML and input check
        self.assertIn('id="delete-modal-overlay"', server_py)
        self.assertIn('id="modal-delete-confirm-input"', server_py)
        self.assertIn('id="modal-btn-confirm-delete"', server_py)
        self.assertIn("confirmation.toLowerCase() !== 'confirm'", server_py)
        self.assertIn("fetch(resolveApiUrl('/api/accounts/delete')", server_py)

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

    def test_mobile_responsive_ui_contract(self):
        server_py = (ROOT / 'dashboard/server.py').read_text()
        # Verify responsive CSS @media rules exist
        self.assertIn('@media (max-width: 640px)', server_py)
        # Verify badge has white-space: nowrap !important to prevent breaking into 2 lines
        self.assertIn('.badge {', server_py)
        self.assertIn('white-space: nowrap !important;', server_py)
        # Verify recheck-btn has white-space: nowrap to prevent broken wrapping
        self.assertIn('.recheck-btn {', server_py)
        # Verify header sub row stacks neatly on mobile
        self.assertIn('.header-sub-row {', server_py)
        self.assertIn('flex-direction: column;', server_py)
        # Verify cli-tool-item stacks on mobile
        self.assertIn('.cli-tool-item {', server_py)

    def test_recheck_button_position_and_settings_top_row(self):
        server_py = (ROOT / 'dashboard/server.py').read_text()
        # Verify settings-card-top-row exists in CSS
        self.assertIn('.settings-card-top-row {', server_py)
        # Verify Re-check button is nested inside settings-card-top-row alongside title
        self.assertIn('<div class="settings-card-top-row">\n                        <div class="settings-card-title-group">\n                            <span class="settings-card-icon">🛠️</span>\n                            <span class="settings-card-title">CLI Tools Status</span>\n                        </div>\n                        <button class="recheck-btn"', server_py)
        # Verify subtitle is placed cleanly outside/below the top row
        self.assertIn('</div>\n                    <div class="settings-card-sub">Detects and configures CLI tools', server_py)

    def test_system_updates_creative_card_design(self):
        server_py = (ROOT / 'dashboard/server.py').read_text()
        # Verify Bento grid, commit box, and bouncing dots CSS
        self.assertIn('.bento-update-grid {', server_py)
        self.assertIn('.bento-update-cell {', server_py)
        self.assertIn('.commit-log-box {', server_py)
        self.assertIn('.update-cta-btn {', server_py)
        self.assertIn('.bouncing-dots {', server_py)
        self.assertIn('.check-update-btn {', server_py)
        # Verify System Updates header has clean status pill without duplicate recheck button
        self.assertIn('<span class="settings-card-title">System Updates</span>', server_py)
        self.assertIn('id="update-status-pill"', server_py)
        self.assertNotIn('<button class="recheck-btn" onclick="loadBuildInfo()"', server_py)
        # Verify all JS element targets exist for update_ui.js
        self.assertIn('id="settings-installed-version"', server_py)
        self.assertIn('id="settings-source-version"', server_py)
        self.assertIn('id="settings-commit-date"', server_py)
        self.assertIn('id="settings-commit-message"', server_py)
        self.assertIn('id="settings-last-update"', server_py)
        self.assertIn('id="update-result-box"', server_py)
        self.assertIn('id="btn-update-suite"', server_py)
        self.assertIn('id="btn-check-update"', server_py)
        self.assertIn('id="update-loading-dots"', server_py)

    def test_check_remote_updates_endpoint(self):
        # Verify check_updates function in update_suite
        import update_suite
        res = update_suite.check_updates()
        self.assertIn('has_update', res)
        self.assertIn('behind_count', res)
        self.assertIn('local_commit', res)

    def test_dashboard_model_normalization_and_logs_clean(self):
        import codex_bridge
        expected_codex = ['gpt-6-astra', 'gpt-5.6-luna', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.5', 'gpt-5.4-mini']
        for m in expected_codex:
            self.assertIn(m, codex_bridge.MODELS)

        import gemini_bridge
        self.assertIn('gemini-3.8-flash', gemini_bridge._CANDIDATE_MODELS)
        self.assertNotIn('gemini-3.8-flash-tiered', gemini_bridge._CANDIDATE_MODELS)
        self.assertEqual(gemini_bridge._send_model('gemini-3.8-flash'), 'gemini-3.8-flash-tiered')
        self.assertEqual(gemini_bridge._send_model('Gemini Flash 3.8'), 'gemini-3.8-flash-tiered')

        import pool_runtime
        self.assertEqual(pool_runtime.clean_model_name('gemini-3.8-flash-tiered'), 'gemini-3.8-flash')
        self.assertEqual(pool_runtime.clean_model_name('gemini-3.7-flash-tiered'), 'gemini-3.7-flash')

        from integrations import providers
        hermes_prov = providers('hermes')
        for p_name in ['gemini', 'codex']:
            p_cfg = hermes_prov[f'aipool-{p_name}']
            for m_id, m_cfg in p_cfg['models'].items():
                self.assertEqual(m_cfg['context_length'], 1048576)

        pm = PoolManager()
        status = pm.get_all_status()
        self.assertEqual(status.get('active_hermes_default'), 'gemini-3.8-flash')


if __name__ == '__main__':
    unittest.main()
