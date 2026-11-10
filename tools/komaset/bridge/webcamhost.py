"""Webcam host for komabridge: the Koma app's Webcam area.

komacam (tools/komacam/komacam.py) does the work, in this process and unchanged in what it sends to the camera. It arms
live view through this bridge's own endpoints (POST /tether/start, GET /live?mode=1, and POST /tether/stop on stop),
serves the OBS source at 127.0.0.1:8770 (the OBS Browser source path, works in Zoom), and feeds a
virtual camera through pyvirtualcam (OBS Virtual Camera; never run end to end). This module starts and stops it for the
app, reports its state, checks this computer for what a virtual camera needs, and relays the picture the call receives.

The camera stays awake while it streams: the liveview feature renews Auto Power Off on every frame (STATIC, komacam
README), and the bridge's keepalive thread (keyset,0,27 after 20 s without a keyset; MOCK) sends only while remote
control is on, which it is for as long as the webcam runs. komabridge's main() starts that thread; the webcam starts it
too when the bridge was started without it. On macOS, caffeinate keeps this computer awake exactly while the webcam
runs (komacam's own default).

While the webcam holds the camera (on, or stopping), nothing else may end remote control or live view or take live view
over: POST /tether/stop, POST /live/stop (the video channel) and GET /live in modes 0, 1 and 3 answer 409 webcam_on
unless they carry this webcam's own X-Koma-Webcam header (komacam's requests do). Shoot in another window, on a phone or
the old page would otherwise freeze the call for a re-arm (about 20 s on a second session).

Routes (the loopback ones accept this computer's own pages only: localguard.local_origin_ok):
  GET  /webcam            state, counters, the device name and the check (cached; ?check=1 checks again)
  POST /webcam/start      start; answers at once, the camera wakes in the background
  POST /webcam/stop       stop in the background: live view off, remote control off (keyset,0,2 + remtout)
  GET  /webcam/preview    multipart JPEG: what the call receives (1280x720, letterboxed), or the state card

Device names (what the call app lists): "OBS Virtual Camera" on the OBS backend (macOS, Windows), the v4l2loopback
card label on Linux, "Koma GR" only for the macOS camera extension in tools/komacam/macos-extension, which is drafted
and not built. Evidence: MOCK only. The virtual camera paths have never run from here.
"""
from __future__ import annotations

import hmac
import http.client
import importlib.util
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import localguard

HERE = Path(__file__).resolve().parent
KOMACAM_DIR = HERE.parents[1] / "komacam"

OBS_DEVICE = "OBS Virtual Camera"
KOMA_DEVICE = "Koma GR"
KOMA_EXTENSION = "com.forthright.komacam.extension"          # tools/komacam/macos-extension/project.yml
OBS_EXTENSION = "com.obsproject.obs-studio.mac-camera-extension"  # as `systemextensionsctl list` shows it (OBS 33.0.1)
APPROVE_PATH = "System Settings > General > Login Items & Extensions > Camera Extensions"  # komacam README
PACKAGES = (("pyvirtualcam", "pyvirtualcam"), ("numpy", "numpy"), ("PIL", "Pillow"))  # (import name, pip name)
V4L2_COMMAND = 'sudo modprobe v4l2loopback exclusive_caps=1 card_label="OBS Virtual Camera"'
BOUNDARY = "komawebcam"
WEBCAM_HEADER = "X-Koma-Webcam"   # komacam's requests carry this webcam's secret (refuse() lets them through)

_komacam = None


def load_komacam():
    """tools/komacam/komacam.py as a module (imported on first use; the bridge stays stdlib-only without the webcam)."""
    global _komacam
    if _komacam is None:
        if str(KOMACAM_DIR) not in sys.path:
            sys.path.insert(0, str(KOMACAM_DIR))
        import komacam
        _komacam = komacam
    return _komacam


# ------------------------------------------------------------------------------------------------ the check

