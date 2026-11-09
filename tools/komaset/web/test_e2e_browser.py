#!/usr/bin/env python3
"""End-to-end: the real koma-setup.html in headless Chrome against komabridge + the SIMULATED camera (mockcard).

The bridge runs in-process (so a test can pull the cable, restart the bridge or open a second tab) on a free port and
serves the page at /, as it does for the owner. The mock runs tonight's hardware timings (MockBody.hw_timings,
~/koma-session-7..13): fresh-boot arm 0.8 s, a second remote session 20 s, -KSET busy 7 s after an EV change, live
view back 7 s after a shot, a card read inside remote control dropping live view, RETR 3.9 MB/s. One test runs them at
full scale (the slow arm and the EV busy window as the owner will see them); the others at a quarter scale.

Everything here is MOCK: it proves the page and the bridge agree, not that a camera does any of it.

    python3 -m pytest tools/komaset/web/test_e2e_browser.py -v

Needs the `playwright` Python package and a Chrome / Chromium (channel "chrome" first, then Playwright's own); skipped
otherwise. Every test fails on an uncaught page error or an unhandled promise rejection.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE = HERE.parent / "bridge"
sys.path.insert(0, str(BRIDGE))

import komabridge as KB  # noqa: E402
from mockcard import MockBody  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

EV_OFF = 0x053


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Bridge:
    """komabridge on a MockBody, in a thread. stop() and start() again on the same port = a bridge restart."""

    def __init__(self, scale: float, port: int | None = None):
        self.port = port or free_port()
        self.scale = scale
        self.start()

    def start(self):
        self.body = MockBody(tether=True).hw_timings(self.scale)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.rearm_after_read = True  # as main() sets it for the server
        self.server = KB.make_server(self.link, port=self.port)
        self.server.quiet = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.link.stop_live()
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def warm_second_session(self):
        """One remote session before the page opens, so the page's arm is a second session (the slow one)."""
        KB.tether_start(self.link)
        KB.tether_stop(self.link)


