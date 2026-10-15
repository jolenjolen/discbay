"""DiscBay Studio: a local web app for making launcher discs.

    discbay-studio            serves http://localhost:8404

It only listens on this computer (127.0.0.1), rejects requests from other
websites, and uses the same disc-making code as `discbay-ctl burn`.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, after_this_request, jsonify, request, send_file, send_from_directory

from . import catalog, client, config as cfg, discmaker, drive

STATIC = Path(__file__).with_name("studio_static")
DEFAULT_PORT = 8404
MAX_COVER = 8 * 1024 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,200}$")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["DEVICE"] = "/dev/sr0"
app.config["PORT"] = DEFAULT_PORT


# ---------------------------------------------------------------- safety
@app.before_request
def only_this_computer():
    """Block DNS-rebinding and cross-site requests: this page can burn discs."""
    port = app.config["PORT"]
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if request.host not in allowed:
        abort(403)
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] not in allowed:
            abort(403)
        # A custom header can't be sent cross-site without CORS, which we never allow.
        if request.headers.get("X-DiscBay-Studio") != "1":
            abort(403)


@app.after_request
def no_framing(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# ---------------------------------------------------------------- pages
@app.get("/")
def index():
    if not (STATIC / "index.html").is_file():
        # Usually the studio_static folder wasn't next to studio.py when installing.
        return (f"<h1>DiscBay Studio's page files are missing</h1>"
                f"<p>Expected them in <code>{STATIC}</code>.</p>"
                "<p>Check that the project has a <code>discbay/studio_static/</code> folder containing "
                "index.html, studio.css and studio.js, then run <code>./install.sh</code> again.</p>"), 500
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:name>")
def static_file(name):
    return send_from_directory(STATIC, name)


# ---------------------------------------------------------------- catalog
@app.get("/api/apps")
def apps():
    return jsonify(catalog.list_apps())


@app.get("/api/steam")
def steam():
    return jsonify(catalog.list_steam_games())


@app.get("/api/appimages")
def appimages():
    return jsonify(catalog.list_appimages())


@app.get("/api/icon/<app_id>")
def icon(app_id):
    if not ID_RE.match(app_id):
        abort(404)
    path = catalog.app_icon(app_id)
    if not path:
        abort(404)
    resp = send_file(path, max_age=3600)
    if path.endswith(".svg"):
        resp.headers["Content-Security-Policy"] = "script-src 'none'"
    return resp


@app.get("/api/steam/<appid>/art")
def steam_art(appid):
    path = catalog.steam_art(appid)
    if not path:
        abort(404)
    return send_file(path, max_age=3600)


# ---------------------------------------------------------------- drive status
_space_cache: dict = {"key": None, "free": None, "busy": False}
_space_lock = threading.Lock()


def _refresh_space(device: str, key) -> None:
    free = discmaker.media_space(device)
    with _space_lock:
        if _space_cache["key"] == key:
            _space_cache["free"] = free
        _space_cache["busy"] = False


@app.get("/api/status")
def status():
    device = app.config["DEVICE"]
    # While a Studio tab is open, ask the daemon not to autostart discs:
    # you may be about to rewrite the one you just inserted.
    client.hold(12, exclusive=False)

    if JOBS.running():
        return jsonify({"device": device, "media": {"state": "burning", "present": True, "writable": False},
                        "space": None, "daemon": client.state() is not None})

    media = discmaker.describe_media(device)
    current = None
    if media.get("discbay"):
        raw = drive.read_root_file(device, "discbay.toml")
        try:
            current = cfg.parse_manifest(raw, media.get("label")).name if raw else None
        except cfg.ConfigError:
            current = None

    key = (media.get("state"), media.get("label"))
    with _space_lock:
        if _space_cache["key"] != key:
            _space_cache.update(key=key, free=None, busy=False)
        if media.get("writable") and _space_cache["free"] is None and not _space_cache["busy"]:
            _space_cache["busy"] = True
            threading.Thread(target=_refresh_space, args=(device, key), daemon=True).start()
        free = _space_cache["free"]

    rewrites = None
    if free is not None:
        rewrites = max(0, free // (discmaker.SESSION_OVERHEAD + 1024 * 1024))
    return jsonify({"device": device, "media": media, "current": current,
                    "space": {"free": free, "rewrites": rewrites} if free is not None else None,
                    "daemon": client.state() is not None})


# ---------------------------------------------------------------- disc specs
def _spec_from_json(data: dict, art_path: str | None) -> discmaker.DiscSpec:
    try:
        countdown = data.get("countdown")
        return discmaker.DiscSpec(
            label=str(data.get("label") or discmaker.label_from_name(str(data.get("name", "")))),
            name=str(data.get("name", "")).strip(),
            target_kind=str(data.get("kind", "")),
            target=str(data.get("target", "")).strip(),
            args=[str(a) for a in data.get("args", []) if str(a)],
            subtitle=(str(data["subtitle"]).strip() or None) if data.get("subtitle") else None,
            color=str(data["color"]) if data.get("color") else None,
            art_path=art_path,
            countdown=float(countdown) if countdown not in (None, "") else None,
            close_on_eject=bool(data.get("close_on_eject")),
            autostart=bool(data.get("autostart", True)),
            window=str(data["window"]) if data.get("window") else None,
        )
    except (TypeError, ValueError) as exc:
        raise discmaker.DiscError(f"Invalid disc details: {exc}") from exc


def _save_cover(data: dict, folder: str) -> str | None:
    """Decode the cropped cover the page sends as a data: URL."""
    url = data.get("cover")
    if not url:
        return None
    match = re.match(r"^data:image/(png|jpeg);base64,(.+)$", url, re.S)
    if not match:
        raise discmaker.DiscError("The cover must be a PNG or JPEG image")
    raw = base64.b64decode(match.group(2), validate=False)
    if len(raw) > MAX_COVER:
        raise discmaker.DiscError("The cover image is too large")
    if not (raw.startswith(b"\x89PNG") or raw.startswith(b"\xff\xd8")):
        raise discmaker.DiscError("The cover isn't a valid image")
    path = os.path.join(folder, "cover.png" if match.group(1) == "png" else "cover.jpg")
    with open(path, "wb") as f:
        f.write(raw)
    return path


@app.post("/api/check")
def check():
    """Validate the form and show what the disc will contain."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        spec = _spec_from_json(data, "cover.png" if data.get("has_cover") else None)
        spec.art_path = None  # existence checked at burn time
        disc, manifest, warnings = discmaker.prepare(spec)
    except discmaker.DiscError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    return jsonify({"ok": True, "label": spec.label, "warnings": warnings,
                    "launch": disc.launch.describe() if disc.launch else None, "manifest": manifest})


