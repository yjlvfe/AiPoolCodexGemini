"""Stable error taxonomy shared by adapters, retries, audit, and dashboard.

Parity with 9Router errorConfig.js and accountFallback.js.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
import math
import re
import time
from typing import Any


class ErrorCode(str, Enum):
    # Core failover & agent contract codes
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TRANSIENT_PROVIDER_ERROR = "TRANSIENT_PROVIDER_ERROR"
    TEMP_RATE_LIMIT = "TEMP_RATE_LIMIT"
    ALL_ACCOUNTS_EXHAUSTED = "ALL_ACCOUNTS_EXHAUSTED"

    # Legacy/Compatibility aliases & specialized errors
    ACCOUNT_AUTH_FAILURE = "ACCOUNT_AUTH_FAILURE"
    ACCOUNT_QUOTA_EXHAUSTED = "ACCOUNT_QUOTA_EXHAUSTED"
    INVALID_REQUEST = "INVALID_REQUEST"
    PROVIDER_OUTAGE = "PROVIDER_OUTAGE"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    POLICY_REJECTION = "POLICY_REJECTION"
    STREAM_INTERRUPTION = "STREAM_INTERRUPTION"
    CLIENT_AUTHENTICATION = "CLIENT_AUTHENTICATION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ClassifiedError:
    code: ErrorCode
    status: int
    retryable: bool
    failover: bool
    before_output_only: bool = True
    message: str = "request failed"
    retry_after: int | None = None
    request_id: str | None = None
    retry_seconds: float | None = None

    def __post_init__(self):
        if self.retry_after is not None and self.retry_seconds is None:
            object.__setattr__(self, "retry_seconds", float(self.retry_after))
        elif self.retry_seconds is not None and self.retry_after is None:
            object.__setattr__(self, "retry_after", max(0, int(math.ceil(self.retry_seconds))))


# 9Router behavioral parity: Rate limits (429) and quota exhaustion MUST trigger failover
# across pool candidates (shouldFallback: true).
_FAILOVER = {
    ErrorCode.ACCOUNT_AUTH_FAILURE,
    ErrorCode.ACCOUNT_QUOTA_EXHAUSTED,
    ErrorCode.TEMP_RATE_LIMIT,
}

_RESET_AFTER_RE = re.compile(
    r"reset after\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?",
    re.IGNORECASE,
)


def parse_retry_seconds(
    headers: Any = None,
    payload: Any = None,
    text: str = "",
    now: float | None = None,
) -> float | None:
    """Parse precise retry seconds from headers, payload, or text.

    Supports:
    - Retry-After header (seconds or delta)
    - resets_at / reset_at / resetAt (epoch seconds or ISO timestamp)
    - resets_in_seconds / retry_after payload fields
    - Antigravity text pattern 'reset after (Xh)?(Ym)?(Zs)?'
    """
    current_now = time.time() if now is None else float(now)

    # 1. Check headers for Retry-After
    if headers:
        raw_val = None
        if hasattr(headers, "get"):
            raw_val = headers.get("Retry-After") or headers.get("retry-after")
        elif isinstance(headers, (list, tuple)):
            for k, v in headers:
                if str(k).lower() == "retry-after":
                    raw_val = v
                    break
        if raw_val is not None:
            try:
                sec = float(raw_val)
                if sec >= 0:
                    return sec
            except (TypeError, ValueError):
                pass

    # 2. Check structured payload (dict or parsed JSON)
    data = None
    if isinstance(payload, dict):
        data = payload
    elif isinstance(payload, (str, bytes)):
        try:
            data = json.loads(payload)
        except Exception:
            data = None

    if isinstance(data, dict):
        raw_err = data.get("error")
        err: dict[str, Any] = raw_err if isinstance(raw_err, dict) else data
        for field in ("resets_in_seconds", "resetsInSeconds", "retry_after", "retryAfter"):
            val = err.get(field)
            if val is not None:
                try:
                    sec = float(val)
                    if sec >= 0:
                        return sec
                except (TypeError, ValueError):
                    pass

        for field in ("resets_at", "reset_at", "resetAt"):
            val = err.get(field)
            if val is not None:
                try:
                    num = float(val)
                    if num > 1_000_000_000:
                        return max(0.0, num - current_now)
                    elif num >= 0:
                        return num
                except (TypeError, ValueError):
                    try:
                        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                        return max(0.0, dt.timestamp() - current_now)
                    except Exception:
                        pass

        msg = err.get("message") or err.get("detail") or ""
        if isinstance(msg, str) and msg:
            text = f"{text}\n{msg}"

    # 3. Check text for Antigravity 'reset after (\d+h)?(\d+m)?(\d+s)?'
    search_text = text
    if isinstance(payload, str) and payload not in search_text:
        search_text = f"{search_text}\n{payload}"

    if search_text:
        m = _RESET_AFTER_RE.search(search_text)
        if m and any(m.groups()):
            h = int(m.group(1)) if m.group(1) else 0
            minutes = int(m.group(2)) if m.group(2) else 0
            s = int(m.group(3)) if m.group(3) else 0
            total_sec = h * 3600 + minutes * 60 + s
            return float(max(0, total_sec))

    return None


def classify(
    exc: BaseException | str | None = None,
    status: int | None = None,
    stream_started: bool = False,
    retry_after: int | None = None,
    request_id: str | None = None,
    retry_seconds: float | None = None,
    headers: Any = None,
) -> ClassifiedError:
    if isinstance(exc, str):
        text = exc.strip().lower()
        exc_obj = None
    else:
        text = str(exc or "").strip().lower()
        exc_obj = exc

    status = int(status or getattr(exc_obj, "status", getattr(exc_obj, "code", 0)) or 0)

    # Resolve retry duration from headers / exc / text if not provided
    if retry_seconds is None and retry_after is None:
        parsed_sec = parse_retry_seconds(
            headers=headers or getattr(exc_obj, "headers", None),
            payload=exc_obj,
            text=text,
        )
        if parsed_sec is not None:
            retry_seconds = parsed_sec
            retry_after = max(0, int(math.ceil(parsed_sec)))

    # 1. Hard account invalid credentials / auth failure
    if status in (401, 403) or "unauthorized" in text or "invalid api key" in text or "invalid_credentials" in text or (status == 400 and ("not supported when using codex" in text or "resource_exhausted" in text)):
        code = ErrorCode.ACCOUNT_AUTH_FAILURE
        msg = "Account authentication failed or credentials invalid"

    # 2. Antigravity HTTP 409 Conflict as quota exhaustion
    elif status == 409:
        code = ErrorCode.ACCOUNT_QUOTA_EXHAUSTED
        msg = "Account quota exhausted (HTTP 409 Conflict)"

    # 3. Hard quota exhaustion / capacity signals
    elif "all_accounts_exhausted" in text or "all accounts are exhausted" in text or ("no usable" in text and "account" in text):
        code = ErrorCode.ALL_ACCOUNTS_EXHAUSTED
        msg = "All accounts are exhausted or invalid"
        status = status or 503

    elif any(
        k in text for k in (
            "quota", "quota_exceeded", "quota exceeded", "usage_limit", "exhausted",
            "monthly_cap", "monthly limit", "weekly limit", "five_hour",
            "5-hour", "5-hour limit", "5h limit",
            "rate_limit", "rate_limit_exceeded", "rate limit",
            "too many requests", "too many requests per account",
            "resource_exhausted", "resource exhausted",
            "not supported when using codex", "validation_required",
            "capacity", "overloaded",
            "model_at_capacity", "selected model is at capacity",
        )
    ):
        code = ErrorCode.ACCOUNT_QUOTA_EXHAUSTED
        msg = "Account quota exceeded or exhausted"
        if not status:
            status = 429

    # 4. Temporary rate limit (429 without explicit quota text)
    elif status == 429:
        code = ErrorCode.TEMP_RATE_LIMIT
        msg = "Temporary rate limit encountered"

    # 5. Bad request / invalid request
    elif status == 400:
        code = ErrorCode.INVALID_REQUEST
        msg = "Invalid request payload or parameters"

    # 6. Model unavailable / 404
    elif status == 404 or ("model" in text and "available" in text):
        code = ErrorCode.MODEL_UNAVAILABLE
        msg = "Requested model is unavailable"

    # 7. Network timeout / Connect error
    elif isinstance(exc_obj, (TimeoutError, socket_timeout := getattr(__import__("socket"), "timeout", ()))) or "timeout" in text or "timed out" in text:
        code = ErrorCode.NETWORK_TIMEOUT
        msg = "Upstream network timeout"
        status = status or 504

    # 8. Provider outage / 5xx / connection reset
    elif status >= 500 or isinstance(exc_obj, (OSError, ConnectionError)) or "connection reset" in text:
        code = ErrorCode.TRANSIENT_PROVIDER_ERROR
        msg = "Upstream provider error"
        status = status or 503

    # 9. Policy rejection
    elif "policy" in text or status in (406, 451):
        code = ErrorCode.POLICY_REJECTION
        msg = "Request rejected by upstream policy"

    else:
        code = ErrorCode.UNKNOWN
        msg = "Upstream request failed"

    if stream_started:
        code = ErrorCode.STREAM_INTERRUPTION
        msg = "Stream interrupted after output started"

    client_retryable = code in {
        ErrorCode.TRANSIENT_PROVIDER_ERROR,
        ErrorCode.TEMP_RATE_LIMIT,
        ErrorCode.NETWORK_TIMEOUT,
        ErrorCode.PROVIDER_OUTAGE,
    } and not stream_started

    internal_failover = (code in _FAILOVER) and not stream_started

    return ClassifiedError(
        code=code,
        status=status or 500,
        retryable=client_retryable,
        failover=internal_failover,
        before_output_only=True,
        message=msg,
        retry_after=retry_after,
        request_id=request_id,
        retry_seconds=retry_seconds,
    )


def classify_http_status(
    status: int,
    headers: Any = None,
    body: Any = None,
    stream_started: bool = False,
    request_id: str | None = None,
) -> ClassifiedError:
    """Classify an HTTP response by status code, headers, and body."""
    text = ""
    if isinstance(body, str):
        text = body
    elif isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    elif isinstance(body, dict):
        text = json.dumps(body)

    retry_sec = parse_retry_seconds(headers=headers, payload=body, text=text)
    return classify(
        exc=text,
        status=status,
        stream_started=stream_started,
        retry_seconds=retry_sec,
        request_id=request_id,
        headers=headers,
    )


def classify_payload(
    payload: Any,
    status: int | None = None,
    headers: Any = None,
    stream_started: bool = False,
    request_id: str | None = None,
) -> ClassifiedError:
    """Classify an error payload (dict, str, or bytes) with optional HTTP status."""
    text = ""
    if isinstance(payload, str):
        text = payload
    elif isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="replace")
    elif isinstance(payload, dict):
        text = json.dumps(payload)

    retry_sec = parse_retry_seconds(headers=headers, payload=payload, text=text)
    return classify(
        exc=text,
        status=status,
        stream_started=stream_started,
        retry_seconds=retry_sec,
        request_id=request_id,
        headers=headers,
    )


def public_error(error: ClassifiedError, model: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": error.code.value if error.code in {
            ErrorCode.MODEL_UNAVAILABLE,
            ErrorCode.PROVIDER_UNAVAILABLE,
            ErrorCode.TRANSIENT_PROVIDER_ERROR,
            ErrorCode.ALL_ACCOUNTS_EXHAUSTED,
            ErrorCode.TEMP_RATE_LIMIT,
        } else "gateway_error",
        "code": error.code.value,
        "message": error.message,
        "retryable": error.retryable,
        "failover": error.failover,
    }
    if error.code == ErrorCode.MODEL_UNAVAILABLE:
        payload["suggested_action"] = "change_model_or_retry_later"
        if model:
            payload["model"] = model
    elif error.code in {ErrorCode.PROVIDER_UNAVAILABLE, ErrorCode.TRANSIENT_PROVIDER_ERROR}:
        payload["suggested_action"] = "retry_later"

    if error.request_id:
        payload["request_id"] = error.request_id
    if error.retry_after is not None and error.retry_after > 0:
        payload["retry_after"] = int(error.retry_after)
    return payload
