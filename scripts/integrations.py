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
import stat
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


def _provider_config_key(provider_map: dict, provider: str) -> str:
    """Resolve the existing OpenClaw/Hermes provider key without renaming it."""
    wanted = str(provider).strip().lower()
    aliases = {"gemini": ("gemini", "antigravity"), "codex": ("codex", "openai")}.get(wanted, (wanted,))
    for key in provider_map:
        normalized = str(key).strip().lower()
        if normalized == wanted or any(normalized.endswith(alias) for alias in aliases):
            return key
    return provider


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
            target_key = _provider_config_key(dest, key)
            old = dest.get(target_key)
            if old is not None and not isinstance(old, dict):
                raise ValueError(f"Provider name {target_key} is already used by a non-provider value")
            dest[target_key] = {**(old or {}), **entry}
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
            target_key = _provider_config_key(dest, key)
            old = dest.get(target_key)
            if old is not None and not isinstance(old, dict):
                raise ValueError(f"Provider name {target_key} is already used by a non-provider value")
            dest[target_key] = {**(old or {}), **entry}
        defaults = data.setdefault("agents", {}).setdefault("defaults", {})
        if defaults.get("models"):
            for old_key in LEGACY_PROVIDER_NAMES:
                for model_key in list(defaults["models"]):
                    if str(model_key).startswith(old_key + "/"):
                        del defaults["models"][model_key]
            for key, entry in desired.items():
                # Prune old models under this provider that are no longer in the active catalog
                valid_keys = {key + "/" + m["id"] for m in entry.get("models", [])}
                for model_key in list(defaults["models"]):
                    if str(model_key).startswith(key + "/") and model_key not in valid_keys:
                        del defaults["models"][model_key]
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
            for path in sorted(list(root.glob('model-runtime-aliases-*.js')) + list(root.glob('model-runtime-aliases-*.mjs'))):
                text = path.read_text(encoding='utf-8')
                old = 'new Set(["codex", "codex-cli"])'
                new = 'new Set(["codex-cli"])'
                if old in text:
                    path.write_text(text.replace(old, new, 1), encoding='utf-8')
            # Ensure OpenClaw Telegram /models picker matches configured provider model order (newest to oldest)
            # by looking up models.providers[provider].models order first
            for path in sorted(list(root.glob('commands-models-*.mjs')) + list(root.glob('commands-models-*.js'))):
                text = path.read_text(encoding='utf-8')
                alpha_sort = 'const models = [...byProvider.get(provider) ?? /* @__PURE__ */ new Set()].toSorted();'
                preserve_order = 'const providerConfig = Object.entries(params?.cfg?.models?.providers || {}).find(([key]) => key.toLowerCase() === String(provider).toLowerCase())?.[1]; const configuredList = (providerConfig?.models || []).map(m => (typeof m === "string" ? m : m?.id)).filter(Boolean); const rawSet = byProvider.get(provider) ?? new Set(); const models = configuredList.length > 0 ? configuredList.filter(id => rawSet.has(id)).concat([...rawSet].filter(id => !configuredList.includes(id))) : [...rawSet];'
                if alpha_sort in text:
                    text = text.replace(alpha_sort, preserve_order, 1)
                    path.write_text(text, encoding='utf-8')
            for path in sorted(list(root.glob('telegram-ingress-drain-factory-*.js')) + list(root.glob('telegram-ingress-drain-factory-*.mjs'))):
                text = path.read_text(encoding='utf-8')
                alpha_sort = 'const models = [...modelSet].toSorted((left, right) => left.localeCompare(right));'
                preserve_order = 'const providerConfig = Object.entries(runtimeCfg?.models?.providers || {}).find(([key]) => key.toLowerCase() === String(provider).toLowerCase())?.[1]; const configuredList = (providerConfig?.models || []).map(m => (typeof m === "string" ? m : m?.id)).filter(Boolean); const models = configuredList.length > 0 ? configuredList.filter(id => modelSet.has(id)).concat([...modelSet].filter(id => !configuredList.includes(id))) : [...modelSet];'
                if alpha_sort in text:
                    text = text.replace(alpha_sort, preserve_order, 1)
                    path.write_text(text, encoding='utf-8')
                elif 'const models = [...modelSet];' in text:
                    text = text.replace('const models = [...modelSet];', preserve_order, 1)
                    path.write_text(text, encoding='utf-8')
            for path in sorted(list(root.glob('command-ui-*.js')) + list(root.glob('command-ui-*.mjs'))):
                text = path.read_text(encoding='utf-8')
                if 'const cfgPath = "/root/.openclaw/openclaw.json";' not in text and 'function buildModelsKeyboard' in text:
                    injection = '''\ttry {
\t\tconst cfgPath = "/root/.openclaw/openclaw.json";
\t\tif (fs.existsSync(cfgPath)) {
\t\t\tconst cfg = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
\t\t\tconst providerConfig = Object.entries(cfg?.models?.providers || {}).find(([key]) => key.toLowerCase() === String(provider).toLowerCase())?.[1];
\t\t\tconst configuredList = (providerConfig?.models || []).map(m => (typeof m === "string" ? m : m?.id)).filter(Boolean);
\t\t\tif (configuredList.length > 0) {
\t\t\t\tconst configuredSet = new Set(configuredList);
\t\t\t\tconst matching = configuredList.filter(id => models.includes(id));
\t\t\t\tconst extra = models.filter(id => !configuredSet.has(id));
\t\t\t\tmodels = [...matching, ...extra];
\t\t\t}
\t\t}
\t} catch (e) {}
'''
                    if 'import fs from "node:fs";' not in text:
                        text = 'import fs from "node:fs";\n' + text
                    target_str = 'const pageSize = params.pageSize ?? MODELS_PAGE_SIZE;'
                    idx = text.find(target_str)
                    if idx != -1:
                        text = text[:idx] + injection + text[idx:]
                        path.write_text(text, encoding='utf-8')
            # Trigger full emoji, RTL and UI ordering restore script if installed
            if os.path.exists('/usr/local/bin/openclaw-emoji-restore'):
                try:
                    subprocess.run(['/usr/local/bin/openclaw-emoji-restore'], capture_output=True, text=True, timeout=10, check=False)
                except Exception:
                    pass
            return "repaired"
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    return None


