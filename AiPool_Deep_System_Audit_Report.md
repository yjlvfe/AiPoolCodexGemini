# AiPool Deep System Reliability & Performance Audit Report
**Date:** September 16, 2026  
**Audited Target:** `/root/Projects/AiPoolCodexGemini` (Production: `/var/lib/aipool/app`)  
**Audit Mode:** READ-ONLY — no remediation performed.  

---

## 1. Executive Summary

- **Overall Status:** **DEGRADED** (Partially Unstable under episodic contention and client-disconnect / retry spikes)
- **Confirmed Findings Summary:**
  - **Critical:** 0
  - **High:** 3
  - **Medium:** 6
  - **Low:** 3
  - **Observability Gaps:** 5
  - **Unverified:** 2

The audit establishes that the core proxy mechanics, model conversion adapters, and basic round-robin rotations are operational. However, the system suffers from systemic design and implementation issues that explain the observed freezes, slow responses, agent stalls, and recovery failures:
1. **Unbounded Inbound & Upstream Timeouts / Retry Expansion:** Both bridges lack per-request wall-clock deadlines and body-read timeouts. Under provider hiccups, a single request can block worker threads for up to 40 to 120 minutes (via a hardcoded minimum 20-attempt retry floor).
2. **Lock Timeout Proceeds Without Lock:** In `cli/account_manager.py:180-186`, when the inter-process `flock` acquisition times out after 5 seconds, the critical section executes anyway (`locked_ok=False`), creating file corruption and race hazards during concurrent account operations.
3. **Broken-Pipe Tracebacks & False Failures:** Gemini bridge throws secondary unhandled `BrokenPipeError` tracebacks on client disconnect because error-handler writes lack exception containment.
4. **SQLite Bottleneck:** A single 111 MB SQLite database (`auth.db`) operating in legacy `DELETE` journal mode (not WAL) coordinates requests, audit data, durable recovery, account counters, and per-request device authentication.

---

## 2. System Map

- **Services (systemd):**
  - `codex.service` (PID 946): Port 8124 (`127.0.0.1:8124`), OpenAI/ChatGPT proxy bridge (`/v1/responses`, `/v1/chat/completions`).
  - `gemini.service` (PID 953): Port 8123 (`127.0.0.1:8123`), Google Antigravity bridge (`/v1/chat/completions`).
  - `dashboard.service` (PID 1723077): Port 8444 (`127.0.0.1:8444`), Management UI & Client API server.
- **Data & Runtime Storage:**
  - Database: `/var/lib/aipool/runtime/auth.db` (111.5 MB SQLite; holds `request_events`, `durable_requests`, `client_usage_counters`, `pool_cooldowns`, `devices`).
  - Account Stores: `/var/lib/aipool/accounts/codex` and `/var/lib/aipool/accounts/antigravity` (each holding slot subdirectories with `credentials.json`, `active` symlink/file, and `switch.lock`).
- **Integrations:**
  - Hermes Agent: Communicates with Gemini on 8123 (`chat_completions`) and Codex on 8124 (`codex_responses`).
  - OpenClaw: Native or proxy OpenAI-compatible routes mapped to localhost bridges.
- **Provider Paths:**
  - Codex Bridge: Outbound HTTPS to `https://chatgpt.com/backend-api/latents/chat/completions`.
  - Gemini Bridge: Outbound HTTPS to Google Antigravity endpoints (`daily`, `autopush`, `prod`).

---

## 3. Audit Coverage

