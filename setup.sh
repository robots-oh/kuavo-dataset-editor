#!/usr/bin/env bash
# No sudo, no ROS/system Python package changes. Python -I ignores ROS PYTHONPATH.
set -euo pipefail
EDITOR_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${EDITOR_BOOTSTRAP_PYTHON:-python3}" -I "$EDITOR_DIR/tools/setup_env.py" "$@"
