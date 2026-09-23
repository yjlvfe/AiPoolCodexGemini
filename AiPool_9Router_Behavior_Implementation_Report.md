# AiPool 9Router Behavior Adoption: Implementation Report

## Executive Summary
AiPool (`/root/Projects/AiPoolCodexGemini`) has been fundamentally rebased on **9Router** as the runtime behavioral source of truth. All legacy policies conflicting with 9Router (including Zero-Retry, rigid sticky-only constraints, global circuit breaker lockouts, and the 60s cooldown floor) have been systematically removed and replaced with 9Router-parity mechanisms.

The entire test suite now stands at **258 passed tests** (0 failed, 0 errors).

---

## 1. Key Architectural Transformations

### Priority 0: Account Exhaustion & Rotation
- **Root Cause of Legacy Defect**: Previously, when all accounts were in cooldown, AiPool's `candidates()` method fell back to `candidates(include_cooldown=True)`. This caused an aggressive 429 storm where exhausted accounts were repeatedly probed against upstream APIs.
- **9Router Parity Adopted**:
  - Eliminated `include_cooldown=True` storm fallback.
  - Added `get_earliest_retry_after(provider, model)` to compute the minimum cooldown remaining across accounts.
  - When all accounts are in cooldown, AiPool fast-rejects with `HTTP 503 Service Unavailable` accompanied by the `Retry-After: <sec>` header. Upstream APIs are not hammered.
  - Accounts with active locks are skipped immediately during candidate evaluation.

### Error Taxonomy & Antigravity HTTP 409
- **Google Antigravity HTTP 409 Conflict**: Mapped directly to `ACCOUNT_QUOTA_EXHAUSTED` with `failover=True`.
- **HTTP 429 Rate Limits**: Added `ErrorCode.TEMP_RATE_LIMIT` to the `_FAILOVER` set. Every 429 now triggers candidate rotation across the pool (`shouldFallback: true`).
- **In-Band Stream Peeking**:
  - String patterns `"model_at_capacity"` and `"selected model is at capacity"` are detected during stream peeking and converted to HTTP 503, immediately triggering candidate rotation.
  - Pattern matching for `"rate limit"`, `"too many requests"`, `"quota exceeded"`, `"capacity"`, and `"overloaded"`.
  - Precise timestamp parsing for `resets_at` (epoch sec & ISO), `resets_in_seconds`, `Retry-After`, and Antigravity `reset after (\d+h)?(\d+m)?(\d+s)?`.

### Retry Engine & Abolishing Zero-Retry
- **Abolished Zero-Retry**: Replaced rigid single-attempt constraints with 9Router's Layer 1 Transport Retry hierarchy:
  - Transient 502, 503, 504 errors and socket/network dropouts execute up to 2 attempts on the same account with a 2.0-second backoff delay.
  - HTTP 429, 409, 401, 403, and quota errors enforce 0 transport retries, failing fast to Level 3 candidate rotation.

### Cooldown Duration & Per-Model Isolation
- **9Router Exponential Backoff**: Replaced hardcoded 60s floor with exponential backoff:
  $$\text{cooldown} = \min(2.0 \times 2^{\text{level}-1}, 300\text{ seconds})$$
- **Per-Model vs Account-Wide Locks**:
  - Model-specific rate limits cool down `(provider, account, model)`.
  - Account-wide failures (401 invalid credentials, account suspension) cool down `(provider, account, "__all__")`.
  - Other models on the account remain available when a specific model is rate-limited.
- **Immediate Reset on Success**: Successful requests invoke `clear_cooldown(provider, account, model)`, resetting model locks and backoff levels.

### De-globalized Circuit Breaker
- In the legacy system, `ModelCircuitBreaker` was keyed on `(provider, model)`, meaning one failing account tripped the circuit breaker for all accounts in the pool.
- Circuit breaker state is now de-globalized and keyed on `(provider, account_id, model)`. A failure on Account A never blocks Account B.

### Streaming, Native SSE & Empty Response Elimination
- **Native SSE for Antigravity**:
  - Replaced synchronous full-body blocking with `/streamGenerateContent?alt=sse`.
  - Incremental chunk translation delivers initial tokens in 1–2 seconds, completely eliminating the 75-second client timeout issue.
- **First-Chunk Header Buffering**:
  - HTTP 200 OK headers and `Content-Type: text/event-stream` are held until a valid content or reasoning chunk is confirmed.
  - If upstream returns an empty candidate list or safety block, the request is intercepted before headers and returns HTTP 502/400 JSON error. It **never** returns `200 OK` with an empty body.
- **Codex Chunk Idle Timeout**:
  - Increased from 30s to 180s to prevent premature socket timeouts on o1/o3/gpt-5 thinking models.
- **Terminal SSE Framing Compliance**:
  - Chat completions (`/v1/chat/completions`): emits `data: {"error": ...}\n\ndata: [DONE]\n\n`.
  - Responses API (`/v1/responses`): emits `event: response.failed\ndata: {"type": "response.failed", ...}\n\n` (never sends `data: [DONE]`).
- **Empty Delta Filtering**: Deltas where `content == ""` without reasoning, tools, or finish reason are discarded.

---

## 2. Acceptance Matrix Verification

| Scenario | Expected 9Router-like Behavior | Verification Result |
|---|---|---|
| **Hard quota exhausted** | Active account cooled; request rotates immediately to next candidate | **PASS** |
| **Temp rate limit (429)** | In-flight failover to next candidate; account cooled with exponential backoff | **PASS** |
| **Network failure / Drop** | Transport retry (up to 2x, 2s delay) on same account before candidate rotation | **PASS** |
| **5xx Server Error** | Transport retry (up to 2x, 2s delay) on same account before candidate rotation | **PASS** |
| **Auth failure (401/403)** | Account cooled for 120s with `"__all__"` scope; request rotates to next candidate | **PASS** |
| **Model unavailable** | Fast-reject with HTTP 503 + `Retry-After: <sec>` when all accounts exhausted | **PASS** |
| **Provider unavailable** | Fast-reject with HTTP 503 + `Retry-After: <sec>` | **PASS** |
| **Account rotation** | Multi-candidate pool rotates cleanly without infinite loops | **PASS** |
| **Model fallback isolation** | Per-model locking (`modelLock_${model}`) preserves other models on the account | **PASS** |
| **Provider fallback** | Seamless rotation across all candidate accounts of the provider | **PASS** |
| **Token refresh** | Proactive validation and synchronized in-place update | **PASS** |
| **Stream interruption** | Proper terminal framing (`[DONE]` on chat, `response.failed` on responses) | **PASS** |
| **Empty response** | First chunk buffered; empty responses converted to HTTP 502/400; no 200 OK empty body | **PASS** |
| **All accounts exhausted** | Fast-reject HTTP 503 + `Retry-After: <sec>`, zero upstream storm requests | **PASS** |

---

## 3. Test Suite Audit & Categorization

| Category | Count | Status | Notes |
|---|---|---|---|
| **KEEP** | 225 | Unchanged | Persistence, account locks, token encryption, CLI & Dashboard UI parity. |
| **UPDATE** | 9 | Updated | Updated assertions from Zero-Retry and global circuit breaker to 9Router-parity. |
| **NEW** | 24 | Added | `test_d_foundation_parity.py` (12 tests), `test_phase_e_streaming_parity.py` (9 tests), retry & failover tests (3 tests). |
| **TOTAL** | **258** | **ALL PASS** | 100% passing rate in 26.43s. |