_HERMES_PICKER_MARKER = "# AiPool: preserve case-insensitive custom provider slugs in Telegram picker"
_HERMES_PICKER_OLD = '        by_slug = {p.get("slug"): p for p in providers}\n'
_HERMES_PICKER_NEW = (
    f"        {_HERMES_PICKER_MARKER}\n"
    '        by_slug = {str(p.get("slug") or "").strip().lower(): p for p in providers}\n'
)


def _hermes_telegram_adapter_paths() -> list[Path]:
    """Find the installed Hermes Telegram adapter without touching Hermes source."""
    candidates = [
        Path('/usr/local/lib/hermes-agent/plugins/platforms/telegram/adapter.py'),
        Path('/usr/lib/hermes-agent/plugins/platforms/telegram/adapter.py'),
    ]
    return [path for path in candidates if path.is_file()]


def _patch_hermes_telegram_picker(path: Path) -> str:
    """Repair the stable case-folding seam used by Hermes' grouped provider picker."""
    text = path.read_text(encoding='utf-8')
    if _HERMES_PICKER_NEW.strip() in text:
        return 'already-present'
    if _HERMES_PICKER_OLD not in text:
        return 'unsupported'
    mode = stat.S_IMODE(path.stat().st_mode)
    owner = path.stat().st_uid, path.stat().st_gid
    updated = text.replace(_HERMES_PICKER_OLD, _HERMES_PICKER_NEW, 1)
    compile(updated, str(path), 'exec')
    atomic_bytes(path, updated.encode('utf-8'))
    os.chmod(path, mode)
    try:
        os.chown(path, *owner)
    except OSError:
        pass
    return 'repaired'


def _ensure_hermes_model_picker_compat() -> str:
    paths = _hermes_telegram_adapter_paths()
    if not paths:
        return 'unsupported'
    statuses = [_patch_hermes_telegram_picker(path) for path in paths]
    if 'unsupported' in statuses:
        return 'unsupported'
    return 'repaired' if 'repaired' in statuses else 'already-present'


def _hermes_model_picker_status() -> str:
    paths = _hermes_telegram_adapter_paths()
    if not paths:
        return 'unsupported'
    statuses = []
    for path in paths:
        text = path.read_text(encoding='utf-8')
        statuses.append('already-present' if _HERMES_PICKER_NEW.strip() in text else 'missing')
    return 'already-present' if all(item == 'already-present' for item in statuses) else 'missing'


