#!/usr/bin/env bash
# Install DiscBay for the current user (no root needed after the dnf step).
set -euo pipefail
cd "$(dirname "$0")"

if ! /usr/bin/python3 -c "import gi" 2>/dev/null; then
  echo "PyGObject is missing. On Fedora:  sudo dnf install python3-gobject gtk4 gtk4-layer-shell"
  exit 1
fi
studio=yes
if [ ! -f discbay/studio_static/index.html ]; then
  echo "error: discbay/studio_static/ is missing (index.html, studio.css, studio.js). Put it back and run this again."
  exit 1
fi
if ! /usr/bin/python3 -c "import flask" 2>/dev/null; then
  echo "Flask is missing, so DiscBay Studio won't be installed. On Fedora:  sudo dnf install python3-flask"
  studio=no
fi
command -v xorriso >/dev/null || echo "Tip: burning discs needs xorriso. On Fedora:  sudo dnf install xorriso"

if command -v pipx >/dev/null; then
  pipx install --force --system-site-packages .
else
  /usr/bin/python3 -m pip install --user --break-system-packages .
fi

install -Dm755 frontends/gtk4-island/discbay_island.py ~/.local/bin/discbay-island
install -Dm644 systemd/discbayd.service ~/.config/systemd/user/discbayd.service
install -Dm644 systemd/discbay-island.service ~/.config/systemd/user/discbay-island.service
install -Dm644 systemd/discbay-studio.service ~/.config/systemd/user/discbay-studio.service

if [ ! -f ~/.config/discbay/discs.toml ]; then
  install -Dm644 examples/discs.toml ~/.config/discbay/discs.toml
  echo "Created ~/.config/discbay/discs.toml (edit it to register your discs)"
fi

services="discbayd.service discbay-island.service"
if [ "$studio" = yes ]; then services="$services discbay-studio.service"; fi
systemctl --user daemon-reload
systemctl --user enable $services
systemctl --user restart $services   # pick up the new code on reinstall
echo "DiscBay is running. Insert a disc, or check with:  discbay-ctl state"
if [ "$studio" = yes ]; then echo "Make discs in DiscBay Studio:  http://localhost:8404"; fi
