import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class Integrations(unittest.TestCase):
    def test_additive_readback_and_idempotence(self):
        import yaml
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as td:
            home = Path(td)
            for agent, filename, original in [
                ('hermes', 'config.yaml', {'model':{'default':'keep-model','provider':'keep'}, 'providers':{'keep':{'api':'https://example.test/v1'}}, 'custom_providers':[{'name':'existing','base_url':'https://example.test/v1'}]}),
                ('openclaw', 'openclaw.json', {'agents':{'defaults':{'model':{'primary':'keep/model'}, 'models':{'keep/model':{}}}}, 'models':{'providers':{'keep':{'baseUrl':'https://example.test/v1','api':'openai-completions','models':[{'id':'model','name':'model'}]}}}})
            ]:
                directory = home / ('.' + agent)
                directory.mkdir()
                path = directory / filename
                path.write_text(yaml.safe_dump(original) if agent == 'hermes' else json.dumps(original))
                before = path.read_bytes()
                env = {k:v for k,v in os.environ.items() if not k.startswith(('HERMES_', 'OPENCLAW_'))}
                env.update(AIPOOL_CONFIG_ENV=os.devnull,HOME=str(home), AIPOOL_INTEGRATION_OFFLINE='1')
                for _ in range(2):
                    result = subprocess.run(['python3', str(ROOT/'scripts'/('setup-'+agent+'.py'))], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
                    info = json.loads(result.stdout)
                    self.assertTrue(info['success'])
                    self.assertEqual(info['config_path'], str(path))
                    self.assertTrue(info['verified'])
                data = yaml.safe_load(path.read_text()) if agent == 'hermes' else json.loads(path.read_text())
                if agent == 'hermes':
                    self.assertEqual(data['model'], original['model'])
                    self.assertEqual(data['providers']['keep'], original['providers']['keep'])
                    self.assertEqual(data['providers']['aipool-codex']['transport'], 'codex_responses')
                else:
                    self.assertEqual(data['agents']['defaults']['model'], original['agents']['defaults']['model'])
                    self.assertIn('keep/model', data['agents']['defaults']['models'])
                    self.assertEqual(data['models']['providers']['keep'], original['models']['providers']['keep'])
                    self.assertEqual(data['models']['providers']['aipool-codex']['api'], 'openai-responses')
                    self.assertIsInstance(data['models']['providers']['aipool-codex']['models'][0], dict)
                backups = list(directory.glob(filename + '.aipool-*.bak'))
                self.assertTrue(any(p.read_bytes() == before for p in backups))

    def test_missing_config_is_not_success(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as td:
            env={k:v for k,v in os.environ.items() if not k.startswith(('HERMES_', 'OPENCLAW_'))}
            env.update(AIPOOL_CONFIG_ENV=os.devnull,HOME=td,AIPOOL_INTEGRATION_OFFLINE='1')
            result=subprocess.run(['python3',str(ROOT/'scripts/setup-hermes.py')],env=env,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((Path(td)/'.hermes/config.yaml').exists())

    def test_malformed_legacy_custom_providers_tolerated(self):
        import yaml
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as td:
            home = Path(td)
            directory = home / '.hermes'
            directory.mkdir()
            path = directory / 'config.yaml'
            # Test various legacy or malformed shapes of custom_providers:
            # e.g., strings in list, dictionary with non-dict values, etc.
            config_data = {
                'custom_providers': [
                    'invalid_string_entry',
                    {'name': 'custom_model', 'base_url': 'https://custom.test/v1'},
                    12345
                ],
                'providers': {}
            }
            path.write_text(yaml.safe_dump(config_data))
            env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'OPENCLAW_'))}
            env.update(AIPOOL_CONFIG_ENV=os.devnull, HOME=str(home), AIPOOL_INTEGRATION_OFFLINE='1')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/setup-hermes.py')], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            info = json.loads(result.stdout)
            self.assertTrue(info['success'])
            self.assertTrue(info['verified'])
            updated = yaml.safe_load(path.read_text())
            self.assertIn('aipool-gemini', updated['providers'])
            self.assertIn('aipool-codex', updated['providers'])


