"""Optional installed-agent schema checks on disposable configurations only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class NativeAgents(unittest.TestCase):
    def fixture(self,home,agent):
        import yaml
        directory=home/('.'+agent);directory.mkdir()
        path=directory/('config.yaml' if agent=='hermes' else 'openclaw.json')
        original = ({'providers':{},'custom_providers':{'codex':{'base_url':'http://127.0.0.1:8124/v1','api_key':'pool-key','models':[{'id':'gpt-6-astra'}]}}} if agent=='hermes' else {'models':{'providers':{'codex_pool':{'baseUrl':'http://127.0.0.1:8124/v1','apiKey':'pool-key','models':['gpt-6-astra']}}}})
        path.write_text(yaml.safe_dump(original) if agent=='hermes' else json.dumps(original))
        env={k:v for k,v in os.environ.items() if not k.startswith(('HERMES_','OPENCLAW_'))}
        env.update(HOME=str(home),AIPOOL_CONFIG_ENV=os.devnull,AIPOOL_INTEGRATION_OFFLINE='1')
        result=subprocess.run([sys.executable,str(ROOT/'scripts'/f'setup-{agent}.py')],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return path,env

    @unittest.skipUnless(shutil.which('openclaw'),'OpenClaw is not installed')
    def test_openclaw_native_schema(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as td:
            path,env=self.fixture(Path(td),'openclaw')
            env['OPENCLAW_CONFIG_PATH']=str(path)
            env['OPENCLAW_STATE_DIR']=str(path.parent)
            result=subprocess.run([shutil.which('openclaw'),'config','validate','--json'],env=env,capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue(json.loads(result.stdout)['valid'])

    @unittest.skipUnless(os.environ.get('HERMES_TEST_SOURCE'),'Set HERMES_TEST_SOURCE to installed source for its real parser')
    def test_hermes_native_provider_parser(self):
        source=Path(os.environ['HERMES_TEST_SOURCE'])
        python=os.environ.get('HERMES_TEST_PYTHON',sys.executable)
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as td:
            path,env=self.fixture(Path(td),'hermes')
            code='''import sys,yaml,json
sys.path.insert(0,sys.argv[1])
from hermes_cli.runtime_provider_custom import _match_new_style_provider
cfg=yaml.safe_load(open(sys.argv[2]))
for name,transport in [('aipool-codex','codex_responses'),('aipool-gemini','chat_completions')]:
 result=_match_new_style_provider(name,cfg['providers'])
 assert result and result['api_mode']==transport,(name,result)
print('Hermes native parser resolves both pool providers with the intended transports')
'''
            result=subprocess.run([python,'-c',code,str(source),str(path)],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
