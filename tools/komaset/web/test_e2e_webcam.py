#!/usr/bin/env python3
"""End-to-end: the Webcam area and Use on your phone of the Koma app, in headless Chrome
against komabridge on the SIMULATED camera, with a simulated computer (webcamhost.MockProbe) and a simulated virtual
camera (webcamhost.MockVirtualCamModule). Phone access listens on 127.0.0.1 on a second port, standing in for this
computer's network address. Every test fails on a page error or an unhandled rejection.

Everything here is MOCK: no USB device, no OBS, no virtual camera driver and no phone is involved.

    (cd tools/komaapp && npm run build)
    python3 -m pytest tools/komaset/web/test_e2e_webcam.py -v
    KOMA_E2E_VIEWPORT=phone python3 -m pytest tools/komaset/web/test_e2e_webcam.py -v
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE = HERE.parent / "bridge"
sys.path.insert(0, str(BRIDGE))

import komabridge as KB  # noqa: E402
import phoneaccess as P  # noqa: E402
import webcamhost as W  # noqa: E402
from mockcard import MockBody  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

DESKTOP = {"width": 1280, "height": 860}
PHONE = {"width": 390, "height": 844}
VIEW = PHONE if os.environ.get("KOMA_E2E_VIEWPORT") == "phone" else DESKTOP
SWITCH = "[data-testid=webcam-switch]"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Bridge:
    """komabridge in this process over a simulated Koma camera, its webcam on a simulated computer."""

    def __init__(self, probe=None, vcam=None):
        self.body = MockBody(tether=True, reboot_s=2.0, koma=True).hw_timings(0.25)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.log = lambda text: None
        self.link.rearm_after_read = True
        self.server = KB.make_server(self.link, port=0)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.vcam = vcam or W.MockVirtualCamModule()
        self.mport = free_port()
        W.configure(self.link, probe=probe or W.MockProbe(), vcam_module=self.vcam, caffeinate=False,
                    mjpeg_port=self.mport, quiet=True, vcam_retry_s=1.0)
        P.configure(self.link, host="127.0.0.1", bind_port=0, pair_from_self=True)  # the stand-in phone is 127.0.0.1

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def webcam(self) -> W.Webcam:
        return W.for_link(self.link, self.port)

    @property
    def phone(self) -> P.PhoneAccess:
        return P.for_link(self.link, self.port, KB)

    def sent(self):
        return [c.strip() for t in self.body.transports for c in t.sent]

    def stop(self):
        W.shutdown(self.link)
        P.shutdown(self.link)
        self.link.stop_live()
        self.server.shutdown()
        self.server.server_close()


def launch(p):
    for kw in ({"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(**kw)
        except Exception:  # noqa: BLE001
            continue
    return None


@unittest.skipIf(sync_playwright is None, "playwright not installed")
@unittest.skipIf(KB.app_index() is None, "the app is not built (cd tools/komaapp && npm run build)")
class WebcamE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = launch(cls.pw)
        if cls.browser is None:
            cls.pw.stop()
            raise unittest.SkipTest("no Chrome / Chromium for playwright")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.bridges: list[Bridge] = []
        self.contexts = []
        self.errors: list[str] = []

    def tearDown(self):
        for c in self.contexts:
            c.close()
        for b in self.bridges:
            try:
                b.stop()
            except Exception:  # noqa: BLE001
                pass
        self.assertEqual(self.errors, [], "page errors")

    def bridge(self, **kw) -> Bridge:
        b = Bridge(**kw)
        self.bridges.append(b)
        return b

    def page(self, url, viewport=None, scheme="dark"):
        ctx = self.browser.new_context(viewport=viewport or VIEW, color_scheme=scheme)
        self.contexts.append(ctx)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        # "Failed to load resource" lines are the bridge's own answers the app handles (a 403 to an unpaired phone)
        pg.on("console", lambda m: self.errors.append(f"console: {m.text}")
              if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.goto(url)
        return pg

    def wait(self, pg, js, timeout_s=15, what="", arg=None):
        try:
            pg.wait_for_function(js, arg=arg, timeout=timeout_s * 1000, polling=200)
        except Exception as error:  # noqa: BLE001
            raise AssertionError(f"timed out after {timeout_s} s waiting for {what or js}; page text:\n"
                                 + pg.evaluate("() => document.body.innerText.slice(0, 900)")) from error

    def text(self, pg, needle, timeout_s=15):
        self.wait(pg, "t => document.body.innerText.includes(t)", timeout_s, repr(needle), arg=needle)

    def until(self, pred, timeout_s=15, what=""):
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if pred():
                return
            time.sleep(0.1)
        self.fail(f"timed out: {what}")

    def switch(self, pg, on: bool):
        pg.click(f"{SWITCH} label:nth-child({2 if on else 1})")

    # ---------------------------------------------------------------- Webcam
    def test_switch_on_preview_and_amber_then_off(self):
        br = self.bridge()
        pg = self.page(br.url + "#/webcam")
        self.text(pg, "Shows up as “OBS Virtual Camera” in Zoom, Meet and FaceTime.")
        self.text(pg, "The GR DIGITAL has no audio path over USB. Use this computer's microphone.")
        self.assertEqual(pg.get_attribute(SWITCH, "data-on"), "false")
        self.assertEqual(pg.locator("[data-testid=webcam-preview]").count(), 0)  # nothing at rest
        self.assertEqual(pg.locator("[data-testid=webcam-setup]").count(), 0)    # nothing missing
        self.assertEqual(br.body.remote, 0)                                     # the page alone starts nothing

        self.switch(pg, True)
        self.wait(pg, f"() => document.querySelector('{SWITCH}').dataset.live === 'true'", 20, "amber ON")
        self.wait(pg, "() => document.querySelector('[data-testid=webcam-preview] img')?.naturalWidth === 1280", 15,
                  "the preview at the call's 1280x720")
        # two facts, the two halves of the pipeline; no row whose value never changes (art direction must-fix 1)
        self.assertEqual(pg.eval_on_selector_all("[data-testid=webcam-spec] dt", "ds => ds.map(d => d.innerText)"),
                         ["Virtual device", "Last frame"])
        self.assertEqual(pg.inner_text("[data-testid=spec-device] dd"), "Receiving")
        self.assertEqual(pg.get_attribute("[data-testid=webcam-preview] img", "alt"), "The picture the call receives")
        self.assertEqual(pg.locator("[data-testid=webcam-preview-caption]").count(), 0)
        self.assertEqual(pg.locator("[data-testid=webcam-status]").count(), 0)  # nothing needs saying: the spec sheet
        self.wait(pg, f"""() => {{  // the ON segment's fill is the amber token once its 120 ms fade ends
            const on = document.querySelector('{SWITCH} label:nth-child(2) span');
            const probe = document.createElement('i'); probe.style.color = 'var(--acc)'; document.body.appendChild(probe);
            const acc = getComputedStyle(probe).color; probe.remove();
            return getComputedStyle(on).backgroundColor === acc; }}""", 5, "the ON segment in amber")
        self.assertEqual(br.body.remote, 2)
        self.until(lambda: br.vcam.frames > 5, 10, "frames in the virtual camera")
        self.text(pg, "Camera not listed")  # the help rows while it runs
        self.text(pg, "Wait for the other Koma page to finish reading the camera.")
        if VIEW is DESKTOP:  # one list grammar, one x: the help's fixes start where the spec sheet's values do
            xs = pg.evaluate("""() => ['[data-testid=webcam-spec] dd', '[data-testid=webcam-help] dd']
                .map(q => Math.round(document.querySelector(q).getBoundingClientRect().left))""")
            self.assertEqual(xs[0], xs[1])
        # the copy control sits right after the name it copies; "Copied." ends the sentence instead of splitting it
        self.assertTrue(pg.evaluate("""() => { const b = document.querySelector('[data-testid=copy-device]'),
            p = b.closest('p'), before = document.createRange(), after = document.createRange();
            before.setStart(p, 0); before.setEndBefore(b); after.setStartAfter(b); after.setEnd(p, p.childNodes.length);
            return before.toString().trim().endsWith('“OBS Virtual Camera”') &&
                   after.toString().startsWith(' in Zoom, Meet and FaceTime.'); }"""))
        # it never starts a line on its own: its top sits on the line that ends with the name
        self.assertTrue(pg.evaluate("""() => { const b = document.querySelector('[data-testid=copy-device]'),
            q = b.parentElement.firstChild, r = document.createRange(); r.selectNodeContents(q);
            const qb = r.getBoundingClientRect(), bb = b.getBoundingClientRect();
            const mid = (bb.top + bb.bottom) / 2;
            return mid > qb.top && mid < qb.bottom && bb.left >= qb.right - 8; }"""))
        pg.click("[data-testid=copy-device]")
        self.wait(pg, "() => document.querySelector('[data-testid=copy-device-done]').innerText === 'Copied.'", 5,
                  "Copied. after the sentence")

        # the bridge runs it: leaving the page leaves it on
        pg.goto(br.url + "#/home")
        self.wait(pg, "() => !!document.querySelector('[data-testid=overview]')", 15, "Overview")
        self.assertTrue(br.webcam.running)
        pg.goto(br.url + "#/webcam")
        self.wait(pg, f"() => document.querySelector('{SWITCH}')?.dataset.on === 'true'", 15, "still on")

        self.switch(pg, False)
        self.wait(pg, f"() => document.querySelector('{SWITCH}').dataset.on === 'false' && "
                      "!document.querySelector('[data-testid=webcam-preview]') && "
                      "!document.querySelector('[data-testid=webcam-status]')", 30, "off")
        self.until(lambda: br.body.remote == 0, 15, "remote control off")
        self.assertIn("-CCTL remtout", br.sent())

    def test_one_title_size_with_settings(self):
        """Webcam's title is the app's shared title, at the size Settings uses (art direction should-fix)."""
        br = self.bridge()
        pg = self.page(br.url + "#/webcam")
        self.wait(pg, "() => !!document.querySelector('#wc-title')", 15, "Webcam")
        size = "h => [getComputedStyle(h).fontSize, getComputedStyle(h).fontWeight, getComputedStyle(h).lineHeight]"
        webcam = pg.eval_on_selector("#wc-title", size)
        pg.goto(br.url + "#/settings/phone")
        self.wait(pg, "() => !!document.querySelector('[data-testid=phone-access]')", 15, "Use on your phone")
        self.assertEqual(webcam, pg.eval_on_selector("#set-title", size))  # polish PageTitle h1

    def test_a_long_device_name_wraps_with_its_copy_control(self):
        """A v4l2 card label is anyone's text. At 390 px a long one wraps inside the name, its last word stays with the
        copy control on one line, and nothing scrolls sideways."""
        label = "Loopback video device for the GR DIGITAL on the desk"
        br = self.bridge(probe=W.MockProbe(system="linux", v4l2=(label,)))
        pg = self.page(br.url + "#/webcam", viewport=PHONE)
        self.text(pg, "Shows up as “" + label + "” in Zoom, Meet and FaceTime.")
        self.assertLessEqual(pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0)
        self.assertTrue(pg.evaluate("""() => { const b = document.querySelector('[data-testid=copy-device]'),
            t = b.parentElement.firstChild, r = document.createRange(); r.selectNodeContents(t);
            const tb = r.getBoundingClientRect(), bb = b.getBoundingClientRect(), mid = (bb.top + bb.bottom) / 2;
            return t.textContent === 'desk”' && mid > tb.top && mid < tb.bottom
                && bb.right <= document.querySelector('.wc-row-text').getBoundingClientRect().right + 12; }"""))

    def test_setup_steps_follow_the_check(self):
        br = self.bridge(probe=W.MockProbe(extension=None, obs=False))
        pg = self.page(br.url + "#/webcam")
        self.wait(pg, "() => !!document.querySelector('[data-step=obs_install]') && "
                      "!!document.querySelector('[data-step=obs_vcam]')", 15, "two setup steps")
        self.assertEqual(pg.get_attribute("[data-step=obs_install] a", "href"), "https://obsproject.com")
        self.assertIn("Open OBS, click Start Virtual Camera once, then quit OBS.", pg.inner_text("[data-testid=webcam-setup]"))
        # OBS gets installed and its virtual camera started elsewhere; back in this window the check runs again
        br.webcam.probe = W.MockProbe()
        pg.evaluate("() => window.dispatchEvent(new Event('focus'))")
        self.wait(pg, "() => !document.querySelector('[data-testid=webcam-setup]')", 10, "nothing missing")

    def test_python_step_on_windows_shows_its_command(self):
        br = self.bridge(probe=W.MockProbe(system="win32", missing={"pyvirtualcam"}))
        pg = self.page(br.url + "#/webcam")
        self.wait(pg, "() => !!document.querySelector('[data-step=python]')", 15, "the python step")
        lines = pg.eval_on_selector_all("[data-step=python] p", "ps => ps.map(p => p.innerText)")
        self.assertEqual(lines, ["Install pyvirtualcam for the Python Koma runs with.",
                                 "Copy the command, then run it in Command Prompt.",
                                 '"/usr/local/bin/python3" -m pip install pyvirtualcam'])

    def test_python_step_copies_its_command(self):
        br = self.bridge(probe=W.MockProbe(missing={"pyvirtualcam"}))
        pg = self.page(br.url + "#/webcam")
        self.wait(pg, "() => !!document.querySelector('[data-step=python]')", 15, "the python step")
        lines = pg.eval_on_selector_all("[data-step=python] p", "ps => ps.map(p => p.innerText)")
        self.assertEqual(lines, ["Install pyvirtualcam for the Python Koma runs with.", "Copy the command, then run it in Terminal."])
        self.assertTrue(pg.evaluate("""() => { const p = document.querySelectorAll('[data-step=python] p')[1];
            return !!(p.compareDocumentPosition(document.querySelector('[data-testid=copy-python]'))
                      & Node.DOCUMENT_POSITION_FOLLOWING); }"""))
        # the two heads name places, as a manual does (art direction polish); while the webcam is off, what is missing
        # comes before the switch: it is the next thing to do
        self.assertEqual(pg.eval_on_selector_all("[data-testid=webcam] h2", "hs => hs.map(h => h.textContent)"),
                         ["On this computer", "In the call app"])
        self.assertTrue(pg.evaluate("() => !!(document.querySelector('[data-testid=webcam-setup]')"
                                    ".compareDocumentPosition(document.querySelector('.wc-switch')) & Node.DOCUMENT_POSITION_FOLLOWING)"))
        pg.click("[data-testid=copy-python]")
        self.text(pg, "Copied.")

    def test_virtual_camera_error_is_its_step_and_no_amber(self):
        br = self.bridge(vcam=W.MockVirtualCamModule(fail="RuntimeError: virtual camera output could not be started"))
        pg = self.page(br.url + "#/webcam")
        self.switch(pg, True)
        self.wait(pg, "() => !!document.querySelector('[data-step=obs_busy]')", 20, "the OBS busy step")
        self.text(pg, "Click Stop Virtual Camera in OBS, or quit OBS.")
        self.wait(pg, "() => document.querySelector('[data-testid=webcam-preview] img')?.naturalWidth > 0", 15, "preview")
        # the camera streams but nothing takes it: one status line names it, Setup follows it, no spec sheet repeats it
        self.wait(pg, "() => /The virtual camera is not running\\./.test(document.querySelector('[data-testid=webcam-status]')?.innerText || '')",
                  15, "the status line")
        self.text(pg, "Finish the step below.")
        # the frame is Koma's output, not what the call has (art direction must-fix 2)
        self.assertEqual(pg.inner_text("[data-testid=webcam-preview-caption]"),
                         "The call receives this once the virtual camera runs.")
        self.assertEqual(pg.get_attribute("[data-testid=webcam-preview] img", "alt"),
                         "Koma's picture, not yet reaching the call")
        self.assertEqual(pg.locator("[data-testid=webcam-spec]").count(), 0)
        self.assertTrue(pg.evaluate("""() => { const a = document.querySelector('[data-testid=webcam-status]'),
            b = document.querySelector('[data-testid=webcam-setup]');
            return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING); }"""))
        self.assertEqual(pg.get_attribute(SWITCH, "data-live"), "false")  # the call gets nothing: no amber

    def test_camera_without_live_view(self):
        br = self.bridge()
        br.body.diaset_duds = 10 ** 6
        br.link.lvfr_wait_s, br.link.lvfr_rearm_s = 1.0, 0.3
        pg = self.page(br.url + "#/webcam")
        self.switch(pg, True)
        self.text(pg, "The camera did not start live view.", 25)
        self.text(pg, "Live view needs Koma on the camera, turned on before USB is plugged in.")
        self.text(pg, "Unplug, turn the camera off and on, then plug in again.")
        self.assertEqual(pg.locator("[data-testid=webcam-spec]").count(), 0)  # the status line alone says it

    def test_command_line_webcam_already_running(self):
        br = self.bridge()

        class Komacam(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"state": "streaming", "fps_in": 25.0, "version": "0.2.0"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        stub = ThreadingHTTPServer(("127.0.0.1", br.mport), Komacam)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        try:
            pg = self.page(br.url + "#/webcam")
            self.switch(pg, True)
            self.text(pg, "A webcam is already running from the command line.")
            self.wait(pg, f"() => document.querySelector('{SWITCH}').dataset.on === 'false'", 10, "back to OFF")
            self.assertEqual(br.body.remote, 0)
        finally:
            stub.shutdown()
            stub.server_close()

    def test_waking_shows_its_elapsed_counter(self):
        br = self.bridge()
        br.body.arm_first_s = br.body.arm_later_s = 40.0
        br.link.lvfr_wait_s = 14.0
        pg = self.page(br.url + "#/webcam")
        self.switch(pg, True)
        self.text(pg, "Waking the camera")
        self.assertEqual(pg.locator("[data-testid=webcam-spec]").count(), 0)  # the status line alone says it
        self.wait(pg, "() => /Waking the camera · 0:1\\d/.test(document.querySelector('[data-testid=webcam-status]').innerText)",
                  16, "the counter past 10 s")

    def test_shoot_waits_while_the_webcam_runs(self):
        """While the webcam runs, Shoot says so instead of taking live view or ending remote control, which would freeze
        the call for a re-arm (QA S1). Turning the webcam off from there brings Shoot back."""
        br = self.bridge()
        br.webcam.start()
        self.until(lambda: br.webcam.snapshot().get("live"), 15, "frames")
        rearms = br.webcam.snapshot()["rearms"]
        pg = self.page(br.url + "#/shoot")
        self.text(pg, "The webcam is on.")
        self.text(pg, "Shoot pauses while it runs.")
        # Shoot's title stays hidden as on Shoot itself; the status line sits at the top of the column
        self.assertEqual(pg.eval_on_selector("#held-title", "h => h.innerText"), "Shoot")
        self.assertLessEqual(pg.eval_on_selector("#held-title", "h => h.getBoundingClientRect().height"), 1)
        top = pg.evaluate("""() => ['[data-testid=shoot-webcam-on]', '[data-testid=shoot-webcam-status]']
            .map(q => Math.round(document.querySelector(q).getBoundingClientRect().top))""")
        self.assertEqual(top[0], top[1])
        pg.wait_for_timeout(2500)
        st = br.webcam.snapshot()
        self.assertTrue(st["live"])
        self.assertEqual((st["rearms"], br.body.remote), (rearms, 2))
        pg.click("[data-testid=shoot-webcam-off]")
        self.wait(pg, "() => !document.querySelector('[data-testid=shoot-webcam-on]')", 30, "Shoot back")
        self.assertFalse(br.webcam.holds)

    def test_camera_unplugged_mid_call(self):
        """The camera goes away while the webcam runs. On Webcam the page stays and says so, switch and all; elsewhere
        Connect carries the switch (art direction must-fix 3, QA S5). The webcam picks the camera up again by itself."""
        br = self.bridge()
        pg = self.page(br.url + "#/webcam")
        self.switch(pg, True)
        self.wait(pg, f"() => document.querySelector('{SWITCH}')?.dataset.live === 'true'", 20, "live")
        br.body.unplug()
        self.wait(pg, "() => /Camera disconnected\\./.test(document.querySelector('[data-testid=webcam-status]')?.innerText || '')",
                  15, "disconnected")
        self.text(pg, "Turn it on, then reconnect USB.")
        self.assertEqual(pg.get_attribute(SWITCH, "data-on"), "true")
        self.assertEqual(pg.get_attribute(SWITCH, "data-live"), "false")
        self.assertEqual(pg.locator("[data-testid=webcam-spec]").count(), 0)
        br.body.plug()
        self.wait(pg, f"() => document.querySelector('{SWITCH}')?.dataset.live === 'true'", 30, "live again")
        br.body.unplug()
        # a page opened while the camera is away has had no connection yet: Connect, which carries the switch (a page
        # that was connected keeps its screens and shows a Disconnected line instead)
        pg.evaluate("() => { location.hash = '#/home'; }")
        pg.reload()
        self.wait(pg, "() => !!document.querySelector('[data-testid=webcam-away]')", 15, "Connect with the switch")
        self.text(pg, "The webcam resumes when the camera is back.")
        self.assertEqual(pg.get_attribute("[data-testid=webcam-away-switch]", "data-on"), "true")
        pg.click("[data-testid=webcam-away-switch] label:nth-child(1)")
        self.wait(pg, "() => !document.querySelector('[data-testid=webcam-away]')", 30, "webcam off")
        self.assertFalse(br.webcam.holds)

    # ---------------------------------------------------------------- Use on your phone
    def _qr_url(self, pg):
        import cv2
        import numpy as np
        png = pg.locator("[data-testid=phone-qr]").screenshot()
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
        for im in (img, cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)):  # as rendered, then 2x
            got, _, _ = cv2.QRCodeDetector().detectAndDecode(im)
            if got:
                return got
        return ""

    def test_phone_pairs_by_qr_then_revoke_and_off(self):
        br = self.bridge()
        desk = self.page(br.url + "#/settings/phone")
        self.wait(desk, "() => document.querySelector('[data-testid=phone-switch]')?.dataset.on === 'false'", 15, "off")
        self.assertEqual(desk.locator("[data-testid=phone-qr]").count(), 0)
        self.text(desk, "Turn on, then scan the code with the phone's camera.")
        desk.click("[data-testid=phone-switch] label:nth-child(2)")
        self.wait(desk, "() => !!document.querySelector('[data-testid=phone-qr]')", 15, "the QR code")
        url = br.phone.url()
        self.assertEqual(desk.inner_text("[data-testid=phone-address]"), br.phone.address)
        try:
            self.assertEqual(self._qr_url(desk), url)  # what a phone's camera reads is the pairing address
        except ImportError:
            pass
        self.text(desk, "No phone paired.")
        self.text(desk, "Scan with the phone's camera. It must be on the same Wi-Fi as this computer.")
        self.assertNotIn("Turn on, then scan", desk.inner_text("[data-testid=phone-access]"))  # said once, where it applies

        phone = self.page(url, viewport=PHONE)
        self.wait(phone, "() => !!document.querySelector('nav[aria-label=Main]') && /token=/.test(location.search)",
                  20, "the paired phone in the app")
        self.wait(phone, "() => !!document.querySelector('[data-testid=overview]')", 15, "Overview on the phone")
        box = phone.eval_on_selector("nav[aria-label=Main]", "n => n.getBoundingClientRect().toJSON()")
        self.assertGreater(box["top"], PHONE["height"] - 100)  # the phone layout: the tab bar at the bottom
        self.text(desk, "1 phone paired.", 10)
        phone.goto(url.split("?", 1)[0] + "?" + phone.evaluate("() => location.search.slice(1)") + "#/webcam")
        self.wait(phone, "() => !!document.querySelector('[data-testid=webcam-switch]')", 15, "Webcam on the phone")
        phone.goto(phone.url.split("#", 1)[0] + "#/settings/phone")
        self.text(phone, "This phone is paired with Koma on the computer at " + br.phone.address + ".")

        desk.click("[data-testid=phone-revoke]")
        self.text(desk, "Revoked. No phone has access.")
        self.wait(phone, "() => !!document.querySelector('[data-testid=phone-gate]')", 15, "the phone signed out")
        self.text(phone, "Access was revoked on the computer.")  # it kept a token the bridge now refuses
        self.assertNotEqual(br.phone.url(), url)

        desk.click("[data-testid=phone-switch] label:nth-child(1)")
        self.wait(desk, "() => !document.querySelector('[data-testid=phone-qr]')", 10, "access off")
        self.assertFalse(br.phone.on)

    def test_a_pairing_code_works_once(self):
        br = self.bridge()
        url = br.phone.turn_on()["url"]
        first = self.page(url, viewport=PHONE)
        self.wait(first, "() => !!document.querySelector('nav[aria-label=Main]')", 20, "first phone paired")
        second = self.page(url, viewport=PHONE)
        self.text(second, "This code is not current.")
        self.text(second, "On the computer, open Settings > Use on your phone and scan the new code.")

    def test_pairing_link_opened_on_the_computer_itself(self):
        br = self.bridge()
        url = br.phone.turn_on()["url"]
        code = url.split("pair=", 1)[1]
        pg = self.page(br.url + "?pair=" + code)  # the loopback listener: pairing happens on the network address
        self.text(pg, "This link pairs a phone.")
        self.assertEqual(br.phone.url(), url)    # the code was not used up
        pg.click("text=Open Koma on this computer")
        self.wait(pg, "() => !!document.querySelector('nav[aria-label=Main]')", 15, "the app on the computer")

    def test_phone_width_has_no_horizontal_scroll(self):
        br = self.bridge()
        br.phone.turn_on()
        for hash_, on in (("#/webcam", False), ("#/webcam", True), ("#/settings/phone", False)):
            if on:
                br.webcam.start()
            pg = self.page(br.url + hash_, viewport=PHONE)
            if on:
                self.wait(pg, "() => document.querySelector('[data-testid=webcam-preview] img')?.naturalWidth > 0", 20)
            else:
                pg.wait_for_timeout(1500)
            over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            self.assertLessEqual(over, 0, hash_)
            wide = pg.evaluate("""() => [...document.querySelectorAll('body *')].filter(e => {
                const r = e.getBoundingClientRect(); if (!r.width) return false;
                let p = e.parentElement; while (p) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return false; p = p.parentElement; }
                return r.right > innerWidth + 1 || r.left < -1; }).map(e => e.tagName + '.' + e.className).slice(0, 5)""")
            self.assertEqual(wide, [], hash_)
            # a Segmented OFF | ON counts as one control: its box, border included, is the target
            small = pg.evaluate("() => [...document.querySelectorAll('[data-testid=webcam] button, [data-testid=webcam] label, "
                                "[data-testid=phone-access] button, [data-testid=phone-access] label')]"
                                ".map(e => (e.closest('.seg') || e).getBoundingClientRect()).filter(r => r.width && r.height < 43.5).length")
            self.assertEqual(small, 0, hash_ + ": touch targets under 44 px")


if __name__ == "__main__":
    unittest.main()
