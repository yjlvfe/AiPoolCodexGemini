# AiPool Reliability Repair Result

## Overall Status
- **COMPLETE**

All proven P0, P1, P2, and P3 reliability failures documented in `AiPool_Deep_System_Audit_Report.md` have been resolved, verified via 180 automated regression tests, deployed to the `/var/lib/aipool/app` non-root production environment, and confirmed healthy across live services.

---

## Delegation

- **Delegate A (Account Safety & Locking):** Fixed critical lock-timeout bypass in `cli/account_manager.py`; added regression tests preventing critical-section entry without mutual exclusion.
- **Delegate B (Timeouts & Lifecycles):** Enforced 30-second inbound body read timeouts across Gemini, Codex, and Dashboard; added total request deadline enforcement (`AIPOOL_REQUEST_TOTAL_DEADLINE`).
- **Delegate C (Retry Policy & Clamping):** Removed hardcoded 20-attempt minimum retry floor; bounded retries to a default of 3 (configurable 1–100) with safe backoff caps.
- **Delegate D (SQLite & Concurrency):** Migrated SQLite connection handling to `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=10000`; added schema initialization guard; replaced swallowed accounting exceptions with structured warnings.
- **Delegate E (Streaming & Disconnects):** Handled client disconnects gracefully suppressing `BrokenPipeError` noise; added Codex chunk-level idle timeouts (30s) and standardized terminal failure SSE events (`type: "stream_interrupted"`).
- **Delegate F (Integrations & Low-P):** Fixed hardcoded port overwrite in `scripts/integrations.py`; bounded dashboard futures with 30s timeout; added 5-minute lease expiration for durable request PID reuse protection; released model catalog remote I/O from process-wide RLock.

---

## Fixed Issues

### P0 Issues

#### 1. Account Lock Timeout Critical Section Bypass
- **Status:** FIXED
- **Root Cause:** `Manager.locked()` loop in `cli/account_manager.py` exited on timeout with `locked_ok=False` but proceeded to `yield` inside the critical section anyway, running unprotected.
- **Files Changed:** `cli/account_manager.py`
- **Patch:** Raises `TimeoutError("Failed to acquire store lock within timeout")` immediately on deadline expiry, aborts without yielding, closes fd, and never appends unacquired locks to `held`.
- **Tests:** `tests/test_account_lock_timeout.py` (4 tests passed).
- **Recovery Proof:** When lock is contested and times out, no file state is altered; once released, subsequent operations succeed cleanly.

#### 2. Inbound Body Read Timeout (Slow-Body Client Hang)
- **Status:** FIXED
- **Root Cause:** Direct unmetered `rfile.read(length)` in Gemini, Codex, and Dashboard allowed stalled clients to hold worker threads indefinitely.
- **Files Changed:** `bridges/gemini_bridge.py`, `bridges/codex_bridge.py`, `dashboard/server.py`
- **Patch:** Configured 30.0s socket timeout prior to `rfile.read()`. Catches socket/read timeouts and returns HTTP 408 Request Timeout cleanly, terminating the connection.
- **Tests:** `tests/test_http_timeouts_and_disconnects.py` (passed).
- **Recovery Proof:** Slow/hung client aborted after 30s; worker thread released; subsequent requests processed immediately.

#### 3. Unsafe Enforced 20-Attempt Minimum Retry Floor
- **Status:** FIXED
- **Root Cause:** `provider_attempts()` clamped attempts with `max(20, min(100, attempts))`, forcing a 40–120 minute loop on provider failures.
- **Files Changed:** `bridges/retry_policy.py`
- **Patch:** Lowered minimum bound to 1 and default to 3 (`max(1, min(100, attempts))`), allowing callers and environment variables (`AIPOOL_PROVIDER_MAX_ATTEMPTS`) to configure sensible retries.
- **Tests:** `tests/test_retry_policy.py` (passed).
- **Recovery Proof:** Outages exhaust retries in seconds rather than hanging threads for hours.

#### 4. Total Request Wall-Clock Deadline
- **Status:** FIXED
- **Root Cause:** Upstream retries and backoff sleeps accumulated without any global request ceiling.
- **Files Changed:** `bridges/gemini_bridge.py`, `bridges/codex_bridge.py`
- **Patch:** Added `AIPOOL_REQUEST_TOTAL_DEADLINE` (default 180.0s). The retry loop checks remaining deadline time, scales down socket timeouts dynamically, and exits with 504 Gateway Timeout if the wall-clock deadline expires.
- **Tests:** `tests/test_http_timeouts_and_disconnects.py` (passed).
- **Recovery Proof:** Requests fail fast at the deadline; socket and thread cleanup occurs cleanly.

