# Changelog

## v2.2.7 — 2026-09-17

### Stable
- Production-ready stable release introducing Zero-Retry, Model-Aware Smart Failover, and provider/model circuit breaking.

### Zero-Retry
- Enforced exactly one provider attempt per account for each logical request.
- Removed internal same-account retries, retry sleeps, and backoff loops; a failed attempt is classified and handled immediately.
- Prevented replay after streamed output begins, preserving request integrity and avoiding duplicate side effects.

### Smart Account Failover
- Account rotation is permitted only for independently proven hard account failures: hard quota exhaustion or invalid/revoked credentials.
- A hard account failure can advance to at most one eligible next account; transient, model, provider, and unknown failures keep the active account sticky.
- Prevented temporary 429 responses, generic 5xx/network failures, and model outages from incorrectly exhausting or rotating accounts.
- Preserved strict Codex/Gemini pool separation and generation-safe active-account promotion.

### Model-Aware Classification
- Added deterministic failure-scope classification across `MODEL`, `PROVIDER`, `ACCOUNT`, `TRANSIENT_REQUEST`, and `UNKNOWN`.
- Model-scoped failures take precedence over provider, account, and transport signals and never trigger account failover.
- Added stable error codes including `MODEL_UNAVAILABLE` and `PROVIDER_UNAVAILABLE`, with safe model context and actionable `suggested_action` values.

### Model Circuit Breaker
- Added per-provider, per-model circuit-breaker isolation with a 45-second default TTL and provider `Retry-After` support.
- Added fast local rejection for known unavailable models without contacting the provider or consuming account quota.
- Added single-flight recovery probes to prevent probe stampedes; successful probes close the breaker and failed probes reopen it.
- Kept failures isolated so an unavailable model does not affect other models or the other provider pool.

### Agent Contract
- Standardized Codex and Gemini error envelopes, `X-Request-ID`, optional `Retry-After`, model context, and `suggested_action` fields.
- Added redacted request/provider-call summaries and stable `FAILURE_CLASSIFIED` and `MODEL_BREAKER_*` observability events.
- Documented the provider-neutral contract for Hermes and other API clients in `docs/AGENT_ERROR_CONTRACT.md`.

### Verification
- Full test suite: **233/233 PASS**.
- Zero-Retry, Smart Account Failover, model-aware classification, circuit-breaker isolation/recovery, and Agent Contract acceptance coverage passed.

## v2.2.6 — 2026-09-16

### Stable
- Production-ready stable release with reliability hardening and sticky account rotation closure.

### Reliability
- Bounded account-lock acquisition, inbound body reads, upstream retries, request lifecycles, streaming, dashboard probes, and durable request leases.
- Added SQLite WAL mode with busy timeouts, clean client-disconnect handling, bounded Codex stream idle timeouts, standardized stream interruption events, and safe integration port handling.

### Account Rotation
- Kept the active account sticky across successful requests, transient 5xx/network failures, and temporary 429 rate limits.
- Restricted persistent rotation to confirmed hard quota exhaustion, permanent invalid credentials, or an explicit manual switch.
- Added durable hard-exhaustion exclusion, invalid-credential retirement, background read-only guarantees, and generation-checked atomic promotion to prevent bounce-back and stale overwrites.

### Verification
- Full test suite: **211/211 PASS**.
- Sticky rotation closure and reliability regression suites passed.
- Production services and runtime health verified after deployment and restart.

## v2.2.5 — 2026-09-15

### Stable
- **Dashboard Quota Subgroup Switching**: Account quota metrics (5h and weekly percentages + countdown timers) and account sorting now dynamically update based on the selected Gemini subgroup (`Gemini` vs `Claude / GPT`).
- **UI Stability**: Removed layout shift and jitter when toggling quota subgroup buttons.

## v2.2.4 — 2026-09-15

### Stable
- Stable release with Claude tool schema format repair on Antigravity gateway bridge.

### Fixed
- Antigravity tool transformation for Claude models (`claude-*`): format `parameters` instead of `parametersJsonSchema`.
- Schema normalization: sanitize `anyOf` to `oneOf` for Anthropic tool calling compliance.
- Filter empty text parts in assistant tool calls.

### Stable
- Final stable release after backend repair and UI/UX consistency completion.

### Fixed
- Magic Link device registration.
- Stable API-key identity across renames.
- Refill quota enforcement beyond retention.
- API-key analytics lifetime/retained semantics.
- Removed dead MoA runtime controls while preserving historical reporting.
- Negative quota validation.

### UI/UX
- Unified button design system and interaction states.
- Unified form/input/dropdown focus, disabled and danger states.
- Improved dashboard cards, stats and header consistency.
- Fixed Activity Stream overflow and metadata alignment.
- Improved Request Inspector layout, Arabic/Unicode handling and Escape dismissal.
- Fixed modal styling separation between normal and destructive actions.
- Fixed Allowed Models responsive grid.
- Fixed integration headers/status badges on narrow screens.
- Verified responsive behavior across mobile, tablet and desktop.

### Verification
- 18 focused UI tests passed.
- 163 full-suite tests passed.
- JavaScript syntax passed.
- Python syntax passed.
- Manager final visual QA passed.
- No browser JS exceptions or browser log errors during final UI verification.
- Runtime deployment verified.

## v2.2.2 — 2026-09-14

### Stable
- Production-ready stable release.

### Fixed
- Magic Link device registration.
- Stable API-key quota identity across renames.
- Refill quota enforcement beyond detailed-event retention.
- Clear lifetime versus retained analytics semantics.
- Removed dead MoA runtime controls while preserving historical reporting.
- Negative quota validation in the frontend and backend.

### Verification
- 159/159 tests passed.
- Independent acceptance review passed.
- Runtime health checks passed.
