#!/usr/bin/env python3
"""
Antigravity Gateway Bridge
=========================
Converts Google Account token into OpenAI-compatible endpoints (chat/completions)
to use Gemini subscriptions inside Hermes without manual API keys.
"""

import json
import os
import time
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"cli"))
from config_env import load
load()
import urllib.request
import urllib.parse
import urllib.error
import threading
import http.server
import socketserver
import secrets
import sqlite3
from copy import deepcopy

from model_catalog import CatalogError, DynamicCatalog, catalog_cache_dir, extract_gemini_catalog

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HOST = os.environ.get("AG_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AG_BRIDGE_PORT", "8123"))

# Antigravity OAuth client credentials (resolved lazily from environment or the
# private credentials file; never hard-code secrets in source)
from oauth_credentials import oauth_credentials

TOKEN_URL = "https://oauth2.googleapis.com/token"

# Antigravity gateway endpoints
ANTIGRAVITY_ENDPOINTS = [
    "https://daily-cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
]
ANTIGRAVITY_PATH = "/v1internal:generateContent"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "Antigravity/1.18.3 Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36")
X_GOOG_API_CLIENT = "google-cloud-sdk vscode_cloudshelleditor/0.1"
CLIENT_METADATA = '{"ideType":"ANTIGRAVITY","platform":"MACOS","pluginType":"GEMINI"}'
SKIP_THOUGHT_SIGNATURE = "skip_thought_signature_validator"

DEFAULT_PROJECT = "aicode-consumers"
MAX_REQUEST_BYTES = 32 * 1024 * 1024

# ---------------------------------------------------------------------------
# Token management (refresh)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_token_cache = {"access": None, "expires_at": 0}


def _session_paths():
    store = os.environ.get('AG_ACCOUNT_STORE', os.path.expanduser('~/.antigravity-accounts'))
    active_path = os.path.join(store, 'active')
    active = ''
    try:
        with open(active_path) as handle:
            active = handle.read().strip()
    except OSError:
        pass
    return [
        os.path.join(store, active, 'antigravity-oauth-token') if active.isdigit() else '',
        os.environ.get('AG_SESSION_TOKEN_FILE') or '',
        os.path.expanduser('~/.gemini/antigravity-cli/antigravity-oauth-token'),
        os.path.expanduser('~/.hermes/.antigravity_oauth.json'),
    ]


def _current_project():
    for path in _session_paths():
        try:
            with open(path) as handle:
                data = json.load(handle)
            project = data.get('project_id') or data.get('projectId')
            if project:
                return project
        except (OSError, ValueError):
            continue
    return DEFAULT_PROJECT


def _load_refresh_from_session():
    candidates = _session_paths()
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                d = json.load(f)
            inner = d.get("token", d)
            rt = inner.get("refresh_token") or inner.get("refreshToken")
            if rt:
                return rt
        except Exception:
            continue
    return None


def _refresh_tokens():
    rt = (os.environ.get("AG_REFRESH_TOKEN") or "").strip()
    if not rt:
        rt = _load_refresh_from_session()
    if not rt:
        raise RuntimeError("No refresh_token found in session files.")

    try:
        client_id, client_secret = oauth_credentials()
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from None

    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": rt,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Token refresh failed: HTTP {e.code}")
    except Exception as e:
        raise RuntimeError(f"Token refresh network error: {type(e).__name__}")

    if "access_token" not in data:
        raise RuntimeError(f"Token refresh response missing access_token: {list(data)}")
    return data


def get_access_token():
    with _lock:
        now = time.time()
        source = os.environ.get('AG_REFRESH_TOKEN') or _load_refresh_from_session()
        if _token_cache.get('source') != source:
            _token_cache['access'] = None
            _token_cache['source'] = source
        if _token_cache["access"] and _token_cache["expires_at"] > now + 60:
            return _token_cache["access"]
        data = _refresh_tokens()
        _token_cache["access"] = data["access_token"]
        _token_cache["expires_at"] = now + int(data.get("expires_in", 3599)) - 30
        return _token_cache["access"]


