#!/usr/bin/env bash
# Install DiscBay for the current user, without root (after the dnf step).
#   ./install.sh               install or update
#   ./install.sh --uninstall   remove this per-user install (do this before switching to the RPM)
set -euo pipefail
cd "$(dirname "$0")"

UNITS="discbayd.service discbay-island.service discbay-studio.service"
UNIT_DIR=~/.config/systemd/user
APPS=~/.local/share/applications
ICONS=~/.local/share/icons/hicolor/scalable/apps

if [ "${1:-}" = "--uninstall" ]; then
  systemctl --user disable --now $UNITS 2>/dev/null || true
  for unit in $UNITS; do rm -f "$UNIT_DIR/$unit"; done
  systemctl --user daemon-reload
  rm -f ~/.local/bin/discbay-island "$APPS/me.jolen.DiscBay.Studio.desktop" "$ICONS/me.jolen.DiscBay.svg"
  if command -v pipx >/dev/null && pipx list --short 2>/dev/null | grep -q '^discbay '; then
    pipx uninstall discbay
  else
    /usr/bin/python3 -m pip uninstall -y --break-system-packages discbay 2>/dev/null || true
  fi
  echo "Removed the per-user install. Your discs.toml and trusted discs are kept in ~/.config and ~/.local/state."
  exit 0
fi

if rpm -q discbay >/dev/null 2>&1; then
  echo "DiscBay is already installed as an RPM. Update it with:  sudo dnf upgrade discbay"
  echo "(A per-user install on top would override it.)"
  exit 1
fi

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
# The units in systemd/ point at /usr/bin (where the RPM puts things); point them at ~/.local/bin.
for unit in $UNITS; do
  install -Dm644 "systemd/$unit" "$UNIT_DIR/$unit"
  sed -i "s#^ExecStart=/usr/bin/#ExecStart=$HOME/.local/bin/#" "$UNIT_DIR/$unit"
done
if [ "$studio" = yes ]; then
  install -Dm644 data/me.jolen.DiscBay.Studio.desktop "$APPS/me.jolen.DiscBay.Studio.desktop"
  install -Dm644 data/me.jolen.DiscBay.svg "$ICONS/me.jolen.DiscBay.svg"
fi

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
if [ "$studio" = yes ]; then echo "Make discs in DiscBay Studio (in your app menu, or http://localhost:8404)"; fi
