"""Per-provider, per-model circuit breaker for local fast rejection.

The breaker is deliberately independent of account pools: its key is exactly
``(provider, model)``.  Callers gate a provider attempt with
:meth:`before_request`, then report the result with :meth:`record_success` or
:meth:`record_failure`.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class ModelUnavailable:
    """Stable local error payload for an open or probing model breaker."""

    provider: str
    model: str
    until: float
    reason: str
    code: str = "MODEL_UNAVAILABLE"

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "provider": self.provider,
            "model": self.model,
            "until": self.until,
            "reason": self.reason,
        }


class ModelUnavailableError(RuntimeError):
    """Raised before a provider call when this model cannot be probed."""

    def __init__(self, state: ModelUnavailable):
        self.state = state
        self.provider = state.provider
        self.model = state.model
        self.until = state.until
        self.reason = state.reason
        self.code = state.code
        super().__init__(
            f"{state.code}({state.provider}, {state.model}, "
            f"until={state.until}, reason={state.reason})"
        )


# Short alias for callers that prefer the domain name in exception handlers.
ModelUnavailableException = ModelUnavailableError


@dataclass(frozen=True)
class ProbeLease:
    """Opaque ownership token for the one request allowed to probe a key."""

    provider: str
    model: str
    generation: int


@dataclass
class _Entry:
    until: float = 0.0
    reason: str = ""
    probe_in_flight: bool = False
    generation: int = 0
    probed: bool = False
    open: bool = False


class ModelCircuitBreaker:
    """Thread-safe circuit breaker isolated by provider and model.

    ``before_request`` returns a :class:`ProbeLease` for the first request of
    a key and for the first request after TTL expiry.  All other requests may
    proceed while the breaker is closed, but receive
    :class:`ModelUnavailableError` while a probe is in flight or TTL is active.
    """

    def __init__(
        self,
        default_ttl: float = 45.0,
        *,
        clock: Callable[[], float] = time.time,
        logger: logging.Logger | None = None,
    ) -> None:
        if default_ttl < 0:
            raise ValueError("default_ttl must be non-negative")
        self.default_ttl = float(default_ttl)
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._entries: dict[tuple[str, str], _Entry] = {}

    def before_request(self, provider: str, model: str) -> ProbeLease | None:
        """Gate a request; raise locally unless it owns the single probe."""
        key = (str(provider), str(model))
        now = float(self._clock())
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            if entry.probe_in_flight:
                state = ModelUnavailable(key[0], key[1], entry.until, "probe_in_flight")
                self._log("MODEL_BREAKER_FAST_REJECT", state)
                raise ModelUnavailableError(state)
            if entry.until > now:
                state = ModelUnavailable(key[0], key[1], entry.until, entry.reason)
                self._log("MODEL_BREAKER_FAST_REJECT", state)
                raise ModelUnavailableError(state)
            if entry.probed and not entry.open:
                return None
            entry.probe_in_flight = True
            entry.generation += 1
            lease = ProbeLease(key[0], key[1], entry.generation)
            self._log("MODEL_BREAKER_PROBE", ModelUnavailable(key[0], key[1], entry.until, "probe"))
            return lease

    def record_success(self, lease: ProbeLease) -> None:
        """Close a breaker after its owned probe succeeds."""
        with self._lock:
            entry = self._owned_entry(lease)
            entry.probe_in_flight = False
            entry.probed = True
            entry.open = False
            entry.until = 0.0
            entry.reason = ""
            self._logger.info(
                "MODEL_BREAKER_CLOSED provider=%s model=%s", lease.provider, lease.model
            )

    def record_failure(
        self, lease: ProbeLease, reason: str, *, retry_after: float | None = None
    ) -> ModelUnavailable:
        """Open/reopen a breaker, honoring a valid provider Retry-After."""
        ttl = self.default_ttl if retry_after is None else max(0.0, float(retry_after))
        now = float(self._clock())
        state = ModelUnavailable(lease.provider, lease.model, now + ttl, str(reason))
        with self._lock:
            entry = self._owned_entry(lease)
            entry.probe_in_flight = False
            entry.probed = True
            entry.open = True
            entry.until = state.until
            entry.reason = state.reason
            self._log("MODEL_BREAKER_OPEN", state)
            return state

    def state(self, provider: str, model: str) -> ModelUnavailable | None:
        """Return current open state, or ``None`` when the key is closed."""
        key = (str(provider), str(model))
        now = float(self._clock())
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or (not entry.probe_in_flight and entry.until <= now):
                return None
            return ModelUnavailable(key[0], key[1], entry.until,
                                    "probe_in_flight" if entry.probe_in_flight else entry.reason)

    def _owned_entry(self, lease: ProbeLease) -> _Entry:
        entry = self._entries.get((lease.provider, lease.model))
        if entry is None or not entry.probe_in_flight or entry.generation != lease.generation:
            raise ValueError("probe lease is stale or not owned by this breaker")
        return entry

    def _log(self, event: str, state: ModelUnavailable) -> None:
        self._logger.info(
            "%s provider=%s model=%s until=%s reason=%s",
            event, state.provider, state.model, state.until, state.reason,
        )


# Convenient name for integrations that call the component simply "Breaker".
CircuitBreaker = ModelCircuitBreaker
