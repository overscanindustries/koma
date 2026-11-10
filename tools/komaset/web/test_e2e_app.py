#!/usr/bin/env python3
"""End-to-end: the Koma app (tools/komaapp/dist) in headless Chrome against komabridge + the SIMULATED camera.

Same pattern as test_e2e_browser.py (which now drives the old page at /legacy): the bridge runs in-process on a free
port over MockBody(tether=True) with the hardware timings at a quarter scale, and serves the app at /. Every test fails
on an uncaught page error or an unhandled promise rejection.

Everything here is MOCK: it proves the app and the bridge agree, not that a camera does any of it.

    (cd tools/komaapp && npm run build)
    python3 -m pytest tools/komaset/web/test_e2e_app.py -v

Needs the build, the `playwright` Python package and a Chrome / Chromium; skipped otherwise.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE = HERE.parent / "bridge"
sys.path.insert(0, str(BRIDGE))
sys.path.insert(0, str(HERE.parent))

import komabridge as KB  # noqa: E402
import komaset  # noqa: E402
from mockcard import MockBody, koma_cur, block_checksum, DEMO_CUR  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

DESKTOP = {"width": 1280, "height": 860}
PHONE = {"width": 390, "height": 844}
# KOMA_E2E_VIEWPORT=phone runs every test at 390 px (the bottom tab bar layout); default desktop.
VIEW = PHONE if os.environ.get("KOMA_E2E_VIEWPORT") == "phone" else DESKTOP
# A browser that finished setup: first run opens setup instead of Overview (FLOWS.md 1).
SETUP_SEEN = ("try { if (!localStorage.getItem('koma.setup')) localStorage.setItem('koma.setup', JSON.stringify("
              "{v: 1, seen: true, active: false, done: true, plan: 'wizard', step: 0, sub: 0})); } catch (e) {}")


SPARE_CUR = HERE / "testdata" / "demo-spare.cur"
MAP = komaset.Map(HERE.parent / "settings-map.json")


def with_byte(block: bytes, off: int, val: int) -> bytes:
    b = bytearray(block)
    b[off] = val
    b[0xFFC:] = block_checksum(b)
    return bytes(b)


def set_camera(body, block: bytes):
    """The camera now holds `block`: its card's PARAM.CUR and its live block in RAM (-KGET), as a real camera after a
    boot. The app reads the live block when the camera answers it, so a test that changes only the card changes
    nothing the app sees."""
    body.card["/EXT0/PARAM.CUR"] = block
    body.ram = bytearray(block)


VOLATILE = [(0x010, 14), (0x028, 8), (0x057, 1), (0x05e, 1), (0x070, 4), (0x0a2, 2), (0x0ce, 1), (0x0f4, 8),
            (0x8bd, 1), (0x8d0, 48), (0x900, 16), (0xffc, 4)]  # apply.svelte.js VOLATILE


def as_read(body) -> bytes:
    """What the app reads from a Koma camera (settings.svelte.js readBlock): the live block, with the bytes the camera
    writes itself (VOLATILE) from PARAM.CUR, checksum fixed."""
    out = bytearray(body.ram)
    cur = body.card["/EXT0/PARAM.CUR"]
    for off, n in VOLATILE:
        out[off:off + n] = cur[off:off + n]
    out[0xFFC:] = block_checksum(out)
    return bytes(out)


def other_value(field_name: str, block: bytes) -> int:
    f = MAP.by_name[field_name]
    return next(int(v) for v in sorted(f.values, key=int) if int(v) != block[f.offset])


class Bridge:
    def __init__(self, scale=0.25, plugged=True, port=0, koma=True):
        # koma: the simulated camera runs Koma (PARAM.CUR with the KOMA header, the look store with MONO active, KSIO,
        # MENU LAYOUT GR II); koma=False is a stock camera
        self.body = MockBody(tether=True, reboot_s=2.0, koma=koma).hw_timings(scale)
        if not plugged:
            self.body.unplug()
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.rearm_after_read = True
        self.server = KB.make_server(self.link, port=port)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def stop(self):
        self.link.stop_live()
        self.server.shutdown()
        self.server.server_close()


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
@unittest.skipIf(KB.app_index() is None, "the app is not built (cd tools/komaapp && npm run build)")
class AppE2E(unittest.TestCase):
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

    def page(self, br, hash_="", viewport=None, scheme="dark", wait_s=None):
        ctx = self.browser.new_context(viewport=viewport or VIEW, accept_downloads=True, color_scheme=scheme)
        self.contexts.append(ctx)
        ctx.add_init_script("delete window.showSaveFilePicker;")  # headless has no save dialog: plain download
        ctx.add_init_script(SETUP_SEEN)
        if wait_s:
            ctx.add_init_script(f"window.KOMA_WAIT_S = {wait_s};")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        # "Failed to load resource" console lines are the app's own API answers (409, 503 ...), which it handles; a
        # failed or refused app file (the page, its assets, the legacy page) fails the test instead.
        pg.on("console", lambda m: self.errors.append(f"console: {m.text}")
              if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.on("response", lambda r: self.errors.append(f"asset {r.status}: {r.url}")
              if r.status >= 400 and self.is_asset(r.url) else None)
        pg.on("requestfailed", lambda r: self.errors.append(f"asset failed: {r.url} {r.failure}")
              if self.is_asset(r.url) and not getattr(self, "offline", False)
              and "ERR_ABORTED" not in (r.failure or "") else None)  # aborted: the test navigated away
        pg.goto(br.url + hash_)
        return pg

    @staticmethod
    def is_asset(url: str) -> bool:
        from urllib.parse import urlsplit
        path = urlsplit(url).path
        return path in ("/", "/index.html", "/legacy", "/favicon.svg", "/sw.js", "/manifest.webmanifest") \
            or path.startswith("/assets/")

    def wait(self, pg, js, timeout_s, what=""):
        try:
            pg.wait_for_function(js, timeout=timeout_s * 1000, polling=200)
        except Exception as error:  # noqa: BLE001
            text = pg.evaluate("() => (document.querySelector('dialog[open]')?.innerText || '') + '\\n---\\n' + document.body.innerText.slice(0, 600)")
            raise AssertionError(f"timed out after {timeout_s} s waiting for {what or js}; page text:\n{text}") from error

    def text(self, pg, needle, timeout_s=10):
        pg.wait_for_function("t => document.body.innerText.includes(t)", arg=needle, timeout=timeout_s * 1000, polling=200)

    # ---------------------------------------------------------------- tests
    def test_connect_then_camera_appears_change_apply_confirmed(self):
        m = komaset.Map(HERE.parent / "settings-map.json")
        iso = m.by_name["iso"]
        br = self.bridge(plugged=False)
        pg = self.page(br)
        self.text(pg, "Connect your GR")
        self.text(pg, "Turn the camera on")
        self.text(pg, "Plug in the USB cable")
        self.text(pg, "Looking for the camera")
        self.assertEqual(pg.locator("nav[aria-label=Main]").count(), 0)  # full screen, no app yet
        br.body.plug()
        self.wait(pg, "() => !!document.querySelector('nav[aria-label=Main]')", 10, "the app after the camera appears")
        self.assertIn("GR DIGITAL", pg.inner_text("[data-testid=camera-state]"))

        pg.click("nav[aria-label=Main] a[data-nav=settings]")
        if VIEW is PHONE:
            self.text(pg, "Back up & restore")
            pg.click("a[data-settings=camera]")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 10", 15, "the camera menu")
        tabs = pg.eval_on_selector_all("[role=tab]", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(tabs[:3], ["SHOOTING", "KEY CUST.", "SETUP"])  # MENU LAYOUT = GR II on the simulated camera
        # a Koma look is active (MONO): IMG SET shows its name, as Shoot does, and is changed in Looks
        self.assertEqual(pg.inner_text("[data-row=img_set] .val").strip(), "MONO")
        self.assertNotIn("RECIPE", pg.inner_text("main").upper())
        before = br.body.card["/EXT0/PARAM.CUR"][iso.offset]
        pg.click("[data-row=iso] button.head")
        self.text(pg, "How sensitive the camera is to light")  # the row's one plain sentence
        options = pg.eval_on_selector_all("[data-row=iso] select option", "os => os.map(o => o.value)")
        want = next(int(v) for v in options if int(v) != before)
        pg.select_option("[data-row=iso] select", str(want))
        self.wait(pg, "() => /1\\s*change/i.test(document.querySelector('[data-testid=pending-bar]')?.innerText || '')", 5,
                  "1 CHANGE in the pending bar")

        # Review lists it with an undo; undo and redo it
        pg.click("[data-testid=pending-bar] >> text=Review")
        self.text(pg, "ISO SETTING")
        pg.click("[data-testid=review-sheet] >> text=Undo all")
        self.wait(pg, "() => !document.querySelector('[data-testid=pending-bar]')", 5, "no pending bar after undo")
        pg.select_option("[data-row=iso] select", str(want))

        pg.click("[data-testid=pending-bar] >> text=Apply to camera")
        self.wait(pg, "() => /Applied\\./.test(document.querySelector('[data-testid=apply-status]')?.innerText || '')",
                  60, "send, restart, reconnect and read back")
        self.assertEqual(pg.eval_on_selector("[data-result=iso]", "e => e.getAttribute('aria-label') || e.innerText").count("not"), 0)
        self.assertEqual(br.body.card["/EXT0/PARAM.CUR"][iso.offset], want)
        self.assertGreaterEqual(br.body.poff_count, 1)
        self.assertIn("/EXT0/PARAM.OK", br.body.card)
        pg.click("[data-testid=apply-sheet] >> text=Done")
        self.wait(pg, "() => !document.querySelector('[data-testid=pending-bar]')", 5, "nothing pending after apply")

    def test_shoot_shutter(self):
        br = self.bridge()
        pg = self.page(br, "#/shoot")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        self.wait(pg, "() => document.querySelector('[data-testid=live] img.live').naturalWidth > 0", 30, "live view")
        before = len(br.body.folder_entries)
        pg.click("[data-testid=shutter]")
        self.wait(pg, "() => /photos? this session/.test(document.querySelector('[data-testid=shoot-status]').innerText)", 30, "a photo")
        self.wait(pg, "() => [...document.querySelectorAll('.thumbs img')].some(i => i.complete && i.naturalWidth > 0)", 15,
                  "the shot in the session strip")
        self.assertEqual(len(br.body.folder_entries), before + 1)
        # Space is the shutter too
        pg.locator("body").focus()
        pg.evaluate("() => document.activeElement && document.activeElement.blur()")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "ready again")
        pg.keyboard.press("Space")
        self.wait(pg, "() => /2 photos this session/.test(document.querySelector('[data-testid=shoot-status]').innerText)", 30,
                  "a second photo by Space")

    def test_interval_and_remote(self):
        br = self.bridge()
        pg = self.page(br, "#/shoot")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        pg.click("[data-testid=interval-panel]")
        before = len(br.body.folder_entries)
        pg.fill("[data-testid=iv-every]", "1")
        pg.fill("[data-testid=iv-count]", "2")
        pg.click("[data-testid=iv-btn]")
        self.wait(pg, "() => /Photo 2 of 2/.test(document.querySelector('[data-testid=iv-status]')?.innerText || '') && "
                      "/Done/.test(document.querySelector('[data-testid=iv-status]').innerText)", 40, "interval done")
        self.assertEqual(len(br.body.folder_entries), before + 2)  # two photos written on the simulated card
        pg.click("[data-testid=remote-panel]")
        pg.click("button[data-rkey='23']")
        pg.wait_for_timeout(800)
        self.assertIn((3, 23), br.body.key_log)

    def test_theme_toggle(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/camera", scheme="dark")
        self.assertEqual(pg.evaluate("() => document.documentElement.dataset.theme"), "dark")
        bg_dark = pg.evaluate("() => getComputedStyle(document.body).backgroundColor")
        if VIEW is PHONE:  # on a phone the theme is Settings > Appearance
            pg.goto(br.url + "#/settings")
            pg.check("[data-testid=theme-choice] input[value=light]", force=True)
        else:
            pg.click("[data-testid=theme-toggle] >> visible=true")
        self.wait(pg, "() => document.documentElement.dataset.theme === 'light'", 5, "light theme")
        self.assertNotEqual(pg.evaluate("() => getComputedStyle(document.body).backgroundColor"), bg_dark)
        self.assertEqual(pg.evaluate("() => localStorage.getItem('koma.theme')"), "light")
        pg.reload()
        self.wait(pg, "() => document.documentElement.dataset.theme === 'light'", 5, "light after reload")
        # the system preference applies until the user chooses
        pg2 = self.page(br, "", scheme="light")
        self.wait(pg2, "() => document.documentElement.dataset.theme === 'light'", 5, "light from the system")
        pg3 = self.page(br, "", scheme="dark")
        self.wait(pg3, "() => document.documentElement.dataset.theme === 'dark'", 5, "dark by default")

    def test_phone_width_no_horizontal_scroll(self):
        br = self.bridge()
        off = self.bridge(plugged=False)
        pages = [(off, ""), (off, "#/card"), (br, "#/home"), (br, "#/shoot"), (br, "#/settings"), (br, "#/settings/camera"),
                 (br, "#/settings/mode"), (br, "#/settings/backup"), (br, "#/settings/copy"), (br, "#/settings/advanced"),
                 (br, "#/photos"), (br, "#/looks"), (br, "#/webcam")]
        for b, h in pages:
            pg = self.page(b, h, viewport=PHONE)
            pg.wait_for_timeout(1500)
            if b is br and h == "#/settings/camera":
                self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 5", 15, "rows")
                pg.click("[data-row=iso] button.head")
            over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            self.assertLessEqual(over, 0, h or "connect")
            wide = pg.evaluate("""() => [...document.querySelectorAll('body *')].filter(e => {
                const r = e.getBoundingClientRect(); if (!r.width) return false;
                let p = e.parentElement; while (p) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return false; p = p.parentElement; }
                return r.right > innerWidth + 1 || r.left < -1; }).map(e => e.tagName + '.' + e.className).slice(0, 5)""")
            self.assertEqual(wide, [], h)
            if b is br:  # a bottom tab bar on phones
                box = pg.eval_on_selector("nav[aria-label=Main]", "n => n.getBoundingClientRect().toJSON()")
                self.assertGreater(box["top"], PHONE["height"] - 100, h)
            pg.context.close()
            self.contexts.remove(pg.context)

    def test_card_file_flow(self):
        off = self.bridge(plugged=False)
        pg = self.page(off)
        self.text(pg, "Work from a memory card instead")
        pg.click("text=Work from a memory card instead")
        self.text(pg, "Choose the settings file")
        pg.set_input_files("[data-testid=card-file]", str(HERE / "testdata" / "demo-main.cur"))
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 10", 10, "the camera menu on the file")
        m = komaset.Map(HERE.parent / "settings-map.json")
        f = m.by_name["iso"]
        main = (HERE / "testdata" / "demo-main.cur").read_bytes()
        pg.click("[data-row=iso] button.head")
        want = next(int(v) for v in pg.eval_on_selector_all("[data-row=iso] select option", "os => os.map(o => o.value)")
                    if int(v) != main[f.offset])
        pg.select_option("[data-row=iso] select", str(want))
        with pg.expect_download() as dl:
            pg.click("[data-testid=pending-bar] >> text=Save to card")
        self.assertEqual(dl.value.suggested_filename, "PARAM.DAT")
        data = Path(dl.value.path()).read_bytes()
        self.assertEqual(len(data), 4096)
        self.assertEqual(data[f.offset], want)
        self.assertEqual(int.from_bytes(data[0xFFC:], "big"), komaset.compute_checksum(bytearray(data), m))
        self.text(pg, "Finish on the camera")

    # ---------------------------------------------------------------- failure paths (all recoverable)
    def _menu_with_change(self, pg):
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 10", 20, "the camera menu")
        pg.click("[data-row=iso] button.head")
        cur = pg.eval_on_selector("[data-row=iso] select", "s => s.value")
        want = next(v for v in pg.eval_on_selector_all("[data-row=iso] select option", "os => os.map(o => o.value)") if v != cur)
        pg.select_option("[data-row=iso] select", want)
        self.wait(pg, "() => !!document.querySelector('[data-testid=pending-bar]')", 5, "the pending bar")
        return int(want)

    def _status(self, pg, pattern, timeout_s, what):
        self.wait(pg, "() => new RegExp(%r).test(document.querySelector('[data-testid=apply-status]')?.innerText || '')"
                  % pattern, timeout_s, what)

    def test_fail_unplugged_before_send_then_retry(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/camera")
        want = self._menu_with_change(pg)
        br.body.unplug()
        pg.click("[data-testid=pending-bar] >> text=Apply to camera")
        self._status(pg, "could not be sent", 30, "the send failure")
        self.assertIn("TRY AGAIN", pg.inner_text("[data-testid=apply-sheet]").upper())
        br.body.plug()
        time.sleep(1)
        pg.get_by_role("button", name="Try again").click()
        self._status(pg, "Applied\\.", 60, "applied after the retry")
        self.assertEqual(br.body.card["/EXT0/PARAM.CUR"][0x059], want)

    def test_fail_unplugged_during_restart_wait_then_keep_waiting(self):
        br = self.bridge()
        br.body.reboot_s = 3.0
        pg = self.page(br, "#/settings/camera", wait_s=8)
        want = self._menu_with_change(pg)
        pg.click("[data-testid=pending-bar] >> text=Apply to camera")
        self.wait(pg, "() => /Waiting for the camera/.test(document.querySelector('[aria-current=step]')?.innerText || '')",
                  20, "the wait step")
        br.body.unplug()  # the cable comes out while the camera restarts: it never comes back by itself
        self._status(pg, "has not come back", 30, "the not-back failure")
        self.assertIn("KEEP WAITING", pg.inner_text("[data-testid=apply-sheet]").upper())
        self.assertTrue(pg.evaluate("() => !!document.querySelector('nav[aria-label=Main]')"))  # the app stays, not Connect
        br.body.plug()  # the camera back on the cable: the sheet carries on by itself, no Keep waiting needed
        self._status(pg, "Applied\\.", 60, "applied once the camera is back")
        self.assertEqual(br.body.card["/EXT0/PARAM.CUR"][0x059], want)

    def test_fail_read_back_mismatch(self):
        br = self.bridge()
        br.body._boot = lambda: br.body.card.pop("/EXT0/PARAM.DAT", None)  # a camera without the settings import
        pg = self.page(br, "#/settings/camera")
        self._menu_with_change(pg)
        pg.click("[data-testid=pending-bar] >> text=Apply to camera")
        self._status(pg, "restarted without loading the settings", 60, "the read-back mismatch")
        self.assertEqual(pg.eval_on_selector("[data-result=iso] .icon", "e => e.getAttribute('aria-label')"), "Not taken")
        pg.click("[data-testid=apply-sheet] >> text=Close")
        # the change is still pending, so it can be applied again or undone
        self.wait(pg, "() => !!document.querySelector('[data-testid=pending-bar]')", 5, "the change still pending")

    def test_fail_bridge_unreachable_then_back(self):
        """A real bridge process killed and started again (in-process, keep-alive connections would outlive it)."""
        import socket
        import subprocess
        with socket.socket() as so:
            so.bind(("127.0.0.1", 0))
            port = so.getsockname()[1]
        cmd = [sys.executable, str(BRIDGE / "komabridge.py"), "--mock", "--mock-tether", "--quiet", "--port", str(port)]

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
            pg = self.page(br, "#/shoot")
            self.wait(pg, "() => !document.querySelector('[data-testid=shutter]')?.disabled", 30, "shoot ready")
            proc.terminate()
            proc.wait(10)
            self.text(pg, "Koma is not running on this computer", 15)
            self.text(pg, "Open Koma again from Applications.")   # headless Chrome on macOS
            proc = spawn()
            self.wait(pg, "() => !!document.querySelector('nav[aria-label=Main]')", 20, "the app again")
            self.wait(pg, "() => !document.querySelector('[data-testid=shutter]')?.disabled", 40, "shoot works again")
        finally:
            proc.terminate()
            proc.wait(10)

    def test_no_console_errors_on_any_screen(self):
        br = self.bridge()
        for h in ("", "#/home", "#/shoot", "#/photos", "#/looks", "#/webcam", "#/settings/camera", "#/settings/mode", "#/settings/backup",
                  "#/settings/copy", "#/settings/update", "#/settings/advanced", "#/settings"):
            pg = self.page(br, h)
            pg.wait_for_timeout(2500)
            pg.context.close()
            self.contexts.remove(pg.context)
        off = self.bridge(plugged=False)
        for h in ("", "#/card"):
            pg = self.page(off, h)
            pg.wait_for_timeout(1500)
        # tearDown fails the test on any console error or page error collected above

    def test_legacy_page_still_served(self):
        br = self.bridge()
        pg = self.page(br)
        pg.goto(br.url + "legacy#edit")
        self.assertEqual(pg.title(), "Koma Setup")
        self.wait(pg, "() => typeof BR === 'object' && BR.state === 'cam'", 15, "the old page sees the camera")

    # ---------------------------------------------------------------- D1 fix pass: Apply correctness and the other flows
    def _apply_done(self, pg, timeout_s=60, what="applied"):
        self._status(pg, "Applied\\.", timeout_s, what)

    def test_retry_after_mismatch_sends_again(self):
        br = self.bridge()
        real_boot = br.body._boot
        br.body._boot = lambda: br.body.card.pop("/EXT0/PARAM.DAT", None)  # the import does not run this time
        pg = self.page(br, "#/settings/camera")
        want = self._menu_with_change(pg)
        pg.click("[data-testid=pending-bar] >> text=Apply to camera")
        self._status(pg, "restarted without loading the settings", 60, "the read-back mismatch")
        self.assertEqual(br.body.poff_count, 1)
        br.body._boot = real_boot                     # the camera imports from now on
        pg.get_by_role("button", name="Send again").click()
        self._apply_done(pg, 60, "applied after Try again")
        self.assertEqual(br.body.poff_count, 2)        # Try again sent the file and restarted again, not only re-read
        self.assertEqual(br.body.card["/EXT0/PARAM.CUR"][MAP.by_name["iso"].offset], want)
        self.assertIn("/EXT0/PARAM.OK", br.body.card)

    def test_different_settings_on_the_camera_then_keep_changes(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/camera")
        want = self._menu_with_change(pg)
        wb = MAP.by_name["white_balance"]
        cur = br.body.card["/EXT0/PARAM.CUR"]
        new_wb = other_value("white_balance", cur)
        br.body.ram[wb.offset] = new_wb  # changed on the camera's body meanwhile: the live block only, not PARAM.CUR
        pg.click("[data-testid=pending-bar] >> text=Apply to camera")
        self._status(pg, "WHITE BALANCE was changed on the camera\\.", 30, "the different-settings stop")
        self.text(pg, "Or connect the first GR and apply there.")
        self.assertEqual(br.body.poff_count, 0)
        self.assertNotIn("/EXT0/PARAM.DAT", br.body.card)  # nothing sent
        pg.get_by_role("button", name="Apply to this GR").click()  # reads it, keeps the edit on top, applies
        self._apply_done(pg)
        cur = br.body.card["/EXT0/PARAM.CUR"]
        self.assertEqual(cur[MAP.by_name["iso"].offset], want)
        self.assertEqual(cur[wb.offset], new_wb)        # the camera's own change was kept, not rolled back

    def test_restore_backup_and_raw_only_restore(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/backup")
        self.wait(pg, "() => !!document.querySelector('[data-testid=backup]')", 20, "the backup button")
        with pg.expect_download() as dl:
            pg.click("[data-testid=backup]")
        backup = Path(dl.value.path()).read_bytes()
        self.assertEqual(backup, as_read(br.body))  # read first: what the camera holds now
        self.assertEqual(dl.value.suggested_filename, f"GR-settings-{time.strftime('%Y-%m-%d')}.dat")
        self.text(pg, f"Saved as {dl.value.suggested_filename}.")
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "GR-settings-2026-11-08.dat"
        # 1. a backup with ISO changed: the ISO row is confirmed and PARAM.CUR holds it
        iso = MAP.by_name["iso"]
        want = other_value("iso", backup)
        tmp.write_bytes(with_byte(backup, iso.offset, want))
        pg.set_input_files("[data-testid=restore-file]", str(tmp))
        pg.get_by_role("button", name="Restore to camera").click()
        self._apply_done(pg, 60, "restored")
        self.assertEqual(pg.eval_on_selector("[data-result=iso] .icon", "e => e.getAttribute('aria-label')"), "Confirmed")
        self.assertEqual(br.body.card["/EXT0/PARAM.CUR"][iso.offset], want)
        pg.click("[data-testid=apply-sheet] >> text=Done")
        # 2. a change to an unmapped byte only: no setting rows, judged by the whole-block rule
        cur = br.body.card["/EXT0/PARAM.CUR"]
        tmp.write_bytes(with_byte(cur, 5, cur[5] ^ 0x5A))
        pg.set_input_files("[data-testid=restore-file]", str(tmp))
        pg.get_by_role("button", name="Restore to camera").click()
        self._apply_done(pg, 60, "raw-only restore confirmed by the block rule")
        self.assertEqual(pg.locator("[data-testid=apply-sheet] [data-result]").count(), 0)
        self.text(pg, "Every setting matches what was sent.")
        self.assertEqual(br.body.card["/EXT0/PARAM.CUR"][5], cur[5] ^ 0x5A)
        pg.click("[data-testid=apply-sheet] >> text=Done")
        # 3. the same with a camera that does not import: never "done" with nothing checked. PARAM.CUR still carries
        # import status 1 from the last import, so only the whole-block compare can catch it.
        br.body._boot = lambda: br.body.card.pop("/EXT0/PARAM.DAT", None)
        cur = br.body.card["/EXT0/PARAM.CUR"]
        tmp.write_bytes(with_byte(cur, 6, cur[6] ^ 0x33))
        pg.set_input_files("[data-testid=restore-file]", str(tmp))
        pg.get_by_role("button", name="Restore to camera").click()
        self._status(pg, "do not match what was sent", 60, "the raw-only mismatch")
        self.assertNotIn("Applied.", pg.inner_text("[data-testid=apply-sheet]"))

    def test_advanced_raw_edit_applies_and_is_checked(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/advanced")
        self.wait(pg, "() => !!document.querySelector('input[aria-label=\"Search fields\"]')", 20, "Advanced")
        pg.fill("input[aria-label='Search fields']", "legacy_03a")
        f = MAP.by_name["legacy_03a"]
        cur = br.body.card["/EXT0/PARAM.CUR"]
        want = (cur[f.offset] + 1) & 0xFF
        pg.fill("input[aria-label=legacy_03a]", str(want))
        pg.press("input[aria-label=legacy_03a]", "Enter")
        self.wait(pg, "() => !!document.querySelector('[data-testid=pending-bar]')", 5, "the pending bar")
        pg.click("[data-testid=pending-bar] >> text=Apply to camera")
        self._apply_done(pg)
        self.assertEqual(br.body.card["/EXT0/PARAM.CUR"][f.offset], want)

    def test_copy_to_another_gr_with_guard(self):
        br = self.bridge()
        main = br.body.card["/EXT0/PARAM.CUR"]
        spare = koma_cur(SPARE_CUR.read_bytes())
        pg = self.page(br, "#/settings/copy")
        main_ram = bytes(br.body.ram)
        pg.get_by_role("button", name="Read the connected camera").click()
        self.wait(pg, "() => /Koma/.test(document.querySelector('[data-testid=copy-from]')?.innerText || '')", 15, "the first GR read")
        pg.get_by_role("button", name="Next").click()
        self.text(pg, "Unplug this GR. Turn the other one on, then plug it in.")
        self.assertEqual(pg.get_by_role("button", name="Read the connected camera").count(), 0)  # not on the first GR
        br.body.unplug()                                   # the cable comes out; the app and the first GR stay
        self.wait(pg, "() => !!document.querySelector('[data-testid=copy-waiting]')", 15, "waiting for the other GR")
        set_camera(br.body, spare)                         # the other GR on the cable
        br.body.plug()
        self.wait(pg, "() => !!document.querySelector('[data-testid=copy-to]')", 20, "the second GR read by itself")
        pg.get_by_role("button", name="Next").click()
        self.text(pg, "Kept on the second GR")
        self.assertNotIn("RECIPE", pg.inner_text("main").upper())
        # the first GR back on the cable: Send stops, nothing sent
        br.body.card["/EXT0/PARAM.CUR"], br.body.ram = main, bytearray(main_ram)
        pg.get_by_role("button", name="Send to the connected GR").click()
        self._status(pg, "not the one chosen under To", 30, "the wrong-camera stop")
        self.assertEqual(br.body.poff_count, 0)
        pg.click("[data-testid=apply-sheet] >> text=Close")
        set_camera(br.body, spare)
        pg.get_by_role("button", name="Send to the connected GR").click()
        self._apply_done(pg, 60, "copied")
        pg.click("[data-testid=apply-sheet] >> text=Done")
        self.text(pg, "Copied. The second GR matches.")
        self.assertEqual(pg.get_by_role("button", name="Copy again").count(), 1)
        cur = br.body.card["/EXT0/PARAM.CUR"]
        for name in ("iso", "white_balance", "photometry", "exposure_comp"):
            f = MAP.by_name[name]
            self.assertEqual(cur[f.offset:f.offset + f.size], main[f.offset:f.offset + f.size], name)
        self.assertEqual(cur[0x0D0:0x0D4], spare[0x0D0:0x0D4])  # the second GR's own lens data stays
        self.assertEqual(cur[0x070:0x074], spare[0x070:0x074])  # and its file numbers

    def test_koma_mode_layout_follows_the_camera(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/mode")
        self.wait(pg, "() => !!document.querySelector('[data-k=\"menu_layout:0\"]')", 20, "Koma mode")
        self.assertEqual(pg.locator("legend").all_inner_texts()[:2], ["MENU LAYOUT", "KOMA FEATURES"])  # each name once
        pg.check("[data-k='menu_layout:0']", force=True)
        pg.click("[data-testid=pending-bar] >> text=Apply to camera")
        self._apply_done(pg)
        self.assertEqual(br.body.card["/EXT0/PARAM.CUR"][0x8BB], 0)
        pg.click("[data-testid=apply-sheet] >> text=Done")
        pg.goto(br.url + "#/settings/camera")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 5", 20, "the camera menu")
        tabs = pg.eval_on_selector_all("[role=tab]", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(tabs[:5], ["STILL", "KOMA", "PLAY", "CUSTOM", "SETUP"])  # MENU LAYOUT = 5-TAB
        # STOCK+KOMA
        set_camera(br.body, with_byte(br.body.card["/EXT0/PARAM.CUR"], 0x8BB, 1))
        pg.reload()
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 5", 20, "the camera menu")
        tabs = pg.eval_on_selector_all("[role=tab]", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(tabs[:3], ["STILL", "KOMA", "SETUP"])

    def test_koma_mode_on_a_stock_camera_offers_no_apply(self):
        br = self.bridge(koma=False)
        pg = self.page(br, "#/settings/mode")
        self.wait(pg, "() => !!document.querySelector('[data-testid=needs-koma]')", 20, "the needs-Koma notice")
        self.assertTrue(pg.eval_on_selector("[data-k='menu_layout:2']", "e => e.disabled"))
        self.assertEqual(pg.locator("[data-testid=pending-bar]").count(), 0)
        # GR II when the setting cannot be read
        pg.goto(br.url + "#/settings/camera")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 5", 20, "the camera menu")
        self.assertEqual(pg.eval_on_selector_all("[role=tab]", "els => els.map(e => e.textContent.trim())")[:3],
                         ["SHOOTING", "KEY CUST.", "SETUP"])

    def test_lands_on_overview_and_runs_nothing_on_the_camera(self):
        """The app opens on Overview, the camera at rest. No live view, no remote control, no
        restart, no writes: only GET /status, /settings-map.json, /settings, /ram reads, /photos and one thumbnail."""
        br = self.bridge()
        ctx = self.browser.new_context(viewport=VIEW, color_scheme="dark")
        self.contexts.append(ctx)
        calls = []
        ctx.on("request", lambda r: calls.append((r.method, r.url.split(str(br.port), 1)[1].split("?")[0])))
        ctx.add_init_script(SETUP_SEEN)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        pg.goto(br.url)
        self.wait(pg, "() => !!document.querySelector('[data-testid=overview]')", 15, "Overview as the landing page")
        self.wait(pg, "() => document.querySelector('[data-testid=overview-look] a')?.firstChild?.nodeValue === 'MONO'", 20, "the current look")
        self.wait(pg, "() => document.querySelector('[data-testid=last-photo]')?.naturalWidth > 0", 30, "the last photo")
        self.assertIn("GR DIGITAL", pg.inner_text("[data-testid=camera-state] >> visible=true"))  # the nav carries identity
        self.assertIn("2.40", pg.inner_text("[data-testid=overview]"))                         # the page, the firmware
        pg.wait_for_timeout(2500)  # a few status polls more: still nothing started
        bad = [c for c in calls if c[1].startswith(("/live", "/tether", "/keys", "/dial", "/reboot"))
               or (c[0] != "GET" and c[1] not in ("",))]
        self.assertEqual(bad, [], "Overview called the camera")
        self.assertEqual((br.body.remote, br.body.key_log, br.body.poff_count), (0, [], 0))
        self.assertEqual(br.body.kset_log, [])
        # the quiet ways on: actions look like links, and the wordmark comes back here
        pg.get_by_role("link", name="Start live view").click()
        self.wait(pg, "() => !!document.querySelector('[data-testid=shutter]')", 10, "Shoot")
        pg.click("[data-testid=home-link] >> visible=true")
        self.wait(pg, "() => !!document.querySelector('[data-testid=overview]')", 10, "back on Overview")
        self.assertEqual(pg.locator("nav[aria-label=Main] a[data-nav][aria-current=page]").count(), 0)  # not a nav item

    def test_legacy_link_and_old_worker_never_caches_the_app(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/advanced")    # the app links no other version
        self.wait(pg, "() => !!document.querySelector('[data-testid=keyboard]')", 15, "Advanced")
        self.assertEqual(pg.locator("[data-testid=update-legacy]").count(), 0)
        pg.goto(br.url + "legacy")                          # a checkout still serves it; an installed Koma does not
        self.wait(pg, "() => document.title === 'Koma Setup'", 15, "the old page")
        self.assertTrue(pg.evaluate("() => navigator.serviceWorker.ready.then(() => true)"))
        self.wait(pg, "async () => (await caches.keys()).includes('koma-setup-v2') && "
                      "(await (await caches.open('koma-setup-v2')).keys()).length >= 2", 15, "the old page's offline cache")
        cached = pg.evaluate("async () => (await (await caches.open('koma-setup-v2')).keys()).map(r => new URL(r.url).pathname)")
        self.assertIn("/legacy", cached)
        self.assertNotIn("/", cached)                       # / is the app: never served from the old page's cache
        self.offline = True
        pg.context.set_offline(True)
        pg.goto(br.url + "legacy")                          # the old page still opens offline
        self.assertEqual(pg.title(), "Koma Setup")
        pg.context.set_offline(False)
        self.offline = False


if __name__ == "__main__":
    unittest.main()