# ---------------------------------------------------------------------------
# Model Mapping & Discovery
# ---------------------------------------------------------------------------
# Map clean public model names to the exact upstream wire IDs required by Antigravity
MODEL_SEND_MAP = {
    # Gemini Flash 3.8 / gemini-3.8-flash
    "gemini-3.8-flash": "gemini-3.8-flash-tiered",
    "Gemini Flash 3.8": "gemini-3.8-flash-tiered",
    "Flash 3.8": "gemini-3.8-flash-tiered",
    "gemini-3.8": "gemini-3.8-flash-tiered",
    "gemini-3.8-flash-tiered": "gemini-3.8-flash-tiered",

    # Gemini Flash 3.7 / gemini-3.7-flash
    "gemini-3.7-flash": "gemini-3.7-flash-tiered",
    "Gemini Flash 3.7": "gemini-3.7-flash-tiered",
    "Flash 3.7": "gemini-3.7-flash-tiered",
    "gemini-3.7": "gemini-3.7-flash-tiered",
    "gemini-3.7-flash-tiered": "gemini-3.7-flash-tiered",

    # Gemini Flash 3.6 / gemini-3.6-flash
    "gemini-3.6-flash": "gemini-3.6-flash-tiered",
    "Gemini Flash 3.6": "gemini-3.6-flash-tiered",
    "Flash 3.6": "gemini-3.6-flash-tiered",
    "gemini-3.6": "gemini-3.6-flash-tiered",
    "gemini-3.6-flash-tiered": "gemini-3.6-flash-tiered",
    "gemini-3.6-flash-high": "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium": "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low": "gemini-3.6-flash-low",

    # Gemini 3.1 Pro / gemini-3.1-pro
    "gemini-3.1-pro": "gemini-3.1-pro-low",
    "Gemini 3.1 Pro": "gemini-3.1-pro-low",
    "gemini-3.1-pro-low": "gemini-3.1-pro-low",

    # Gemini legacy fallbacks
    "gemini-3-flash": "gemini-3-flash",
    "gemini-pro-agent": "gemini-pro-agent",

    # Claude models
    "claude-opus-4-6": "claude-opus-4-6-thinking",
    "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
    "claude-sonnet-4-6": "claude-sonnet-4-6",

    # GPT models
    "gpt-oss-120b": "gpt-oss-120b-medium",
    "gpt-oss-120b-medium": "gpt-oss-120b-medium",
}

# Model discovery is deliberately upstream-driven.  The old candidate list was
# removed because it silently hid newly introduced Antigravity models.
def _fetch_gemini_catalog():
    """Fetch the authoritative Antigravity model catalog.

    ``fetchAvailableModels`` is the same upstream call used by the installed
    Antigravity client.  Only model IDs and the observed wire IDs are retained;
    credentials and response bodies are never written to the catalog cache.
    """
    access = get_access_token()
    project = _current_project()
    body = json.dumps({"project": project}, separators=(",", ":")).encode()
    headers = _antigravity_headers(access)
    last_error = None
    for endpoint in ANTIGRAVITY_ENDPOINTS:
        url = endpoint + "/v1internal:fetchAvailableModels"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            models, wire_models = extract_gemini_catalog(payload)
            return models, {"wire_models": wire_models}
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            try:
                exc.close()
            except Exception:
                pass
        except (OSError, ValueError, TypeError, CatalogError) as exc:
            last_error = type(exc).__name__
    raise CatalogError(f"Gemini model catalog unavailable ({last_error or 'upstream error'})")


_gemini_catalog = DynamicCatalog(
    "gemini",
    _fetch_gemini_catalog,
    catalog_cache_dir() / "gemini.json",
)


def _send_model(model):
    if not isinstance(model, str) or not model or model != model.strip():
        raise ValueError("An exact model ID is required")
    try:
        # A model newly returned by upstream is accepted immediately.  The
        # dynamic wire map takes precedence over compatibility aliases.
        _gemini_catalog.get()
        wire_models = _gemini_catalog.metadata.get("wire_models", {})
        if isinstance(wire_models, dict) and model in wire_models:
            return wire_models[model]
    except CatalogError:
        pass
    return MODEL_SEND_MAP.get(model, model)


