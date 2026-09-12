"""Additive, backed-up agent configuration with exact target readback.

Injection is performed through each agent's OWN CLI when it is installed
(hermes config set / openclaw config patch), then verified with the same
CLI (hermes config get --json / openclaw config validate --json). When a
CLI is missing (fresh workstations), the script falls back to a guarded
file edit of the exact same merged configuration.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HERMES_MIN_API_RETRIES = 25
BRIDGES_DIR = ROOT / "bridges"
sys.path.insert(0, str(BRIDGES_DIR))
sys.path.insert(0, str(ROOT / "cli"))
from model_catalog import normalize_models
from config_env import load
load()
from account_manager import atomic_bytes

# These are the exact stable provider IDs exposed to OpenClaw and the dashboard.
# Do not add prefixes: the integration contract is gemini/codex.
AGENT_PROVIDER_NAMES = {"gemini": "gemini", "codex": "codex"}
LEGACY_PROVIDER_NAMES = {
    "Gemini", "Codex", "Mixture", "mixture",
    "aipool-gemini", "aipool-codex", "aipool-mixture",
    "gemini_pool", "codex_pool",
}


def _live_models(kind: str, port: str) -> list[str]:
    """Read the bridge's authoritative live catalog; never use a baked list."""
    url = f"http://127.0.0.1:{int(port)}/v1/models"
    request = urllib.request.Request(url, headers={"Authorization": "Bearer pool-key"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise ValueError(f"{kind} live model catalog unavailable: {type(exc).__name__}") from None
    rows = payload.get("data") if isinstance(payload, dict) else None
    models = normalize_models(rows or [])
    if not models:
        raise ValueError(f"{kind} live model catalog was empty")
    return models


def _catalogs() -> dict[str, list[str]]:
    return {
        "gemini": _live_models("Gemini", os.environ.get("AG_BRIDGE_PORT", "8123")),
        "codex": _live_models("Codex", os.environ.get("CODEX_BRIDGE_PORT", "8124")),
    }


def _desired_entries(agent: str, catalogs: dict[str, list[str]] | None = None) -> dict[str, dict]:
    catalogs = catalogs or _catalogs()
    result = {}
    for kind, models in catalogs.items():
        name = AGENT_PROVIDER_NAMES[kind]
        url = f"http://127.0.0.1:{int(os.environ.get('AG_BRIDGE_PORT' if kind == 'gemini' else 'CODEX_BRIDGE_PORT', '8123' if kind == 'gemini' else '8124'))}/v1"
        port_num = '8123' if kind == 'gemini' else ('8124' if kind == 'codex' else '8125')
        url = f"http://127.0.0.1:{port_num}/v1"
        if agent == "hermes":
            result[name] = {
                "api": url,
                "api_key": "pool-key",
                "transport": "codex_responses" if kind == "codex" else "chat_completions",
                "default_model": models[0],
                "models": {model: {"context_length": 1048576} for model in models},
                "discover_models": True,
            }
        else:
            result[name] = {
                "baseUrl": url,
                "apiKey": "pool-key",
                "api": "openai-completions",
                "models": [{"id": model, "name": model, "contextWindow": 1048576} for model in models],
            }
    return result


def providers(agent, catalogs: dict[str, list[str]] | None = None):
    return _desired_entries(agent, catalogs)


def _is_legacy_pool(entry: object, agent: str) -> bool:
    if not isinstance(entry, dict):
        return False
    endpoint = entry.get("api") if agent == "hermes" else entry.get("baseUrl")
    return str(endpoint or "").rstrip("/") in {
        "http://127.0.0.1:8123/v1", "http://127.0.0.1:8124/v1"
    }


def merged(agent, original, catalogs: dict[str, list[str]] | None = None):
    data = copy.deepcopy(original)
    desired = providers(agent, catalogs)
    if agent == "hermes":
        dest = data.setdefault("providers", {})
        if not isinstance(dest, dict):
            raise ValueError("Invalid Hermes provider map; no settings were discarded")
        # Remove only this integration's obsolete identities.  MoA is a separate
        # top-level section and is deliberately never traversed or rewritten.
        for key in list(dest):
            if key in LEGACY_PROVIDER_NAMES:
                del dest[key]
        # Keep Hermes' legacy custom_providers list untouched.  It contains the
        # user's ChatGPT/Gemini subscription entries and is also referenced by
        # MoA.  This integration owns only the modern `providers:` map.
        for key, entry in desired.items():
            old = dest.get(key)
            if old is not None and not isinstance(old, dict):
                raise ValueError(f"Provider name {key} is already used by a non-provider value")
            dest[key] = {**(old or {}), **entry}
        agent_config = data.setdefault("agent", {})
        if not isinstance(agent_config, dict):
            raise ValueError("Invalid Hermes agent configuration; nothing changed")
        try:
            configured_retries = int(agent_config.get("api_max_retries", 0))
        except (TypeError, ValueError):
            configured_retries = 0
        agent_config["api_max_retries"] = max(HERMES_MIN_API_RETRIES, configured_retries)
    else:
        models_config = data.setdefault("models", {})
        if not isinstance(models_config, dict):
            raise ValueError("Invalid OpenClaw models map; no settings were discarded")
        dest = models_config.setdefault("providers", {})
        if not isinstance(dest, dict):
            raise ValueError("Invalid OpenClaw provider map; no settings were discarded")
        for key in list(dest):
            if key in LEGACY_PROVIDER_NAMES:
                del dest[key]
        for key, entry in desired.items():
            old = dest.get(key)
            if old is not None and not isinstance(old, dict):
                raise ValueError(f"Provider name {key} is already used by a non-provider value")
            dest[key] = {**(old or {}), **entry}
        defaults = data.setdefault("agents", {}).setdefault("defaults", {})
        if defaults.get("models"):
            for old_key in LEGACY_PROVIDER_NAMES:
                for model_key in list(defaults["models"]):
                    if str(model_key).startswith(old_key + "/"):
                        del defaults["models"][model_key]
            for key, entry in desired.items():
                for model in entry["models"]:
                    defaults["models"].setdefault(key + "/" + model["id"], {})
    return data



def _ensure_openclaw_model_picker_compat() -> str | None:
    """Keep AiPool's OpenAI-compatible ``codex`` provider visible in /models.

    OpenClaw reserves the same ID for its retired CLI runtime in some releases.
    The integration button repairs the installed bundle when that exact guard is
    present, so a package update can be recovered by pressing Configure again.
    """
    if shutil.which('openclaw') is None:
        return None
    try:
        resolved = subprocess.run(
            ['node', '-p', "require.resolve('openclaw/package.json')"],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, 'NODE_PATH': '/usr/local/lib/node_modules'},
        )
        roots = []
        if resolved.returncode == 0 and resolved.stdout.strip():
            roots.append(Path(resolved.stdout.strip()).parent / 'dist')
        roots.extend([Path('/usr/local/lib/node_modules/openclaw/dist'), Path('/usr/lib/node_modules/openclaw/dist')])
        for root in roots:
            for path in sorted(root.glob('model-runtime-aliases-*.js')):
                text = path.read_text(encoding='utf-8')
                old = 'new Set(["codex", "codex-cli"])'
                new = 'new Set(["codex-cli"])'
                if old in text:
                    path.write_text(text.replace(old, new, 1), encoding='utf-8')
                    return str(path)
                if new in text:
                    return str(path)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    return None


def config_path(agent):
    explicit = os.environ.get('AIPOOL_' + agent.upper() + '_CONFIG')
    if explicit:
        return Path(explicit).expanduser().absolute()
    if agent == 'hermes':
        # This installer never enumerates/modifies other Hermes profiles.
        return Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')) / 'config.yaml'
    if os.environ.get('OPENCLAW_CONFIG_PATH'):
        return Path(os.environ['OPENCLAW_CONFIG_PATH']).expanduser().absolute()
    return Path(os.environ.get('OPENCLAW_STATE_DIR', Path.home() / '.openclaw')) / 'openclaw.json'


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


def _clean_env(agent, path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'OPENCLAW_'))}
    if agent == 'hermes':
        env['HERMES_HOME'] = str(path.parent)
    else:
        env['OPENCLAW_CONFIG_PATH'] = str(path)
        env['OPENCLAW_STATE_DIR'] = str(path.parent)
    return env


