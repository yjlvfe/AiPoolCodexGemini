"""Redacted client/provider provenance for the dashboard; never modifies requests."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid

from artifact_store import put as store_artifact
from typing import Any


SECRET = re.compile(
    r"(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"password|secret|cookie|credential)",
    re.I,
)
_PROMPT_FIELDS = ("messages", "input", "prompt", "instructions", "system")


def redact(value: Any) -> Any:
    """Redact credential-shaped keys and common inline bearer/API tokens."""
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
        value = re.sub(r"\b(?:sk|rk)-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
        value = re.sub(
            r"(?i)((?:api[_-]?key|password|access_token|refresh_token|id_token|secret)\s*[:=]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            value,
        )
    return value


def detect_application(headers, client_identity=None):
    ua = headers.get("User-Agent", "") or ""
    client_hdr = headers.get("X-AI-Client", "") or headers.get("X-Client", "") or ""
    if isinstance(client_identity, dict) and client_identity.get("authenticated_client"):
        return client_identity["authenticated_client"]
    if "Hermes" in client_hdr or "hermes" in ua.lower():
        return "Hermes Agent"
    if "OpenClaw" in client_hdr or "openclaw" in ua.lower():
        return "OpenClaw Gateway"
    if "codex" in ua.lower() or "codex_cli" in ua.lower():
        return "OpenAI Codex"
    if "python-requests" in ua.lower() or "OpenAI/Python" in ua:
        return "Hermes Agent"
    return client_hdr or "Direct API"


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _payload_text(value: Any, label: str) -> str:
    """Keep the readable messages and the exact redacted wire payload distinct.

    The old inspector rendered both sides through format_human_prompt(). That
    intentionally normalizes different protocols, so IN and OUT could look
    identical even when the actual JSON sent upstream had been transformed.
    """
    raw = _json_text(redact(value))
    prefix = f"=== {label} PAYLOAD (EXACT JSON) ==="
    # Do not append a normalized rendering here: it made IN and OUT appear
    # identical even when their wire payloads were different. The inspector
    # receives one authoritative, protocol-specific payload per side.
    return prefix + "\n" + raw


def _content_text(value: Any) -> str:
    """Turn OpenAI/Gemini content parts into readable text without binary data."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        chunks = [_content_text(part) for part in value]
        return "\n".join(chunk for chunk in chunks if chunk).strip()
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"].strip()
        if isinstance(value.get("content"), (str, list, dict)):
            nested = _content_text(value["content"])
            if nested:
                return nested
        if isinstance(value.get("parts"), list):
            nested = _content_text(value["parts"])
            if nested:
                return nested
        kind = str(value.get("type", "")).lower()
        if kind in {"image_url", "input_image", "inline_data", "inline_data_part", "image"}:
            mime = value.get("mime_type") or value.get("mimeType")
            return f"[image{(': ' + str(mime)) if mime else ''} omitted]"
        return _json_text(value)
    return str(value)


def _role_label(role: Any) -> str:
    normalized = str(role or "unknown").upper()
    return {
        "USER": "👤 USER",
        "ASSISTANT": "🤖 ASSISTANT",
        "MODEL": "🤖 ASSISTANT",
        "SYSTEM": "⚙️ SYSTEM",
        "DEVELOPER": "⚙️ DEVELOPER",
        "TOOL": "🛠️ TOOL",
    }.get(normalized, f"📌 {normalized}")


def _append_message(blocks: list[str], role: Any, content: Any) -> None:
    text = _content_text(content)
    if text:
        blocks.append(f"=== {_role_label(role)} ===\n{text}")


def _append_input(blocks: list[str], value: Any) -> None:
    """Render Responses API input items while retaining non-message events."""
    if isinstance(value, list):
        unstructured = []
        for item in value:
            if isinstance(item, dict) and (item.get("role") or item.get("type") == "message"):
                _append_message(blocks, item.get("role", "user"), item.get("content", item))
            elif isinstance(item, dict) and item.get("type") in {"input_text", "output_text"}:
                _append_message(blocks, "user" if item["type"] == "input_text" else "assistant", item.get("text", item))
            else:
                unstructured.append(item)
        if unstructured:
            blocks.append("=== 📥 INPUT EVENTS ===\n" + _json_text(unstructured))
    elif value is not None:
        blocks.append(f"=== 📥 INPUT ===\n{_content_text(value)}")


def _append_provider_request(blocks: list[str], request: dict[str, Any]) -> None:
    """Render the nested Gemini provider request shape."""
    system_instruction = request.get("systemInstruction")
    if system_instruction is not None:
        _append_message(blocks, "system", system_instruction)
    contents = request.get("contents")
    if isinstance(contents, list):
        for item in contents:
            if isinstance(item, dict):
                _append_message(blocks, item.get("role", "user"), item.get("parts", item.get("content", item)))
            else:
                _append_message(blocks, "user", item)
    if isinstance(request.get("tools"), list):
        blocks.append(
            f"=== 🛠️ TOOLS ({len(request['tools'])} declarations) ===\n"
            + _json_text(request["tools"])
        )


