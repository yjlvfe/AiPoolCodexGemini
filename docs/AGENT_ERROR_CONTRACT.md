# AiPool Agent Error Contract

This document is the provider-neutral contract exposed by the Codex and Gemini
HTTP bridges. Clients must be able to handle the same envelope, fields, and
headers regardless of which bridge served the request.

## Correlation and response headers

Every request receives a server-generated UUID `request_id`. The same value is
used for the audit record and returned in:

```http
X-Request-ID: <request_id>
```

The caller's `X-Request-ID` is not trusted as the server audit ID. It may be
retained separately as `client_request_id` when the durable-request policy
needs it.

All JSON responses use `Content-Type: application/json` and a matching
`Content-Length`. Terminal error responses close the connection. A
`Retry-After` header is emitted only when the JSON error has a positive integer
`retry_after`; its value must match the JSON value exactly.

Successful JSON responses and stream responses also carry `X-Request-ID`.
Streaming responses use `Content-Type: text/event-stream`; if a stream fails
after headers are sent, the terminal SSE error uses the same public `error`
fields and is followed by `data: [DONE]`.

## JSON envelope

Every classified HTTP failure is encoded as:

```json
{
  "error": {
    "type": "gateway_error",
    "code": "MODEL_UNAVAILABLE",
    "message": "Requested model is unavailable",
    "retryable": true,
    "suggested_action": "change_model_or_retry_later",
    "model": "<requested model>",
    "retry_after": 30,
    "request_id": "<server UUID>"
  }
}
```

`retry_after` is optional and is expressed in whole seconds. `request_id` is
required on every bridge-generated error. `model` is required for
`MODEL_UNAVAILABLE` and should be omitted when no model was safely parsed.
Provider/account identifiers, credentials, prompts, and upstream raw error
bodies are never returned to the client.

## Required model and provider failures

### `MODEL_UNAVAILABLE`

Use when the requested model is not in the provider's available model set or
the provider authoritatively rejects that model. The response is HTTP 404 and
has:

```json
{
  "error": {
    "type": "gateway_error",
    "code": "MODEL_UNAVAILABLE",
    "message": "Requested model is unavailable",
    "retryable": true,
    "suggested_action": "change_model_or_retry_later",
    "model": "gpt-example",
    "request_id": "<uuid>"
  }
}
```

If the provider supplies a reset/retry window, include positive integer
`retry_after` and the matching `Retry-After` header. Otherwise omit both.

### `PROVIDER_UNAVAILABLE`

Use when the selected provider cannot serve the request (provider outage,
transport failure, or provider-wide unavailability), as distinct from a model
selection error. The response is HTTP 503 (HTTP 504 only when the gateway's
own request deadline expires) and has:

```json
{
  "error": {
    "type": "gateway_error",
    "code": "PROVIDER_UNAVAILABLE",
    "message": "Provider is temporarily unavailable",
    "retryable": true,
    "suggested_action": "retry_later",
    "request_id": "<uuid>"
  }
}
```

Include `retry_after` and `Retry-After` only when the provider or pool gives a
reliable positive delay. Do not expose account slot numbers in this envelope.

## Related classified failures

The same envelope and header rules apply to failover errors:

| Code | HTTP | `suggested_action` | `retryable` |
|---|---:|---|---:|
| `TRANSIENT_PROVIDER_ERROR` | 503 (504 for gateway deadline) | `retry_later` | `true` |
| `TEMP_RATE_LIMIT` | 429 | `retry_later` | `true` |
| `ALL_ACCOUNTS_EXHAUSTED` | 503 | `retry_later` | `false` unless a known reset is supplied |

`ALL_ACCOUNTS_EXHAUSTED` is emitted only after every eligible account has
been authoritatively retired for authentication or hard quota exhaustion. A
single transient provider failure must not be relabeled as pool exhaustion.

## Observability contract

Emit one request-scoped summary after provider processing, including requests
with zero attempts:

```text
REQUEST_PROVIDER_CALLS request_id=<uuid> accounts=[<opaque slot IDs>] total_calls=<n>
```

`accounts` preserves outbound call order and `total_calls` equals its length.
Increment the count immediately before each real outbound provider call,
including endpoint fallbacks. Never log authorization headers, tokens, request
bodies, prompts, or provider response bodies.

Classification events use the stable prefix and include a scope:

```text
FAILURE_CLASSIFIED scope=codex request_id=<uuid> code=MODEL_UNAVAILABLE
FAILURE_CLASSIFIED scope=gemini request_id=<uuid> code=PROVIDER_UNAVAILABLE
```

Model circuit-breaker transitions use these prefixes:

```text
MODEL_BREAKER_OPEN scope=<codex|gemini> model=<opaque model> reason=<code>
MODEL_BREAKER_HALF_OPEN scope=<codex|gemini> model=<opaque model>
MODEL_BREAKER_CLOSED scope=<codex|gemini> model=<opaque model>
```

Persist `request_id`, `error_code`, `provider_accounts`, and
`provider_call_count` in the redacted audit record so log and dashboard
inspection agree. Model names are acceptable telemetry; prompts and secrets
are not.

## Acceptance test specification

For **both** Codex and Gemini, the HTTP acceptance suite must assert:

1. `MODEL_UNAVAILABLE` returns 404, the requested `model`,
   `suggested_action=change_model_or_retry_later`, `retryable=true`, and a
   body/header request-ID match.
2. `PROVIDER_UNAVAILABLE` returns 503,
   `suggested_action=retry_later`, `retryable=true`, and a body/header
   request-ID match.
3. A positive `retry_after` appears identically in JSON and `Retry-After`;
   when absent or non-positive, both are absent.
4. Success responses and SSE headers include `X-Request-ID`.
5. An SSE terminal error contains the same `code`, `request_id`,
   `suggested_action`, `retryable`, and optional `retry_after` as the JSON
   path, then emits `[DONE]`.
6. The log capture contains exactly one `REQUEST_PROVIDER_CALLS` line per
   request, with ordered accounts and an accurate call count, including
   endpoint fallback.
7. Non-hard 429 responses remain `TEMP_RATE_LIMIT`; only exhaustion of every
   eligible account produces `ALL_ACCOUNTS_EXHAUSTED`.
8. Captured logs and public payloads contain no bearer tokens, API keys,
   prompts, raw authorization headers, or raw upstream bodies.