@app.post("/api/iso")
def make_iso():
    data = request.get_json(force=True, silent=True) or {}
    tmp = tempfile.mkdtemp(prefix="discbay-studio-")
    try:
        spec = _spec_from_json(data, _save_cover(data, tmp))
        out = Path(tmp) / f"{spec.label.lower()}.iso"
        discmaker.make_iso(spec, out)
    except discmaker.DiscError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    @after_this_request
    def cleanup(resp):
        threading.Timer(30, lambda: shutil.rmtree(tmp, ignore_errors=True)).start()
        return resp

    return send_file(out, as_attachment=True, download_name=out.name, mimetype="application/x-iso9660-image")


# ---------------------------------------------------------------- burn jobs
class Jobs:
    PHASES = (
        (re.compile(r"Beginning to write"), "Writing your disc"),
        (re.compile(r"Working since"), "Finishing the session"),
        (re.compile(r"completed successfully"), "Checking the disc"),
        (re.compile(r"[Ee]ject"), "Ejecting"),
    )

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}

    def running(self) -> bool:
        with self.lock:
            return any(j["state"] == "running" for j in self.jobs.values())

    def start(self, spec: discmaker.DiscSpec, folder: str, device: str, final: bool) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "state": "running", "phase": "Preparing", "started": time.time(),
               "log": [], "result": None, "error": None}
        with self.lock:
            if any(j["state"] == "running" for j in self.jobs.values()):
                raise discmaker.DiscError("A burn is already running")
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job, spec, folder, device, final), daemon=True).start()
        return job_id

    def _line(self, job: dict, line: str) -> None:
        job["log"] = (job["log"] + [line])[-60:]
        for pattern, phase in self.PHASES:
            if pattern.search(line):
                job["phase"] = phase

    def _run(self, job, spec, folder, device, final):
        try:
            with client.hold_drive(exclusive=True):
                result = discmaker.burn(spec, device, final=final, on_line=lambda l: self._line(job, l))
            job["result"] = {
                "launch": result.disc.launch.describe() if result.disc.launch else None,
                "updated": result.updated, "final": result.final,
                "free": result.free_bytes, "rewrites": result.rewrites_left, "warnings": result.warnings,
                "ejected": not discmaker._is_image(device),
            }
            job["state"] = "done"
        except discmaker.DiscError as exc:
            job["error"], job["state"] = str(exc), "failed"
        except Exception as exc:  # report, don't crash the server
            job["error"], job["state"] = f"Unexpected error: {exc}", "failed"
        finally:
            job["finished"] = time.time()
            shutil.rmtree(folder, ignore_errors=True)
            with _space_lock:
                _space_cache.update(key=None, free=None, busy=False)

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            data = dict(job)
        data["elapsed"] = (job.get("finished") or time.time()) - job["started"]
        return data


