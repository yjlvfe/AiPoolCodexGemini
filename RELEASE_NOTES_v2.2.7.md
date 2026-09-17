# AiPool v2.2.7 Release Notes

**Release date:** 2026-09-17  
**Status:** Stable / Production Ready

## Overview

AiPool v2.2.7 introduces the production Zero-Retry and Model-Aware Smart Failover architecture. The release separates model, provider, account, request, and unknown failures so each failure is handled at the correct scope. Account rotation is now reserved for proven account-specific failures, while unavailable models and provider outages fail fast with stable, agent-readable error responses.

## Highlights

### Zero-Retry Architecture

- Each logical request makes at most one provider attempt per account.
- AiPool no longer performs hidden same-account retries, retry sleeps, or backoff loops.
- A failed provider attempt is classified immediately and returned or escalated according to its failure scope.
- Once streamed output has started, the request is never replayed after a later stream failure.
- The architecture avoids duplicate side effects, hidden latency, quota waste, and misleading retry behavior for Hermes and other clients.

### Smart Account Failover

- Account failover occurs only when the current account has independently proven hard failure:
  - hard quota exhaustion; or
  - invalid, revoked, or otherwise confirmed unusable credentials.
- A hard account failure may advance to one eligible next account. If that account fails transiently, processing stops rather than cascading through the pool.
- Temporary 429 responses, generic 5xx responses, network failures, timeouts, model failures, and unknown failures do not rotate or retire the active account.
- The active account remains sticky for failures that are not account-scoped.
- Codex and Gemini remain strictly isolated; neither pool can silently serve the other provider's request.
- Generation-checked promotion prevents stale concurrent requests from overwriting newer active-account state.

### Model-Aware Failure Classification

Failures are classified deterministically using the following scopes and precedence:

1. `MODEL` — the requested model or deployment is unavailable, disabled, overloaded, or not serving.
2. `PROVIDER` — the upstream provider or endpoint is unavailable independently of the account and model.
3. `ACCOUNT` — credentials are invalid/revoked or hard quota exhaustion is explicitly proven.
4. `TRANSIENT_REQUEST` — request rate limiting, timeout, network, or transport interruption without higher-scope evidence.
5. `UNKNOWN` — insufficient or unrecognized evidence; handled fail-closed.

Model evidence takes precedence over provider, account, and transport signals. A model failure never triggers account failover, and AiPool never silently changes the requested model. This prevents cycling through accounts when the actual outage affects the model itself.

### Model Circuit Breaker

- Circuit state is isolated by `(provider, model)`.
- A model marked unavailable is rejected locally on subsequent requests without contacting the provider or consuming account quota.
- The default breaker TTL is 45 seconds; a positive provider `Retry-After` value may determine the recovery window.
- Recovery uses a single-flight probe so only one request probes a model when the TTL expires.
- A successful probe closes the breaker; a failed probe reopens it.
- A failing Gemini model does not affect another Gemini model, Codex models, or the opposite provider pool.

### Agent Contract

The Codex and Gemini bridges now expose a provider-neutral, stable error contract documented in `docs/AGENT_ERROR_CONTRACT.md`:

- Consistent JSON error envelopes with `type`, `code`, `message`, `retryable`, `suggested_action`, and `request_id`.
- `model` context for model-scoped failures when it can be safely parsed.
- `X-Request-ID` on successful responses, streaming responses, and errors.
- `Retry-After` only when a positive `retry_after` value is supplied, with the header and JSON value kept identical.
- Stable codes such as `MODEL_UNAVAILABLE`, `PROVIDER_UNAVAILABLE`, `TRANSIENT_PROVIDER_ERROR`, `TEMP_RATE_LIMIT`, and `ALL_ACCOUNTS_EXHAUSTED`.
- Redacted request/provider-call summaries and stable `FAILURE_CLASSIFIED` and `MODEL_BREAKER_*` observability events.
- No credentials, bearer tokens, prompts, authorization headers, or raw upstream bodies in public responses or logs.

For `MODEL_UNAVAILABLE`, clients receive the actionable recommendation `change_model_or_retry_later`. Provider-wide failures use `retry_later`. These fields are advisory to the external client and never authorize an internal same-account retry.

## Verification

- Full test suite: **233/233 PASS** (`pytest`).
- Zero-Retry behavior verified, including prevention of same-account retries and retry backoff.
- Smart Account Failover verified for hard quota and invalid-credential cases, with non-account failures remaining sticky.
- Model-aware classification verified across model, provider, account, transient-request, and unknown scopes.
- Circuit-breaker isolation, fast reject, TTL recovery, and single-flight probing verified.
- Agent Contract acceptance behavior verified for JSON, headers, streaming errors, request IDs, retry metadata, and redacted observability.
- Production services and bridge health were verified without intentionally modifying production data.

## Upgrade Notes

- No configuration migration is required for v2.2.7.
- Existing account credentials, account state, and runtime data are preserved.
- Clients should honor `suggested_action`, `retryable`, `retry_after`, and `request_id` from the standardized error envelope.
- Clients must not assume that a 429 always means account exhaustion; only explicit hard-quota evidence authorizes account failover.
- When a model is unavailable, select another model or retry after the breaker recovery window rather than expecting AiPool to switch models automatically.
