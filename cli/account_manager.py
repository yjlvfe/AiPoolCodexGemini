"""Transactional account storage shared by every CLI entry point (stdlib only)."""
import base64
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    if path.is_symlink():
        raise ValueError(f'Refusing credential symlink: {path}')
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError('Credential must be a JSON object')
    return data


def private_dir(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f'Unsafe directory: {path}')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def atomic_bytes(path, blob):
    private_dir(path.parent)
    if path.is_symlink():
        raise ValueError(f'Refusing credential symlink: {path}')
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def encoded(data):
    return (json.dumps(data, indent=2) + '\n').encode()


def slot(value):
    if not re.fullmatch(r'[1-9][0-9]*', value):
        raise ValueError('Account number must be a positive canonical integer')
    return value


def decode_claims(token):
    try:
        part = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except (ValueError, IndexError, TypeError):
        return {}


def codex_identity(data):
    tokens = data.get('tokens')
    if not isinstance(tokens, dict) or not all(isinstance(tokens.get(k), str) and tokens[k] for k in ('access_token', 'refresh_token')):
        raise ValueError('Expected a ChatGPT OAuth auth.json containing access_token and refresh_token')
    claims = decode_claims(tokens.get('id_token', ''))
    access = decode_claims(tokens['access_token'])
    account = tokens.get('account_id') or claims.get('https://api.openai.com/auth', {}).get('chatgpt_account_id') or access.get('https://api.openai.com/auth', {}).get('chatgpt_account_id')
    email = claims.get('email') or access.get('https://api.openai.com/profile', {}).get('email') or data.get('email')
    if not account or not email:
        raise ValueError('OAuth file has no account identity; export auth.json from a fresh Codex login')
    return {'identity': str(account), 'email': str(email), 'status': 'IMPORTED (not server-verified)'}


def find_codex():
    override = os.environ.get('CODEX_REAL_BIN') or os.environ.get('CODEX_BIN')
    if override:
        candidates = [Path(override)]
    else:
        found = shutil.which('codex')
        candidates = ([Path(found)] if found else []) + [Path.home() / x for x in ('.local/bin/codex', '.npm-global/bin/codex', '.volta/bin/codex')] + sorted((Path.home() / '.nvm/versions/node').glob('*/bin/codex'), reverse=True) + [Path('/usr/local/bin/codex'), Path('/usr/bin/codex')]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise ValueError('Codex CLI not found. Install @openai/codex in Settings or import with: c add /path/to/auth.json')


def codex_env(home):
    env = dict(os.environ)
    for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN'):
        env.pop(key, None)
    binary = find_codex()
    env.update(CODEX_HOME=str(home), CODEX_REAL_BIN=binary)
    # npm/NVM launchers need node from the same installation, even under systemd.
    env['PATH'] = str(Path(binary).parent) + os.pathsep + env.get('PATH', '')
    return env


