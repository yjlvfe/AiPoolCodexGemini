"""Deduplicated tool schema registry; preserves callable semantics."""
import copy
import hashlib
import json
from typing import Any


def _canonical(tool: dict[str, Any]) -> str:
    return json.dumps(tool, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

class ToolRegistry:
    def __init__(self):
        self._schemas: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, str] = {}

    def register(self, tool: dict[str, Any]) -> str:
        if not isinstance(tool, dict):
            raise ValueError("tool schema must be an object")
        fn = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
            raise ValueError("tool function.name is required")
        digest = hashlib.sha256(_canonical(tool).encode()).hexdigest()[:16]
        existing = self._aliases.get(fn["name"])
        if existing and existing != digest:
            raise ValueError(f"conflicting schemas registered for tool {fn['name']}")
        self._schemas.setdefault(digest, copy.deepcopy(tool))
        self._aliases[fn["name"]] = digest
        return digest

    def deduplicate(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for tool in tools or []:
            digest = self.register(tool)
            if digest not in seen:
                result.append(copy.deepcopy(self._schemas[digest]))
                seen.add(digest)
        return result

    def resolve(self, name: str) -> dict[str, Any] | None:
        digest = self._aliases.get(name)
        return copy.deepcopy(self._schemas[digest]) if digest else None
