#!/usr/bin/env bash
# Rebuild from the committed lock. Never synchronize a foreign/shared environment.
set -euo pipefail
EDITOR_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EDITOR_BOOTSTRAP_PYTHON="${EDITOR_BOOTSTRAP_PYTHON:-python3}"
EDITOR_VENV_DIR="${EDITOR_VENV_DIR:-$EDITOR_DIR/.venv}"
EDITOR_WITH_EXPORT=1
EDITOR_WITH_DESKTOP=1
EDITOR_WITH_TEST=0
for arg in "$@"; do
  case "$arg" in
    --minimal) EDITOR_WITH_EXPORT=0; EDITOR_WITH_DESKTOP=0 ;;
    --with-export) EDITOR_WITH_EXPORT=1 ;;
    --with-desktop) EDITOR_WITH_DESKTOP=1 ;;
    --with-test) EDITOR_WITH_TEST=1 ;;
    *) echo "Usage: bash setup.sh [--minimal] [--with-export] [--with-desktop] [--with-test]" >&2; exit 1 ;;
  esac
done
if [[ -L "$EDITOR_VENV_DIR" || -L "$EDITOR_DIR/.bootstrap" ]]; then
  echo "Refusing to synchronize a symlinked/shared environment. Set EDITOR_VENV_DIR to a new editor-only directory." >&2
  exit 1
fi
if [[ ! -f "$EDITOR_DIR/uv.lock" ]]; then
  echo "uv.lock is missing. Use the full repository/source bundle." >&2
  exit 1
fi
if [[ ! -x "$EDITOR_DIR/.bootstrap/bin/python" ]] || ! "$EDITOR_DIR/.bootstrap/bin/python" -m pip --version >/dev/null 2>&1; then
  if [[ -d "$EDITOR_DIR/.bootstrap" ]]; then
    EDITOR_OLD_BOOTSTRAP="$EDITOR_DIR/.bootstrap-incomplete-$(date +%Y%m%d_%H%M%S)-$$"
    mv -- "$EDITOR_DIR/.bootstrap" "$EDITOR_OLD_BOOTSTRAP"
    echo "Preserved incomplete bootstrap at: $EDITOR_OLD_BOOTSTRAP"
  fi
  "$EDITOR_BOOTSTRAP_PYTHON" -m venv "$EDITOR_DIR/.bootstrap"
fi
"$EDITOR_DIR/.bootstrap/bin/python" -m pip install --index-url https://pypi.org/simple 'uv==0.13.1'
EDITOR_EXTRAS=()
EDITOR_CHECKS=()
if [[ "$EDITOR_WITH_DESKTOP" == 1 ]]; then EDITOR_EXTRAS+=(--extra desktop); EDITOR_CHECKS+=(--desktop); fi
if [[ "$EDITOR_WITH_EXPORT" == 1 ]]; then EDITOR_EXTRAS+=(--extra export); EDITOR_CHECKS+=(--export); fi
if [[ "$EDITOR_WITH_TEST" == 1 ]]; then EDITOR_EXTRAS+=(--extra test); fi
UV_PROJECT_ENVIRONMENT="$EDITOR_VENV_DIR" "$EDITOR_DIR/.bootstrap/bin/uv" sync \
  --project "$EDITOR_DIR" --python 3.12 --locked --no-default-groups "${EDITOR_EXTRAS[@]}"
# This check must use installed dependencies, not this PC's development checkout.
env -u LEROBOT_SOURCE -u PYTHONPATH "$EDITOR_VENV_DIR/bin/python" -m kuavo_dataset_editor doctor \
  "${EDITOR_CHECKS[@]}" --output "$EDITOR_VENV_DIR/editor-environment.json"
echo "Ready. Python: $EDITOR_VENV_DIR/bin/python"
echo "Desktop: EDITOR_PYTHON=$EDITOR_VENV_DIR/bin/python bash $EDITOR_DIR/desktop.sh"
