#!/usr/bin/env python3
"""DiscBay island: a GTK4 notification island that swooshes in from the top.

This is ONE frontend. It holds no logic of its own: it listens to discbayd's
StateChanged signal and draws what it is told. The countdown, launching and
ejecting all happen in the daemon.

Works best on compositors with the layer-shell protocol (KDE Plasma, Sway,
Hyprland, COSMIC...). Elsewhere it falls back to a plain undecorated window.
Requires: python3-gobject, gtk4, gtk4-layer-shell (Fedora package names).
"""

from __future__ import annotations

import glob
import json
import math
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Callable

# --- gtk4-layer-shell must be loaded before libwayland-client -----------------
# The library has to come first in the link order, so we re-exec ourselves with
# LD_PRELOAD set. This is the approach the gtk4-layer-shell docs recommend for Python.
def _maybe_preload_layer_shell() -> None:
    if os.environ.get("DISCBAY_NO_LAYER_SHELL") or not os.environ.get("WAYLAND_DISPLAY"):
        return
    if "gtk4-layer-shell" in os.environ.get("LD_PRELOAD", ""):
        return
    patterns = ["/usr/lib64/libgtk4-layer-shell.so*", "/usr/lib/libgtk4-layer-shell.so*",
                "/usr/lib/*-linux-gnu/libgtk4-layer-shell.so*", "/usr/local/lib*/libgtk4-layer-shell.so*"]
    for pattern in patterns:
        found = sorted(glob.glob(pattern))
        if found:
            env = dict(os.environ)
            env["LD_PRELOAD"] = ":".join(filter(None, [found[0], env.get("LD_PRELOAD")]))
            os.execve(sys.executable, [sys.executable] + sys.argv, env)


_maybe_preload_layer_shell()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Pango", "1.0")
import cairo  # noqa: E402  (python3-gobject ships the cairo bindings on Fedora)
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
except (ValueError, ImportError):
    LayerShell = None

BUS_NAME = "me.jolen.DiscBay1"
OBJECT_PATH = "/me/jolen/DiscBay1"
INTERFACE = "me.jolen.DiscBay1"
SUPPORTED_PROTOCOL = 1

# Gap between the top of the screen (or a panel) and the island, in px. The
# pill's CSS margin adds to it: the surface is bigger than the pill so its
# shadow fits inside, instead of being cut off in a square.
TOP_MARGIN = 2
PILL_MARGIN_TOP, PILL_MARGIN_SIDE, PILL_MARGIN_BOTTOM = 16, 36, 44
DEFAULT_DISC_COLOR = "#8a8fa3"
ACCENT = "#ffffff"

CSS = (b"""
window.discbay-island { background: transparent; box-shadow: none; border: none; }
.island {
  background: rgba(12, 12, 16, 0.94);
  border-radius: 999px;
  padding: 9px 10px 9px 9px;
  margin: %dpx %dpx %dpx %dpx;
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.42), 0 2px 6px rgba(0, 0, 0, 0.25),
              inset 0 0 0 1px rgba(255, 255, 255, 0.07);
  color: white;
}
.island .title { font-weight: 700; font-size: 15px; }
.island .sub   { font-size: 12px; color: rgba(255, 255, 255, 0.62); font-feature-settings: "tnum"; }
.island .sub.warn { color: #ffcc66; }
.island button.pill {
  border-radius: 999px; min-height: 0; min-width: 0;
  padding: 7px 15px; border: none; box-shadow: none;
  background: rgba(255, 255, 255, 0.12); color: white; font-weight: 600;
}
.island button.pill:hover { background: rgba(255, 255, 255, 0.2); }
.island button.pill.primary { background: rgba(255, 255, 255, 0.94); color: #0c0c10; }
.island button.pill.primary:hover { background: #ffffff; }
""" % (PILL_MARGIN_TOP, PILL_MARGIN_SIDE, PILL_MARGIN_BOTTOM, PILL_MARGIN_SIDE))


