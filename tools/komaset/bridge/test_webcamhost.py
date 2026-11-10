#!/usr/bin/env python3
"""Tests for webcamhost (the Webcam area's bridge side), all against the SIMULATED camera (mockcard) and a simulated
computer (MockProbe, MockVirtualCamModule). No USB device, virtual camera or OBS is touched; nothing here says anything
about a real GR DIGITAL, a real OBS Virtual Camera or the macOS camera extension.

Run: python3 -m pytest -q tools/komaset/bridge/test_webcamhost.py
"""
from __future__ import annotations

import http.client
import io
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import komabridge as KB  # noqa: E402
import mockcard  # noqa: E402
import phoneaccess as P  # noqa: E402
import webcamhost as W  # noqa: E402

# `systemextensionsctl list` output (macOS 15, OBS 33.0.1): the evidence for the parser.
SYSEXT = (
    "4 extension(s)\n"
    "--- com.apple.system_extension.network_extension (Go to 'System Settings > General > Login Items & Extensions > "
    "Network Extensions' to modify these system extension(s))\n"
    "enabled\tactive\tteamID\tbundleID (version)\tname\t[state]\n"
    "*\t*\t78UFGP42EU\tch.tripmode.TripMode.FilterExtension (3.2.0/1385)\tFilterExtension\t[activated enabled]\n"
    "--- com.apple.system_extension.driver_extension (Go to 'System Settings > General > Login Items & Extensions > "
    "Driver Extensions' to modify these system extension(s))\n"
    "enabled\tactive\tteamID\tbundleID (version)\tname\t[state]\n"
    "*\t*\tG43BCU2T37\torg.pqrs.Karabiner-DriverKit-VirtualHIDDevice (1.6.0/1.6.0)\t"
    "org.pqrs.Karabiner-DriverKit-VirtualHIDDevice\t[activated enabled]\n"
    "--- com.apple.system_extension.cmio (Go to 'System Settings > General > Login Items & Extensions > Camera "
    "Extensions' to modify these system extension(s))\n"
    "enabled\tactive\tteamID\tbundleID (version)\tname\t[state]\n"
    "*\t*\t2MMRE5MTB8\tcom.obsproject.obs-studio.mac-camera-extension (33.0.1/40954849292)\tOBS Virtual Camera\t"
    "[activated enabled]\n")
NOT_INSTALLED = ("RuntimeError: 'obs' backend: OBS Virtual Camera is not installed in your system. Use the Virtual "
                 "Camera function in OBS to trigger installation.")  # pyvirtualcam 0.12 _native_macos_obs


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ids(chk):
    return [s["id"] for s in chk["steps"]]


