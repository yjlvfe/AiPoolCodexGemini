import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Installation(unittest.TestCase):
    @unittest.skipUnless(shutil.which('systemd-analyze'), 'Native systemd parser not installed')
    def test_rendered_service_passes_native_parser_with_spaces(self):
        import importlib.util
        import sys
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=ROOT.parent, prefix='aipool systemd ') as value:
            clone = Path(value) / 'clone with spaces'
            (clone / '.venv/bin').mkdir(parents=True)
            (clone / '.venv/bin/python').symlink_to(sys.executable)
            (clone / 'bridges').mkdir()
            script = clone / 'bridges/probe.py'
            script.write_text('print("native-unit-probe")\n')
            spec = importlib.util.spec_from_file_location('install_test', ROOT/'scripts/manage_install.py')
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            with patch.dict(os.environ, {'AIPOOL_CONFIG_ENV':os.devnull}):
                spec.loader.exec_module(module)
            with patch.object(module, 'ROOT', clone):
                unit = Path(value) / 'aipool-fixture.service'
                unit.write_text(module.render_unit('aipool-fixture','bridges/probe.py',Path(value)/'prefix with spaces'))
            result = subprocess.run(['systemd-analyze','--user','verify',str(unit)], capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertNotIn('not absolute',result.stderr)

    def test_reinstall_never_follows_old_alias_symlink(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent, prefix='aipool-install-test-') as temp:
            home = Path(temp) / 'normal user'
            home.mkdir()
            prefix = home / '.local'
            binary = prefix / 'bin'
            binary.mkdir(parents=True)
            target = binary / 'antigravity-account-switch'
            original = b'original installed switcher\n'
            target.write_bytes(original)
            (binary / 'ag3').symlink_to(target)
            script = ROOT / 'scripts/manage_install.py'
            self.assertTrue(script.exists(), 'Need one shared portable installation implementation')
            env = {**os.environ, "AIPOOL_CONFIG_ENV":os.devnull, 'HOME':str(home), 'AIPOOL_PREFIX':str(prefix)}
            for _ in range(2):
                result = subprocess.run(['python3', str(script), '--cli-only'], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.resolve(), ROOT / 'cli/antigravity-account-switch')
            for name in ('ag3', 'agusage', 'aglist', 'cusage', 'c1', 'c', 'cx'):
                self.assertTrue((binary / name).is_symlink())
            for name in ('ag', 'agusage', 'cusage', 'aglist'):
                result = subprocess.run([str(binary / name)] + (['help'] if name == 'ag' else []), env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_ensure_dependencies_resilient_to_missing_pip(self):
        import importlib.util
        import sys
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location('install_mod', ROOT / 'scripts/manage_install.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(dir=ROOT.parent, prefix='aipool-pip-test-') as td:
            fake_root = Path(td)
            venv_bin = fake_root / '.venv/bin'
            venv_bin.mkdir(parents=True)
            fake_python = venv_bin / 'python'
            fake_python.write_text('#!' + sys.executable + '\nimport sys\n# Fake python that pretends yaml/json5 are installed\nif "-c" in sys.argv and "import yaml, json5" in sys.argv[sys.argv.index("-c")+1]:\n    sys.exit(0)\n# If called with pip, pretend pip is missing\nif "-m" in sys.argv and "pip" in sys.argv:\n    sys.stderr.write("No module named pip\\n")\n    sys.exit(1)\nsys.exit(0)\n')
            fake_python.chmod(0o755)
            with patch.object(module, 'ROOT', fake_root):
                # Since fake python can import yaml, json5, ensure_dependencies should succeed without calling pip
                runtime = module.ensure_dependencies()
                self.assertEqual(runtime, fake_python)
