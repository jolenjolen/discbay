# DiscBay guide

Everything the README leaves out. New here? Start with the
[README](../README.md), then come back when you want the details.

- [What gets installed](#what-gets-installed)
- [Installing from source](#installing-from-source)
- [Building the RPM](#building-the-rpm)
- [DiscBay Studio](#discbay-studio)
- [Labels](#labels)
- [Making discs from the command line](#making-discs-from-the-command-line)
- [Trust](#trust)
- [Discs you already own](#discs-you-already-own)
- [Changing a disc you've already burned](#changing-a-disc-youve-already-burned)
- [Commands](#commands)
- [How it works](#how-it-works)
- [Developing without a drive](#developing-without-a-drive)
- [Writing a frontend](#writing-a-frontend)

## What gets installed

Three services run for each user. The RPM switches them on for everyone,
starting at your next login (or right away with
`systemctl --user start discbayd discbay-island discbay-studio`):

| Service | What it does |
|---|---|
| `discbayd` | Watches the drive, runs the countdown, launches and closes apps |
| `discbay-island` | The notification island at the top of the screen |
| `discbay-studio` | The disc maker at http://localhost:8404 |

Your settings live in `~/.config/discbay/discs.toml` and trusted discs in
`~/.local/state/discbay`. Updates and uninstalls never touch them. To turn
one service off just for you, for example Studio:
`systemctl --user disable --now discbay-studio`. DiscBay Studio is also in
your app menu.

The island uses the layer-shell protocol, which KDE Plasma, Sway, Hyprland
and COSMIC support. On other desktops it falls back to a plain window.
It drops in just below a top panel rather than over it. On KDE a small KWin
script (loaded while the island runs) tells it where the panels are live, so
the island opens on the screen you're using, moves up when a panel auto-hides
or a fullscreen window covers it, and glides back down when the panel
returns. On other desktops it keeps clear of any panel that reserves space. To have it float over panels instead,
run `systemctl --user edit discbay-island` and add:

```ini
[Service]
Environment=DISCBAY_ISLAND_OVER_PANELS=1
```

KDE's own removable-device notifier also reacts to discs. You may want to
turn off its pop-up and automounting for the optical drive.

## Installing from source

Without the RPM, for example while hacking on DiscBay or on another distro:

```sh
sudo dnf install python3-gobject gtk4 gtk4-layer-shell python3-flask xorriso
git clone https://github.com/jolenjolen/discbay.git
cd discbay && ./install.sh
```

`install.sh` installs into your home folder without root. Run it again after
`git pull` to update; it restarts the services for you. To switch to the RPM
later, remove this install first with `./install.sh --uninstall`, because
its services would otherwise take priority over the RPM's.

## Building the RPM

```sh
sudo dnf install rpm-build python3-devel pyproject-rpm-macros systemd-rpm-macros \
  desktop-file-utils python3-gobject python3-flask xorriso 'dnf-command(builddep)'
packaging/build-rpm.sh
sudo dnf install dist/discbay-*.noarch.rpm
```

The script installs any remaining Python build dependencies itself (it asks
for your password once). The build runs the test suite. `packaging/build-rpm.sh --srpm` makes just
the source RPM. Bump the version in both `pyproject.toml` and
`packaging/discbay.spec` (the script checks they match), and add a
`%changelog` entry.

The packages on COPR are built from this repository by
[`.copr/Makefile`](../.copr/Makefile): in the COPR project, the package's
source is the GitHub repository with the *make srpm* build method, and the
GitHub webhook rebuilds it on every push.

## DiscBay Studio

Open **http://localhost:8404**:

- pick an installed app from a grid with its icon, a Steam game with its
  cover art, an AppImage, or anything else (a program file, a website or
  folder, a command)
- drop in any image as the cover. It's cropped to a square around its most
  detailed part (drag and zoom to adjust), and the disc colour is picked
  from it. Apps without a cover get one made from their icon
- see the disc and its island exactly as they'll appear, then burn with
  live progress, or save a disc image instead

While Studio is open, inserted discs don't autostart, so you can put in the
disc you want to rewrite. Studio only listens on this computer and refuses
requests from other websites. Change the port with `DISCBAY_STUDIO_PORT` in
`~/.config/systemd/user/discbay-studio.service`.

## Labels

**Print labels** in Studio prints the disc face and a case spine on
[Label Planet LPCD118P](https://www.labelplanet.co.uk/label-finder/lpcd118p/)
sheets: A4, two 118 mm disc labels with 41 mm holes, plus spine strips.
Choose the top or bottom labels so half-used sheets aren't wasted. A slim
clear jewel case shows the printed disc through the lid, and the 6.5 mm
strip folds around the case's 5 mm edge.

Before using real label sheets, print **the alignment test** on plain paper
and hold it over a sheet against a window. If it's off, nudge it in the
Alignment section; Studio remembers the setting. Print at A4, 100% (actual
size), with no margins.

## Making discs from the command line

```sh
discbay-ctl burn --label KRITA_01 --name "Krita" --color "#3daee9" \
    --art cover.png --app org.kde.krita --close-on-eject
```

**Discs stay rewritable.** `burn` leaves the disc open, so running it again
on the same disc (with different options) replaces what it launches, even on
a write-once CD-R. Each rewrite adds a new session that costs about 13 MB, so
a 700 MB CD-R allows roughly 40 to 50 rewrites; `burn` tells you how many are
left. Add `--final` to close a disc for good. If xorriso hits any problem,
it stops before writing, so a mistake never wastes a session.

Prefer an image file? `discbay-ctl make-disc` takes the same options and
writes an `.iso`. Burning that with `xorriso -as cdrecord` closes the disc.

Pick one launch target:

| Option | Launches | Example |
|---|---|---|
| `--app` | An installed app by desktop ID: native, Flatpak or Snap | `org.kde.krita` |
| `--exec` | A program file on this computer (add `--arg` for arguments) | `~/Apps/Game.AppImage` |
| `--steam` | A Steam game | `367520` |
| `--url` | A URL, file or folder, with its default app | `~/Music/Albums` |
| `--command` | Any command line | `"retroarch -L core.so rom.sfc"` |

Find desktop IDs with `ls /usr/share/applications` or
`flatpak list --app --columns=application`.

### Websites that open like a game

Add `--window` to a website `--url` (or pick **Opens** in Studio) and the
site opens without tabs or an address bar, like a native game. Handy for
cloud gaming:

```sh
discbay-ctl burn --label FORTNITE --name "Fortnite" --close-on-eject \
    --url "<the game's page on xbox.com/play>" --window fullscreen
```

| `--window` | Opens |
|---|---|
| `tab` | In a normal browser tab (the default) |
| `app` | In its own window, no tabs or address bar |
| `fullscreen` | That, full screen (F11 still leaves it) |
| `kiosk` | Full screen with no way out but closing it, or ejecting with `--close-on-eject` |

The disc stores only the website and how to open it, so it works on any
computer: DiscBay picks the browser. It prefers a Chromium-based one
(Chromium, Chrome, Edge, Brave, Vivaldi, native or Flatpak), as only those can
open a site as an app; with only Firefox, it opens a window of its own, full
screen for `fullscreen` and `kiosk`. To choose a browser yourself, set
`DISCBAY_BROWSER` (e.g. `google-chrome` or `com.microsoft.Edge`) with
`systemctl --user edit discbayd`.

The browser gets its own DiscBay profile, so it always starts as a separate
copy (your everyday browser windows are never touched) that ejecting the
disc can close. Sign in to the site once in that window and it stays signed
in. To make an already-burned web disc open this way, add one line locally:

```toml
[discs.FORTNITE]
window = "fullscreen"
```

## Trust

DiscBay never runs software stored on a disc; discs only point at apps that
are already installed. Even so, a disc made by someone else could point at
any command. So the first time a disc's manifest wants to launch something,
the island shows exactly what it will run and asks **Trust & start** or
**Not now**. Your answer is remembered for that exact disc; if the manifest
changes, it asks again. Manage it with `discbay-ctl trusted` and
`discbay-ctl untrust <fingerprint>`, or change the policy with
`disc_manifests` in `discs.toml`.

## Discs you already own

An old CD with no manifest can be mapped by its label instead. Run
`discbay-ctl probe` with it inserted, then add an entry to
`~/.config/discbay/discs.toml` (see [examples/discs.toml](../examples/discs.toml))
and run `discbay-ctl reload`.

## Changing a disc you've already burned

To change a disc everywhere, run `burn` on it again. To change a closed disc,
or just on this computer, you don't need to burn at all. Add an entry with
the disc's label to `discs.toml` containing only what you want to change,
and run `discbay-ctl reload`:

```toml
[discs.OPENTTD]
on_eject = "close"
```

Everything else (name, art, launch target) still comes from the disc. Only
a launch target you write yourself is trusted automatically.

## Commands

```sh
discbay-ctl state           # current state as JSON
discbay-ctl watch           # stream of state changes (one JSON per line)
discbay-ctl cancel | start | trust | eject | reload
discbay-ctl probe [x.iso]   # show what DiscBay sees on a disc or an image
discbay-ctl burn ...        # burn or rewrite a launcher disc (see burn -h)
discbay-ctl make-disc ...   # build a launcher ISO file instead
discbay-ctl trusted         # list trusted discs; untrust FINGERPRINT to forget one
```

## How it works

```
 optical drive ──ioctl──▶  discbayd  ──D-Bus / JSON──▶  any frontend
   (/dev/sr0)          state machine,                 GTK4 island, GNOME,
                       countdown, launch             Gamescope, widgets…
```

- The daemon asks the kernel for the drive status directly. Drives report
  "not ready" while a fresh disc spins up, so DiscBay knows a disc is there
  **before any data can be read**. That's when the chirp plays.
- Once the drive is ready, the label, the manifest and the cover art are read
  straight off the raw device, from the newest session on rewritten discs.
  No mounting.
- The daemon owns all logic, the countdown included. Frontends only draw, so
  you can write one for your desktop without touching the core.
- The daemon starts at login, before your desktop is up. Apps it launches get
  the desktop's environment (`WAYLAND_DISPLAY`, `DISPLAY` and the rest, as
  `systemctl --user show-environment` reports them), and a disc that's in the
  drive at boot waits for the desktop before its countdown starts.

## When an app doesn't start

If the island says **Closed straight away**, the app quit with an error
within a few seconds of starting. Its own output is in the daemon's log:

```sh
journalctl --user -u discbayd -n 50
```

## Developing without a drive

```sh
discbayd --simulate &
python3 frontends/gtk4-island/discbay_island.py &
discbay-ctl sim insert HOLLOW_01           # a bare label
discbay-ctl sim insert --iso krita_01.iso  # a disc image, manifest and art included
discbay-ctl sim remove
```

Run the tests with `python3 -m unittest discover -s tests`. The README's
hero animation is generated by `docs/media/make_hero.py`.

## Writing a frontend

Frontends live in `frontends/<name>/`. Read [PROTOCOL.md](PROTOCOL.md):
subscribe to one D-Bus signal (or read `discbay-ctl watch`), draw the
`state`, play sounds on `event`, and call `Cancel`, `LaunchNow` or `Trust`.
Pull requests for new desktops are very welcome.

Planned: gamepad controls on the island, identifying audio CDs by their
track list, and a Gamescope (SteamOS gaming mode) frontend.
