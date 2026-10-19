#!/usr/bin/env bash
set -euo pipefail
EDITOR_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${EDITOR_PYTHON:-}" && -f "$EDITOR_DIR/.editor-python" ]]; then
  IFS= read -r EDITOR_RUNTIME < "$EDITOR_DIR/.editor-python"
  EDITOR_PYTHON="$EDITOR_DIR/$EDITOR_RUNTIME"
fi
EDITOR_PYTHON="${EDITOR_PYTHON:-$EDITOR_DIR/.venv/bin/python}"
if [[ ! -x "$EDITOR_PYTHON" ]]; then
  echo "Editor environment missing: $EDITOR_PYTHON" >&2
  echo "See $EDITOR_DIR/README.md (or set EDITOR_PYTHON)." >&2
  exit 1
fi
unset PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD
export PYTHONPATH="$EDITOR_DIR/src${LEROBOT_SOURCE:+:$LEROBOT_SOURCE}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"
if [[ "${1:-}" == --print-python ]]; then
  printf '%s\n' "$EDITOR_PYTHON"
  exit 0
fi
if [[ "${1:-}" == python ]]; then
  shift
  exec "$EDITOR_PYTHON" "$@"
fi
if [[ $# -eq 0 ]]; then
  set -- serve --workspace "$EDITOR_DIR/workspace"
fi
exec "$EDITOR_PYTHON" -m kuavo_dataset_editor "$@"
