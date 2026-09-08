"""Optional Linux root-run smoke check; runs a disposable CLI install as nobody."""
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if os.geteuid() != 0:
    raise SystemExit('This optional cross-user test requires root; regular tests do not.')
user = pwd.getpwnam('nobody')
with tempfile.TemporaryDirectory(prefix='aipool-user-', dir='/var/lib') as value:
    base = Path(value)
    clone = base / 'clone with spaces'
    home = base / 'ordinary home'
    prefix = home / 'local prefix'
    files = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'))
    files.update(subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0'))
    for name in sorted(files - {''}):
        source = ROOT / name
        if source.is_file():
            target = clone / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    home.mkdir()
    for directory, dirs, names in os.walk(base):
        os.chown(directory, user.pw_uid, user.pw_gid)
        for name in names:
            os.chown(Path(directory) / name, user.pw_uid, user.pw_gid)
    def demote():
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    env = {'HOME':str(home), 'PATH':str(prefix/'bin')+':/usr/bin:/bin', 'AIPOOL_CONFIG_ENV':'/dev/null', 'LANG':'C.UTF-8'}
    def run(*args):
        result = subprocess.run(args, cwd=clone, env=env, preexec_fn=demote, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (args, result.stdout, result.stderr)
        print(result.stdout.strip())
    for _ in range(2):
        run('/bin/bash', 'install.sh', '--cli-only', '--prefix', str(prefix))
        for command in ('aglist','clist','aghelp','chelp'):
            run(command)
    run('agusage','--short')
    run('cusage','--short')
    run('/bin/bash','uninstall.sh','--cli-only')
    assert not (prefix/'bin/ag').is_symlink()
    print('PASS: unprivileged UID, empty HOME, spaced clone/prefix, reinstall and uninstall.')
