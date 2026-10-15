#!/usr/bin/env bash
# Creates only an editor-local environment. Never source ROS/Isaac setup here.
set -euo pipefail
EDITOR_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EDITOR_BOOTSTRAP_PYTHON="${EDITOR_BOOTSTRAP_PYTHON:-python3.12}"
EDITOR_WITH_EXPORT=0
EDITOR_WITH_DESKTOP=0
for arg in "$@"; do
  case "$arg" in
    --with-export) EDITOR_WITH_EXPORT=1 ;;
    --with-desktop) EDITOR_WITH_DESKTOP=1 ;;
    *) echo "Usage: bash setup.sh [--with-export] [--with-desktop]" >&2; exit 1 ;;
  esac
done
"$EDITOR_BOOTSTRAP_PYTHON" -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required"'
"$EDITOR_BOOTSTRAP_PYTHON" -m venv "$EDITOR_DIR/.venv"
EDITOR_VENV_PYTHON="$EDITOR_DIR/.venv/bin/python"
"$EDITOR_VENV_PYTHON" -m pip install --upgrade pip
"$EDITOR_VENV_PYTHON" -m pip install -e "$EDITOR_DIR"
if [[ "$EDITOR_WITH_DESKTOP" == 1 ]]; then
  "$EDITOR_VENV_PYTHON" -m pip install -e "$EDITOR_DIR[desktop]"
fi
if [[ "$EDITOR_WITH_EXPORT" == 1 ]]; then
  "$EDITOR_VENV_PYTHON" -m pip install 'torch==2.9.1' 'torchvision==0.24.1' --index-url https://download.pytorch.org/whl/cpu
  "$EDITOR_VENV_PYTHON" -m pip install \
    'lerobot[dataset] @ git+https://github.com/huggingface/lerobot.git@d20a4538016d9fe0374a34011aebc1c8cddf3e0d' \
    'torch==2.9.1' 'torchvision==0.24.1' 'torchcodec==0.8.1'
fi
echo "Ready: bash $EDITOR_DIR/run.sh"
