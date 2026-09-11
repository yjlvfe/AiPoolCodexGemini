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
SERVICES = {'ai-gemini-bridge': 'bridges/gemini_bridge.py', 'ai-codex-bridge': 'bridges/codex_bridge.py', 'ai-dashboard': 'dashboard/server.py', 'ai-bot': 'dashboard/bot_service.py'}
STATE = Path.home() / '.local/state/aipool'


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
    links = {'ag': 'ag', 'cx': 'cx', 'c': 'cx', 'antigravity-account-switch': 'antigravity-account-switch', 'codex-account-switch': 'codex-account-switch', 'codex-account-query': 'codex-account-query', 'agusage': 'ag', 'agswitch': 'ag', 'aglist': 'ag', 'aghelp': 'ag', 'cusage': 'cx', 'cswitch': 'cx', 'clist': 'cx', 'chelp': 'cx'}
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
    runtime = Path(f'/run/user/{os.getuid()}')
    if runtime.exists():
        os.environ.setdefault('XDG_RUNTIME_DIR', str(runtime))
        os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path={runtime}/bus')
    return shutil.which('systemctl') and subprocess.run(['systemctl', '--user', 'show-environment'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


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


def render_unit(name, relative, prefix):
    script = ROOT / relative
    path = os.pathsep.join([str(prefix / 'bin'), str(Path.home() / '.local/bin'), '/usr/local/bin', '/usr/bin', '/bin'])
    env_files = [f'EnvironmentFile=-{str(ROOT / "config.env").replace("%", "%%")}']
    # The Telegram bot must issue tokens into the same auth DB consumed by
    # the deployed dashboard.  Keep this optional for generic installations.
    if name == 'ai-bot':
        env_files.append('EnvironmentFile=-/etc/aipool/aipool.env')
    lines = [
        '[Unit]',
        f'Description=AiPool - {name}',
        'After=network.target',
        '',
        '[Service]',
        'Type=simple',
        f'WorkingDirectory={str(script.parent).replace("%", "%%")}',
        f'ExecStart={quote(ROOT / ".venv/bin/python")} {quote(script)}',
        f'Environment={quote("PATH=" + path)}',
        *env_files,
        'Restart=on-failure',
        'RestartSec=3',
        '',
        '[Install]',
        'WantedBy=default.target',
        '',
    ]
    return '\\n'.join(lines)


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
                run(['systemctl', '--user', 'disable', '--now', name + '.service'])
                unit = Path.home() / '.config/systemd/user' / (name + '.service')
                if unit.is_file() and str(ROOT) in unit.read_text():
                    unit.unlink()
            run(['systemctl', '--user', 'daemon-reload'])
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
        raise ValueError('No systemd user session. Run install.sh --cli-only for CLI tools; enable a user systemd session before installing background services. Do not run with sudo on a desktop.')
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
    units = Path.home() / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    for name, relative in SERVICES.items():
        unit = units / (name + '.service')
        if unit.is_symlink():
            unit.unlink()
        content = render_unit(name, relative, prefix)
        if unit.is_file() and unit.read_text() != content:
            shutil.copy2(unit,unit.with_name(unit.name+f'.aipool-{time.time_ns()}.bak'))
        unit.write_text(content)
    run(['systemctl', '--user', 'daemon-reload'])
    for name in SERVICES:
        if args.no_dashboard and name == 'ai-dashboard':
            continue
        # An unconfigured bot is optional; preserve an existing configured instance.
        if name == 'ai-bot' and not (os.environ.get('TG_BOT_TOKEN') or os.environ.get('BOT_TOKEN')):
            active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', name + '.service']).returncode == 0
            if not active:
                print('Telegram bot not configured; skipped.')
                continue
        run(['systemctl', '--user', 'enable', name + '.service'])
        run(['systemctl', '--user', 'restart', name + '.service'])
        run(['systemctl', '--user', 'is-active', '--quiet', name + '.service'])
    if not args.cli_only:
        integration_status = integrate_installed_agents()
        print('Agent integrations:', json.dumps(integration_status, ensure_ascii=False))
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
                    raise ValueError('Dashboard is not reachable; installation is NOT complete. Check systemctl --user status ai-dashboard')
                time.sleep(0.5)
        print(f'Dashboard reachable: http://127.0.0.1:{port}')
    print('Installation completed. Account authentication is checked separately with cusage / agusage.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'Install failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