def ag_module():
    spec = importlib.util.spec_from_file_location('aipool_ag', ROOT / 'cli/ag_provider.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Manager:
    def __init__(self, provider):
        self.provider = provider
        self.ag = provider == 'antigravity'
        self.label = 'Antigravity' if self.ag else 'Codex'
        self.prefix = 'ag' if self.ag else 'c'
        self.store = Path(os.environ.get('AG_ACCOUNT_STORE' if self.ag else 'CODEX_ACCOUNT_STORE', Path.home() / ('.antigravity-accounts' if self.ag else '.codex-accounts')))
        self.filename = 'antigravity-oauth-token' if self.ag else 'auth.json'
        self.live = Path(os.environ.get('AG_CLI_AUTH', Path.home() / '.gemini/antigravity-cli/antigravity-oauth-token')) if self.ag else Path(os.environ.get('CODEX_SHARED_HOME', Path.home() / '.codex')) / 'auth.json'

    def ids(self):
        if not self.store.exists():
            return []
        return sorted([p.name for p in self.store.iterdir() if re.fullmatch(r'[1-9][0-9]*', p.name) and not p.is_symlink() and (p.is_dir() or p.is_file())], key=int)

    def credential(self, n):
        p = self.store / slot(n)
        # Legacy numbered-file imports remain readable and are migrated transactionally on switch.
        return p / self.filename if p.is_dir() else p

    def active(self):
        p = self.store / 'active'
        if p.is_symlink():
            raise ValueError('Unsafe active marker')
        value = p.read_text().strip() if p.is_file() else ''
        return slot(value) if value else ''

    @contextlib.contextmanager
    def locked(self):
        private_dir(self.store)
        fd = os.open(self.store / 'switch.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def identity(self, data):
        if not self.ag:
            return codex_identity(data)
        inner = data.get('token', data)
        if not isinstance(inner, dict) or not inner.get('refresh_token'):
            raise ValueError('Missing Google refresh token')
        return {'identity': data.get('email') or inner.get('email'), 'email': data.get('email') or inner.get('email'), 'refresh': inner['refresh_token']}

    def duplicate(self, data, meta, number):
        for n in self.ids():
            if n == number:
                continue
            try:
                other = read_json(self.credential(n))
                identity = self.identity(other)
                if self.ag and not identity.get('email'):
                    md = self.store / n / 'metadata.json'
                    if md.is_file():
                        identity['email'] = read_json(md).get('email')
            except (ValueError, OSError):
                # A broken old slot must not prevent adding a valid new identity.
                continue
            same = str(identity.get('identity') or '').casefold() == str(meta['identity']).casefold()
            if self.ag:
                same = (identity.get('email') or '').casefold() == meta['email'].casefold() or identity.get('refresh') == data['token']['refresh_token']
            if same:
                raise ValueError(f'This account is already registered as {self.prefix}{n}; choose a different account')

    def enroll(self, browser=False):
        if self.ag:
            return ag_module().login()
        private_dir(self.store)
        with tempfile.TemporaryDirectory(prefix='.login-', dir=self.store) as home:
            env = codex_env(home)
            cmd = [env['CODEX_REAL_BIN'], '-c', 'cli_auth_credentials_store="file"', 'login']
            if not browser:
                cmd.append('--device-auth')
            print('Sign in with the NEW ChatGPT account. Existing credentials are not reused.', flush=True)
            result = subprocess.run(cmd, env=env, timeout=900)
            if result.returncode:
                raise ValueError('Codex login failed or was cancelled. If device auth is disabled, enable it in ChatGPT Security or retry: c add --browser')
            path = Path(home) / 'auth.json'
            if not path.is_file():
                raise ValueError('Codex did not create auth.json; update the official CLI and retry')
            return read_json(path)

    def add_or_switch(self, number=None, source=None, browser=False, server_verified=False):
        with self.locked():
            ids = self.ids()
            number = slot(number) if number else str(max(map(int, ids), default=0) + 1)
            path = self.credential(number)
            existing = path.is_file()
            previous = self.active()
            previous_live = None
            if previous and self.live.is_file() and self.credential(previous).is_file():
                candidate = read_json(self.live)
                saved = read_json(self.credential(previous))
                if self.identity(candidate).get('identity') == self.identity(saved).get('identity'):
                    previous_live = candidate
            data = read_json(Path(source).expanduser()) if source else read_json(path) if existing else self.enroll(browser)
            if source is None and existing and previous == number and previous_live:
                data = previous_live
            if self.ag:
                if server_verified:
                    meta = self.identity(data)
                    derived = {'access_token':data['token']['access_token'], 'refresh_token':data['token']['refresh_token'], 'project_id':data.get('project_id'), 'email':meta.get('email')}
                else:
                    data, derived, meta = ag_module().validate(data)
            else:
                meta = codex_identity(data)
                derived = None
            self.duplicate(data, meta, number)
            # No writes to live credentials or numbered slots occur before validation and uniqueness.
            target = self.store / number
            legacy = target.read_bytes() if target.is_file() else None
            if target.is_symlink():
                raise ValueError('Unsafe account directory')
            if legacy is not None:
                target.unlink()
            private_dir(target)
            writes = {}
            if previous_live and previous != number:
                writes[self.credential(previous)] = encoded(previous_live)
            writes.update({target / self.filename: encoded(data), target / 'metadata.json': encoded(meta), self.live: encoded(data), self.store / 'active': (number + '\n').encode()})
            if self.ag:
                # Keep the historical explicit integration only when it already exists.
                hermes = Path(os.environ.get('AG_HERMES_AUTH', Path.home() / '.hermes/.antigravity_oauth.json'))
                if hermes.is_file() or os.environ.get('AG_HERMES_AUTH'):
                    writes[hermes] = encoded(derived)
            snapshots = {}
            try:
                for p in writes:
                    if p.is_symlink():
                        raise ValueError(f'Unsafe credential path: {p}')
                    snapshots[p] = p.read_bytes() if p.exists() else None
                for p, blob in writes.items():
                    atomic_bytes(p, blob)
            except BaseException:
                for p, blob in snapshots.items():
                    if blob is None:
                        p.unlink(missing_ok=True)
                    else:
                        atomic_bytes(p, blob)
                if not existing or legacy is not None:
                    shutil.rmtree(target)
                if legacy is not None:
                    atomic_bytes(target, legacy)
                raise
            print(f'Active {self.label} account: {number}\nEmail: {meta["email"]}')
            if not self.ag:
                print('OAuth credentials saved. Use cusage to verify current server status.')

    def clean(self):
        # Intentionally non-destructive: old versions deleted valid or active accounts.
        with self.locked():
            seen = {}
            for n in self.ids():
                try:
                    data = read_json(self.credential(n))
                    identity = self.identity(data)
                    key = identity.get('identity') or identity.get('refresh')
                    if key in seen:
                        print(f'Duplicate candidate: {self.prefix}{n} matches {self.prefix}{seen[key]}')
                    seen[key] = n
                except (ValueError, OSError):
                    print(f'Account {n}: invalid credential; left untouched')
            print('Scan complete. Nothing deleted. Use rm N for a non-active slot (archived, not destroyed).')

    def remove(self, number):
        number = slot(number)
        with self.locked():
            if number == self.active():
                raise ValueError('Cannot remove the active account; switch to another account first')
            source = self.store / number
            if source.is_symlink() or not source.exists():
                raise ValueError('Account is missing or unsafe')
            archive = self.store / 'archive'
            private_dir(archive)
            import time
            target = archive / f'{number}-{time.time_ns()}'
            os.replace(source, target)
            print(f'Account {number} archived in {target}')

    def usage(self, selection=None, short=False):
        ids = [slot(selection)] if selection else self.ids()
        if not short:
            print(f'{self.label} Usage\n')
        if not ids:
            print(f'No accounts registered. Run: {self.prefix} add')
            return
        from usage_format import show_account
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(ids), 8)) as pool:
            for output in pool.map(lambda n: show_account(self, n, short), ids):
                print(output, end='')