class Probe:
    """What this computer has for a virtual camera. Read-only; tests and --mock-vcam replace it."""

    def system(self) -> str:
        return sys.platform

    def have_module(self, name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    def python(self) -> str:
        return sys.executable or "python3"

    def obs_app(self) -> bool:
        if sys.platform == "darwin":
            return any(Path(p).is_dir() for p in ("/Applications/OBS.app", str(Path.home() / "Applications/OBS.app")))
        if sys.platform == "win32":
            roots = {os.environ.get(k) for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")} - {None}
            return any((Path(r) / "obs-studio" / "bin" / "64bit" / "obs64.exe").is_file() for r in roots)
        return bool(shutil.which("obs")) or Path("/var/lib/flatpak/app/com.obsproject.Studio").is_dir()

    def camera_extensions(self) -> list | None:
        """macOS: the camera extensions `systemextensionsctl list` reports, or None when it cannot run."""
        exe = shutil.which("systemextensionsctl") or "/usr/bin/systemextensionsctl"
        try:
            out = subprocess.run([exe, "list"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return parse_camera_extensions(out)

    def v4l2_devices(self) -> list | None:
        """Linux: the v4l2loopback devices, [] when the module is not loaded."""
        if not Path("/sys/module/v4l2loopback").is_dir():
            return []
        out = []
        for d in sorted(Path("/sys/devices/virtual/video4linux").glob("video*")):
            try:
                name = (d / "name").read_text().strip()
            except OSError:
                name = ""
            out.append({"device": "/dev/" + d.name, "name": name})
        return out


def parse_camera_extensions(text: str) -> list:
    """The cmio section of `systemextensionsctl list`: [{bundle, name, state, enabled}], tab-separated rows of
    enabled, active, teamID, bundleID (version), name, [state]."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("---"):
            inside = "system_extension.cmio" in line
            continue
        if not inside or "\t" not in line or line.startswith("enabled"):
            continue
        cols = line.split("\t")
        if len(cols) < 6:
            continue
        state = cols[5].strip().strip("[]")
        out.append({"bundle": cols[3].split(" (", 1)[0].strip(), "name": cols[4].strip(), "state": state,
                    "enabled": state == "activated enabled"})
    return out


def check(probe) -> dict:
    """What a virtual camera needs on this computer and what is missing, as steps in the order to do them.

    backend: "extension" (the Koma camera extension, macOS), "obs" (pyvirtualcam into OBS Virtual Camera, macOS and
    Windows), "v4l2loopback" (pyvirtualcam, Linux) or None. Each step is {"id", ...}; the app holds the words."""
    sysname = probe.system()
    plat = ("macos" if sysname == "darwin" else "windows" if sysname == "win32"
            else "linux" if sysname.startswith("linux") else "other")
    missing = [pip for mod, pip in PACKAGES if not probe.have_module(mod)]
    python_step = {"id": "python", "packages": missing, "platform": plat,  # the app names where the command goes
                   "command": f'"{probe.python()}" -m pip install ' + " ".join(missing)} if missing else None
    steps, facts = [], {"platform": plat, "packages_missing": missing}
    backend, device = "obs", OBS_DEVICE
    if plat == "macos":
        exts = probe.camera_extensions()
        facts["camera_extensions"] = exts
        koma = next((e for e in exts or [] if e["bundle"] == KOMA_EXTENSION), None)
        if koma is not None:  # the Koma extension reads komacam's 127.0.0.1:8770 stream itself: no pyvirtualcam
            if not koma["enabled"]:
                steps.append({"id": "approve", "name": KOMA_DEVICE, "path": APPROVE_PATH})
            return _result(plat, "extension", KOMA_DEVICE, steps, facts, vcam=False)
        obs = probe.obs_app()
        facts["obs_app"] = obs
        ext = next((e for e in exts or [] if e["bundle"] == OBS_EXTENSION), None)
        if python_step:
            steps.append(python_step)
        if exts is not None:  # None: systemextensionsctl did not answer, so nothing is claimed about the extension
            if ext is None:
                if not obs:
                    steps.append({"id": "obs_install"})
                steps.append({"id": "obs_vcam"})
            elif not ext["enabled"]:
                steps.append({"id": "approve", "name": ext["name"] or OBS_DEVICE, "path": APPROVE_PATH})
        elif not obs:
            steps.append({"id": "obs_install"})
    elif plat == "windows":
        obs = probe.obs_app()
        facts["obs_app"] = obs
        if python_step:
            steps.append(python_step)
        if not obs:
            steps.append({"id": "obs_install"})
    elif plat == "linux":
        devs = probe.v4l2_devices() or []
        facts["v4l2_devices"] = devs
        backend = "v4l2loopback"
        if python_step:
            steps.append(python_step)
        if devs:
            device = devs[0]["name"] or OBS_DEVICE
        else:
            steps.append({"id": "v4l2", "command": V4L2_COMMAND})
    else:
        backend, device = None, None
        steps.append({"id": "unsupported"})
    return _result(plat, backend, device, steps, facts, vcam=backend is not None and not missing)


def _result(plat, backend, device, steps, facts, vcam) -> dict:
    return {"platform": plat, "backend": backend, "device": device, "steps": steps, "vcam_possible": bool(vcam),
            "facts": facts}


def vcam_issue(error: str | None) -> str | None:
    """The step that answers a VirtualCam error (komacam's own text, or pyvirtualcam's: _native_macos_obs strings)."""
    if not error:
        return None
    e = error.lower()
    if any(f"{m} is not installed" in e for m in ("pyvirtualcam", "numpy", "pillow")):
        return "python"
    if "not supported" in e or "upgrade to obs" in e:
        return "obs_update"
    if "not installed" in e:            # "OBS Virtual Camera is not installed in your system. Use the Virtual Camera ..."
        return "obs_vcam"
    if "could not be started" in e:     # "virtual camera output could not be started"
        return "obs_busy"
    if "v4l2" in e or "loopback" in e:
        return "v4l2"
    return "vcam_other"


# ------------------------------------------------------------------------------------------------ the webcam

class Webcam:
    """komacam's App for the bridge: one at a time, started and stopped by the app."""

    def __init__(self, link, port: int, probe=None, mjpeg_port: int | None = None, caffeinate: bool | None = None,
                 vcam_module=None, vcam_fps: float = 30.0, vcam_retry_s: float = 20.0, preview_fps: float = 15.0,
                 quiet: bool = False, mode: int = 1):  # quiet: no bridge log lines of its own
        self.link, self.port = link, port
        self.probe = probe or Probe()
        env_port = os.environ.get("KOMA_WEBCAM_PORT")
        self.mjpeg_port = mjpeg_port if mjpeg_port is not None else int(env_port) if env_port else 8770
        self.caffeinate = sys.platform == "darwin" if caffeinate is None else caffeinate
        self.vcam_module, self.vcam_fps, self.vcam_retry_s = vcam_module, vcam_fps, vcam_retry_s
        self.preview_fps, self.quiet, self.mode = preview_fps, quiet, mode
        self.lock = threading.Lock()
        self.app = None
        self.vcam = None
        self.session = 0
        self.started = None
        self.stopping = False
        self.error = None      # {"code", "text"} of the last failed start
        self.mjpeg = None      # {"port", "ok"} of the running session
        self.secret = secrets.token_urlsafe(16)
        self._check = None

    @property
    def running(self) -> bool:
        return self.app is not None and not self.stopping

    @property
    def holds(self) -> bool:
        """The webcam has the camera: running, or still stopping (it ends remote control itself). /status.webcam."""
        return self.app is not None

    def check(self, refresh: bool = False) -> dict:
        if refresh or self._check is None:
            self._check = check(self.probe)
        return self._check

    # -- start / stop
    def start(self) -> dict:
        with self.lock:
            if self.stopping:  # an ON from another window or the phone while this one stops: say so, start nothing
                self.error = {"code": "stopping", "text": "The webcam was still stopping."}
                return self._snapshot_unlocked()
            if self.app is not None:
                return self._snapshot_unlocked()
            self.error = None
            try:
                kc = load_komacam()
            except Exception as error:  # noqa: BLE001  (komacam missing or broken: say so, never take the bridge down)
                self.error = {"code": "komacam", "text": f"{type(error).__name__}: {error}"}
                return self._snapshot_unlocked()
            chk = self.check(refresh=True)
            app, mjpeg_ok = None, True
            for outputs in ({"mjpeg"}, set()):
                a = kc.build_parser().parse_args(["--bridge-port", str(self.port), "--port", str(self.mjpeg_port),
                                                  "--mode", str(self.mode), "--status-every", "0",
                                                  "--health-every", "0"])
                a.outputs, a.caffeinate = set(outputs), self.caffeinate
                app = kc.App(a)
                app.client.extra_headers[WEBCAM_HEADER] = self.secret  # refuse() lets komacam's own requests through
                try:
                    app.start()
                    break
                except OSError as error:  # the OBS source port is taken
                    app = None
                    if _komacam_answers(self.mjpeg_port):
                        self.error = {"code": "cli_running", "text": f"komacam already serves 127.0.0.1:{self.mjpeg_port}"}
                        return self._snapshot_unlocked()
                    mjpeg_ok = False
                    if not self.quiet:
                        sys.stderr.write(f"komabridge: webcam without the OBS source ({error})\n")
            if app is None:
                self.error = {"code": "start", "text": "komacam did not start"}
                return self._snapshot_unlocked()
            vc = None
            if chk["vcam_possible"]:
                vc = kc.VirtualCam(app.out, fps=self.vcam_fps, module=self.vcam_module, retry_s=self.vcam_retry_s,
                                   page_url=app.url if mjpeg_ok else "")
                vc.start()
                app.vcam = vc
            ensure_keepalive(self.link)
            self.app, self.vcam = app, vc
            self.session += 1
            self.started = time.monotonic()
            self.mjpeg = {"port": self.mjpeg_port, "ok": mjpeg_ok}
            return self._snapshot_unlocked()

    def stop(self, wait: bool = False) -> dict:
        with self.lock:
            app = self.app
            if app is None or self.stopping:
                return self._snapshot_unlocked()
            self.stopping = True

        def run():
            try:
                app.stop()
            except Exception as error:  # noqa: BLE001
                sys.stderr.write(f"komabridge: webcam stop: {type(error).__name__}: {error}\n")
            finally:
                with self.lock:
                    self.app = self.vcam = None
                    self.stopping = False

        t = threading.Thread(target=run, daemon=True, name="webcam-stop")
        t.start()
        if wait:
            t.join(60)
        return self.snapshot()

    # -- state
    def snapshot(self, refresh_check: bool = False) -> dict:
        if refresh_check:
            self.check(refresh=True)
        with self.lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict:
        chk = self.check()
        app, vc = self.app, self.vcam
        out = {"on": app is not None and not self.stopping, "stopping": self.stopping, "session": self.session,
               "error": self.error, "backend": chk["backend"], "device": chk["device"], "check": chk,
               "keepalives": getattr(self.link, "keepalives", 0)}
        steps = [dict(s) for s in chk["steps"]]
        if app is not None:
            s = app.sup.snapshot()
            age = app.hub.age()
            out.update(
                since_s=round(time.monotonic() - self.started, 1) if self.started else None,
                phase=s["state"], fps_in=s["fps_in"], frames=s["frames"],
                frame_age_s=None if age == float("inf") else round(age, 1),
                live=s["state"] == "streaming" and age <= 1.5,
                arms=s["arms"], rearms=s["rearms"], reconnects=s["reconnects"], stalls=s["stalls"],
                arm_failures=s["arm_failures"], live_off=s["live_off"], last_end=s["last_end"],
                source=list(load_komacam().MODE_NATIVE_SIZE.get(app.a.mode, (320, 240))),
                output=list(app.proc.out_size), fit=app.proc.fit, readers=app.out.clients, mjpeg=self.mjpeg)
            issue = vcam_issue(vc.error) if vc is not None else None
            out["vcam"] = {"used": vc is not None, "open": bool(vc is not None and vc.device and not vc.error),
                           "device": vc.device if vc is not None else None,
                           "error": vc.error if vc is not None else None, "issue": issue,
                           "sent": vc.sent if vc is not None else 0}
            ids = {st["id"] for st in steps}
            explained = bool(ids & {"python", "obs_install", "obs_vcam", "approve", "v4l2"})  # the check says why
            if issue and issue not in ids and not (explained and issue in ("obs_vcam", "obs_busy", "vcam_other")):
                steps.append({"id": issue, "detail": vc.error})
        out["steps"] = steps
        return out

    # -- the preview: what the call receives
    def preview(self, handler):
        with self.lock:
            app = self.app if not self.stopping else None
        if app is None:
            return handler._json(409, {"error": "The webcam is off.", "code": "webcam_off"})
        kc = load_komacam()
        hd = kc.HAVE_PIL
        size = app.proc.out_size if hd else app.out.native_size
        handler.send_response(200)
        handler._cors()
        handler.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        if handler.command == "HEAD":
            return None
        period = 1.0 / max(1.0, self.preview_fps)
        last_id, last_card = 0, 0.0

        def part(body: bytes, ctype: str):
            handler.wfile.write(f"--{BOUNDARY}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n\r\n"
                                .encode("ascii") + body + b"\r\n")
            handler.wfile.flush()

        try:
            while self.app is app and not app.sup.stop_event.is_set():
                t0 = time.monotonic()
                f = app.hub.wait_next(last_id, timeout=0.5)
                if f is not None and app.hub.age() <= app.out.stale_s:
                    last_id = f.id
                    part(app.proc.hd_jpeg(f) if hd else f.data, "image/jpeg" if hd else f.ctype)
                    wait = period - (time.monotonic() - t0)
                    if wait > 0:
                        time.sleep(wait)
                    continue
                if time.monotonic() - last_card >= 0.5:  # no live frame: the state card the call shows, twice a second
                    last_card = time.monotonic()
                    card = app.out.placeholder(size)
                    if card is not None:
                        part(card, "image/jpeg")
            handler.wfile.write(f"--{BOUNDARY}--\r\n".encode("ascii"))
            handler.wfile.flush()
        except OSError:
            pass  # the page went away
        return None


def _komacam_answers(port: int) -> bool:
    """True when a komacam (its command line, most likely) already serves this port."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
    try:
        conn.request("GET", "/status.json", headers={"Host": f"127.0.0.1:{port}"})
        r = conn.getresponse()
        body = r.read(65536)
        return r.status == 200 and b'"fps_in"' in body and b'"version"' in body
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def ensure_keepalive(link):
    """The bridge's Auto Power Off keepalive thread, if main() has not started it (tests build their own Link)."""
    if not getattr(link, "_keepalive_started", False):
        link.start_keepalive()


# ------------------------------------------------------------------------------------------------ simulated (--mock-vcam)

class MockProbe(Probe):
    """SIMULATED: a computer with everything a virtual camera needs (macOS, OBS with its camera extension allowed,
    pyvirtualcam, numpy and Pillow), or with the parts listed in `missing` absent. For --mock-vcam, tests and the
    screenshots; it never looks at this computer."""

    def __init__(self, system="darwin", missing=(), obs=True, extension="activated enabled", koma_extension=None,
                 v4l2=("OBS Virtual Camera",)):
        self._system, self.missing, self.obs = system, set(missing), obs
        self.extension, self.koma_extension, self.v4l2 = extension, koma_extension, list(v4l2)

    def system(self):
        return self._system

    def have_module(self, name):
        return name not in self.missing

    def python(self):
        return "/usr/local/bin/python3"

    def obs_app(self):
        return self.obs

    def camera_extensions(self):
        out = []
        if self.extension:
            out.append({"bundle": OBS_EXTENSION, "name": OBS_DEVICE, "state": self.extension,
                        "enabled": self.extension == "activated enabled"})
        if self.koma_extension:
            out.append({"bundle": KOMA_EXTENSION, "name": KOMA_EXTENSION, "state": self.koma_extension,
                        "enabled": self.koma_extension == "activated enabled"})
        return out

    def v4l2_devices(self):
        return [{"device": f"/dev/video{i + 10}", "name": n} for i, n in enumerate(self.v4l2)]


class MockVirtualCamModule:
    """SIMULATED pyvirtualcam: a Camera that takes frames and drops them (counts them in `frames`). `fail` makes every
    open raise that text, as the real module does when OBS Virtual Camera is missing or busy."""

    class PixelFormat:
        RGB = "rgb"

    def __init__(self, device=OBS_DEVICE, fail: str | None = None):
        self.device, self.fail, self.frames = device, fail, 0
        module = self

        class Camera:
            def __init__(self, width, height, fps, fmt=None, **kw):
                if module.fail:
                    raise RuntimeError(module.fail)
                self.device, self.width, self.height = module.device, width, height
                self.period, self.next = 1.0 / fps, time.monotonic()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def send(self, arr):
                module.frames += 1

            def sleep_until_next_frame(self):
                self.next += self.period
                time.sleep(max(0.0, self.next - time.monotonic()))

        self.Camera = Camera


# ------------------------------------------------------------------------------------------------ routes

def for_link(link, port: int) -> Webcam:
    """The Webcam of this bridge (one per Link), made on first use. configure() sets its options first."""
    wc = getattr(link, "webcam", None)
    if wc is None:
        wc = Webcam(link, port, **(getattr(link, "webcam_config", {}) or {}))
        link.webcam = wc
    return wc


def configure(link, **opts):
    """Options for the Webcam made by for_link: probe, mjpeg_port, caffeinate, vcam_module, quiet, ..."""
    link.webcam_config = dict(opts)


def configure_mock(link, **probe_opts):
    """--mock-vcam: the simulated computer and virtual camera above."""
    configure(link, probe=MockProbe(**probe_opts), vcam_module=MockVirtualCamModule(), caffeinate=False)


def shutdown(link):
    wc = getattr(link, "webcam", None)
    if wc is not None and wc.app is not None:
        wc.stop(wait=True)


def _video_mode(query) -> bool:
    """True when ?mode= names the video channel (absent, 0, 1 or 3); mode 2, the camera's own screen, is separate."""
    return query.get("mode", [""])[0] in ("", "0", "1", "3")


def refuse(handler, link, method: str, route: str, query: dict) -> bool:
    """komabridge asks before its own routes. While the webcam holds the camera, a request that would end remote control
    or live view, or take live view over, gets 409 webcam_on unless it is komacam's own. True when it answered."""
    wc = getattr(link, "webcam", None)
    if wc is None or not wc.holds:
        return False
    takes = ((method == "POST" and route == "/tether/stop")
             or (method == "POST" and route == "/live/stop" and _video_mode(query))
             or (method == "GET" and route == "/live" and _video_mode(query)))
    if not takes:
        return False
    given = handler.headers.get(WEBCAM_HEADER) or ""
    if given and hmac.compare_digest(given.encode(), wc.secret.encode()):
        return False
    handler.close_connection = True  # a body (sendBeacon's) stays unread
    localguard.answer(handler, 409, {"error": "The webcam is using the camera. Turn it off in Webcam first.",
                                     "code": "webcam_on"})
    return True


def dispatch(handler, link, port: int, method: str, route: str, query: dict):
    try:
        return _dispatch(handler, link, port, method, route, query)
    except localguard.BodyError as error:
        return localguard.answer(handler, error.status, {"error": error.text, "code": "body"})


def _dispatch(handler, link, port: int, method: str, route: str, query: dict):
    if not getattr(handler, "koma_lan", False) and not localguard.local_origin_ok(handler):
        return handler._json(403, {"error": "The webcam is switched by Koma on this computer.", "code": "origin"})
    wc = for_link(link, port)
    if route == "/webcam" and method == "GET":
        return handler._json(200, wc.snapshot(refresh_check=query.get("check", [""])[0] == "1"))
    if route == "/webcam/start" and method == "POST":
        localguard.small_body(handler)
        return handler._json(200, wc.start())
    if route == "/webcam/stop" and method == "POST":
        localguard.small_body(handler)
        return handler._json(200, wc.stop())
    if route == "/webcam/preview" and method == "GET":
        return wc.preview(handler)
    return handler._json(404, {"error": f"No such endpoint: {method} {route}"})
