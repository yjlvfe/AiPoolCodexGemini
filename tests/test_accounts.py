"""Integration fixtures use synthetic identities; no real OAuth is asserted."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def auth(email='first@example.test', account='account-one'):
    claims = {'email': email, 'https://api.openai.com/auth': {'chatgpt_account_id': account}}
    jwt = 'x.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.x'
    return {'tokens': {'access_token': jwt, 'id_token': jwt, 'refresh_token': 'fixture-' + account, 'account_id': account}}


class Accounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT.parent, prefix='aipool-test-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home with spaces'
        self.home.mkdir()
        self.env = {**os.environ, "AIPOOL_CONFIG_ENV":os.devnull, 'HOME': str(self.home), 'CODEX_ACCOUNT_STORE': str(self.home / '.codex-accounts'), 'AG_ACCOUNT_STORE': str(self.home / '.antigravity-accounts'), 'CODEX_SHARED_HOME': str(self.home / '.codex'), 'AIPOOL_NO_BROWSER': '1'}
        self.input = self.home / 'input.json'
        self.input.write_text(json.dumps(auth()))

    def run_cli(self, *args):
        return subprocess.run([str(ROOT / 'cli/cx'), *args], env=self.env, text=True, capture_output=True, timeout=15)

    def fake_codex(self):
        binary = self.home / 'node bin/codex'
        binary.parent.mkdir(exist_ok=True)
        binary.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if 'login' in args:
    if os.environ.get('FAKE_LOGIN_FAIL'):
        print('fixture login cancelled', file=sys.stderr)
        sys.exit(17)
    Path(os.environ['CODEX_HOME'], 'auth.json').write_text(os.environ['FAKE_AUTH'])
    sys.exit(0)
if 'app-server' in args:
    for line in sys.stdin:
        msg = json.loads(line)
        if 'id' not in msg:
            continue
        result = {}
        if msg['method'] == 'account/read':
            result = {'account': {'type':'chatgpt', 'email':'first@example.test', 'planType':'plus'}}
        if msg['method'] == 'account/rateLimits/read':
            result = {'rateLimits': {'primary': {'usedPercent':20,'windowDurationMins':300,'resetsAt':1999999999}, 'secondary': {'usedPercent':35,'windowDurationMins':10080,'resetsAt':1999999999}}}
        print(json.dumps({'id':msg['id'], 'result':result}), flush=True)
''')
        binary.chmod(0o755)
        self.env.update(CODEX_REAL_BIN=str(binary), FAKE_AUTH=json.dumps(auth()))

    def test_first_login_uses_isolated_home_and_keeps_config(self):
        self.fake_codex()
        shared = self.home / '.codex'
        shared.mkdir()
        (shared / 'config.toml').write_text('model = "unchanged-model"\n')
        (shared / 'sessions').mkdir()
        result = self.run_cli('add')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((shared / 'config.toml').read_text(), 'model = "unchanged-model"\n')
        self.assertEqual(json.loads((shared / 'auth.json').read_text()), auth())
        self.assertEqual((shared / 'auth.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse(list((self.home / '.codex-accounts').glob('.login-*')))

    def test_failed_login_preserves_active_auth(self):
        self.fake_codex()
        self.assertEqual(self.run_cli('add').returncode, 0)
        before = (self.home / '.codex/auth.json').read_bytes()
        self.env['FAKE_LOGIN_FAIL'] = '1'
        failed = self.run_cli('add')
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual((self.home / '.codex/auth.json').read_bytes(), before)
        self.assertFalse((self.home / '.codex-accounts/2').exists())

    def test_usage_uses_app_server_and_active_marker(self):
        self.fake_codex()
        self.assertEqual(self.run_cli('add').returncode, 0)
        result = self.run_cli('usage')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Account 1 [ACTIVE]', result.stdout)
        self.assertIn('80% left', result.stdout)
        self.assertIn('65% left', result.stdout)

    def test_invalid_import_and_path_traversal_leave_state(self):
        self.input.write_text('{}')
        self.assertNotEqual(self.run_cli('add', str(self.input)).returncode, 0)
        self.assertFalse((self.home / '.codex-accounts/1').exists())
        self.assertNotEqual(self.run_cli('rm', '..').returncode, 0)
        self.assertTrue(self.input.exists())

    def test_incomplete_slot_does_not_block_add(self):
        store = self.home / '.codex-accounts'
        (store / '4').mkdir(parents=True)
        result = self.run_cli('add', str(self.input))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((store / '5/auth.json').is_file())
        self.assertTrue((store / '4').is_dir())

    def test_rotated_live_token_saved_before_switch(self):
        self.assertEqual(self.run_cli('add', str(self.input)).returncode, 0)
        live = self.home / '.codex/auth.json'
        rotated = json.loads(live.read_text())
        rotated['tokens']['refresh_token'] = 'fixture-rotated'
        live.write_text(json.dumps(rotated))
        other = self.home / 'other.json'
        other_data = json.loads(self.input.read_text())
        other_data['tokens']['account_id'] = 'second-fixture-account'
        other.write_text(json.dumps(other_data))
        self.assertEqual(self.run_cli('add', str(other)).returncode, 0)
        saved = json.loads((self.home / '.codex-accounts/1/auth.json').read_text())
        self.assertEqual(saved['tokens']['refresh_token'], 'fixture-rotated')
        self.assertEqual(self.run_cli('switch', '1').returncode, 0)
        self.assertEqual(json.loads(live.read_text())['tokens']['refresh_token'], 'fixture-rotated')

    def test_archive_refuses_active_and_clean_is_non_destructive(self):
        self.assertEqual(self.run_cli('add', str(self.input)).returncode, 0)
        store = self.home / '.codex-accounts'
        import shutil
        shutil.copytree(store / '1', store / '2')
        self.assertEqual(self.run_cli('clean').returncode, 0)
        self.assertTrue((store / '2/auth.json').is_file())
        self.assertNotEqual(self.run_cli('rm', '1').returncode, 0)
        self.assertEqual(self.run_cli('rm', '2').returncode, 0)
        self.assertEqual(len(list((store / 'archive').iterdir())), 1)

    def test_symlink_live_target_is_not_overwritten(self):
        shared = self.home / '.codex'
        shared.mkdir()
        victim = self.home / 'protected'
        victim.write_text('preserve this')
        (shared / 'auth.json').symlink_to(victim)
        result = self.run_cli('add', str(self.input))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(victim.read_text(), 'preserve this')
        self.assertFalse((self.home / '.codex-accounts/1').exists())

    def test_duplicate_import_does_not_publish_second_slot(self):
        first = self.run_cli('add', str(self.input))
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        duplicate = self.run_cli('add', str(self.input))
        self.assertNotEqual(duplicate.returncode, 0, 'Duplicate was incorrectly accepted')
        self.assertIn('already registered', duplicate.stderr)
        self.assertFalse((self.home / '.codex-accounts/2').exists())
        self.assertEqual((self.home / '.codex-accounts/active').read_text().strip(), '1')


if __name__ == '__main__':
    unittest.main()