| Area | Static | Runtime | Logs | Tests | Status |
|---|---|---|---|---|---|
| **Gemini Bridge** | Audited | Audited (Port 8123) | Audited (journal) | Audited (46+ tests) | **Degraded** (Synthetic streaming, BrokenPipe crashes) |
| **Codex Bridge** | Audited | Audited (Port 8124) | Audited (journal) | Audited | **Degraded** (No durable journal, 40m retry ceiling) |
| **Dashboard** | Audited | Audited (Port 8444) | Audited (journal) | Audited | **Operational** (Sync queries on request thread) |
| **Account Pools** | Audited | Audited (4 cdx, 8 ag) | Audited | Audited | **Vulnerable** (flock timeout proceeds unlocked) |
| **API / HTTP** | Audited | Verified loopback | Audited | Audited | **Vulnerable** (rfile.read slow-body hang) |
| **Streaming** | Audited | Audited | Audited | Audited | **Degraded** (No chunk idle deadline, synthetic Gemini) |
| **Retry / Timeouts**| Audited | Verified parameters | Audited | Audited | **Severe Flaw** (Enforced 20-retry floor) |
| **Concurrency / Locks**| Audited | Thread inspect | Audited | Audited | **High Risk** (ThreadingMixIn, DELETE mode DB) |
| **SQLite DB** | Audited | Read-only inspect | Audited | Audited | **Bottleneck** (111 MB, synchronous=2, DELETE mode) |
| **Systemd Units** | Audited | Verified units | Audited | N/A | **Clean** (Correct sandboxing & permissions) |
| **OS / Network** | Audited | Sockets & routes | Audited | N/A | **Healthy** (No OOM on AiPool, direct eth0 egress) |
| **Agent Integrations**| Audited | Configs inspected | Audited | Audited | **Partially Flawed** (Port override bug in integration script) |
| **Observability** | Audited | Code analysis | Audited | Audited | **Deficient** (No correlation ID, missing stream metrics) |
| **Recovery** | Audited | Replay verified | Audited | Audited | **Flawed** (Gemini PID-reuse hazard, Codex zero replay) |

---

## 4. Known Symptom Correlation

| Known Symptom | Confirmed Causes | Possible but Unverified | Evidence |
|---|---|---|---|
| **Slow responses** | 1. Hardcoded 20-attempt minimum retry loop (`retry_policy.py:10-24`).<br>2. Gemini synthetic buffering (waits for 100% provider completion before emitting first token).<br>3. `auth.db` lock contention during `_record()` analytics scan (`request_log.py:342-380`). | DNS latency or upstream network jitter. | `bridges/retry_policy.py:20`, `gemini_bridge.py:889-895`, journal logs showing multi-second durable deferred recovery. |
| **API call freezes** | 1. Inbound body read without deadline (`rfile.read(length)`).<br>2. Codex upstream `events()` `readline()` without chunk/idle deadline.<br>3. Dashboard background account refresh future awaited with unbounded `.result()`. | Network interface packet stall. | `bridges/codex_bridge.py:156,254`, `dashboard/app.py:340-341`. |
| **Agents stop continuing** | 1. Codex non-replayable stream interruption (`codex_bridge.py:391-396`) aborts with SSE error, leaving agent without terminal structured state.<br>2. Client disconnect / timeout propagation causes `BrokenPipeError` in bridge, leaving agents in stalled state. | Token budget cutoff on agent side. | `codex_bridge.py:452-462`, `gemini_bridge.py:688-693`, journald tracebacks. |
| **Recovery failures** | 1. Account lock timeout bypass (`cli/account_manager.py:180-186`) allows race condition during failover.<br>2. Requests without explicit `Idempotency-Key` generate UUIDs, preventing deduplicated replay after restart.<br>3. Codex has no durable recovery mechanism at all. | Stale OS lock descriptors. | `cli/account_manager.py:180-186`, `bridges/durable_requests.py:36-46`. |

---

## 5. Confirmed Issues

### [HIGH] 1. Account Lock Timeout Proceeds Unlocked
- **Component:** Account Management (`cli/account_manager.py`)
- **Location:** `cli/account_manager.py:155-197`
- **Trigger:** Two processes (e.g. Dashboard background refresh and a Bridge account switch) attempt to acquire `switch.lock` concurrently; one waits > 5.0 seconds.
- **Expected:** The manager must raise a `TimeoutError` or `LockError` and abort the critical section.
- **Actual:** The loop exits after 5 seconds with `locked_ok = False`, yet executes `yield` regardless!
- **Evidence:**
  ```python
  while True:
      try:
          fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
          locked_ok = True
          break
      except (BlockingIOError, OSError):
          if time.time() - start >= timeout:
              break
          time.sleep(0.05)
  held.add(key)
  try:
      yield  # <--- RUNS UNPROTECTED WITHOUT LOCK!
  ```
- **Impact:** Concurrent account switching, rotation, or deletion will execute simultaneously without mutual exclusion, leading to corrupted `active` files, partial credential reads, and race conditions.
- **Recovery Behavior:** Lock is not cleaned up because it was never acquired; subsequent operations encounter inconsistent credential state.
- **Explains Known Symptom:** YES (Recovery failures and strange account-switching stalls).
- **Confidence:** 100% (Direct code verification).
- **Recommended Remediation Direction:** Raise `TimeoutError("Failed to acquire store lock within timeout")` immediately when `time.time() - start >= timeout` instead of breaking to `yield`.