def launch(p):
    for kw in ({"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(**kw)
        except Exception:  # noqa: BLE001
            continue
    for exe in sorted(Path.home().glob("Library/Caches/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")):
        try:
            return p.chromium.launch(executable_path=str(exe))
        except Exception:  # noqa: BLE001
            continue
    return None


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class E2E(unittest.TestCase):
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
        self.errors: list[str] = []
        self.contexts = []

    def tearDown(self):
        for c in self.contexts:
            c.close()
        for b in self.bridges:
            try:
                b.stop()
            except Exception:  # noqa: BLE001
                pass
        self.assertEqual(self.errors, [], "page errors")

    # ---------------------------------------------------------------- helpers
    def bridge(self, scale=0.25, **kw) -> Bridge:
        b = Bridge(scale, **kw)
        self.bridges.append(b)
        return b

    def page(self, br: Bridge, hash_=""):
        ctx = self.browser.new_context(viewport={"width": 1200, "height": 900}, accept_downloads=True)
        self.contexts.append(ctx)
        ctx.add_init_script("delete window.showSaveFilePicker;")  # headless has no save dialog: plain download
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: self.errors.append(f"console: {m.text}")
              if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.goto(br.url + hash_)
        return pg

    def wait(self, pg, js: str, timeout_s: float, what: str = ""):
        try:
            pg.wait_for_function(js, timeout=timeout_s * 1000, polling=200)
        except Exception as error:  # noqa: BLE001
            state = pg.evaluate("""() => ({view: UI.view, br: BR.state, S: {a: S.active, ar: S.armed, b: S.busy},
                LV: {on: LV.on, want: LV.want, tries: LV.tries, seen: LV.seen}, over: $('liveOver').textContent,
                err: $('shootErr').textContent, wake: $('shootWake').innerText, X: {msg: X.msg, err: X.err}})""")
            raise AssertionError(f"timed out after {timeout_s} s waiting for {what or js}; page {state}") from error

    def live_up(self, pg, timeout_s=20):
        self.wait(pg, "() => LV.on && LV.seen && !$('liveFps').hidden && /fps/.test($('liveFps').textContent)",
                  timeout_s, "live view frames with an fps counter")

    def shoot_ready(self, pg, timeout_s=20):
        self.wait(pg, "() => !$('shootOn').hidden && S.active && S.armed && !$('shootBtn').disabled", timeout_s,
                  "remote control on and awake")

    # ---------------------------------------------------------------- tests
    def test_full_timing_slow_arm_shot_live_ev(self):
        """Full-scale hardware timings: a 20 s second-session arm, a shot, live view back after it, EV in the busy
        window. The page must say 'waking up' and never fail before the bridge gives up (30 s)."""
        br = self.bridge(scale=1.0)
        br.warm_second_session()
        pg = self.page(br, "#shoot")
        self.wait(pg, "() => !$('shootWake').hidden && /waking up/.test($('shootWake').innerText)", 8,
                  "the waking-up line")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 14:  # deep in the stall: still waking, no error, buttons held
            st = pg.evaluate("() => ({wake: !$('shootWake').hidden, err: !$('shootErr').hidden, btn: $('shootBtn').disabled})")
            self.assertTrue(st["wake"] and not st["err"] and st["btn"], st)
            time.sleep(1)
        self.shoot_ready(pg, 20)
        self.assertTrue(pg.evaluate("() => $('shootWake').hidden && $('shootErr').hidden"))
        armed_at = time.monotonic()
        self.assertLess(armed_at - t0, 25)

        pg.click("#liveBtn")
        self.live_up(pg, 10)
        before = len(br.body.folder_entries)
        pg.click("#shootBtn")
        self.wait(pg, "() => S.shots.length === 1 && $('shotImg').complete && $('shotImg').naturalWidth > 0", 30,
                  "the new shot shown")
        self.assertEqual(len(br.body.folder_entries), before + 1)
        self.assertIn("RIMG", pg.inner_text("#shotMeta"))
        # live view is back by itself (7 s on the camera; the bridge re-sent diaset after the shot's card reads)
        self.wait(pg, "() => LV.on && !$('liveFps').hidden", 20, "live view back after the shot")
        self.assertGreaterEqual(br.link.read_rearms, 1)

        # EV: two changes in the busy window. The second is busy for ~7 s; the page keeps it and lands it.
        pg.click("#shootExpo summary")
        self.wait(pg, "() => X.cam && !X.reading", 10, "exposure read")
        start = br.body.ram[EV_OFF]
        slider = "#xs-ev"
        idx = pg.evaluate(f"() => Number(document.querySelector('{slider}').value)")
        pg.evaluate(f"() => {{ const r = document.querySelector('{slider}'); r.value = String({idx + 1}); r.dispatchEvent(new Event('input')); }}")
        self.wait(pg, f"() => X.cam[{EV_OFF}] === {start + 2}", 10, "first EV change landed")
        pg.evaluate(f"() => {{ const r = document.querySelector('{slider}'); r.value = String({idx + 2}); r.dispatchEvent(new Event('input')); }}")
        self.wait(pg, "() => /still busy/.test(X.msg)", 12, "the busy-retry note")
        self.assertEqual(pg.evaluate(f"() => xVal({EV_OFF})"), start + 4)  # optimistic: the wanted value stays shown
        self.wait(pg, f"() => X.cam[{EV_OFF}] === {start + 4} && !X.msg && !X.err", 30, "second EV change landed")
        self.assertEqual(br.body.ram[EV_OFF], start + 4)

    def test_settings_read_edit_send(self):
        br = self.bridge()
        pg = self.page(br, "#edit")
        pg.get_by_role("button", name="Read settings from camera").click()
        self.wait(pg, "() => !!E.blk", 10, "settings read")
        pg.evaluate("""() => { const f = M.byName['iso'], cur = E.blk[f.offset];
            applyField(E, f, cur === 6 ? '7' : '6'); renderEdit(); }""")
        self.wait(pg, "() => changeCount() > 0 || E.blk.some((x, i) => x !== E.orig[i])", 5, "an edit")
        pg.get_by_role("button", name="Send to camera").click()
        self.wait(pg, "() => $('usbSheet').open", 10, "the sent sheet")
        dat = br.body.card.get("/EXT0/PARAM.DAT")
        self.assertIsNotNone(dat)
        self.assertNotEqual(dat, br.body.card["/EXT0/PARAM.CUR"])

    def test_photos_grid_lightbox_download(self):
        br = self.bridge()
        pg = self.page(br, "#photos")
        self.wait(pg, "() => document.querySelectorAll('#photoGrid .ph').length === 3", 15, "3 photos")
        self.wait(pg, "() => [...document.querySelectorAll('#photoGrid img')].every(i => i.complete && i.naturalWidth > 0)",
                  15, "thumbnails")
        pg.click("#photoGrid .ph")
        self.wait(pg, "() => $('lightbox').open && $('lbImg').complete && $('lbImg').naturalWidth > 0", 10, "lightbox")
        with pg.expect_download() as dl:
            pg.click("#lbDownload")
        self.assertTrue(dl.value.suggested_filename.upper().endswith(".JPG"))

    def test_live_modes_and_rearm_after_card_read(self):
        br = self.bridge()
        pg = self.page(br, "#shoot")
        self.shoot_ready(pg)
        pg.click("#liveBtn")
        self.live_up(pg)
        self.assertEqual(pg.evaluate("() => $('liveImg').naturalWidth"), 320)
        pg.check("#liveMode input[value='0']")
        self.wait(pg, "() => $('liveImg').naturalWidth === 160 && !$('liveFps').hidden", 10, "grey 160x120")
        pg.check("#liveMode input[value='1']")
        self.wait(pg, "() => $('liveImg').naturalWidth === 320 && !$('liveFps').hidden", 10, "picture 320x240")
        # a card read inside remote control drops live view on the camera; the bridge re-arms, the picture goes on
        n = br.link.read_rearms
        pg.evaluate("() => fetch(bridgeUrl('/photos/100RICOH/RIMG0001.JPG'), {cache: 'no-store'}).then(r => r.blob())")
        self.wait(pg, f"() => true", 1)
        self.assertGreater(br.link.read_rearms, n)
        frames = br.link.live.snapshot()["frames"]
        time.sleep(2)
        snap = br.link.live.snapshot()
        self.assertTrue(snap["running"])
        self.assertGreater(snap["frames"], frames)

    def test_unplug_and_replug(self):
        br = self.bridge()
        pg = self.page(br, "#shoot")
        self.shoot_ready(pg)
        pg.click("#liveBtn")
        self.live_up(pg)
        br.body.unplug()
        self.wait(pg, "() => !$('shootOff').hidden && $('shootOn').hidden", 10, "the camera-off panel")
        self.assertIn("Turn the camera on first", pg.inner_text("#shootOff"))
        br.body.plug()
        self.shoot_ready(pg, 20)
        self.live_up(pg, 20)  # live view was on before: it comes back by itself

    def test_bridge_restart(self):
        """A real process restart (komabridge.py --mock --mock-tether --mock-hw): keep-alive connections die too."""
        import subprocess
        port = free_port()
        cmd = [sys.executable, str(BRIDGE / "komabridge.py"), "--mock", "--mock-tether", "--mock-hw", "--quiet",
               "--port", str(port)]

        def spawn():
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), 0.2).close()
                    return proc
                except OSError:
                    time.sleep(0.1)
            proc.kill()
            raise AssertionError("bridge did not start")
        proc = spawn()
        try:
            br = type("B", (), {"url": f"http://127.0.0.1:{port}/"})()
            pg = self.page(br, "#shoot")
            self.shoot_ready(pg)
            pg.click("#liveBtn")
            self.live_up(pg)
            proc.terminate()
            proc.wait(10)
            self.wait(pg, "() => BR.state === 'off' && !$('shootOff').hidden", 12, "bridge-off panel")
            self.assertIn("Turn the camera on first", pg.inner_text("#shootOff"))
            proc = spawn()
            self.shoot_ready(pg, 20)
            self.live_up(pg, 25)  # live view was on: it comes back by itself
        finally:
            proc.terminate()
            proc.wait(10)

    def test_two_tabs_do_not_fight(self):
        br = self.bridge()
        a = self.page(br, "#shoot")
        self.shoot_ready(a)
        a.click("#liveBtn")
        self.live_up(a)
        b = self.page(br, "#shoot")
        self.shoot_ready(b)
        b.click("#liveBtn")
        self.live_up(b)
        self.wait(a, "() => !LV.on && /another window/.test($('liveOver').textContent)", 8, "tab A told")
        b_id = b.evaluate("() => TAB_ID")
        for _ in range(6):  # no ping-pong: B keeps the stream
            time.sleep(1)
            snap = br.link.live.snapshot()
            self.assertTrue(snap["running"])
            self.assertEqual(snap["client"], b_id)
        # B leaves Shoot (remote control off); A can take live view back with its button and gets re-armed
        b.evaluate("() => { location.hash = ''; }")
        time.sleep(1)
        a.click("#liveBtn")
        self.live_up(a, 20)

    def test_remote_view_streams_and_keys(self):
        br = self.bridge()
        pg = self.page(br, "#remote")
        self.wait(pg, "() => R.active && R.video && R.osd && /fps/.test($('rRate').textContent)", 20, "both layers")
        self.assertIn("menus", pg.inner_text("#rRate"))
        pg.click("button[data-rkey='23']")  # MENU/OK opens the (stand-in) menu: picture stops, menu layer stays
        self.wait(pg, "() => !$('rCap').hidden", 10, "menu over a stopped picture")
        pg.click("button[data-rkey='22']")
        self.wait(pg, "() => R.video", 15, "picture back")
        self.assertIn((3, 23), br.body.key_log)

    def test_interval_and_shot_in_photos(self):
        br = self.bridge()
        pg = self.page(br, "#photos")
        self.wait(pg, "() => document.querySelectorAll('#photoGrid .ph').length === 3", 15, "3 photos")
        pg.evaluate("() => { location.hash = 'shoot'; }")
        self.shoot_ready(pg)
        pg.click("#shootBtn")
        self.wait(pg, "() => S.shots.length === 1", 20, "a shot")
        pg.evaluate("() => { location.hash = 'photos'; }")
        self.wait(pg, "() => document.querySelectorAll('#photoGrid .ph').length === 4", 10, "the shot in Review photos")
        pg.evaluate("() => { location.hash = 'shoot'; }")
        self.shoot_ready(pg)
        pg.click("#ivBox summary")
        pg.fill("#ivEvery", "1")
        pg.fill("#ivCount", "2")
        pg.click("#ivBtn")
        self.wait(pg, "() => /Photo 2 of 2/.test($('ivStatus').textContent) && /Done/.test($('ivStatus').textContent)",
                  40, "interval done")


if __name__ == "__main__":
    unittest.main()
