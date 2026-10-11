import io
import os
import stat
import tempfile
import unittest

from discbay import drive, protocol as P
from discbay.config import ConfigError, parse, parse_manifest
from discbay.engine import Engine
from discbay.launch import LaunchError, LaunchSpec, parse_launch, strip_field_codes
from discbay.trust import MemoryTrust

CONFIG = """
[settings]
countdown = 5

[discs.HOLLOW_01]
name = "Hollow Knight"
steam = 367520
color = "#4a6cff"

[discs.EDITOR]
name = "Text Editor"
app = "org.kde.kate"
on_eject = "close"

[discs.MANUAL]
name = "Manual only"
command = "foo"
autostart = false
"""

MANIFEST = b'''
name = "Krita"
subtitle = "Paint"
color = "#3daee9"
art = "COVER.PNG"
app = "org.kde.krita"
'''


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeProc:
    def __init__(self, pid, detached):
        self.pid = pid
        self.detached = detached
        self.code = None

    def poll(self):
        return self.code


class FakeLauncher:
    def __init__(self, fail=None):
        self.started, self.stopped, self.ran = [], [], []
        self.fail = fail

    def start(self, spec):
        if self.fail:
            raise LaunchError(self.fail)
        proc = FakeProc(4000 + len(self.started), spec.detached)
        self.started.append((spec, proc))
        return proc

    def stop(self, proc):
        self.stopped.append(proc)

    def run(self, command):
        self.ran.append(command)


def make(config=CONFIG, trust=None, fail=None):
    clock, launcher, events = FakeClock(), FakeLauncher(fail), []
    engine = Engine(parse(config), launcher, events.append, clock, trust or MemoryTrust())
    return engine, clock, launcher, events


def insert(engine, label, kind="data", manifest=None):
    engine.drive_update(drive.SPINNING)
    engine.drive_update(drive.READY, drive.DiscInfo(kind=kind, label=label, manifest=manifest))


