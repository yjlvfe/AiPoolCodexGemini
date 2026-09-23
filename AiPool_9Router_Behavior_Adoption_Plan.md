# AiPool Adoption Plan: Rebasing on 9Router Behavior

## 1. Architectural Principles
1. **9Router Is Behavioral Source of Truth**: When existing AiPool policy conflicts with 9Router, the legacy AiPool policy is eliminated or rewritten.
2. **Data & Credential Safety**: Account directories (`/var/lib/aipool/accounts`), tokens, agent state sqlite files, and systemd configurations are strictly preserved.
3. **No Upstream Storms**: Never probe exhausted accounts upstream when all accounts are cooling down; fast-reject with HTTP 503 + `Retry-After`.
4. **Immediate Failover on All 429s & 409s**: Every 429 and Antigravity 409 rotates to the next pool candidate after marking the active account cooled.
5. **Eliminate 75s Empty Streaming Responses**: Implement native SSE streaming for Antigravity, buffer first chunk before 200 OK headers, and discard empty deltas.

---

## 2. Phase-by-Phase Roadmap

### Phase D: Core Accounts, Quota, Retry & Failover Engine

#### D.1: Error Classification & Taxonomy (`bridges/error_taxonomy.py`)
- Remove legacy restriction where `TEMP_RATE_LIMIT` had `failover: false`.
- Add `ErrorCode.TEMP_RATE_LIMIT` to `_FAILOVER` set.
- Add recognition for Google Antigravity `409 Conflict` as pool quota exhaustion.
- Parse text indicators (`"rate limit"`, `"quota exceeded"`, `"capacity"`, `"overloaded"`, `"model_at_capacity"`).
- Return exact cooldown recommendations from upstream headers/text.

#### D.2: Account Pool Runtime & Cooldowns (`bridges/pool_runtime.py`)
- Abolish the `include_cooldown=True` fallback storm in `candidates()`. When all accounts are in cooldown, return empty candidate list so callers issue clean HTTP 503 with earliest `Retry-After`.
- Implement per-model locks (`modelLock_${model}`) alongside account-wide locks (`__all__`).
- Replace hardcoded 60s cooldown floor with 9Router-style exponential backoff (base 2s, double per level, max 300s).
- Add support for upstream `resets_at` / `resets_in_seconds` parsing without artificial floors.
- Support sticky round-robin (consecutive count threshold) and priority fill-first.

#### D.3: Retry Policy (`bridges/retry_policy.py`)
- Abolish rigid "Zero-Retry" policy.
- Implement bounded Layer 1 transport retries on the same account for transient 502/503/504 errors and socket connection resets (2 attempts with 2s delay).
- Maintain Level 3 account failover when transport retries exhaust or when 429/quota occurs.

#### D.4: Circuit Breaker Refactoring (`bridges/model_circuit_breaker.py`)
- De-globalize `ModelCircuitBreaker`. Key breaker states on `(provider, account_id, model)` so that a failure on Account A never blocks Account B.
- Fast-reject only when all accounts for that model are in cooldown/tripped.

---

### Phase E: Streaming, Timeouts, and Empty Response Prevention

#### E.1: Antigravity Native SSE Streaming (`bridges/gemini_bridge.py`)
- Update `call_antigravity` to use `streamGenerateContent?alt=sse` when streaming is requested.
- Stream incremental SSE chunks directly from upstream via `urllib.request.urlopen`.
- Parse incoming SSE candidate deltas and translate to OpenAI `chat.completion.chunk` in real time.
- Deliver initial tokens in 1–2 seconds, completely eliminating the 75s TTFT client freeze.

#### E.2: First-Chunk Header Buffering (`bridges/gemini_bridge.py` & `bridges/codex_bridge.py`)
- Do not flush HTTP 200 headers until the first valid content chunk (`text`, `thought`, or `tool_calls`) is confirmed.
- If upstream returns an empty response or safety block without candidates, intercept before headers and return HTTP 502/400 JSON error.
- Never emit `200 OK` followed by an empty body or empty delta.

#### E.3: Codex Reasoning Timeouts & SSE Terminal Formatting (`bridges/codex_bridge.py`)
- Increase chunk idle timeout from 30s to 180s to accommodate o1/o3/gpt-5 reasoning pauses.
- Format terminal abort events correctly:
  - For `/v1/chat/completions`: `data: {"error": ...}\n\ndata: [DONE]\n\n`.
  - For `/v1/responses`: `event: response.failed\ndata: {...}\n\n` (never send `[DONE]`).
- Discard empty content deltas (`delta.content === ""`) without finish reason.

---

### Phase F: Test Suite Migration, Verification & Documentation

#### F.1: Test Suite Audit & Categorization
- **KEEP**: Tests verifying account persistence, lock timeouts, auth token storage, JSON parity between CLI and Dashboard.
- **UPDATE**: Tests that asserted legacy policies:
  - `test_zero_retry_smart_failover.py`: Update to verify bounded transport retries and failover on all 429s.
  - `test_model_circuit_breaker.py`: Update to verify per-account scoping rather than global account lockout.
  - `test_retry_policy.py`: Update to verify 9Router retry hierarchy.
  - `test_sticky_account_policy.py`: Update to verify sticky round-robin with rotation on exhaustion.
  - `test_streaming_reliability.py`: Update to verify native SSE and empty response prevention.
- Add new test scenarios:
  1. Active account quota exhausted $\to$ immediate switch to next healthy candidate.
  2. All accounts exhausted $\to$ fast 503 with `Retry-After`, zero upstream requests.
  3. Antigravity HTTP 409 treated as quota exhaustion.
  4. Per-model lock leaves other models active on the same account.
  5. Transient 503 triggers in-place retry before account rotation.
  6. Empty upstream candidates return HTTP 502, never 200 OK + empty body.

#### F.2: Documentation & Migration
- Produce:
  - `AiPool_9Router_Behavior_Implementation_Report.md`
  - `AiPool_9Router_Migration_and_Rollback.md`
- Synchronize non-root production environment: `scripts/aipool_nonroot_migration.py --apply`.

---

### Phase G: Live A/B Verification
- Verify against live services on 8123 (Gemini), 8124 (Codex), and 8444 (Dashboard).
- Verify Hermes Agent compatibility (`GeminiFlash`, `Codex`).
- Verify OpenClaw compatibility.
- Ensure all 18 Final Report questions are documented and verified.
