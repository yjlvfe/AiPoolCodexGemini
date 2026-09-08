#!/usr/bin/env bash
# ==============================================================================
# install-cli-tools.sh - Automatic CLI tool checker & installer for AI Pool Suite
# Supports: OpenAI Codex CLI and Gemini / Antigravity CLI
# ==============================================================================
set -uo pipefail

ACTION="${1:-check}"
TOOL="${2:-all}"

check_tool() {
    local tool="$1"
    local installed="false"
    local path=""
    local version=""

    if [ "$tool" = "codex" ]; then
        if command -v codex >/dev/null 2>&1; then
            installed="true"
            path="$(command -v codex)"
            version="$("$path" --version 2>/dev/null || echo "installed")"
        elif [ -x "$HOME/.local/bin/codex" ]; then
            installed="true"
            path="$HOME/.local/bin/codex"
            version="$("$path" --version 2>/dev/null || echo "installed")"
        elif [ -x "/usr/local/bin/codex" ]; then
            installed="true"
            path="/usr/local/bin/codex"
            version="$("$path" --version 2>/dev/null || echo "installed")"
        fi
    elif [ "$tool" = "antigravity" ]; then
        if command -v agy >/dev/null 2>&1; then
            installed="true"
            path="$(command -v agy)"
            version="installed"
        elif [ -x "$HOME/.local/bin/agy" ]; then
            installed="true"
            path="$HOME/.local/bin/agy"
            version="installed"
        elif [ -x "$HOME/.gemini/bin/agy" ]; then
            installed="true"
            path="$HOME/.gemini/bin/agy"
            version="installed"
        elif [ -x "/usr/local/bin/agy" ]; then
            installed="true"
            path="/usr/local/bin/agy"
            version="installed"
        fi
    fi

    echo "{\"installed\": $installed, \"path\": \"$path\", \"version\": \"$version\"}"
}

install_codex() {
    echo "⚡ Installing OpenAI Codex CLI..."
    # Ensure ~/.local/bin exists and npm prefix is set if not root
    mkdir -p "$HOME/.local/bin"

    if command -v npm >/dev/null 2>&1; then
        echo "Using npm to install @openai/codex..."
        if [ "$(id -u)" -eq 0 ]; then
            npm install -g @openai/codex
        else
            npm install -g --prefix "$HOME/.local" @openai/codex || npm install -g @openai/codex
        fi
    else
        echo "❌ Node.js / npm is not installed."
        echo "💡 Install Node.js LTS via: sudo apt-get install -y nodejs npm"
        return 1
    fi

    if command -v codex >/dev/null 2>&1 || [ -x "$HOME/.local/bin/codex" ]; then
        echo "✓ OpenAI Codex CLI installed successfully."
        return 0
    else
        echo "❌ Failed to install Codex CLI."
        return 1
    fi
}

install_antigravity() {
    echo "⚡ Setting up Gemini / Antigravity CLI..."
    mkdir -p "$HOME/.local/bin"

    # If agy binary is available in any existing path, symlink or copy it to ~/.local/bin/agy
    if [ -x "$HOME/.gemini/bin/agy" ]; then
        ln -sf "$HOME/.gemini/bin/agy" "$HOME/.local/bin/agy"
        echo "✓ Antigravity CLI linked from ~/.gemini/bin/agy"
        return 0
    elif [ -x "/usr/local/bin/agy" ]; then
        ln -sf "/usr/local/bin/agy" "$HOME/.local/bin/agy"
        echo "✓ Antigravity CLI linked from /usr/local/bin/agy"
        return 0
    fi

    # Check if we have a backup or local copy in the workspace
    if [ -f "$HOME/.local/bin/agy" ]; then
        chmod +x "$HOME/.local/bin/agy"
        echo "✓ Antigravity CLI binary verified at ~/.local/bin/agy"
        return 0
    fi

    echo "💡 Antigravity CLI requires Google official package or token import."
    echo "💡 You can import tokens directly: ag add <token-file>"
    return 1
}

case "$ACTION" in
    check)
        codex_status=$(check_tool "codex")
        ag_status=$(check_tool "antigravity")
        echo "{\"codex\": $codex_status, \"antigravity\": $ag_status}"
        ;;
    install)
        if [ "$TOOL" = "codex" ]; then
            install_codex
        elif [ "$TOOL" = "antigravity" ]; then
            install_antigravity
        else
            echo "❌ Unknown tool: $TOOL" >&2
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 {check|install} [codex|antigravity]" >&2
        exit 1
        ;;
esac