class CheckTest(unittest.TestCase):
    def test_parse_systemextensionsctl(self):
        self.assertEqual(W.parse_camera_extensions(SYSEXT), [{
            "bundle": W.OBS_EXTENSION, "name": "OBS Virtual Camera", "state": "activated enabled", "enabled": True}])
        waiting = SYSEXT.replace("\tOBS Virtual Camera\t[activated enabled]", "\tOBS Virtual Camera\t[activated waiting for user]")
        self.assertEqual([e["enabled"] for e in W.parse_camera_extensions(waiting)], [False])
        self.assertEqual(W.parse_camera_extensions("0 extension(s)\n"), [])

    def test_macos(self):
        ready = W.check(W.MockProbe())
        self.assertEqual((ready["platform"], ready["backend"], ready["device"], ready["steps"], ready["vcam_possible"]),
                         ("macos", "obs", "OBS Virtual Camera", [], True))
        approve = W.check(W.MockProbe(extension="activated waiting for user"))
        self.assertEqual(approve["steps"], [{"id": "approve", "name": "OBS Virtual Camera", "path": W.APPROVE_PATH}])
        self.assertEqual(W.APPROVE_PATH, "System Settings > General > Login Items & Extensions > Camera Extensions")
        self.assertEqual(ids(W.check(W.MockProbe(extension=None))), ["obs_vcam"])
        self.assertEqual(ids(W.check(W.MockProbe(extension=None, obs=False))), ["obs_install", "obs_vcam"])
        py = W.check(W.MockProbe(missing={"pyvirtualcam", "numpy"}, extension=None))
        self.assertEqual(ids(py), ["python", "obs_vcam"])
        self.assertEqual(py["steps"][0]["packages"], ["pyvirtualcam", "numpy"])
        self.assertTrue(py["steps"][0]["command"].endswith('" -m pip install pyvirtualcam numpy'))
        self.assertEqual(py["steps"][0]["platform"], "macos")  # the app names where the command goes (Terminal)
        self.assertFalse(py["vcam_possible"])

    def test_macos_koma_extension_is_used_when_installed(self):
        """The drafted extension (never built): when it is there, its name and no pyvirtualcam."""
        ext = W.check(W.MockProbe(koma_extension="activated enabled"))
        self.assertEqual((ext["backend"], ext["device"], ext["steps"], ext["vcam_possible"]),
                         ("extension", "Koma GR", [], False))
        waiting = W.check(W.MockProbe(koma_extension="activated waiting for user"))
        self.assertEqual(waiting["steps"], [{"id": "approve", "name": "Koma GR", "path": W.APPROVE_PATH}])

    def test_macos_without_systemextensionsctl_claims_nothing_about_the_extension(self):
        class NoCtl(W.MockProbe):
            def camera_extensions(self):
                return None
        self.assertEqual(ids(W.check(NoCtl())), [])
        self.assertEqual(ids(W.check(NoCtl(obs=False))), ["obs_install"])

    def test_windows_linux_other(self):
        self.assertEqual(ids(W.check(W.MockProbe(system="win32", obs=False))), ["obs_install"])
        self.assertEqual(ids(W.check(W.MockProbe(system="win32"))), [])
        none = W.check(W.MockProbe(system="linux", v4l2=()))
        self.assertEqual((none["backend"], ids(none)), ("v4l2loopback", ["v4l2"]))
        self.assertIn("modprobe v4l2loopback", none["steps"][0]["command"])
        labelled = W.check(W.MockProbe(system="linux", v4l2=("GR on the desk",)))
        self.assertEqual((labelled["device"], labelled["steps"]), ("GR on the desk", []))
        other = W.check(W.MockProbe(system="sunos5"))
        self.assertEqual((other["backend"], other["device"], ids(other)), (None, None, ["unsupported"]))

    def test_vcam_errors_map_to_steps(self):
        for text, want in ((NOT_INSTALLED, "obs_vcam"),
                           ("Your OBS Virtual Camera version is not supported. Upgrade to OBS 28 or higher.", "obs_update"),
                           ("RuntimeError: virtual camera output could not be started", "obs_busy"),
                           ("pyvirtualcam is not installed", "python"), ("Pillow is not installed", "python"),
                           ("RuntimeError: 'v4l2loopback' backend: no v4l2 loopback device found", "v4l2"),
                           ("RuntimeError: something else", "vcam_other"), ("", None), (None, None)):
            self.assertEqual(W.vcam_issue(text), want, text)

    def test_the_state_card_says_what_the_page_says(self):
        """komacam's card (what the call shows without a picture) reads "No live view" while it re-arms a camera that
        refuses live view, the same test the app's Webcam page makes (art direction, should-fix)."""
        kc = W.load_komacam()
        base = {"live_off": 0, "arm_failures": 0, "frames": 0}
        self.assertEqual(kc.card_word({**base, "state": "arming"}), "Waking the camera")
        self.assertEqual(kc.card_word({**base, "state": "arming", "arm_failures": 1}), "No live view")
        self.assertEqual(kc.card_word({**base, "state": "starting stream", "live_off": 2, "frames": 50}), "No live view")
        self.assertEqual(kc.card_word({**base, "state": "arming", "arm_failures": 1, "frames": 50}), "Waking the camera")
        self.assertEqual(kc.card_word({**base, "state": "no camera", "arm_failures": 3}), "Camera not connected")
        self.assertEqual(kc.card_word({**base, "state": "streaming"}), "Waiting for the picture")

    @unittest.skipUnless(W.load_komacam().HAVE_PIL, "Pillow")
    def test_the_state_card_is_legible_in_a_call(self):
        """60 and 36 px type on a 720-line card (a Zoom tile shows it at a third of that): the two lines span well over
        100 of the 720 lines (the old sizes, 25 and 18 px, spanned about 70)."""
        from PIL import Image
        kc = W.load_komacam()
        img = Image.open(io.BytesIO(kc.placeholder_jpeg((1280, 720), ["GR DIGITAL", "Waking the camera"]))).convert("L")
        rows = [y for y in range(720) if max(img.crop((0, y, 1280, y + 1)).getdata()) > 90]
        self.assertGreater(rows[-1] - rows[0], 100)
        self.assertLess(abs((rows[0] + rows[-1]) / 2 - 360), 40)  # still centred

    @unittest.skipUnless(W.load_komacam().HAVE_PIL, "Pillow")
    def test_the_state_card_is_set_in_the_apps_archivo(self):
        """The card a call shows during every wait is in the app's typeface (art direction polish): the built app's
        Archivo, at the card's sizes and weights. Without the file it falls back to Pillow's own font and still draws."""
        from PIL import Image
        kc = W.load_komacam()
        path = kc.card_font_file()
        if path is None:
            self.skipTest("no built app and no npm package in this checkout")
        self.assertIn("archivo-latin-wdth-normal", path)
        font = kc._card_font(60, 500)
        self.assertEqual(font.getname()[0].split()[0], "Archivo")
        self.assertEqual([a["name"] for a in font.get_variation_axes()], [b"Weight", b"Width"])
        saved = list(kc._card_font_path)
        try:
            kc._card_font_path[:] = [None]  # a checkout without the app: Pillow's default font
            fallback = Image.open(io.BytesIO(kc.placeholder_jpeg((1280, 720), ["GR DIGITAL", "Stopped"]))).convert("L")
            self.assertGreater(max(fallback.getdata()), 150)  # the type is drawn
            self.assertNotEqual(kc._card_font(60, 500).getname()[0].split()[0], "Archivo")
        finally:
            kc._card_font_path[:] = saved

    def test_real_probe_reads_this_computer_without_failing(self):
        """The real Probe runs read-only checks (a file test, find_spec, systemextensionsctl list); it must not raise."""
        chk = W.check(W.Probe())
        self.assertIn(chk["platform"], ("macos", "windows", "linux", "other"))
        self.assertIsInstance(chk["steps"], list)


