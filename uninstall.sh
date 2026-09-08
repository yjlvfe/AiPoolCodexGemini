#!/usr/bin/env bash
# ==============================================================================
# AiPoolCodexGemini - One-Click Uninstaller
# ==============================================================================
set -euo pipefail

BIN_DIR="/usr/local/bin"
LIBEXEC_DIR="/usr/local/libexec"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "=========================================================="
echo " 🗑️  Uninstalling AiPoolCodexGemini Suite"
echo "=========================================================="

# 1. Stop and disable Systemd services
echo "🛑 Stopping and disabling Systemd services..."
for svc in ai-gemini-bridge ai-codex-bridge ai-dashboard ai-bot; do
    systemctl --user stop "$svc.service" 2>/dev/null || true
    systemctl --user disable "$svc.service" 2>/dev/null || true
    rm -f "$SYSTEMD_USER_DIR/$svc.service"
done

systemctl --user daemon-reload

# 2. Remove installed binaries and symlinks
echo "🧹 Removing CLI tools and binary shortcuts..."
rm -f "$BIN_DIR/ag" "$BIN_DIR/cx" "$BIN_DIR/c"
rm -f "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/codex-account-switch"
rm -f "$LIBEXEC_DIR/codex-account-query"
rm -f "$BIN_DIR/agusage" "$BIN_DIR/agswitch" "$BIN_DIR/cusage" "$BIN_DIR/cswitch"

for i in {1..20}; do
    rm -f "$BIN_DIR/ag$i" "$BIN_DIR/c$i"
done

echo ""
echo "=========================================================="
echo " ✅ All services stopped and CLI tools removed cleanly!"
echo "=========================================================="
echo " 💡 Note: Account tokens and databases are safely preserved in:"
echo "    - $HOME/.antigravity-accounts"
echo "    - $HOME/.codex-accounts"
echo "    (To wipe accounts permanently, run: rm -rf ~/.antigravity-accounts ~/.codex-accounts)"
echo "=========================================================="
