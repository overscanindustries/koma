#!/usr/bin/env python3
"""End-to-end: the Photos area of the Koma app (tools/komaapp) in headless Chrome against komabridge and
the SIMULATED camera, its card seeded with six real frames (testdata/photos, EXIF and 160x120 thumbnails as
the camera wrote them) after the mock's own three. Every import lands in a temporary home folder.

Same pattern as test_e2e_app.py: the bridge runs in-process on a free port over MockBody(tether=True, koma=True) with
the hardware timings at a quarter scale. KOMA_E2E_VIEWPORT=phone runs every test at 390 px. Every test fails on an
uncaught page error, a console error other than the app's own handled API answers, or a failed app file.

Everything here is MOCK: it proves the app and the bridge agree, not that a camera does any of it.

    (cd tools/komaapp && npm run build)
    python3 -m pytest tools/komaset/web/test_e2e_photos.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import photos_fixture as PF  # noqa: E402  (puts the bridge on sys.path)
import komabridge as KB  # noqa: E402
import mockcard  # noqa: E402
from mockcard import MockBody  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

DESKTOP = {"width": 1280, "height": 860}
PHONE = {"width": 390, "height": 844}
VIEW = PHONE if os.environ.get("KOMA_E2E_VIEWPORT") == "phone" else DESKTOP
PHONE_RUN = VIEW is PHONE

FRAMES = sorted(p.name for p in PF.FRAMES.glob("*.JPG"))  # R0015702 ... R0015713


class Bridge:
    def __init__(self, scale=0.25, seed=True, empty=False):
        self.body = MockBody(tether=True, koma=True).hw_timings(scale)
        if empty:
            PF.seed(self.body, keep_mock=False)
            for k in [k for k in self.body.card if "/DCIM/" in k]:
                del self.body.card[k]
            self.body.folder_entries.clear()
        elif seed:
            PF.seed(self.body)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.rearm_after_read = True
        self.root = Path(tempfile.mkdtemp(prefix="koma-e2e-photos-"))
        self.importer = PF.importer(self.link, self.root)
        self.folder = self.root / "home" / "Pictures" / "Koma"
        self.server = KB.make_server(self.link, port=0)
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
        shutil.rmtree(self.root, ignore_errors=True)


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
class PhotosE2E(unittest.TestCase):
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
        self.requests: list[str] = []

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

    def page(self, br, hash_="#/photos", scheme="dark", before=None):
        ctx = self.browser.new_context(viewport=VIEW, accept_downloads=True, color_scheme=scheme)
        self.contexts.append(ctx)
        pg = ctx.new_page()
        if before:
            before(pg)
        pg.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        # "Failed to load resource" lines are the app's own API answers (409 remote_on, 400 folder), which it handles.
        pg.on("console", lambda m: self.errors.append(f"console: {m.text}")
              if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.on("request", lambda r: self.requests.append(r.url.split(str(br.port), 1)[-1]))
        pg.on("response", lambda r: self.errors.append(f"asset {r.status}: {r.url}")
              if r.status >= 400 and self.is_asset(r.url) else None)
        pg.goto(br.url + hash_)
        return pg

    @staticmethod
    def is_asset(url: str) -> bool:
        from urllib.parse import urlsplit
        path = urlsplit(url).path
        return path in ("/", "/index.html", "/favicon.svg") or path.startswith("/assets/")

    def wait(self, pg, js, timeout_s=20, what="", arg=None):
        try:
            pg.wait_for_function(js, arg=arg, timeout=timeout_s * 1000, polling=150)
        except Exception as error:  # noqa: BLE001
            text = pg.evaluate("() => (document.querySelector('dialog[open]')?.innerText || '') + '\\n---\\n' + document.body.innerText.slice(0, 900)")
            raise AssertionError(f"timed out after {timeout_s} s waiting for {what or js}; page text:\n{text}") from error

    def sheet_ready(self, pg, n=9):
        self.wait(pg, "n => document.querySelectorAll('[data-testid=photos] .cell').length === n", 30, f"{n} cells", n)
        self.wait(pg, "() => [...document.querySelectorAll('.cell .frame img')].filter(i => i.complete && i.naturalWidth).length >= 6",
                  40, "the visible thumbnails")

    def text(self, pg, needle, timeout_s=20):
        self.wait(pg, "t => document.body.innerText.includes(t)", timeout_s, repr(needle), needle)

    def no_hscroll(self, pg):
        self.assertLessEqual(pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0)

    def assert_line_geometry(self, pg, testid):
        """A status line: the dot on the sentence's first line, in an 18 px gutter of its own; an action beside a sentence
        centres on it, and one that wraps under it starts at the sentence's left edge, not the dot's."""
        g = pg.eval_on_selector(f"[data-testid={testid}]", """sl => {
            const r = e => e && e.getBoundingClientRect().toJSON();
            return {t: r(sl.querySelector('.t')), dot: r(sl.querySelector('.dot')), btn: r(sl.querySelector(':scope > button'))};
        }""")
        t, dot, btn = g["t"], g["dot"], g["btn"]
        self.assertAlmostEqual(dot["top"] + dot["height"] / 2, t["top"] + 10, delta=0.5)  # the first 20 px line
        self.assertAlmostEqual(t["left"] - dot["left"], 18, delta=0.5)
        self.assertIsNotNone(btn)
        if btn["top"] >= t["bottom"] - 0.5:  # wrapped under the sentence
            self.assertAlmostEqual(btn["left"], t["left"], delta=0.5)
        else:
            self.assertAlmostEqual(btn["top"] + btn["height"] / 2, t["top"] + t["height"] / 2, delta=1)

    # ---------------------------------------------------------------- the contact sheet
    def test_contact_sheet_by_day(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        heads = pg.eval_on_selector_all(".day h2", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(heads, ["2026/11/07", "2026/11/06", "2026/11/05"])
        counts = pg.eval_on_selector_all(".day .cnt", "els => els.map(e => parseInt(e.textContent))")
        self.assertEqual(counts, [6, 2, 1])
        # each day head is a SectionHead: the date, then at the rule's right end the count and a Select link
        ends = pg.eval_on_selector_all(".day .sh", "els => els.map(e => e.getBoundingClientRect().right"
                                                   " - e.querySelector('[data-testid=day-select]').getBoundingClientRect().right)")
        for gap in ends:
            self.assertAlmostEqual(gap, 0, delta=1)
        self.assertEqual(pg.eval_on_selector_all("[data-testid=day-select]", "els => els.map(e => e.textContent)"),
                         ["Select"] * 3)
        labels = pg.eval_on_selector_all(".cell .frame", "els => els.map(e => e.getAttribute('aria-label'))")
        self.assertEqual(labels[:3], ["Open photo 5713, 23:07", "Open photo 5712, 23:05", "Open photo 5710, 22:39"])
        self.assertEqual(labels[-1], "Open photo 0001, 07:12")
        # the camera's own EXIF thumbnail, at its native 160 x 120, never upscaled
        sizes = pg.eval_on_selector_all(".cell .frame img", "els => els.slice(0, 6).map(i => [i.naturalWidth, i.naturalHeight, i.getBoundingClientRect().width])")
        for w, h, shown in sizes:
            self.assertEqual((w, h), (160, 120))
            self.assertLessEqual(shown, 160.5)
        surface = pg.inner_text("[data-testid=photos]")
        self.assertNotIn(".JPG", surface)         # file names live under Details only
        self.assertNotIn("100RICOH", surface)
        self.assertNotIn("F2.4", surface)         # no exposure in the grid: /photos carries none
        self.assertIn("Import 9 new", surface)    # nothing imported yet: no per-frame tags
        self.assertEqual(pg.locator(".cell .tag").count(), 0)
        self.assertEqual(pg.inner_text("[data-testid=dest-sample]"), "To ~/Pictures/Koma")  # the day folders: Details
        self.no_hscroll(pg)
        # the card was read through DCIM only, never the root
        sent = [c.strip() for t in br.body.transports for c in t.sent]
        self.assertNotIn("-NLST /EXT0", sent)
        self.assertIn("-NLST /EXT0/DCIM", sent)
        # Shift selects the run from the last box toggled; a day head's Select takes the whole day, then reads Deselect
        pg.click("[data-photo='100RICOH/R0015713.JPG'] .pick")
        pg.click("[data-photo='100RICOH/R0015709.JPG'] .pick", modifiers=["Shift"])
        self.assertEqual(pg.locator(".cell.sel").count(), 4)
        pg.click("[data-testid=day-select] >> nth=1")
        self.assertEqual(pg.locator(".cell.sel").count(), 6)
        self.assertEqual(pg.inner_text("[data-testid=day-select] >> nth=1"), "Deselect")

    def test_thumbnails_load_as_cells_come_into_view(self):
        br = self.bridge()
        PF.many(br.body, 120)
        pg = self.page(br)
        self.wait(pg, "() => document.querySelectorAll('[data-testid=photos] .cell').length === 129", 40, "129 cells")
        self.wait(pg, "() => [...document.querySelectorAll('.cell .frame img')].filter(i => i.complete).length >= 6", 30)
        pg.wait_for_timeout(2500)
        asked = {r for r in self.requests if "thumb=1" in r}
        self.assertLess(len(asked), 80, "thumbnails for cells far out of view were read")
        pg.evaluate("() => document.getElementById('main').scrollTo(0, 1e6)")
        self.wait(pg, "() => { const c = [...document.querySelectorAll('.cell')].pop(); return !!c.querySelector('img'); }", 60,
                  "the last cell's thumbnail after scrolling")

    # ---------------------------------------------------------------- the full view
    def test_full_view_exif_keys_and_focus(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        first = pg.locator(".cell .frame").first
        first.focus()
        pg.keyboard.press("Enter")
        self.wait(pg, "() => document.querySelector('[data-testid=full-photo]')?.naturalWidth > 0", 20, "the full photo")
        self.assertTrue(pg.inner_text("[data-testid=view-taken]").endswith("2026/11/07 · 23:07"))
        self.assertEqual(pg.eval_on_selector_all("[data-testid=view-exposure] span", "e => e.map(x => x.textContent)"),
                         ["F2.4", "1″", "ISO 154"])  # EXPOSURE_ORDER, as the file holds it (1/1 s, 240/100, 154)
        # the readout under the number: exposure, then date and time, no label over values that name themselves (a
        # screen reader hears "Taken" before the date); the spec sheet under it holds the one fact that needs its label
        visible = pg.evaluate("() => [...document.querySelectorAll('[data-testid=photo-view] *')].filter(e => !e.closest('.sr-only'))"
                              ".flatMap(e => [...e.childNodes]).filter(n => n.nodeType === 3).map(n => n.textContent).join(' ')")
        self.assertNotIn("Exposure", visible)
        self.assertNotIn("Taken", visible)
        self.assertEqual(pg.inner_text("[data-testid=view-spec]").split("\n")[0], "Imported")
        self.assertTrue(pg.evaluate("() => { const at = s => document.querySelector(`[data-testid=${s}]`);"
                                    " const after = (a, b) => !!(at(a).compareDocumentPosition(at(b)) & Node.DOCUMENT_POSITION_FOLLOWING);"
                                    " return after('view-exposure', 'view-taken') && after('view-taken', 'view-spec'); }"))
        self.assertEqual(pg.inner_text("[data-testid=view-pos]"), "1 / 9")
        self.assertEqual(pg.inner_text("[data-testid=view-imported]"), "Not yet")
        # the playback keys are icon-only, named for a screen reader; Download is text only
        self.assertEqual(pg.get_attribute("[data-testid=view-next]", "aria-label"), "Next photo")
        self.assertEqual(pg.get_attribute("[data-testid=view-prev]", "aria-label"), "Previous photo")
        self.assertEqual(pg.inner_text("[data-testid=view-next]"), "")
        self.assertEqual(pg.locator("[data-testid=view-download] svg").count(), 0)
        img = pg.eval_on_selector("[data-testid=full-photo]", "i => [i.naturalWidth, i.getBoundingClientRect().width]")
        self.assertEqual(img[0], 1280)
        self.assertLessEqual(img[1], 1280.5)      # never above its own size
        # the file name only under Details
        self.assertNotIn("R0015713.JPG", pg.inner_text("[data-testid=view-spec]"))
        pg.click("[data-testid=photo-view] summary")
        self.assertIn("R0015713.JPG", pg.inner_text("[data-testid=photo-view] .details"))
        # right arrow twice: R0015710, 1/2 s
        pg.keyboard.press("ArrowRight")
        pg.keyboard.press("ArrowRight")
        self.wait(pg, "() => document.querySelector('[data-testid=view-pos]')?.innerText === '3 / 9'", 10)
        self.wait(pg, "() => (document.querySelector('[data-testid=view-exposure]')?.innerText || '').includes('1/2')", 20, "1/2")
        pg.keyboard.press("ArrowLeft")
        self.wait(pg, "() => document.querySelector('[data-testid=view-pos]')?.innerText === '2 / 9'", 10)
        pg.keyboard.press("Escape")
        self.wait(pg, "() => !document.querySelector('dialog[open]')", 10, "closed")
        focused = pg.evaluate("() => document.activeElement.closest('[data-photo]')?.dataset.photo")
        self.assertEqual(focused, "100RICOH/R0015712.JPG")  # back to the photo last shown

    def test_full_view_of_a_file_without_exif(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        pg.click("[data-photo='100RICOH/RIMG0003.JPG'] .frame")
        self.wait(pg, "() => document.querySelector('[data-testid=full-photo]')?.naturalWidth > 0", 20)
        self.assertEqual(pg.locator("[data-testid=view-exposure]").count(), 0)  # absent, not blank
        self.assertEqual(pg.eval_on_selector("[data-testid=photo-view] .exw", "e => e.getBoundingClientRect().height"), 0)
        self.assertIn("2026/11/06", pg.inner_text("[data-testid=view-taken]"))  # the card's date
        img = pg.eval_on_selector("[data-testid=full-photo]", "i => [i.naturalWidth, i.getBoundingClientRect().width]")
        self.assertEqual(img[0], 320)
        self.assertLessEqual(img[1], 320.5)  # a 320 px frame is shown at 320, not stretched
        pg.click("[data-testid=view-close]")
        self.wait(pg, "() => !document.querySelector('dialog[open]')", 10)

    # ---------------------------------------------------------------- import
    def test_import_new_copies_checks_and_remembers(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        pg.click("[data-testid=import]")
        self.text(pg, "Imported 9 photos. Every copy matches the card.", 40)
        for name in FRAMES:
            self.assertEqual((br.folder / "2026" / "11" / "07" / name).read_bytes(), (PF.FRAMES / name).read_bytes(), name)
        self.assertTrue((br.folder / "2026" / "11" / "05" / "RIMG0001.JPG").is_file())
        self.assertEqual(pg.locator("[data-testid=import]").count(), 0)
        # all imported: no frame carries a tag, each day head says so
        self.assertEqual(pg.locator(".cell .tag").count(), 0)
        self.assertEqual(pg.eval_on_selector_all(".day .cnt", "els => els.filter(e => /Imported/.test(e.textContent)).length"), 3)
        # remembered: a fresh page shows them imported and offers nothing
        pg.reload()
        self.sheet_ready(pg)
        self.wait(pg, "() => [...document.querySelectorAll('.day .cnt')].filter(e => /Imported/.test(e.textContent)).length === 3",
                  10, "the day heads say Imported")
        self.assertEqual(pg.locator(".cell .tag").count(), 0)
        self.assertEqual(pg.locator("[data-testid=import]").count(), 0)
        self.assertNotIn("Imported 9 photos", pg.inner_text("[data-testid=photos]"))  # an old result is not news
        # a new photo on the card: only it is new
        PF.add(br.body, "R0015714.JPG", (PF.FRAMES / "R0015710.JPG").read_bytes(), "2026:11:08 09:12:40")
        pg.reload()
        self.wait(pg, "() => document.querySelectorAll('[data-testid=photos] .cell').length === 10", 30)
        self.wait(pg, "() => document.querySelector('[data-testid=import]')?.innerText === 'Import 1 new'", 15)
        new_tags = pg.eval_on_selector_all(".cell", "els => els.filter(e => /new/i.test(e.querySelector('.tag')?.textContent || '')).map(e => e.dataset.photo)")
        self.assertEqual(new_tags, ["100RICOH/R0015714.JPG"])
        self.assertEqual(pg.locator(".cell .tag").count(), 1)  # mixed: NEW marks the new one, an unmarked frame is imported
        # the tags are labels, not targets: clicking one selects nothing
        tag = pg.locator("[data-photo='100RICOH/R0015714.JPG'] .tag")
        self.assertEqual(tag.evaluate("t => !!t.closest('label')"), False)
        tag.click()
        self.assertEqual(pg.locator(".cell.sel").count(), 0)
        pg.click("[data-testid=import]")
        self.text(pg, "Imported 1 photo. The copy matches the card.", 30)
        self.assertTrue((br.folder / "2026" / "11" / "08" / "R0015714.JPG").is_file())
        # the full view says it is imported; where the copy is, only under Details
        pg.click("[data-photo='100RICOH/R0015714.JPG'] .frame")
        self.wait(pg, "() => document.querySelector('[data-testid=full-photo]')?.naturalWidth > 0", 20)
        self.wait(pg, "() => document.querySelector('[data-testid=view-imported]')?.innerText === 'Yes'", 10)
        self.assertNotIn("~/Pictures", pg.inner_text("[data-testid=view-spec]"))
        pg.click("[data-testid=photo-view] summary")
        self.assertEqual(pg.inner_text("[data-testid=view-copy]"), "~/Pictures/Koma/2026/11/08/R0015714.JPG")
        pg.keyboard.press("Escape")
        self.wait(pg, "() => !document.querySelector('dialog[open]')", 10)
        pg.click("[data-photo='100RICOH/R0015714.JPG'] .pick")
        self.assertEqual(pg.locator(".cell.sel").count(), 1)

    def test_import_stop_then_the_rest(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        br.link.cache.clear()
        br.link.cache_bytes = 0
        br.body.retr_bps = 40e3  # about 2 s a frame from here on
        pg.click("[data-testid=import]")
        self.wait(pg, "() => /Copied [1-9] of 9/.test(document.querySelector('[data-testid=import-status]')?.innerText || '')",
                  60, "the first copy")
        self.assert_line_geometry(pg, "import-status")
        pg.click("[data-testid=import-status] button")
        self.wait(pg, "() => /^Stopped\\. \\d of 9 imported\\./.test(document.querySelector('[data-testid=import-status]')?.innerText || '')",
                  30, "stopped")
        done = int(pg.inner_text("[data-testid=import-status]").split(". ")[1].split(" ")[0])
        self.assertGreaterEqual(done, 1)
        self.assertLess(done, 9)
        # newest first, the sheet's order: the imported frames are the first cells, unmarked; NEW marks the rest
        marks = pg.eval_on_selector_all(".cell", "els => els.map(e => /new/i.test(e.querySelector('.tag')?.textContent || ''))")
        self.assertEqual(marks, [False] * done + [True] * (9 - done))
        br.body.retr_bps = 0
        self.wait(pg, f"() => document.querySelector('[data-testid=import]')?.innerText === 'Import {9 - done} new'", 10)
        pg.click("[data-testid=import]")
        self.text(pg, f"Imported {9 - done} photos.", 40)
        self.assertEqual(len([p for p in br.folder.rglob("*.JPG")]), 9)

    def test_import_line_carries_the_result_and_details_the_rest(self):
        br = self.bridge()
        path = "/EXT0/DCIM/100RICOH/R0015709.JPG"
        half = br.body.card[path][:len(br.body.card[path]) // 2]  # the camera is still writing it: no EOI yet
        br.body.card[path] = half
        br.body.folder_entries[:] = [e if e[:8] != b"R0015709" else
                                     mockcard._dirent("R0015709.JPG", 0x20, len(half), br.body.photo_dates[path])
                                     for e in br.body.folder_entries]
        day = br.folder / "2026" / "11" / "07"
        day.mkdir(parents=True)
        (day / "R0015713.JPG").write_bytes(b"another camera's photo")                 # same name, another file
        (day / "R0015712.JPG").write_bytes((PF.FRAMES / "R0015712.JPG").read_bytes())  # already there, byte for byte
        pg = self.page(br)
        self.sheet_ready(pg)
        pg.click("[data-testid=import]")
        # (a count that starts a clause is bound to its verb by a no-break space; read here as a space)
        self.wait(pg, "() => (document.querySelector('[data-testid=import-status]')?.innerText || '').replace(/\\u00a0/g, ' ') === "
                      "'Imported 8 photos. Every copy matches the card. 1 was still being written on the camera.'",
                  40, "the result line")
        pg.click("[data-testid=import-details] summary")
        self.assertEqual(pg.eval_on_selector_all("[data-testid=import-details] li",
                                                 "e => e.map(x => x.textContent.replace(/\\u00a0/g, ' '))"),
                         ["One folder for each day, such as ~/Pictures/Koma/2026/11/07/",
                          "1 was already in the folder.", "R0015713.JPG was saved as R0015713-2.JPG.",
                          "R0015709.JPG was still being written on the camera."])
        self.wait(pg, "() => document.querySelector('[data-testid=import]')?.innerText === 'Import 1 new'", 10)
        self.assertEqual((day / "R0015713.JPG").read_bytes(), b"another camera's photo")

    def slow_import(self, br, pg, copied="[1-9]"):
        br.link.cache.clear()
        br.link.cache_bytes = 0
        br.body.retr_bps = 40e3  # about 2 s a frame from here on
        pg.click("[data-testid=import]")
        self.wait(pg, f"() => /Copied {copied} of 9/.test(document.querySelector('[data-testid=import-status]')?.innerText || '')",
                  60, "the first copy")

    def test_import_halted_by_live_view_names_it_once(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        self.slow_import(br, pg)
        self.assertTrue(KB.tether_start(br.link)["ok"])  # Shoot in another tab
        self.wait(pg, "() => /^Stopped\\. \\d of 9 imported\\.$/.test(document.querySelector('[data-testid=import-status]')?.innerText || '')",
                  30, "the import stopped")
        self.text(pg, "Remote control is on. Photos pause until it is off.")
        body = pg.inner_text("[data-testid=photos-lines]")
        self.assertNotIn("Live view started", body)  # the cause is named once, by the busy line
        self.assertEqual(body.count("Remote control"), 1)
        done = int(pg.inner_text("[data-testid=import-status]").split(". ")[1].split(" ")[0])
        br.body.retr_bps = 0
        pg.click("[data-testid=busy-action]")
        self.wait(pg, f"() => document.querySelector('[data-testid=import]')?.innerText === 'Import {9 - done} new'", 20)
        self.assertFalse(br.link.remote_on)

    def test_import_across_a_bridge_restart(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        self.slow_import(br, pg)
        old = br.importer
        br.importer = PF.importer(br.link, br.root)  # what a restart gives: job ids start again, the job is gone
        old.stop()
        self.text(pg, "Koma restarted. The import stopped.", 20)
        self.wait(pg, "() => /^Import [1-8] new$/.test(document.querySelector('[data-testid=import]')?.innerText || '')", 20)
        old.wait(20)
        br.body.retr_bps = 0
        br.importer = PF.importer(br.link, br.root)  # its list now holds what the old process copied
        pg.click("[data-testid=import]")              # job 1 of the new process: shown, not taken for an old one
        self.wait(pg, "() => /^Imported [1-8] photos?\\./.test(document.querySelector('[data-testid=import-status]')?.innerText || '')",
                  40, "the new process's result")
        self.assertEqual(len(list(br.folder.rglob("*.JPG"))), 9)

    def test_choose_folder(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        pg.click("[data-testid=choose-folder]")
        self.wait(pg, "() => !!document.querySelector('[data-testid=folder-sheet][open]')", 5)
        self.assertEqual(pg.input_value("#folder-path"), "~/Pictures/Koma")
        pg.fill("#folder-path", "Pictures/GR")
        self.assertIn("Pictures/GR/2026/11/07/", pg.inner_text("#folder-help"))  # the live sample follows the typing
        pg.click("[data-testid=folder-use]")
        self.text(pg, "Type the full path, such as ~/Pictures/Koma.")
        # the refusal's dot hangs on the first 16 px line of the sentence, in the status line's 18 px gutter
        dot = pg.eval_on_selector("#folder-err", "p => { const d = p.querySelector('.dot').getBoundingClientRect(),"
                                                 " r = p.getBoundingClientRect(); return [d.top + d.height / 2 - r.top, d.left - r.left]; }")
        self.assertAlmostEqual(dot[0], 8, delta=0.5)
        self.assertAlmostEqual(dot[1], 0, delta=0.5)
        pg.fill("#folder-path", "~/Pictures/GR")
        pg.click("[data-testid=folder-use]")
        self.wait(pg, "() => !document.querySelector('[data-testid=folder-sheet][open]')", 10, "the sheet closed")
        self.wait(pg, "() => document.querySelector('[data-testid=dest-sample]')?.innerText === 'To ~/Pictures/GR'", 10)
        pg.click("[data-testid=import]")
        self.text(pg, "Imported 9 photos.", 40)
        self.assertTrue((br.root / "home" / "Pictures" / "GR" / "2026" / "11" / "07" / "R0015713.JPG").is_file())
        self.assertFalse(br.folder.exists())

    # ---------------------------------------------------------------- download
    def selected_is(self, pg, n):
        """The selection's count: in the phone's bar, or in the desktop button's own words."""
        if PHONE_RUN:
            self.wait(pg, "n => (document.querySelector('[data-testid=sel-count]')?.innerText || '').startsWith(n + ' selected')", 5,
                      f"{n} selected", n)
        else:
            want = "Download 1 photo" if n == 1 else f"Download {n} photos"
            self.wait(pg, "t => document.querySelector('[data-testid=download-selected]')?.innerText === t", 5, want, want)

    def saved(self, got) -> dict:
        """name -> bytes of what arrived; a desktop's several photos come as one ZIP, unpacked here."""
        out = {}
        for d in got:
            data = Path(d.path()).read_bytes()
            if d.suggested_filename.endswith(".zip"):
                with zipfile.ZipFile(d.path()) as z:
                    self.assertIsNone(z.testzip())
                    for i in z.infolist():
                        self.assertEqual(i.compress_type, zipfile.ZIP_STORED)
                        out[i.filename] = z.read(i)
            else:
                out[d.suggested_filename] = data
        return out

    def test_download_selected(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        pg.click("[data-photo='100RICOH/R0015713.JPG'] .pick")
        pg.click("[data-photo='100RICOH/R0015710.JPG'] .pick")
        self.selected_is(pg, 2)
        self.assertEqual(pg.locator(".cell.sel").count(), 2)
        downloads = []
        pg.on("download", lambda d: downloads.append(d))
        pg.click("[data-testid=download-selected]")
        for _ in range(100):
            if len(downloads) >= (2 if PHONE_RUN else 1):
                break
            pg.wait_for_timeout(100)
        # a desktop saves several as one ZIP named for the day; a phone saves one file after another
        names = [d.suggested_filename for d in downloads]
        self.assertEqual(names, ["R0015713.JPG", "R0015710.JPG"] if PHONE_RUN else ["Koma 2026-11-07.zip"])
        got = self.saved(downloads)
        self.assertEqual(set(got), {"R0015713.JPG", "R0015710.JPG"})
        for name, data in got.items():
            self.assertEqual(data, (PF.FRAMES / name).read_bytes())
        self.text(pg, "Downloaded 2 photos.")
        # done, with the done dot, in the status line on a desktop and in the bottom bar on a phone
        where = "[data-testid=sel-bar]" if PHONE_RUN else "[data-testid=photos-lines]"
        self.assertEqual(pg.locator(f"{where} [data-testid=download-status].ok").count(), 1)
        pg.click("[data-photo='100RICOH/R0015713.JPG'] .pick")  # untick one: the selection goes on
        self.selected_is(pg, 1)
        pg.get_by_role("button", name="Deselect", exact=True).click()
        self.wait(pg, "() => document.querySelectorAll('.cell.sel').length === 0", 5, "nothing selected")

    def test_selection_does_not_wait_for_the_import_folder(self):
        br = self.bridge()
        pg = self.page(br, before=lambda pg: pg.route("**/photos/import*", lambda r: r.abort()))  # that read misses
        self.sheet_ready(pg)
        pg.click("[data-photo='100RICOH/R0015713.JPG'] .pick")
        self.selected_is(pg, 1)
        self.assertTrue(pg.is_enabled("[data-testid=download-selected]"))
        self.assertEqual(pg.locator("[data-testid=import]").count(), 0)  # what is new is unknown: no count to offer
        self.assertEqual(pg.locator("[data-testid=dest-sample]").count(), 0)

    def downloads(self, pg) -> list:
        got = []
        pg.on("download", lambda d: got.append(d))
        return got

    def test_download_that_fails_tries_again_from_where_it_stopped(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        for name in ("R0015713", "R0015710", "R0015709"):
            pg.click(f"[data-photo='100RICOH/{name}.JPG'] .pick")
        gone = br.body.card.pop("/EXT0/DCIM/100RICOH/R0015710.JPG")  # went from the card after it was listed
        br.link.cache.clear()
        br.link.cache_bytes = 0
        got = self.downloads(pg)
        pg.click("[data-testid=download-selected]")
        self.text(pg, "Photo 2 of 3 did not arrive from the camera. The download stopped.")
        br.body.card["/EXT0/DCIM/100RICOH/R0015710.JPG"] = gone
        if PHONE_RUN:
            # the error form in the bar too: the failed dot and the sentence, above the selection row and its one action
            self.assertEqual(pg.locator("[data-testid=sel-bar] [data-testid=download-status].bad").count(), 1)
            self.assertEqual(pg.inner_text("[data-testid=sel-count]"), "3 selected")
            self.assertEqual(pg.locator("[data-testid=sel-bar] [data-testid=download-status] button").count(), 0)
            self.assertEqual(pg.inner_text("[data-testid=download-selected]"), "Try again")  # one action, in the bar
            pg.click("[data-testid=download-selected]")
        else:
            pg.click("[data-testid=download-again]")
        self.text(pg, "Downloaded 3 photos.")
        pg.wait_for_timeout(300)
        self.assertEqual(sorted(self.saved(got)), ["R0015709.JPG", "R0015710.JPG", "R0015713.JPG"])  # each once

    def test_download_paused_by_live_view_carries_on_after(self):
        br = self.bridge()
        pg = self.page(br)
        self.sheet_ready(pg)
        for name in ("R0015713", "R0015712", "R0015710"):
            pg.click(f"[data-photo='100RICOH/{name}.JPG'] .pick")
        br.link.cache.clear()
        br.link.cache_bytes = 0
        br.body.retr_bps = 60e3  # about 1.5 s a frame
        got = self.downloads(pg)
        pg.click("[data-testid=download-selected]")
        self.wait(pg, "() => /Downloading 2 of 3/.test(document.body.innerText)", 20, "the second download")
        self.assertTrue(KB.tether_start(br.link)["ok"])  # Shoot in another tab
        self.text(pg, "Stopped. 2 of 3 downloaded.", 30)
        self.text(pg, "Photos pause until it is off.")  # the busy line names the holder, once, with its action
        br.body.retr_bps = 0
        pg.click("[data-testid=busy-action]")
        self.wait(pg, "() => !/Photos pause/.test(document.body.innerText)", 20, "the camera handed back")
        pg.click("[data-testid=download-selected]")
        self.text(pg, "Downloaded 3 photos.")
        pg.wait_for_timeout(300)
        self.assertEqual(sorted(self.saved(got)), ["R0015710.JPG", "R0015712.JPG", "R0015713.JPG"])

    # ---------------------------------------------------------------- states
    def test_live_view_on_then_stop_it(self):
        br = self.bridge()
        self.assertTrue(KB.tether_start(br.link)["ok"])
        stop = threading.Event()

        def stream():  # a live view client (another tab's Shoot)
            try:
                with urllib.request.urlopen(f"{br.url}live?mode=1&cid=other", timeout=30) as r:
                    while not stop.is_set() and r.read(4096):
                        pass
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=stream, daemon=True).start()
        self.addCleanup(stop.set)
        for _ in range(100):
            if br.link.live.snapshot()["running"]:
                break
            time.sleep(0.05)
        pg = self.page(br)
        self.text(pg, "Live view is on. Photos pause while it runs.", 30)
        self.assert_line_geometry(pg, "photos-status")
        self.assertEqual(pg.locator("[data-testid=import]").count(), 0)
        pg.click("[data-testid=busy-action]")
        self.sheet_ready(pg)
        self.assertFalse(br.link.remote_on)
        self.assertNotIn("Photos pause", pg.inner_text("[data-testid=photos]"))

    def test_leaving_shoot_hands_the_camera_back(self):
        br = self.bridge()
        pg = self.page(br, "#/shoot")
        self.wait(pg, "() => !document.querySelector('[data-testid=shutter]')?.disabled", 40, "Shoot armed")
        seen = []
        pg.evaluate("() => { location.hash = '#/photos'; }")
        t0 = time.time()
        while time.time() - t0 < 30:
            txt = pg.inner_text("main")
            seen.append("Photos pause" in txt)
            if pg.locator("[data-testid=photos] .cell").count() == 9:
                break
            time.sleep(0.2)
        self.assertEqual(pg.locator("[data-testid=photos] .cell").count(), 9)
        self.assertNotIn(True, seen, "Photos said busy while Shoot was letting go")

    def test_empty_card(self):
        br = self.bridge(empty=True)
        pg = self.page(br)
        self.wait(pg, "() => !!document.querySelector('[data-testid=photos-empty]')", 20)
        self.assertEqual(pg.inner_text("[data-testid=photos-empty]").split("\n")[-1], "The card holds no photos.")
        self.assertEqual(pg.locator("[data-testid=import]").count(), 0)
        self.no_hscroll(pg)

    def test_both_themes_and_no_horizontal_scroll(self):
        br = self.bridge()
        for scheme in ("light", "dark"):
            pg = self.page(br, scheme=scheme)
            self.sheet_ready(pg)
            self.no_hscroll(pg)
            pg.click(".cell .frame >> nth=0")
            self.wait(pg, "() => document.querySelector('[data-testid=full-photo]')?.naturalWidth > 0", 20)
            self.no_hscroll(pg)
            if PHONE_RUN:
                box = pg.eval_on_selector("[data-testid=full-photo]", "i => i.getBoundingClientRect().toJSON()")
                self.assertLessEqual(box["right"], PHONE["width"] + 0.5)


if __name__ == "__main__":
    unittest.main()