class EngineTests(unittest.TestCase):
    def test_beep_event_fires_before_data_is_readable(self):
        engine, _, _, events = make()
        engine.drive_update(drive.SPINNING)
        self.assertEqual(events[-1]["event"], P.EV_INSERTED)
        self.assertEqual(events[-1]["state"], P.SPINNING)
        self.assertIsNone(events[-1]["disc"])

    def test_fast_drive_skipping_spinning_still_emits_insert(self):
        engine, _, _, events = make()
        engine.drive_update(drive.READY, drive.DiscInfo("data", "HOLLOW_01"))
        self.assertEqual([e["event"] for e in events], [P.EV_INSERTED, P.EV_IDENTIFIED])

    def test_countdown_then_autostart(self):
        engine, clock, launcher, events = make()
        insert(engine, "HOLLOW_01")
        snap = events[-1]
        self.assertEqual(snap["state"], P.COUNTDOWN)
        self.assertEqual(snap["disc"]["name"], "Hollow Knight")
        self.assertEqual(snap["countdown"]["total"], 5)
        self.assertNotIn("launch", snap["disc"])  # local discs never expose their target

        clock.t += 4.9
        engine.tick()
        self.assertEqual(launcher.started, [])
        clock.t += 0.2
        engine.tick()
        self.assertEqual(launcher.started[0][0].to_argv(), ["steam", "steam://rungameid/367520"])
        self.assertEqual(events[-1]["event"], P.EV_LAUNCHED)

    def test_cancel_stops_launch(self):
        engine, clock, launcher, events = make()
        insert(engine, "HOLLOW_01")
        self.assertTrue(engine.cancel())
        self.assertEqual(events[-1]["state"], P.IDLE)
        clock.t += 10
        engine.tick()
        self.assertEqual(launcher.started, [])
        self.assertTrue(engine.launch_now())  # can still start it by hand
        self.assertEqual(len(launcher.started), 1)

    def test_label_match_is_case_insensitive(self):
        engine, _, _, events = make()
        insert(engine, "hollow_01")
        self.assertEqual(events[-1]["state"], P.COUNTDOWN)

    def test_unknown_disc(self):
        engine, _, _, events = make()
        insert(engine, "MYSTERY")
        self.assertEqual(events[-1]["state"], P.UNKNOWN)
        self.assertEqual(events[-1]["disc"]["label"], "MYSTERY")

    def test_blank_disc_says_so(self):
        engine, _, _, events = make()
        insert(engine, None, kind="blank")
        self.assertEqual(events[-1]["state"], P.UNKNOWN)
        self.assertEqual(events[-1]["message"], "Blank disc")

    def test_registering_unknown_disc_without_reinsert(self):
        engine, _, _, events = make()
        insert(engine, "MYSTERY")
        engine.reload(parse(CONFIG + '\n[discs.MYSTERY]\nname = "Found it"\ncommand = "x"\n'))
        self.assertEqual(events[-1]["state"], P.IDLE)
        self.assertEqual(events[-1]["disc"]["name"], "Found it")
        self.assertTrue(events[-1]["can_launch"])

    def test_autostart_off(self):
        engine, clock, launcher, events = make()
        insert(engine, "MANUAL")
        self.assertEqual(events[-1]["state"], P.IDLE)
        clock.t += 60
        engine.tick()
        self.assertEqual(launcher.started, [])

    def test_steam_is_detached_so_it_stays_running(self):
        engine, _, launcher, events = make()
        insert(engine, "HOLLOW_01")
        engine.launch_now()
        launcher.started[0][1].code = 0  # steam:// handoff exits immediately
        engine.tick()
        self.assertEqual(events[-1]["state"], P.RUNNING)

    def test_tracked_app_exit_returns_to_idle(self):
        engine, _, launcher, events = make()
        insert(engine, "EDITOR")
        engine.launch_now()
        launcher.started[0][1].code = 0
        engine.tick()
        self.assertEqual(events[-1]["event"], P.EV_APP_EXITED)
        self.assertEqual(events[-1]["state"], P.IDLE)

    def test_eject_closes_app_when_configured(self):
        engine, _, launcher, events = make()
        insert(engine, "EDITOR")
        engine.launch_now()
        engine.drive_update(drive.ABSENT)
        self.assertEqual(len(launcher.stopped), 1)
        self.assertEqual(events[-1]["event"], P.EV_EJECTED)
        self.assertEqual(events[-1]["state"], P.EMPTY)

    def test_eject_during_countdown(self):
        engine, clock, launcher, events = make()
        insert(engine, "HOLLOW_01")
        engine.drive_update(drive.ABSENT)
        clock.t += 10
        engine.tick()
        self.assertEqual(launcher.started, [])
        self.assertEqual(events[-1]["state"], P.EMPTY)

    def test_unreadable_disc_times_out(self):
        engine, clock, _, events = make()
        engine.drive_update(drive.SPINNING)
        clock.t += 41
        engine.tick()
        self.assertEqual(events[-1]["state"], P.ERROR)

    def test_launch_failure_shows_error_and_allows_retry(self):
        engine, _, launcher, events = make(fail="App 'org.kde.kate' is not installed")
        insert(engine, "EDITOR")
        engine.launch_now()
        self.assertEqual(events[-1]["state"], P.ERROR)
        self.assertIn("not installed", events[-1]["message"])
        self.assertTrue(events[-1]["can_launch"])

    def test_seq_increments(self):
        engine, _, _, events = make()
        insert(engine, "HOLLOW_01")
        self.assertEqual([e["seq"] for e in events], [1, 2])