def main(provider):
    manager = Manager(provider)
    args = sys.argv[1:]
    invoked = Path(sys.argv[0]).name
    match = re.fullmatch(r'(?:ag|c|C)([1-9][0-9]*)', invoked)
    if match:
        args = ['switch', match[1]] + args
    elif invoked.endswith('usage'):
        args = ['usage'] + args
    elif invoked.endswith('list'):
        args = ['list'] + args
    elif invoked.endswith('help'):
        args = ['help'] + args
    elif invoked in ('agswitch', 'cswitch'):
        args = ['switch'] + args
    cmd, rest = (args[0], args[1:]) if args else ('list', [])
    if re.fullmatch(r'[1-9][0-9]*', cmd):
        cmd, rest = 'switch', [cmd] + rest
    try:
        if cmd in ('help', '--help', '-h'):
            print(f'{manager.label} Account Manager\n\n{manager.prefix} add [FILE] [--browser]  Add a NEW identity or import credentials\n{manager.prefix} switch N / {manager.prefix}N   Switch, or enroll an unregistered slot\n{manager.prefix} list                  List saved identities without network calls\n{manager.prefix} usage [N] [--short]   Query current provider limits\n{manager.prefix} clean                 Non-destructive duplicate scan\n{manager.prefix} rm N                  Archive a non-active account\nNo provider/model settings are changed.')
        elif cmd == 'add':
            browser = '--browser' in rest
            sources = [x for x in rest if x != '--browser']
            if len(sources) > 1 or (sources and sources[0].startswith('-')):
                raise ValueError('Usage: add [FILE] [--browser]')
            manager.add_or_switch(source=sources[0] if sources else None, browser=browser)
        elif cmd in ('switch', 'refresh-active'):
            if cmd == 'refresh-active' and not rest:
                rest = [manager.active()]
            if len(rest) != 1:
                raise ValueError('Usage: switch N')
            manager.add_or_switch(number=rest[0])
        elif cmd in ('usage', '--usage'):
            selected = [x for x in rest if x != '--short']
            if len(selected) > 1:
                raise ValueError('Usage: usage [N] [--short]')
            manager.usage(selected[0] if selected else None, '--short' in rest)
        elif cmd == 'list' and not rest:
            print(f'{manager.label} Accounts\n')
            for n in manager.ids():
                try:
                    identity = manager.identity(read_json(manager.credential(n)))
                    print(f'Account {n}' + (' [ACTIVE]' if n == manager.active() else '') + f' | {identity.get("email") or "unverified"}')
                except (ValueError, OSError):
                    print(f'Account {n} | INVALID AUTH')
            if not manager.ids():
                print(f'No accounts registered. Run: {manager.prefix} add')
        elif cmd == 'clean' and not rest:
            manager.clean()
        elif cmd in ('rm', 'remove', 'del') and len(rest) == 1:
            manager.remove(rest[0])
        else:
            raise ValueError(f'Unknown command or arguments; use {manager.prefix} help')
        return 0
    except KeyboardInterrupt:
        print('Cancelled; no new account was saved.', file=sys.stderr)
        return 130
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'{manager.label}: {exc}', file=sys.stderr)
        return 1
