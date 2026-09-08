#!/usr/bin/env python3
"""
Antigravity Gateway Bridge
=========================
Converts Google Account token into OpenAI-compatible endpoints (chat/completions)
to use Gemini subscriptions inside Hermes without manual API keys.
"""

import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"cli"))
from config_env import load
load()
import time
import datetime
import urllib.request
import urllib.parse
import urllib.error
import threading
import http.server
import socketserver
import sys
import shutil
import secrets
from copy import deepcopy

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HOST = os.environ.get("AG_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AG_BRIDGE_PORT", "8123"))

# Antigravity OAuth client credentials
CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "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"
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

    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": rt,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Token refresh failed: HTTP {e.code}: {e.read()[:200]}")
    except Exception as e:
        raise RuntimeError(f"Token refresh network error: {e}")

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

# Clean list of public model IDs exposed via /v1/models (no ugly tiered suffixes)
_CANDIDATE_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low",
    "gemini-3.1-pro",
    "gemini-pro-agent",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "gpt-oss-120b",
]

_available = set(_CANDIDATE_MODELS)
_discovery_lock = threading.Lock()
_DISCOVERY_TTL = 1800
_last_probe = 0.0
_probe_running = False


def _send_model(model):
    if not isinstance(model, str) or not model or model != model.strip():
        raise ValueError('An exact model ID is required')
    return MODEL_SEND_MAP.get(model, model)


def list_available_models():
    out = []
    with _discovery_lock:
        for name in _CANDIDATE_MODELS:
            if name in _available:
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
def _clean_schema(schema):
    if not isinstance(schema, dict):
        return {"type": "OBJECT", "properties": {}}

    banned = {
        "$schema", "$defs", "definitions", "additionalProperties",
        "patternProperties", "unevaluatedProperties", "propertyNames",
        "minProperties", "maxProperties", "title", "default", "examples",
        "anyOf", "oneOf", "allOf", "$ref", "minimum", "maximum", "minItems", "maxItems",
        "exclusiveMinimum", "exclusiveMaximum", "format", "pattern"
    }

    def sanitize(node):
        if not isinstance(node, dict):
            return {"type": "STRING"}

        out = {}
        for k, v in node.items():
            if k in banned or k in ("type", "properties", "required"):
                continue
            if isinstance(v, dict):
                out[k] = sanitize(v)
            elif isinstance(v, list):
                if k == "enum":
                    out[k] = [str(x) for x in v if isinstance(x, (str, int, float, bool))]
                else:
                    out[k] = [sanitize(x) if isinstance(x, dict) else x for x in v]
            elif isinstance(v, (int, float, bool, str)):
                out[k] = v

        raw_t = node.get("type")
        if isinstance(raw_t, list):
            non_null = [str(x) for x in raw_t if str(x).lower() != "null"]
            raw_t = non_null[0] if non_null else "string"
        t = str(raw_t or "").upper()

        if t in ("STR", "TEXT", "STRING"):
            out["type"] = "STRING"
        elif t in ("INT", "INTEGER"):
            out["type"] = "INTEGER"
        elif t in ("FLOAT", "NUMBER"):
            out["type"] = "NUMBER"
        elif t in ("BOOL", "BOOLEAN"):
            out["type"] = "BOOLEAN"
        elif t in ("ARRAY", "LIST"):
            out["type"] = "ARRAY"
            if "items" not in node or not isinstance(node["items"], dict):
                out["items"] = {"type": "STRING"}
            else:
                out["items"] = sanitize(node["items"])
        elif t in ("OBJ", "OBJECT", "DICT") or "properties" in node:
            out["type"] = "OBJECT"
            raw_props = node.get("properties")
            if not isinstance(raw_props, dict):
                raw_props = {}
            cleaned_props = {}
            for pk, pv in raw_props.items():
                if pk == "type":
                    continue
                if isinstance(pv, dict):
                    cleaned_props[str(pk)] = sanitize(pv)
                else:
                    cleaned_props[str(pk)] = {"type": "STRING"}
            out["properties"] = cleaned_props
            if "required" in node:
                req = node.get("required")
                if isinstance(req, list):
                    out["required"] = [r for r in req if isinstance(r, str) and r in cleaned_props]
        else:
            out["type"] = "STRING"

        return out

    return sanitize(deepcopy(schema))