class ManifestTests(unittest.TestCase):
    def manifest(self, raw=MANIFEST, label="KRITA_01"):
        return parse_manifest(raw, label)

    def test_new_disc_asks_for_trust_and_shows_what_it_runs(self):
        engine, clock, launcher, events = make()
        insert(engine, "KRITA_01", manifest=self.manifest())
        snap = events[-1]
        self.assertEqual(snap["state"], P.UNTRUSTED)
        self.assertEqual(snap["disc"]["name"], "Krita")
        self.assertEqual(snap["disc"]["source"], "disc")
        self.assertEqual(snap["disc"]["launch"], "app org.kde.krita")
        self.assertFalse(snap["can_launch"])
        clock.t += 60
        engine.tick()
        self.assertEqual(launcher.started, [])  # never autostarts without trust

    def test_trust_launches_and_is_remembered(self):
        trust = MemoryTrust()
        engine, _, launcher, events = make(trust=trust)
        insert(engine, "KRITA_01", manifest=self.manifest())
        self.assertTrue(engine.trust())
        self.assertEqual(events[-1]["event"], P.EV_LAUNCHED)
        self.assertEqual(launcher.started[0][0].value, "org.kde.krita")

        engine.drive_update(drive.ABSENT)
        insert(engine, "KRITA_01", manifest=self.manifest())
        self.assertEqual(events[-1]["state"], P.COUNTDOWN)  # second time: no question

    def test_changed_manifest_asks_again(self):
        trust = MemoryTrust()
        engine, _, _, events = make(trust=trust)
        insert(engine, "KRITA_01", manifest=self.manifest())
        engine.trust()
        engine.drive_update(drive.ABSENT)
        evil = MANIFEST.replace(b'app = "org.kde.krita"', b'command = "rm -rf ~"')
        insert(engine, "KRITA_01", manifest=self.manifest(evil))
        self.assertEqual(events[-1]["state"], P.UNTRUSTED)
        self.assertEqual(events[-1]["disc"]["launch"], "rm -rf ~")

    def test_declining_blocks_launch(self):
        engine, _, launcher, events = make()
        insert(engine, "KRITA_01", manifest=self.manifest())
        self.assertTrue(engine.cancel())
        self.assertEqual(events[-1]["state"], P.IDLE)
        self.assertFalse(engine.launch_now())
        self.assertEqual(launcher.started, [])

    def test_local_entry_wins_over_manifest(self):
        engine, _, _, events = make()
        insert(engine, "EDITOR", manifest=self.manifest(label="EDITOR"))
        self.assertEqual(events[-1]["state"], P.COUNTDOWN)
        self.assertEqual(events[-1]["disc"]["name"], "Text Editor")
        self.assertEqual(events[-1]["disc"]["source"], "local")

    def test_local_entry_can_tweak_one_setting_of_a_burned_disc(self):
        trust = MemoryTrust()
        manifest = self.manifest()
        trust.add(manifest.fingerprint, "KRITA_01", "Krita", "app org.kde.krita")
        engine, _, launcher, events = make(CONFIG + '\n[discs.KRITA_01]\non_eject = "close"\n', trust=trust)
        insert(engine, "KRITA_01", manifest=manifest)
        snap = events[-1]
        self.assertEqual(snap["state"], P.COUNTDOWN)      # still trusted, still the disc's target
        self.assertEqual(snap["disc"]["name"], "Krita")    # name and colour kept from the disc
        self.assertEqual(snap["disc"]["color"], "#3daee9")
        engine.launch_now()
        self.assertEqual(launcher.started[0][0].value, "org.kde.krita")
        engine.drive_update(drive.ABSENT)
        self.assertEqual(len(launcher.stopped), 1)         # the local tweak took effect

    def test_local_tweak_does_not_bypass_trust(self):
        engine, _, _, events = make(CONFIG + '\n[discs.KRITA_01]\ncolor = "#ff0000"\n')
        insert(engine, "KRITA_01", manifest=self.manifest())
        self.assertEqual(events[-1]["state"], P.UNTRUSTED)
        self.assertEqual(events[-1]["disc"]["color"], "#ff0000")

    def test_local_launch_target_replaces_the_discs_and_is_trusted(self):
        engine, _, _, events = make(CONFIG + '\n[discs.KRITA_01]\napp = "org.gimp.GIMP"\n')
        insert(engine, "KRITA_01", manifest=self.manifest())
        self.assertEqual(events[-1]["state"], P.COUNTDOWN)
        self.assertEqual(events[-1]["disc"]["source"], "local")
        self.assertEqual(events[-1]["disc"]["name"], "Krita")

    def test_reload_applies_to_disc_already_in_the_drive(self):
        engine, _, launcher, events = make(trust=MemoryTrust())
        insert(engine, "KRITA_01", manifest=self.manifest())
        engine.trust()
        engine.reload(parse(CONFIG + '\n[discs.KRITA_01]\non_eject = "close"\n'))
        self.assertEqual(events[-1]["state"], P.RUNNING)  # the game keeps running
        engine.drive_update(drive.ABSENT)
        self.assertEqual(len(launcher.stopped), 1)

    def test_policy_never(self):
        engine, _, _, events = make('[settings]\ndisc_manifests = "never"\n')
        insert(engine, "KRITA_01", manifest=self.manifest())
        self.assertEqual(events[-1]["state"], P.IDLE)
        self.assertEqual(events[-1]["disc"]["name"], "Krita")  # still themed
        self.assertFalse(engine.trust())
        self.assertFalse(engine.launch_now())

    def test_policy_always(self):
        engine, _, _, events = make('[settings]\ndisc_manifests = "always"\n')
        insert(engine, "KRITA_01", manifest=self.manifest())
        self.assertEqual(events[-1]["state"], P.COUNTDOWN)

    def test_manifest_without_target_is_just_themed(self):
        engine, _, _, events = make()
        insert(engine, "PHOTOS", manifest=parse_manifest(b'name = "Holiday 2026"\n', "PHOTOS"))
        self.assertEqual(events[-1]["state"], P.IDLE)
        self.assertEqual(events[-1]["disc"]["name"], "Holiday 2026")

    def test_disc_cannot_set_close_command(self):
        disc = parse_manifest(b'name="x"\napp="a"\nclose_command="rm -rf ~"\n', "X")
        self.assertIsNone(disc.close_command)

    def test_invalid_manifest_is_an_error(self):
        engine, _, _, events = make()
        insert_info = drive.DiscInfo(kind="data", label="BAD", manifest_error="Disc manifest is not valid TOML")
        engine.drive_update(drive.READY, insert_info)
        self.assertEqual(events[-1]["state"], P.ERROR)

    def test_fingerprint_depends_on_label_and_content(self):
        a = parse_manifest(MANIFEST, "A").fingerprint
        self.assertEqual(a, parse_manifest(MANIFEST, "A").fingerprint)
        self.assertNotEqual(a, parse_manifest(MANIFEST, "B").fingerprint)


