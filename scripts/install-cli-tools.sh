#!/usr/bin/env bash
# No remote shell installers and no independent shortcut generation.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/install_codex.py" "$@"