class _Served:
    """A bridge on an ephemeral port over the simulated camera, its webcam on a simulated computer."""

    probe_opts: dict = {}
    vcam_fail = None

    def setUp(self):
        self.body = mockcard.MockBody(tether=True, koma=True)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.log = lambda text: None
        self.server = KB.make_server(self.link, port=0)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.vcam = W.MockVirtualCamModule(fail=self.vcam_fail)
        self.mport = free_port()
        W.configure(self.link, probe=W.MockProbe(**self.probe_opts), vcam_module=self.vcam, caffeinate=False,
                    mjpeg_port=self.mport, quiet=True, vcam_retry_s=1.0)
        P.configure(self.link, host="127.0.0.1", bind_port=0, pair_from_self=True)  # the stand-in phone is 127.0.0.1

    def tearDown(self):
        W.shutdown(self.link)
        P.shutdown(self.link)
        self.server.shutdown()
        self.server.server_close()

    def jreq(self, method, path, obj=None, port=None, headers=None):
        port = port or self.port
        data = json.dumps(obj).encode() if obj is not None else None
        r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, json.loads(body) if body.startswith(b"{") else {}

    def until(self, pred, timeout=15.0, what=""):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = pred()
            if got:
                return got
            time.sleep(0.1)
        self.fail(f"timed out: {what}")

    def webcam(self):
        return self.jreq("GET", "/webcam")[1]

    def sent(self):
        return [c.strip() for t in self.body.transports for c in t.sent]

    def preview_parts(self, n, port=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port or self.port, timeout=10)
        conn.request("GET", "/webcam/preview", headers=headers or {})
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "multipart/x-mixed-replace; boundary=komawebcam")
        out = []
        import komacam
        for _, body in komacam.iter_multipart(r, "komawebcam"):
            out.append(body)
            if len(out) >= n:
                break
        conn.close()
        return out