---

### [HIGH] 2. Enforced 20-Attempt Minimum Retry Loop Causes Massive Latency / Freezes
- **Component:** Retry Policy (`bridges/retry_policy.py`)
- **Location:** `bridges/retry_policy.py:10-24`
- **Trigger:** Transient 5xx status or provider connection hiccup.
- **Expected:** Retries should respect environment variables or abort quickly after 3–5 attempts.
- **Actual:** `provider_attempts()` clamps the retry count to a minimum of 20 (`max(20, attempts)`). With 120s urllib timeout per attempt plus exponential backoff, a failing request can remain pending in a bridge thread for up to 41 minutes (Codex) or 2 hours (Gemini multi-endpoint).
- **Evidence:**
  ```python
  def provider_attempts(override: str | int | None = None) -> int:
      # ...
      attempts = int(raw)
      return max(20, min(100, attempts))  # <--- CANNOT BE LESS THAN 20!
  ```
- **Impact:** Bridges exhaust thread pools and caller agents hang waiting for responses until agent-side timeouts trigger.
- **Recovery Behavior:** Only exhausts after 20 cycles, cycling through all accounts repeatedly.
- **Explains Known Symptom:** YES (Severe episodic response latency and frozen API calls).
- **Confidence:** 100% (Direct code & unit test verification).
- **Recommended Remediation Direction:** Lower the floor to 1 or 3 attempts and enforce a strict total wall-clock deadline on the overall request.

---

### [HIGH] 3. Unbounded Inbound Socket Read (`rfile.read(length)`)
- **Component:** Bridge HTTP Handlers (`bridges/gemini_bridge.py`, `bridges/codex_bridge.py`, `dashboard/server.py`)
- **Location:** `gemini_bridge.py:673`, `codex_bridge.py:254`, `dashboard/server.py:316`
- **Trigger:** Client connects, sends headers with `Content-Length: N`, and halts or trickles payload bytes.
- **Expected:** Socket read deadline should abort stalled reads after a short timeout (e.g. 10–30s).
- **Actual:** Calls `self.rfile.read(length)` directly on standard socket without a read timeout.
- **Evidence:**
  ```python
  raw_payload = json.loads(self.rfile.read(length))
  ```
- **Impact:** Worker threads in `ThreadingHTTPServer` stay permanently occupied, leading to thread exhaustion.
- **Recovery Behavior:** Connection remains held indefinitely until TCP keepalive expires or remote end terminates.
- **Explains Known Symptom:** YES (Frozen API calls).
- **Confidence:** 100%.
- **Recommended Remediation Direction:** Set `self.connection.settimeout(30.0)` prior to reading the request body.

---

### [MEDIUM] 4. Client Disconnect Causes Unhandled `BrokenPipeError` Cascade
- **Component:** Gemini Bridge (`bridges/gemini_bridge.py`)
- **Location:** `gemini_bridge.py:688-693`, `870-888`
- **Trigger:** Client cancels or closes connection while upstream generation is occurring.
- **Expected:** Bridge detects socket closure gracefully, aborts upstream, and cleans up without tracebacks.
- **Actual:** Bridge catches the initial exception, enters `_send_error()`, and unconditionally calls `_send_json()` which executes `self.wfile.write(data)`. This triggers `BrokenPipeError: [Errno 32] Broken pipe`, producing unhandled tracebacks in system journal.
- **Evidence:** Live system journal shows recurring `BrokenPipeError: [Errno 32] Broken pipe` traces originating at `gemini_bridge.py:688`.
- **Impact:** Misleading error logs, failed request metrics logged as internal errors, resources held until garbage collection.
- **Recovery Behavior:** Thread terminates on unhandled exception in `ThreadingMixIn`.
- **Explains Known Symptom:** YES (Recovery failures and noisy journal logs).
- **Confidence:** 100%.
- **Recommended Remediation Direction:** Wrap `_send_error` and `wfile.write` calls in `try...except (BrokenPipeError, ConnectionResetError)` and close silently.

---

