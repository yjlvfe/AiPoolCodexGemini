#!/usr/bin/env bash
# ==============================================================================
# AiPoolCodexGemini - One-Click Smart Adaptive Installer
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "=========================================================="
echo " 🚀 Installing AiPoolCodexGemini Unified Suite"
echo "=========================================================="

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Error: python3 is not installed. Please install python3 first."
    exit 1
fi

# 2. Adaptive Directory Selection (User-Mode vs System-Mode)
if [ "${1:-}" = "--dry-run" ]; then
    echo "DRY_RUN_OK: Adaptive mode check passed."
    exit 0
fi

if [ -w "/usr/local/bin" ] 2>/dev/null && [ -w "/usr/local" ] 2>/dev/null; then
    BIN_DIR="/usr/local/bin"
    LIBEXEC_DIR="/usr/local/libexec"
    INSTALL_MODE="system (/usr/local/bin)"
    SUDO_CMD=""
elif command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
    BIN_DIR="/usr/local/bin"
    LIBEXEC_DIR="/usr/local/libexec"
    INSTALL_MODE="system via sudo (/usr/local/bin)"
    SUDO_CMD="sudo"
else
    BIN_DIR="$HOME/.local/bin"
    LIBEXEC_DIR="$HOME/.local/libexec"
    INSTALL_MODE="user ($HOME/.local/bin)"
    SUDO_CMD=""
fi

echo "📦 Install mode: $INSTALL_MODE"

# 3. Setup Directories
echo "📁 Setting up account data and binary directories..."
mkdir -p "$HOME/.antigravity-accounts"
mkdir -p "$HOME/.codex-accounts"
mkdir -p "$SYSTEMD_USER_DIR"

if [ -n "$SUDO_CMD" ]; then
    $SUDO_CMD mkdir -p "$BIN_DIR" "$LIBEXEC_DIR"
else
    mkdir -p "$BIN_DIR" "$LIBEXEC_DIR"
fi

# Ensure user PATH contains $HOME/.local/bin if in user mode
if [[ "$BIN_DIR" == *"$HOME/.local/bin"* ]]; then
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        export PATH="$HOME/.local/bin:$PATH"
        for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
            if [ -f "$rc" ] && ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$rc" 2>/dev/null; then
                echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
            fi
        done
        echo "💡 Added $HOME/.local/bin to your shell PATH."
    fi
fi

