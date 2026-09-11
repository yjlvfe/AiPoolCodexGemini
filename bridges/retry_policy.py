"""Bounded provider retry policy shared by both HTTP bridges."""
from __future__ import annotations

import os
import time
from typing import Any
import urllib.error


MIN_PROVIDER_ATTEMPTS = 20
DEFAULT_PROVIDER_ATTEMPTS = 20


def provider_attempts(value: Any = None) -> int:
    raw = value if value is not None else os.environ.get(
        "AIPOOL_PROVIDER_MAX_ATTEMPTS", str(DEFAULT_PROVIDER_ATTEMPTS)
    )
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = DEFAULT_PROVIDER_ATTEMPTS
    # Keep the policy bounded against a bad environment value while never
    # silently weakening the required twenty-attempt recovery floor.
    return min(100, max(MIN_PROVIDER_ATTEMPTS, parsed))


def transient_status(status: Any) -> bool:
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    return status in {408, 425, 500, 502, 503, 504, 522, 524}


def transient_exception(exc: BaseException) -> bool:
    status = getattr(exc, "status", None)
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
    """Return a bounded delay; tests can set the base to zero."""
    try:
        maximum = min(60.0, max(0.0, float(os.environ.get("AIPOOL_RETRY_MAX_DELAY", "5"))))
    except (TypeError, ValueError):
        maximum = 5.0
    try:
        base = min(maximum, max(0.0, float(os.environ.get("AIPOOL_RETRY_BASE_DELAY", "0.25"))))
    except (TypeError, ValueError):
        base = min(maximum, 0.25)
    try:
        hinted = max(0.0, float(retry_after))
    except (TypeError, ValueError):
        hinted = 0.0
    if hinted:
        return min(maximum, hinted)
    return min(maximum, base * (2 ** max(0, min(int(attempt), 8))))


def wait_before_retry(attempt: int, retry_after: Any = None) -> None:
    delay = retry_delay(attempt, retry_after)
    if delay > 0:
        time.sleep(delay)
