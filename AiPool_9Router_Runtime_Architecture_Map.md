# 9Router Runtime Architecture & Request Lifecycle Map

## 1. System Entry Points & Network Topology
- **Host Process Entry**: `custom-server.js` wraps Node HTTP server.
  - Generates process security token (`PEER_TOKEN`).
  - Peer verification: Extracts unspoofable client IP from raw TCP socket (`req.socket.remoteAddress`). Replaces forwarded headers with authoritative `x-9r-real-ip`.
  - Upgrades JBR HTTP/2 cleartext (h2c) frames to HTTP/1.1 IncomingMessages.
  - Boots `startBackgroundTokenRefresh` on server `listening` event.
- **Next.js Rewrites**:
  - `/v1/:path*` $\to$ `/api/v1/:path*`
  - `/codex/:path*` & `/responses` $\to$ `/api/v1/responses`
- **Dashboard Guard (`src/dashboardGuard.js`)**:
  - `PUBLIC_PREFIXES = ["/v1", "/v1beta", "/api/v1", "/api/v1beta", "/codex", "/responses"]` bypass cookie auth and pass through to endpoint-level API key validation.

## 2. Request Lifecycle Pipeline (`/v1/chat/completions`)

```
 [Client Request]
        │
        ▼
 ┌──────────────────────────────────────────────────────────┐
 │ custom-server.js                                         │
 │ - Resolves real client IP from TCP socket               │
 │ - Attaches x-9r-real-ip and x-9r-peer-token              │
 └──────────────────────────┬───────────────────────────────┘
                            │
                            ▼
 ┌──────────────────────────────────────────────────────────┐
 │ /src/app/api/v1/chat/completions/route.js                │
 │ - Ensures translator initialization (initTranslators()) │
 │ - Calls handleChat(request)                              │
 └──────────────────────────┬───────────────────────────────┘
                            │
                            ▼
 ┌──────────────────────────────────────────────────────────┐
 │ /src/sse/handlers/chat.js (handleChat)                   │
 │ 1. Parse JSON body & strip context markers ([1m])        │
 │ 2. Extract and validate API Key                          │
 │ 3. Bypass check (warmup/naming pings)                    │
 │ 4. Capability detection (vision, pdf, audio, tools)      │
 │ 5. Route: Combo vs Single Model                          │
 └──────────────┬───────────────────────────┬───────────────┘
                │                           │
          [Is Combo]                 [Single Model]
                │                           │
                ▼                           ▼
 ┌───────────────────────────┐ ┌────────────────────────────────────────┐
 │ handleComboChat           │ │ handleSingleModelChat                  │
 │ - Capacity augmentation   │ │ 1. Resolve { provider, model }         │
 │ - Capability sort (Tiers) │ │ 2. Multi-account loop: while (true)    │
 │ - Sequential fallback     │ │    - getProviderCredentials()          │
 └───────────────────────────┘ │    - checkAndRefreshToken()            │
                               │    - handleChatCore()                  │
                               │    - On Error: markAccountUnavailable()│
                               │      & retry next account              │
                               │    - If all fail: 503 + Retry-After    │
                               └──────────────────┬─────────────────────┘
                                                  │
                                                  ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ /open-sse/handlers/chatCore.js (handleChatCore)                      │
 │ 1. Detect format (openai, responses, antigravity, claude)            │
 │ 2. RTK token compression & modality stripping                        │
 │ 3. Translate request if not native passthrough                       │
 │ 4. Executor execution (open-sse/executors/<provider>.js)             │
 │ 5. Catch 401/403 -> refreshWithRetry (up to 3x) on same account      │
 │ 6. Dispatch to streaming / non-streaming handler                     │
 └──────────────────────────────────────────────────────────────────────┘
```

## 3. Account Lifecycle, Quota & Cooldown Mechanics

### Account Selection
- Concurrency mutex (`selectionMutex`) serializes candidate selection.
- Filters out accounts in `excludeConnectionIds` (in-flight attempted set).
- Filters out accounts with active locks: `modelLock_${model}` or `modelLock___all`.
- Filters out Antigravity accounts where `antigravityQuotaCache.remainingPercentage <= 0`.
- Ordering strategies:
  - `fill-first`: Highest priority account (`priority ASC, createdAt ASC`).
  - `round-robin`: Rotates after `stickyRoundRobinLimit` consecutive requests (default 3).

### Quota Signals & Classification
- Text substring match (case-insensitive):
  - `"rate limit"`, `"too many requests"`, `"quota exceeded"`, `"capacity"`, `"overloaded"` $\to$ exponential backoff cooldown.
  - `"not supported when using codex"`, `"no credentials"`, `"improperly formed request"` $\to$ 120s cooldown.
  - `"request not allowed"` $\to$ 5s cooldown.
- HTTP Status match:
  - `429`: Exponential backoff.
  - `401`, `402`, `403`, `404`: 120s fixed cooldown.
  - `409` (Antigravity): Treated as quota exhaustion.
  - `500`, `502`, `503`, `504`: Transport retry (2-3 attempts), then 30s transient cooldown.
  - `400..499` (other client errors): `shouldFallback: false`, `cooldownMs: 0`. Never penalizes account.

### Cooldown Hierarchy
1. GitHub monthly quota reset $\to$ 1st day of next UTC month.
2. Antigravity upstream `resetAt` $\to$ exact `resetsAtMs - now`.
3. Codex / other upstream `resets_at` $\to$ `resetsAtMs - now` (capped at 30 min).
4. Exponential backoff: $2000 \times 2^{\text{level}-1}$ ms (2s, 4s, 8s, 16s... up to 300s).
5. Transient 5xx $\to$ 30s.

### Rotation Termination & Fast-Reject
- When an account fails, it enters `excludeConnectionIds`.
- The loop tries the next candidate.
- When `availableConnections.length === 0`:
  - Collects all active lock expiries.
  - Calculates earliest `retryAfter`.
  - Returns HTTP 503 `Service Unavailable` with header `Retry-After: <seconds>` and descriptive body. Upstream is never re-hammered.

## 4. Streaming & SSE Architecture

- **True SSE from Upstream**:
  - Antigravity uses `streamGenerateContent?alt=sse`.
  - Codex uses Responses API with `stream: true`.
- **Pre-Header Validation**:
  - Before sending HTTP 200 OK headers, the handler validates upstream response headers and content type.
  - Non-SSE responses (e.g. HTML error pages) are intercepted and converted to structured JSON error responses.
- **Byte Watchdog**:
  - Upstream tap monitors raw incoming bytes with a generous watchdog (360s), preventing premature timeouts during long reasoning phases.
- **Terminal Framing Guarantee**:
  - Chat completions (`/v1/chat/completions`): on mid-stream failure, emits `data: {"error": ...}\n\ndata: [DONE]\n\n`.
  - Responses API (`/v1/responses`): on mid-stream failure, emits `event: response.failed\ndata: {...}\n\n` (NEVER `data: [DONE]`).
- **Chunk Filtering**:
  - Empty chunks (`delta.content === ""` without reasoning, tool calls, or finish reasons) are discarded before reaching the downstream client.
