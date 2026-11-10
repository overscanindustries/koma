#!/usr/bin/env python3
"""End-to-end: the design system and the finish pass (shell, Overview, Connect, Shoot, Settings states)
in headless Chrome against komabridge + the SIMULATED camera. Same pattern as test_e2e_app.py; every test fails on an
uncaught page error. The simulated card holds real photographs where a test needs EXIF
(tools/komaapp/design/samples.py).

Everything here is MOCK: it proves the app and the bridge agree, not that a camera does any of it.

    (cd tools/komaapp && npm run build)
    python3 -m pytest tools/komaset/web/test_e2e_polish.py -v
    KOMA_E2E_VIEWPORT=phone python3 -m pytest tools/komaset/web/test_e2e_polish.py -v

Bridges listen on 8890..8969 (this file's test range; design/screens.py uses 8871..8880).
"""
from __future__ import annotations

import itertools
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE = HERE.parent / "bridge"
DESIGN = HERE.parents[1] / "komaapp" / "design"
sys.path.insert(0, str(BRIDGE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(DESIGN))

import komabridge as KB  # noqa: E402
import komaset  # noqa: E402
from mockcard import MockBody, FOLDER, block_checksum  # noqa: E402

MAP = komaset.Map(HERE.parent / "settings-map.json")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

DESKTOP = {"width": 1280, "height": 860}
PHONE = {"width": 390, "height": 844}
VIEW = PHONE if os.environ.get("KOMA_E2E_VIEWPORT") == "phone" else DESKTOP
_P = tuple(int(x) for x in os.environ.get("KOMA_E2E_PORTS", "8890-8969").split("-"))   # KOMA_E2E_PORTS=9400-9420
PORTS = itertools.cycle(range(_P[0], _P[1] + 1))
SETUP_SEEN = ("try { if (!localStorage.getItem('koma.setup')) localStorage.setItem('koma.setup', JSON.stringify("
              "{v: 1, seen: true, active: false, done: true, plan: 'wizard', step: 0, sub: 0})); } catch (e) {}")


def free_port():
    for _ in range(90):
        port = next(PORTS)
        with socket.socket() as so:
            try:
                so.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port in 8890..8969")


class Bridge:
    def __init__(self, scale=0.25, plugged=True, koma=True, samples=False, photos=True, reboot_s=2.0, tag=None):
        self.body = MockBody(tether=True, reboot_s=reboot_s, koma=koma).hw_timings(scale)
        if tag is not None:  # a firmware tag as recorded on hardware (240K2, Rev024M), checksum fixed
            import mockcard as MC
            cur = bytearray(self.body.card["/EXT0/PARAM.CUR"])
            cur[MC.KSIO_TAG_OFF:MC.KSIO_TAG_OFF + 7] = tag.ljust(7, b"\0")
            cur[0xFFC:] = MC.block_checksum(cur)
            self.body.card["/EXT0/PARAM.CUR"] = bytes(cur)
        if samples:
            import samples as S
            S.use_samples(self.body)
        if not photos:
            for key in [k for k in self.body.card if k.startswith(FOLDER + "/")]:
                del self.body.card[key]
            self.body.folder_entries = []
            self.body.photo_dates.clear()
        if not plugged:
            self.body.unplug()
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.rearm_after_read = True
        self.server = KB.make_server(self.link, port=free_port())
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
    return None


# Elements allowed to carry the amber: the shutter, the live dot, the active nav item and
# tab, the running phase, the current step. Matched against the element (or its pseudo-element's host) class list.
AMBER_SCAN = r"""(allowed) => {
  const acc = getComputedStyle(document.documentElement).getPropertyValue('--acc').trim();
  const probe = document.createElement('i'); probe.style.color = acc; document.body.appendChild(probe);
  const rgb = getComputedStyle(probe).color; probe.remove();
  const bad = [];
  for (const el of document.querySelectorAll('body *')) {
    for (const pseudo of [null, '::before', '::after']) {
      const cs = getComputedStyle(el, pseudo);
      if (pseudo && (cs.content === 'none' || cs.content === 'normal')) continue;
      if (!pseudo && (cs.display === 'none' || cs.visibility === 'hidden')) continue;
      const hit = [cs.color, cs.backgroundColor, cs.borderTopColor, cs.borderLeftColor, cs.outlineColor]
        .some((c, i) => c === rgb && (i !== 2 && i !== 3 || parseFloat(i === 2 ? cs.borderTopWidth : cs.borderLeftWidth) > 0)
          && (i !== 4 || cs.outlineStyle !== 'none'));
      if (!hit) continue;
      const host = el.closest(allowed);
      if (!host) bad.push(el.tagName + '.' + (el.className && el.className.baseVal === undefined ? el.className : '') + (pseudo || ''));
    }
  }
  return bad.slice(0, 8);
}"""
AMBER_OK = ".sh, .lv, a[data-nav][aria-current], [role=tab][aria-selected=true], .subnav, .pl .now, .pl li.now, .dots li.on, .seg .live"

# What must never be on the surface outside Details or Advanced.
FILE_TEXT = r"R\d{7}\.JPG|RIMG\d{4}|PARAM\.(CUR|DAT|OK)|GR-settings-|\.dat\b|0x[0-9a-f]{3,}|\d+ fps|KB/s"


@unittest.skipIf(sync_playwright is None, "playwright not installed")
@unittest.skipIf(KB.app_index() is None, "the app is not built (cd tools/komaapp && npm run build)")
class PolishE2E(unittest.TestCase):
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
        self.bridges: list = []
        self.contexts = []
        self.errors: list[str] = []
        self.procs = []

    def tearDown(self):
        for c in self.contexts:
            c.close()
        for b in self.bridges:
            try:
                b.stop()
            except Exception:  # noqa: BLE001
                pass
        for p in self.procs:
            p.terminate()
            p.wait(10)
        self.assertEqual(self.errors, [], "page errors")

    def bridge(self, **kw) -> Bridge:
        b = Bridge(**kw)
        self.bridges.append(b)
        return b

    def page(self, br, hash_="", viewport=None, scheme="dark", reduced=None, init=""):
        ctx = self.browser.new_context(viewport=viewport or VIEW, color_scheme=scheme, reduced_motion=reduced or "no-preference")
        self.contexts.append(ctx)
        ctx.add_init_script("delete window.showSaveFilePicker;")
        # setup already seen: a first run opens setup on any camera state (onboard.svelte.js firstRunCheck)
        ctx.add_init_script(SETUP_SEEN)
        if init:
            ctx.add_init_script(init)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: self.errors.append("console: " + m.text)
              if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.goto(br.url + hash_)
        return pg

    def wait(self, pg, js, timeout_s=20, what=""):
        try:
            pg.wait_for_function(js, timeout=timeout_s * 1000, polling=200)
        except Exception as error:  # noqa: BLE001
            text = pg.evaluate("() => (document.querySelector('dialog[open]')?.innerText || '') + '\\n---\\n' + document.body.innerText.slice(0, 700)")
            raise AssertionError(f"timed out after {timeout_s} s waiting for {what or js}; page text:\n{text}") from error

    def text(self, pg, needle, timeout_s=15):
        try:
            pg.wait_for_function("t => document.body.innerText.includes(t)", arg=needle, timeout=timeout_s * 1000, polling=200)
        except Exception as error:  # noqa: BLE001
            raise AssertionError(f"timed out after {timeout_s} s waiting for {needle!r}; page text:\n"
                                 + pg.evaluate("() => document.body.innerText.slice(0, 700)")) from error

    def visible_text(self, pg, sel="main"):
        return pg.inner_text(sel)

    def amber_ok(self, pg, where):
        bad = pg.evaluate(AMBER_SCAN, AMBER_OK)
        self.assertEqual(bad, [], f"amber outside the allowed places on {where}")

    # ---------------------------------------------------------------- Overview
    def test_overview_is_a_rich_calm_home(self):
        """A real photo with time and exposure (EXIF), the look large, firmware and the real Koma version (1.0 from
        the firmware tag, not the store's header byte 1), one primary action, no file name, identity once."""
        br = self.bridge(samples=True)
        pg = self.page(br)
        self.wait(pg, "() => document.querySelector('[data-testid=last-photo]')?.naturalWidth > 0", 30, "the last photo")
        self.wait(pg, "() => document.querySelector('[data-testid=overview-look] a')?.firstChild?.nodeValue === 'MONO'", 20, "the look")
        self.wait(pg, "() => !!document.querySelector('[data-testid=overview-primary]')", 20, "the primary action")
        cap = pg.inner_text("[data-testid=last-caption]")
        # R0015720 (B&W): f/2.4, 1/3 s, ISO 400 (EXIF); the date too, as the photo was not taken today (when())
        self.assertRegex(cap, r"^Last photo · (\d{4}/\d\d/\d\d · )?\d\d:\d\d · F2\.4 1/3 ISO 400$")
        spec = pg.inner_text("[data-testid=overview-spec]")
        self.assertRegex(spec, r"Firmware\s+2\.40")
        self.assertRegex(spec, r"Koma\s+1\.0")
        self.assertEqual(pg.inner_text("[data-testid=spec-layout] a"), "GR II")             # the layout row links to Koma mode
        self.assertNotRegex(spec, r"Koma\s+1(?!\.)")
        self.assertEqual(pg.inner_text("[data-testid=overview-primary]"), "Start live view")
        self.assertEqual(pg.locator("main .btn.primary:visible").count(), 1)                  # one primary per view
        main = self.visible_text(pg)
        self.assertNotRegex(main, FILE_TEXT)                                                  # file names under Details only
        self.assertNotIn("GR DIGITAL", self.visible_text(pg, "[data-testid=overview]"))     # the nav carries identity
        self.assertEqual(pg.locator("[data-testid=camera-state]:visible").count(), 1)
        self.assertEqual(pg.locator("header.status").count(), 0)                              # no header band
        # the photo is never drawn above its native size
        w = pg.evaluate("() => { const i = document.querySelector('[data-testid=last-photo]'); return [i.getBoundingClientRect().width, i.naturalWidth]; }")
        self.assertLessEqual(w[0], w[1] + 0.5)
        pg.click("summary:has-text('Details')")
        self.assertIn("R0015720.JPG", pg.inner_text("[data-testid=overview-details]"))
        self.assertIn("240K1.0", pg.inner_text("[data-testid=overview-details]"))
        self.assertEqual(pg.locator("nav[aria-label='Go to']").count(), 0)                  # the nav is not repeated
        self.assertEqual(pg.inner_text("[data-testid=overview-photos]"), "8 photos on the card")  # design/samples: 8 frames
        if VIEW is PHONE:
            self.assertIn("Project Koma.", pg.inner_text("main"))
        else:  # 7 / 5: one top-aligned column (look, facts, action, count) that starts on the photo's top edge
            g = pg.evaluate("""() => { const r = s => document.querySelector(s).getBoundingClientRect();
              return [r('.look').top - r('[data-testid=last-photo]').top, r('[data-testid=overview-spec]').top - r('.look').bottom,
                      r('[data-testid=overview-photos]').top - r('[data-testid=overview-primary]').bottom]; }""")
            self.assertLess(abs(g[0]), 12, g)
            s7 = pg.evaluate("() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--s7'))")
            self.assertAlmostEqual(g[1], s7, delta=2)                                         # --s7 under the look
            self.assertGreater(g[2], 0, g)
        self.amber_ok(pg, "Overview")

    def test_overview_states_empty_stock_busy(self):
        empty = self.bridge(photos=False)
        pg = self.page(empty)
        self.wait(pg, "() => !!document.querySelector('[data-testid=overview-empty]')", 30, "the empty state")
        self.assertIn("The card holds no photos.", pg.inner_text("[data-testid=overview-empty]"))
        self.assertEqual(pg.locator("[data-testid=overview] figure").count(), 0)              # no frame, no figure at rest
        stock = self.bridge(koma=False)
        pg2 = self.page(stock)
        self.wait(pg2, "() => /Not installed/.test(document.querySelector('[data-testid=overview-spec]')?.innerText || '')", 30, "Koma not installed")
        self.assertEqual(pg2.inner_text("[data-testid=overview-primary]"), "Set up Koma")     # setup, not live view
        busy = self.bridge()
        pg3 = self.page(busy, "#/shoot")
        self.wait(pg3, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        pg4 = self.page(busy)                                                                 # a second window while Shoot holds the camera
        # the line names the real holder: remote control while Shoot wakes, live view once its stream runs
        self.wait(pg4, "() => /(Remote control|Live view) is on\\./.test(document.querySelector('[data-testid=overview-busy]')?.innerText || '')",
                  30, "the busy line")

    def test_overview_koma_version_only_from_a_release_tag(self):
        """Tags recorded on hardware name no release: 240K2 (an earlier build, VERSIONING.md) reads "An earlier Koma
        build" and Rev024M (the loader's own tag) "Installed", the tag under Details; only 240K1.0 form names one."""
        for tag, text in ((b"240K2", "An earlier Koma build"), (b"Rev024M", "Installed")):
            br = self.bridge(tag=tag)
            pg = self.page(br)
            self.wait(pg, "() => /Koma\\s+" + text + "/.test(document.querySelector('[data-testid=overview-spec]')?.innerText || '')",
                      30, "Koma " + text)
            self.assertNotRegex(pg.inner_text("[data-testid=overview-spec]"), r"Koma\s+\d")
            pg.click("summary:has-text('Details')")
            self.assertIn(tag.decode(), pg.inner_text("[data-testid=overview-details]"))

    def test_overview_keeps_the_photo_while_live_view_holds_the_card(self):
        """The photo already shown stays; the busy line sits under it; the page asks again until the card is free."""
        br = self.bridge(samples=True)
        pg = self.page(br)
        self.wait(pg, "() => document.querySelector('[data-testid=last-photo]')?.naturalWidth > 0", 30, "the last photo")
        shoot = self.page(br, "#/shoot")
        self.wait(shoot, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        pg.click("[data-testid=overview-photos]")                                           # leave and come back
        pg.click("[data-testid=home-link] >> visible=true")
        self.wait(pg, "() => !!document.querySelector('[data-testid=overview-busy]')", 20, "the busy line")
        self.assertEqual(pg.locator("[data-testid=last-photo]").count(), 1)
        shoot.context.close()
        self.contexts.remove(shoot.context)
        self.wait(pg, "() => !document.querySelector('[data-testid=overview-busy]')", 40, "the card free again")
        self.assertEqual(pg.locator("[data-testid=last-photo]").count(), 1)

    # ---------------------------------------------------------------- shell
    def test_nav_status_is_identical_on_every_screen(self):
        br = self.bridge()
        seen = set()
        for h in ("", "#/shoot", "#/photos", "#/looks", "#/webcam", "#/settings/camera", "#/settings/backup", "#/settings/advanced"):
            pg = self.page(br, h)
            self.wait(pg, "() => /GR DIGITAL/.test(document.querySelector('[data-testid=camera-state]')?.innerText || '')", 15, "NavStatus")
            self.assertEqual(pg.locator("[data-testid=camera-state]").count(), 1, h)
            seen.add(pg.eval_on_selector("[data-testid=camera-state]", "e => e.textContent.replace(/\\s+/g, ' ').trim()"))
            self.assertEqual(pg.locator("header.status").count(), 0, h)
            pg.context.close()
            self.contexts.remove(pg.context)
        self.assertEqual(len(seen), 1, seen)
        self.assertIn("Connected", seen.pop())

    def test_amber_only_where_it_means_live(self):
        br = self.bridge()
        pg = self.page(br, "#/shoot")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        self.wait(pg, "() => !!document.querySelector('[data-testid=live] .lv')", 30, "the live dot")
        self.amber_ok(pg, "Shoot")
        pg.goto(br.url + "#/settings/camera")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 5", 20, "the camera menu")
        self.amber_ok(pg, "Camera menu")
        pg.goto(br.url + "#/settings/copy")
        self.text(pg, "The GR to copy from")
        self.amber_ok(pg, "Copy")

    # ---------------------------------------------------------------- Connect and Offline
    def test_connect_ladder_only_after_twenty_seconds(self):
        off = self.bridge(plugged=False)
        ctx = self.browser.new_context(viewport=VIEW)
        self.contexts.append(ctx)
        ctx.add_init_script(SETUP_SEEN)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        pg.clock.install()
        pg.goto(off.url)
        self.text(pg, "Looking for the camera.")
        self.text(pg, "Plug in the USB cable")
        self.assertNotIn("Still looking", pg.inner_text("body"))                              # never a wall of tips at rest
        pg.clock.fast_forward(19000)
        self.assertNotIn("Still looking", pg.inner_text("body"))
        pg.clock.fast_forward(2000)
        self.text(pg, "Still looking")
        pg.click("text=Still looking")
        self.text(pg, "Plug it into this computer directly, not through a hub.")
        self.assertRegex(pg.inner_text("[data-testid=connect-status]"), r"Looking for the camera · 0:2\d")  # past 10 s: no period, · m:ss

    def test_offline_full_screen_then_back(self):
        port = free_port()
        cmd = [sys.executable, str(BRIDGE / "komabridge.py"), "--mock", "--mock-tether", "--mock-koma", "--quiet", "--port", str(port)]

        def spawn():
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.procs.append(proc)
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), 0.2).close()
                    return proc
                except OSError:
                    time.sleep(0.1)
            raise AssertionError("bridge did not start")
        proc = spawn()
        br = type("B", (), {"url": f"http://127.0.0.1:{port}/"})()
        pg = self.page(br)
        self.wait(pg, "() => !!document.querySelector('[data-testid=overview]')", 20, "Overview")
        proc.terminate()
        proc.wait(10)
        self.procs.remove(proc)
        self.wait(pg, "() => !!document.querySelector('[data-testid=offline]')", 20, "the offline screen")
        self.assertIn("Koma is not running on this computer.", pg.inner_text("[data-testid=offline]"))
        self.assertRegex(pg.inner_text("[data-testid=offline]"),                             # how to start it, per platform
                         r"Open Koma again from Applications\.|Open Koma again from the Start menu\.|Start Koma again\.")
        self.assertNotIn("--real", pg.inner_text("body"))                                     # no command on a user surface
        self.assertEqual(pg.locator("nav[aria-label=Main]").count(), 0)                      # no nav
        self.assertIn("KOMA", pg.inner_text("body"))                                          # the wordmark
        spawn()
        self.wait(pg, "() => !!document.querySelector('nav[aria-label=Main]')", 30, "the app again")

    # ---------------------------------------------------------------- Shoot
    def test_shoot_strip_nothing_over_the_picture_and_camera_lost(self):
        br = self.bridge()
        pg = self.page(br, "#/shoot")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        self.wait(pg, "() => !!document.querySelector('[data-testid=live] .lv')", 30, "frames")
        strip = pg.inner_text(".strip")
        for part in ("EV", "ISO", "WB", "MONO"):
            self.assertIn(part, strip)
        self.assertEqual(pg.inner_text("[data-testid=live]").strip().upper(), "LIVE")          # only the live indicator
        box = pg.eval_on_selector("[data-testid=live] .frame", "e => e.getBoundingClientRect().toJSON()")
        self.assertLessEqual(box["width"], 640.5)                                             # the 640 x 480 cap
        self.assertAlmostEqual(box["width"] / box["height"], 4 / 3, places=1)
        self.assertNotRegex(self.visible_text(pg), FILE_TEXT)                                 # fps and KB/s under Details
        br.body.unplug()
        self.wait(pg, "() => !!document.querySelector('[data-testid=shoot-lost]')", 20, "the camera-lost line")
        self.assertIn("Camera disconnected. Turn it on, then reconnect USB.", pg.inner_text("[data-testid=shoot-lost]"))
        self.assertEqual(pg.locator("nav[aria-label=Main]").count(), 1)                      # the screen stays
        # the held frame carries its own dimming (img.held at .6); the frame itself is not dimmed twice
        self.wait(pg, "() => { const e = document.querySelector('[data-testid=live] img.held'); return !!e && getComputedStyle(e).opacity === '0.6'; }", 5,
                  "the held frame dimmed")
        self.assertTrue(pg.evaluate("() => !!document.querySelector('[data-testid=live] img.held')?.src"))
        br.body.plug()
        self.wait(pg, "() => !document.querySelector('[data-testid=shoot-lost]')", 30, "the camera back")
        self.wait(pg, "() => !!document.querySelector('[data-testid=live] .lv')", 40, "live view again")

    def test_interval_ring_on_the_shutter(self):
        br = self.bridge()
        pg = self.page(br, "#/shoot")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        pg.click("[data-testid=interval-panel]")
        self.assertNotIn("·", pg.inner_text("#pp-interval"))                                 # two rows, no dangling joiner
        pg.get_by_label("Every").fill("2")                                                   # each field has its own label
        pg.get_by_label("Photos").fill("3")
        pg.click("[data-testid=iv-btn]")
        self.wait(pg, "() => document.querySelector('[data-testid=shutter]').classList.contains('ring')", 10, "the interval ring")
        self.assertEqual(pg.inner_text("[data-testid=iv-btn]"), "Stop")                       # Stop always visible
        self.wait(pg, "() => /Photo 3 of 3/.test(document.querySelector('[data-testid=iv-status]')?.innerText || '') && "
                      "/Done/.test(document.querySelector('[data-testid=iv-status]').innerText)", 40, "interval done")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').classList.contains('ring')", 10, "the ring gone")

    # ---------------------------------------------------------------- Settings
    def test_apply_phases_count_and_undo_last(self):
        br = self.bridge(reboot_s=14.0)
        pg = self.page(br, "#/settings/camera", init="window.KOMA_WAIT_S = 60;")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 10", 20, "the camera menu")
        other = "s => [...s.options].map(o => o.value).find(v => v !== s.value)"
        pg.click("[data-row=iso] button.head")
        pg.select_option("[data-row=iso] select", pg.eval_on_selector("[data-row=iso] select", other))
        pg.click("[data-row=white_balance] button.head")
        pg.select_option("[data-row=white_balance] select", pg.eval_on_selector("[data-row=white_balance] select", other))
        self.wait(pg, "() => /2\\s*changes/i.test(document.querySelector('[data-testid=pending-bar]')?.innerText || '')", 5, "2 changes")
        pg.locator("body").click(position={"x": 5, "y": 5})
        pg.keyboard.press("ControlOrMeta+z")                                                  # undo: the last edit only
        self.wait(pg, "() => /1\\s*change\\b/i.test(document.querySelector('[data-testid=pending-bar]')?.innerText || '')", 5, "1 change after undo")
        self.assertEqual(pg.eval_on_selector("[data-row=white_balance] select", "s => s.closest('[data-row]').classList.contains('changed')"), False)
        self.assertTrue(pg.eval_on_selector("[data-row=iso]", "r => r.classList.contains('changed')"))
        pg.click("[data-testid=pending-bar] >> text=Apply to camera")
        self.wait(pg, "() => /Waiting for the camera/.test(document.querySelector('[data-testid=apply-sheet] [aria-current=step]')?.innerText || '')", 30, "waiting")
        self.wait(pg, "() => /0:1\\d/.test(document.querySelector('[data-testid=apply-sheet] [aria-current=step]')?.innerText || '')", 20, "the elapsed counter")
        self.assertEqual(pg.locator("[data-testid=apply-status]").count(), 0)                 # the running phase is the state: no second line
        self.assertIn("Waiting for the camera", pg.inner_text("[data-testid=camera-state]"))
        self.wait(pg, "() => /Applied\\. Every setting matches what was sent\\./.test(document.querySelector('[data-testid=apply-status]')?.innerText || '')", 60, "applied")
        self.amber_ok(pg, "the Apply sheet, done")

    def test_no_pill_switch_and_on_off_segments(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/camera")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 10", 20, "the camera menu")
        self.assertEqual(pg.locator(".switch").count(), 0)
        self.assertEqual(pg.locator("[data-testid=changed-only] input").count(), 2)          # All | Changed
        pg.goto(br.url + "#/shoot")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        pg.click("[data-testid=remote-panel]")
        labels = pg.eval_on_selector_all("[aria-labelledby=osd-l] span", "s => s.map(x => x.textContent)")
        self.assertEqual(labels, ["OFF", "ON"])

    def test_backup_restore_states_effect(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/backup", init="")
        self.wait(pg, "() => !!document.querySelector('[data-testid=backup]')", 20, "the backup button")
        self.assertNotRegex(self.visible_text(pg), FILE_TEXT)
        import tempfile
        cur = bytearray(br.body.card["/EXT0/PARAM.CUR"])
        f = MAP.by_name["iso"]
        cur[f.offset] = next(int(v) for v in sorted(f.values, key=int) if int(v) != cur[f.offset])   # one setting: ISO
        cur[0xFFC:] = block_checksum(cur)
        tmp = Path(tempfile.mkdtemp()) / "GR-settings-test.dat"
        tmp.write_bytes(bytes(cur))
        pg.set_input_files("[data-testid=restore-file]", str(tmp))
        self.text(pg, "Writes 1 setting to this camera.")
        self.text(pg, "ISO SETTING")

    def test_appearance_and_theme_tokens(self):
        br = self.bridge()
        if VIEW is PHONE:
            pg = self.page(br, "#/settings", scheme="light")
            pg.check("[data-testid=theme-choice] input[value=dark]", force=True)
            self.wait(pg, "() => document.documentElement.dataset.theme === 'dark'", 5, "dark by choice")
            pg.check("[data-testid=theme-choice] input[value=system]", force=True)
            self.wait(pg, "() => document.documentElement.dataset.theme === 'light'", 5, "the system's light")
            self.assertEqual(pg.evaluate("() => localStorage.getItem('koma.theme')"), "")
        else:
            pg = self.page(br, "#/settings", scheme="dark")
            self.assertEqual(pg.locator("[data-testid=theme-toggle] svg").count(), 1)         # icon only, with a label
            self.assertTrue(pg.get_attribute("[data-testid=theme-toggle]", "aria-label"))
        bg = pg.evaluate("() => getComputedStyle(document.body).backgroundColor")
        self.assertIn(bg, ("rgb(22, 23, 24)", "rgb(244, 244, 241)"))                          # --bg, dark or paper

    # ---------------------------------------------------------------- art review 2
    def test_file_row_takes_a_file_dropped_anywhere(self):
        """The file chooser is a button and one sentence between two hairlines (no dashed box, no radius); a file
        dropped anywhere on the page is taken, and the row's rules turn ink while it is dragged over."""
        br = self.bridge()
        pg = self.page(br, "#/settings/backup")
        self.wait(pg, "() => !!document.querySelector('[data-testid=restore-file-row]')", 20, "the restore row")
        row = pg.eval_on_selector("[data-testid=restore-file-row]", """e => { const s = getComputedStyle(e);
            return [s.borderTopStyle, s.borderBottomStyle, s.borderLeftStyle, s.borderTopLeftRadius]; }""")
        self.assertEqual(row, ["solid", "solid", "none", "0px"])
        self.assertEqual(pg.inner_text("[data-testid=restore-file-row] button"), "Choose a backup")
        self.assertEqual(pg.locator("main .btn.primary:visible").count(), 1)                  # Save backup; the chooser is quiet
        cur = bytearray(br.body.card["/EXT0/PARAM.CUR"])
        f = MAP.by_name["iso"]
        cur[f.offset] = next(int(v) for v in sorted(f.values, key=int) if int(v) != cur[f.offset])
        cur[0xFFC:] = block_checksum(cur)
        over = pg.evaluate("""async (bytes) => {
            const dt = new DataTransfer();
            dt.items.add(new File([new Uint8Array(bytes)], 'GR-settings-2026-11-08.dat'));
            const fire = t => document.querySelector('h1').dispatchEvent(new DragEvent(t, {dataTransfer: dt, bubbles: true, cancelable: true}));
            fire('dragover');
            await new Promise(r => setTimeout(r, 40));
            const over = document.querySelector('[data-testid=restore-file-row]').classList.contains('over');
            fire('drop');
            return over;
        }""", list(cur))
        self.assertTrue(over, "the row marks the drag")
        self.text(pg, "Writes 1 setting to this camera.")
        self.assertIn("saved as a backup", pg.inner_text("main"))
        self.assertNotRegex(self.visible_text(pg), FILE_TEXT)

    def test_copy_anyway_is_a_quiet_action(self):
        """Two Koma versions: the copy is held with the reason, and Copy anyway is a button on that line (no checkbox)."""
        br = self.bridge()
        pg = self.page(br, "#/settings/copy")
        pg.get_by_role("button", name="Read the connected camera").click()
        self.wait(pg, "() => !!document.querySelector('[data-testid=copy-from]')", 15, "the first GR read")
        self.assertNotIn("Or use", pg.inner_text("main"))
        pg.get_by_role("button", name="Next").click()
        other = bytearray(br.body.card["/EXT0/PARAM.CUR"])
        other[0x8B4] = (other[0x8B4] + 1) & 0xFF                                               # another Koma version
        f = MAP.by_name["iso"]
        other[f.offset] = next(int(v) for v in sorted(f.values, key=int) if int(v) != other[f.offset])
        other[0xFFC:] = block_checksum(other)
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "PARAM.CUR"
        tmp.write_bytes(bytes(other))
        pg.set_input_files("[data-testid=copy-to-file]", str(tmp))
        self.wait(pg, "() => !!document.querySelector('[data-testid=copy-to]')", 15, "the second GR from its file")
        pg.get_by_role("button", name="Next").click()
        self.wait(pg, "() => !!document.querySelector('[data-testid=copy-anyway]')", 10, "Copy anyway")
        self.assertEqual(pg.locator("main input[type=checkbox]").count(), 0)
        save = pg.get_by_role("button", name="Save to card")
        self.assertTrue(save.is_disabled())
        pg.click("[data-testid=copy-anyway]")
        self.assertFalse(save.is_disabled())
        self.assertEqual(pg.locator("[data-testid=copy-anyway]").count(), 0)
        widths = pg.evaluate("() => [document.querySelector('.dots').getBoundingClientRect().right, document.querySelector('.step').getBoundingClientRect().right]")
        self.assertLess(abs(widths[0] - widths[1]), 1, widths)                                 # one right edge

    def test_review_names_raw_edits_without_a_count(self):
        """An Advanced edit shows in Review as one line: no byte count, no engineering label with an offset."""
        br = self.bridge()
        pg = self.page(br, "#/settings/advanced")
        self.wait(pg, "() => !!document.querySelector('input[aria-label=\"Search fields\"]')", 20, "Advanced")
        pg.fill("input[aria-label='Search fields']", "legacy_03a")
        f = MAP.by_name["legacy_03a"]
        pg.fill("input[aria-label=legacy_03a]", str((br.body.card["/EXT0/PARAM.CUR"][f.offset] + 1) & 0xFF))
        pg.press("input[aria-label=legacy_03a]", "Enter")
        self.wait(pg, "() => !!document.querySelector('[data-testid=pending-bar]')", 5, "the pending bar")
        self.assertRegex(pg.inner_text("[data-testid=pending-bar]"), r"(?i)1\s*change\b")   # one line for every raw edit
        pg.click("[data-testid=pending-bar] >> text=Review")
        self.text(pg, "Raw edits made in Advanced are included.")
        sheet = pg.inner_text("[data-testid=review-sheet]")
        self.assertNotRegex(sheet, r"\bbytes?\b|0x[0-9A-Fa-f]+|LEGACY")

    def test_nav_status_names_a_drop_in_words(self):
        """Not connected, the word shows on every layout (on a phone after SIM); connected on a phone, the green dot
        stands for it and the word is for screen readers only."""
        off = self.bridge(plugged=False)
        pg = self.page(off, "#/card")
        self.text(pg, "Choose the settings file")
        pg.set_input_files("[data-testid=card-file]", str(HERE / "testdata" / "demo-main.cur"))
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 10", 10, "the camera menu on the file")
        w = pg.eval_on_selector("[data-testid=camera-state] .word", "e => [e.textContent, e.getBoundingClientRect().width]")
        self.assertEqual(w[0], "Not connected")
        self.assertGreater(w[1], 20)
        if VIEW is PHONE:
            br = self.bridge()
            pg2 = self.page(br, "#/settings")
            self.wait(pg2, "() => /Connected/.test(document.querySelector('[data-testid=camera-state]')?.textContent || '')", 15, "connected")
            self.assertLessEqual(pg2.eval_on_selector("[data-testid=camera-state] .word", "e => e.getBoundingClientRect().width"), 1)

    def test_remote_keys_at_camera_size_on_the_dpad_rows(self):
        """Key labels at --fs-cam (never the 11 px micro size inside a button); 40 px targets on a desktop, 44 on a
        phone; W/T, DISP and DELETE sit on the three rows of the arrow pad."""
        br = self.bridge()
        pg = self.page(br, "#/shoot")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        pg.click("[data-testid=remote-panel]")
        self.wait(pg, "() => document.querySelectorAll('[data-rkey]').length === 9", 10, "the keys")
        keys = pg.eval_on_selector_all("[data-rkey]", "ks => ks.map(k => { const r = k.getBoundingClientRect(); return [getComputedStyle(k).fontSize, r.width, r.height]; })")
        least = 44 if VIEW is PHONE else 40
        for size, w, h in keys:
            self.assertEqual(size, "13px")
            self.assertGreaterEqual(min(w, h), least - 0.5, keys)
        rows = pg.evaluate("""() => { const r = s => document.querySelector(s).getBoundingClientRect();
            return [r('[data-rkey="16"]').top - r('[data-rkey="11"]').top, r('[data-rkey="22"]').top - r('[data-rkey="14"]').top,
                    r('[data-rkey="20"]').bottom - r('[data-rkey="12"]').bottom]; }""")
        for d in rows:
            self.assertLess(abs(d), 1, rows)
        self.assertLessEqual(pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0)

    def test_keyboard_bindings_listed_once_in_advanced(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/advanced")
        self.wait(pg, "() => !!document.querySelector('[data-testid=keyboard]')", 20, "the keyboard list")
        keys = pg.inner_text("[data-testid=keyboard]")
        for k in ("Space", "Cmd+Z or Ctrl+Z", "Esc", "Arrow keys", "Enter"):
            self.assertIn(k, keys)
        pg.goto(br.url + "#/shoot")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]').disabled", 30, "remote control on")
        pg.click("[data-testid=remote-panel]")
        pg.click("#pp-remote summary")
        self.assertNotIn("Keyboard", pg.inner_text("#pp-remote"))                              # listed once, in Advanced

    def test_reduced_motion_stills_everything(self):
        off = self.bridge(plugged=False)
        pg = self.page(off, reduced="reduce")
        self.text(pg, "Looking for the camera.")
        self.assertEqual(pg.eval_on_selector("[data-testid=connect-status] .rule", "e => getComputedStyle(e).display"), "none")
        dur = pg.eval_on_selector(".link", "e => getComputedStyle(e).transitionDuration")
        self.assertTrue(all(float(x.strip().rstrip("s")) == 0 for x in dur.split(",")), dur)


class LooksCatalog(unittest.TestCase):
    """src/lib/looks.js, the one look array: 21 entries in naming.md's IMG SET order, ids and
    names as the settings map stores them, every line a fragment (no terminal period). No browser needed."""

    def test_looks_follow_naming_and_the_map(self):
        import re
        src = (HERE.parents[1] / "komaapp" / "src" / "lib" / "looks.js").read_text()
        rows = re.findall(r"\{id: (\d+), name: '([^']+)', group: '(\w+)', line: (?:\"([^\"]+)\"|'([^']+)')\}", src)
        self.assertEqual(len(rows), 21)
        naming = (HERE.parents[2] / "archive" / "design" / "naming.md").read_text()
        order = re.search(r"IMG SET list, 21 entries, GR III order: (.+?)\.\n", naming).group(1).split(", ")
        self.assertEqual([r[1] for r in rows], order)
        stored = MAP.by_name["recipe_rcp_active"].values
        for i, name, group, a, b in rows:
            self.assertEqual(stored[int(i)], name)
            line = a or b
            self.assertFalse(line.endswith("."), line)
        self.assertEqual([r[2] for r in rows].count("mono"), 11)
        self.assertEqual([r[2] for r in rows].count("colour"), 6)


if __name__ == "__main__":
    unittest.main()