# 4. Permissions on Scripts
chmod +x "$SCRIPT_DIR"/cli/* "$SCRIPT_DIR"/*.sh 2>/dev/null || true

# 5. Install CLI Tools
echo "⚙️ Installing CLI management tools (ag, cx, c, agusage, cusage)..."
if [ -n "$SUDO_CMD" ]; then
    $SUDO_CMD cp -f "$SCRIPT_DIR/cli/antigravity-account-switch" "$BIN_DIR/antigravity-account-switch"
    $SUDO_CMD cp -f "$SCRIPT_DIR/cli/codex-account-switch" "$BIN_DIR/codex-account-switch"
    $SUDO_CMD cp -f "$SCRIPT_DIR/cli/codex-account-query" "$LIBEXEC_DIR/codex-account-query"
    $SUDO_CMD chmod +x "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/codex-account-switch" "$LIBEXEC_DIR/codex-account-query"

    $SUDO_CMD ln -sf "$SCRIPT_DIR/cli/ag" "$BIN_DIR/ag"
    $SUDO_CMD ln -sf "$SCRIPT_DIR/cli/cx" "$BIN_DIR/cx"
    $SUDO_CMD ln -sf "$BIN_DIR/cx" "$BIN_DIR/c"
    $SUDO_CMD ln -sf "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/agusage"
    $SUDO_CMD ln -sf "$BIN_DIR/antigravity-account-switch" "$BIN_DIR/agswitch"
    $SUDO_CMD ln -sf "$BIN_DIR/codex-account-switch" "$BIN_DIR/cusage"
    $SUDO_CMD ln -sf "$BIN_DIR/codex-account-switch" "$BIN_DIR/cswitch"

    for i in {1..10}; do
        echo -e "#!/usr/bin/env bash\nexec $BIN_DIR/antigravity-account-switch $i" | $SUDO_CMD tee "$BIN_DIR/ag$i" >/dev/null
        $SUDO_CMD chmod +x "$BIN_DIR/ag$i"
        echo -e "#!/usr/bin/env bash\nexec $BIN_DIR/codex-account-switch switch $i" | $SUDO_CMD tee "$BIN_DIR/c$i" >/dev/null
        $SUDO_CMD chmod +x "$BIN_DIR/c$i"
    done
else
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

    for i in {1..10}; do
        echo -e "#!/usr/bin/env bash\nexec $BIN_DIR/antigravity-account-switch $i" > "$BIN_DIR/ag$i"
        chmod +x "$BIN_DIR/ag$i"
        echo -e "#!/usr/bin/env bash\nexec $BIN_DIR/codex-account-switch switch $i" > "$BIN_DIR/c$i"
        chmod +x "$BIN_DIR/c$i"
    done
fi

# 6. Install Systemd Services (User Services, completely portable)
echo "🔄 Configuring and starting Systemd user services..."
PYTHON_BIN="$(command -v python3)"

# ai-gemini-bridge.service
cat <<EOF > "$SYSTEMD_USER_DIR/ai-gemini-bridge.service"
[Unit]
Description=AI Suite - Gemini / Antigravity Gateway Bridge (Port 8123)
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR/bridges
ExecStart=$PYTHON_BIN $SCRIPT_DIR/bridges/gemini_bridge.py
Restart=always
RestartSec=3
Environment=AG_BRIDGE_HOST=127.0.0.1
Environment=AG_BRIDGE_PORT=8123

[Install]
WantedBy=default.target
EOF

# ai-codex-bridge.service
cat <<EOF > "$SYSTEMD_USER_DIR/ai-codex-bridge.service"
[Unit]
Description=AI Suite - Codex / ChatGPT Gateway Bridge (Port 8124)
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR/bridges
ExecStart=$PYTHON_BIN $SCRIPT_DIR/bridges/codex_bridge.py
Restart=always
RestartSec=3
Environment=CODEX_BRIDGE_HOST=127.0.0.1
Environment=CODEX_BRIDGE_PORT=8124

[Install]
WantedBy=default.target
EOF

# ai-dashboard.service
cat <<EOF > "$SYSTEMD_USER_DIR/ai-dashboard.service"
[Unit]
Description=AI Suite - Multi-Account Pool Dashboard (Port 8444)
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR/dashboard
ExecStart=$PYTHON_BIN $SCRIPT_DIR/dashboard/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

# ai-bot.service
cat <<EOF > "$SYSTEMD_USER_DIR/ai-bot.service"
[Unit]
Description=AI Suite - Multi-Account Pool Telegram Bot Service
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR/dashboard
ExecStart=$PYTHON_BIN $SCRIPT_DIR/dashboard/bot_service.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload

for svc in ai-gemini-bridge ai-codex-bridge ai-dashboard ai-bot; do
    systemctl --user enable "$svc.service" 2>/dev/null || true
    systemctl --user restart "$svc.service" 2>/dev/null || true
done

# Quick health check for dashboard
sleep 1.5
DASHBOARD_LIVE=0
if command -v curl &>/dev/null && curl -s -m 2 http://127.0.0.1:8444/ &>/dev/null; then
    DASHBOARD_LIVE=1
elif python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8444/', timeout=2)" 2>/dev/null; then
    DASHBOARD_LIVE=1
fi

echo ""
echo "=========================================================="
echo " ✅ Installation and Setup Completed Successfully!"
echo "=========================================================="
if [ "$DASHBOARD_LIVE" -eq 1 ]; then
    echo " 🌐 Web Dashboard: [LIVE ✓]"
    echo "    👉 Open in browser: http://localhost:8444"
else
    echo " 🌐 Web Dashboard: [Starting...]"
    echo "    👉 Open in browser: http://localhost:8444"
    echo "    (If not open immediately, check status: systemctl --user status ai-dashboard)"
fi
echo "----------------------------------------------------------"
echo " 🟢 Gemini Gateway: http://127.0.0.1:8123/v1"
echo " 🟣 Codex Gateway:  http://127.0.0.1:8124/v1"
echo "----------------------------------------------------------"
echo " 📌 Quick CLI Commands (Location: $BIN_DIR):"
echo "   - Gemini: ag (list), ag add (register new), agusage (quotas)"
echo "   - Codex:  cx (list), c add (register new), cusage (quotas)"
echo "----------------------------------------------------------"
echo " 🔄 To update at any time:"
echo "   ./update.sh"
echo " 🗑️  To uninstall at any time:"
echo "   ./uninstall.sh"
echo "=========================================================="
