#!/usr/bin/env python3
"""Fast-forward updates with durable, honest before/after status."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "cli"))
from config_env import load
load()


def git(*args):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, timeout=120)
    if result.returncode:
        # Never echo a credential-bearing remote URL into the dashboard.
        error = re.sub(r'https?://[^\s]+', '[remote]', result.stderr.strip())
        raise ValueError(error or 'Git operation failed: ' + args[0])
    return result.stdout.strip()


def version():
    data = {'version':'unknown', 'commit':None, 'commit_date':None, 'commit_msg':None, 'dirty':False}
    try:
        data.update(json.loads((ROOT / 'version.json').read_text()))
        data['commit'] = git('rev-parse', 'HEAD')
        data['short_commit'] = data['commit'][:8]
        stamp = int(git('log', '-1', '--format=%ct'))
        data['commit_date'] = time.strftime('%m/%d %H:%M', time.localtime(stamp))
        data['commit_msg'] = git('log', '-1', '--format=%s')
        data['dirty'] = bool(git('status', '--porcelain', '--untracked-files=no'))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return data


def result_path():
    return Path(git('rev-parse', '--absolute-git-dir')) / 'aipool-update.json'


def save(result):
    path = result_path()
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(result, indent=2) + '\n')
    os.replace(tmp, path)


def update(cli_only=False, no_dashboard=False):
    result = {'success':False, 'updated':False, 'status':'checking', 'started_at':time.time(), 'version_before':version(), 'message':''}
    lock = result_path().with_suffix('.lock')
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('An update is already running') from None
        try:
            save(result)
            if result['version_before']['dirty']:
                raise ValueError('Local tracked changes detected. Commit or back up your edits before updating; nothing was discarded.')
            if git('branch', '--show-current') != 'main':
                raise ValueError('Update requires the main branch; no automatic branch changes are made')
            print('Fetching origin/main...', flush=True)
            git('fetch', 'origin', 'main')
            target = git('rev-parse', 'origin/main')
            result['target_commit'] = target
            current = git('rev-parse', 'HEAD')
            git('merge-base', '--is-ancestor', current, target)
            if current != target:
                print(git('merge', '--ff-only', 'origin/main'), flush=True)
            result['status'] = 'installing'
            save(result)
            command = [sys.executable, str(ROOT / 'scripts/manage_install.py'), '--update']
            if cli_only:
                command.append('--cli-only')
            if no_dashboard:
                command.append('--no-dashboard')
            install = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=240)
            if install.returncode:
                raise ValueError('Files updated, but installation failed. ' + install.stdout[-3000:] + install.stderr[-3000:])
            after = version()
            if after['commit'] != target or after['dirty']:
                raise ValueError('Installed source revision could not be verified')
            result.update(success=True, updated=(current != target), status='installed' if current != target else 'up_to_date', version_after=after, pending_dashboard_restart=no_dashboard and not cli_only, message=install.stdout[-6000:])
            print(('Installed new build: ' if current != target else 'Already up to date; installation refreshed: ') + after['short_commit'], flush=True)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result.update(status='failed', message=str(exc), version_after=version())
        result['finished_at'] = time.time()
        save(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cli-only', action='store_true')
    parser.add_argument('--no-dashboard', action='store_true')
    parser.add_argument('--version', action='store_true')
    args = parser.parse_args()
    if args.version:
        print(json.dumps(version()))
        return 0
    result = update(args.cli_only, args.no_dashboard)
    if not result['success']:
        print(result['message'], file=sys.stderr)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
