"""Usage rendering keeps provider values separate from unavailable data."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from account_manager import ROOT, ag_module, atomic_bytes, codex_env, encoded, private_dir, read_json


def bar(value):
    value = max(0, min(100, round(value)))
    filled = round(value / 5)
    return '[' + '█' * filled + '-' * (20 - filled) + ']'


def countdown(value):
    try:
        if isinstance(value, str) and not value.isdigit():
            value = datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        seconds = max(0, int(float(value) - time.time()))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        return f'{days}d {hours}h' if days else f'{hours}h {seconds // 60}m' if hours else f'{seconds // 60}m'
    except (ValueError, TypeError):
        return 'N/A'


def inspect(manager, number):
    with manager.locked():
        path = manager.credential(number)
        data = read_json(path)
        snapshot = path.read_bytes()
    if manager.ag:
        provider = ag_module()
        fresh, _, meta = provider.refresh(data)
        project = fresh.get('project_id') or data.get('project_id')
        if not project:
            raise ValueError('No Code Assist project on this account yet; run: ' + manager.prefix + ' switch ' + number + ' --verify to onboard it')
        info = {'status':'OK', 'email':meta['email'], 'usage':provider.quota({**fresh, 'project_id':project})}
    else:
        with tempfile.TemporaryDirectory(prefix='.query-', dir=manager.store) as home:
            atomic_bytes(Path(home) / 'auth.json', encoded(data))
            env = codex_env(home)
            result = subprocess.run([sys.executable, str(ROOT / 'cli/codex-account-query'), 'usage'], env=env, capture_output=True, text=True, timeout=50)
            if result.returncode:
                raise ValueError('Codex account query failed')
            info = json.loads(result.stdout)
            fresh = read_json(Path(home) / 'auth.json')
    with manager.locked():
        # CAS: do not overwrite credentials that another process refreshed during the query.
        if path.is_file() and path.read_bytes() == snapshot:
            atomic_bytes(path, encoded(fresh))
            if manager.active() == number and manager.live.is_file() and manager.live.read_bytes() == snapshot:
                atomic_bytes(manager.live, encoded(fresh))
    return info


def show_account(manager, number, short=False):
    email = '-'
    try:
        identity = manager.identity(read_json(manager.credential(number)))
        email = identity.get('email') or '-'
        info = inspect(manager, number)
        email = info.get('email') or email
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        info = {'status':'CHECK FAILED', 'error':str(exc)}
    status = info.get('status', 'CHECK FAILED')
    active = ' [ACTIVE]' if manager.active() == number else ''
    lines = [f'{number}{active} {email}'] if short else [f'Account {number}{active}', f'Email: {email}']
    if status != 'OK':
        lines.append('Status: ' + status + (': ' + info['error'] if info.get('error') else ''))
        if manager.ag:
            lines.append('Fix: re-check with Google: ' + manager.prefix + ' switch ' + number + ' --verify   (or open Antigravity once in a browser, then retry)')
    elif manager.ag:
        if not short:
            lines.append('Status: OK')
        groups = info.get('usage', {}).get('groups') or []
        if not groups:
            lines.append('Quota windows: N/A (not returned by provider)')
        for group in groups:
            buckets = {b.get('window'):b for b in group.get('buckets') or []}
            lines.append(group.get('displayName') or 'Models')
            for window, label in [('5h','5-hour'), ('weekly','Weekly')]:
                bucket = buckets.get(window, {})
                fraction = bucket.get('remainingFraction')
                text = 'N/A' if fraction is None else f'{bar(float(fraction) * 100)} {round(float(fraction) * 100)}% left'
                lines.append(f'{label}: {text}')
                if not short:
                    lines.append('Reset in: ' + countdown(bucket.get('resetTime')))
    else:
        if not short:
            lines.append('Plan: ' + str(info.get('planType') or 'N/A').capitalize())
        windows = {w.get('windowDurationMins'):w for w in info.get('windows') or []}
        for duration, label in [(300, '5-hour'), (10080, 'Weekly')]:
            win = windows.get(duration, {})
            used = win.get('usedPercent')
            text = 'N/A' if used is None else f'{bar(100 - used)} {max(0, min(100, round(100 - used)))}% left'
            lines.append(f'{label}: {text}')
            if not short:
                lines.append('Reset in: ' + countdown(win.get('resetsAt')))
    return ('  '.join(lines) if short else '\n'.join(lines)) + '\n' + ('' if short else '\n')