class HoldTests(unittest.TestCase):
    def test_held_disc_does_not_autostart_or_prompt(self):
        engine, clock, launcher, events = make()
        engine.set_hold(10, exclusive=False)
        self.assertEqual(events[-1]["event"], P.EV_BUSY)
        self.assertTrue(events[-1]["held"])
        insert(engine, "HOLLOW_01")
        self.assertEqual(events[-1]["state"], P.IDLE)
        clock.t += 6
        engine.tick()
        self.assertEqual(launcher.started, [])
        engine.drive_update(drive.ABSENT)
        insert(engine, "KRITA_01", manifest=parse_manifest(MANIFEST, "KRITA_01"))
        self.assertEqual(events[-1]["state"], P.IDLE)  # no trust prompt while Studio is open

    def test_release_makes_disc_usable_without_reinserting(self):
        engine, clock, launcher, events = make()
        engine.set_hold(10, exclusive=False)
        insert(engine, "KRITA_01", manifest=parse_manifest(MANIFEST, "KRITA_01"))
        clock.t += 11                      # lease expires on its own
        engine.tick()
        self.assertEqual(events[-1]["state"], P.UNTRUSTED)
        engine.drive_update(drive.ABSENT)
        engine.set_hold(10, exclusive=False)
        insert(engine, "HOLLOW_01")
        engine.set_hold(0, exclusive=False)  # released early
        self.assertEqual(events[-1]["state"], P.IDLE)
        self.assertTrue(events[-1]["can_launch"])  # offered, not surprise-launched
        self.assertEqual(launcher.started, [])

    def test_plain_hold_does_not_shorten_burn_lease(self):
        engine, clock, _, events = make()
        engine.set_hold(15, exclusive=True)
        engine.set_hold(2, exclusive=False)
        clock.t += 5
        engine.tick()
        self.assertTrue(engine.busy)
        self.assertTrue(events[-1]["burning"])
        engine.set_hold(0, exclusive=True)
        self.assertFalse(engine.busy)
        self.assertFalse(engine.launch_now())

    def test_disc_rewritten_in_drive_counts_as_new_disc(self):
        engine, _, _, events = make()
        insert(engine, "HOLLOW_01")
        engine.cancel()
        engine.drive_update(drive.READY, drive.DiscInfo("data", "EDITOR"))
        self.assertEqual([e["event"] for e in events[-3:]], [P.EV_EJECTED, P.EV_INSERTED, P.EV_IDENTIFIED])
        self.assertEqual(events[-1]["disc"]["name"], "Text Editor")


