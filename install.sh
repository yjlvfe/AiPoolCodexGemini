#!/usr/bin/env bash
# ==============================================================================
# AiPoolCodexGemini - One-Click Unified Installer
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="/usr/local/bin"
LIBEXEC_DIR="/usr/local/libexec"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "=========================================================="
echo " 🚀 Installing AiPoolCodexGemini Unified Suite"
echo "=========================================================="

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Error: python3 is not installed. Please install python3 first."
    exit 1
fi

# 2. Setup Data Directories
echo "📁 Setting up account data directories..."
mkdir -p "$HOME/.antigravity-accounts"
mkdir -p "$HOME/.codex-accounts"
mkdir -p "$LIBEXEC_DIR"
mkdir -p "$SYSTEMD_USER_DIR"

# 3. Permissions on Scripts
chmod +x "$SCRIPT_DIR"/cli/*

# 4. Install CLI Tools to /usr/local/bin
echo "⚙️ Installing CLI management tools (ag, cx, c, agusage, cusage)..."
cp -f "$SCRIPT_DIR/cli/antigravity-account-switch" "$BIN_DIR/antigravity-account-switch"
cp -f "$SCRIPT_DIR/cli/codex-account-switch" "$BIN_DIR/codex-account-switch"
cp -f "$SCRIPT_DIR/cli/codex-account-query" "$LIBEXEC_DIR/codex-account-query"
chmod +x "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/codex-account-switch" "$LIBEXEC_DIR/codex-account-query"

ln -sf "$SCRIPT_DIR/cli/ag" "$BIN_DIR/ag"
ln -sf "$SCRIPT_DIR/cli/cx" "$BIN_DIR/cx"
ln -sf "$BIN_DIR/cx" "$BIN_DIR/c"
ln -sf "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/agusage"
ln -sf "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/agswitch"
ln -sf "$BIN_DIR/codex-account-switch" "$BIN_DIR/cusage"
ln -sf "$BIN_DIR/codex-account-switch" "$BIN_DIR/cswitch"

# Generate account shortcuts ag1..ag10 and c1..c10
for i in {1..10}; do
    echo -e '#!/usr/bin/env bash\nexec /usr/local/bin/antigravity-account-switch '"$i" > "$BIN_DIR/ag$i"
    chmod +x "$BIN_DIR/ag$i"

    echo -e '#!/usr/bin/env bash\nexec /usr/local/bin/codex-account-switch switch '"$i" > "$BIN_DIR/c$i"
    chmod +x "$BIN_DIR/c$i"
done

# 5. Install Systemd Services
echo "🔄 Installing and starting Systemd services..."
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
echo " ✅ Installation and Setup Completed Successfully!"
echo "=========================================================="
echo " 🌐 Web Dashboard:"
echo "    👉 Open in browser: http://localhost:8444  (or http://127.0.0.1:8444)"
echo "----------------------------------------------------------"
echo " 🟢 Gemini Gateway: http://127.0.0.1:8123/v1"
echo " 🟣 Codex Gateway:  http://127.0.0.1:8124/v1"
echo "----------------------------------------------------------"
echo " 📌 Quick CLI Commands:"
echo "   - Gemini: ag (list), ag add (register new), agusage (quotas)"
echo "   - Codex:  cx (list), c add (register new), cusage (quotas)"
echo "----------------------------------------------------------"
echo " 🗑️  To uninstall at any time:"
echo "   ./uninstall.sh"
echo "=========================================================="
