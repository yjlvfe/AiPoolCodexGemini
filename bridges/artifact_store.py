"""Bounded local artifact storage for large history/tool outputs.

Artifacts are content-addressed, redacted only by the caller, and referenced by
stable IDs. Large tool output remains available through its integrity-checked ref.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "dashboard" / "artifacts"
MAX_ARTIFACT_BYTES = 2_000_000


def _root() -> Path:
    path = Path(os.environ.get("AIPOOL_ARTIFACT_DIR", DEFAULT_ROOT))
    if path.is_symlink():
        raise ValueError("artifact root must not be a symlink")
    ancestor = path.parent
    while ancestor != ancestor.parent:
        if ancestor.is_symlink():
            raise ValueError("artifact root ancestor must not be a symlink")
        ancestor = ancestor.parent
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        raise ValueError("artifact root must be a directory")
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def put(kind: str, value: Any) -> dict[str, Any]:
    if not isinstance(kind, str) or not kind or not isinstance(value, (str, dict, list)):
        raise ValueError("artifact kind and JSON-compatible value are required")
    raw = value.encode("utf-8") if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact exceeds the configured size limit")
    digest = hashlib.sha256(raw).hexdigest()
    ref = f"artifact:{kind}:{digest}"
    path = _root() / digest
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("unsafe artifact path")
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("artifact integrity check failed")
    else:
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("artifact integrity check failed")
        else:
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    path.unlink()
                except OSError:
                    pass
                raise
    return {"ref": ref, "kind": kind, "sha256": digest, "bytes": len(raw)}


def get(ref: str) -> Any:
    if not isinstance(ref, str) or not ref.startswith("artifact:"):
        raise ValueError("invalid artifact reference")
    parts = ref.split(":", 2)
    if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{64}", parts[2]):
        raise ValueError("invalid artifact reference")
    path = _root() / parts[2]
    if path.is_symlink() or not path.is_file():
        raise ValueError("unsafe artifact path")
    raw = path.read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != parts[2]:
        raise ValueError("artifact integrity check failed")
    try:
        return json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8")