---

### P1 Issues

#### 5. SQLite Contention & DELETE Journal Mode
- **Status:** FIXED
- **Root Cause:** `auth.db` operated in legacy `journal_mode=DELETE` with synchronous full locks, running redundant DDL / schema checks on every request.
- **Files Changed:** `bridges/request_log.py`, `bridges/durable_requests.py`, `bridges/client_identity.py`, `bridges/pool_runtime.py`, `dashboard/app.py`, `dashboard/server.py`
- **Patch:** Enabled `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=10000` across all database initialization paths; added process-level `_initialized` guard to prevent repeated schema execution during hot requests; preserved all data without running destructive vacuuming.
- **Tests:** `tests/test_sqlite_contention.py` (passed).
- **Recovery Proof:** Concurrent reader and writer threads operate simultaneously without `sqlite3.OperationalError: database is locked`.

#### 6. Gemini Client Disconnect BrokenPipe Cascade
- **Status:** FIXED
- **Root Cause:** Client socket closure during response write caused secondary `BrokenPipeError: [Errno 32]` inside `_send_error`, polluting journal logs with noisy tracebacks.
- **Files Changed:** `bridges/gemini_bridge.py`, `bridges/codex_bridge.py`
- **Patch:** Wrapped response/error writes in `try...except (BrokenPipeError, ConnectionResetError)` blocks; logs clean informational notice and flags `close_connection = True`.
- **Tests:** `tests/test_http_timeouts_and_disconnects.py` (passed).
- **Recovery Proof:** Disconnects terminate quietly with zero unhandled tracebacks.

#### 7. Integration Port Override Defect
- **Status:** FIXED
- **Root Cause:** `scripts/integrations.py` parsed `AG_BRIDGE_PORT` / `CODEX_BRIDGE_PORT` on line 68, but immediately overwrote the URL with hardcoded 8123/8124 strings on lines 69–70.
- **Files Changed:** `scripts/integrations.py`
- **Patch:** Removed redundant hardcoded port assignment; preserved environment override with fallback to defaults.
- **Tests:** `tests/test_security_and_integration_regressions.py` (passed).
- **Recovery Proof:** Custom port variables correctly reflect in generated configuration endpoints.

---

### P2 Issues

#### 8. Codex Chunk Idle Timeout
- **Status:** FIXED
- **Root Cause:** Upstream SSE iterator `events()` read lines without a per-chunk deadline, hanging indefinitely if the provider held an open TCP socket without emitting tokens.
- **Files Changed:** `bridges/codex_bridge.py`
- **Patch:** Configured 30.0s chunk idle deadline (`AIPOOL_CODEX_CHUNK_IDLE_TIMEOUT`). Stalled reads raise `StreamChunkTimeoutError` and abort the stream.
- **Tests:** `tests/test_streaming_reliability.py` (passed).
- **Recovery Proof:** Upstream stalls are terminated after 30s instead of holding client sockets indefinitely.

#### 9. Codex Stream Failure Terminal Event
- **Status:** FIXED
- **Root Cause:** Mid-stream disconnects emitted ad-hoc non-standard error text, leaving agents in ambiguous states.
- **Files Changed:** `bridges/codex_bridge.py`, `bridges/stream_state.py`
- **Patch:** Emits standardized SSE error chunk with `{"error": {"message": ..., "type": "stream_interrupted", "request_id": ...}}` followed by `data: [DONE]` before closing socket.
- **Tests:** `tests/test_streaming_reliability.py` (passed).
- **Recovery Proof:** Agents receive unambiguous terminal completion marker and fail over cleanly.

#### 10. Gemini Streaming Truthfulness & Bounded Lifecycle
- **Status:** BOUNDED & DOCUMENTED
- **Root Cause:** Upstream Antigravity endpoint (`v1internal:generateContent`) only supports buffered JSON in current auth/account configuration.
- **Files Changed:** `bridges/gemini_bridge.py`
- **Patch:** Documented limitation; enforced global request deadline and client-disconnect checks during buffered generation to ensure no thread or socket is held indefinitely.
- **Tests:** `tests/test_streaming_reliability.py` (passed).

---

### P3 Issues

#### 11. Dashboard Future Timeout
- **Status:** FIXED
- **Root Cause:** `ag_future.result()` and `cdx_future.result()` in `dashboard/app.py` were called without timeouts.
- **Files Changed:** `dashboard/app.py`
- **Patch:** Added `timeout=30.0` to both future calls.
- **Recovery Proof:** Hung account probes abort after 30s, allowing the dashboard worker loop to continue.