def format_human_prompt(payload: Any) -> str:
    """Render prompt-bearing fields from either client or provider payloads.

    Do not return early after ``messages``: Responses requests can carry both
    system instructions and messages, and Gemini provider requests carry a
    nested ``request`` object.  Omitting either side made the Inspector lie
    about the actual prompt flow.
    """
    if not isinstance(payload, dict):
        return ""
    blocks: list[str] = []

    for key in ("instructions", "system"):
        if key in payload:
            _append_message(blocks, "system", payload[key])

    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                _append_message(blocks, message.get("role", "unknown"), message.get("content", message))
            else:
                _append_message(blocks, "unknown", message)

    if "input" in payload:
        _append_input(blocks, payload["input"])
    if "prompt" in payload:
        _append_message(blocks, "user", payload["prompt"])

    provider_request = payload.get("request")
    if isinstance(provider_request, dict):
        _append_provider_request(blocks, provider_request)

    if isinstance(payload.get("tools"), list) and not isinstance(provider_request, dict):
        blocks.append(
            f"=== 🛠️ TOOLS ({len(payload['tools'])} functions) ===\n"
            + _json_text(payload["tools"])
        )

    return "\n\n".join(blocks).strip()


def _prompt_limit() -> int:
    try:
        return min(8_192, max(0, int(os.environ.get("AIPOOL_AUDIT_PROMPTS_MAX_CHARS", "8192"))))
    except (TypeError, ValueError):
        return 8_192


def _bounded_prompt(value: str, limit: int) -> tuple[str, bool]:
    return value[:limit], len(value) > limit



def capture(handler, raw_payload, provider_payload=None):
    """Capture client metadata and prompt text before the provider call.

    ``provider_payload`` is optional for compatibility with callers/tests.  A
    bridge should call :func:`attach_provider_payload` after its adapter has
    built the exact upstream body.
    """
    raw = raw_payload if isinstance(raw_payload, dict) else {}
    provider = provider_payload if isinstance(provider_payload, dict) else raw
    canonical = json.dumps(raw_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    # IN is the exact redacted client payload; OUT is replaced later by the
    # final provider payload through attach_provider_payload(). Never derive
    # both fields from the same normalized representation.
    raw_text = _payload_text(raw, "IN")
    provider_text = _payload_text(provider, "OUT")
    limit = _prompt_limit()
    raw_text, raw_truncated = _bounded_prompt(raw_text, limit)
    provider_text, provider_truncated = _bounded_prompt(provider_text, limit)
    client_address = getattr(handler, "client_address", ("",))[0]
    request_path = str(getattr(handler, "path", "")).split("?", 1)[0][:256]
    headers = getattr(handler, "headers", {})
    artifact_refs = []
    messages = raw.get("messages") if isinstance(raw, dict) else None
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "tool":
                continue
            content = message.get("content")
            if isinstance(content, str) and len(content.encode("utf-8")) > 4096:
                try:
                    artifact_refs.append(store_artifact("tool_output", content))
                except (OSError, TypeError, ValueError):
                    pass
    audit = {
        "request_id": str(uuid.uuid4()),
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "peer_ip": str(client_address),
        "endpoint": request_path,
        "client_label": detect_application(headers, getattr(handler, "_client_identity", None)),
        "user_agent": redact(headers.get("User-Agent", ""))[:256],
        "prompt_capture_status": "recorded",
        "prompt_text": redact(raw_text),
        "provider_prompt_text": redact(provider_text),
        "prompt_capture_truncated": raw_truncated or provider_truncated,
        "artifact_refs": artifact_refs,
    }
    return audit


def attach_response(audit: dict[str, Any], response_payload: Any) -> dict[str, Any]:
    """Record the assistant response returned by the provider as OUT."""
    if not isinstance(audit, dict):
        return audit
    text = _response_text(response_payload)
    bounded, truncated = _bounded_prompt(text, _prompt_limit())
    audit['response_text'] = redact(bounded)
    audit['response_capture_truncated'] = truncated
    return audit


def response_text(audit: dict[str, Any]) -> str:
    """Return the stored assistant output, never the outbound request payload."""
    if not isinstance(audit, dict):
        return ''
    return str(audit.get('response_text') or '')


def _response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload or '')
    choices = payload.get('choices') or []
    blocks = []
    for choice in choices:
        message = choice.get('message') if isinstance(choice, dict) else None
        if isinstance(message, dict):
            content = message.get('content')
            if content:
                blocks.append(_content_text(content))
            reasoning = message.get('reasoning_content')
            if reasoning:
                blocks.append('=== REASONING ===\\n' + _content_text(reasoning))
            for call in message.get('tool_calls') or []:
                blocks.append('=== TOOL CALL ===\\n' + _json_text(redact(call)))
    if blocks:
        return '\\n\\n'.join(blocks).strip()
    return _json_text(redact(payload))


def attach_provider_payload(audit: dict[str, Any], provider_payload: Any) -> dict[str, Any]:
    """Record the exact final provider request body for diagnostics."""
    if not isinstance(audit, dict):
        return audit
    # OUT must contain the exact final redacted provider payload, not the
    # normalized message rendering used for IN. This is what makes protocol
    # transformations visible in the inspector (for example OpenAI messages
    # versus Gemini request/contents/parts).
    text = _payload_text(provider_payload, "OUT")
    bounded, truncated = _bounded_prompt(text, _prompt_limit())
    audit["provider_prompt_text"] = bounded
    rendered = bounded
    audit["provider_prompt_text"] = rendered
    audit["prompt_capture_truncated"] = bool(audit.get("prompt_capture_truncated")) or truncated
    return audit
