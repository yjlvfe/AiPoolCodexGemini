# Changelog

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
