#!/usr/bin/env bash
# ==============================================================================
# AiPoolCodex&Gemini - One-Click Installer
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="/usr/local/bin"
LIBEXEC_DIR="/usr/local/libexec"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "=========================================================="
echo " 🚀 تثبيت منظومة AiPoolCodex&Gemini الموحدة"
echo "=========================================================="

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ خطأ: python3 غير مثبت. يرجى تثبيته أولاً."
    exit 1
fi

# 2. Setup Data Directories
echo "📁 تجهيز مجلدات تخزين الحسابات..."
mkdir -p "$HOME/.antigravity-accounts"
mkdir -p "$HOME/.codex-accounts"
mkdir -p "$LIBEXEC_DIR"
mkdir -p "$SYSTEMD_USER_DIR"

# 3. Permissions on Scripts
chmod +x "$SCRIPT_DIR"/cli/*

# 4. Install CLI Tools to /usr/local/bin
echo "⚙️ تثبيت أدوات سطر الأوامر (ag, cx, agusage, cusage)..."
cp -f "$SCRIPT_DIR/cli/antigravity-account-switch" "$BIN_DIR/antigravity-account-switch"
cp -f "$SCRIPT_DIR/cli/codex-account-switch" "$BIN_DIR/codex-account-switch"
cp -f "$SCRIPT_DIR/cli/codex-account-query" "$LIBEXEC_DIR/codex-account-query"
chmod +x "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/codex-account-switch" "$LIBEXEC_DIR/codex-account-query"

ln -sf "$SCRIPT_DIR/cli/ag" "$BIN_DIR/ag"
ln -sf "$SCRIPT_DIR/cli/cx" "$BIN_DIR/cx"
ln -sf "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/agusage"
ln -sf "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/agswitch"
ln -sf "$BIN_DIR/codex-account-switch" "$BIN_DIR/cusage"
ln -sf "$BIN_DIR/codex-account-switch" "$BIN_DIR/cswitch"

# Generate account shortcuts ag1..ag10 and c1..c10
for i in {1..10}; do
    # Gemini shortcut: agN
    cat << 'EOF' > "$BIN_DIR/ag$i" 2>/dev/null || true
#!/usr/bin/env bash
exec /usr/local/bin/antigravity-account-switch "${0##*ag}"
EOF
    # Fix the script generation with actual loop variable
    echo -e '#!/usr/bin/env bash\nexec /usr/local/bin/antigravity-account-switch '"$i" > "$BIN_DIR/ag$i"
    chmod +x "$BIN_DIR/ag$i"

    # Codex shortcut: cN
    echo -e '#!/usr/bin/env bash\nexec /usr/local/bin/codex-account-switch '"$i" > "$BIN_DIR/c$i"
    chmod +x "$BIN_DIR/c$i"
done

# 5. Install Systemd Services
echo "🔄 تثبيت وتشغيل خدمات النظام (Systemd)..."
for svc in ai-gemini-bridge ai-codex-bridge ai-dashboard ai-bot; do
    cp -f "$SCRIPT_DIR/systemd/$svc.service" "$SYSTEMD_USER_DIR/$svc.service"
done

systemctl --user daemon-reload

for svc in ai-gemini-bridge ai-codex-bridge ai-dashboard ai-bot; do
    systemctl --user enable "$svc.service" || true
    systemctl --user restart "$svc.service" || true
done

echo ""
echo "=========================================================="
echo " ✅ اكتمل التثبيت والتشغيل بنجاح!"
echo "=========================================================="
echo " 🌐 لوحة التحكم (Dashboard):"
echo "    👉 افتح في متصفحك: http://localhost:8444  (أو http://127.0.0.1:8444)"
echo "----------------------------------------------------------"
echo " 🟢 جسر Gemini يعمل على: http://127.0.0.1:8123/v1"
echo " 🟣 جسر Codex يعمل على:  http://127.0.0.1:8124/v1"
echo "----------------------------------------------------------"
echo " 📌 أوامر الاستخدام السريعة:"
echo "   - حسابات Gemini: ag (عرض), ag add (إضافة), agusage (استهلاك)"
echo "   - حسابات Codex:  cx (عرض), c add (إضافة), cusage (استهلاك)"
echo "----------------------------------------------------------"
echo " 🗑️  لإلغاء التثبيت وحذف الخدمات في أي وقت:"
echo "   ./uninstall.sh"
echo "=========================================================="