### [MEDIUM] 5. Integration Endpoint Configuration Overrides Environment Ports
- **Component:** Integrations Script (`scripts/integrations.py`)
- **Location:** `scripts/integrations.py:68-70`
- **Trigger:** User sets custom `AG_BRIDGE_PORT` or `CODEX_BRIDGE_PORT` in the environment.
- **Expected:** Integration generator should use the configured port.
- **Actual:** The code parses the environment variable on line 68, but immediately overwrites it with hardcoded port strings on line 69–70!
- **Evidence:**
  ```python
  url = f"http://127.0.0.1:{int(os.environ.get('AG_BRIDGE_PORT' if kind == 'gemini' else 'CODEX_BRIDGE_PORT', '8123' if kind == 'gemini' else '8124'))}/v1"
  port_num = '8123' if kind == 'gemini' else ('8124' if kind == 'codex' else '8125')
  url = f"http://127.0.0.1:{port_num}/v1"  # <--- OVERWRITTEN!
  ```
- **Impact:** If non-standard ports are configured for testing or isolation, agent integrations will still target 8123/8124.
- **Recovery Behavior:** Static misconfiguration.
- **Explains Known Symptom:** NO.
- **Confidence:** 100%.
- **Recommended Remediation Direction:** Remove lines 69–70 so the environment variable resolution on line 68 takes effect.

---

### [MEDIUM] 6. SQLite Database Contention (`auth.db` in `DELETE` Mode)
- **Component:** SQLite Backend (`bridges/request_log.py`, `dashboard/app.py`, `bridges/durable_requests.py`)
- **Location:** `/var/lib/aipool/runtime/auth.db`
- **Trigger:** High volume of concurrent agent requests or dashboard polling while background analytics run.
- **Expected:** Non-blocking concurrent reads and fast writes via Write-Ahead Logging (WAL).
- **Actual:** PRAGMA audit revealed `journal_mode=delete`, `synchronous=2`, and 1000ms default busy timeout. Every request log insertion runs migrations, indexes, counter scans, and retention deletes inside an open transaction.
- **Evidence:** Live PRAGMA inspection: `PRAGMA journal_mode` returns `delete`. File size is 111.5 MB with only ~300 request rows.
- **Impact:** `sqlite3.OperationalError: database is locked` during spikes; requests queue up waiting for the database lock.
- **Recovery Behavior:** Transactions retry up to busy timeout (1–10s) before throwing unhandled errors.
- **Explains Known Symptom:** YES (Intermittent latency spikes).
- **Confidence:** 100%.
- **Recommended Remediation Direction:** Enable `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=10000;` on database creation.

---

### [MEDIUM] 7. Codex Stream Failure Leaves Agent Without Resumable State
- **Component:** Codex Bridge Streaming (`bridges/codex_bridge.py`)
- **Location:** `codex_bridge.py:391-396`, `bridges/stream_state.py:35-37`
- **Trigger:** Upstream ChatGPT disconnects or encounters an error midway through token emission.
- **Expected:** Either seamless fallback or a standardized terminal protocol error allowing graceful recovery.
- **Actual:** Bridge emits `event: error` with `Codex stream failed after it started; request was not replayed`, sets `interrupted`, and closes socket. Codex has no durable recovery store, so context is discarded.
- **Impact:** Agents receive incomplete JSON/code and abort execution without a resume marker.
- **Recovery Behavior:** Non-recoverable; agent task fails.
- **Explains Known Symptom:** YES (Agents stop continuing midway).
- **Confidence:** 100%.
- **Recommended Remediation Direction:** Implement partial durable chunk journaling or emit standard OpenAI finish reason `error`.

---

### [MEDIUM] 8. Gemini Streaming is Entirely Buffered (Fake Streaming)
- **Component:** Gemini Bridge (`bridges/gemini_bridge.py`)
- **Location:** `gemini_bridge.py:889-895`, `704-742`
- **Trigger:** Client requests `stream: true`.
- **Expected:** Upstream tokens streamed chunk-by-chunk to the client as they are generated.
- **Actual:** `_handle_stream()` executes a synchronous blocking call (`_call_with_retry()`) waiting for the *entire* provider response to finish. Once 100% complete, it converts the JSON and emits synthetic SSE events all at once.
- **Evidence:**
  ```python
  raw_resp = self._call_with_retry(payload, audit, model, chat)
  # ... converts entire raw_resp to chunks, then calls _send_cached_stream()
  ```
- **Impact:** First-token latency (TTFT) equals the entire generation time (often 10–60 seconds). If caller times out on TTFT, generation is wasted.
- **Recovery Behavior:** None.
- **Explains Known Symptom:** YES (Apparent agent freeze/hang while waiting for first chunk).
- **Confidence:** 100%.
- **Recommended Remediation Direction:** Migrate to true upstream chunked streaming proxying.

---