class LaunchTests(unittest.TestCase):
    def test_one_target_only(self):
        with self.assertRaises(ValueError):
            parse_launch({"app": "a", "exec": "/bin/true"}, "x")

    def test_steam_needs_numeric_id(self):
        with self.assertRaises(ValueError):
            parse_launch({"steam": "hollow"}, "x")
        self.assertEqual(parse_launch({"steam": 367520}, "x").value, "367520")

    def test_args_only_for_app_or_exec(self):
        with self.assertRaises(ValueError):
            parse_launch({"url": "https://x", "args": ["a"]}, "x")

    def test_exec_checks_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Game.AppImage")
            with open(path, "w") as f:
                f.write("#!/bin/sh\n")
            spec = LaunchSpec("exec", path, ("--fullscreen",))
            with self.assertRaises(LaunchError):
                spec.to_argv()  # not executable yet
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
            self.assertEqual(spec.to_argv(), [path, "--fullscreen"])
        with self.assertRaises(LaunchError):
            LaunchSpec("exec", "/nope/missing").to_argv()

    def test_desktop_field_codes_are_stripped(self):
        self.assertEqual(strip_field_codes("krita %F"), ["krita"])
        self.assertEqual(strip_field_codes('/usr/bin/flatpak run --file-forwarding org.x.Y @@ %F @@'),
                         ["/usr/bin/flatpak", "run", "--file-forwarding", "org.x.Y", "@@", "@@"])
        self.assertEqual(strip_field_codes("app --name=%c --pct=100%%"), ["app", "--name=", "--pct=100%"])

    def test_detached_kinds(self):
        self.assertTrue(LaunchSpec("steam", "1").detached)
        self.assertTrue(LaunchSpec("url", "https://x").detached)
        self.assertTrue(LaunchSpec("command", "steam steam://rungameid/1").detached)
        self.assertFalse(LaunchSpec("app", "org.kde.kate").detached)
        self.assertFalse(LaunchSpec("command", "steam -x", detached_override=False).detached)


def live_members(pgid):
    """PIDs in a process group that are not zombies (grandchild zombies are init's to reap)."""
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if int(fields[2]) == pgid and fields[0] != "Z":
            pids.append(int(entry))
    return pids


