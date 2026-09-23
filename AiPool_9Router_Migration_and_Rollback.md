# AiPool 9Router Behavior Adoption: Migration & Rollback Guide

## 1. Data Integrity & Migration Safety

### Protected State Inventory
The following data and state directories are strictly preserved and remain intact:
- Account slots: `/var/lib/aipool/accounts/codex/` and `/var/lib/aipool/accounts/antigravity/`
- OAuth tokens and metadata: `auth.json`, `antigravity-oauth-token`, `metadata.json`
- Auxiliary Agent state databases: `goals_*.sqlite`, `memories_*.sqlite`, `state_*.sqlite`
- Configuration environment: `/root/Projects/AiPoolCodexGemini/config.env` and `/var/lib/aipool/app/config.env`
- Integration socket: `/run/aipool/agent-integration.sock`

### Verification of Preserved Backups
Before making changes, backups were created:
- Accounts Tarball: `/var/lib/aipool-backup-accounts.tar.gz` (11 MB)
- Runtime Backup: `/var/lib/aipool/runtime.bak`
- Git Checkpoint Branch: `feature/9router-behavior-rebase`

---

## 2. Production Deployment Steps (Non-Root Sync)

According to the project deployment contract in `AGENTS.md`:

```bash
# 1. Apply non-root migration script to update /var/lib/aipool/app
python3 /root/Projects/AiPoolCodexGemini/scripts/aipool_nonroot_migration.py --apply

# 2. Ensure proper file ownership and permissions for the service user
chown -R aipool:aipool /var/lib/aipool/accounts /var/lib/aipool/runtime /var/lib/aipool/app
chmod 700 /var/lib/aipool/accounts /var/lib/aipool/accounts/*
chmod 600 /var/lib/aipool/accounts/*/* /var/lib/aipool/runtime/* 2>/dev/null || true

# 3. Restart system services
systemctl restart codex.service gemini.service dashboard.service

# 4. Verify system service health
systemctl is-active codex.service gemini.service dashboard.service
```

---

## 3. Comprehensive Rollback Plan

If unexpected regressions occur in live production environments, execute the following steps to immediately restore the prior stable state:

### Step 1: Stop Services
```bash
systemctl stop codex.service gemini.service dashboard.service
```

### Step 2: Restore Working Directory from Git Checkpoint
```bash
cd /root/Projects/AiPoolCodexGemini
git checkout main
```

### Step 3: Restore Accounts & Runtime State
```bash
rm -rf /var/lib/aipool/accounts
tar -xpzf /var/lib/aipool-backup-accounts.tar.gz -C / --preserve-permissions
cp -a /var/lib/aipool/runtime.bak/* /var/lib/aipool/runtime/
chown -R aipool:aipool /var/lib/aipool/accounts /var/lib/aipool/runtime
```

### Step 4: Re-apply Prior Production Tree & Restart
```bash
python3 /root/Projects/AiPoolCodexGemini/scripts/aipool_nonroot_migration.py --apply
systemctl restart codex.service gemini.service dashboard.service
```

### Step 5: Post-Rollback Sanity Verification
- Probe Gemini bridge: `curl -s http://127.0.0.1:8123/health`
- Probe Codex bridge: `curl -s http://127.0.0.1:8124/health`
- Probe Dashboard: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8444/login`
