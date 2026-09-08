"""Additive, backed-up agent configuration with exact target readback."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "cli"))
from config_env import load
load()
sys.path.insert(0, str(ROOT/'cli'))
from account_manager import atomic_bytes


def config_path(agent):
    explicit = os.environ.get('AIPOOL_' + agent.upper() + '_CONFIG')
    if explicit:
        return Path(explicit).expanduser().absolute()
    if agent == 'hermes':
        # This installer never enumerates/modifies other Hermes profiles.
        return Path(os.environ.get('HERMES_HOME', Path.home()/'.hermes'))/'config.yaml'
    if os.environ.get('OPENCLAW_CONFIG_PATH'):
        return Path(os.environ['OPENCLAW_CONFIG_PATH']).expanduser().absolute()
    return Path(os.environ.get('OPENCLAW_STATE_DIR', Path.home()/'.openclaw'))/'openclaw.json'


def read_config(agent, path):
    if not path.is_file():
        raise ValueError(f'{agent} config not found at {path}; finish agent setup or set AIPOOL_{agent.upper()}_CONFIG')
    if path.is_symlink():
        raise ValueError('Config is a symlink; explicitly target its real path')
    if agent == 'hermes':
        try:
            import yaml
        except ImportError:
            raise ValueError('Install the suite dependencies first: install.sh') from None
        data = yaml.safe_load(path.read_text())
    else:
        try:
            data = json.loads(path.read_text())
        except ValueError:
            try:
                import json5
                data = json5.loads(path.read_text())
            except ImportError:
                raise ValueError('JSON5 config requires suite dependencies: install.sh') from None
    if not isinstance(data, dict):
        raise ValueError('Agent config must be an object; nothing changed')
    return data


def providers(agent):
    result = {}
    for name, port, transport, models in [
        ('gemini', os.environ.get('AG_BRIDGE_PORT','8123'), 'chat_completions', ['gemini-3.8-flash-tiered']),
        ('codex', os.environ.get('CODEX_BRIDGE_PORT','8124'), 'codex_responses', ['gpt-6-astra','gpt-5.6-luna','gpt-5.6-sol'])
    ]:
        url = f'http://127.0.0.1:{int(port)}/v1'
        if agent == 'hermes':
            result['aipool-'+name] = {'api':url,'api_key':'pool-key','transport':transport,'default_model':models[0], 'models':{m:{} for m in models},'discover_models':True}
        else:
            result['aipool-'+name] = {'baseUrl':url,'apiKey':'pool-key','api':'openai-responses' if name=='codex' else 'openai-completions','models':[{'id':m,'name':m} for m in models]}
    return result


def merged(agent, original):
    data = copy.deepcopy(original)
    desired = providers(agent)
    if agent == 'hermes':
        legacy = data.get('custom_providers')
        if isinstance(legacy,dict):
            if not all(isinstance(entry,dict) for entry in legacy.values()):
                raise ValueError('Malformed legacy provider; no configuration was discarded')
            data['custom_providers'] = [dict(entry,name=entry.get('name',name)) for name,entry in legacy.items()]
        for entry in data.get('custom_providers',[]) or []:
            if not isinstance(entry,dict):
                raise ValueError('Malformed legacy provider; nothing changed')
            if entry.get('name') in ('codex','gemini'):
                pool = desired['aipool-'+entry['name']]
                if entry.get('base_url') == pool['api']:
                    entry['api_mode'] = pool['transport']
        dest = data.setdefault('providers', {})
    else:
        dest = data.setdefault('models',{}).setdefault('providers',{})
        # Repair only entries created by prior suite releases at these exact local endpoints.
        for legacy,kind in [('codex_pool','codex'),('gemini_pool','gemini')]:
            entry=dest.get(legacy)
            expected=desired['aipool-'+kind]
            if isinstance(entry,dict) and entry.get('baseUrl')==expected['baseUrl']:
                entry['models']=[{'id':model,'name':model} if isinstance(model,str) else model for model in entry.get('models',[])]
                entry['api']=expected['api']
    if not isinstance(dest, dict):
        raise ValueError('Invalid provider map; no settings were discarded')
    for key, entry in desired.items():
        if key in dest:
            old = dest[key]
            if not isinstance(old,dict) or (old.get('api') if agent=='hermes' else old.get('baseUrl')) != (entry.get('api') if agent=='hermes' else entry.get('baseUrl')):
                raise ValueError(f'Provider name {key} is already used by a different endpoint; nothing changed')
            # Keep unrelated per-provider options; suite transport/catalog are explicit.
            dest[key] = {**old, **entry}
        else:
            dest[key] = entry
    if agent == 'openclaw':
        defaults = data.setdefault('agents',{}).setdefault('defaults',{})
        # Only extend an existing allowlist; absent allowlists remain unrestricted.
        if defaults.get('models'):
            for key, entry in desired.items():
                for model in entry['models']:
                    defaults['models'].setdefault(key+'/'+model['id'],{})
    return data


def status(agent):
    path = config_path(agent)
    result = {'installed':path.is_file(),'connected':False,'config_path':str(path),'verified':False}
    if not path.is_file():
        return result
    try:
        data = read_config(agent,path)
        actual = data.get('providers',{}) if agent=='hermes' else data.get('models',{}).get('providers',{})
        expected = providers(agent)
        result['connected'] = all(isinstance(actual.get(k),dict) and all(actual[k].get(field)==value for field,value in entry.items()) for k,entry in expected.items())
        result['verified'] = result['connected']
        result['providers'] = list(expected)
        result['message'] = 'Configuration verified; existing sessions keep their selected provider.' if result['connected'] else 'Pool providers are missing or mismatched.'
    except (OSError, ValueError) as exc:
        result['message'] = str(exc)
    return result


def integrate(agent):
    path = config_path(agent)
    original = read_config(agent,path)
    before = path.read_bytes()
    data = merged(agent,original)
    backup = path.with_name(path.name + '.aipool-' + str(time.time_ns()) + '.bak')
    if data != original:
        if agent == 'hermes':
            import yaml
            body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode()
        else:
            body = (json.dumps(data, indent=2, ensure_ascii=False)+'\n').encode()
        atomic_bytes(backup,before)
        atomic_bytes(path,body)
        if read_config(agent,path) != data:
            atomic_bytes(path,before)
            raise ValueError('Configuration readback failed; original restored')
    check = status(agent)
    if not check['verified']:
        if path.read_bytes()!=before:
            atomic_bytes(path,before)
        raise ValueError('Provider configuration was not verified; original restored')
    check.update(success=True,changed=data!=original,backup=str(backup) if data!=original else None,
        message='Configuration saved and read back. No default model, selected provider or fallback was changed. Select the new pool provider in your agent to route requests through this suite.',
        selection_examples=['/model custom:aipool-gemini:gemini-3.8-flash-tiered','/model custom:aipool-codex:gpt-6-astra'] if agent=='hermes' else ['/model aipool-gemini/gemini-3.8-flash-tiered','/model aipool-codex/gpt-6-astra'])
    return check


def main(agent):
    try:
        result = status(agent) if '--status' in sys.argv else integrate(agent)
        print(json.dumps(result))
        return 0
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'success':False,'verified':False,'message':str(exc)}))
        return 1