### [LOW] 9. Dashboard Background Refresh Awaits Futures Without Timeout
- **Component:** Dashboard Service (`dashboard/app.py`)
- **Location:** `dashboard/app.py:335-344`
- **Trigger:** External account status check hangs or encounters network delay.
- **Expected:** `future.result(timeout=...)` to prevent worker thread lockup.
- **Actual:** Calls `ag_future.result()` and `cdx_future.result()` without a timeout argument.
- **Evidence:** `dashboard/app.py:340`: `ag_res = ag_future.result()`.
- **Impact:** The background polling thread hangs indefinitely if account probe hangs.
- **Confidence:** 100%.
- **Recommended Remediation Direction:** Pass `timeout=15.0` to `result()`.

---

### [LOW] 10. Durable Request Recovery PID-Reuse Hazard
- **Component:** Durable Requests (`bridges/durable_requests.py`)
- **Location:** `durable_requests.py:174-215`
- **Trigger:** Gemini bridge crashes; another unrelated OS process is allocated the exact same PID.
- **Expected:** Stale requests recovered based on heartbeat/lease timestamps.
- **Actual:** Reclaims requests only if `os.kill(owner_pid, 0)` raises `ProcessLookupError`.
- **Impact:** Orphaned durable requests can remain locked in `in_progress` indefinitely.
- **Confidence:** 95%.
- **Recommended Remediation Direction:** Add a timestamp-based lease expiration (e.g. lease expires after 5 minutes).

---

### [LOW] 11. Model Catalog Lock Held Across External Fetch
- **Component:** Model Catalog (`bridges/model_catalog.py`)
- **Location:** `bridges/model_catalog.py:270-293`
- **Trigger:** Codex `/v1/models` request received.
- **Expected:** Cached catalog served lock-free or with minimal lock duration.
- **Actual:** Process-wide `RLock` is held while executing the remote fetcher.
- **Impact:** All threads requesting `/v1/models` block behind the remote fetcher.
- **Confidence:** 100%.
- **Recommended Remediation Direction:** Fetch models outside the lock and acquire the lock only to swap the cache reference.

---

## 6. Performance Findings

1. **Confirmed Bottlenecks:**
   - **Synthetic Gemini Streaming:** Waiting for full generation completion before sending headers introduces multi-second TTFT delays.
   - **Enforced 20 Retries:** Upstream 5xx errors keep worker threads blocked in exponential backoff sleep loops for minutes.
   - **SQLite DELETE Mode Single-Writer:** Serialized SQLite access with heavy retention scans on every request insertion.
2. **Episodic Bottlenecks:**
   - **Subprocess Spawns in Dashboard:** Account switches execute CLI subprocesses with 150s timeouts directly in HTTP worker threads.
3. **Cleared Suspects:**
   - **System CPU/RAM:** Server has 4.7 GiB RAM free, low CPU load (1.93 on 10 cores), disk usage 21%.
   - **Network Routing:** Direct `eth0` routing for localhost; Docker NordVPN containers do not intercept AiPool loopback ports.
4. **Missing Metrics:**
   - No request phase metrics (no breakdown between DNS, connect, TTFT, generation, DB write).

---

## 7. Freeze / Hang Findings

1. **Inbound Body Read Hang (`rfile.read(length)`):**
   - Unbounded read in `codex_bridge.py:254` and `gemini_bridge.py:673`.
2. **Streaming Upstream Idle Hang:**
   - Codex `events()` (`codex_bridge.py:156`) reads lines from upstream socket with no chunk idle timeout. If upstream TCP connection stays open without sending bytes, the bridge hangs until socket timeout (120s) or forever if keepalive is active.
3. **Dashboard Background Future Hang:**
   - `dashboard/app.py:340` awaits worker futures with unbounded `.result()`.
4. **Unlocked Critical Section Execution:**
   - `cli/account_manager.py:186` executes unprotected on lock timeout, potentially leading to mutual wait states during concurrent CLI invocations.

---

## 8. Timeout Matrix

