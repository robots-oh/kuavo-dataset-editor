#!/usr/bin/env bash
set -euo pipefail
EDITOR_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${LEROBOT_SOURCE:-}" && -d "$EDITOR_DIR/.lerobot-source" ]]; then
  export LEROBOT_SOURCE="$EDITOR_DIR/.lerobot-source"
fi
EDITOR_PYTHON="${EDITOR_PYTHON:-$EDITOR_DIR/.venv/bin/python}"
if [[ ! -x "$EDITOR_PYTHON" ]]; then
  echo "Editor environment missing: $EDITOR_PYTHON" >&2
  echo "See $EDITOR_DIR/README.md (or set EDITOR_PYTHON)." >&2
  exit 1
fi
export PYTHONPATH="$EDITOR_DIR/src${LEROBOT_SOURCE:+:$LEROBOT_SOURCE}${PYTHONPATH:+:$PYTHONPATH}"
if [[ $# -eq 0 ]]; then
  set -- serve --workspace "$EDITOR_DIR/workspace"
fi
exec "$EDITOR_PYTHON" -m kuavo_dataset_editor "$@"
