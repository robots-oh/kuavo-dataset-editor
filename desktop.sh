#!/usr/bin/env bash
# One-command desktop entry for this standalone package.
set -euo pipefail
EDITOR_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$EDITOR_DIR/run.sh" desktop --workspace "$EDITOR_DIR/workspace" "$@"