def config_path(agent):
    explicit = os.environ.get('AIPOOL_' + agent.upper() + '_CONFIG')
    if explicit:
        return Path(explicit).expanduser().absolute()
    if agent == 'hermes':
        # Default to root or standard user hermes config if available
        root_hermes = Path('/root/.hermes/config.yaml')
        if root_hermes.is_file():
            return root_hermes
        return Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')) / 'config.yaml'
    if os.environ.get('OPENCLAW_CONFIG_PATH'):
        return Path(os.environ['OPENCLAW_CONFIG_PATH']).expanduser().absolute()
    root_openclaw = Path('/root/.openclaw/openclaw.json')
    if root_openclaw.is_file():
        return root_openclaw
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
            # Preserve the provider keys already used by OpenClaw (for example
            # YJGemini/YJCodex); the CLI otherwise creates duplicate lowercase
            # providers and the runtime picker may read the wrong entry.
            current = read_config(agent, path)
            current_providers = current.get('models', {}).get('providers', {})
            target_desired = {
                _provider_config_key(current_providers, key): value
                for key, value in desired.items()
            }
            patch = {'models': {'providers': target_desired}}
            cmd = [binary, 'config', 'patch', '--stdin']
            for k in target_desired:
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
            def _hermes_match(act_prov, exp_prov):
                if not isinstance(act_prov, dict):
                    return False
                if act_prov.get('api') != exp_prov.get('api') or act_prov.get('transport') != exp_prov.get('transport'):
                    return False
                act_models = act_prov.get('models', {})
                exp_models = exp_prov.get('models', {})
                return set(exp_models.keys()).issubset(set(act_models.keys())) if isinstance(act_models, dict) else False
            result['connected'] = all(_hermes_match(actual.get(_provider_config_key(actual, k)), entry) for k, entry in expected.items())
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
            result['connected'] = all(_openclaw_match(actual.get(_provider_config_key(actual, k)), entry) for k, entry in expected.items())
        result['verified'] = result['connected']
        if agent == 'hermes':
            result['picker_compat'] = _hermes_model_picker_status()
            result['verified'] = result['verified'] and result['picker_compat'] == 'already-present'
        result['providers'] = list(expected)
        if result['connected'] and (agent != 'hermes' or result.get('picker_compat') == 'already-present'):
            result['message'] = 'Configuration verified; existing sessions keep their selected provider.'
        elif agent == 'hermes' and result['connected']:
            result['message'] = 'Provider configuration verified, but Hermes Telegram picker compatibility is not repaired.'
        else:
            result['message'] = 'Pool providers are missing or mismatched.'
    except (OSError, ValueError) as exc:
        result['message'] = str(exc)
    return result


def integrate(agent):
    picker_compat = None
    if agent == 'hermes':
        # Re-apply after Hermes upgrades; this is deliberately before config writes so
        # unsupported bundles cannot report a misleading provider-only success.
        picker_compat = _ensure_hermes_model_picker_compat()
        if picker_compat == 'unsupported':
            raise ValueError('Hermes Telegram picker bundle is unsupported; no config changed')
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
            # Preferred and mandatory path: the agent's own CLI writes and validates the config.
            evidence = apply_native(agent, path, data)
            if evidence:
                method = 'native-cli'
            else:
                raise ValueError(f'Native CLI configuration via {agent} failed')
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
                 picker_compat=picker_compat,
                 backup=str(backup) if data != original and method == 'file-edit' else None,
                 message=f'Providers injected {("via " + method.replace("-", " ")) if method != "already-present" else "already"}; verified with readback. No default model, selected provider or fallback was changed. Select the new pool provider in your agent to route requests through this suite.',
                 selection_examples=['/model gemini/gemini-3.8-flash', '/model codex/gpt-6-astra'])
    if agent == 'hermes':
        check['restart_required'] = True
        check['message'] += ' Restart Hermes gateway from an external shell to load Telegram changes.'
    return check


def main(agent):
    try:
        if agent == 'hermes' and '--repair-picker' in sys.argv:
            result = _ensure_hermes_model_picker_compat()
            print(json.dumps({'picker_compat': result}))
            return 0 if result in ('repaired', 'already-present') else 1
        result = status(agent) if '--status' in sys.argv else integrate(agent)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'success': False, 'verified': False, 'message': str(exc)}))
        return 1
