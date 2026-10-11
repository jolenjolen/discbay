# DiscBay

**Console-style disc launching for Linux.** Slide a CD into the drive, hear the
chirp as it spins up, and a notification island swooshes down from the top of
the screen with the disc's name and a short countdown. Let it run and your
game or app starts. Cancel it and nothing happens. Pull the disc out and,
if you like, the app closes, just like a cartridge.

The discs don't hold the software. A disc carries a tiny `discbay.toml`
manifest (its name, colour, cover art and which installed app to open), or
just a volume label you map to an app yourself. Any app works: native apps,
Flatpaks, AppImages, Steam games, emulators, URLs, folders.

## How it works

```
 optical drive ──ioctl──▶  discbayd  ──D-Bus / JSON──▶  any frontend
   (/dev/sr0)          state machine,                 GTK4 island, GNOME,
                       countdown, launch             Gamescope, widgets…
```

- The daemon asks the kernel for the drive status directly. Drives report
  "not ready" while a fresh disc spins up, so DiscBay knows a disc is there
  **before any data can be read**. That's when the chirp plays.
- Once the drive is ready, the ISO 9660 label, the manifest and the cover art
  are read straight off the raw device. No mounting.
- The daemon owns all logic (countdown included). Frontends only draw, so
  you can write one for your desktop without touching the core. See
  [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Install (Fedora)

```sh
sudo dnf install python3-gobject gtk4 gtk4-layer-shell python3-flask xorriso
git clone https://github.com/jolenjolen/discbay.git
cd discbay
./install.sh
```

Run `./install.sh` again after `git pull` to update; it restarts the services for you.

This starts three user services: `discbayd` (the daemon), `discbay-island`
(the notification island) and `discbay-studio` (the disc maker below).

## DiscBay Studio

Open **http://localhost:8404** to make discs without the command line:

- pick an installed app from a grid with its icon, a Steam game with its
  cover art, an AppImage, or anything else (a program file, a website or
  folder, a command)
- drop in any image as the cover. It's cropped to a square around its most
  detailed part (drag and zoom to adjust), and the disc colour is picked
  from it. Apps without a cover get one made from their icon
- see the disc and its island exactly as they'll appear, then burn with
  live progress, or save a disc image instead

### Labels

**Print labels** in Studio prints the disc face and a case spine on
[Label Planet LPCD118P](https://www.labelplanet.co.uk/label-finder/lpcd118p/)
sheets (A4, two 118 mm disc labels with 41 mm holes, plus spine strips).
Choose the top or bottom labels so half-used sheets aren't wasted. A slim
clear jewel case shows the printed disc through the lid, and the 6.5 mm
strip folds around the case's 5 mm edge.

Before using real label sheets, print **the alignment test** on plain paper
and hold it over a sheet against a window. If it's off, nudge it in the
Alignment section; Studio remembers the setting. Print at A4, 100% (actual
size), with no margins.

While Studio is open, inserted discs don't autostart, so you can put in the
disc you want to rewrite. Studio only listens on this computer and refuses
requests from other websites. Change the port with `DISCBAY_STUDIO_PORT` in
`~/.config/systemd/user/discbay-studio.service`.

The bundled island frontend uses the layer-shell protocol, which KDE Plasma,
Sway, Hyprland and COSMIC support. On other desktops it falls back to a plain
window.

**Tip:** KDE's own removable-device notifier will also react to discs. You may
want to turn off its pop-up and automounting for the optical drive.

## Making a disc

```sh
sudo dnf install xorriso
discbay-ctl burn --label KRITA_01 --name "Krita" --color "#3daee9" \
    --art cover.png --app org.kde.krita --close-on-eject
```

**Discs stay rewritable.** `burn` leaves the disc open, so running it again
on the same disc (with different options) replaces what it launches, even on
a write-once CD-R. Each rewrite adds a new session that costs about 13 MB, so
a 700 MB CD-R allows roughly 40-50 rewrites; `burn` tells you how many are
left. Add `--final` to close a disc for good. If xorriso hits any problem,
it stops before writing, so a mistake never wastes a session.

Prefer an image file? `discbay-ctl make-disc` takes the same options and
writes an `.iso`. Note that burning that with `xorriso -as cdrecord` closes
the disc.

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

### Trust

DiscBay never runs software stored on a disc; discs only point at apps that
are already installed. Even so, a disc made by someone else could point at
any command. So the first time a disc's manifest wants to launch something,
the island shows exactly what it will run and asks **Trust & start** or
**Not now**. Your answer is remembered for that exact disc; if the manifest
changes, it asks again. Manage it with `discbay-ctl trusted` and
`discbay-ctl untrust <fingerprint>`, or change the policy with
`disc_manifests` in `discs.toml`.

### Discs you already own

An old CD with no manifest can be mapped by its label instead. Run
`discbay-ctl probe` with it inserted, then add an entry to
`~/.config/discbay/discs.toml` (see [examples/discs.toml](examples/discs.toml))
and run `discbay-ctl reload`.

### Changing a disc you've already burned

To change a disc everywhere, run `burn` on it again. To change a closed disc,
or just on this computer, you don't need to burn at all.
Add an entry with the disc's label to `discs.toml` containing only what you
want to change, and run `discbay-ctl reload`:

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

## Developing without a drive

```sh
discbayd --simulate &
python3 frontends/gtk4-island/discbay_island.py &
discbay-ctl sim insert HOLLOW_01           # a bare label
discbay-ctl sim insert --iso krita_01.iso  # a disc image, manifest and art included
discbay-ctl sim remove
```

Run the tests with `python3 -m unittest discover -s tests`.

## Writing a frontend

Frontends live in `frontends/<name>/`. Read [docs/PROTOCOL.md](docs/PROTOCOL.md):
subscribe to one D-Bus signal (or read `discbay-ctl watch`), draw the
`state`, play sounds on `event`, and call `Cancel` / `LaunchNow` / `Trust`. Pull
requests for new desktops are very welcome.

## Status

Early prototype. Planned: gamepad controls on the island, audio-CD
identification by table of contents, a Gamescope (SteamOS gaming mode) frontend.

## Licence

DiscBay is free software, released under the [GNU General Public License v3.0 or later](LICENSE).