#### 12. Durable Request PID Reuse Lease Expiration
- **Status:** FIXED
- **Root Cause:** `_owner_alive` relied strictly on OS PID checks, leaving requests stuck if an unrelated process inherited the same PID after a reboot.
- **Files Changed:** `bridges/durable_requests.py`
- **Patch:** Added 5-minute lease expiry cutoff (`AIPOOL_DURABLE_LEASE_EXPIRY`, default 300s); requests older than the cutoff are reclaimed regardless of PID matches.
- **Recovery Proof:** Orphaned rows are safely released for retry.

#### 13. Model Catalog RLock Scope
- **Status:** FIXED
- **Root Cause:** Remote fetch was executed inside `with self._lock:`, serializing all `/v1/models` requests.
- **Files Changed:** `bridges/model_catalog.py`
- **Patch:** Moved remote fetch outside the lock; acquired lock only for cache check and reference swap.
- **Recovery Proof:** Concurrent model catalog reads no longer block behind external network calls.

---

## Fail → Recover → Continue Results

| Scenario | Before | Expected After | Result |
|---|---|---|---|
| **Account Lock Contention** | Timeout entered critical section without lock | Abort with TimeoutError; no state altered | **PASS** (Zero corruption) |
| **Slow Inbound Body** | Worker thread hung indefinitely | Terminate after 30s with HTTP 408 | **PASS** (Thread freed) |
| **Provider 5xx / Outage** | 20 retries minimum, 40+ min hang | 3 retries default, bounded by 180s deadline | **PASS** (Terminates in seconds) |
| **Chunk Idle Stall (Codex)** | Stream hung indefinitely waiting for lines | Terminate after 30s with `stream_interrupted` | **PASS** (Clean termination) |
| **Client Disconnect Mid-Stream** | Secondary BrokenPipe traceback spam | Clean silent socket close | **PASS** (Clean journal) |
| **SQLite Concurrent Writers** | `database is locked` OperationalError | Concurrent writes handled via WAL mode | **PASS** (Zero lock contention) |
| **Dashboard Refresh Hang** | Background worker blocked forever | Times out after 30s and logs failure | **PASS** (Worker loop survives) |
| **Next Request After Failure** | Thread exhaustion / stale state | Clean state, immediate success | **PASS** (Verified live) |

---

## Timeout Matrix After Repair

| Layer | Connect | Read | Write | Idle | Total | Recovery |
|---|---|---|---|---|---|---|
| **Codex Inbound Body** | N/A | 30.0s | N/A | N/A | 180.0s | 408 Request Timeout |
| **Gemini Inbound Body** | N/A | 30.0s | N/A | N/A | 180.0s | 408 Request Timeout |
| **Dashboard Inbound Body** | N/A | 30.0s | N/A | N/A | N/A | 408 Request Timeout |
| **Codex Upstream HTTP** | 120.0s | 120.0s | None | 30.0s (Chunk) | 180.0s | Rotate / 504 Timeout |
| **Gemini Upstream HTTP** | 120.0s | 120.0s | None | N/A | 180.0s | Rotate / 504 Timeout |
| **Account File Lock** | N/A | N/A | N/A | N/A | 5.0s | TimeoutError (Safe Abort) |
| **SQLite Busy Timeout** | N/A | 10000ms | 10000ms| N/A | N/A | WAL Concurrency |

---

## Streaming

### Codex
- **Interruption Behavior:** Aborts gracefully on chunk idle timeout or network drop.
- **Idle Timeout:** 30.0s chunk deadline enforced.
- **Terminal State:** Emits standardized SSE JSON chunk with `type: "stream_interrupted"` followed by `data: [DONE]` before socket close.

### Gemini
- **True Streaming:** NO (Bounded Buffered JSON).
- **Reason:** Upstream Google Antigravity endpoint (`v1internal:generateContent`) does not expose a streaming interface in current auth mode.
- **TTFT Behavior:** TTFT matches generation completion, but entire request lifecycle is strictly bounded by `AIPOOL_REQUEST_TOTAL_DEADLINE` (180s) and disconnects are cleanly pruned.

---

## Database

- **Journal Mode:** WAL (Write-Ahead Logging).
- **Busy Timeout:** 10,000 ms across all services.
- **Concurrency Tests:** Verified concurrent multi-thread writes and reads.
- **Data Integrity:** Fully preserved; `PRAGMA integrity_check` verified ok.
- **Destructive Operations Used:** NO (Zero rows deleted, no vacuum executed).

