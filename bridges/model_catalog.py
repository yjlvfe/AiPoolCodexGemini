"""Dynamic model catalog discovery and safe synchronization primitives.

The bridge model lists are sourced from the provider's live catalog (or the
provider-owned cache for Codex).  This module deliberately has no embedded
model allow-list: a provider update can therefore surface without a code
change.  A last-known-good disk cache is used only when a live refresh fails.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable


DEFAULT_TTL_SECONDS = 300.0
DEFAULT_DISCOVERY_TIMEOUT = 8.0
_PUBLIC_GEMINI_PREFIXES = ("gemini-", "claude-", "gpt-oss-")


class CatalogError(RuntimeError):
    """Raised when no live catalog or last-known-good catalog is available."""


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def catalog_cache_dir() -> Path:
    return Path(
        os.environ.get("AIPOOL_MODEL_CACHE_DIR", "/var/lib/aipool/runtime/model-catalog")
    ).expanduser()


def normalize_models(models: Iterable[Any]) -> list[str]:
    """Return stable, unique model IDs while rejecting malformed entries."""
    if isinstance(models, (set, frozenset)):
        models = sorted(models)
    found: set[str] = set()
    ordered: list[str] = []
    for item in models or ():
        if isinstance(item, dict):
            value = item.get("id") or item.get("slug") or item.get("model") or item.get("name")
        else:
            value = item
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value.startswith("models/"):
            value = value[7:]
        if not value or any(char.isspace() for char in value):
            continue
        if value not in found:
            found.add(value)
            ordered.append(value)
    # Preserve provider order in the written catalog; callers compare sets when
    # deciding whether an update is needed.
    return ordered


def _clean_gemini_id(value: str) -> str:
    value = value.strip()
    if value.startswith("models/"):
        value = value[7:]
    # Antigravity uses a private `-tiered` wire suffix for some public IDs.
    # The bridge adds the suffix again when the upstream catalog proves it is
    # required, so the public/OpenAI-compatible ID stays clean.
    if value.endswith("-tiered"):
        value = value[: -len("-tiered")]
    return value


def extract_gemini_catalog(payload: Any) -> tuple[list[str], dict[str, str]]:
    """Extract public Gemini IDs and their observed upstream wire IDs.

    The current upstream response is ``{"models": {id: metadata}}``.  A list
    shape is accepted as a forward-compatible fallback.  Deprecated IDs and
    private UI-only ``chat_*``/``tab_*`` entries are not exposed as chat models.
    """
    if not isinstance(payload, dict):
        raise CatalogError("Gemini model catalog is not a JSON object")
    raw = payload.get("models")
    if isinstance(raw, dict):
        raw_ids = list(raw.keys())
    elif isinstance(raw, list):
        raw_ids = raw
    else:
        raise CatalogError("Gemini model catalog has no models map")

    deprecated = set(normalize_models(payload.get("deprecatedModelIds") or []))
    public: list[str] = []
    seen_public: set[str] = set()
    wire: dict[str, str] = {}

    # Strict whitelist allowed by user:
    # 1. gemini-3.6-flash to gemini-3.8-flash (no high/med/low subvariants)
    # 2. gemini-3.1-pro
    # 3. claude models (e.g. claude-opus-4-6, claude-sonnet-4-6)
    # 4. gpt models (e.g. gpt-oss-120b)
    def _is_curated_model(m_id: str) -> bool:
        if m_id.startswith("claude-") or m_id.startswith("gpt-"):
            return True
        if m_id in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.1-pro", "gemini-3.1-pro-low"):
            return True
        return False

    for item in raw_ids:
        if isinstance(item, dict):
            upstream = item.get("id") or item.get("name") or item.get("model")
        else:
            upstream = item
        if not isinstance(upstream, str):
            continue
        upstream = upstream.strip()
        public_id = _clean_gemini_id(upstream)
        if not public_id or upstream in deprecated or public_id in deprecated:
            continue
        if not public_id.startswith(_PUBLIC_GEMINI_PREFIXES):
            continue
        if not _is_curated_model(public_id):
            continue
        if public_id not in seen_public:
            public.append(public_id)
            seen_public.add(public_id)
        wire.setdefault(public_id, upstream)
    # Desired authoritative ordering: Newest to Oldest
    ORDER_PREFERENCE = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.1-pro",
        "gemini-3.1-pro-low",
        "claude-opus-4-6-thinking",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "gpt-oss-120b",
        "gpt-oss-120b-medium",
    ]

    sorted_public: list[str] = []
    for pref in ORDER_PREFERENCE:
        if pref in public and pref not in sorted_public:
            sorted_public.append(pref)
    for p in public:
        if p not in sorted_public:
            sorted_public.append(p)

    return sorted_public, wire


def extract_gemini_models(payload: Any) -> list[str]:
    return extract_gemini_catalog(payload)[0]


def _cache_payload(
    provider: str,
    models: list[str],
    fetched_at: float,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "provider": provider,
        "models": models,
        "fetched_at": fetched_at,
        "source": "live",
        "schema": 1,
    }
    if isinstance(metadata, dict) and metadata:
        payload["metadata"] = metadata
    return payload


def read_catalog_cache(path: Path) -> tuple[list[str], float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return [], 0.0
        models = normalize_models(data.get("models") or [])
        fetched_at = float(data.get("fetched_at") or 0)
        return models, fetched_at
    except (OSError, ValueError, TypeError):
        return [], 0.0


def read_catalog_cache_details(path: Path) -> tuple[list[str], float, dict[str, Any]]:
    """Read models and non-secret discovery metadata from a catalog cache."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return [], 0.0, {}
        models = normalize_models(data.get("models") or [])
        fetched_at = float(data.get("fetched_at") or 0)
        metadata = data.get("metadata")
        return models, fetched_at, metadata if isinstance(metadata, dict) else {}
    except (OSError, ValueError, TypeError):
        return [], 0.0, {}


