# Maintenance map

- `bridges/`: provider HTTP adapters and account failover. Keep executable paths stable for systemd.
- `bridges/pool_runtime.py`: account rotation; compatibility exports for logging.
- `bridges/request_log.py`: SQLite migration, events and reporting.
- `bridges/request_audit.py`: request identity, canonical SHA-256, bounded redaction and provenance.
- `dashboard/server.py`: authenticated HTTP routes and orchestration.
- `dashboard/templates/dashboard.html`: existing UI, CSS and JavaScript; extracted unchanged except expandable request details.
- `dashboard/update_ui.js`: updater UI.
- `cli/`: account management and CLI entry points. Never move credentials with code.
- `scripts/`: install, integration and maintenance implementations.
- root shell scripts: public compatibility entry points.
- `systemd/`: service templates; installed units may be user services.
- `examples/`: public sample configuration only.
- `tests/`: isolated automated regression tests.
- `.evidence/`: private local test artifacts, excluded from git.
- `.evidence/design-archive/`: historical generated images moved out of the project root.

## Request tracing
New accepted model requests capture a unique UUID and canonical JSON payload SHA-256. Identical payloads have identical hashes but different UUIDs. Account retries stay within the same incoming request.
Request details expand beneath dashboard log rows. Historical records cannot recover missing prompts or sources.
`peer_ip` is the TCP peer, not an asserted forwarding header. `client_label` (X-AI-Client) and User-Agent are untrusted caller declarations, not authenticated attribution. Localhost alone cannot establish which local process sent the request.
Only prompt fields are retained, capped at 16,384 characters after common credential redaction. `prompt_truncated` records truncation. This is not guaranteed detection of all secrets in natural-language text. Authorization/cookie headers are never captured. Treat auth.db and backups as sensitive private data. No automatic prompt retention cleanup is currently implemented.

## Verify
`python3 -m unittest discover -s tests -v`
`python3 -m compileall -q bridges dashboard`
`git diff --check`
Tests use synthetic data and temporary SQLite databases, not production accounts.

## Scope
No cosmetic redesign, account renaming, provider remapping or service-path changes. Further splitting of legacy adapter conversion routines is separate work requiring protocol fixtures.
