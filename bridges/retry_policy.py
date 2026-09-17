"""Bounded provider retry policy shared by both HTTP bridges.

NOTE: In Zero-Retry Architecture:
AiPool performs ZERO internal same-account retries and ZERO backoff sleeps.
Provider calls are executed at most ONCE per account per request.
Hermes / client owns end-to-end request retries.
"""
from __future__ import annotations

import os
from typing import Any
import urllib.error


# In Zero-Retry architecture, provider max attempts per account is always strictly 1.
MIN_PROVIDER_ATTEMPTS = 1
DEFAULT_PROVIDER_ATTEMPTS = 1
MAX_PROVIDER_ATTEMPTS = 1


def provider_attempts(value: Any = None) -> int:
    """Always returns 1 in Zero-Retry architecture regardless of env var or argument."""
    return 1


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
    """Zero-retry mode: returns 0.0 (no sleep)."""
    return 0.0


def wait_before_retry(attempt: int, retry_after: Any = None) -> None:
    """Zero-retry mode: no-op."""
    return
