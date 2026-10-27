#!/usr/bin/env bash
# Lightweight local connection screen; remote data remains on the server.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec bash run.sh remote "$@"