class NativeCliInjection(unittest.TestCase):
    """Fake agent CLIs prove the suite injects through the agents' own commands."""

    def make_shim(self, directory, name, body):
        binary = directory / name
        binary.write_text('#!' + sys.executable + '\n' + body)
        binary.chmod(0o755)
        return binary

    def base_env(self, shim_dir, home):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'OPENCLAW_'))}
        env.update(AIPOOL_CONFIG_ENV=os.devnull, HOME=str(home),
                   PATH=str(shim_dir) + os.pathsep + os.environ.get('PATH', ''))
        env.pop('AIPOOL_INTEGRATION_OFFLINE', None)
        return env

    def run_setup(self, agent, env):
        return subprocess.run([sys.executable, str(ROOT / 'scripts' / ('setup-' + agent + '.py'))], env=env, capture_output=True, text=True)

    def test_openclaw_injected_through_native_cli(self):
        import yaml
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as td:
            home = Path(td)
            directory = home / '.openclaw'
            directory.mkdir()
            path = directory / 'openclaw.json'
            path.write_text(json.dumps({'models': {'providers': {'keep': {'baseUrl': 'https://example.test/v1'}}}}))
            shim_dir = home / 'bin'
            shim_dir.mkdir()
            self.make_shim(shim_dir, 'openclaw', '''import json, os, sys
from pathlib import Path
log = Path(os.environ['SHIM_LOG'])
with open(log, 'a') as f:
    f.write(' '.join(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
if args[:2] == ['config', 'patch']:
    patch = json.load(sys.stdin)
    path = Path(os.environ['OPENCLAW_CONFIG_PATH'])
    cfg = json.loads(path.read_text())
    for k, v in patch.get('models', {}).get('providers', {}).items():
        cfg.setdefault('models', {}).setdefault('providers', {})[k] = v
    path.write_text(json.dumps(cfg))
    sys.exit(0)
if args[:2] == ['config', 'validate']:
    print(json.dumps({'valid': True}))
    sys.exit(0)
if args[:2] == ['config', 'get']:
    cfg = json.loads(Path(os.environ['OPENCLAW_CONFIG_PATH']).read_text())
    node = cfg
    for part in args[2].split('.'):
        node = node[part]
    print(json.dumps(node))
    sys.exit(0)
sys.exit(2)
''')
            env = self.base_env(shim_dir, home)
            env['SHIM_LOG'] = str(home / 'calls.log')
            result = self.run_setup('openclaw', env)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            info = json.loads(result.stdout)
            self.assertTrue(info['success'], info)
            self.assertEqual(info['method'], 'native-cli', info)
            self.assertIn('config patch', info.get('evidence', ''))
            data = json.loads(path.read_text())
            self.assertEqual(data['models']['providers']['keep'], {'baseUrl': 'https://example.test/v1'})
            self.assertEqual(data['models']['providers']['aipool-gemini']['api'], 'openai-completions')
            log = (home / 'calls.log').read_text()
            self.assertIn('config patch', log)
            self.assertIn('config validate', log)
            self.assertIn('config get models.providers.aipool-gemini', log)

    def test_hermes_injected_through_native_cli(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as td:
            home = Path(td)
            directory = home / '.hermes'
            directory.mkdir()
            path = directory / 'config.yaml'
            path.write_text('model:\n  default: keep-model\n')
            shim_dir = home / 'bin'
            shim_dir.mkdir()
            self.make_shim(shim_dir, 'hermes', '''import json, os, sys, yaml
from pathlib import Path
log = Path(os.environ['SHIM_LOG'])
with open(log, 'a') as f:
    f.write(' '.join(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
path = Path(os.environ['HERMES_HOME']) / 'config.yaml'
if args[:2] == ['config', 'set']:
    key = args[2]
    value = json.loads(args[3])
    cfg = yaml.safe_load(path.read_text()) if path.exists() else {}
    node = cfg
    parts = key.split('.')
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    sys.exit(0)
if args[:2] == ['config', 'get']:
    cfg = yaml.safe_load(path.read_text())
    node = cfg
    for part in args[2].split('.'):
        node = node[part]
    print(json.dumps(node))
    sys.exit(0)
sys.exit(2)
''')
            env = self.base_env(shim_dir, home)
            env['SHIM_LOG'] = str(home / 'calls.log')
            result = self.run_setup('hermes', env)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            info = json.loads(result.stdout)
            self.assertTrue(info['success'], info)
            self.assertEqual(info['method'], 'native-cli', info)
            self.assertIn('hermes config set', info.get('evidence', ''))
            import yaml as _yaml
            data = _yaml.safe_load(path.read_text())
            self.assertEqual(data['model']['default'], 'keep-model')
            self.assertEqual(data['providers']['aipool-codex']['transport'], 'codex_responses')
            self.assertEqual(data['providers']['aipool-gemini']['api'], 'http://127.0.0.1:8123/v1')
            log = (home / 'calls.log').read_text()
            self.assertIn('config set providers.aipool-gemini', log)
            self.assertIn('config get providers.aipool-codex --json', log)

    def test_native_failure_falls_back_to_file_edit(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as td:
            home = Path(td)
            directory = home / '.hermes'
            directory.mkdir()
            path = directory / 'config.yaml'
            path.write_text('model:\n  default: keep-model\n')
            shim_dir = home / 'bin'
            shim_dir.mkdir()
            self.make_shim(shim_dir, 'hermes', 'import sys\nsys.exit(3)\n')
            env = self.base_env(shim_dir, home)
            result = self.run_setup('hermes', env)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            info = json.loads(result.stdout)
            self.assertTrue(info['success'], info)
            self.assertEqual(info['method'], 'file-edit', info)
            import yaml as _yaml
            data = _yaml.safe_load(path.read_text())
            self.assertEqual(data['providers']['aipool-gemini']['transport'], 'chat_completions')
            backups = list(directory.glob('config.yaml.aipool-*.bak'))
            self.assertTrue(backups)


if __name__ == '__main__':
    unittest.main()
