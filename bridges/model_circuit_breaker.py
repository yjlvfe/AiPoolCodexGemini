"""Per-provider, per-account, per-model circuit breaker for local fast rejection.

Keyed on ``(provider, account_id, model)`` so an account failure does not trip
the breaker for other healthy accounts.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Callable, Iterable


@dataclass(frozen=True)
class ModelUnavailable:
    """Stable local error payload for an open or probing model breaker."""

    provider: str
    model: str
    until: float
    reason: str
    code: str = "MODEL_UNAVAILABLE"
    account_id: str = "__all__"

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "provider": self.provider,
            "account_id": self.account_id,
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
        self.account_id = state.account_id
        self.until = state.until
        self.reason = state.reason
        self.code = state.code
        super().__init__(
            f"{state.code}({state.provider}, {state.model}, "
            f"until={state.until}, reason={state.reason})"
        )


ModelUnavailableException = ModelUnavailableError


@dataclass(frozen=True)
class ProbeLease:
    """Opaque ownership token for the request allowed to probe a key."""

    provider: str
    model: str
    generation: int
    account_id: str = "__all__"


@dataclass
class _Entry:
    until: float = 0.0
    reason: str = ""
    probe_in_flight: bool = False
    generation: int = 0
    probed: bool = False
    open: bool = False


class ModelCircuitBreaker:
    """Thread-safe circuit breaker isolated by (provider, account_id, model).

    Fast rejects only if the specific account is tripped or all candidate accounts
    are tripped.
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
        self._entries: dict[tuple[str, str, str], _Entry] = {}

    def _is_tripped(self, entry: _Entry | None, now: float) -> tuple[bool, str, float]:
        if entry is None:
            return False, "", 0.0
        if entry.probe_in_flight:
            return True, "probe_in_flight", entry.until
        if entry.until > now:
            return True, entry.reason, entry.until
        return False, "", 0.0

    def before_request(
        self,
        provider: str,
        model: str,
        account_id: str | None = None,
        all_accounts: Iterable[str] | None = None,
    ) -> ProbeLease | None:
        """Gate a request; fast reject only if the specific account is tripped or all accounts are tripped."""
        p_str = str(provider)
        m_str = str(model)
        now = float(self._clock())

        with self._lock:
            # Case 1: Specific account provided
            if account_id is not None:
                acc_str = str(account_id)
                key = (p_str, acc_str, m_str)
                tripped, reason, until = self._is_tripped(self._entries.get(key), now)
                if not tripped:
                    # Also check global wildcard entry
                    tripped, reason, until = self._is_tripped(self._entries.get((p_str, "__all__", m_str)), now)

                if tripped:
                    state = ModelUnavailable(p_str, m_str, until, reason, account_id=acc_str)
                    self._log("MODEL_BREAKER_FAST_REJECT", state)
                    raise ModelUnavailableError(state)

                entry = self._entries.setdefault(key, _Entry())
                if entry.probed and not entry.open:
                    return None
                entry.probe_in_flight = True
                entry.generation += 1
                lease = ProbeLease(p_str, m_str, entry.generation, account_id=acc_str)
                self._log("MODEL_BREAKER_PROBE", ModelUnavailable(p_str, m_str, entry.until, "probe", account_id=acc_str))
                return lease

            # Case 2: all_accounts provided (fast reject only if ALL accounts are tripped)
            if all_accounts is not None:
                acc_list = list(all_accounts)
                if acc_list:
                    all_tripped = True
                    latest_until = 0.0
                    last_reason = ""
                    for acc in acc_list:
                        key = (p_str, str(acc), m_str)
                        tripped, reason, until = self._is_tripped(self._entries.get(key), now)
                        if not tripped:
                            all_tripped = False
                            break
                        if until > latest_until:
                            latest_until = until
                            last_reason = reason
                    if all_tripped:
                        state = ModelUnavailable(p_str, m_str, latest_until, last_reason, account_id="__all__")
                        self._log("MODEL_BREAKER_FAST_REJECT", state)
                        raise ModelUnavailableError(state)
                    return None

            # Case 3: Neither account_id nor all_accounts provided
            global_key = (p_str, "__all__", m_str)
            tripped, reason, until = self._is_tripped(self._entries.get(global_key), now)
            if tripped:
                state = ModelUnavailable(p_str, m_str, until, reason, account_id="__all__")
                self._log("MODEL_BREAKER_FAST_REJECT", state)
                raise ModelUnavailableError(state)

            # Check if all tracked accounts for (provider, *, model) are tripped
            matching_entries = [
                (acc, entry) for (p, acc, m), entry in self._entries.items()
                if p == p_str and m == m_str and acc != "__all__"
            ]
            if matching_entries:
                all_tripped = True
                latest_until = 0.0
                last_reason = ""
                for _, entry in matching_entries:
                    tripped, reason, until = self._is_tripped(entry, now)
                    if not tripped:
                        all_tripped = False
                        break
                    if until > latest_until:
                        latest_until = until
                        last_reason = reason
                if all_tripped:
                    state = ModelUnavailable(p_str, m_str, latest_until, last_reason, account_id="__all__")
                    self._log("MODEL_BREAKER_FAST_REJECT", state)
                    raise ModelUnavailableError(state)

            entry = self._entries.setdefault(global_key, _Entry())
            if entry.probed and not entry.open:
                return None
            entry.probe_in_flight = True
            entry.generation += 1
            lease = ProbeLease(p_str, m_str, entry.generation, account_id="__all__")
            self._log("MODEL_BREAKER_PROBE", ModelUnavailable(p_str, m_str, entry.until, "probe", account_id="__all__"))
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
                "MODEL_BREAKER_CLOSED provider=%s account=%s model=%s",
                lease.provider, lease.account_id, lease.model
            )

    def record_failure(
        self, lease: ProbeLease, reason: str, *, retry_after: float | None = None
    ) -> ModelUnavailable:
        """Open/reopen a breaker, honoring a valid provider Retry-After."""
        ttl = self.default_ttl if retry_after is None else max(0.0, float(retry_after))
        now = float(self._clock())
        state = ModelUnavailable(lease.provider, lease.model, now + ttl, str(reason), account_id=lease.account_id)
        with self._lock:
            entry = self._owned_entry(lease)
            entry.probe_in_flight = False
            entry.probed = True
            entry.open = True
            entry.until = state.until
            entry.reason = state.reason
            self._log("MODEL_BREAKER_OPEN", state)
            return state

    def state(self, provider: str, model: str, account_id: str | None = None) -> ModelUnavailable | None:
        """Return current open state, or ``None`` when closed."""
        p_str = str(provider)
        m_str = str(model)
        acc_str = str(account_id or "__all__")
        now = float(self._clock())
        with self._lock:
            entry = self._entries.get((p_str, acc_str, m_str))
            if entry is None or (not entry.probe_in_flight and entry.until <= now):
                return None
            return ModelUnavailable(
                p_str, m_str, entry.until,
                "probe_in_flight" if entry.probe_in_flight else entry.reason,
                account_id=acc_str,
            )

    def _owned_entry(self, lease: ProbeLease) -> _Entry:
        key = (lease.provider, lease.account_id, lease.model)
        entry = self._entries.get(key)
        if entry is None or not entry.probe_in_flight or entry.generation != lease.generation:
            raise ValueError("probe lease is stale or not owned by this breaker")
        return entry

    def _log(self, event: str, state: ModelUnavailable) -> None:
        self._logger.info(
            "%s provider=%s model=%s until=%s reason=%s",
            event, state.provider, state.model, state.until, state.reason,
        )


CircuitBreaker = ModelCircuitBreaker
