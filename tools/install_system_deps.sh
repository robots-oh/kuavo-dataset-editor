#!/usr/bin/env bash
# Optional first-PC preparation. Run explicitly; setup.sh never invokes sudo.
set -euo pipefail
source /etc/os-release
if [[ "$ID" != ubuntu || ( "$VERSION_ID" != 22.04 && "$VERSION_ID" != 24.04 ) ]]; then
  echo "This helper supports Ubuntu 22.04/24.04. See docs/GETTING_STARTED.md for other Linux distributions." >&2
  exit 1
fi
EDITOR_ALSA=libasound2
if [[ "$VERSION_ID" == 24.04 ]]; then EDITOR_ALSA=libasound2t64; fi
sudo apt-get update
sudo apt-get install -y python3 python3-venv git openssh-client ca-certificates ffmpeg \
  libegl1 libopengl0 libnss3 libxkbcommon-x11-0 libxcb-cursor0 \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
  libxcb-xinerama0 libxcb-xkb1 "$EDITOR_ALSA"
