"""Integration fixtures use synthetic identities; no real OAuth is asserted."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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


class AntigravityAccounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT.parent, prefix='aipool-agtest-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home with spaces'
        self.home.mkdir()
        self.env = {**os.environ, "AIPOOL_CONFIG_ENV": os.devnull, 'HOME': str(self.home), 'AG_ACCOUNT_STORE': str(self.home / '.antigravity-accounts'), 'AG_CLI_AUTH': str(self.home / '.gemini-live'), 'AIPOOL_NO_BROWSER': '1'}

    def ag_data(self, email='agone@example.test', project='project-one'):
        return {'token': {'refresh_token': 'refresh-' + email, 'access_token': 'access-' + email, 'token_type': 'Bearer', 'expiry': 9999999999999}, 'email': email, 'project_id': project}

    def slot(self, number, **kw):
        store = Path(self.env['AG_ACCOUNT_STORE'])
        directory = store / str(number)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'antigravity-oauth-token').write_text(json.dumps(self.ag_data(**kw)))
        return directory

    def run_ag(self, *args):
        return subprocess.run([str(ROOT / 'cli/ag'), *args], env=self.env, text=True, capture_output=True, timeout=20)

    def test_switch_is_local_instant_and_offline(self):
        self.slot(1)
        self.slot(2, email='agtwo@example.test', project='project-two')
        (Path(self.env['AG_ACCOUNT_STORE']) / 'active').write_text('1')
        result = self.run_ag('2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Local switch', result.stdout)
        self.assertIn('Active Antigravity account: 2', result.stdout)
        self.assertEqual((Path(self.env['AG_ACCOUNT_STORE']) / 'active').read_text().strip(), '2')
        self.assertEqual(json.loads((Path(self.env['AG_CLI_AUTH']).read_text()))['email'], 'agtwo@example.test')
        slot_one = json.loads((Path(self.env['AG_ACCOUNT_STORE']) / '1/antigravity-oauth-token').read_text())
        self.assertEqual(slot_one['email'], 'agone@example.test')

    def test_add_resume_without_pending_fails_cleanly(self):
        result = self.run_ag('add', '--resume')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No pending Antigravity login', result.stderr)

    def test_add_resume_continues_pending_session(self):
        import contextlib
        import io
        import socket
        import threading
        import urllib.request
        sys.path.insert(0, str(ROOT / 'cli'))
        import ag_provider
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        store = Path(self.env['AG_ACCOUNT_STORE'])
        store.mkdir(parents=True)
        pending = {'url': 'https://accounts.google.com/example', 'state': 'fixture-state', 'verifier': 'fixture-verifier',
                   'redirect': f'http://localhost:{port}/oauth-callback', 'slot': '2', 'expires': time.time() + 3600}
        (store / 'pending-oauth.json').write_text(json.dumps(pending))
        self.env.update(AG_OAUTH_PORT=str(port), AG_OAUTH_CLIENT_ID='fixture-cid', AG_OAUTH_CLIENT_SECRET='fixture-secret')
        old = {k: v for k, v in os.environ.items()}
        os.environ.update(self.env)
        calls = {}
        def fake_request(url, payload=None, access=None, form=False):
            calls['payload'] = payload
            calls['form'] = form
            return {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3599}
        try:
            original = ag_provider.request
            ag_provider.request = fake_request
            output = io.StringIO()
            result = {}
            def runner():
                with contextlib.redirect_stdout(output):
                    result['token'] = ag_provider.login(resume=True)
            thread = threading.Thread(target=runner)
            thread.start()
            deadline = time.time() + 15
            while time.time() < deadline and 'STEP 1' not in output.getvalue():
                time.sleep(0.05)
            self.assertIn('STEP 1', output.getvalue(), 'Continuation panel was not printed')
            self.assertIn('ag add --resume', output.getvalue())
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/oauth-callback?code=fixture-code&state=fixture-state', timeout=5) as response:
                self.assertEqual(response.status, 200)
            thread.join(15)
            self.assertFalse(thread.is_alive())
            token = result.get('token')
            self.assertIsNotNone(token)
            self.assertEqual(token['token']['refresh_token'], 'new-refresh')
            self.assertEqual(calls.get('form'), True)
            self.assertEqual(calls.get('payload', {}).get('code'), 'fixture-code')
            self.assertEqual(calls.get('payload', {}).get('code_verifier'), 'fixture-verifier')
            self.assertFalse((store / 'pending-oauth.json').exists())
        finally:
            ag_provider.request = original
            os.environ.clear()
            os.environ.update(old)

    def test_quota_unwraps_quota_summary(self):
        sys.path.insert(0, str(ROOT / 'cli'))
        import ag_provider
        original = ag_provider.request
        try:
            ag_provider.request = lambda url, payload=None, access=None, form=False: {'quotaSummary': {'groups': ['fixture-group']}}
            self.assertEqual(ag_provider.quota({'token': {'access_token': 'a'}, 'project_id': 'p'}), {'groups': ['fixture-group']})
            ag_provider.request = lambda url, payload=None, access=None, form=False: {'groups': []}
            self.assertEqual(ag_provider.quota({'token': {'access_token': 'a'}, 'project_id': 'p'}), {'groups': []})
        finally:
            ag_provider.request = original

    def test_usage_failure_shows_remediation_hint(self):
        import contextlib
        import io
        sys.path.insert(0, str(ROOT / 'cli'))
        from account_manager import Manager
        from unittest.mock import patch
        import usage_format
        self.slot(1)
        (Path(self.env['AG_ACCOUNT_STORE']) / 'active').write_text('1')
        old = {k: v for k, v in os.environ.items()}
        os.environ.update(self.env)
        try:
            manager = Manager('antigravity')
            with patch.object(usage_format, 'inspect', side_effect=ValueError('Google refresh failed: Cannot reach Google')):
                lines = usage_format.show_account(manager, '1')
        finally:
            os.environ.clear()
            os.environ.update(old)
        text = lines if isinstance(lines, str) else '\n'.join(lines)
        self.assertIn('CHECK FAILED', text)
        self.assertIn('Fix: re-check with Google: ag switch 1 --verify', text)


if __name__ == '__main__':
    unittest.main()
