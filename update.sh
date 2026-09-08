#!/usr/bin/env bash
# ==============================================================================
# AiPoolCodexGemini - One-Click Update Script
# ==============================================================================
# Safely pulls the latest updates from GitHub and restarts background services.
# Preserves local authentication databases, configs, and account tokens.
# ==============================================================================

set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}====================================================${NC}"
echo -e "${CYAN}   AiPoolCodexGemini - System Updater               ${NC}"
echo -e "${CYAN}====================================================${NC}"

# Check git repository
if [ ! -d ".git" ]; then
    echo -e "${RED}Error: Not a git repository. Cannot update automatically.${NC}"
    exit 1
fi

echo -e "\n${BLUE}→ Step 1/3: Pulling latest changes from GitHub...${NC}"
# Stash local non-tracked modifications if any, but preserve auth.db
git fetch origin main || git fetch origin master || true
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD || echo "main")
git pull origin "$CURRENT_BRANCH" || {
    echo -e "${YELLOW}Notice: Direct pull had conflicts or notices, rebasing safely...${NC}"
    git stash || true
    git pull --rebase origin "$CURRENT_BRANCH"
    git stash pop || true
}

echo -e "${GREEN}✓ Source code updated to latest commit:${NC} $(git rev-parse --short HEAD)"

echo -e "\n${BLUE}→ Step 2/3: Refreshing CLI shortcuts & permissions...${NC}"
chmod +x cli/cx cli/ag install.sh uninstall.sh update.sh setup-hermes.sh setup-openclaw.sh 2>/dev/null || true
if [ -d "/usr/local/bin" ] && [ -w "/usr/local/bin" ]; then
    ln -sf "$SCRIPT_DIR/cli/cx" /usr/local/bin/cx 2>/dev/null || true
    ln -sf "$SCRIPT_DIR/cli/cx" /usr/local/bin/c 2>/dev/null || true
    ln -sf "$SCRIPT_DIR/cli/ag" /usr/local/bin/ag 2>/dev/null || true
fi
echo -e "${GREEN}✓ CLI tools and executable permissions refreshed.${NC}"

echo -e "\n${BLUE}→ Step 3/3: Restarting user systemd services...${NC}"
systemctl --user daemon-reload 2>/dev/null || true

RESTART_DASHBOARD=1
if [ "$1" = "--no-dashboard" ]; then
    RESTART_DASHBOARD=0
fi

SERVICES=("ai-gemini-bridge" "ai-codex-bridge" "ai-bot")
for s in "${SERVICES[@]}"; do
    if systemctl --user is-enabled "$s.service" &>/dev/null; then
        systemctl --user restart "$s.service" 2>/dev/null || true
        echo -e "  [+] Restarted $s.service"
    fi
done

if [ "$RESTART_DASHBOARD" -eq 1 ]; then
    if systemctl --user is-enabled "ai-dashboard.service" &>/dev/null; then
        systemctl --user restart "ai-dashboard.service" 2>/dev/null || true
        echo -e "  [+] Restarted ai-dashboard.service"
    fi
fi

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}✓ AiPoolCodexGemini successfully updated!            ${NC}"
echo -e "${GREEN}====================================================${NC}"