def write_catalog_cache(
    path: Path,
    provider: str,
    models: Iterable[Any],
    fetched_at: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Atomically persist a last-known-good catalog without exposing secrets."""
    normalized = normalize_models(models)
    if not normalized:
        raise CatalogError("Refusing to cache an empty model catalog")
    path = path.expanduser()
    timestamp = float(fetched_at or time.time())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(_cache_payload(provider, normalized, timestamp, metadata), handle, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o640)
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
    except OSError:
        # A read-only runtime cache must not make a healthy upstream fail.
        return


class DynamicCatalog:
    """Thread-safe TTL catalog with last-known-good fallback."""

    def __init__(
        self,
        provider: str,
        fetcher: Callable[[], Iterable[Any]],
        cache_path: Path,
        *,
        ttl: float | None = None,
    ) -> None:
        self.provider = provider
        self.fetcher = fetcher
        self.cache_path = cache_path
        self.ttl = ttl if ttl is not None else _float_env("AIPOOL_MODEL_CATALOG_TTL", DEFAULT_TTL_SECONDS)
        self._lock = threading.RLock()
        self._models: list[str] = []
        self._fetched_at = 0.0
        self._last_error: str | None = None
        self.metadata: dict[str, Any] = {}
        self._load_disk_cache()

    def _load_disk_cache(self) -> None:
        models, fetched_at, metadata = read_catalog_cache_details(self.cache_path)
        if models:
            self._models = models
            self._fetched_at = fetched_at
            self.metadata = metadata

    def _expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return not self._models or now - self._fetched_at >= self.ttl

    def refresh(self, *, force: bool = False) -> list[str]:
        with self._lock:
            if not force and not self._expired():
                return list(self._models)
            try:
                fetched = self.fetcher()
                metadata: dict[str, Any] = {}
                if isinstance(fetched, tuple) and len(fetched) == 2:
                    fetched, raw_metadata = fetched
                    if isinstance(raw_metadata, dict):
                        metadata = raw_metadata
                models = normalize_models(fetched)
                if not models:
                    raise CatalogError(f"{self.provider} live catalog is empty")
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                if self._models:
                    return list(self._models)
                raise CatalogError(f"{self.provider} catalog unavailable") from None
            self._models = models
            self._fetched_at = time.time()
            self._last_error = None
            self.metadata = metadata
            write_catalog_cache(self.cache_path, self.provider, models, self._fetched_at, metadata)
            return list(self._models)

    def get(self, *, force: bool = False) -> list[str]:
        return self.refresh(force=force)

    def peek(self) -> list[str]:
        with self._lock:
            return list(self._models)

    def status(self) -> dict[str, Any]:
        with self._lock:
            age = max(0.0, time.time() - self._fetched_at) if self._fetched_at else None
            return {
                "provider": self.provider,
                "models": list(self._models),
                "count": len(self._models),
                "fetched_at": self._fetched_at or None,
                "age_seconds": age,
                "stale": bool(self._models and self._expired()),
                "last_error": self._last_error,
                "cache_path": str(self.cache_path),
            }


def discover_codex_models(
    roots: Iterable[Path] | None = None,
) -> list[str]:
    """Aggregate visible model slugs from every Codex account cache.

    Account caches are official client state and may temporarily differ.  The
    union is exposed; request-time account failover remains responsible for a
    model unavailable on one account.
    """
    if roots is None:
        roots = (
            Path(os.environ.get("AIPOOL_CODEX_ACCOUNTS_DIR", "/var/lib/aipool/accounts/codex")),
            Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser(),
        )
    paths: set[Path] = set()
    for root in roots:
        root = Path(root).expanduser()
        if root.is_file() and root.name == "models_cache.json":
            paths.add(root)
        elif root.is_dir():
            direct = root / "models_cache.json"
            if direct.is_file():
                paths.add(direct)
            try:
                paths.update(root.glob("*/models_cache.json"))
            except OSError:
                continue

    visible: set[str] = set()
    for path in sorted(paths):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = data.get("models") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("visibility", "list")).strip().lower() != "list":
                continue
            slug = row.get("slug") or row.get("id") or row.get("model")
            if isinstance(slug, str) and slug.strip():
                visible.add(slug.strip())
    models = normalize_models(visible)
    if not models:
        raise CatalogError("No visible Codex models found in account caches")
    return models


def sync_provider_entry(
    existing: dict[str, Any] | None,
    discovered: Iterable[Any],
    *,
    key: str,
    endpoint: str,
    transport: str,
    display_name: str | None = None,
    context_length: int = 1_048_576,
) -> tuple[dict[str, Any], str]:
    """Build one provider entry and return ``(entry, added|updated|unchanged)``."""
    models = normalize_models(discovered)
    if not models:
        raise CatalogError(f"Cannot synchronize {key} with an empty catalog")
    old = dict(existing or {})
    old_models = normalize_models(old.get("models") or [])
    # Synchronization is additive by design.  A provider may temporarily return a
    # partial catalog (or an account cache may lag); never delete a model that is
    # already configured unless a separate, explicit pruning policy is introduced.
    effective_models = normalize_models([*old_models, *models])
    new_models = {model: {"context_length": context_length} for model in effective_models}
    entry = dict(old)
    entry.update(
        {
            "name": display_name or key,
            "api": endpoint,
            "transport": transport,
            "default_model": old.get("default_model") if old.get("default_model") in effective_models else effective_models[0],
            "models": new_models,
            "discover_models": True,
        }
    )
    if not existing:
        return entry, "added"
    comparable_old = {
        "name": old.get("name") or key,
        "api": old.get("api"),
        "transport": old.get("transport"),
        "default_model": old.get("default_model"),
        "models": old_models,
        "discover_models": old.get("discover_models"),
    }
    comparable_new = {
        "name": entry["name"],
        "api": entry["api"],
        "transport": entry["transport"],
        "default_model": entry["default_model"],
        "models": effective_models,
        "discover_models": True,
    }
    return entry, "unchanged" if comparable_old == comparable_new else "updated"