| Layer / Path | Connect Timeout | Read Timeout | Write Timeout | Idle / Chunk Timeout | Total Wall-Clock Cap | Recovery on Expiry |
|---|---|---|---|---|---|---|
| **Codex Upstream HTTP** | 120s (socket) | 120s (socket) | None | **Missing** | **Missing** | Retry with next account |
| **Gemini Upstream HTTP**| 120s (socket) | 120s (socket) | None | N/A (buffered) | **Missing** | Retry with next endpoint/account |
| **Codex Inbound Body** | N/A | **Missing** | N/A | **Missing** | **Missing** | Thread occupied indefinitely |
| **Gemini Inbound Body**| N/A | **Missing** | N/A | N/A | **Missing** | Thread occupied indefinitely |
| **Client Response Write**| N/A | N/A | **Missing** | **Missing** | **Missing** | Thread blocks on slow client |
| **Dashboard Inbound Body**| N/A | **Missing** | N/A | N/A | **Missing** | Thread occupied |
| **Dashboard CLI Process**| N/A | N/A | N/A | N/A | 120s–150s | `subprocess.TimeoutExpired` |
| **Account File Lock** | N/A | N/A | N/A | N/A | 5.0s | **Bypassed (Yields unlocked!)** |
| **SQLite Busy Wait** | N/A | 1000ms | 1000ms | N/A | 10s (some paths) | `OperationalError: database locked` |

---

## 9. Retry / Failover Matrix

| Failure Event | Retry? | Max Attempts | Backoff Formula | Account Rotated? | Provider Rotated? | Safe After Partial Stream? |
|---|---|---|---|---|---|---|
| **HTTP 401 / 403** | Yes | 20 (enforced floor) | `Retry-After` or exp | Yes (cooldown) | No | **No** (Before output only) |
| **HTTP 429 (Rate Limit)**| Yes | 20 (enforced floor) | `Retry-After` or exp | Yes (cooldown) | No | **No** (Before output only) |
| **HTTP 408** | Inconsistent | 20 | Exponential (0.25–5s) | Outage marked | No | **No** |
| **HTTP 5xx (Server Error)**| Yes | 20 (enforced floor) | Exponential (0.25–5s) | Outage marked | No | **No** |
| **Network / Reset Error** | Yes | 20 (enforced floor) | Exponential (0.25–5s) | Outage marked | No | **No** |
| **Partial Stream Disconnect**| **No** | 0 | None | No | No | Aborts with `STREAM_INTERRUPTED` |
| **Inbound Body Read Error** | No | 0 | None | No | No | Returns 400 Bad Request |

---

## 10. Resilience Matrix

| Failure Scenario | Detected? | Timeout? | Retried? | Resources Cleaned? | Caller Notified? | Next Request Safe? |
|---|---|---|---|---|---|---|
| **Upstream Socket Timeout** | Yes | Yes (120s) | Yes (up to 20x) | Socket closed | Yes (eventually) | Yes |
| **Upstream 429 Quota** | Yes | Immediate | Yes (rotates slot)| Yes | Only after 20x fail | Yes |
| **Client Disconnect Mid-Stream** | Partially | None | No | Unhandled traceback | No (client gone) | Yes (thread exits) |
| **Client Disconnect Mid-Buffer** | No | None | Upstream finishes | Wasted generation | No | Yes |
| **SQLite Database Lock** | Yes | Yes (1s–10s)| No | Rollback triggered | Returns 500 error | Yes |
| **Account Lock Contention** | Detected | Yes (5s) | **Proceeds Unlocked**| No | No | **VULNERABLE (Race condition)** |
| **Service Crash Mid-Request** | Startup | N/A | Replayed (Gemini)| Stale rows cleaned | Caller sees connection reset | Yes (Gemini) / Lost (Codex) |

---

## 11. Concurrency / Locking Findings

- **Threading Model:** Both bridges and the dashboard utilize Python's standard library `socketserver.ThreadingMixIn`. Every inbound HTTP request spawns an unmanaged native thread.
- **Lock Leaks & Bypasses:** `cli/account_manager.py` exhibits a severe defect where failure to obtain `switch.lock` after 5 seconds does not abort the operation, allowing multiple processes to execute critical account switching sections concurrently.
- **RLock in Model Catalog:** `model_catalog.py` holds a process-wide mutex while performing network I/O, which serializes requests to `/v1/models`.

---

## 12. Database Findings (Read-Only)

- **File:** `/var/lib/aipool/runtime/auth.db` (Size: 111,550,464 bytes / ~111 MB).
- **Integrity:** `PRAGMA integrity_check` returned `ok`.
- **Journal Mode:** `DELETE` (Rollback journal). No WAL mode enabled.
- **Lock Contention:** Any concurrent write locks the entire database.
- **Bloat Analysis:** The database holds only 300 request events and ~88 durable requests. The 111 MB size indicates significant unvacuumed space and large serialized request payloads stored in SQLite columns.

