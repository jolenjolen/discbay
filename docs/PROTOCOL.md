# DiscBay frontend protocol (v1)

DiscBay is split in two:

- **`discbayd`**, the daemon, is the single source of truth. It watches the
  drive, identifies discs, runs the autostart countdown and launches apps.
- **Frontends** only draw. They never decide anything; they show the state
  they are sent and ask the daemon to act.

Because of this split, anyone can write a frontend for their own desktop
(GNOME, Gamescope, Hyprland widgets, a TUI, an LED strip...) without touching
the daemon, and every frontend behaves identically.

There are two ways to connect. Pick whichever is easier in your language.

## Option A: D-Bus (session bus)

| | |
|---|---|
| Bus name | `me.jolen.DiscBay1` |
| Object path | `/me/jolen/DiscBay1` |
| Interface | `me.jolen.DiscBay1` |

**Signal**

- `StateChanged(s state_json)`: sent on every change. Always a *full*
  snapshot, so you never need to keep your own history.

**Methods**

| Method | Effect |
|---|---|
| `GetState() -> s` | Current snapshot (call it once on startup, event is `"sync"`). |
| `Cancel()` | Stop the autostart countdown, or decline to trust a new disc. |
| `Trust()` | In `untrusted`: remember this disc and launch it. |
| `LaunchNow()` | Start the disc's app immediately. |
| `Eject()` | Eject the disc. |
| `ReloadConfig()` | Re-read `discs.toml`. |
| `Hold(u seconds, b exclusive)` | Lease for tools like DiscBay Studio. While held, discs don't autostart or ask for trust. `exclusive` also stops the daemon touching the drive (for burning). Leases expire on their own; renew them, and pass `0` to release early. The two leases are separate. |
| `SimulateInsert(s label, s manifest_toml, s base_dir)` / `SimulateRemove()` | Only when the daemon runs with `--simulate`. Pass empty strings for a disc without a manifest. |

**Property:** `ProtocolVersion` (u).

## Option B: JSON lines on stdout

```sh
discbay-ctl watch
```

prints the current snapshot, then one line of JSON per change. Send actions
with `discbay-ctl cancel | start | trust | eject`. This is handy for eww, AGS,
Quickshell, waybar modules or shell scripts.

## The snapshot

```json
{
  "protocol": 1,
  "seq": 7,
  "event": "identified",
  "state": "countdown",
  "disc": {
    "label": "HOLLOW_01",
    "name": "Hollow Knight",
    "subtitle": "Team Cherry",
    "art": "/home/you/.config/discbay/art/hollow-knight.png",
    "color": "#5b6cff",
    "autostart": true,
    "source": "local"
  },
  "kind": "data",
  "message": null,
  "countdown": { "total": 5.0, "remaining": 4.98, "ends_at": 1791810000.12 },
  "can_launch": true,
  "pid": null,
  "tracked": false,
  "time": 1791809995.14
}
```

| Field | Meaning |
|---|---|
| `seq` | Increases on every change. Ignore a snapshot with a lower `seq` than one you've seen. |
| `event` | What just happened. Use it for sounds and one-off animations. |
| `state` | What is true right now. Use it for what to draw. |
| `disc` | `null` until the label is read. `source` is `"local"` (the user's discs.toml) or `"disc"` (the disc's own manifest). `art` is a local image path, already extracted from the disc. |
| `disc.launch` | Only present in `untrusted`: a readable description of what the disc wants to run. Show it to the user before they trust it. In every other state the launch target is never sent. |
| `can_launch` | Whether `LaunchNow()` would do anything right now. Use it to show or hide a Start button. |
| `held` | A tool holds the daemon (autostart paused). |
| `burning` | A disc is being burned. Show that instead of the normal state, and don't play the insert sound. |
| `countdown.ends_at` | Unix time the autostart fires. Animate locally from this rather than waiting for updates; the daemon does not send ticks. |
| `message` | Human-readable detail for `unknown`, `idle` and `error`. |

### States

| State | Meaning | Typical UI |
|---|---|---|
| `empty` | No disc | Hidden |
| `spinning` | Disc detected, not readable yet | "Reading disc…", fast spin |
| `countdown` | Known disc, autostart pending | Name, timer, Cancel / Start |
| `unknown` | Readable, not registered, no manifest | Show the label so the user can register it |
| `untrusted` | Disc's manifest wants to launch something new | Show `disc.launch`; offer Trust / Not now. Never trust on a default keypress. |
| `idle` | Disc in, nothing pending | Brief "cancelled", or a Start button |
| `running` | App launched | Brief "Starting…", then hide |
| `error` | Unreadable disc or failed launch | Show `message` |

### Events

`inserted` (play the insert sound here; it fires before any data is read),
`identified`, `cancelled`, `launched`, `app_exited`, `ejected`, `failed`, `busy`
(`held` or `burning` changed), `sync`.

## Compatibility rules

- Ignore fields you don't recognise; new fields can appear at any time.
- If `protocol` is higher than you support, keep working but warn the user.
- Renaming or removing a field bumps the protocol version and the D-Bus
  names (`DiscBay2`), so old frontends never silently break.

## Minimal frontend in 15 lines

```python
import json, gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

bus = Gio.bus_get_sync(Gio.BusType.SESSION)

def on_change(conn, sender, path, iface, sig, params):
    s = json.loads(params.unpack()[0])
    if s["event"] == "inserted":
        print("*beep*")
    print(s["state"], (s["disc"] or {}).get("name"))

bus.signal_subscribe(None, "me.jolen.DiscBay1", "StateChanged",
                     "/me/jolen/DiscBay1", None, Gio.DBusSignalFlags.NONE, on_change)
GLib.MainLoop().run()
```