JOBS = Jobs()


@app.post("/api/burn")
def burn():
    data = request.get_json(force=True, silent=True) or {}
    folder = tempfile.mkdtemp(prefix="discbay-studio-")
    try:
        spec = _spec_from_json(data, _save_cover(data, folder))
        discmaker.prepare(spec)  # fail fast, before touching the drive
        media = discmaker.describe_media(app.config["DEVICE"])
        if not media.get("writable"):
            raise discmaker.DiscError({
                "complete": "This disc is closed, so it can't be written again. Insert a blank CD-R or a CD-RW.",
                "empty": "Insert a blank CD-R, or a DiscBay disc to rewrite.",
                "spinning": "The disc is still spinning up. Try again in a moment.",
                "no-drive": f"No drive at {app.config['DEVICE']}.",
            }.get(media.get("state"), "This disc can't be written."))
        job_id = JOBS.start(spec, folder, app.config["DEVICE"], bool(data.get("final")))
    except discmaker.DiscError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "job": job_id})


@app.get("/api/jobs/<job_id>")
def job(job_id):
    data = JOBS.get(job_id)
    if data is None:
        abort(404)
    return jsonify(data)


@app.post("/api/eject")
def eject():
    if JOBS.running():
        return jsonify({"ok": False, "error": "A burn is running"}), 409
    ok = drive.eject(app.config["DEVICE"])
    return jsonify({"ok": ok})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="discbay-studio", description="DiscBay Studio web app")
    ap.add_argument("--port", type=int, default=int(os.environ.get("DISCBAY_STUDIO_PORT", DEFAULT_PORT)))
    ap.add_argument("--device", help="drive to burn to (default: the device in discs.toml)")
    args = ap.parse_args(argv)
    try:
        device = args.device or cfg.load().settings.device
    except cfg.ConfigError:
        device = "/dev/sr0"
    app.config.update(DEVICE=os.path.expanduser(device), PORT=args.port)
    if not (STATIC / "index.html").is_file():
        print(f"warning: page files missing from {STATIC}; reinstall with the studio_static folder in place",
              flush=True)
    print(f"DiscBay Studio: http://localhost:{args.port}  (drive: {device})", flush=True)
    # Localhost only. Nothing here is meant to be reachable from the network.
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
