#!/usr/bin/env python3
"""End-to-end: setup (the first-run wizard), Install / Update Koma and Return to official firmware, in headless Chrome
against komabridge on the SIMULATED camera.

The browser writes the card in this computer's reader; here the reader is a folder in the origin private file system
(OPFS) handed to the app through showDirectoryPicker (onboard_fixtures.card_picker_script). The tests play the camera's
part between the steps (onboard_fixtures: after_koma_install, after_official), the way the hardware behaved where it was
observed. The write's minimum wait is shortened to KOMA_FLASH_MIN_S. Everything here is MOCK: no camera, no keyed
power-on, no flash.

    (cd tools/komaapp && npm run build)
    python3 -m pytest tools/komaset/web/test_e2e_onboard.py -v
    KOMA_E2E_VIEWPORT=phone python3 -m pytest tools/komaset/web/test_e2e_onboard.py -v

Bridges bind ports 8874..8973 (this file's test range; KOMA_E2E_PORTS=9211-9219 picks another). KOMA_APP_DIST serves a
build from another folder than tools/komaapp/dist. Needs the build, playwright and Chrome; skipped otherwise.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bridge"))
sys.path.insert(0, str(HERE))

import komabridge as KB  # noqa: E402
import komainstall as KI  # noqa: E402
if os.environ.get("KOMA_APP_DIST"):
    KB.APP_DIST = Path(os.environ["KOMA_APP_DIST"])
import onboard_fixtures as F  # noqa: E402
from mockcard import MockBody  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

DESKTOP = {"width": 1280, "height": 860}
PHONE = {"width": 390, "height": 844}
VIEW = PHONE if os.environ.get("KOMA_E2E_VIEWPORT") == "phone" else DESKTOP
STOCK_OK = KI._stock()[0] is not None   # Ricoh's 2.40 file on this computer: the firmware and package can be built
PACKAGE_SHA = KI.PACKAGE_SHA256          # the package the bridge rebuilds from its delta and the stock file
NO_STOCK = "Ricoh's 2.40 update file is not on this computer: the package cannot be built"
FLASH_MIN_S = 3                          # the write's minimum wait in these tests (150 s in the app)


PORTS = tuple(int(x) for x in os.environ.get("KOMA_E2E_PORTS", "8874-8973").split("-"))


def free_port() -> int:
    for port in range(PORTS[0], PORTS[1] + 1):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in {PORTS[0]}..{PORTS[1]}")


class Bridge:
    def __init__(self, body: MockBody):
        self.body = body.hw_timings(0.1)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.rearm_after_read = True
        port = free_port()
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

    def writes(self) -> list:
        """Every write command the simulated camera received."""
        out = []
        for t in self.body.transports:
            out += [c for c in t.sent if isinstance(c, str) and c.split(" ")[0] in ("-STOR", "-CCTL", "-KSET", "DELE", "-FMT")]
        return out


def launch(p):
    for kw in ({"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(**kw)
        except Exception:  # noqa: BLE001
            continue
    return None


def setup_state(**kw) -> dict:
    s = {"v": 1, "seen": True, "active": True, "done": False, "plan": "wizard", "step": 0, "sub": 0, "mode": None,
         "cam": None, "pkg": None, "backup": None, "card": None, "card2": None, "flashFrom": 0, "waitFrom": 0,
         "confirmed": None, "look": None, "before": None, "stopped": False}
    s.update(kw)
    return s


@unittest.skipIf(sync_playwright is None, "playwright not installed")
@unittest.skipIf(KB.app_index() is None, "the app is not built (cd tools/komaapp && npm run build)")
class OnboardE2E(unittest.TestCase):
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
        KI.forget_upload()
        self.bridges, self.contexts, self.errors = [], [], []

    def tearDown(self):
        for c in self.contexts:
            c.close()
        for b in self.bridges:
            try:
                b.stop()
            except Exception:  # noqa: BLE001
                pass
        self.assertEqual(self.errors, [], "page errors")

    def bridge(self, body) -> Bridge:
        b = Bridge(body)
        self.bridges.append(b)
        return b

    def page(self, br, hash_="", card=None, setup=None, scheme="dark", no_fsa=False, reduced=False):
        ctx = self.browser.new_context(viewport=VIEW, accept_downloads=True, color_scheme=scheme,
                                       reduced_motion="reduce" if reduced else "no-preference")
        self.contexts.append(ctx)
        ctx.add_init_script(f"window.KOMA_FLASH_MIN_S = {FLASH_MIN_S};")
        if no_fsa:   # a browser without the File System Access API (Safari, Firefox)
            ctx.add_init_script("delete window.showDirectoryPicker;")
        if setup is not None:
            ctx.add_init_script(f"localStorage.setItem('koma.setup', {json.dumps(json.dumps(setup))});")
        if card is not None:
            ctx.add_init_script(F.card_picker_script(card))
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        # "Failed to load resource" lines are the app's own handled answers (404 no_param_cur ...)
        pg.on("console", lambda m: self.errors.append(f"console: {m.text}")
              if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.goto(br.url + hash_)
        return pg

    def wait(self, pg, js, timeout_s=20, what=""):
        try:
            pg.wait_for_function(js, timeout=timeout_s * 1000, polling=200)
        except Exception as error:  # noqa: BLE001
            text = pg.evaluate("() => document.body.innerText.slice(0, 900)")
            raise AssertionError(f"timed out after {timeout_s} s waiting for {what or js}; page text:\n{text}") from error

    def step(self, pg, sid, timeout_s=20):
        self.wait(pg, f"() => document.querySelector('[data-step]')?.dataset.step === '{sid}'", timeout_s, "step " + sid)

    def lead(self, pg) -> str:
        return pg.inner_text("[data-testid=step-lead]").strip().replace("\xa0", " ")   # no-break spaces hold line breaks

    def next(self, pg):
        self.wait(pg, "() => { const b = document.querySelector('[data-testid=setup-next]'); return b && !b.disabled; }", 30, "Continue enabled")
        pg.click("[data-testid=setup-next]")

    def exit_shown(self, pg) -> bool:
        """Exit is offered: a withheld Exit keeps its place in the header but is hidden and disabled."""
        return pg.eval_on_selector("[data-testid=setup-exit]", "b => !b.disabled && getComputedStyle(b).visibility === 'visible'")

    def hidden_primary(self, pg) -> bool:
        """The primary keeps its place but is not shown and cannot act (no dead button)."""
        return pg.eval_on_selector("[data-testid=setup-next]", "b => b.disabled && getComputedStyle(b).visibility === 'hidden'")

    def card(self, pg) -> dict:
        return pg.evaluate(F.CARD_LISTING_JS)

    def no_hscroll(self, pg, where):
        sw = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(sw, 0, f"horizontal scroll on {where}")

    def choose_card(self, pg):
        self.wait(pg, "() => { const b = document.querySelector('[data-testid=setup-choose]'); return b && !b.disabled; }", 20, "the files ready")
        pg.click("[data-testid=setup-choose]")
        self.wait(pg, "() => /The card is ready/.test(document.querySelector('[data-testid=card-status]')?.innerText || '')", 30, "read back")

    def walk_presses(self, pg, official=False):
        """The eight screens of the keyed power-on. Stop here and Exit until MENU/OK; on MENU/OK, the point of no
        return: Stop here disabled with its reason, no Exit; while the camera writes, no Stop here, no Back, and 'The lamp
        has stopped' in view but disabled until the lamp has had time to stop. One fact, one place: on MENU/OK the reason,
        while the camera writes the boxed rule, and the write itself is the status line's phase. The index counts every
        screen of the step: an install has the card trip after the presses (OF 10)."""
        writing = "Writing the " + ("official" if official else "Koma") + " firmware"
        of = 8 if official else 10
        for i in range(8):
            self.step(pg, f"install-{i}")
            self.no_hscroll(pg, f"install-{i}")
            self.assertEqual(pg.inner_text("[data-testid=step-sub]").strip().upper(), f"{i + 1} OF {of}")   # not every screen is a press
            stop = pg.locator("[data-testid=setup-stop]")
            if i < 5:
                self.assertFalse(stop.is_disabled(), f"Stop here on press {i + 1}")
                self.assertTrue(self.exit_shown(pg))
            elif i in (5, 6):
                self.assertIn("POINT OF NO RETURN", pg.inner_text("main").upper())
                self.assertFalse(self.exit_shown(pg))                                   # no way out while it writes
                if i == 5:
                    self.assertTrue(stop.is_disabled())
                    self.assertEqual(stop.get_attribute("aria-describedby"), "stop-why")
                    self.assertEqual(pg.inner_text("#stop-why").strip(), "The update cannot be stopped once MENU/OK is pressed.")
                else:
                    self.assertEqual(stop.count(), 0)                                   # nothing to stop while it writes
                    self.assertEqual(pg.locator("#stop-why").count(), 0)                # the reason is said once, on MENU/OK
                    self.assertEqual(pg.get_attribute("[data-testid=setup-back]", "aria-describedby"), "nr-rule")
                    self.assertEqual(pg.inner_text("#nr-rule").strip(), "Keep the camera on. Do not open the battery cover.")
            else:
                self.assertEqual(stop.count(), 0)
                self.assertEqual(self.lead(pg), "When the lamp has stopped, turn the camera off.")
            if i == 6:
                self.assertEqual(self.lead(pg), "Do not touch the camera while the lamp blinks.")
                self.assertTrue(pg.inner_text("[data-testid=flash-status]").strip().startswith(writing))   # the write, named once
                # the one answer in view, disabled, pointing at how long the write takes
                nxt = "[data-testid=setup-next]"
                self.assertTrue(pg.eval_on_selector(nxt, "b => b.disabled && getComputedStyle(b).visibility === 'visible'"))
                self.assertEqual(pg.inner_text(nxt).strip(), "The lamp has stopped")
                self.assertEqual(pg.get_attribute(nxt, "aria-describedby"), "flash-takes")
                self.assertEqual(pg.inner_text("#flash-takes").strip().replace("\xa0", " "), "The camera takes 2 to 3 minutes.")
                self.assertTrue(pg.eval_on_selector("[data-testid=setup-back]", "b => b.disabled"))
                self.wait(pg, "() => !document.querySelector('[data-testid=setup-next]').disabled", FLASH_MIN_S + 5, "the lamp has had time to stop")
            if i in (1, 2, 3, 5, 7):
                self.assertTrue(pg.locator("[data-testid=setup-figure] svg").count() >= 1, f"figure on press {i}")
                self.assertTrue(pg.locator("[data-testid=setup-figure] [data-control] .hl").count() >= 1, f"highlight on press {i}")
            pg.click("[data-testid=setup-next]")

    # ---------------------------------------------------------------- tests
    @unittest.skipUnless(STOCK_OK, "Ricoh's 2.40 update file is not on this computer: the Koma firmware cannot be built")
    def test_first_run_on_a_stock_camera_installs_koma_end_to_end(self):
        br = self.bridge(F.stock_body())
        pg = self.page(br, card={})
        # setup opens by itself: no setup record, the address the launcher opens, a camera without Koma on the cable
        self.step(pg, "welcome")
        self.assertEqual(pg.inner_text("[data-testid=step-index]").strip(), "1 / 7")
        self.assertNotIn("Return to the official firmware", pg.inner_text("main"))        # said once, on Done
        self.assertEqual(pg.locator("[data-testid=setup-figure] [data-control] .hl").count(), 0)  # the camera, nothing marked
        self.assertIn("The card goes in this computer twice.", pg.inner_text("main"))
        self.assertIn("charged battery", pg.inner_text("[data-testid=welcome-needs]"))    # what to have at hand
        self.no_hscroll(pg, "welcome")
        self.next(pg)
        self.step(pg, "camera")
        self.wait(pg, "() => !!document.querySelector('[data-testid=row-koma]')", 30, "the camera read")
        self.assertEqual(self.lead(pg), "The camera runs the official firmware.")
        self.assertEqual(pg.inner_text("[data-testid=row-firmware] dd").strip(), "2.40")       # one fact, one place
        self.assertEqual(pg.inner_text("[data-testid=row-koma] dd").strip(), "Not installed")
        self.assertEqual(pg.locator("[data-testid=row-settings]").count(), 0)                 # nothing to back up
        self.next(pg)

        # card trip 1: the Koma firmware alone (release/docs/install.md)
        self.step(pg, "card")
        self.assertIn("The Koma firmware", pg.inner_text("[data-testid=card-facts]"))
        self.assertEqual(self.lead(pg), "Turn the camera off and unplug it. Put its card in this computer.")
        self.choose_card(pg)
        card = self.card(pg)
        info = json.loads(KB_get(br, "/install"))
        self.assertEqual(card["QRFLECT3"], info["firmware"]["koma_sha256"])    # the loader Koma firmware, flash-1 image
        self.assertNotIn("GRMOD.BIN", card)                                     # not yet: the second trip
        self.assertEqual(card["DCIM"], "dir")                                   # photos stay
        self.next(pg)

        br.body.unplug()   # the user takes the camera off the cable for the keyed power-on
        self.walk_presses(pg)
        # card trip 2: Koma's file on, the update file off
        self.step(pg, "install-8")
        self.assertEqual(pg.inner_text("[data-testid=step-index]").strip(), "4 / 7")
        self.assertEqual(pg.inner_text("[data-testid=step-sub]").strip().upper(), "9 OF 10")
        self.assertEqual(self.lead(pg), "Put the card in this computer again.")
        facts = pg.inner_text("[data-testid=card-facts]")
        self.assertIn("Koma 1.0", facts)
        self.assertIn("The firmware update file", facts)
        self.choose_card(pg)
        card = self.card(pg)
        self.assertEqual(card["GRMOD.BIN"], PACKAGE_SHA)                        # the package the bridge serves
        self.assertNotIn("QRFLECT3", card)
        self.next(pg)
        self.step(pg, "install-9")
        self.assertEqual(self.lead(pg), "Put the card back in the camera.")
        self.next(pg)

        self.step(pg, "connect")
        self.assertIn("Turn the camera on. Then plug in the USB cable.", self.lead(pg))
        F.after_koma_install(br.body)
        br.body.plug()
        self.step(pg, "look", 30)                                               # Connect USB moves on by itself
        self.wait(pg, "() => document.querySelectorAll('[data-look]').length === 21 && !document.querySelector('fieldset[disabled]')", 20, "the looks")
        names = pg.eval_on_selector_all("[data-look]", "els => els.map(e => e.dataset.look)")
        self.assertEqual(names[:3], ["STANDARD", "MONO", "SOFT MONO"])           # naming.md order
        self.assertEqual(pg.eval_on_selector("[data-look='MONO']", "e => e.classList.contains('active')"), True)
        pg.check("[data-look='SOFT MONO'] input", force=True)
        pg.click("[data-testid=setup-use]")
        self.wait(pg, "() => /SOFT MONO is active/.test(document.querySelector('[data-testid=look-status]')?.innerText || '')", 20, "the look set")
        self.assertEqual(br.body.ram[0x8C0], 5)                                 # SOFT MONO's stored id, read back
        self.next(pg)

        self.step(pg, "done")
        self.assertEqual(self.lead(pg), "Koma is running on the camera.")
        self.assertEqual(pg.inner_text("[data-testid=done-koma] dd").strip(), "1.0")
        self.assertEqual(pg.inner_text("[data-testid=setup-home]").strip(), "Finish")     # the quiet way to Overview
        self.assertEqual(pg.inner_text("[data-testid=done-look] dd").strip(), "SOFT MONO")
        self.assertFalse(self.exit_shown(pg))                                  # Done's own button is the way out
        pg.click("[data-testid=setup-finish]")
        self.wait(pg, "() => location.hash === '#/shoot' && !!document.querySelector('nav[aria-label=Main]')", 15, "Shoot")
        state = json.loads(pg.evaluate("() => localStorage.getItem('koma.setup')"))
        self.assertEqual((state["done"], state["active"]), (True, False))
        pg.reload()
        self.wait(pg, "() => !!document.querySelector('nav[aria-label=Main]')", 15, "the app, not setup again")
        self.assertEqual(pg.locator("[data-testid=setup]").count(), 0)
        # nothing went to the camera over USB but the look (-KSET) and the page's own Shoot session
        self.assertEqual(br.body.poff_count, 0)
        self.assertFalse(any(c.startswith(("-STOR", "DELE")) for c in br.writes()), br.writes())
        self.assertTrue(any(c.startswith("-KSET") for c in br.writes()))

    @unittest.skipUnless(STOCK_OK, "Ricoh's 2.40 update file is not on this computer")
    def test_resume_after_a_reload_while_the_camera_writes(self):
        br = self.bridge(F.stock_body())
        pg = self.page(br, card={})
        self.step(pg, "welcome")
        self.next(pg)
        self.step(pg, "camera")
        self.next(pg)
        self.step(pg, "card")
        self.choose_card(pg)
        self.next(pg)
        br.body.unplug()
        for i in range(6):
            self.step(pg, f"install-{i}")
            pg.click("[data-testid=setup-next]")
        self.step(pg, "install-6")
        before = json.loads(pg.evaluate("() => localStorage.getItem('koma.setup')"))["flashFrom"]
        time.sleep(2.2)
        pg.reload()
        self.step(pg, "install-6")                                       # the same screen, the count from the same start
        self.assertEqual(self.lead(pg), "Do not touch the camera while the lamp blinks.")
        self.assertEqual(json.loads(pg.evaluate("() => localStorage.getItem('koma.setup')"))["flashFrom"], before)
        self.assertFalse(self.exit_shown(pg))
        pg.click("[data-testid=setup-back]", force=True)                # disabled while the camera writes
        self.step(pg, "install-6")

    def test_a_koma_camera_gets_the_short_plan(self):
        """First run on a camera already running Koma: Welcome, Your camera, Pick a look, Done; Exit on every screen,
        and Exit ends it on Overview."""
        br = self.bridge(F.koma_body())
        pg = self.page(br)
        self.step(pg, "welcome")
        self.assertEqual(pg.inner_text("[data-testid=setup]").count("Koma is on this camera."), 1)
        self.assertEqual(pg.inner_text("[data-testid=step-index]").strip(), "1 / 4")
        self.assertEqual(pg.locator("[data-testid=welcome-needs]").count(), 0)          # nothing to have at hand
        self.assertTrue(self.exit_shown(pg))
        self.next(pg)
        self.step(pg, "camera")
        self.wait(pg, "() => !!document.querySelector('[data-testid=row-koma]')", 30)
        self.assertEqual(pg.inner_text("[data-testid=row-koma] dd").strip(), "1.0")
        self.assertTrue(self.exit_shown(pg))
        pg.click("[data-testid=setup-exit]")
        self.wait(pg, "() => location.hash === '#/home' && !!document.querySelector('[data-testid=overview]')", 15, "Overview")
        state = json.loads(pg.evaluate("() => localStorage.getItem('koma.setup')"))
        self.assertEqual((state["plan"], state["done"], state["active"]), ("koma", True, False))
        pg.reload()
        self.wait(pg, "() => !!document.querySelector('[data-testid=overview]')", 20, "Overview, not setup again")
        self.assertEqual(pg.locator("[data-testid=setup]").count(), 0)

    def test_first_run_keeps_a_deep_link(self):
        """Setup opens by itself only at the address the launcher opens; a link to a page keeps its page."""
        br = self.bridge(F.stock_body())
        pg = self.page(br, "#/home")
        self.wait(pg, "() => !!document.querySelector('[data-testid=overview]')", 20, "Overview")
        pg.wait_for_timeout(2500)
        self.assertEqual(pg.locator("[data-testid=setup]").count(), 0)

    @unittest.skipUnless(STOCK_OK, NO_STOCK)
    def test_update_koma_replaces_an_older_build(self):
        br = self.bridge(F.koma_body(F.OLDER_BUILD))
        pg = self.page(br, "#/settings/update", card={"GRMOD.BIN": "old module", "KOMALOG.TXT": "old log", "PARAM.CUR": "kept"},
                       setup={"v": 1, "seen": True, "active": False, "done": True})
        self.wait(pg, "() => !!document.querySelector('[data-testid=update-install]')", 30, "Update Koma")
        # a build the package replaces: never its number (VERSIONING.md "Earlier builds")
        self.assertEqual(pg.inner_text("[data-testid=upd-camera] dd").strip(), "An earlier Koma build")
        self.assertEqual(pg.inner_text("[data-testid=upd-here] dd").strip(), "Koma 1.0")
        self.assertEqual(pg.inner_text("[data-testid=update-install]").strip(), "Update")   # the title supplies the noun
        self.assertIn("Backs up the settings", pg.inner_text("[data-testid=update-koma]"))
        with pg.expect_download() as dl:
            pg.click("[data-testid=update-install]")
        self.assertRegex(dl.value.suggested_filename, r"^GR-settings-\d{4}-\d\d-\d\d\.dat$")
        self.assertEqual(Path(dl.value.path()).read_bytes(), br.body.card["/EXT0/PARAM.CUR"])   # the backup is PARAM.CUR
        self.step(pg, "card")
        self.assertEqual(pg.inner_text("[data-testid=step-index]").strip(), "1 / 4")
        self.choose_card(pg)
        card = self.card(pg)
        self.assertEqual(card["GRMOD.BIN"], PACKAGE_SHA)
        self.assertNotIn("KOMALOG.TXT", card)                     # so the log after the restart is new
        self.assertNotIn("QRFLECT3", card)                        # no firmware write for an update
        self.assertIn("PARAM.CUR", card)
        self.next(pg)
        br.body.unplug()
        self.step(pg, "install-0")
        self.assertEqual(self.lead(pg), "Put the card back in the camera.")
        self.next(pg)
        self.step(pg, "connect")
        F.after_koma_install(br.body)
        br.body.plug()
        self.step(pg, "done", 30)                                  # moves on by itself once confirmed
        pg.click("[data-testid=setup-finish]")
        self.wait(pg, "() => location.hash === '#/shoot'", 15)
        self.assertEqual(br.body.poff_count, 0)

    def put_on_card(self, pg, name, text):
        """The camera writes a file to the card (here: the /IROM installer's record)."""
        pg.evaluate("""async ([name, text]) => {
          const card = await window.__cardReady;
          const w = await (await card.getFileHandle(name, {create: true})).createWritable();
          await w.write(text); await w.close();
        }""", [name, text])

    @unittest.skipUnless(STOCK_OK, NO_STOCK)
    def test_update_koma_into_internal_memory(self):
        """Where Koma goes: In the camera. The existing card installer (irodump) as GRMOD.BIN and the package as
        KOMAPKG.BIN; one start; then the installer's record is read and the installer comes off the card. No USB write."""
        br = self.bridge(F.koma_body(F.OLDER_BUILD))
        pg = self.page(br, "#/settings/update", card={"GRMOD.BIN": "old module", "KOMALS.TXT": "", "IRODUMP.TXT": "old"},
                       setup={"v": 1, "seen": True, "active": False, "done": True})
        self.wait(pg, "() => !!document.querySelector('[data-testid=update-install]')", 30, "Update Koma")
        with pg.expect_download():
            pg.click("[data-testid=update-install]")
        self.step(pg, "card")
        self.assertIn("2 GB or smaller", self.lead(pg) + pg.inner_text("main").replace(" ", " "))
        pg.check("[data-k=where-irom]", force=True)
        self.assertIn("Koma 1.0 and its installer", pg.inner_text("[data-testid=card-facts]"))
        self.choose_card(pg)
        card = self.card(pg)
        info = json.loads(KB_get(br, "/install"))
        self.assertEqual(card["GRMOD.BIN"], info["installer"]["published_sha256"])     # the installer runs first
        self.assertEqual(card["KOMAPKG.BIN"], PACKAGE_SHA)
        for gone in ("KOMALS.TXT", "IRODUMP.TXT", "KOMALOG.TXT"):                      # a listing would install nothing
            self.assertNotIn(gone, card)
        self.next(pg)
        br.body.unplug()
        self.step(pg, "install-0")
        self.assertEqual(pg.inner_text("[data-testid=step-sub]").strip().upper(), "1 OF 3")
        self.assertIn("wait 1 minute", self.lead(pg).replace("\xa0", " "))
        self.next(pg)
        self.step(pg, "install-1")
        # the camera has not run the installer yet: nothing changes on the card
        self.wait(pg, "() => { const b = document.querySelector('[data-testid=setup-choose]'); return b && !b.disabled; }", 20)
        pg.click("[data-testid=setup-choose]")
        self.wait(pg, "() => /no finished record/.test(document.querySelector('[data-testid=card-status]')?.innerText || '')", 20)
        self.assertIn("GRMOD.BIN", self.card(pg))
        size = KI.PACKAGE_BYTES
        self.put_on_card(pg, "IRODUMP.TXT", f"koma=irominstall\nmode=install\nsrc_valid=1\ncopy_rc=0\ndst_size={size}\n"
                                           f"dst_valid=1\nresult=installed\nirom=GRMOD.BIN size={size}\nend=1\n")
        pg.click("[data-testid=setup-choose]")
        self.wait(pg, "() => /Koma is in the camera/.test(document.querySelector('[data-testid=card-status]')?.innerText || '')", 20)
        card = self.card(pg)
        self.assertNotIn("GRMOD.BIN", card)                        # the installer does not hide /IROM
        self.assertNotIn("KOMAPKG.BIN", card)
        self.next(pg)
        self.step(pg, "install-2")
        self.assertEqual(self.lead(pg), "Put a card in the camera.")
        self.next(pg)
        self.step(pg, "connect")
        F.after_koma_install(br.body)
        br.body.plug()
        self.step(pg, "done", 30)
        self.assertEqual(br.body.poff_count, 0)

    def test_update_koma_offers_the_release_over_an_earlier_build(self):
        """A camera whose tag names an earlier (pre-1.0) build (240K2, the SDHC Koma firmware) is offered
        this computer's release, never shown by its own number, and never as a downgrade."""
        br = self.bridge(F.koma_body(F.OTHER_BUILD, tag=b"240K2"))
        pg = self.page(br, "#/settings/update", setup={"v": 1, "seen": True, "active": False, "done": True})
        self.wait(pg, "() => !!document.querySelector('[data-testid=update-install]')", 30, "Update Koma")
        self.assertEqual(pg.inner_text("[data-testid=upd-camera] dd").strip(), "An earlier Koma build")
        self.assertEqual(pg.inner_text("[data-testid=upd-here] dd").strip(), "Koma 1.0")
        self.assertEqual(pg.inner_text("[data-testid=update-install]").strip(), "Update")
        self.assertNotRegex(pg.inner_text("[data-testid=update-koma]"), r"Koma 2|2\.4(?!0)|generation")

    def test_update_koma_never_replaces_a_build_it_is_not_known_to_replace(self):
        """Only a recorded predecessor, an earlier build's tag or an older release tag is known to be older. The build
        this computer serves (Koma 1.0, 094F6569) is this computer's; a sibling build (5429091F) on the same release stays."""
        for build, camera, line in ((F.PACKAGE_BUILD, "Koma 1.0", "This camera runs Koma 1.0. Nothing to update."),
                                    (F.OTHER_BUILD, "Koma 1.0", "This camera runs a different Koma build from this computer\u2019s. It stays as it is.")):
            br = self.bridge(F.koma_body(build))
            pg = self.page(br, "#/settings/update", setup={"v": 1, "seen": True, "active": False, "done": True})
            self.wait(pg, "() => !!document.querySelector('[data-testid=update-official]')", 30, "Update Koma read")
            self.assertEqual(pg.inner_text("[data-testid=upd-camera] dd").strip(), camera)
            self.assertEqual(pg.inner_text("[data-testid=update-line]").strip(), line)
            # the same build never shows two numbers: one row when nothing is to update
            self.assertEqual(pg.locator("[data-testid=upd-here]").count(), 0 if build == F.PACKAGE_BUILD else 1)
            self.assertEqual(pg.locator("[data-testid=update-install]").count(), 0, f"no install over {build:08X}")
            # the return is offered, with the fact that it was tried over the first Koma firmware only (a release tag
            # does not show the firmware)
            self.assertEqual(pg.inner_text("[data-testid=update-untried]").strip(),
                             "This return has been tested on the first Koma firmware only.")

    def test_setup_keeps_a_build_it_is_not_known_to_replace(self):
        """Setup on a camera already running Koma writes nothing: Your camera turns the plan into the short one."""
        br = self.bridge(F.koma_body(F.OTHER_BUILD))
        pg = self.page(br, "#/setup", card={}, setup=setup_state())
        self.step(pg, "welcome")
        self.next(pg)
        self.step(pg, "camera")
        self.wait(pg, "() => !!document.querySelector('[data-testid=row-koma]')", 30)
        self.assertEqual(self.lead(pg), "The camera runs the Koma firmware.")              # never a generation
        self.assertEqual(pg.inner_text("[data-testid=row-koma] dd").strip(), "1.0")
        self.assertEqual(pg.inner_text("[data-testid=step-index]").strip(), "2 / 4")
        # the consequence under the sheet, as Update Koma words it
        self.assertEqual(pg.inner_text("[data-testid=camera-line]").strip(), "This camera runs a different Koma build from this computer\u2019s. It stays as it is.")
        # the settings are read; backed up once the file is saved, on Continue
        self.assertEqual(pg.inner_text("[data-testid=row-settings] dd").strip(), "Ready to back up")
        self.assertEqual(pg.inner_text("[data-testid=setup-next]").strip(), "Save backup and continue")
        with pg.expect_download() as dl:
            self.next(pg)
        self.assertRegex(dl.value.suggested_filename, r"^GR-settings-\d{4}-\d\d-\d\d\.dat$")
        self.step(pg, "look")
        pg.click("[data-testid=setup-back]")
        self.step(pg, "camera")
        self.wait(pg, "() => (document.querySelector('[data-testid=row-settings] dd')?.innerText || '').trim() === 'Backed up'", 10, "Backed up")
        self.wait(pg, "() => !document.querySelector('[data-testid=setup-next]').disabled", 30, "the camera read again")
        self.assertEqual(pg.inner_text("[data-testid=setup-next]").strip(), "Continue")        # the backup is saved once
        self.next(pg)
        self.step(pg, "look")
        self.assertNotIn("GRMOD.BIN", self.card(pg))                  # nothing was written to the card

    @unittest.skipUnless(STOCK_OK, "Ricoh's 2.40 update file is not on this computer")
    def test_return_to_official_firmware(self):
        br = self.bridge(F.koma_body(tag=b"Rev024M"))      # the loader Koma firmware, where the return was observed
        pg = self.page(br, "#/settings/update", card={"GRMOD.BIN": "module", "KOMALOG.TXT": "log", "PARAM.CUR": "x", "PARAM.OK": "y"},
                       setup={"v": 1, "seen": True, "active": False, "done": True})
        self.wait(pg, "() => !!document.querySelector('[data-testid=update-official]')", 30)
        self.assertIn("Writes Ricoh’s 2.40 firmware to the card", pg.inner_text("[data-testid=update-koma]"))
        self.assertEqual(pg.locator("[data-testid=update-untried]").count(), 0)
        with pg.expect_download():
            pg.click("[data-testid=update-official]")
        self.step(pg, "card")
        self.choose_card(pg)
        card = self.card(pg)
        self.assertEqual(card["QRFLECT3"], KI._loader().STOCK_SHA256)    # Ricoh's own 2.40 file
        for gone in ("GRMOD.BIN", "KOMALOG.TXT", "PARAM.CUR"):
            self.assertNotIn(gone, card)
        self.assertIn("PARAM.OK", card)                                  # not Koma's to remove
        self.next(pg)
        br.body.unplug()
        self.walk_presses(pg, official=True)
        self.step(pg, "connect")
        self.assertIn("take one photo", self.lead(pg))
        # the camera comes back without a new photo first: refused, then the photo
        br.body.card.pop("/EXT0/KOMALOG.TXT", None)
        br.body.plug()
        self.wait(pg, "() => /No new photo on the card/.test(document.querySelector('[data-testid=connect-status]')?.innerText || '')", 30)
        br.body.unplug()
        time.sleep(2.5)
        F.after_official(br.body)
        br.body.plug()
        self.step(pg, "done", 30)
        self.assertEqual(self.lead(pg), "The camera runs the official firmware 2.40.")
        self.assertEqual(pg.inner_text("[data-testid=setup-finish]").strip(), "Finish")

    def test_refuses_a_camera_on_other_firmware(self):
        br = self.bridge(F.stock_body(version="02.30"))
        pg = self.page(br, "#/setup")
        self.step(pg, "welcome")
        self.next(pg)
        self.step(pg, "camera")
        self.wait(pg, "() => /firmware 2.30\\. Koma needs 2.40/.test(document.querySelector('[data-testid=step-lead]')?.innerText || '')", 30)
        self.assertTrue(self.hidden_primary(pg))

    def test_an_unknown_firmware_tag_offers_read_again(self):
        body = F.stock_body()
        F.add_photo(body, "RIMG0005.JPG", "Rev0108")                    # a tag from before 2.40
        br = self.bridge(body)
        pg = self.page(br, "#/setup", setup=setup_state(step=1))
        self.step(pg, "camera")
        self.wait(pg, "() => /could not read the firmware from the newest photo/.test(document.querySelector('[data-testid=camera-status]')?.innerText || '')", 30)
        self.assertEqual(self.lead(pg), "Take a new photo with the camera.")
        self.assertTrue(self.hidden_primary(pg))
        F.add_photo(br.body, "RIMG0006.JPG", "Rev0240", "2030:01:01 00:00:00")
        pg.click("[data-testid=camera-read]")
        self.wait(pg, "() => /official firmware/.test(document.querySelector('[data-testid=step-lead]')?.innerText || '')", 30)

    @unittest.skipUnless(STOCK_OK, "Ricoh's 2.40 update file is not on this computer")
    def test_refuses_a_card_with_other_update_files_and_a_folder_that_is_not_a_card(self):
        br = self.bridge(F.stock_body())
        pg = self.page(br, "#/setup", card={"QRFLECT7": "ricoh script", "notes.txt": "x"})
        self.step(pg, "welcome")
        self.next(pg)
        self.step(pg, "camera")
        self.next(pg)
        self.step(pg, "card")
        self.wait(pg, "() => !document.querySelector('[data-testid=setup-choose]')?.disabled", 20)
        pg.click("[data-testid=setup-choose]")
        self.wait(pg, "() => /The card holds QRFLECT7/.test(document.querySelector('[data-testid=card-status]')?.innerText || '')", 20)
        self.assertNotIn("QRFLECT3", self.card(pg))               # nothing written
        self.assertEqual(pg.locator("[data-testid=card-phases] li.failed").count(), 0)   # refused before writing
        # a folder that is not a card (no DCIM, other files)
        pg.evaluate("""async () => { const c = await window.__cardReady; await c.removeEntry('DCIM', {recursive: true});
                       await c.removeEntry('QRFLECT7'); }""")
        pg.click("[data-testid=setup-choose]")
        self.wait(pg, "() => /not the top level of a camera card/.test(document.querySelector('[data-testid=card-status]')?.innerText || '')", 20)
        self.assertNotIn("QRFLECT3", self.card(pg))

    def test_confirm_waits_for_koma_to_start(self):
        br = self.bridge(F.koma_body(F.OLDER_BUILD))
        pg = self.page(br, "#/setup", card={},
                       setup=setup_state(plan="update", step=2, mode="update",
                                         pkg={"version": "1.0.0", "build": "094F6569", "supersedes": ["747F061F"]},
                                         backup={"skipped": True}, card={"at": 1, "folder": "card", "files": [], "removed": []}))
        self.step(pg, "connect")
        # the camera on the cable still runs the old build: refused with the stage and the action
        self.wait(pg, "() => /another Koma build/.test(document.querySelector('[data-testid=connect-status]')?.innerText || '')", 30)
        self.assertTrue(self.hidden_primary(pg))
        # Koma started but its start record stops part way: not confirmed
        br.body.unplug()
        time.sleep(2.5)
        F.after_koma_install(br.body, stage=2)
        br.body.plug()
        self.wait(pg, "() => /start record on the card is incomplete/.test(document.querySelector('[data-testid=connect-status]')?.innerText || '')", 30)
        br.body.unplug()
        time.sleep(2.5)
        F.after_koma_install(br.body)
        br.body.plug()
        self.step(pg, "done", 30)
        self.assertEqual(self.lead(pg), "Koma is running on the camera.")

    @unittest.skipUnless(STOCK_OK, NO_STOCK)
    def test_download_route_without_file_system_access(self):
        import tempfile
        br = self.bridge(F.koma_body(F.OLDER_BUILD))
        pg = self.page(br, "#/setup", no_fsa=True,
                       setup=setup_state(plan="update", step=0, mode="update", backup={"skipped": True}))
        self.step(pg, "card")
        self.assertIn("Save the file, then copy it to the top level of the card.", self.lead(pg))
        self.wait(pg, "() => !document.querySelector('[data-testid=setup-save]')?.disabled", 20)
        with pg.expect_download() as dl:
            pg.click("[data-testid=setup-save]")
        self.assertEqual(dl.value.suggested_filename, "GRMOD.BIN")
        self.assertEqual(hashlib.sha256(Path(dl.value.path()).read_bytes()).hexdigest(), PACKAGE_SHA)
        self.assertTrue(pg.eval_on_selector("[data-phase=write]", "e => e.classList.contains('now')"))   # not done before read-back
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "NO_NAME"
            (card / "DCIM" / "100RICOH").mkdir(parents=True)
            (card / "DCIM" / "100RICOH" / "RIMG0001.JPG").write_bytes(b"\xff\xd8\xff\xd9")
            (card / "GRMOD.BIN").write_bytes(b"not the package")
            (card / "KOMALOG.TXT").write_bytes(b"KOMA TLM V1\nEND\n")
            with pg.expect_file_chooser() as fc:
                pg.click("[data-testid=setup-check]")
            fc.value.set_files(str(card))
            self.wait(pg, "() => /does not hold what was written/.test(document.querySelector('[data-testid=card-status]')?.innerText || '')", 20)
            (card / "GRMOD.BIN").write_bytes(Path(dl.value.path()).read_bytes())
            with pg.expect_file_chooser() as fc:
                pg.click("[data-testid=setup-check]")
            fc.value.set_files(str(card))
            # Koma's old log must go too, or the check after the restart could read it: the refusal names it
            self.wait(pg, "() => /The card still holds KOMALOG.TXT. Delete it, then check the card again./.test(document.querySelector('[data-testid=card-status]')?.innerText || '')", 20)
            (card / "KOMALOG.TXT").unlink()
            with pg.expect_file_chooser() as fc:
                pg.click("[data-testid=setup-check]")
            fc.value.set_files(str(card))
            self.wait(pg, "() => /The card is ready/.test(document.querySelector('[data-testid=card-status]')?.innerText || '')", 20)

    def test_exit_then_resume_from_settings(self):
        br = self.bridge(F.koma_body(F.OLDER_BUILD))
        pg = self.page(br, "#/settings/update", card={}, setup={"v": 1, "seen": True, "active": False, "done": True})
        self.wait(pg, "() => !!document.querySelector('[data-testid=update-install]')", 30)
        with pg.expect_download():
            pg.click("[data-testid=update-install]")
        self.step(pg, "card")
        self.choose_card(pg)
        self.next(pg)
        self.step(pg, "install-0")
        pg.click("[data-testid=setup-exit]")
        self.wait(pg, "() => location.hash === '#/settings/update' && !!document.querySelector('[data-testid=update-koma]')", 15)
        self.wait(pg, "() => !!document.querySelector('[data-testid=update-resume]')", 30, "Continue the update")
        self.assertIn("An update was left part way.", pg.inner_text("[data-testid=update-koma]"))
        pg.click("[data-testid=update-resume]")
        self.step(pg, "install-0")

    @unittest.skipUnless(STOCK_OK, "Ricoh's 2.40 update file is not on this computer")
    def test_stop_here_then_continue_setup(self):
        """Setup stopped before MENU/OK can go on from the same screen, from Settings or from #/setup; the stop says what
        is on the card."""
        br = self.bridge(F.stock_body())
        pg = self.page(br, card={})
        self.step(pg, "welcome")
        self.next(pg)
        self.step(pg, "camera")
        self.next(pg)
        self.step(pg, "card")
        self.choose_card(pg)
        self.next(pg)
        br.body.unplug()
        for i in range(2):
            self.step(pg, f"install-{i}")
            pg.click("[data-testid=setup-next]")
        self.step(pg, "install-2")
        pg.click("[data-testid=setup-stop]")
        self.wait(pg, "() => location.hash === '#/home'", 15)
        br.body.plug()
        pg.goto(br.url + "#/settings/update")
        self.wait(pg, "() => !!document.querySelector('[data-testid=update-resume]')", 30, "Continue setup")
        text = pg.inner_text("[data-testid=update-koma]")
        self.assertIn("Nothing was written to the camera. The card still holds the update file.", text)
        self.assertEqual(pg.inner_text("[data-testid=update-resume]").strip(), "Continue setup")
        self.assertEqual(pg.inner_text("[data-testid=update-restart]").strip(), "Start again")
        pg.click("[data-testid=update-resume]")
        # the held-key screens start again from the mode dial: nobody knows what the camera did meanwhile
        self.step(pg, "install-1")
        pg.click("[data-testid=setup-exit]")
        self.wait(pg, "() => location.hash === '#/home'", 15)
        self.wait(pg, "() => (document.querySelector('[data-testid=overview-primary]')?.innerText || '').trim() === 'Continue setup'", 30, "Continue setup on Overview")
        pg.goto(br.url + "#/setup")                                    # the address goes on with it too
        self.step(pg, "install-1")

    def test_keyboard_only(self):
        """Each step's name takes focus (a screen reader announces it); Tab reaches the primary action; Enter moves on;
        the look list is one radio group, moved with the arrow keys."""
        br = self.bridge(F.koma_body())
        pg = self.page(br, "#/setup", setup=setup_state())
        self.step(pg, "welcome")
        self.wait(pg, "() => document.activeElement?.id === 'onb-title'", 5, "focus on the step name")

        def tab_to(testid, limit=12):
            for _ in range(limit):
                pg.keyboard.press("Tab")
                if pg.evaluate("t => document.activeElement?.dataset?.testid === t", testid):
                    return
            raise AssertionError(f"Tab never reached {testid}")
        tab_to("setup-next")
        pg.keyboard.press("Enter")
        self.step(pg, "camera")
        self.wait(pg, "() => document.activeElement?.id === 'onb-title'", 5)
        self.wait(pg, "() => !document.querySelector('[data-testid=setup-next]')?.disabled", 30, "the camera read")
        tab_to("setup-next")
        pg.keyboard.press("Enter")
        self.step(pg, "look")                       # this computer's build already runs: the short plan
        self.wait(pg, "() => document.querySelectorAll('[data-look]').length === 21 && !document.querySelector('fieldset[disabled]')", 20)
        pg.focus("[data-look='MONO'] input")
        pg.keyboard.press("ArrowRight")             # the next look in the group order
        self.wait(pg, "() => document.querySelector('[data-look=\"SOFT MONO\"] input')?.checked", 5, "arrow keys move the choice")
        tab_to("setup-use")
        pg.keyboard.press("Enter")
        self.wait(pg, "() => /SOFT MONO is active/.test(document.querySelector('[data-testid=look-status]')?.innerText || '')", 20)

    def test_setup_from_settings_and_the_gallery(self):
        br = self.bridge(F.koma_body())
        pg = self.page(br, "#/settings/update", setup={"v": 1, "seen": True, "active": False, "done": True})
        self.wait(pg, "() => !!document.querySelector('[data-testid=update-official]')", 30)
        pg.click("[data-testid=update-koma] details summary")             # where Koma runs, setup from the start is under Details
        pg.click("[data-testid=update-restart]")
        self.step(pg, "welcome")
        self.no_hscroll(pg, "welcome")
        pg.goto(br.url + "#/gr")
        self.wait(pg, "() => !!document.querySelector('[data-testid=gr-gallery]')", 15, "the gallery")
        ids = pg.eval_on_selector_all("figure[data-view] [data-control]", "els => [...new Set(els.map(e => e.closest('figure').dataset.view + ':' + e.dataset.control))]")
        for view, want in (("back", ["lcd", "adj", "t", "w", "play", "up", "right", "down", "left", "menu-ok", "delete", "disp", "lamp"]),
                           ("front", ["lens", "af-window", "af-light", "mic", "flash", "updown-dial", "mode-dial", "shutter", "power"]),
                           ("top", ["flash", "hot-shoe", "power", "shutter", "mode-dial", "unlock", "updown-dial", "usb-door", "lens"])):
            for cid in want:
                self.assertIn(f"{view}:{cid}", ids)
        self.assertGreaterEqual(pg.locator("figure[data-view] .hl").count(), 3)   # one highlight per view at least
        self.no_hscroll(pg, "gallery")

    FIGURE_JS = r"""() => [...document.querySelectorAll('[data-testid=setup-figure] svg')].map(svg => {
      const vb = svg.viewBox.baseVal, w = svg.getBoundingClientRect().width, segs = [];
      for (const p of svg.querySelectorAll('.callout path')) {
        const pts = p.getAttribute('d').match(/-?[\d.]+/g).map(Number);
        for (let i = 0; i + 3 < pts.length; i += 2) segs.push([pts[i], pts[i + 1], pts[i + 2], pts[i + 3], p]);
      }
      const cross = (a, b) => { const d = (a[2] - a[0]) * (b[3] - b[1]) - (a[3] - a[1]) * (b[2] - b[0]); if (!d) return false;
        const t = ((b[0] - a[0]) * (b[3] - b[1]) - (b[1] - a[1]) * (b[2] - b[0])) / d;
        const u = ((b[0] - a[0]) * (a[3] - a[1]) - (b[1] - a[1]) * (a[2] - a[0])) / d;
        return t > 0.001 && t < 0.999 && u > 0.001 && u < 0.999; };
      let crossings = 0;
      for (let i = 0; i < segs.length; i++) for (let j = i + 1; j < segs.length; j++)
        if (segs[i][4] !== segs[j][4] && cross(segs[i], segs[j])) crossings++;
      return {pxPerMm: Math.round(w / vb.width * 1000) / 1000, crossings};
    })"""

    def test_figure_scale_and_leaders(self):
        """One scale across a sequence (every view is 112 mm wide and room for callouts is kept on both sides), and no
        two leaders cross: at the figure's width, and in the reduced-motion frames (side by side on a phone)."""
        br = self.bridge(F.stock_body())
        scales = []
        for reduced in (False, True):
            for sub in (1, 2, 3, 4, 5, 6, 7):
                # a load lands on the held-key screens (2 to 4) at screen 1: walk on from there
                pg = self.page(br, "#/setup", reduced=reduced,
                               setup=setup_state(step=3, sub=1 if 2 <= sub <= 4 else sub, mode="install", flashFrom=int(time.time() * 1000),
                                                 card={"at": 1, "folder": "card", "files": [], "removed": []}))
                for i in range(1, sub if 2 <= sub <= 4 else 1):
                    self.step(pg, f"install-{i}")
                    pg.click("[data-testid=setup-next]")
                self.step(pg, f"install-{sub}")
                figs = pg.evaluate(self.FIGURE_JS)
                for f in figs:
                    self.assertEqual(f["crossings"], 0, f"leaders cross on press {sub + 1} (reduced motion {reduced})")
                if sub == 3:   # the hold: the count on the drawing, or on the after frame only (nothing held before)
                    counts = pg.eval_on_selector_all("[data-testid=setup-figure] svg",
                                                     "svgs => svgs.map(s => [...s.querySelectorAll('.count')].map(t => t.textContent))")
                    self.assertEqual(counts, [[], ["3\u2009s"]] if reduced else [["3\u2009s"]])
                if not reduced:
                    scales.append(figs[0]["pxPerMm"])
                self.contexts.pop().close()
        self.assertEqual(len(set(scales)), 1, f"the camera changes size across the presses: {scales}")


def KB_get(br, path) -> bytes:
    import urllib.request
    with urllib.request.urlopen(br.url.rstrip("/") + path, timeout=30) as r:
        return r.read()


if __name__ == "__main__":
    unittest.main()