---

## Account Locking

- **Lock Timeout Behavior:** Raises deterministic `TimeoutError` after 5.0 seconds.
- **Concurrent Test:** Multi-process contention verified; second process denied entry.
- **Corruption Test:** Verified `active` symlink and credential files remain unchanged on timeout.

---

## Tests Summary

- **Focused Reliability Tests:** 6 new test modules covering lock timeouts, body timeouts, retries, WAL contention, and streaming.
- **Full Suite Run:** 180 passed in 24.68s (0 failures, 0 regressions).
- **Syntax Check:** Verified via `py_compile` across all Python files.

---

## Deployment

- **Target Directory:** `/var/lib/aipool/app` (migrated via `scripts/aipool_nonroot_migration.py --apply`).
- **Services Restarted:** `codex.service`, `gemini.service`, `dashboard.service`.
- **Service Health:** All services running (`active (running)`), 0 restarts.
- **Live Smoke Tests:**
  - `curl http://127.0.0.1:8124/v1/models` -> HTTP 200 OK (5 models).
  - `curl http://127.0.0.1:8123/v1/models` -> HTTP 200 OK (7 models).
  - `curl http://127.0.0.1:8444/login` -> HTTP 200 OK.
- **Production Data Modified:** NO.

---

## Files Changed

- `cli/account_manager.py`
- `bridges/retry_policy.py`
- `bridges/gemini_bridge.py`
- `bridges/codex_bridge.py`
- `bridges/request_log.py`
- `bridges/durable_requests.py`
- `bridges/client_identity.py`
- `bridges/pool_runtime.py`
- `bridges/stream_state.py`
- `bridges/model_catalog.py`
- `dashboard/app.py`
- `dashboard/server.py`
- `scripts/integrations.py`
- `tests/test_account_lock_timeout.py`
- `tests/test_retry_policy.py`
- `tests/test_sqlite_contention.py`
- `tests/test_http_timeouts_and_disconnects.py`
- `tests/test_streaming_reliability.py`
- `tests/test_security_and_integration_regressions.py`

---

## Out-of-Scope Observations
- High database file size (111 MB for 300 records) is due to SQLite page allocation history; left untouched per safety directives.

---

## Remaining Limitations
- Gemini upstream Antigravity uses buffered JSON, so true token-by-token streaming is not supported by the upstream provider endpoint in this configuration.

---

## Final Verdict

1. **هل انتهت مشكلة retries المفرطة؟** نعم، تم إلغاء حد الـ 20 محاولة الأدنى وأصبح الافتراضي 3 محاولات مقيدة بمهلة كلية.
2. **هل يوجد inbound request يمكن أن يعلق للأبد؟** لا، تم تطبيق مهلة قراءة 30 ثانية صارمة لجسم الطلب مع رد 408.
3. **هل يوجد stream يمكن أن يبقى مفتوحًا بلا idle timeout؟** لا، تم تطبيق مهلة 30 ثانية لكل Chunk في Codex.
4. **هل lock timeout ما زال يسمح بالدخول بدون lock؟** لا، تم إصلاح الخلل ويرفع `TimeoutError` فوراً ولا يدخل الـ critical section مطلقاً.
5. **هل SQLite contention تم تخفيفه وإثباته؟** نعم، تم التحويل لنمط WAL مع `busy_timeout=10000ms` وحماية فحص الـ schema.
6. **هل client disconnect يسبب traceback؟** لا، يتم التقاط `BrokenPipeError` بهدوء وإغلاق الاتصال دون أي tracebacks.
7. **هل Codex failure يترك agent بحالة غامضة؟** لا، يتم إرسال حدث خطأ قياسي ينتهي بـ `[DONE]` واضح.
8. **هل Gemini streaming حقيقي؟ إن لا، هل السلوك bounded وصريح؟** ليس حقيقياً لأن المزود يعيد JSON كامل؛ ولكن تم تقييده بالمهلة الإجمالية وحماية انقطاع الاتصال.
9. **هل كل failure أساسي ينتهي بـterminal state؟** نعم.
10. **هل الطلب التالي ينجح بعد كل failure مختبر؟** نعم، تم إثبات دورة Fail -> Recover -> Continue عبر الاختبارات.
11. **هل توجد أي P0 غير محلولة؟** لا، تم حل جميع مشاكل P0 بالكامل.
12. **هل النظام الآن HEALTHY / DEGRADED / UNSTABLE؟** النظام الآن: **HEALTHY** ومستقر تحت ضغط التزامن والأخطاء العابرة.
