#!/usr/bin/env bash
# Portable source-only bundle. Does not include bags, credentials or environments.
set -euo pipefail
EDITOR_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EDITOR_NAME="$(basename -- "$EDITOR_DIR")"
EDITOR_BUNDLE="${1:-$EDITOR_DIR/dist/dataset-editor-$(date +%Y%m%d_%H%M%S).tar.gz}"
if [[ -e "$EDITOR_BUNDLE" ]]; then
  echo "Refusing to overwrite existing bundle: $EDITOR_BUNDLE" >&2
  exit 1
fi
mkdir -p -- "$(dirname -- "$EDITOR_BUNDLE")"
EDITOR_ITEMS=()
for item in README.md pyproject.toml uv.lock .python-version setup.sh run.sh desktop.sh package.sh .gitignore src tests tools docs .github; do
  if [[ -e "$EDITOR_DIR/$item" ]]; then
    EDITOR_ITEMS+=("$EDITOR_NAME/$item")
  fi
done
tar -czf "$EDITOR_BUNDLE" \
  --exclude="$EDITOR_NAME/.git" --exclude="$EDITOR_NAME/.venv" \
  --exclude="$EDITOR_NAME/.lerobot-source" --exclude="$EDITOR_NAME/workspace" \
  --exclude="$EDITOR_NAME/dist" --exclude="$EDITOR_NAME/build" \
  --exclude='*.bag' --exclude='*.mp4' --exclude='*.parquet' --exclude='*.log' \
  --exclude='*.sqlite*' --exclude='.env*' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='*.egg-info' \
  -C "$(dirname -- "$EDITOR_DIR")" "${EDITOR_ITEMS[@]}"
echo "$EDITOR_BUNDLE"