class LauncherTests(unittest.TestCase):
    def test_stop_kills_group_and_leaves_no_zombies(self):
        from discbay.engine import SubprocessLauncher
        launcher = SubprocessLauncher()
        launcher.KILL_GRACE = 0.2
        # A child that ignores SIGTERM, with a grandchild in the same group.
        proc = launcher.start(LaunchSpec("command", "sh -c 'trap \"\" TERM; sleep 30 & wait'"))
        import time
        time.sleep(0.2)
        launcher.stop(proc)
        deadline = time.monotonic() + 3
        while launcher._children and time.monotonic() < deadline:
            launcher.reap()
            time.sleep(0.05)
        self.assertEqual(launcher._children, [])
        self.assertIsNotNone(proc.returncode)
        self.assertEqual(live_members(proc.pid), [])  # nothing in the group is still running


class FlatpakTests(unittest.TestCase):
    def test_app_id_from_flatpak_command_line(self):
        from discbay.engine import flatpak_app_id
        self.assertEqual(flatpak_app_id(["/usr/bin/flatpak", "run", "--branch=stable", "--arch=x86_64",
                                         "--command=openttd", "org.openttd.OpenTTD", "-x"]),
                         "org.openttd.OpenTTD")
        self.assertIsNone(flatpak_app_id(["openttd"]))
        self.assertIsNone(flatpak_app_id(["flatpak", "list"]))


class ConfigTests(unittest.TestCase):
    def test_bad_policy(self):
        with self.assertRaises(ConfigError):
            parse('[settings]\ndisc_manifests = "maybe"\n')

    def test_legacy_command_still_works(self):
        disc = parse('[discs.X]\nname="x"\ncommand="kate"\n').discs["X"]
        self.assertEqual(disc.launch.to_argv(), ["kate"])


class IsoTests(unittest.TestCase):
    def test_parse_label(self):
        pvd = bytearray(2048)
        pvd[0] = 1
        pvd[1:6] = b"CD001"
        pvd[40:72] = b"HOLLOW_01".ljust(32)
        self.assertEqual(drive.parse_iso_label(bytes(pvd)), "HOLLOW_01")

    def test_media_state_reads_udev_database(self):
        from unittest import mock
        fake_db = "S:cdrom\nE:ID_CDROM_MEDIA=1\nE:ID_CDROM_MEDIA_STATE=blank\nE:ID_CDROM_MEDIA_CD_R=1\n"
        fake_stat = mock.Mock(st_rdev=os.makedev(11, 0))
        with mock.patch("os.stat", return_value=fake_stat), \
             mock.patch("builtins.open", mock.mock_open(read_data=fake_db)) as opened:
            self.assertEqual(drive.media_state("/dev/sr0"), "blank")
        opened.assert_called_once_with("/run/udev/data/b11:0", encoding="utf-8", errors="replace")
        self.assertIsNone(drive.media_state("/nonexistent/device"))

    def test_not_iso(self):
        self.assertIsNone(drive.parse_iso_label(bytes(2048)))

    def test_read_root_file_from_real_image(self):
        try:
            import pycdlib
        except ImportError:
            self.skipTest("pycdlib not installed")
        art = b"\x89PNG fake image bytes"
        with tempfile.TemporaryDirectory() as tmp:
            for level, name in ((1, "/DISCBAY.TOM;1"), (3, "/DISCBAY.TOML;1")):
                path = os.path.join(tmp, f"disc{level}.iso")
                iso = pycdlib.PyCdlib()
                iso.new(vol_ident="KRITA_01", interchange_level=level, joliet=3)
                iso.add_fp(io.BytesIO(MANIFEST), len(MANIFEST), name, joliet_path="/discbay.toml")
                iso.add_fp(io.BytesIO(art), len(art), "/COVER.PNG;1", joliet_path="/cover.png")
                iso.write(path)
                iso.close()
                self.assertEqual(drive.read_label(path), "KRITA_01")
                self.assertEqual(drive.read_root_file(path, "discbay.toml"), MANIFEST, name)
                self.assertEqual(drive.read_root_file(path, "COVER.PNG"), art)
                self.assertIsNone(drive.read_root_file(path, "missing.txt"))


