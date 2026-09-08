#!/usr/bin/env bash
# ==============================================================================
# Setup Hermes Agent Integration
# ==============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/scripts/setup-hermes.py"
