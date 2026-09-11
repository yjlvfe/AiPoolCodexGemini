"""Provider-neutral canonical request model.

The model is deliberately loss-aware: unknown top-level fields and message
metadata are retained rather than silently discarded by an adapter.
"""
from dataclasses import dataclass, field
from typing import Any
import copy
import json

KNOWN_FIELDS = {
    "model", "messages", "input", "instructions", "tools", "tool_choice",
    "stream", "stream_options", "temperature", "top_p", "max_tokens",
    "max_completion_tokens", "frequency_penalty", "presence_penalty", "n",
    "stop", "response_format", "metadata", "user"
}

@dataclass
class CanonicalRequest:
    model: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    instructions: str | list[str] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: Any = None
    stream: bool = False
    options: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CanonicalRequest":
        if not isinstance(payload, dict):
            raise ValueError("request payload must be an object")
        model = payload.get("model")
        if not isinstance(model, str) or not model or model != model.strip():
            raise ValueError("an exact model ID is required")
        messages = payload.get("messages", [])
        if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
            raise ValueError("messages must be a list of objects")
        tools = payload.get("tools", [])
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise ValueError("tools must be a list of objects")
        instructions = payload.get("instructions")
        if instructions is not None and not (
            isinstance(instructions, str)
            or (isinstance(instructions, list) and all(isinstance(item, str) for item in instructions))
        ):
            raise ValueError("instructions must be text or an array of text")
        stream = payload.get("stream", False)
        if not isinstance(stream, bool):
            raise ValueError("stream must be boolean")
        options = {k: copy.deepcopy(payload[k]) for k in KNOWN_FIELDS
                   if k not in {"model", "messages", "instructions", "tools", "tool_choice"}
                   and k in payload}
        extensions = {k: copy.deepcopy(v) for k, v in payload.items() if k not in KNOWN_FIELDS}
        return cls(model=model, messages=copy.deepcopy(messages),
                   instructions=copy.deepcopy(payload.get("instructions")),
                   tools=copy.deepcopy(tools),
                   tool_choice=copy.deepcopy(payload.get("tool_choice")),
                   stream=stream, options=options,
                   extensions=extensions)

    def to_payload(self) -> dict[str, Any]:
        out = {"model": self.model, "messages": copy.deepcopy(self.messages),
               **copy.deepcopy(self.options), **copy.deepcopy(self.extensions)}
        if self.instructions is not None:
            out["instructions"] = copy.deepcopy(self.instructions)
        if self.tools:
            out["tools"] = copy.deepcopy(self.tools)
        if self.tool_choice is not None:
            out["tool_choice"] = copy.deepcopy(self.tool_choice)
        out["stream"] = self.stream
        return out

    def canonical_json_bytes(self) -> bytes:
        return json.dumps(self.to_payload(), ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")


def wire_payload_bytes(payload: dict[str, Any]) -> int:
    """Return bytes of the exact compact JSON representation sent on wire."""
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
