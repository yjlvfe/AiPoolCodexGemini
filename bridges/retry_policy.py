"""Bounded provider retry policy matching 9Router BaseExecutor retry hierarchy.

Layer 1: Transport retries on the same account for transient 502/503/504 and network errors.
Layer 2: Mid-flight token refresh retry on 401/403.
Layer 3: Account failover on rate limit (429), quota exhaustion, or exhausted transport retries.
"""
from __future__ import annotations

import os
import time
from typing import Any
import urllib.error


# 9Router DEFAULT_RETRY_CONFIG:
# 429: 0 retries (immediate account failover)
# 502: up to 2 attempts (1 retry), 2.0s delay
# 503: up to 2 attempts (1 retry), 2.0s delay
# 504: up to 2 attempts (1 retry), 2.0s delay
# Network errors: up to 2 attempts, 2.0s delay
MAX_TRANSPORT_ATTEMPTS = 2
DEFAULT_TRANSPORT_DELAY = 2.0

TRANSIENT_TRANSPORT_STATUSES = {408, 500, 502, 503, 504, 520, 521, 522, 523, 524}
NO_RETRY_STATUSES = {400, 401, 403, 404, 409, 429}

MIN_PROVIDER_ATTEMPTS = 1
DEFAULT_PROVIDER_ATTEMPTS = 2
MAX_PROVIDER_ATTEMPTS = 5


def provider_attempts(value: Any = None) -> int:
    """Return max attempts on the same account for transport retries (default 2)."""
    if value is not None:
        try:
            return max(MIN_PROVIDER_ATTEMPTS, min(MAX_PROVIDER_ATTEMPTS, int(value)))
        except (TypeError, ValueError):
            pass
    env_val = os.environ.get("AIPOOL_PROVIDER_MAX_ATTEMPTS")
    if env_val is not None:
        try:
            return max(MIN_PROVIDER_ATTEMPTS, min(MAX_PROVIDER_ATTEMPTS, int(env_val)))
        except (TypeError, ValueError):
            pass
    return DEFAULT_PROVIDER_ATTEMPTS


def should_transport_retry(status_code: Any, attempt: int) -> bool:
    """Check if Layer 1 transport retry should be performed on the same account.

    Returns True for transient 502, 503, 504 and network errors when attempt < 2.
    Returns False for 429, 401, 403, 409, or when attempt >= 2.
    """
    if attempt >= MAX_TRANSPORT_ATTEMPTS:
        return False

    if isinstance(status_code, BaseException):
        if isinstance(status_code, urllib.error.HTTPError):
            code = status_code.code
        else:
            return isinstance(status_code, (TimeoutError, ConnectionError, OSError, urllib.error.URLError))
    else:
        try:
            code = int(status_code) if status_code is not None else 0
        except (TypeError, ValueError):
            code = 0

    if code == 0:
        # None or 0 represents network drop / connection failure
        return True

    if code in NO_RETRY_STATUSES:
        return False

    return code in TRANSIENT_TRANSPORT_STATUSES


def transport_retry_delay(status_code: Any = None, attempt: int = 1) -> float:
    """Return the backoff delay in seconds for Layer 1 transport retry (2.0s)."""
    try:
        code = int(status_code) if status_code is not None else 0
    except (TypeError, ValueError):
        code = 0

    if code in NO_RETRY_STATUSES:
        return 0.0
    return DEFAULT_TRANSPORT_DELAY


def transient_status(status: Any) -> bool:
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    return status in {408, 425, 429, 500, 502, 503, 504, 522, 524}


def transient_exception(exc: BaseException) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "code", None)
    if transient_status(status):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return transient_status(exc.code)
    return isinstance(exc, (TimeoutError, ConnectionError, OSError, urllib.error.URLError))


def transient_event(event: dict[str, Any]) -> bool:
    """Recognize an upstream failure before output is sent to the client."""
    if not isinstance(event, dict) or event.get("type") not in {"error", "response.failed"}:
        return False
    status = event.get("status") or event.get("status_code")
    if transient_status(status):
        return True
    text = str(event).lower()
    return any(
        marker in text
        for marker in (
            "temporarily unavailable",
            "service unavailable",
            "server error",
            "internal error",
            "overloaded",
            "timeout",
            "timed out",
            "connection reset",
            "try again",
        )
    )


def retry_delay(attempt: int, retry_after: Any = None) -> float:
    """Return transport retry delay; honors retry_after if given, else 2.0s."""
    if retry_after is not None:
        try:
            val = float(retry_after)
            if val >= 0:
                return val
        except (TypeError, ValueError):
            pass
    return DEFAULT_TRANSPORT_DELAY


def wait_before_retry(attempt: int, retry_after: Any = None) -> None:
    """Sleep for retry_delay seconds."""
    delay = retry_delay(attempt, retry_after)
    if delay > 0:
        time.sleep(delay)
