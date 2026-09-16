# Changelog

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
