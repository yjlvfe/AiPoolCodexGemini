# Background Mutation Safety Audit (Delegate C)

Scope: dashboard refresh, usage polling, account reports, `cusage`, `clist`, `cwho`, health/status workers, and quota refresh. The persistent active marker is `<ACCOUNT_STORE>/active`.

| Path | Reads active? | Writes active marker / changes selected account? | Allowed? |
|---|---:|---:|---|
| `dashboard/app.py:319-383` `_background_poller` → `_update_all_background` | Yes, indirectly at `:514-531` while enriching status | No. Writes only `pool_snapshot.json` at `:309-317, :383`; no switch/compact/remove/promotion call | PASS |
| `dashboard/app.py:397-411` `trigger_instant_refresh` | Via worker report/enrichment | No; starts the same read/report worker | PASS |
| `dashboard/server.py:448-479` logs/metrics/refresh/status endpoints | Via `PoolManager` reporting methods | No; refresh only schedules worker | PASS |
| `cli/account_reports.py:45-82` `pool_report` | Yes at `:49` (`Manager.active()`) | No; parallel `inspect()` calls do not transition the marker | PASS |
| `cli/account_manager.py:413-424` `Manager.usage` (`cusage`/`agusage`) | `usage_format.show_account` reads active at `usage_format.py:73` | No marker write; no `add_or_switch`, `compact`, `remove`, or promotion path | PASS |
| `cli/account_manager.py:481-501` `list`/`who` (`clist`/`cwho`) | Yes (`active()` at `:486`, `:492`) | No writes | PASS |
| `bridges/pool_runtime.py:74-109` candidates/health eligibility | Yes at `:75-85` | No transition; `_store_cooldown` at `:60-71` writes only SQLite cooldown state | PASS |
| `bridges/pool_runtime.py:131-165` credential/quota refresh | Yes at `:137`, `:161` | No marker write and no account selection change; may refresh credential/live token files at `:157-163` | PASS for active-selection safety; credential mutation is a separate caveat |
| `cli/usage_format.py:30-61` quota inspection | Reads active at `:56`; CAS-refreshes credential/live files at `:54-59` | No `<store>/active` write | PASS for active-selection safety; not filesystem-read-only |
| `cli/ag_provider.py:318-346` token/quota calls | No marker access | No marker/state transition | PASS |

## Acceptance tests C1-C6

Added `tests/test_background_mutation_safety.py`:

- C1 dashboard refresh worker: static call-site guard against transition methods — PASS
- C2 account report: static call-site guard — PASS
- C3 `cusage`/usage polling with `active=3`: marker byte-for-byte unchanged — PASS
- C4 `clist`/`cwho` read fixture and marker unchanged — PASS
- C5 health/status and pool runtime: static call-site guard — PASS
- C6 sixteen concurrent usage/reporting tasks with `active=3`: marker unchanged — PASS

Command:

```text
/usr/local/bin/pytest -q tests/test_background_mutation_safety.py
6 passed in 0.24s
```

## Conclusion

No background/reporting path writes the persistent `active` marker or changes the selected account. Metadata/cache/cooldown writes are isolated from active selection. The only notable non-marker mutation is credential/live-token refresh in `usage_format.inspect` and `AccountPool._credentials`; it is CAS-protected and does not alter account selection, but should be treated as credential-cache persistence rather than strict filesystem read-only behavior.