class WebcamTest(_Served, unittest.TestCase):
    def test_off_by_default_touches_nothing(self):
        st = self.webcam()
        self.assertEqual((st["on"], st["device"], st["backend"], st["steps"]), (False, "OBS Virtual Camera", "obs", []))
        self.assertEqual(self.jreq("GET", "/webcam/preview")[0], 409)
        self.assertEqual(self.jreq("GET", "/webcam/nope")[0], 404)
        self.assertEqual(self.jreq("PUT", "/webcam/start")[0], 501)  # no such method on the bridge at all
        self.assertFalse(self.jreq("GET", "/status")[1]["webcam"])
        self.assertFalse([c for c in self.sent() if c.startswith("-CCTL")])  # no remote control, no live view

    def test_start_streams_to_the_virtual_camera_then_stop_ends_remote_control(self):
        st = self.jreq("POST", "/webcam/start")[1]
        self.assertTrue(st["on"])
        st = self.until(lambda: (lambda s: s if s.get("live") else None)(self.webcam()), 15, "frames")
        self.assertEqual((st["phase"], st["source"], st["output"], st["fit"]), ("streaming", [320, 240], [1280, 720], "letterbox"))
        self.until(lambda: self.webcam()["vcam"]["open"] and self.vcam.frames > 5, 10, "the virtual camera takes frames")
        self.assertEqual(self.body.remote, 2)
        self.assertTrue(self.jreq("GET", "/status")[1]["webcam"])
        # komacam's own outputs run: the OBS Browser source is served
        c = http.client.HTTPConnection("127.0.0.1", self.mport, timeout=5)
        c.request("GET", "/status.json", headers={"Host": f"127.0.0.1:{self.mport}"})
        self.assertEqual(c.getresponse().status, 200)
        c.close()
        # the preview is what the call receives: 1280x720
        from PIL import Image
        for body in self.preview_parts(3):
            self.assertEqual(Image.open(io.BytesIO(body)).size, (1280, 720))
        self.jreq("POST", "/webcam/stop")
        self.until(lambda: not self.webcam()["on"] and not self.webcam()["stopping"], 30, "stopped")
        self.assertEqual(self.body.remote, 0)
        self.assertIn("-CCTL keyset,0,2", self.sent())
        self.assertIn("-CCTL remtout", self.sent())
        self.assertFalse(self.jreq("GET", "/status")[1]["webcam"])
        with self.assertRaises(OSError):  # the OBS source is closed again
            socket.create_connection(("127.0.0.1", self.mport), timeout=1).close()
        # a second start is a new session (the app's preview reconnects on it)
        s1 = self.webcam()["session"]
        self.jreq("POST", "/webcam/start")
        self.assertEqual(self.webcam()["session"], s1 + 1)

    def test_keeps_the_camera_awake_while_streaming(self):
        """The bridge's Auto Power Off keepalive (keyset,0,27 after the idle time) runs whenever the webcam does."""
        self.link.keepalive_idle_s, self.link.keepalive_check_s = 1.0, 0.2
        self.jreq("POST", "/webcam/start")
        self.until(lambda: self.webcam().get("live"), 15, "frames")
        self.until(lambda: "-CCTL keyset,0,27" in self.sent(), 6, "a keepalive while streaming")
        self.assertGreaterEqual(self.webcam()["keepalives"], 1)
        self.assertTrue(self.webcam()["live"])  # the stream rode through it

    def test_preview_shows_the_state_card_while_the_camera_wakes(self):
        self.body.arm_first_s = 8.0
        self.jreq("POST", "/webcam/start")
        t0 = time.monotonic()
        parts = self.preview_parts(1)
        from PIL import Image
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertEqual(Image.open(io.BytesIO(parts[0])).size, (1280, 720))
        st = self.webcam()
        self.assertFalse(st["live"])
        self.assertIn(st["phase"], ("starting", "arming"))

    def test_command_line_komacam_on_the_port_refuses(self):
        class Komacam(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"state": "streaming", "fps_in": 25.0, "version": "0.2.0"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        stub = ThreadingHTTPServer(("127.0.0.1", self.mport), Komacam)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        try:
            st = self.jreq("POST", "/webcam/start")[1]
            self.assertEqual((st["on"], st["error"]["code"]), (False, "cli_running"))
            self.assertEqual(self.body.remote, 0)
        finally:
            stub.shutdown()
            stub.server_close()

    def test_port_taken_by_something_else_runs_without_the_obs_source(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", self.mport))
            s.listen(1)
            st = self.jreq("POST", "/webcam/start")[1]
            self.assertTrue(st["on"])
            self.assertEqual(st["mjpeg"], {"port": self.mport, "ok": False})
            self.until(lambda: self.webcam().get("live"), 15, "frames without the OBS source")

    def test_camera_without_live_view(self):
        self.body.diaset_duds = 10 ** 6  # every diaset answers 200 and does not take (stock firmware, no liveview)
        self.link.lvfr_wait_s, self.link.lvfr_rearm_s = 1.0, 0.3
        self.jreq("POST", "/webcam/start")
        st = self.until(lambda: (lambda s: s if s.get("arm_failures", 0) >= 1 else None)(self.webcam()), 20, "arm failure")
        self.assertEqual((st["frames"], st["live"]), (0, False))


class GuardTest(_Served, unittest.TestCase):
    def test_only_this_computer_switches_the_webcam(self):
        """A sandboxed iframe on any website sends Origin "null", which the loopback listener lets through for the old
        page; the webcam's routes answer this computer's own pages only (QA B1)."""
        for origin in ("null", "file://", "https://evil.example"):
            for method, path in (("POST", "/webcam/start"), ("POST", "/webcam/stop"), ("GET", "/webcam"),
                                 ("GET", "/webcam/preview")):
                self.assertEqual(self.jreq(method, path, headers={"Origin": origin})[0], 403, (origin, path))
        self.assertFalse(self.webcam()["on"])
        self.assertEqual(self.body.remote, 0)
        for origin in (f"http://127.0.0.1:{self.port}", "http://localhost:5173"):  # the app, and its dev server
            self.assertEqual(self.jreq("GET", "/webcam", headers={"Origin": origin})[0], 200, origin)

    def test_the_webcam_keeps_live_view_while_it_runs(self):
        """While the webcam holds the camera, nothing else ends remote control or live view or takes live view over
        (Shoot in another window, a phone, the old page); the camera's own screen (mode 2) stays free. komacam's own
        requests carry the webcam's header, so its stop still ends everything (QA S1)."""
        self.jreq("POST", "/webcam/start")
        self.until(lambda: self.webcam().get("live"), 15, "frames")
        rearms = self.webcam()["rearms"]
        for method, path in (("POST", "/tether/stop"), ("POST", "/live/stop"), ("POST", "/live/stop?mode=1"),
                             ("GET", "/live?mode=1"), ("GET", "/live"), ("GET", "/live?mode=3")):
            status, d = self.jreq(method, path)
            self.assertEqual((status, d.get("code")), (409, "webcam_on"), path)
        wrong = {W.WEBCAM_HEADER: "not-the-secret"}
        self.assertEqual(self.jreq("POST", "/tether/stop", headers=wrong)[0], 409)
        self.assertEqual(self.jreq("POST", "/live/stop?mode=2")[0], 200)
        self.assertEqual(self.jreq("GET", "/live/status")[0], 200)  # reads are free
        time.sleep(1.5)
        st = self.webcam()
        self.assertTrue(st["live"])
        self.assertEqual((st["rearms"], self.body.remote), (rearms, 2))
        self.assertTrue(self.jreq("GET", "/status")[1]["webcam"])
        # sendBeacon's POST carries a body: the refusal closes the connection rather than read it as the next request
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/tether/stop", body=b"x" * 64, headers={"Content-Type": "text/plain"})
        r = conn.getresponse()
        self.assertEqual((r.status, r.getheader("Connection")), (409, "close"))
        conn.close()
        self.jreq("POST", "/webcam/stop")
        self.until(lambda: not self.webcam()["on"] and not self.webcam()["stopping"], 30, "stopped")
        self.assertEqual(self.body.remote, 0)  # komacam's own /tether/stop went through
        self.assertFalse(self.jreq("GET", "/status")[1]["webcam"])
        self.assertEqual(self.jreq("POST", "/tether/stop")[0], 200)  # with the webcam off, anyone may again

    def test_on_while_stopping_is_refused_and_said(self):
        """An ON from another window or the phone while the webcam stops starts nothing and says so (QA N2)."""
        self.jreq("POST", "/webcam/start")
        self.until(lambda: self.webcam().get("live"), 15, "frames")
        wc = W.for_link(self.link, self.port)
        wc.stop()                  # stopping is set before stop() returns; the stop runs in the background
        st = wc.start()
        self.assertEqual((st["on"], st["error"]["code"]), (False, "stopping"))
        self.until(lambda: not self.webcam()["stopping"], 30, "stopped")
        st = self.webcam()
        self.assertEqual((st["on"], st["error"]["code"]), (False, "stopping"))  # it stays said until the next ON
        st = self.jreq("POST", "/webcam/start")[1]
        self.assertEqual((st["on"], st["error"]), (True, None))

    def test_an_oversized_body_closes_the_connection(self):
        """A body over 4 KB is never read, so the connection closes after the answer (QA N1)."""
        for path in ("/webcam/start", "/webcam/stop"):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request("POST", path, body=b"x" * 5000, headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            self.assertEqual((r.status, r.getheader("Connection")), (413, "close"), path)
            r.read()
            conn.close()
        self.assertFalse(self.webcam()["on"])

    def test_no_other_website_reads_the_picture(self):
        """komacam's OBS source port sends no Access-Control-Allow-Origin: the OBS Browser source reads it from the same
        origin and the macOS extension is not a browser, so no other page needs to read it (QA S2)."""
        self.jreq("POST", "/webcam/start")
        self.until(lambda: self.webcam().get("live"), 15, "frames")
        for path in ("/status.json", "/stream.mjpg", "/hd.mjpg", "/"):
            conn = http.client.HTTPConnection("127.0.0.1", self.mport, timeout=5)
            conn.request("GET", path, headers={"Host": f"127.0.0.1:{self.mport}", "Origin": "https://evil.example"})
            r = conn.getresponse()
            self.assertEqual(r.status, 200, path)
            self.assertIsNone(r.getheader("Access-Control-Allow-Origin"), path)
            conn.close()


@unittest.skipUnless(sys.platform == "darwin" and Path("/usr/bin/caffeinate").exists(), "macOS caffeinate")
class CaffeinateTest(_Served, unittest.TestCase):
    def test_this_mac_stays_awake_exactly_while_the_webcam_runs(self):
        """caffeinate -i -s -w <bridge pid> (komacam's own default on macOS) starts with the webcam and ends with it."""
        self.link.webcam = None
        W.configure(self.link, probe=W.MockProbe(), vcam_module=self.vcam, caffeinate=True, mjpeg_port=self.mport,
                    quiet=True, vcam_retry_s=1.0)

        def mine():
            out = subprocess.run(["pgrep", "-f", f"caffeinate -i -s -w {os.getpid()}"], capture_output=True, text=True)
            return out.stdout.split()
        self.assertEqual(mine(), [])
        self.jreq("POST", "/webcam/start")
        self.until(lambda: mine(), 5, "caffeinate running")
        self.jreq("POST", "/webcam/stop")
        self.until(lambda: not self.webcam()["on"] and not self.webcam()["stopping"], 30, "stopped")
        self.until(lambda: not mine(), 5, "caffeinate ended")


class VcamErrorTest(_Served, unittest.TestCase):
    vcam_fail = NOT_INSTALLED

    def test_the_error_becomes_its_step(self):
        self.jreq("POST", "/webcam/start")
        st = self.until(lambda: (lambda s: s if s.get("vcam", {}).get("issue") else None)(self.webcam()), 10, "vcam error")
        self.assertEqual(st["vcam"]["issue"], "obs_vcam")
        self.assertFalse(st["vcam"]["open"])
        self.assertEqual([s["id"] for s in st["steps"]], ["obs_vcam"])
        self.until(lambda: self.webcam().get("live"), 15, "the camera streams anyway (preview, OBS source)")


class PendingApprovalTest(_Served, unittest.TestCase):
    probe_opts = {"extension": "activated waiting for user"}
    vcam_fail = "RuntimeError: virtual camera output could not be started"

    def test_the_check_explains_it_once(self):
        self.jreq("POST", "/webcam/start")
        st = self.until(lambda: (lambda s: s if s.get("vcam", {}).get("issue") else None)(self.webcam()), 10, "vcam error")
        self.assertEqual([s["id"] for s in st["steps"]], ["approve"])  # not a second, contradictory step


class ExtensionTest(_Served, unittest.TestCase):
    probe_opts = {"koma_extension": "activated enabled"}

    def test_the_extension_reads_the_obs_source_and_no_vcam_runs(self):
        st = self.jreq("POST", "/webcam/start")[1]
        self.assertEqual((st["device"], st["backend"], st["vcam"]["used"]), ("Koma GR", "extension", False))
        self.until(lambda: self.webcam().get("live"), 15, "frames")
        conn = http.client.HTTPConnection("127.0.0.1", self.mport, timeout=5)  # the extension reads /hd.mjpg
        conn.request("GET", "/hd.mjpg", headers={"Host": f"127.0.0.1:{self.mport}"})
        r = conn.getresponse()
        r.read(2000)
        self.until(lambda: self.webcam()["readers"] == 1, 5, "one reader")
        conn.close()
        self.assertEqual(self.vcam.frames, 0)


class LanWebcamTest(_Served, unittest.TestCase):
    def test_a_paired_phone_needs_its_token_for_the_webcam(self):
        ph = self.jreq("POST", "/phone/on")[1]
        lan = int(ph["address"].rsplit(":", 1)[1])
        self.assertEqual(self.jreq("GET", "/webcam", port=lan)[0], 403)
        code = ph["url"].split("pair=", 1)[1]
        token = self.jreq("POST", "/phone/pair", {"code": code}, port=lan)[1]["token"]
        hdr = {"X-Koma-Token": token}
        self.assertEqual(self.jreq("GET", "/webcam", port=lan, headers=hdr)[0], 200)
        self.assertTrue(self.jreq("POST", "/webcam/start", port=lan, headers=hdr)[1]["on"])
        self.until(lambda: self.webcam().get("live"), 15, "frames")
        self.assertEqual(len(self.preview_parts(2, port=lan, headers=hdr)), 2)
        self.assertEqual(self.jreq("GET", f"/webcam/preview", port=lan)[0], 403)


@unittest.skipIf(os.name == "nt", "POSIX signals")
class CliTest(unittest.TestCase):
    def test_quit_ends_the_webcam_cleanly(self):
        """komabridge --mock --mock-tether --mock-vcam: start the webcam, then SIGTERM (the launcher's Quit). The bridge
        keeps serving until komacam has sent /tether/stop (keyset,0,2 + remtout), then exits 0."""
        bport = next(p for p in range(8875, 8975) if _port_free(p))
        wport = next(p for p in range(bport + 1, 8975) if _port_free(p))
        env = dict(os.environ, KOMA_WEBCAM_PORT=str(wport))
        p = subprocess.Popen([sys.executable, str(HERE / "komabridge.py"), "--mock", "--mock-tether", "--mock-vcam",
                              "--port", str(bport), "--quiet"], stderr=subprocess.PIPE, text=True, env=env)
        try:
            base = f"http://127.0.0.1:{bport}"
            end = time.monotonic() + 20
            while time.monotonic() < end:
                try:
                    urllib.request.urlopen(base + "/status", timeout=2).read()
                    break
                except OSError:
                    time.sleep(0.2)
            urllib.request.urlopen(urllib.request.Request(base + "/webcam/start", method="POST"), timeout=10).read()
            end = time.monotonic() + 20
            while time.monotonic() < end:
                if json.loads(urllib.request.urlopen(base + "/webcam", timeout=5).read()).get("live"):
                    break
                time.sleep(0.3)
            else:
                self.fail("no frames")
            p.send_signal(signal.SIGTERM)
            err = p.communicate(timeout=60)[1]
        finally:
            if p.poll() is None:
                p.kill()
        self.assertEqual(p.returncode, 0, err[-3000:])
        self.assertIn("-CCTL remtout -> 200", err)
        self.assertIn("stopped; remote mode off", err)


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    unittest.main()
