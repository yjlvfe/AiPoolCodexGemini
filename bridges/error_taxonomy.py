"""Stable error taxonomy shared by adapters, retries, audit, and dashboard."""
from dataclasses import dataclass
from enum import Enum
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


# Only hard account-level failures qualify for failover to the next healthy account.
# Transient errors (5xx, network, temporary rate limit) must NEVER retry the same account
# or failover to a different account within AiPool.
_FAILOVER = {
    ErrorCode.ACCOUNT_AUTH_FAILURE,
    ErrorCode.ACCOUNT_QUOTA_EXHAUSTED,
}


def classify(
    exc: BaseException | str | None = None,
    status: int | None = None,
    stream_started: bool = False,
    retry_after: int | None = None,
    request_id: str | None = None,
) -> ClassifiedError:
    if isinstance(exc, str):
        text = exc.strip().lower()
        exc_obj = None
    else:
        text = str(exc or "").strip().lower()
        exc_obj = exc

    status = int(status or getattr(exc_obj, "status", getattr(exc_obj, "code", 0)) or 0)

    # 1. Hard account invalid credentials / auth failure
    if status in (401, 403) or "unauthorized" in text or "invalid api key" in text or "invalid_credentials" in text:
        code = ErrorCode.ACCOUNT_AUTH_FAILURE
        msg = "Account authentication failed or credentials invalid"

    # 2. Hard quota exhaustion (explicitly verified quota/monthly cap limits)
    elif "all_accounts_exhausted" in text or "all accounts are exhausted" in text or "no usable" in text and "account" in text:
        code = ErrorCode.ALL_ACCOUNTS_EXHAUSTED
        msg = "All accounts are exhausted or invalid"
        status = status or 503

    elif status == 429 and any(
        k in text for k in (
            "quota", "quota_exceeded", "usage_limit", "exhausted",
            "monthly_cap", "monthly limit", "weekly limit", "five_hour"
        )
    ):
        code = ErrorCode.ACCOUNT_QUOTA_EXHAUSTED
        msg = "Account quota exceeded or exhausted"

    # 3. Temporary rate limit (transient 429 without hard quota evidence)
    elif status == 429:
        code = ErrorCode.TEMP_RATE_LIMIT
        msg = "Temporary rate limit encountered"

    # 4. Bad request / invalid request
    elif status == 400:
        code = ErrorCode.INVALID_REQUEST
        msg = "Invalid request payload or parameters"

    # 5. Model unavailable / 404
    elif status == 404 or ("model" in text and "available" in text):
        code = ErrorCode.MODEL_UNAVAILABLE
        msg = "Requested model is unavailable"

    # 6. Network timeout / Connect error
    elif isinstance(exc_obj, (TimeoutError, socket_timeout := getattr(__import__("socket"), "timeout", ()))) or "timeout" in text or "timed out" in text:
        code = ErrorCode.NETWORK_TIMEOUT
        msg = "Upstream network timeout"
        status = status or 504

    # 7. Provider outage / 5xx / connection reset
    elif status >= 500 or isinstance(exc_obj, (OSError, ConnectionError)) or "connection reset" in text:
        code = ErrorCode.TRANSIENT_PROVIDER_ERROR
        msg = "Upstream provider error"
        status = status or 503

    # 8. Policy rejection
    elif "policy" in text or status in (406, 451):
        code = ErrorCode.POLICY_REJECTION
        msg = "Request rejected by upstream policy"

    else:
        code = ErrorCode.UNKNOWN
        msg = "Upstream request failed"

    if stream_started:
        code = ErrorCode.STREAM_INTERRUPTION
        msg = "Stream interrupted after output started"

    # In Zero-Retry architecture:
    # - retryable (for client/Hermes): true only for transient errors.
    # - failover (internal account failover within AiPool): true ONLY for hard auth/quota before output.
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
