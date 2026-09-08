#!/usr/bin/env bash
# ==============================================================================
# AiPoolCodexGemini - One-Click Uninstaller
# ==============================================================================
set -euo pipefail

BIN_DIR="/usr/local/bin"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "=========================================================="
echo " 🗑️  إلغاء تثبيت منظومة AiPoolCodexGemini"
echo "=========================================================="

# 1. Stop and disable systemd services
echo "🛑 إيقاف وتعطيل خدمات النظام..."
for svc in ai-gemini-bridge ai-codex-bridge ai-dashboard ai-bot; do
    systemctl --user stop "$svc.service" 2>/dev/null || true
    systemctl --user disable "$svc.service" 2>/dev/null || true
    rm -f "$SYSTEMD_USER_DIR/$svc.service"
done

systemctl --user daemon-reload || true

# 2. Remove CLI binaries and symlinks from /usr/local/bin
echo "🧹 إزالة أدوات سطر الأوامر والاختصارات..."
rm -f "$BIN_DIR/ag" "$BIN_DIR/cx" "$BIN_DIR/c"
rm -f "$BIN_DIR/agusage" "$BIN_DIR/agswitch"
rm -f "$BIN_DIR/cusage" "$BIN_DIR/cswitch"
rm -f "$BIN_DIR/antigravity-account-switch"
rm -f "$BIN_DIR/codex-account-switch"
rm -f /usr/local/libexec/codex-account-query 2>/dev/null || true

for i in {1..20}; do
    rm -f "$BIN_DIR/ag$i" "$BIN_DIR/c$i"
done

echo ""
echo "=========================================================="
echo " ✅ تم إيقاف جميع الخدمات وإزالة كافة الأوامر بنجاح!"
echo "=========================================================="
echo " 💡 ملاحظة: ملفات وتوكنات الحسابات لا تزال محفوظة بأمان في:"
echo "    - $HOME/.antigravity-accounts"
echo "    - $HOME/.codex-accounts"
echo "    (إذا أردت حذف الحسابات نهائياً، نفّذ: rm -rf ~/.antigravity-accounts ~/.codex-accounts)"
echo "=========================================================="
