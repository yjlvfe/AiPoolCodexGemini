#!/usr/bin/env bash
# ==============================================================================
# Setup OpenClaw Integration
# ==============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/scripts/setup-openclaw.py"