class MultisessionTests(unittest.TestCase):
    def setUp(self):
        import shutil
        if not shutil.which("xorriso"):
            self.skipTest("xorriso not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *cmd):
        import subprocess
        subprocess.run(cmd, check=True, capture_output=True)

    def test_reads_newest_session_and_inherits_old_files(self):
        """Lay out a real two-session CD the way a burner does, then read it."""
        v1, v2 = os.path.join(self.dir, "v1"), os.path.join(self.dir, "v2")
        os.makedirs(v1)
        os.makedirs(v2)
        with open(os.path.join(v1, "DISCBAY.TOML"), "w") as f:
            f.write('name = "Old"\napp = "org.old.App"\n')
        with open(os.path.join(v1, "COVER.PNG"), "wb") as f:
            f.write(b"cover" * 100)
        with open(os.path.join(v2, "DISCBAY.TOML"), "w") as f:
            f.write('name = "New"\napp = "org.new.App"\n')
        s1, s2 = os.path.join(self.dir, "s1.iso"), os.path.join(self.dir, "s2.iso")
        self._run("xorriso", "-as", "mkisofs", "-quiet", "-V", "DISC_V1", "-R", "-J", "-o", s1, v1)
        next_lba = os.path.getsize(s1) // 2048 + 6900   # gap like a CD's lead-out + lead-in
        self._run("xorriso", "-indev", s1, "-outdev", f"stdio:{s2}", "-grow_blindly", str(next_lba),
                  "-volid", "DISC_V2", "-map", os.path.join(v2, "DISCBAY.TOML"), "/DISCBAY.TOML", "-commit")
        disc = os.path.join(self.dir, "disc.img")
        with open(disc, "wb") as out:
            out.write(open(s1, "rb").read().ljust(next_lba * 2048, b"\0"))
            out.write(open(s2, "rb").read())

        self.assertEqual(drive.read_label(disc, session_lba=0), "DISC_V1")
        self.assertEqual(drive.read_label(disc, session_lba=next_lba), "DISC_V2")
        self.assertIn(b"org.new.App", drive.read_root_file(disc, "discbay.toml", session_lba=next_lba))
        self.assertEqual(drive.read_root_file(disc, "COVER.PNG", session_lba=next_lba), b"cover" * 100)

    def test_multisession_ioctl_struct_matches_kernel_layout(self):
        import struct
        # union cdrom_addr (4) + xa_flag (1) + addr_format (1), padded to int alignment
        self.assertEqual(struct.calcsize("=iBB2x"), 8)
        self.assertEqual(drive.last_session_lba(os.path.join(self.dir, "missing")), 0)

    def test_burn_then_rewrite(self):
        import subprocess
        import sys
        img = os.path.join(self.dir, "disc.img")
        art = os.path.join(self.dir, "a.png")
        with open(art, "wb") as f:
            f.write(b"\x89PNG fake")
        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        def burn(*args):
            return subprocess.run([sys.executable, "-m", "discbay.ctl", "burn", "--device", img, *args],
                                  capture_output=True, text=True, env=env)

        first = burn("--label", "GAME_01", "--name", "First", "--art", art, "--command", "true")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = burn("--label", "GAME_02", "--name", "Second", "--command", "false", "--close-on-eject")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

        self.assertEqual(drive.read_label(img), "GAME_02")
        disc = parse_manifest(drive.read_root_file(img, "discbay.toml"), "GAME_02")
        self.assertEqual((disc.name, disc.launch.value, disc.on_eject), ("Second", "false", "close"))
        self.assertNotIn("COVER.PNG", drive.root_names(img))  # art from the old version removed


if __name__ == "__main__":
    unittest.main()
