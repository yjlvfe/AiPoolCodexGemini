import importlib.util
import json
import os
from pathlib import Path
import subprocess
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