---

## 13. System / Resource Findings

- **CPU / RAM:** Server is healthy (Load: 1.93, RAM: 3.1G used / 4.7G free, Swap: 3.5G used).
- **File Descriptors:** Very low usage (Codex: 16 FDs, Gemini: 10 FDs, Dashboard: 12 FDs vs 524,288 limit).
- **Systemd Units:** All three units are configured securely with `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, and `Restart=on-failure`.
- **Recent Restarts:** Codex and Gemini have been running continuously for >4.5 hours with `NRestarts=0`.

---

## 14. Network / Connection Findings

- All services bind strictly to `127.0.0.1` (`8123`, `8124`, `8444`).
- Localhost network path is direct and bypasses Docker VPN containers (`yjdownloadvideo-nordvpn-1` and `traffic2-vpn`).
- Sockets show normal TCP state distribution (317 `TIME_WAIT`, 41 `ESTABLISHED`, no socket leak in `CLOSE_WAIT`).

---

## 15. Agent Continuity Findings (Hermes & OpenClaw)

1. **Non-Resumable Codex Streams:** When Codex encounters an upstream interruption, it emits a raw SSE error message and closes the socket. Agents cannot resume the stream and fail the running task.
2. **Missing Request Correlation:** Client-provided `X-Request-ID` is ignored except for durable caching seeds in Gemini. Bridge logs do not output request IDs, making it impossible to correlate an agent's failure to a specific bridge log entry.
3. **Port Override Misconfiguration:** `scripts/integrations.py` hardcodes ports 8123/8124, preventing integration setups from using customized ports.

---

## 16. Observability Gaps

1. **No Correlation IDs:** Neither bridge returns or logs an end-to-end request correlation ID.
2. **Swallowed Accounting Exceptions:** Multiple `except Exception: pass` blocks in `bridges/request_log.py` (lines 302, 328, 377) silently discard counter update failures.
3. **Missing TTFT & Chunk Timing:** Latency is only measured as total elapsed time; there is no metric for Time-To-First-Token.
4. **Disabled Access Logging:** `log_message()` is overridden with `pass` in both bridges, preventing standard HTTP access logging.

---

## 17. Test Coverage Gaps

The test suite (164 passing tests) has critical blind spots:
1. **No Client Disconnect Tests:** Zero tests simulating client socket closure during generation or error reporting.
2. **No Slow-Client / Trickle Tests:** No tests asserting timeouts on slow inbound body reads.
3. **No Database Lock Contention Tests:** No tests simulating SQLite locking during concurrent request insertions.
4. **No Real Provider Stream Failure Tests:** Gemini stream tests only test buffered replay; missing tests for upstream EOF truncation or mid-stream disconnects.

---

## 18. Verified Healthy Areas

- **Process Sandboxing & Systemd Policies:** `ProtectSystem=strict`, restricted capability sets, and user permissions (`aipool:aipool`) are cleanly implemented.
- **Account Isolation:** Codex and Antigravity pools are strictly separated.
- **Basic Failover & Cooldown:** HTTP 429 properly rotates slots and calculates bounded cooldown timers (1–3600s).
- **Memory Safety:** No memory leaks or runaway RSS growth detected in active services.
- **Database Integrity:** SQLite file is free of page corruption.

---

## 19. Unverified Areas

1. **Upstream Google Antigravity Internal Latency:** Exact internal queue times within Google's endpoints cannot be measured from outside.
2. **Long-Term File Descriptor Saturation:** While FD counts are currently low, behavior under 100+ concurrent slow trickling clients could not be safely verified on production.

---

## 20. Priority Remediation Order

### P0 (Immediate Reliability Blockers)
1. **Fix Account Lock Timeout:** In `cli/account_manager.py:180-186`, raise an exception when `time.time() - start >= timeout` instead of continuing into `yield` with `locked_ok=False`.
2. **Add Inbound Body Read Timeout:** Set a socket timeout (e.g. 30s) before calling `rfile.read()` in all bridge handlers.
3. **Normalize Retry Policy Floor:** Change `max(20, min(100, attempts))` in `retry_policy.py:20` to allow sensible retry ceilings (e.g. 3–5 attempts) and implement an overall request deadline.

### P1 (Stability & Performance)
4. **Switch SQLite to WAL Mode:** Execute `PRAGMA journal_mode=WAL;` on `auth.db` to eliminate writer contention.
5. **Catch BrokenPipe on Error Reporting:** Wrap `_send_error` in `try...except (BrokenPipeError, ConnectionResetError): pass` in `gemini_bridge.py`.
6. **Fix Integrations Port Overwrite:** Remove hardcoded port overrides in `scripts/integrations.py:69-70`.

### P2 (Continuity & Observability)
7. **Migrate Gemini to True Streaming:** Replace synthetic full-response buffering with live token proxying.
8. **Add Request ID Propagation:** Return `X-Request-ID` in HTTP headers and prepend it to all log messages.
9. **Add Chunk Idle Timeout:** Implement a read deadline on `readline()` in Codex streaming.

---

## 21. Final Verdict

1. **ما السبب/الأسباب المثبتة للبطء؟**  
   - حلقة إعادة المحاولة القسرية بحد أدنى 20 محاولة (`retry_policy.py:20`)، مما يجعل الطلب ينتظر عشرات الدقائق عند حدوث أي خطأ في المزود.  
   - البث الوهمي في Gemini؛ حيث ينتظر اكتمال الرد 100% وتوليد كل التوكنات قبل إرسال أول بايت للعميل.  
   - قفل قاعدة بيانات SQLite (`auth.db`) بوضع `DELETE` القديم وتكرار عمليات الفحص الثقيلة مع كل طلب.
2. **هل يوجد API path يمكن أن يتجمد؟**  
   - نعم؛ قراءة جسم الطلب الوارد `rfile.read(length)` بدون socket timeout، وقراءة سطور البث `response.readline()` في Codex بدون idle timeout، وانتظار `future.result()` في خلفية الـ Dashboard.
3. **هل يوجد Timeout ناقص أو غير bounded؟**  
   - نعم؛ غياب كامل للـ Wall-Clock Total Timeout للطلب ككل، وغياب Inbound Read Timeout.
4. **هل يوجد Retry loop أو failover غير آمن؟**  
   - نعم؛ حلقة التدوير تفرض 20 محاولة حتى لو تم طلب عدد أقل عبر المتغيرات البيئية.
5. **هل يوجد lock/DB contention؟**  
   - نعم؛ قاعدة `auth.db` بحجم 111 ميجابايت بدون نمط WAL، وخطأ قاتل في `account_manager.py` يتجاوز القفل عند انتهاء الـ 5 ثوانٍ وينفذ العمليات بالتوازي دون قفل.
6. **هل يمكن failure واحد أن يترك الوكيل متوقفًا؟**  
   - نعم؛ انقطاع البث في Codex يرسل رسالة SSE خطأ ويقطع الاتصال دون إمكانية استئناف، وانقطاع اتصال العميل في Gemini يسبب `BrokenPipeError` يسقط الثريد.
7. **هل النظام يتعافى ويقبل الطلب التالي دائمًا؟**  
   - يتعافى غالباً للطلبات الجديدة المستقلة، ولكنه يفشل في استعادة سياق الوكلاء المتوقفين في منتصف البث، وقد يدخل في تضارب حسابات إذا تزامنت عمليتا تدوير.
8. **هل توجد resource/systemd/network مشاكل؟**  
   - لا؛ موارد الخادم ومسارات الشبكة ووحدات systemd سليمة ونظيفة تماماً.
9. **هل Hermes/OpenClaw continuity سليمة؟**  
   - غير سليمة تماماً؛ تتأثر بالـ 20 retries وبانقطاع البث المفاجئ دون علامات إكمال واضحة.
10. **هل هناك مشاكل خفية مؤكدة غير الأعراض التي ذكرها المستخدم؟**  
    - نعم؛ ثغرة تجاوز قفل الحسابات (`cli/account_manager.py:186`)، وكتابة عناوين المنافذ الثابتة في كود الـ integrations متجاهلة إعدادات البيئة.
11. **ما الأشياء التي بقيت غير مثبتة؟**  
    - أزمنة التأخير الداخلية لسيرفرات Google Antigravity، وسلوك واصفات الملفات تحت ضغط مئات الاتصالات المتزامنة البطيئة.
12. **هل النظام حاليًا HEALTHY أم DEGRADED أم UNSTABLE أم CRITICAL؟**  
    - النظام حالياً: **DEGRADED** (يعمل في المسار السليم الفردي، لكنه يتدهور ويتجمد تحت الأخطاء العابرة أو انقطاع الاتصال أو تزامن العمليات).

---

`Audit mode: READ-ONLY — no remediation performed.`
