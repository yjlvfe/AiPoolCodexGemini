#!/usr/bin/env python3
"""Shared installer. Replace links atomically; never write through old aliases."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "cli"))
from config_env import load
load()
SERVICES = {
    'codex': 'bridges/codex_bridge.py',
    'gemini': 'bridges/gemini_bridge.py',
    'dashboard': 'dashboard/server.py',
}
SYSTEM_UNIT_DIR = Path('/etc/systemd/system')
USER_UNIT_DIRS = (Path('/etc/systemd/user'), Path.home() / '.config/systemd/user')
DEPLOY_ROOT = Path('/var/lib/aipool/app')
DEPLOY_VENV = Path('/var/lib/aipool/venv')
STATE = Path('/var/lib/aipool/runtime')
SERVICE_USERS = {'codex': 'aipool', 'gemini': 'aipool', 'dashboard': 'root'}
LEGACY_BY_SERVICE = {
    'codex': (
        'ai-codex.service', 'ai-codex-bridge.service',
        'aipool-codex.service', 'aipool-codex-bridge.service',
    ),
    'gemini': (
        'ai-gemini.service', 'ai-gemini-bridge.service',
        'aipool-gemini.service', 'aipool-gemini-bridge.service',
    ),
    'dashboard': (
        'ai-dashboard.service', 'ai-bot.service', 'aipool-dashboard.service',
    ),
}
HARDENED_COMMON = (
    'NoNewPrivileges=true', 'PrivateTmp=true', 'ProtectSystem=strict', 'ProtectHome=true',
    'ReadWritePaths=/var/lib/aipool/runtime /var/lib/aipool/accounts',
    'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6', 'CapabilityBoundingSet=',
    'LockPersonality=true', 'MemoryDenyWriteExecute=true', 'RestrictSUIDSGID=true', 'UMask=0077',
)

def unit_paths(name):
    return (SYSTEM_UNIT_DIR / name, *(directory / name for directory in USER_UNIT_DIRS))


def legacy_units(services=None):
    services = tuple(services or SERVICES)
    candidates = tuple(unit for service in services for unit in LEGACY_BY_SERVICE[service])
    found = set()
    for command in (
        ['systemctl', 'list-unit-files', '--all', '--no-legend'],
        ['systemctl', '--user', 'list-unit-files', '--all', '--no-legend'],
    ):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        found.update(line.split(None, 1)[0] for line in result.stdout.splitlines())
    for name in candidates:
        if any(path.exists() for path in unit_paths(name)):
            found.add(name)
    return tuple(name for name in candidates if name in found)


def retire_legacy_units(names):
    for name in names:
        for scope in ([], ['--user']):
            subprocess.run(['systemctl', *scope, 'stop', name], check=False, timeout=60)
            subprocess.run(['systemctl', *scope, 'disable', name], check=False, timeout=60)
        for path in unit_paths(name):
            path.unlink(missing_ok=True)
    run(['systemctl', 'daemon-reload'])
    subprocess.run(['systemctl', 'reset-failed'], check=False, timeout=60)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False, timeout=60)


def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def run(args):
    subprocess.run(args, check=True, timeout=60)


def link(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.is_dir() and not target.is_symlink():
        raise ValueError(f'Refusing to overwrite directory: {target}')
    tmp = target.with_name('.' + target.name + f'.aipool-{os.getpid()}')
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(source)
    os.replace(tmp, target)


def ensure_cli_tools():
    """Ensure the suite-owned ag/c launchers and official Codex CLI exist."""
    required = (ROOT / 'cli' / 'ag', ROOT / 'cli' / 'cx')
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError('Suite CLI files missing: ' + ', '.join(missing))
    installer = ROOT / 'scripts' / 'install_codex.py'
    result = subprocess.run([sys.executable, str(installer)], capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise ValueError('Codex CLI installation failed: ' + (result.stderr or result.stdout)[-2000:])
    print((result.stdout or '').strip())
    return True


def integrate_installed_agents():
    """Use native agent CLIs/configs when present; never create guessed configs."""
    script = ROOT / 'scripts' / 'integrations.py'
    results = {}
    for agent, binary in (('hermes', 'hermes'), ('openclaw', 'openclaw')):
        config = (Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')) / 'config.yaml'
                  if agent == 'hermes' else
                  Path(os.environ.get('OPENCLAW_CONFIG_PATH', Path(os.environ.get('OPENCLAW_STATE_DIR', Path.home() / '.openclaw')) / 'openclaw.json')))
        if not shutil.which(binary) or not config.is_file():
            results[agent] = {'skipped': True, 'reason': 'agent CLI or config not installed'}
            continue
        result = subprocess.run([sys.executable, str(ROOT / 'scripts' / ('setup-hermes.py' if agent == 'hermes' else 'setup-openclaw.py'))], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError(f'{agent} integration failed: ' + (result.stderr or result.stdout)[-2000:])
        results[agent] = json.loads(result.stdout.strip().splitlines()[-1])
        if not results[agent].get('verified'):
            raise ValueError(f'{agent} integration did not verify readback')
    return results


def install_cli(prefix):
    bindir = prefix / 'bin'
    links = {'ag': 'ag', 'cx': 'cx', 'c': 'cx', 'antigravity-account-switch': 'antigravity-account-switch', 'codex-account-switch': 'codex-account-switch', 'codex-account-query': 'codex-account-query', 'agusage': 'ag', 'agswitch': 'ag', 'aglist': 'ag', 'aghelp': 'ag', 'agwho': 'ag', 'cusage': 'cx', 'cswitch': 'cx', 'clist': 'cx', 'chelp': 'cx', 'cwho': 'cx'}
    for i in range(1, 101):
        links[f'ag{i}'] = 'ag'
        links[f'c{i}'] = 'cx'
    for source in set(links.values()):
        p = ROOT / 'cli' / source
        p.chmod(p.stat().st_mode | 0o111)
    # All aliases point to repository entry points, never to each other.
    for name, source in links.items():
        link(ROOT / 'cli' / source, bindir / name)
    link(ROOT / 'cli/codex-account-query', prefix / 'libexec/codex-account-query')
    print(f'CLI installed: {bindir}')
    if str(bindir) not in os.environ.get('PATH', '').split(os.pathsep):
        print(f'Add to your shell profile: export PATH="{bindir}:$PATH"')
    return [str(bindir / name) for name in links] + [str(prefix / 'libexec/codex-account-query')]


def service_available():
    return os.geteuid() == 0 and shutil.which('systemctl') and subprocess.run(
        ['systemctl', 'show-environment'], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False
    ).returncode == 0

def ensure_dependencies():
    runtime = ROOT / '.venv/bin/python'
    if not runtime.is_file():
        try:
            subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(ROOT / '.venv')], check=True)
        except subprocess.CalledProcessError:
            try:
                subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
            except subprocess.CalledProcessError:
                subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(ROOT / '.venv')], check=True)

    # 1. Check if dependencies are already available in .venv
    test_import = subprocess.run([str(runtime), '-c', 'import yaml, json5'], capture_output=True)
    if test_import.returncode == 0:
        return runtime

    # Find venv site-packages directory
    sp_dirs = list((ROOT / '.venv/lib').glob('python*/site-packages'))
    venv_sp = sp_dirs[0] if sp_dirs else None

    # 2. Add host python site-packages to venv via .pth fallback
    if venv_sp and venv_sp.is_dir():
        try:
            res = subprocess.run([sys.executable, '-c', 'import site; print("\\n".join(site.getsitepackages()))'], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                pth_file = venv_sp / 'system-fallback.pth'
                pth_file.write_text(res.stdout.strip() + '\n')
                if subprocess.run([str(runtime), '-c', 'import yaml, json5'], capture_output=True).returncode == 0:
                    return runtime
        except Exception:
            pass

    # 3. Try to copy or link packages from host python if host has them
    if venv_sp and venv_sp.is_dir():
        for pkg in ('yaml', 'json5'):
            try:
                res = subprocess.run([sys.executable, '-c', f'import {pkg}; print({pkg}.__file__)'], capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    init_path = Path(res.stdout.strip())
                    pkg_path = init_path.parent if init_path.name == '__init__.py' else init_path
                    dest = venv_sp / pkg_path.name
                    if not dest.exists():
                        try:
                            dest.symlink_to(pkg_path)
                        except OSError:
                            if pkg_path.is_dir():
                                shutil.copytree(pkg_path, dest)
                            else:
                                shutil.copy2(pkg_path, dest)
            except Exception:
                pass
        if subprocess.run([str(runtime), '-c', 'import yaml, json5'], capture_output=True).returncode == 0:
            return runtime

    # 4. Check if pip is available in .venv, if not try ensurepip
    has_pip = subprocess.run([str(runtime), '-m', 'pip', '--version'], capture_output=True).returncode == 0
    if not has_pip:
        subprocess.run([str(runtime), '-m', 'ensurepip', '--default-pip'], capture_output=True)
        has_pip = subprocess.run([str(runtime), '-m', 'pip', '--version'], capture_output=True).returncode == 0

    # 5. If pip is available in venv, install requirements
    if has_pip:
        subprocess.run([str(runtime), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(ROOT / 'requirements.txt')], capture_output=True)
        if subprocess.run([str(runtime), '-c', 'import yaml, json5'], capture_output=True).returncode == 0:
            return runtime

    # 6. Try installing using host python pip with --target
    if venv_sp and venv_sp.is_dir():
        pip_cmds = [
            [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--target', str(venv_sp), '-r', str(ROOT / 'requirements.txt')],
            ['pip3', 'install', '--disable-pip-version-check', '--target', str(venv_sp), '-r', str(ROOT / 'requirements.txt')],
            ['pip', 'install', '--disable-pip-version-check', '--target', str(venv_sp), '-r', str(ROOT / 'requirements.txt')]
        ]
        for cmd in pip_cmds:
            try:
                subprocess.run(cmd, capture_output=True)
                if subprocess.run([str(runtime), '-c', 'import yaml, json5'], capture_output=True).returncode == 0:
                    return runtime
            except Exception:
                continue

    # 7. Final check - non-fatal warning so core services and updates succeed
    verify_import = subprocess.run([str(runtime), '-c', 'import yaml, json5'], capture_output=True)
    if verify_import.returncode != 0:
        print(
            '[aipool] Note: Optional packages (PyYAML, json5) not yet in .venv.\n'
            '[aipool] Core bridges, dashboard, and CLI tools are fully functional.\n'
            '[aipool] Agent integrations will use native agent CLIs.',
            file=sys.stderr
        )
    return runtime


def render_unit(name, relative):
    script = DEPLOY_ROOT / relative
    python = DEPLOY_VENV / 'bin/python'
    user = SERVICE_USERS[name]
    environment = ['EnvironmentFile=/etc/aipool/aipool.env']
    if name == 'dashboard':
        environment += [
            'Environment=HOME=/root',
            'Environment=PYTHONUNBUFFERED=1',
            'Environment=PYTHONPATH=/var/lib/aipool/app/cli:/var/lib/aipool/app/bridges:/var/lib/aipool/app/scripts',
            'Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        ]
    else:
        environment.append('Environment=HOME=/var/lib/aipool')
    return "\n".join([
        '[Unit]', f'Description=AiPool {name} service',
        'After=network-online.target', 'Wants=network-online.target', '',
        '[Service]', 'Type=simple', f'User={user}', f'Group={user}',
        f'WorkingDirectory={script.parent}',
        f'ExecStart={quote(python)}' + (' -u' if name == 'dashboard' else '') + f' {quote(script)}',
        *environment, 'Restart=on-failure', 'RestartSec=3', *HARDENED_COMMON, '',
        '[Install]', 'WantedBy=multi-user.target', '',
    ])


def install_system_services(include_dashboard=True):
    names = tuple(SERVICES) if include_dashboard else ('codex', 'gemini')
    SYSTEM_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        (SYSTEM_UNIT_DIR / f'{name}.service').write_text(render_unit(name, SERVICES[name]))
    run(['systemctl', 'daemon-reload'])
    for name in names:
        run(['systemctl', 'enable', '--now', f'{name}.service'])
        run(['systemctl', 'is-active', '--quiet', f'{name}.service'])
        run(['systemctl', 'is-enabled', '--quiet', f'{name}.service'])
    retire_legacy_units(legacy_units(names))

def installed_prefix():
    try:
        data = json.loads((STATE / 'installation.json').read_text())
        if data['root'] == str(ROOT):
            return Path(data['prefix'])
    except (OSError, ValueError, KeyError):
        pass
    return Path('/usr/local') if os.geteuid() == 0 else Path.home() / '.local'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', type=Path)
    parser.add_argument('--cli-only', action='store_true')
    parser.add_argument('--no-dashboard', action='store_true')
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()
    if args.uninstall:
        manifest_path = STATE / 'installation.json'
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('root') != str(ROOT):
            raise ValueError('Installation belongs to another clone; nothing removed')
        if not args.cli_only and manifest.get('services'):
            for name in SERVICES:
                subprocess.run(['systemctl', 'disable', '--now', name + '.service'], check=False)
                (SYSTEM_UNIT_DIR / (name + '.service')).unlink(missing_ok=True)
            run(['systemctl', 'daemon-reload'])
        for value in manifest['links']:
            path = Path(value)
            if path.is_symlink() and path.resolve().parent == ROOT / 'cli':
                path.unlink()
        manifest_path.unlink()
        print('Suite removed. All accounts, configuration and databases preserved.')
        return
    if sys.version_info < (3, 10):
        raise ValueError('Python 3.10+ is required')
    if not args.cli_only and not service_available():
        raise ValueError('systemd is unavailable; use --cli-only for CLI tools')
    prefix = (args.prefix or Path(os.environ['AIPOOL_PREFIX']) if os.environ.get('AIPOOL_PREFIX') else args.prefix or installed_prefix()).expanduser().absolute()
    if args.update:
        state_file = STATE / 'installation.json'
        if not state_file.is_file():
            raise ValueError('Cannot update before an installation manifest exists; run install.sh first')
        print(f'Updating existing AiPool installation at {prefix}')
    if not args.cli_only:
        ensure_dependencies()
    if os.environ.get('AIPOOL_SKIP_NETWORK_INSTALL') != '1':
        ensure_cli_tools()
    else:
        print('CLI dependency installation skipped by explicit test flag')
    links = install_cli(prefix)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    prior = {}
    try:
        prior = json.loads((STATE/'installation.json').read_text())
    except (OSError,ValueError):
        pass
    manifest = {'root': str(ROOT), 'prefix': str(prefix), 'links': links, 'services': not args.cli_only or (prior.get('root') == str(ROOT) and bool(prior.get('services')))}
    (STATE / 'installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
    integration_status = integrate_installed_agents()
    print('Agent integrations:', json.dumps(integration_status, ensure_ascii=False))
    if args.cli_only:
        print('CLI-only installation completed. No services or account files changed.')
        return
    install_system_services(include_dashboard=not args.no_dashboard)
    if not args.no_dashboard:
        port = os.environ.get('DASHBOARD_PORT', '8444')
        deadline = time.monotonic() + 45
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/settings/version', timeout=2) as response:
                    if response.status != 200:
                        raise ValueError('Dashboard HTTP check failed')
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError('Dashboard is not reachable; installation is NOT complete. Check systemctl status dashboard.service')
                time.sleep(0.5)
        print(f'Dashboard reachable: http://127.0.0.1:{port}')
    print('Installation completed. Account authentication is checked separately with cusage / agusage.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'Install failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
