import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.account_manager import Manager
from dashboard.app import PoolManager


def auth(account='acct-1', email='user@example.com', refresh='old-refresh'):
    return {'tokens': {'access_token': 'access', 'refresh_token': refresh, 'account_id': account}, 'email': email}


class CodexReloginTests(unittest.TestCase):
    def manager(self, root):
        return Manager('codex')

    def test_requires_existing_slot_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            store = Path(root) / 'accounts'; store.mkdir()
            with patch.dict(os.environ, {'CODEX_ACCOUNT_STORE': str(store)}):
                manager = self.manager(root)
                with self.assertRaisesRegex(ValueError, 'existing slot'):
                    manager.relogin('1')
                (store / '1').symlink_to(Path(root))
                with self.assertRaisesRegex(ValueError, 'existing slot'):
                    manager.relogin('1')

    def test_relogin_is_unsupported_for_gemini_without_oauth_side_effect(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'AG_ACCOUNT_STORE': str(Path(root) / 'accounts')}):
            with patch('cli.account_manager.ag_module', side_effect=AssertionError('Gemini OAuth must not start')) as ag_module:
                with self.assertRaisesRegex(ValueError, 'Codex accounts only'):
                    Manager('gemini').relogin('1')
            ag_module.assert_not_called()

    def test_failed_login_leaves_slot_and_metadata_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            store = Path(root) / 'accounts'; slot = store / '1'; slot.mkdir(parents=True)
            (slot / 'auth.json').write_text(json.dumps(auth()))
            metadata = {'identity': 'acct-1', 'email': 'user@example.com', 'status': 'original'}
            (slot / 'metadata.json').write_text(json.dumps(metadata))
            (store / 'active').write_text('1\n')
            with patch.dict(os.environ, {'CODEX_ACCOUNT_STORE': str(store)}):
                manager = self.manager(root)
                with patch.object(manager, 'enroll', side_effect=ValueError('login cancelled')):
                    with self.assertRaisesRegex(ValueError, 'login cancelled'):
                        manager.relogin('1')
            self.assertEqual(json.loads((slot / 'auth.json').read_text()), auth())
            self.assertEqual(json.loads((slot / 'metadata.json').read_text()), metadata)
            self.assertEqual((store / 'active').read_text(), '1\n')

    def test_cancel_terminates_process_group_before_temp_home_cleanup(self):
        manager = PoolManager.__new__(PoolManager)
        process = type('Process', (), {'pid': 4321, 'poll': lambda self: None, 'wait': lambda self, timeout: 0})()
        temp_dir = tempfile.mkdtemp(prefix='codex-relogin-test-')
        manager._relogin_sessions = {7: {'process': process, 'temp_dir': temp_dir}}

        def terminate_group(pid, sig):
            self.assertEqual(pid, process.pid)
            self.assertEqual(sig, __import__('signal').SIGTERM)
            self.assertTrue(os.path.isdir(temp_dir))

        with patch('dashboard.app.os.killpg', side_effect=terminate_group) as killpg, patch('dashboard.app.shutil.rmtree', wraps=__import__('shutil').rmtree) as rmtree:
            result = manager.cancel_relogin('codex', 7)

        self.assertTrue(result['success'])
        killpg.assert_called_once()
        rmtree.assert_called_once_with(temp_dir, ignore_errors=True)
        self.assertFalse(os.path.exists(temp_dir))

    def test_success_replaces_credentials_in_place_and_preserves_identity(self):
        with tempfile.TemporaryDirectory() as root:
            store = Path(root) / 'accounts'; slot = store / '1'; slot.mkdir(parents=True)
            old = auth(refresh='old'); (slot / 'auth.json').write_text(json.dumps(old))
            (slot / 'metadata.json').write_text(json.dumps({'identity': 'acct-1', 'email': 'user@example.com', 'status': 'original'}))
            (store / 'active').write_text('1\n')
            fresh = auth(refresh='new')
            with patch.dict(os.environ, {'CODEX_ACCOUNT_STORE': str(store)}):
                manager = self.manager(root)
                with patch.object(manager, 'enroll', return_value=fresh), patch.object(manager, 'sync_live'):
                    manager.relogin('1')
            self.assertEqual(json.loads((slot / 'auth.json').read_text()), fresh)
            self.assertEqual(json.loads((slot / 'metadata.json').read_text())['identity'], 'acct-1')
            self.assertEqual((store / 'active').read_text(), '1\n')


if __name__ == '__main__':
    unittest.main()
