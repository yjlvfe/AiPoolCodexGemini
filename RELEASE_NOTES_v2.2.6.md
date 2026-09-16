# AiPool v2.2.6 Release Notes

**Release date:** 2026-09-16  
**Status:** Stable / Production Ready

## Highlights

- Reliability hardening across locking, timeouts, retries, streaming, SQLite concurrency, dashboard workers, and durable request handling.
- Sticky account rotation is closed: normal successes, transient provider errors, network failures, and temporary 429 responses no longer rewrite the active account.
- Persistent failover is limited to confirmed hard quota exhaustion, permanent invalid credentials, or an explicit manual switch.
- Atomic, generation-checked promotion prevents concurrent account bouncing and stale in-flight requests from overwriting newer active-account state.
- Background refresh, reporting, and CLI inspection paths remain read-only with respect to the active account pointer.

## Verification

- Full test suite: **211/211 PASS**.
- Sticky rotation closure and reliability regression suites passed.
- Production services and runtime health were verified after deployment and restart.

## Upgrade Notes

- No configuration migration is required for this release.
- Existing account and runtime state is preserved.
- Review the documented timeout and retry environment variables if you need to tune operational limits.
