"""Stable error taxonomy shared by adapters, retries, audit, and dashboard."""
from dataclasses import dataclass
from enum import Enum
from typing import Any

class ErrorCode(str, Enum):
    ACCOUNT_AUTH_FAILURE = "ACCOUNT_AUTH_FAILURE"
    ACCOUNT_QUOTA_EXHAUSTED = "ACCOUNT_QUOTA_EXHAUSTED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
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

_RETRYABLE = {ErrorCode.ACCOUNT_AUTH_FAILURE, ErrorCode.ACCOUNT_QUOTA_EXHAUSTED,
              ErrorCode.PROVIDER_OUTAGE, ErrorCode.NETWORK_TIMEOUT}
_FAILOVER = {ErrorCode.ACCOUNT_AUTH_FAILURE, ErrorCode.ACCOUNT_QUOTA_EXHAUSTED,
             ErrorCode.PROVIDER_OUTAGE, ErrorCode.NETWORK_TIMEOUT}

def classify(exc: BaseException | None = None, status: int | None = None,
             stream_started: bool = False) -> ClassifiedError:
    status = int(status or getattr(exc, "status", 0) or 0)
    text = str(exc or "").lower()
    if status in (401, 403) or "unauthorized" in text or "invalid api key" in text:
        code = ErrorCode.ACCOUNT_AUTH_FAILURE
    elif status == 429 or any(x in text for x in ("quota", "rate limit", "usage_limit", "exhausted")):
        code = ErrorCode.ACCOUNT_QUOTA_EXHAUSTED
    elif status == 400:
        code = ErrorCode.INVALID_REQUEST
    elif status == 404 or "model" in text and "available" in text:
        code = ErrorCode.MODEL_UNAVAILABLE
    elif isinstance(exc, TimeoutError) or "timeout" in text:
        code = ErrorCode.NETWORK_TIMEOUT
    elif status >= 500 or isinstance(exc, OSError):
        code = ErrorCode.PROVIDER_OUTAGE
    elif "policy" in text or status in (406, 451):
        code = ErrorCode.POLICY_REJECTION
    else:
        code = ErrorCode.UNKNOWN
    if stream_started:
        code = ErrorCode.STREAM_INTERRUPTION
    return ClassifiedError(code=code, status=status or 500,
        retryable=code in _RETRYABLE and not stream_started,
        failover=code in _FAILOVER and not stream_started,
        before_output_only=True, message="upstream request failed")

def public_error(error: ClassifiedError) -> dict[str, Any]:
    return {"type": "gateway_error", "code": error.code.value,
            "message": error.message, "retryable": error.retryable,
            "failover": error.failover}