def list_available_models(force_refresh=True):
    models = _gemini_catalog.get(force=force_refresh)
    out = []
    for name in models:
        if name.startswith("claude"):
            owned = "anthropic"
        elif name.startswith("gpt"):
            owned = "openai"
        else:
            owned = "google"
        out.append({"id": name, "object": "model", "owned_by": owned})
    return out


# ---------------------------------------------------------------------------
# Schema & Tool Transformation Helpers
# ---------------------------------------------------------------------------
def _build_tools(tools):
    declarations = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise ValueError("Only function tools are supported by this endpoint")
        fn = tool.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
            raise ValueError("Function tools require function.name")
        declaration = {"name": fn["name"]}
        if "description" in fn:
            if not isinstance(fn["description"], str):
                raise ValueError("Function description must be text")
            declaration["description"] = fn["description"]
        if "parameters" in fn:
            if not isinstance(fn["parameters"], dict):
                raise ValueError("Function parameters must be a JSON schema object")
            declaration["parametersJsonSchema"] = deepcopy(fn["parameters"])
        declarations.append(declaration)
    return [{"functionDeclarations": declarations}] if declarations else None


def _content_parts(content):
    """Lossless supported content translation; reject rather than omit."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        raise PoolError("Message content must be text or content parts", 400)
    parts = []
    for block in content:
        if not isinstance(block, dict):
            raise PoolError("Content parts must be objects", 400)
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append({"text": block["text"]})
        elif block.get("type") in ("image_url", "image"):
            image = block.get("image_url", block.get("url"))
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str) or not url:
                raise PoolError("Image requires a URL", 400)
            if url.startswith("data:") and ";base64," in url:
                meta, data = url.split(",", 1)
                parts.append({"inlineData": {"mimeType": meta[5:].split(";", 1)[0], "data": data}})
            elif urllib.parse.urlsplit(url).scheme in ("https", "http", "gs"):
                parts.append({"fileData": {"fileUri": url}})
            else:
                raise PoolError("Unsupported image URL", 400)
        else:
            raise PoolError("Unsupported content part", 400)
    return parts


# ---------------------------------------------------------------------------
# Request & Response Conversion
# ---------------------------------------------------------------------------
def to_antigravity_body(payload):
    from canonical_request import CanonicalRequest
    try:
        payload = CanonicalRequest.from_payload(payload).to_payload()
    except ValueError as exc:
        raise PoolError(str(exc), 400) from None
    messages = payload.get("messages", [])
    model_name = payload.get("model") or "gemini-3.8-flash"
    wire_model = _send_model(model_name)

    instructions = payload.get("instructions")
    system_parts = ([{"text": item} for item in instructions] if isinstance(instructions, list)
                    else _content_parts(instructions))
    contents = []
    call_names = {}
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("system", "developer"):
            parts = _content_parts(content)
            if any("text" not in part for part in parts):
                raise PoolError("System content must contain text only", 400)
            system_parts.extend(parts)
        elif role in ("user", "assistant"):
            parts = _content_parts(content)
            tool_calls = m.get("tool_calls") or []
            if not isinstance(tool_calls, list) or (tool_calls and role != "assistant"):
                raise PoolError("tool_calls must be an assistant array", 400)
            for tc in tool_calls:
                fn = tc.get("function") if isinstance(tc, dict) else None
                if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
                    raise PoolError("Tool calls require function.name", 400)
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        raise PoolError("Tool arguments must be valid JSON", 400) from None
                if not isinstance(args, dict):
                    raise PoolError("Tool arguments must be a JSON object", 400)
                call_id = tc.get("id") or tc.get("call_id")
                if not isinstance(call_id, str) or not call_id:
                    raise PoolError("Tool calls require an id", 400)
                part = {"functionCall": {"name": fn["name"], "args": args, "id": call_id},
                        "thoughtSignature": tc.get("thought_signature") or SKIP_THOUGHT_SIGNATURE}
                parts.append(part)
                call_names[call_id] = fn["name"]
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
        elif role == "tool":
            call_id = m.get("tool_call_id")
            name = m.get("name") or call_names.get(call_id)
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                raise PoolError("Tool results require a matching tool_call_id or name", 400)
            part = {"functionResponse": {"name": name, "id": call_id, "response": {"output": deepcopy(content)}}}
            if contents and contents[-1].get("role") == "user" and any("functionResponse" in p for p in contents[-1]["parts"]):
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
        else:
            raise PoolError("Unsupported message role", 400)
    if not contents:
        raise PoolError("At least one non-system message is required", 400)

    try:
        max_tok = int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 65536)
    except (TypeError, ValueError):
        raise PoolError("max_tokens must be an integer", 400) from None
    if max_tok < 1:
        raise PoolError("max_tokens must be positive", 400)
    gen_cfg = {}
    temp = payload.get("temperature")
    if temp is not None:
        try:
            gen_cfg["temperature"] = float(temp)
        except (TypeError, ValueError):
            raise PoolError("temperature must be numeric", 400) from None

    if wire_model.startswith("gpt-"):
        gen_cfg["maxOutputTokens"] = min(max_tok, 8192)
    elif wire_model.startswith("claude-"):
        budget = 4000
        effective_max = max(max_tok, budget + 4000)
        gen_cfg["maxOutputTokens"] = min(effective_max, 64000)
        gen_cfg["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingBudget": budget,
        }
    else:
        eff = str(payload.get("reasoning_effort") or "").lower()
        # Hermes levels normalize to provider-supported tiers. The highest
        # provider tier is high; xhigh/max/ultra intentionally clamp to it.
        if eff in ("xhigh", "max", "ultra", "high") or "-high" in wire_model or "-high" in str(model_name):
            budget = 16000
        elif eff in ("none", "minimal", "low") or "-low" in wire_model or "-low" in str(model_name):
            budget = 2000
        else:
            budget = 8000
        effective_max = max(max_tok, budget + 2048)
        gen_cfg["maxOutputTokens"] = min(effective_max, 65536)
        gen_cfg["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingBudget": budget,
        }

    ag_body = {
        "model": wire_model,
        "request": {
            "contents": contents,
            "generationConfig": gen_cfg,
        },
        "project": payload.get("project") or os.environ.get("AG_PROJECT") or _current_project(),
    }

    if system_parts:
        ag_body["request"]["systemInstruction"] = {"role": "system", "parts": system_parts}

    try:
        converted_tools = _build_tools(payload.get("tools"))
    except ValueError as exc:
        raise PoolError(str(exc), 400) from None
    if converted_tools:
        ag_body["request"]["tools"] = converted_tools

    return ag_body


def to_openai_response(ag_response, request_model=None):
    resp = ag_response.get("response") or ag_response
    candidates = resp.get("candidates") or []
    choices = []

    for idx, cand in enumerate(candidates):
        content_parts = (cand.get("content") or {}).get("parts") or []
        text_parts = []
        thought_parts = []
        tool_calls = []

        for p in content_parts:
            if not isinstance(p, dict):
                continue
            if p.get("thought") or (isinstance(p.get("text"), str) and p.get("thought")):
                thought_parts.append(p.get("text", ""))
            elif p.get("text"):
                text_parts.append(p["text"])
            elif p.get("functionCall"):
                fc = p["functionCall"]
                name = fc.get("name", "")
                args = fc.get("args", {})
                call_id = str(fc.get("id") or f"call_{secrets.token_hex(8)}")
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                    }
                })

        text = "".join(text_parts)
        thought = "".join(thought_parts)
        reason = cand.get("finishReason") or "STOP"
        finish_reason = "tool_calls" if tool_calls else {
            "STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "content_filter",
            "RECITATION": "content_filter", "BLOCKLIST": "content_filter",
            "PROHIBITED_CONTENT": "content_filter", "SPII": "content_filter",
        }.get(reason, "stop")

        msg = {"role": "assistant"}
        msg["content"] = text if text else ("" if tool_calls else None)
        if thought:
            msg["reasoning_content"] = thought
        if tool_calls:
            msg["tool_calls"] = tool_calls

        choices.append({
            "index": idx,
            "message": msg,
            "finish_reason": finish_reason,
        })

    if not choices:
        raise PoolError("Provider returned no candidates (blocked or malformed response)", 502)

    usage = resp.get("usageMetadata") or {}
    translated_usage = {}
    if 'promptTokenCount' in usage and 'candidatesTokenCount' in usage:
        output = usage['candidatesTokenCount'] + usage.get('thoughtsTokenCount', 0)
        translated_usage = {'prompt_tokens': usage['promptTokenCount'], 'completion_tokens': output,
            'total_tokens': usage.get('totalTokenCount', usage['promptTokenCount'] + output),
            'completion_tokens_details': {'reasoning_tokens': usage.get('thoughtsTokenCount', 0)}}
    return {
        "id": "chatcmpl-" + (resp.get("responseId") or str(int(time.time() * 1000))),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": clean_model_name(request_model or resp.get("modelVersion") or "gemini-3.8-flash"),
        "choices": choices,
        **( {"usage": translated_usage} if translated_usage else {} ),
    }


def _antigravity_headers(access):
    return {
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-Goog-Api-Client": X_GOOG_API_CLIENT,
        "Client-Metadata": CLIENT_METADATA,
    }


from pool_runtime import AccountPool, PoolError, record, retry_seconds, clean_model_name
from durable_requests import DurableRequestStore, request_key
from retry_policy import provider_attempts, retry_delay
AG_POOL = AccountPool('gemini')

# A provider outage is a transient infrastructure event, not a reason to
# abandon a live Hermes turn after one account cycle.  The default is 20
# upstream attempts; deployments may raise it but cannot lower it below 20.
def call_antigravity(ag_body, access=None):
    access = access or get_access_token()
    endpoints = [os.environ['AG_UPSTREAM_URL']] if os.environ.get('AG_UPSTREAM_URL') else [e + ANTIGRAVITY_PATH for e in ANTIGRAVITY_ENDPOINTS]
    last_status = 503
    for url in endpoints:
        req = urllib.request.Request(url, data=json.dumps(ag_body).encode(), headers=_antigravity_headers(access))
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
                if not isinstance(result, dict):
                    raise PoolError('Antigravity returned a non-object response', 502)
                return result
        except urllib.error.HTTPError as exc:
            status = exc.code
            last_status = status
            delay = retry_seconds(exc.headers)
            exc.close()
            if status == 404:
                continue # Deployment endpoint unavailable; try next, same account/model.
            if status in (401,403,429) or status < 500:
                error = PoolError(f'Antigravity upstream HTTP {status}',status)
                error.retry_after = delay
                raise error from None
        except OSError:
            continue
    raise PoolError('Antigravity endpoints are unavailable',last_status)


def call_with_retry(ag_body, attempts=None, on_success=None, sleep=None):
    """Call Antigravity with a real transient-outage retry budget.

    Account quota/auth failures rotate away from that account.  Provider 5xx
    and network failures keep retrying the same logical request across the
    available account set, including accounts temporarily marked by the
    circuit breaker.  This distinction prevents an outage from emptying the
    candidate list after the first pass.
    """
    model = ag_body['model']
    candidates = AG_POOL.candidates(model, include_cooldown=True)
    if not candidates:
        raise PoolError('No usable Antigravity account for this exact model; pool is empty', 503)
    configured = attempts
    if configured is None:
        configured = (os.environ.get('AIPOOL_GEMINI_MAX_ATTEMPTS') or
                      os.environ.get('AIPOOL_PROVIDER_MAX_ATTEMPTS'))
    total = provider_attempts(configured)
    sleeper = sleep or time.sleep
    last_status = 503
    quota_exhausted = set()
    outage_failures = 0

    for attempt in range(total):
        usable = [n for n in candidates if n not in quota_exhausted] or candidates
        number = usable[attempt % len(usable)]
        try:
            credentials = AG_POOL.credentials(number)
            body = deepcopy(ag_body)
            body['project'] = credentials.get('project_id') or body.get('project')
            result = call_antigravity(body, credentials['token']['access_token'])
            if on_success:
                on_success(number)
            return result
        except (PoolError, ValueError, OSError, RuntimeError) as exc:
            last_status = int(getattr(exc, 'status', 503) or 503)
            if last_status not in (401, 403, 429) and last_status < 500:
                raise
            if last_status in (401, 403, 429):
                quota_exhausted.add(number)
                AG_POOL.exhausted(number, model, getattr(exc, 'retry_after', 60), reason='account_failure')
                if len(quota_exhausted) >= len(candidates):
                    break
            else:
                outage_failures += 1
                AG_POOL.exhausted(number, model, min(5, max(1, outage_failures)), reason='provider_outage')
            if attempt + 1 < total:
                retry_after = getattr(exc, 'retry_after', None) if last_status in (401, 403, 429) else None
                sleeper(retry_delay(attempt, retry_after))
    raise PoolError(
        f'Antigravity provider did not recover after {total} attempts', last_status
    )


# ---------------------------------------------------------------------------
# HTTP server (OpenAI-compatible)
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            raise PoolError("Invalid Content-Length", 400) from None
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise PoolError("Invalid request size", 400)
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PoolError("Request body must be valid JSON", 400) from None
        if not isinstance(value, dict):
            raise PoolError("Request body must be a JSON object", 400)
        return value

    def _send_json(self, obj, status=200):
        self.close_connection = True
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, message, status=500):
        from error_taxonomy import classify, public_error
        classified = classify(message, status=status, stream_started=False)
        self._send_json({"error": public_error(classified)}, status)

    def _record_bridge_request(self, out_resp, req_model, pool_name='Gemini'):
        audit = getattr(self, '_audit', None)
        if audit is not None:
            from request_audit import attach_response
            attach_response(audit, out_resp)
        if audit is not None and getattr(self, '_request_started_at', None) is not None:
            audit['latency_ms'] = round((time.perf_counter() - self._request_started_at) * 1000, 2)
        record(pool_name, clean_model_name(req_model), out_resp.get('usage'), getattr(self, '_account_used', None), audit=audit)

    def _send_cached_stream(self, out, requested_model=None):
        """Deliver a buffered provider response as OpenAI SSE."""
        self.close_connection = True
        self._stream_started = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        choice = (out.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        model = requested_model or out.get('model', 'gemini-3.8-flash')
        base = {
            'id': out.get('id', 'chatcmpl-replay'), 'object': 'chat.completion.chunk',
            'created': out.get('created', int(time.time())), 'model': model,
        }
        reasoning = message.get('reasoning_content')
        if reasoning:
            self.wfile.write(('data: ' + json.dumps({**base, 'choices': [
                {'index': 0, 'delta': {'role': 'assistant', 'reasoning_content': reasoning}, 'finish_reason': None}
            ]}) + '\n\n').encode())
        if message.get('tool_calls'):
            delta = {'role': 'assistant', 'content': message.get('content'), 'tool_calls': [dict(tool, index=i) for i, tool in enumerate(message['tool_calls'])]}
            finish = 'tool_calls'
        else:
            delta = {'role': 'assistant', 'content': message.get('content') or ''}
            finish = None
        self.wfile.write(('data: ' + json.dumps({**base, 'choices': [
            {'index': 0, 'delta': delta, 'finish_reason': finish}
        ]}) + '\n\n').encode())
        if finish is None:
            self.wfile.write(('data: ' + json.dumps({**base, 'choices': [
                {'index': 0, 'delta': {}, 'finish_reason': choice.get('finish_reason', 'stop')}
            ]}) + '\n\n').encode())
        usage = out.get('usage')
        if usage:
            self.wfile.write(('data: ' + json.dumps({**base, 'choices': [], 'usage': usage}) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()

    def do_GET(self):
        from client_identity import authorize, is_loopback
        forwarded = self.headers.get('X-Real-IP') or self.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        peer = forwarded if forwarded else self.client_address[0]
        if not is_loopback(peer) and not authorize(self, 'Gemini'):
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in ("/v1/models", "/models"):
                self._send_json({"object": "list", "data": list_available_models()})
                return
            if path in ("/health", "/healthz"):
                self._send_json({"status": "ok"})
                return
            self._send_json({"error": {"message": "Not found", "type": "not_found"}}, 404)
        except Exception as e:
            self._send_error(e)

    def do_POST(self):
        from client_identity import authorize
        if not authorize(self, 'Gemini'):
            return
        from request_audit import attach_provider_payload, capture
        self._audit = None
        self._stream_started = False
        path = urllib.parse.urlparse(self.path).path
        if path not in ("/v1/chat/completions", "/chat/completions", "/v1/completions"):
            self._send_json({"error": {"message": "Not found", "type": "not_found"}}, 404)
            return
        payload = {}
        self._account_used = None
        self._durable_store = None
        self._durable_key = None
        self._durable_claimed = False
        started_at = time.perf_counter()
        self._request_started_at = started_at
        try:
            started_at = self._request_started_at = time.perf_counter()
            raw_payload = self._read_body()
            payload = raw_payload
            self._audit = capture(self, raw_payload)
            self._audit['provider_label'] = 'Gemini'
            self._audit['wire_input_bytes'] = int(self.headers.get('Content-Length', 0))
            if not isinstance(payload, dict) or not isinstance(payload.get('model'), str) or not payload['model']:
                payload = {}
                raise PoolError('A literal model ID is required',400)
            if not isinstance(payload.get('messages'), list):
                raise PoolError('messages must be an array', 400)
            if not all(isinstance(message, dict) for message in payload['messages']):
                raise PoolError('each message must be an object', 400)
            if 'tools' in payload and not isinstance(payload.get('tools'), list):
                raise PoolError('tools must be an array', 400)
            m = payload.get("model")
            self._requested_model = m

            # Validate client model permissions
            client_identity = getattr(self, '_client_identity', {})
            client_obj = client_identity.get('client_obj')
            if client_obj and client_obj.get('allowed_models'):
                clean_req = str(m).lower()
                clean_allowed = [str(mod).lower() for mod in client_obj['allowed_models']]
                if clean_req not in clean_allowed:
                    raise PoolError(f"Model {m} is not permitted for this API Key", 403)
            n_msgs = len(payload.get("messages", []))
            n_tools = len(payload.get("tools", []))
            stream = payload.get("stream", False)
            print(f"[bridge] POST model={m} msgs={n_msgs} tools={n_tools} stream={stream}", file=sys.stderr)
            ag_body = to_antigravity_body(payload)
            # The provider receives this nested Gemini body, so the Inspector
            # must display it rather than the pre-adapter client payload.
            attach_provider_payload(self._audit, ag_body)
            self._audit['wire_provider_bytes'] = len(json.dumps(ag_body, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            self._audit['wire_tokens_estimate'] = max(1, (self._audit['wire_provider_bytes'] + 3) // 4)

            # Journal the request before contacting the provider.  If this
            # process is restarted after accepting the request, the next
            # bridge instance can finish it and a caller retry can replay the
            # durable response instead of losing the turn.
            try:
                self._durable_store = DurableRequestStore()
                supplied_id = (self.headers.get('Idempotency-Key') or
                               self.headers.get('X-Request-ID') or
                               self.headers.get('X-Request-Id'))
                scope = (getattr(self, '_client_identity', {}).get('credential_id') or 'local')
                self._durable_key = request_key('Gemini', path, payload, supplied_id, client_scope=scope)
                state = self._durable_store.begin(
                    key=self._durable_key,
                    request_id=self._audit['request_id'],
                    provider='Gemini', model=m, endpoint=path, payload=payload,
                )
                if state['state'] == 'completed':
                    stored = json.loads(state['response_json'])
                    out = to_openai_response(stored, request_model=m)
                    self._audit['replayed_after_restart'] = True
                    self._record_bridge_request(out, m, "Gemini")
                    if stream:
                        self._send_cached_stream(out, requested_model=m)
                    else:
                        self._send_json(out, 200)
                    return
                if state['state'] == 'in_progress':
                    raise PoolError('Request is already being recovered; retry the same request', 503)
                self._durable_claimed = state['state'] == 'claimed'
            except PoolError:
                raise
            except ValueError as exc:
                raise PoolError(str(exc), 409) from None
            except (OSError, TypeError, sqlite3.Error) as exc:
                # Request durability is best-effort; an unavailable journal
                # must not turn a healthy provider into a gateway outage.
                print(f'[bridge] durable request journal unavailable: {type(exc).__name__}', file=sys.stderr)
                self._durable_store = None
                self._durable_key = None

            if stream:
                self._handle_stream(ag_body, requested_model=m,
                                    durable_store=self._durable_store, durable_key=self._durable_key)
                return

            ag_resp = self._call_with_retry(ag_body)
            if self._durable_store and self._durable_key:
                self._durable_store.complete(self._durable_key, ag_resp, 200)
            out = to_openai_response(ag_resp, request_model=m)
            self._audit['latency_ms'] = round((time.perf_counter() - started_at) * 1000, 2)
            self._record_bridge_request(out, m, "Gemini")
            self._send_json(out, 200)
        except Exception as e:
            print(f"[bridge] POST ERROR: {e}", file=sys.stderr)
            from error_taxonomy import classify
            status = getattr(e, 'status', 500)
            classified = classify(e, status=status, stream_started=False)
            if self._audit is not None:
                self._audit['error_code'] = classified.code.value
                self._audit['latency_ms'] = round((time.perf_counter() - started_at) * 1000, 2)
            if self._durable_store and self._durable_key and self._durable_claimed:
                self._durable_store.fail(
                    self._durable_key, classified.code.value,
                    retryable=(status >= 500 or isinstance(e, (OSError, TimeoutError))),
                )
            record('Gemini',payload.get('model'),None,getattr(self,'_account_used',None),'FAILED',audit=getattr(self,'_audit',None),error_code=classified.code.value)
            if not self._stream_started:
                self._send_error(e,status)
            else:
                self.close_connection = True

    def _handle_stream(self, ag_body, requested_model=None, durable_store=None, durable_key=None):
        ag_resp = self._call_with_retry(ag_body)
        out = to_openai_response(ag_resp, request_model=requested_model)
        if durable_store and durable_key:
            durable_store.complete(durable_key, ag_resp, 200)
        self._record_bridge_request(out, requested_model or out['model'], "Gemini")
        self._send_cached_stream(out, requested_model=requested_model)

    def _call_with_retry(self, ag_body, attempts=None):
        def succeeded(number):
            self._account_used = number
            try:
                AG_POOL.promote(number)
            except Exception:
                print('[gemini-bridge] Active-account synchronization failed after upstream success',file=sys.stderr)
        return call_with_retry(ag_body, attempts=attempts, on_success=succeeded)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def _recovery_worker(store):
    """Finish requests left in progress by a previous bridge process."""
    store.requeue_in_progress()
    while True:
        rows = store.pending(limit=4)
        if not rows:
            time.sleep(2)
            continue
        for row in rows:
            key = row['request_key']
            claimed = store.claim_pending(key)
            if not claimed:
                continue
            try:
                payload = json.loads(claimed['payload_json'] or '{}')
                ag_body = to_antigravity_body(payload)
                result = call_with_retry(ag_body)
                store.complete(key, result, 200)
                print(f"[gemini-bridge] recovered durable request model={claimed['model']}", flush=True)
            except Exception as exc:
                status = int(getattr(exc, 'status', 503) or 503)
                store.fail(key, type(exc).__name__, retryable=status >= 500)
                print(f"[gemini-bridge] durable recovery deferred: {type(exc).__name__}", file=sys.stderr, flush=True)


def main():
    try:
        get_access_token()
        print("[bridge] Token OK")
    except Exception as e:
        print(f"[bridge] WARNING: Token check failed: {e}", file=sys.stderr)

    recovery_store = DurableRequestStore()
    recovery_store.initialize()
    threading.Thread(target=_recovery_worker, args=(recovery_store,), daemon=True,
                     name='durable-request-recovery').start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[bridge] OpenAI-compatible proxy listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] exiting")


if __name__ == "__main__":
    main()