def _cli_get_json(binary, env, *args, timeout=45):
    result = subprocess.run([binary, *args], env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        return None, result.stderr.strip() or result.stdout.strip()
    text = result.stdout.strip()
    try:
        return json.loads(text), None
    except ValueError:
        start = text.find('{')
        end = text.rfind('}')
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1]), None
            except ValueError:
                pass
        return None, text[:300]


def apply_native(agent, path, data):
    """Inject via the agent's own CLI. Returns True only when applied and verified natively."""
    if os.environ.get('AIPOOL_INTEGRATION_OFFLINE') == '1':
        return False
    desired = (
        data.get('providers', {})
        if agent == 'hermes'
        else data.get('models', {}).get('providers', {})
    )
    binary = shutil.which('openclaw' if agent == 'openclaw' else 'hermes')
    if not binary:
        return False
    env = _clean_env(agent, path)
    before = path.read_bytes()
    try:
        if agent == 'openclaw':
            patch = {'models': {'providers': {k: v for k, v in desired.items()}}}
            cmd = [binary, 'config', 'patch', '--stdin']
            for k in desired:
                cmd.extend(['--replace-path', f'models.providers.{k}.models'])
            applied = subprocess.run(cmd, input=json.dumps(patch),
                                     env=env, capture_output=True, text=True, timeout=60)
            if applied.returncode:
                raise ValueError(applied.stderr.strip() or applied.stdout.strip() or 'openclaw config patch failed')
            validated = subprocess.run([binary, 'config', 'validate', '--json'], env=env,
                                       capture_output=True, text=True, timeout=60)
            if validated.returncode:
                raise ValueError('openclaw config validate rejected the result')
            try:
                if not json.loads(validated.stdout)['valid']:
                    raise ValueError('openclaw config validate reported invalid')
            except (ValueError, KeyError):
                raise ValueError('openclaw config validate returned an unexpected result')
            native_evidence = 'openclaw config patch --stdin + openclaw config validate --json (valid=true)'
            for name, entry in desired.items():
                value, err = _cli_get_json(binary, env, 'config', 'get', 'models.providers.' + name)
                if not isinstance(value, dict) or value.get('baseUrl') != entry['baseUrl']:
                    raise ValueError(f'openclaw config get readback mismatch for {name}: {err or value}')
        else:
            for name, entry in desired.items():
                result = subprocess.run([binary, 'config', 'set', 'providers.' + name, json.dumps(entry), '--force'],
                                        env=env, capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise ValueError(result.stderr.strip() or result.stdout.strip() or f'hermes config set {name} failed')
            target_retries = int((data.get('agent') or {}).get('api_max_retries', HERMES_MIN_API_RETRIES))
            retry_result = subprocess.run(
                [binary, 'config', 'set', 'agent.api_max_retries', str(max(HERMES_MIN_API_RETRIES, target_retries)), '--force'],
                env=env, capture_output=True, text=True, timeout=60,
            )
            if retry_result.returncode:
                raise ValueError(retry_result.stderr.strip() or retry_result.stdout.strip() or 'hermes retry policy update failed')
            native_evidence = 'hermes config set providers (native CLI) + JSON readback'
            for name, entry in desired.items():
                value, err = _cli_get_json(binary, env, 'config', 'get', 'providers.' + name, '--json')
                if not isinstance(value, dict) or value.get('api') != entry['api'] or value.get('transport') != entry['transport']:
                    raise ValueError(f'hermes config get readback mismatch for {name}: {err or value}')
            retry_value, retry_err = _cli_get_json(binary, env, 'config', 'get', 'agent.api_max_retries', '--json')
            if int(retry_value or 0) < HERMES_MIN_API_RETRIES:
                raise ValueError(f'hermes retry policy readback mismatch: {retry_err or retry_value}')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Native injection must never leave a half-written config behind.
        try:
            atomic_bytes(path, before)
        except OSError:
            pass
        raise ValueError(f'Native {agent} CLI injection failed ({exc}); falling back to guarded file edit') from None
    # Cross-check with our own reader so the file on disk equals the intended merge.
    actual_config = read_config(agent, path)
    if actual_config.get('providers' if agent == 'hermes' else 'models', {}) != data.get('providers' if agent == 'hermes' else 'models', {}) or (
        agent == 'hermes' and int((actual_config.get('agent') or {}).get('api_max_retries', 0) or 0) < HERMES_MIN_API_RETRIES
    ):
        try:
            atomic_bytes(path, before)
        except OSError:
            pass
        raise ValueError(f'Native {agent} CLI result differs from the intended merge; falling back to guarded file edit') from None
    return native_evidence


def status(agent):
    path = config_path(agent)
    result = {'installed': path.is_file(), 'connected': False, 'config_path': str(path), 'verified': False}
    if not path.is_file():
        return result
    try:
        data = read_config(agent, path)
        actual = data.get('providers', {}) if agent == 'hermes' else data.get('models', {}).get('providers', {})
        expected = providers(agent)
        if agent == 'hermes':
            result['connected'] = all(isinstance(actual.get(k), dict) and all(actual[k].get(field) == value for field, value in entry.items()) for k, entry in expected.items())
            try:
                result['connected'] = result['connected'] and int((data.get('agent') or {}).get('api_max_retries', 0)) >= HERMES_MIN_API_RETRIES
            except (TypeError, ValueError):
                result['connected'] = False
        else:
            # For OpenClaw: models order may differ or be sorted by openclaw; verify models by ID set and baseUrl/api
            def _openclaw_match(act_prov, exp_prov):
                if not isinstance(act_prov, dict):
                    return False
                if act_prov.get('baseUrl') != exp_prov.get('baseUrl') or act_prov.get('api') != exp_prov.get('api'):
                    return False
                act_ids = {m.get('id') for m in act_prov.get('models', []) if isinstance(m, dict)}
                exp_ids = {m.get('id') for m in exp_prov.get('models', []) if isinstance(m, dict)}
                return exp_ids.issubset(act_ids)
            result['connected'] = all(_openclaw_match(actual.get(k), entry) for k, entry in expected.items())
        result['verified'] = result['connected']
        result['providers'] = list(expected)
        result['message'] = 'Configuration verified; existing sessions keep their selected provider.' if result['connected'] else 'Pool providers are missing or mismatched.'
    except (OSError, ValueError) as exc:
        result['message'] = str(exc)
    return result


def integrate(agent):
    if agent == 'openclaw':
        # Keep the provider picker repair part of the dashboard install flow;
        # package upgrades may recreate the hashed OpenClaw bundle file.
        _ensure_openclaw_model_picker_compat()
    path = config_path(agent)
    original = read_config(agent, path)
    before = path.read_bytes()
    data = merged(agent, original)
    backup = path.with_name(path.name + '.aipool-' + str(time.time_ns()) + '.bak')
    method = 'already-present'
    evidence = None
    if data != original:
        if os.environ.get('AIPOOL_INTEGRATION_OFFLINE') != '1':
            # Preferred path: the agent's own CLI writes and validates the config.
            try:
                evidence = apply_native(agent, path, data)
                method = 'native-cli'
            except ValueError:
                evidence = None
        if method == 'already-present':
            method = 'file-edit'
            evidence = 'guarded atomic file write + readback'
            if agent == 'hermes':
                import yaml
                body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode()
            else:
                body = (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode()
            atomic_bytes(backup, before)
            atomic_bytes(path, body)
            if read_config(agent, path) != data:
                atomic_bytes(path, before)
                raise ValueError('Configuration readback failed; original restored')
    if agent == 'openclaw':
        # Provider-picker compatibility is a bundle-level fix; reload the
        # gateway so Telegram reads the repaired catalog immediately.
        try:
            subprocess.run(['openclaw', 'gateway', 'restart'], capture_output=True,
                           text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    check = status(agent)
    if not check['verified']:
        if path.read_bytes() != before:
            atomic_bytes(path, before)
        raise ValueError('Provider configuration was not verified; original restored')
    check.update(success=True, changed=data != original, method=method, evidence=evidence,
                 backup=str(backup) if data != original and method == 'file-edit' else None,
                 message=f'Providers injected {("via " + method.replace("-", " ")) if method != "already-present" else "already"}; verified with readback. No default model, selected provider or fallback was changed. Select the new pool provider in your agent to route requests through this suite.',
                 selection_examples=['/model gemini/gemini-3.8-flash', '/model codex/gpt-6-astra'])
    return check


def main(agent):
    try:
        result = status(agent) if '--status' in sys.argv else integrate(agent)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'success': False, 'verified': False, 'message': str(exc)}))
        return 1