def _build_tools(tools):
    declarations = []
    for idx, tool in enumerate(tools or []):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function") or {}
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        cleaned = _clean_schema(fn.get("parameters"))
        if idx == 11 or len(declarations) == 11:
            print(f"[bridge] TOOL 11 NAME={fn.get('name')}: cleaned={json.dumps(cleaned)}", file=sys.stderr)
        declarations.append({
            "name": fn["name"],
            "description": fn.get("description") or "",
            "parameters": cleaned,
        })
    return [{"functionDeclarations": declarations}] if declarations else None


# ---------------------------------------------------------------------------
# Request & Response Conversion
# ---------------------------------------------------------------------------
def to_antigravity_body(payload):
    messages = payload.get("messages", [])
    model_name = payload.get("model") or "gemini-3.8-flash"
    wire_model = _send_model(model_name)

    system_parts = []
    contents = []
    call_names = {}

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        if role in ("system", "developer"):
            if isinstance(content, str) and content.strip():
                system_parts.append({"text": content})
            continue

        if role == "user":
            parts = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text" and block.get("text"):
                            parts.append({"text": block["text"]})
                        elif block.get("type") in ("image_url", "image"):
                            url = (block.get("image_url") or {}).get("url", "")
                            if "data:" in url and ";base64," in url:
                                meta, data = url.split(",", 1)
                                mime = meta[5:].split(";", 1)[0] or "image/png"
                                parts.append({"inlineData": {"mimeType": mime, "data": data}})
            else:
                text_str = str(content) if content is not None else ""
                if text_str.strip():
                    parts.append({"text": text_str})
            if parts:
                contents.append({"role": "user", "parts": parts})

        elif role == "assistant":
            parts = []
            if isinstance(content, str) and content.strip():
                parts.append({"text": content})
            tool_calls = m.get("tool_calls") or []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name")
                if not name:
                    continue
                args_raw = fn.get("arguments", {})
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw) if args_raw.strip() else {}
                    except Exception:
                        args = {"value": args_raw}
                else:
                    args = args_raw or {}
                call_id = str(tc.get("id") or tc.get("call_id") or "")
                function_call = {"name": name, "args": args}
                if call_id:
                    function_call["id"] = call_id
                part = {"functionCall": function_call}
                part["thoughtSignature"] = SKIP_THOUGHT_SIGNATURE
                parts.append(part)
                if call_id:
                    call_names[call_id] = name
            if parts:
                contents.append({"role": "model", "parts": parts})

        elif role == "tool":
            name = m.get("name") or call_names.get(str(m.get("tool_call_id") or "")) or "tool"
            out_text = str(content) if content is not None else ""
            call_id = str(m.get("tool_call_id") or "")
            function_response = {"name": name, "response": {"output": out_text}}
            if call_id:
                function_response["id"] = call_id
            part = {"functionResponse": function_response}
            if contents and contents[-1].get("role") == "user" and any("functionResponse" in p for p in contents[-1].get("parts", [])):
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})

    if not contents:
        contents.append({"role": "user", "parts": [{"text": "Continue."}]})

    max_tok = int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 65536)
    gen_cfg = {}
    temp = payload.get("temperature")
    if temp is not None:
        gen_cfg["temperature"] = float(temp)

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
        if eff == "high" or "-high" in wire_model or "-high" in str(model_name):
            budget = 16000
        elif eff == "low" or "-low" in wire_model or "-low" in str(model_name):
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

    converted_tools = _build_tools(payload.get("tools"))
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
        finish_reason = "tool_calls" if tool_calls else (cand.get("finishReason") or "stop").lower()
        if finish_reason == "stop":
            finish_reason = "stop"

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
        choices.append({
            "index": 0,
            "message": {"role": "assistant", "content": ""},
            "finish_reason": "stop",
        })

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
AG_POOL = AccountPool('antigravity')


def call_antigravity(ag_body, access=None):
    access = access or get_access_token()
    endpoints = [os.environ['AG_UPSTREAM_URL']] if os.environ.get('AG_UPSTREAM_URL') else [e + ANTIGRAVITY_PATH for e in ANTIGRAVITY_ENDPOINTS]
    last_status = 503
    for url in endpoints:
        req = urllib.request.Request(url, data=json.dumps(ag_body).encode(), headers=_antigravity_headers(access))
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode())
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


