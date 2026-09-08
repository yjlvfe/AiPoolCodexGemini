import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Updates(unittest.TestCase):
    def test_real_git_update_noop_and_dirty_refusal(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent, prefix='aipool-update-test-') as temp:
            base = Path(temp)
            source = base / 'publisher'
            source.mkdir()
            for directory in ('cli', 'scripts'):
                shutil.copytree(ROOT / directory, source / directory, ignore=shutil.ignore_patterns('__pycache__'))
            for name in ('install.sh', 'update.sh', 'version.json'):
                shutil.copy2(ROOT / name, source / name)
            env = {**os.environ, "AIPOOL_CONFIG_ENV":os.devnull, 'HOME':str(base / 'normal user'), 'GIT_CONFIG_GLOBAL':os.devnull, 'AIPOOL_PREFIX':str(base / 'normal user/.local')}
            Path(env['HOME']).mkdir()
            def run(args, cwd=source, ok=True):
                result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
                if ok:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result
            run(['git','init','-b','main'])
            run(['git','config','user.email','test@example.test'])
            run(['git','config','user.name','Fixture'])
            run(['git','add','.'])
            run(['git','commit','-m','first fixture build'])
            clone = base / 'clone with spaces'
            run(['git','clone',str(source),str(clone)])
            before = run(['git','rev-parse','HEAD'], clone).stdout.strip()
            (source / 'version.json').write_text('{"version":"9.0.1"}\n')
            run(['git','add','version.json'])
            run(['git','commit','-m','second fixture build'])
            run(['bash','update.sh','--cli-only'], clone)
            result_path = clone / '.git/aipool-update.json'
            result = json.loads(result_path.read_text())
            self.assertTrue(result['success'])
            self.assertTrue(result['updated'])
            self.assertEqual(result['version_before']['commit'], before)
            self.assertEqual(result['version_after']['version'], '9.0.1')
            self.assertNotEqual(result['version_after']['commit'], before)
            run(['bash','update.sh','--cli-only'], clone)
            result = json.loads(result_path.read_text())
            self.assertTrue(result['success'])
            self.assertFalse(result['updated'])
            self.assertEqual(result['status'], 'up_to_date')
            (clone / 'version.json').write_text('{"version":"local edit"}\n')
            self.assertNotEqual(run(['bash','update.sh','--cli-only'], clone, ok=False).returncode, 0)
            result = json.loads(result_path.read_text())
            self.assertFalse(result['success'])
            self.assertEqual(json.loads((clone / 'version.json').read_text())['version'], 'local edit')
            run(['git','restore','version.json'], clone)
            run(['git','remote','set-url','origin',str(base/'missing-origin')], clone)
            self.assertNotEqual(run(['bash','update.sh','--cli-only'], clone, ok=False).returncode, 0)
            self.assertFalse(json.loads(result_path.read_text())['success'])