# ---------------------------------------------------------------------------- sound
def _insert_sound_path() -> Path:
    """Generate a short two-tone console chirp once and cache it."""
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "discbay"
    path = cache / "insert-chirp.wav"
    if path.exists():
        return path
    cache.mkdir(parents=True, exist_ok=True)
    rate, frames = 44100, bytearray()
    for freq, dur in ((988.0, 0.055), (0.0, 0.025), (1480.0, 0.09)):
        n = int(rate * dur)
        for i in range(n):
            env = min(1.0, i / (rate * 0.004)) * min(1.0, (n - i) / (rate * 0.02))
            sample = 0.32 * env * math.sin(2 * math.pi * freq * i / rate) if freq else 0.0
            frames += struct.pack("<h", int(sample * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def play_sound(path: Path) -> None:
    for player in ("pw-play", "paplay", "aplay"):
        exe = shutil.which(player)
        if exe:
            args = [exe, "-q", str(path)] if player == "aplay" else [exe, str(path)]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


# ---------------------------------------------------------------------------- easing
def ease_out_back(t: float) -> float:
    c1 = 1.4
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def ease_in_cubic(t: float) -> float:
    return t * t * t


def parse_hex(color: str | None) -> tuple[float, float, float]:
    color = (color or DEFAULT_DISC_COLOR).lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    try:
        return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return parse_hex(DEFAULT_DISC_COLOR)


# ---------------------------------------------------------------------------- the disc
# Plasma's scripting API: one line per panel, "location,height,hiding,floating".
PLASMA_PANEL_SCRIPT = (
    "var o=[];panels().forEach(function(p){"
    "o.push([p.location,p.height,p.hiding,p.floating?1:0].join(','))});print(o.join('\\n'));"
)
PLASMA_FLOAT_GAP = 8  # a floating Plasma panel hovers this far from the screen edge


def top_panel_offset(report: str) -> int:
    """How far down the island must sit to clear Plasma's top panels."""
    offset = 0
    for line in report.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 4 or parts[0] != "top":
            continue
        try:
            height = int(float(parts[1]))
        except ValueError:
            continue
        if parts[2] == "autohide":
            continue   # tucked away until you hover: nothing to avoid
        offset = max(offset, height + (PLASMA_FLOAT_GAP if parts[3] == "1" else 0))
    return offset


def pixbuf_to_surface(pixbuf: GdkPixbuf.Pixbuf) -> cairo.ImageSurface:
    """A cairo copy of a pixbuf (Gdk.cairo_set_source_pixbuf is deprecated)."""
    if not pixbuf.get_has_alpha():
        pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
    w, h, rowstride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
    src = pixbuf.get_pixels()
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
    out = bytearray(stride * h)
    little = sys.byteorder == "little"
    for y in range(h):
        row = src[y * rowstride: y * rowstride + w * 4]
        o = y * stride
        for x in range(0, w * 4, 4):
            r, g, b, a = row[x], row[x + 1], row[x + 2], row[x + 3]
            r, g, b = r * a // 255, g * a // 255, b * a // 255   # cairo wants premultiplied
            out[o + x: o + x + 4] = bytes((b, g, r, a) if little else (a, r, g, b))
    return cairo.ImageSurface.create_for_data(out, cairo.FORMAT_ARGB32, w, h, stride)


class DiscWidget(Gtk.DrawingArea):
    SIZE = 46

    def __init__(self):
        super().__init__()
        self.set_content_width(self.SIZE)
        self.set_content_height(self.SIZE)
        self.set_valign(Gtk.Align.CENTER)
        self.color = parse_hex(None)
        self.art: cairo.ImageSurface | None = None
        self._art_path: str | None = None
        self.angle = 0.0
        self.speed = 0.0          # radians per second
        self.target_speed = 0.0
        self.progress: float | None = None   # countdown fraction remaining, or None
        self.set_draw_func(self._draw)

    def set_theme(self, color: str | None, art: str | None) -> None:
        self.color = parse_hex(color)
        if art != self._art_path:
            self._art_path = art
            self.art = None
            if art and os.path.exists(art):
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(art, 96, 96, True)
                    self.art = pixbuf_to_surface(pixbuf)
                except GLib.Error:
                    self.art = None

    def advance(self, dt: float) -> None:
        # Ease towards the target speed so spin-up and slow-down feel physical.
        self.speed += (self.target_speed - self.speed) * min(1.0, dt * 3.0)
        self.angle = (self.angle + self.speed * dt) % (2 * math.pi)

    def _draw(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        r = min(width, height) / 2 - 4
        hole = r * 0.17
        hub = r * 0.36
        red, green, blue = self.color

        # Countdown ring around the disc.
        cr.set_line_width(2.4)
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.arc(cx, cy, r + 2.6, 0, 2 * math.pi)
        cr.stroke()
        if self.progress is not None:
            cr.set_source_rgba(1, 1, 1, 0.95)
            start = -math.pi / 2
            cr.arc(cx, cy, r + 2.6, start, start + 2 * math.pi * max(0.0, self.progress))
            cr.stroke()

        cr.save()
        cr.translate(cx, cy)
        cr.rotate(self.angle)

        # Disc body as a ring (even-odd leaves the centre hole see-through).
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.arc(0, 0, r, 0, 2 * math.pi)
        cr.new_sub_path()
        cr.arc(0, 0, hole, 0, 2 * math.pi)
        cr.clip()

        if self.art is not None:
            scale = (2 * r) / min(self.art.get_width(), self.art.get_height())
            cr.save()
            cr.scale(scale, scale)
            cr.set_source_surface(self.art, -self.art.get_width() / 2, -self.art.get_height() / 2)
            cr.paint()
            cr.restore()
        else:
            cr.set_source_rgb(red * 0.55, green * 0.55, blue * 0.55)
            cr.paint()
            # Printed label area in the disc colour.
            cr.set_source_rgb(red, green, blue)
            cr.arc(0, 0, r * 0.94, 0, 2 * math.pi)
            cr.fill()

        # Rainbow sheen: two opposite wedges, which is what makes rotation readable.
        for base in (0.0, math.pi):
            for i, (sr, sg, sb) in enumerate(((1, .55, .9), (.55, .85, 1), (.75, 1, .7))):
                a0 = base + 0.32 + i * 0.16
                cr.move_to(0, 0)
                cr.arc(0, 0, r, a0, a0 + 0.2)
                cr.close_path()
                cr.set_source_rgba(sr, sg, sb, 0.22)
                cr.fill()

        # Clear hub ring around the hole, like a real CD.
        cr.set_source_rgba(1, 1, 1, 0.28)
        cr.arc(0, 0, hub, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(1)
        cr.arc(0, 0, hub * 0.72, 0, 2 * math.pi)
        cr.stroke()
        cr.restore()


# ---------------------------------------------------------------------------- panels (KDE)
ISLAND_BUS = "me.jolen.DiscBay1.Island"
ISLAND_PATH = "/me/jolen/DiscBay1/Island"
ISLAND_IFACE = "me.jolen.DiscBay1.Island"
ISLAND_XML = f"""
<node>
  <interface name="{ISLAND_IFACE}">
    <method name="Panels"><arg type="s" name="report" direction="in"/></method>
  </interface>
</node>
"""
KWIN_SCRIPT_NAME = "discbay-island-panels"

# Runs inside KWin, which knows where every panel is right now: whether an
# auto-hiding panel is out or tucked away, and whether a fullscreen window is
# covering it. It reports the visible top panels to the island whenever that
# might have changed.
KWIN_SCRIPT = """
var BUS = "%(bus)s", PATH = "%(path)s", IFACE = "%(iface)s";
function report() {
    var active = workspace.activeWindow;
    var covered = {};
    // A focused fullscreen window sits above the panels on its screen.
    if (active && active.fullScreen && !active.minimized && active.output)
        covered[active.output.name] = true;
    var panels = [];
    var wins = workspace.windowList();
    for (var i = 0; i < wins.length; i++) {
        var w = wins[i];
        if (!w.dock || w.hidden || w.minimized || !w.output || covered[w.output.name])
            continue;
        var o = w.output.geometry, g = w.frameGeometry;
        if (g.width < g.height)
            continue;                                   // a vertical panel
        var bottom = g.y + g.height - o.y;
        if (g.y - o.y > g.height || bottom <= 1)
            continue;                                   // not at the top, or slid away
        panels.push({output: w.output.name, bottom: Math.round(bottom)});
    }
    var screen = workspace.activeScreen ? workspace.activeScreen.name : "";
    callDBus(BUS, PATH, IFACE, "Panels", JSON.stringify({active: screen, panels: panels}));
}
function watch(w) {
    var names = ["fullScreenChanged", "minimizedChanged", "outputChanged", "hiddenChanged"];
    if (w.dock)
        names.push("frameGeometryChanged");
    for (var i = 0; i < names.length; i++)
        if (w[names[i]])
            w[names[i]].connect(report);
}
var all = workspace.windowList();
for (var i = 0; i < all.length; i++)
    watch(all[i]);
workspace.windowAdded.connect(function (w) { watch(w); report(); });
workspace.windowRemoved.connect(report);
workspace.windowActivated.connect(report);
if (workspace.screensChanged)
    workspace.screensChanged.connect(report);
report();
""" % {"bus": ISLAND_BUS, "path": ISLAND_PATH, "iface": ISLAND_IFACE}


def panel_bottom(report: dict, output: str | None) -> int:
    """How far down a visible top panel reaches on this output (0 if none)."""
    return max((int(p.get("bottom", 0)) for p in report.get("panels", [])
                if output is None or p.get("output") == output), default=0)


class KWinPanels:
    """Keeps a live picture of KDE's top panels, from a small KWin script.

    ``report`` is None until KWin has answered (or if this isn't KDE).
    """

    def __init__(self, bus: Gio.DBusConnection, on_change: Callable[[], None]):
        self.bus = bus
        self.on_change = on_change
        self.report: dict | None = None
        node = Gio.DBusNodeInfo.new_for_xml(ISLAND_XML)
        try:
            bus.register_object(ISLAND_PATH, node.interfaces[0], self._on_call, None, None)
        except GLib.Error as exc:
            print(f"discbay-island: can't listen for panel updates: {exc.message}", file=sys.stderr)
            return
        Gio.bus_own_name_on_connection(bus, ISLAND_BUS, Gio.BusNameOwnerFlags.REPLACE,
                                       lambda *_: self._load(), None)

    def _on_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            self.report = json.loads(params.unpack()[0])
        except (ValueError, TypeError):
            pass
        invocation.return_value(None)
        self.on_change()

    def _kwin(self, path: str, iface: str, method: str, args=None, reply=None):
        return self.bus.call_sync("org.kde.KWin", path, iface, method, args,
                                  GLib.VariantType(reply) if reply else None,
                                  Gio.DBusCallFlags.NO_AUTO_START, 2000, None)

    def _load(self) -> None:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or GLib.get_user_cache_dir()
        path = os.path.join(runtime, KWIN_SCRIPT_NAME + ".js")
        try:
            with open(path, "w") as f:
                f.write(KWIN_SCRIPT)
            name = GLib.Variant("(s)", (KWIN_SCRIPT_NAME,))
            # A copy left over from an earlier run of the island would report twice.
            self._kwin("/Scripting", "org.kde.kwin.Scripting", "unloadScript", name, "(b)")
            script_id = self._kwin("/Scripting", "org.kde.kwin.Scripting", "loadScript",
                                   GLib.Variant("(ss)", (path, KWIN_SCRIPT_NAME)), "(i)").unpack()[0]
            if script_id < 0:
                raise GLib.Error("KWin refused the script")
            self._kwin(f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run")
        except (GLib.Error, OSError):
            self.report = None   # not KDE (or KWin scripting is off): the fallbacks apply

    def unload(self) -> None:
        try:
            self._kwin("/Scripting", "org.kde.kwin.Scripting", "unloadScript",
                       GLib.Variant("(s)", (KWIN_SCRIPT_NAME,)), "(b)")
        except GLib.Error:
            pass

    def active_output(self) -> str | None:
        """The screen you're working on right now (follows the mouse or focus, per KDE's setting)."""
        try:
            return self._kwin("/KWin", "org.kde.KWin", "activeOutputName", None, "(s)").unpack()[0] or None
        except GLib.Error:
            return (self.report or {}).get("active") or None


# ---------------------------------------------------------------------------- the island
class Island(Gtk.ApplicationWindow):
    # How long the island lingers on transient states before retracting (seconds).
    LINGER = {"launched": 1.6, "cancelled": 1.3, "ejected": 1.0,
              "unknown": 4.5, "error": 4.5, "app_exited": 0.0}

    def __init__(self, app: Gtk.Application):
        super().__init__(application=app)
        self.add_css_class("discbay-island")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_title("DiscBay")

        self.over_panels = False
        self.bus: Gio.DBusConnection | None = None
        self.layer = bool(LayerShell and getattr(LayerShell, "is_supported", lambda: True)())
        if self.layer:
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, "discbay-island")
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)
            # Exclusive zone 0 asks the compositor to place us clear of anything
            # that reserves screen space (a top panel, a dock): the island drops
            # in just below it instead of covering it, and moves by itself if
            # the panel is resized, moved or hidden. -1 would float over panels.
            self.over_panels = os.environ.get("DISCBAY_ISLAND_OVER_PANELS", "") not in ("", "0")
            LayerShell.set_exclusive_zone(self, -1 if self.over_panels else 0)
        self.panel_offset = 0      # px to clear top panels, when we place ourselves (KDE)
        self.offset_now = 0.0      # eases towards panel_offset so the island glides
        self.plasma = False
        self.kwin: KWinPanels | None = None
        self.output: str | None = None   # the screen the island is on, when we chose it
        self._input_box = None

        self._build()

        self.state: dict | None = None
        self.shown = 0.0                 # 0 = hidden above the screen, 1 = in place
        self._anim: tuple[float, float, float, float, str] | None = None  # from, to, start, dur, kind
        self._hide_timer = 0
        self._last_frame = time.monotonic()
        self._last_whole_second: int | None = None
        self.add_tick_callback(self._on_frame)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # ------------------------------------------------------------- layout
    def _build(self) -> None:
        self.disc = DiscWidget()

        self.title = Gtk.Label(xalign=0, css_classes=["title"], ellipsize=Pango.EllipsizeMode.END,
                               width_chars=13, max_width_chars=26)
        self.sub = Gtk.Label(xalign=0, css_classes=["sub"], ellipsize=Pango.EllipsizeMode.MIDDLE,
                             max_width_chars=44)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=1)
        text.append(self.title)
        text.append(self.sub)

        self.primary_action = "LaunchNow"   # or "Trust" for a new disc
        self.cancel_btn = Gtk.Button(label="Cancel", css_classes=["pill"])
        self.cancel_btn.connect("clicked", lambda *_: self._call("Cancel"))
        self.start_btn = Gtk.Button(label="Start", css_classes=["pill", "primary"])
        self.start_btn.connect("clicked", lambda *_: self._call(self.primary_action))
        buttons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, margin_start=14)
        buttons.append(self.cancel_btn)
        buttons.append(self.start_btn)
        self.buttons = Gtk.Revealer(child=buttons, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                    transition_duration=260)

        row = Gtk.Box(spacing=11, css_classes=["island"])
        row.append(self.disc)
        row.append(text)
        row.append(self.buttons)
        self.pill = row
        self.set_child(row)

    # ------------------------------------------------------------- daemon
    def connect_daemon(self, bus: Gio.DBusConnection) -> None:
        self.bus = bus
        if self.layer and not self.over_panels:
            self.kwin = KWinPanels(bus, self._panels_changed)
        bus.signal_subscribe(None, INTERFACE, "StateChanged", OBJECT_PATH, None,
                             Gio.DBusSignalFlags.NONE, self._on_signal)
        Gio.bus_watch_name_on_connection(bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
                                         self._on_daemon_up, self._on_daemon_down)

    def _on_daemon_up(self, conn, name, owner):
        def done(conn, res):
            try:
                self.apply(json.loads(conn.call_finish(res).unpack()[0]))
            except GLib.Error as exc:
                print(f"discbay-island: GetState failed: {exc.message}", file=sys.stderr)
        conn.call(BUS_NAME, OBJECT_PATH, INTERFACE, "GetState", None, None,
                  Gio.DBusCallFlags.NONE, 3000, None, done)

    def _on_daemon_down(self, conn, name):
        self.state = None
        self.retract()

    def _on_signal(self, conn, sender, path, iface, signal_name, params):
        self.apply(json.loads(params.unpack()[0]))

    def _call(self, method: str) -> None:
        self.bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, method, None, None,
                      Gio.DBusCallFlags.NONE, 3000, None, None)

    # ------------------------------------------------------------- state -> view
    def apply(self, snap: dict) -> None:
        if snap.get("protocol", 0) > SUPPORTED_PROTOCOL:
            print("discbay-island: daemon speaks a newer protocol; please update the island",
                  file=sys.stderr)
        self.state = snap
        event, state = snap["event"], snap["state"]
        disc = snap.get("disc") or {}

        if event == "inserted" and not snap.get("burning"):
            play_sound(self.sound)

        if snap.get("burning"):
            # A burn is running (DiscBay Studio or discbay-ctl burn).
            self._cancel_hide()
            self.buttons.set_reveal_child(False)
            self.disc.progress = None
            self.disc.set_theme(None, None)
            self.title.set_label("Burning disc…")
            self.sub.set_label("Don't eject until it's done")
            self.sub.remove_css_class("warn")
            self.disc.target_speed = 18.0
            self.reveal()
            return
        if event == "busy":
            # Burn finished, or Studio opened/closed: don't pop up for that.
            self.retract()
            return

        self.disc.set_theme(disc.get("color"), disc.get("art"))
        self.disc.progress = None
        self.buttons.set_reveal_child(False)
        self._set_buttons("Cancel", "Start", "LaunchNow", cancel_visible=True)
        self.sub.remove_css_class("warn")
        self.sub.set_tooltip_text(None)
        self._last_whole_second = None
        self._cancel_hide()

        if state == "spinning":
            self.title.set_label("Reading disc…")
            self.sub.set_label("Inserted")
            self.disc.target_speed = 14.0
        elif state == "countdown":
            self.title.set_label(disc.get("name") or "Disc")
            self.buttons.set_reveal_child(True)
            self.disc.target_speed = 5.0
            self._update_countdown()
            self.start_btn.grab_focus()
        elif state == "untrusted":
            # A disc's own manifest wants to run something. Say exactly what,
            # and never focus the Trust button, so a stray Enter can't approve it.
            what = disc.get("launch") or "an app"
            self.title.set_label(disc.get("name") or "New disc")
            self.sub.set_label(f"New disc · wants to run {what}")
            self.sub.set_tooltip_text(f"This disc wants to run: {what}")
            self.sub.add_css_class("warn")
            self._set_buttons("Not now", "Trust & start", "Trust", cancel_visible=True)
            self.buttons.set_reveal_child(True)
            self.disc.target_speed = 2.0
            self.cancel_btn.grab_focus()
        elif state == "unknown":
            self.title.set_label(disc.get("label") or "Unknown disc")
            self.sub.set_label(snap.get("message") or "Unknown disc")
            self.disc.target_speed = 1.5
            self._hide_after(self.LINGER["unknown"])
        elif state == "running":
            self.title.set_label(disc.get("name") or "Disc")
            self.sub.set_label("Starting…")
            self.disc.target_speed = 3.0
            self._hide_after(self.LINGER["launched"])
        elif state == "idle":
            self.title.set_label(disc.get("name") or "Disc")
            if event == "cancelled":
                self.sub.set_label(snap.get("message") or "Cancelled")
                self._hide_after(self.LINGER["cancelled"])
            elif event == "identified":
                # Autostart off, or nothing to launch: offer Start only when it can work.
                self.sub.set_label(snap.get("message") or "Ready")
                if snap.get("can_launch"):
                    self._set_buttons("", "Start", "LaunchNow", cancel_visible=False)
                    self.buttons.set_reveal_child(True)
                self._hide_after(6.0)
            else:
                self._hide_after(0.0)
            self.disc.target_speed = 0.0
        elif state == "error":
            self.title.set_label(disc.get("name") or "Disc error")
            self.sub.set_label(snap.get("message") or "Something went wrong")
            self.sub.set_tooltip_text(snap.get("message"))
            self.sub.add_css_class("warn")
            if snap.get("can_launch"):
                self._set_buttons("", "Retry", "LaunchNow", cancel_visible=False)
                self.buttons.set_reveal_child(True)
            self.disc.target_speed = 0.0
            self._hide_after(self.LINGER["error"])
        elif state == "empty":
            self.disc.target_speed = 0.0
            if event == "ejected" and self.shown > 0:
                self.title.set_label("Disc ejected")
                self.sub.set_label("")
                self._hide_after(self.LINGER["ejected"])
            else:
                self.retract()
            return

        # Only pop up for things the user just did, not for a quiet startup sync.
        if event != "sync" or state in ("spinning", "countdown", "untrusted"):
            self.reveal()
        elif event == "sync":
            self.retract()

    def _set_buttons(self, cancel: str, primary: str, action: str, cancel_visible: bool) -> None:
        self.cancel_btn.set_label(cancel or "Cancel")
        self.cancel_btn.set_visible(cancel_visible)
        self.start_btn.set_label(primary)
        self.primary_action = action

    def _update_countdown(self) -> None:
        cd = (self.state or {}).get("countdown") or {}
        total, ends_at = cd.get("total"), cd.get("ends_at")
        if not total or ends_at is None:
            return
        remaining = max(0.0, ends_at - time.time())
        self.disc.progress = remaining / total
        whole = math.ceil(remaining)
        if whole != self._last_whole_second:
            self._last_whole_second = whole
            self.sub.set_label(f"Inserted · starting in {whole}")

    # ------------------------------------------------------------- show/hide
    def _measure_panels(self) -> None:
        """Find top panels on KDE and drop below them.

        Exclusive zone 0 only avoids panels that reserve space ("Always
        visible"); a Plasma panel set to dodge windows, or to let windows go
        over or under it, reserves nothing but still sits on screen. So on KDE
        we place ourselves: from KWin's live report when we have it (which
        knows when a panel has hidden), otherwise from plasmashell's list.
        """
        if not self.layer or self.over_panels or self.bus is None:
            return
        if self.kwin is not None and self.kwin.report is not None:
            self._place_on_active_output()
            return
        try:
            reply = self.bus.call_sync("org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell",
                                       "evaluateScript", GLib.Variant("(s)", (PLASMA_PANEL_SCRIPT,)),
                                       GLib.VariantType("(s)"), Gio.DBusCallFlags.NO_AUTO_START, 500, None)
        except GLib.Error:
            if self.plasma:   # plasmashell went away: fall back to the compositor's placement
                self.plasma, self.panel_offset = False, 0
                LayerShell.set_exclusive_zone(self, 0)
            return
        if not self.plasma:
            self.plasma = True
            LayerShell.set_exclusive_zone(self, -1)   # we measure from the very top ourselves
        self.panel_offset = top_panel_offset(reply.unpack()[0])

    def _place_on_active_output(self) -> None:
        """Open on the screen you're using, below that screen's panels only."""
        name = self.kwin.active_output()
        monitor = None
        if name:
            monitors = Gdk.Display.get_default().get_monitors()
            for i in range(monitors.get_n_items()):
                if monitors.get_item(i).get_connector() == name:
                    monitor = monitors.get_item(i)
        try:
            LayerShell.set_monitor(self, monitor)
        except TypeError:   # older bindings won't take None: leave the choice to KWin
            monitor = None
        self.output = name if monitor is not None else None
        if not self.plasma:
            self.plasma = True
            LayerShell.set_exclusive_zone(self, -1)   # we measure from the very top ourselves
        self.panel_offset = panel_bottom(self.kwin.report, self.output)
        self.offset_now = float(self.panel_offset)

    def _panels_changed(self) -> None:
        """KWin says a panel moved, hid or came back: glide to the new spot."""
        if self.kwin is None or self.kwin.report is None or self.over_panels:
            return
        if not self.plasma:
            self.plasma = True
            LayerShell.set_exclusive_zone(self, -1)
        self.panel_offset = panel_bottom(self.kwin.report, self.output)

    def reveal(self) -> None:
        if not self.get_visible():
            self._measure_panels()
            self.offset_now = float(self.panel_offset)
            self.shown = 0.0
            self._apply_offset()
            self.present()
        if self.shown < 1.0 and (self._anim is None or self._anim[4] != "in"):
            self._anim = (self.shown, 1.0, time.monotonic(), 0.48, "in")

    def retract(self) -> None:
        self._cancel_hide()
        if self.get_visible() and (self._anim is None or self._anim[4] != "out"):
            self._anim = (self.shown, 0.0, time.monotonic(), 0.26, "out")

    def _hide_after(self, seconds: float) -> None:
        self._cancel_hide()
        self._hide_timer = GLib.timeout_add(int(seconds * 1000), self._hide_now)

    def _hide_now(self):
        self._hide_timer = 0
        self.retract()
        return GLib.SOURCE_REMOVE

    def _cancel_hide(self) -> None:
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)
            self._hide_timer = 0

    def _apply_offset(self) -> None:
        height = max(self.pill.get_height(), 64) + PILL_MARGIN_TOP + PILL_MARGIN_BOTTOM
        if self.layer:
            # Slide the whole surface from above the screen edge into place.
            rest = self.offset_now + TOP_MARGIN
            LayerShell.set_margin(self, LayerShell.Edge.TOP, int(-height + (height + rest) * self.shown))
        # Without layer-shell we can't move the window, so the island just fades.
        self.pill.set_opacity(max(0.0, min(1.0, self.shown * 1.6)))

    def _update_input_region(self) -> None:
        """Only the pill takes clicks; the shadow margin around it lets them through."""
        surface = self.get_surface()
        ok, bounds = self.pill.compute_bounds(self)
        if surface is None or not ok:
            return
        box = (int(bounds.get_x()), int(bounds.get_y()),
               int(bounds.get_width()), int(bounds.get_height()))
        key = box + (self.get_width(), self.get_height())   # GTK may reset it when resizing
        if key != self._input_box:
            self._input_box = key
            surface.set_input_region(cairo.Region(cairo.RectangleInt(*box)))

    def _on_frame(self, widget, clock):
        now = time.monotonic()
        dt, self._last_frame = min(0.05, now - self._last_frame), now

        if abs(self.offset_now - self.panel_offset) > 0.5:
            self.offset_now += (self.panel_offset - self.offset_now) * min(1.0, dt * 12.0)
            if self._anim is None:
                self._apply_offset()
        elif self.offset_now != self.panel_offset:
            self.offset_now = float(self.panel_offset)
            if self._anim is None:
                self._apply_offset()
        self._update_input_region()

        if self._anim is not None:
            start_val, end_val, t0, dur, kind = self._anim
            t = min(1.0, (now - t0) / dur)
            eased = ease_out_back(t) if kind == "in" else ease_in_cubic(t)
            self.shown = start_val + (end_val - start_val) * eased
            self._apply_offset()
            if t >= 1.0:
                self._anim = None
                self.shown = end_val
                if kind == "out":
                    self.set_visible(False)

        if self.get_visible():
            self.disc.advance(dt)
            if self.state and self.state.get("state") == "countdown":
                self._update_countdown()
            self.disc.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _on_key(self, ctrl, keyval, keycode, mods):
        if not self.state:
            return False
        if keyval == Gdk.KEY_Escape:
            self._call("Cancel")
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.state.get("can_launch"):
            self._call("LaunchNow")
            return True
        return False


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="me.jolen.DiscBay.Island",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.hold()  # stay alive while the island is hidden
        # systemd stops us with SIGTERM: quit properly so the KWin script is unloaded.
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._quit)

    def _quit(self):
        self.quit()
        return GLib.SOURCE_REMOVE

    def do_shutdown(self):
        island = getattr(self, "island", None)
        if island is not None and island.kwin is not None:
            island.kwin.unload()
        Gtk.Application.do_shutdown(self)

    def do_activate(self):
        if getattr(self, "island", None):
            return
        self.island = Island(self)
        self.island.sound = Path(os.path.expanduser(os.environ["DISCBAY_SOUND"])) \
            if os.environ.get("DISCBAY_SOUND") else _insert_sound_path()
        if not self.island.layer:
            print("discbay-island: layer-shell not available, using a normal window "
                  "(placement depends on your desktop)", file=sys.stderr)
        self.island.connect_daemon(Gio.bus_get_sync(Gio.BusType.SESSION, None))


def main() -> int:
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