# ---------------------------------------------------------------------------
# HTTP server (OpenAI-compatible)
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, message, status=500):
        self._send_json({"error": {"message": str(message), "type": "antigravity_bridge"}}, status)

    def _record_bridge_request(self, out_resp, req_model, pool_name='Antigravity'):
        record(pool_name, clean_model_name(req_model), out_resp.get('usage'), getattr(self, '_account_used', None))

    def do_GET(self):
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
        path = urllib.parse.urlparse(self.path).path
        if path not in ("/v1/chat/completions", "/chat/completions", "/v1/completions"):
            self._send_json({"error": {"message": "Not found", "type": "not_found"}}, 404)
            return
        payload = {}
        self._account_used = None
        try:
            payload = self._read_body()
            if not isinstance(payload, dict) or not isinstance(payload.get('model'), str) or not payload['model']:
                payload = {}
                raise PoolError('A literal model ID is required',400)
            m = payload.get("model")
            n_msgs = len(payload.get("messages", []))
            n_tools = len(payload.get("tools", []))
            stream = payload.get("stream", False)
            print(f"[bridge] POST model={m} msgs={n_msgs} tools={n_tools} stream={stream}", file=sys.stderr)
            ag_body = to_antigravity_body(payload)

            if stream:
                self._handle_stream(ag_body, requested_model=m)
                return

            ag_resp = self._call_with_retry(ag_body)
            out = to_openai_response(ag_resp, request_model=m)
            self._record_bridge_request(out, m, "Antigravity")
            self._send_json(out, 200)
        except Exception as e:
            print(f"[bridge] POST ERROR: {e}", file=sys.stderr)
            record('Antigravity',payload.get('model'),None,getattr(self,'_account_used',None),'FAILED')
            self._send_error(e,getattr(e,'status',500))

    def _handle_stream(self, ag_body, requested_model=None):
        ag_resp = self._call_with_retry(ag_body)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
        except Exception as e:
            print(f"[bridge] stream header error: {e}", file=sys.stderr)
            return

        try:
            out = to_openai_response(ag_resp, request_model=requested_model)
            choice = (out.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content")
            tool_calls = msg.get("tool_calls")
            model = requested_model or out.get("model", "gemini-3.8-flash")
            created = out.get("created", int(time.time()))
            rid = out.get("id", "chatcmpl")
            print(f"[bridge] stream output text_len={len(content)} tool_calls={len(tool_calls or [])}", file=sys.stderr)

            if reasoning:
                chunk_r = {
                    "id": rid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": reasoning}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk_r)}\n\n".encode("utf-8"))
                self.wfile.flush()

            if tool_calls:
                chunk_t = {
                    "id": rid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": content or None, "tool_calls": tool_calls}, "finish_reason": "tool_calls"}],
                }
                self.wfile.write(f"data: {json.dumps(chunk_t)}\n\n".encode("utf-8"))
                self.wfile.flush()
            else:
                chunk_c = {
                    "id": rid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk_c)}\n\n".encode("utf-8"))
                self.wfile.flush()

                chunk_stop = {
                    "id": rid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                self.wfile.write(f"data: {json.dumps(chunk_stop)}\n\n".encode("utf-8"))
                self.wfile.flush()

            usage_dict = out.get("usage") or {}
            self._record_bridge_request(out, requested_model or model, "Antigravity")
            if usage_dict:
                chunk_usage = {
                    "id": rid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [],
                    "usage": usage_dict
                }
                self.wfile.write(f"data: {json.dumps(chunk_usage)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception as e:
            print(f"[bridge] stream body error: {e}", file=sys.stderr)
            chunk_err = {
                "id": "chatcmpl-err",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "gemini-3.8-flash",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": f"Error: {e}"}, "finish_reason": "stop"}],
            }
            try:
                self.wfile.write(f"data: {json.dumps(chunk_err)}\n\ndata: [DONE]\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

    def _call_with_retry(self, ag_body, attempts=None):
        model = ag_body['model']
        candidates = AG_POOL.candidates(model)
        last_status = 429
        for number in candidates:
            try:
                credentials = AG_POOL.credentials(number)
                body = deepcopy(ag_body)
                body['project'] = credentials.get('project_id') or body.get('project')
                result = call_antigravity(body, credentials['token']['access_token'])
                self._account_used = number
                try:
                    AG_POOL.promote(number)
                except Exception:
                    print('[gemini-bridge] Active-account synchronization failed after upstream success',file=sys.stderr)
                return result
            except (PoolError,ValueError,OSError) as exc:
                last_status = getattr(exc,'status',401)
                if last_status not in (401,403,429,503):
                    raise
                AG_POOL.exhausted(number,model,getattr(exc,'retry_after',60))
        raise PoolError('No usable Antigravity account for this exact model; pool quota/authentication exhausted',last_status)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    try:
        get_access_token()
        print("[bridge] Token OK")
    except Exception as e:
        print(f"[bridge] WARNING: Token check failed: {e}", file=sys.stderr)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[bridge] OpenAI-compatible proxy listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] exiting")


if __name__ == "__main__":
    main()
