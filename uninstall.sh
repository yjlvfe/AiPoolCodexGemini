#!/usr/bin/env bash
# ==============================================================================
# AiPoolCodexGemini - Smart Adaptive Uninstaller
# ==============================================================================
set -euo pipefail

SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "=========================================================="
echo " 🗑️  Uninstalling AiPoolCodexGemini Suite"
echo "=========================================================="

# 1. Stop and disable Systemd user services
echo "🛑 Stopping and disabling Systemd services..."
for svc in ai-gemini-bridge ai-codex-bridge ai-dashboard ai-bot; do
    systemctl --user stop "$svc.service" 2>/dev/null || true
    systemctl --user disable "$svc.service" 2>/dev/null || true
    rm -f "$SYSTEMD_USER_DIR/$svc.service"
done

systemctl --user daemon-reload

# 2. Clean both system and user binary locations
echo "🧹 Removing CLI tools and shortcuts..."
LOCATIONS=("/usr/local" "$HOME/.local")

for loc in "${LOCATIONS[@]}"; do
    bdir="$loc/bin"
    ldir="$loc/libexec"

    if [ -w "$bdir" ] 2>/dev/null; then
        rm -f "$bdir/ag" "$bdir/cx" "$bdir/c"
        rm -f "$bdir/antigravity-account-switch" "$bdir/codex-account-switch"
        rm -f "$ldir/codex-account-query" 2>/dev/null || true
        rm -f "$bdir/agusage" "$bdir/agswitch" "$bdir/cusage" "$bdir/cswitch"
        for i in {1..20}; do
            rm -f "$bdir/ag$i" "$bdir/c$i"
        done
    elif command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
        sudo rm -f "$bdir/ag" "$bdir/cx" "$bdir/c" 2>/dev/null || true
        sudo rm -f "$bdir/antigravity-account-switch" "$bdir/codex-account-switch" 2>/dev/null || true
        sudo rm -f "$ldir/codex-account-query" 2>/dev/null || true
        sudo rm -f "$bdir/agusage" "$bdir/agswitch" "$bdir/cusage" "$bdir/cswitch" 2>/dev/null || true
        for i in {1..20}; do
            sudo rm -f "$bdir/ag$i" "$bdir/c$i" 2>/dev/null || true
        done
    fi
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
