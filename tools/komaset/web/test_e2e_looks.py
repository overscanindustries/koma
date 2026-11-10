#!/usr/bin/env python3
"""End-to-end: the Looks area of the Koma app in headless Chrome against komabridge + the SIMULATED camera.

Same harness as test_e2e_app.py. The simulated camera runs Koma with the Look IA (MONO on it, the 'RCP6' look store at
its defaults, the ADJ LEVER row 0x8ce in range; two tests keep the 0xff of builds before the Look IA) and
gets a real black and white frame R0015710 (faintly warm-toned; tools/komaapp/design/samples/R0015710.JPG) as its
newest photo, taken today at 14:32 unless a test says otherwise. A black and white last photo is the usual case:
the gallery draws on the bundled color sample then (R0015657, tools/komaapp/src/assets/looks/sample.jpg), and the
editor's split on the photo itself. Every write is checked on the simulated card's PARAM.CUR after the Apply: the look id, IMG SET, the editor
rows, the records, the FX table and the ADJ LEVER mask, against the layout of tools/koma/features/bwrecipes.py.

Everything here is MOCK: it proves the app, the bridge and the simulated camera agree, not that a camera does any of it.
The simulated camera copies an imported PARAM.DAT as it is; it does not run bwrecipes' `sync`.

    (cd tools/komaapp && npm run build)
    python3 -m pytest tools/komaset/web/test_e2e_looks.py -v      # KOMA_E2E_VIEWPORT=phone for 390 px

The first test needs no browser: tools/komaapp/src/lib/looksData.js must equal what tools/komaapp/scripts/looks_data.py
generates from tools/koma today.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1] / "komaapp"
sys.path.insert(0, str(HERE))

from test_e2e_app import Bridge, launch, sync_playwright, KB, VIEW, PHONE  # noqa: E402  (helpers only)
from mockcard import FOLDER, _dirent, block_checksum, DEMO_CUR  # noqa: E402

PHOTO = APP / "design" / "samples" / "R0015710.JPG"             # black and white, faintly warm-toned
SAMPLE = APP / "src" / "assets" / "looks" / "sample.jpg"         # the bundled sample: color (R0015657)
ACT, IMGSET, ROWS, GRAIN, ADJROW, REC, FXT, ADJM, STDREC = 0x8C0, 0x030, 0x8C1, 0x8CC, 0x8CE, 0x910, 0xA00, 0xA18, 0xA1C
ORDER = ["STANDARD", "MONO", "SOFT MONO", "HARD MONO", "HICON B&W", "SHINJUKU", "FILM 400", "FINE 100", "DENSE", "NOIR",
         "MATTE", "SELECT", "POSITIVE", "NEGATIVE", "SLIDE 50", "WARM NEG", "BLEACH", "RETRO", "CUSTOM 1", "CUSTOM 2",
         "CUSTOM 3"]
ADJ_ON = {"STANDARD", "MONO", "HARD MONO", "HICON B&W", "SHINJUKU", "POSITIVE", "CUSTOM 1"}
ID = {"MONO": 1, "SHINJUKU": 2, "HICON B&W": 3, "HARD MONO": 4, "NOIR": 9, "POSITIVE": 12, "CUSTOM 2": 19, "STANDARD": 21}


def free_port():
    """A free port in this file's test range (8872..8971)."""
    for port in range(8872, 8972):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def record(block, look_id):
    o = STDREC if look_id == 21 else REC + 12 * (look_id - 1)
    return bytes(block[o:o + 12])


def record_ok(rec):
    x = 0x5A
    for b in rec[:10]:
        x ^= b
    return rec[10] == 0xA5 and rec[11] == x


def adj_mask(block):
    m = block[ADJM] | (block[ADJM + 1] << 8) | (block[ADJM + 2] << 16)
    return m if (block[ADJM] ^ block[ADJM + 1] ^ block[ADJM + 2] ^ 0x5A) == block[ADJM + 3] else None


TODAY_1432 = time.strftime("%Y:%m:%d") + " 14:32:10"     # taken today: the source line gives the time alone


def with_photo(br, name="R0015710.JPG", stamp=None, on_card=True, photo=PHOTO):
    """The JPEG as the newest photo of 100RICOH, taken today at 14:32 unless `stamp` says otherwise. on_card=False
    lists it (-NLST) without the file: a read that fails."""
    stamp = stamp or TODAY_1432
    data = photo.read_bytes()
    full = f"{FOLDER}/{name}"
    if on_card:
        br.body.card[full] = data
    br.body.photo_dates[full] = stamp
    br.body.folder_entries.append(_dirent(name, 0x20, len(data), stamp))
    br.body.next_number += 1
    return br


def set_bytes(br, changes):
    """PARAM.CUR and the live block in RAM (-KGET, which the app reads on a Koma camera) both take the changes."""
    cur = bytearray(br.body.card["/EXT0/PARAM.CUR"])
    for off, val in changes.items():
        cur[off] = val
        if getattr(br.body, "ram", None) is not None:
            br.body.ram[off] = val
    cur[0xFFC:] = block_checksum(cur)
    br.body.card["/EXT0/PARAM.CUR"] = bytes(cur)


def record_bytes(look_id, values):
    """A valid 12-byte look record: p0..p8, kind, 0xa5, xor of bytes 0..9 with 0x5a."""
    x = 0x5A
    for v in values:
        x ^= v
    o = STDREC if look_id == 21 else REC + 12 * (look_id - 1)
    return dict(zip(range(o, o + 12), list(values) + [0xA5, x]))


# The mean chroma of a canvas in the page (8-bit Cb / Cr codes): about 0 for a neutral black and white rendering.
CHROMA = """sel => { const c = document.querySelector(sel), g = c.getContext('2d'), d = g.getImageData(0, 0, c.width, c.height).data;
    let b = 0, r = 0, n = 0; for (let i = 0; i < d.length; i += 4 * 7) { b += -0.168736 * d[i] - 0.331264 * d[i + 1] + 0.5 * d[i + 2];
    r += 0.5 * d[i] - 0.418688 * d[i + 1] - 0.081312 * d[i + 2]; n++; } return Math.hypot(b / n, r / n); }"""


READY = ("() => { const f = [...document.querySelectorAll('[data-testid=looks] .frame')];"
         " return f.length >= 21 && f.every(x => x.classList.contains('ready')); }")


class LooksDataTest(unittest.TestCase):
    def test_looks_data_matches_the_firmware_sources(self):
        r = subprocess.run([sys.executable, str(APP / "scripts" / "looks_data.py"), "--check"], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_try_on_tone_curves_equal_the_cameras(self):
        """The try-on's curve() (looksRender.js, in node) against bwrecipes.curve() for all 21 looks, all 4096 entries."""
        import json
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        sys.path.insert(0, str(APP.parent))
        from koma.features import bwrecipes as bw
        want = {r["name"]: list(bw.curve(r["params"])) for r in bw.ALL}
        js = ("const R = await import(process.argv[1]); const {LOOKS_DATA: D} = await import(process.argv[2]);"
              "const out = {}; D.names.forEach((n, i) => { out[n] = Array.from(R.curve(D.defaults[i].slice(0, 9))); });"
              "console.log(JSON.stringify(out));")
        lib = APP / "src" / "lib"
        r = subprocess.run([node, "--input-type=module", "-e", js, (lib / "looksRender.js").as_uri(),
                            (lib / "looksData.js").as_uri()], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(sorted(got), sorted(want))
        for name in want:
            self.assertEqual(got[name], want[name], name)


@unittest.skipIf(sync_playwright is None, "playwright not installed")
@unittest.skipIf(KB.app_index() is None, "the app is not built (cd tools/komaapp && npm run build)")
class LooksE2E(unittest.TestCase):
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
        self.bridges, self.contexts, self.errors, self.calls, self.urls, self.answers = [], [], [], [], [], []

    def tearDown(self):
        for c in self.contexts:
            c.close()
        for b in self.bridges:
            try:
                b.stop()
            except Exception:  # noqa: BLE001
                pass
        self.assertEqual(self.errors, [], "page errors")

    def bridge(self, photo=True, look_ia=True, **kw):
        """look_ia: the simulated camera runs the Look IA module (its ADJ LEVER row 0x8ce in range: MONO's ON, 0). The
        simulated camera's own block is a Koma 0.3 store with 0xff there, as builds before the Look IA leave it (look_ia=False)."""
        b = Bridge(port=free_port(), **kw)
        self.bridges.append(b)
        if look_ia:
            set_bytes(b, {ADJROW: 0})
        return with_photo(b) if photo else b

    def page(self, br, hash_="#/looks", viewport=None, scheme="dark", wait_s=None, abort=()):
        """abort: URL patterns whose requests fail (the bundled sample photo, to test the page without any photo)."""
        ctx = self.browser.new_context(viewport=viewport or VIEW, color_scheme=scheme, reduced_motion="reduce")
        self.contexts.append(ctx)
        for pattern in abort:
            ctx.route(pattern, lambda route: route.abort())
        ctx.add_init_script("delete window.showSaveFilePicker;")
        # setup was done once on this computer: the first-run wizard (test_e2e_onboard.py) stays out of these tests
        ctx.add_init_script("localStorage.setItem('koma.setup', JSON.stringify({v: 1, seen: true, active: false, done: true, "
                            "plan: 'wizard', step: 0, sub: 0}));")
        if wait_s:
            ctx.add_init_script(f"window.KOMA_WAIT_S = {wait_s};")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: self.errors.append(f"console: {m.text}")
              if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.on("request", lambda r: self.calls.append((r.method, r.url.split(str(br.port), 1)[-1].split("?")[0])))
        pg.on("request", lambda r: self.urls.append(r.url.split(str(br.port), 1)[-1]))
        pg.on("response", lambda r: self.answers.append((r.request.method, r.url.split(str(br.port), 1)[-1], r.status)))
        pg.goto(br.url + hash_)
        return pg

    def wait(self, pg, js, timeout_s=30, what=""):
        try:
            pg.wait_for_function(js, timeout=timeout_s * 1000, polling=200)
        except Exception as error:  # noqa: BLE001
            text = pg.evaluate("() => (document.querySelector('dialog[open]')?.innerText || '') + '\\n---\\n' + document.body.innerText.slice(0, 700)")
            raise AssertionError(f"timed out after {timeout_s} s waiting for {what or js}; page text:\n{text}") from error

    def bar(self, pg):
        return pg.inner_text("[data-testid=looks-bar]")

    def gallery(self, br, **kw):
        pg = self.page(br, **kw)
        self.wait(pg, READY, 40, "every look drawn on the last photo")
        return pg

    def apply_done(self, pg, done=None):
        """Wait for the Apply sheet's done line (a look's own: 'NOIR is active.', 'Applied to NOIR.'), then close it."""
        self.wait(pg, "() => !!document.querySelector('[data-testid=apply-status] .ok')", 60,
                  "send, restart, reconnect and read back")
        if done is not None:
            self.assertEqual(pg.inner_text("[data-testid=apply-status] .ok .t").strip(), done)
        pg.click("[data-testid=apply-sheet] >> text=Done")

    def card_cur(self, br):
        return br.body.card["/EXT0/PARAM.CUR"]

    # ---------------------------------------------------------------- the gallery
    def test_gallery_lists_the_21_looks_in_gr_iii_order_on_the_sample_for_a_black_and_white_photo(self):
        br = self.bridge()
        pg = self.gallery(br)
        names = pg.eval_on_selector_all("[data-testid=looks] label.card .name", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(names, ORDER)
        heads = pg.eval_on_selector_all("[data-testid=looks] h2", "els => els.map(e => e.textContent.trim().toUpperCase())")
        self.assertEqual(heads, ["MONOCHROME", "COLOR", "CUSTOM"])          # STANDARD first, under no head
        on = pg.eval_on_selector_all("label.card.on .name", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(on, ["MONO"])                                    # the simulated camera has MONO on it
        adj = pg.eval_on_selector_all("label.card", "els => els.filter(e => e.querySelector('[data-testid=adj-tag]'))"
                                                    ".map(e => e.querySelector('.name').textContent.trim())")
        self.assertEqual(set(adj), ADJ_ON)                                # the default ADJ LEVER looks
        # the last photo is black and white: the cards are drawn on the color sample, and Details says why
        src = pg.inner_text("[data-testid=looks-source]")
        self.assertIn("Drawn on a sample photo.", src)
        self.assertIn("APPROXIMATE", src.upper())
        pg.click("[data-testid=looks-details] summary")
        self.assertIn("The last photo is black and white, so the filters and SELECT's color would not show.",
                      pg.inner_text("[data-testid=looks-details]"))
        pg.click("[data-testid=looks-details] summary")
        self.assertIn("On the camera.", self.bar(pg))
        self.assertNotIn("RECIPE", pg.inner_text("main").upper())
        self.assertNotIn("R0015710", pg.inner_text("main"))              # the file name lives under Details only
        # the photo is read once: the newest-only list, one whole JPEG; nothing else touches the camera, nothing is
        # written
        gets = [c for c in self.calls if c[1].startswith("/photos")]
        self.assertEqual(gets, [("GET", "/photos"), ("GET", "/photos/100RICOH/R0015710.JPG")])
        self.assertIn("/photos?newest=1", self.urls)
        self.assertFalse([c for c in self.calls if c[0] != "GET" or c[1].startswith(("/live", "/tether", "/keys", "/reboot"))])
        self.assertEqual(pg.locator("[data-testid^=note-]").count(), 0)  # no notes about what a photo cannot show
        # the renders differ: SHINJUKU is not MONO, and POSITIVE is in color on the sample
        diff = pg.evaluate("""() => { const c = n => document.querySelector('label[data-look=' + n + '] canvas');
            const a = c('mono').getContext('2d').getImageData(0, 0, 40, 30).data, b = c('shinjuku').getContext('2d').getImageData(0, 0, 40, 30).data;
            let d = 0; for (let i = 0; i < a.length; i++) d += Math.abs(a[i] - b[i]); return d; }""")
        self.assertGreater(diff, 1000)
        self.assertGreater(pg.evaluate(CHROMA, "label[data-look=positive] canvas"), 3.0)

    def test_a_color_last_photo_is_drawn_on(self):
        br = self.bridge(photo=False)
        with_photo(br, name="R0015799.JPG", photo=SAMPLE)
        pg = self.gallery(br)
        self.assertIn("Drawn on the last photo, taken 14:32.", pg.inner_text("[data-testid=looks-source]"))
        pg.click("[data-testid=looks-details] summary")
        self.assertNotIn("Why a sample", pg.inner_text("[data-testid=looks-details]"))

    def test_set_a_look_on_the_camera_reads_it_back(self):
        br = self.bridge()
        pg = self.gallery(br)
        polls = br.body.poff_count
        pg.click("label[data-look=noir]")
        self.assertIn("The camera restarts to load it.", self.bar(pg))
        self.assertEqual(pg.locator("label.card.on[data-look=noir]").count(), 0)     # nothing marked before read-back
        pg.click("[data-testid=set-look]")
        self.wait(pg, "() => /Setting NOIR on the camera/.test(document.querySelector('[data-testid=apply-sheet]')?.innerText || '')",
                  10, "the Apply sheet")
        # the phases in present participles, the done line naming the look, and what keeps it
        self.assertEqual(pg.eval_on_selector_all("[data-testid=apply-sheet] [data-phase] .lab", "els => els.map(e => e.childNodes[0].textContent.trim())"),
                         ["Sending the settings", "Restarting the camera", "Waiting for the camera", "Reading back"])
        self.wait(pg, "() => !!document.querySelector('[data-testid=apply-status] .ok')", 60, "the read-back")
        self.assertIn("The camera keeps the look once it is turned off with its power button.",
                      pg.inner_text("[data-testid=apply-status]"))
        self.assertEqual(pg.locator("[data-testid=apply-sheet] [data-result]").count(), 0)   # the done line confirms it
        self.apply_done(pg, "NOIR is active.")
        self.assertEqual(br.body.poff_count, polls + 1)
        cur = self.card_cur(br)
        self.assertEqual(cur[ACT], ID["NOIR"])
        self.assertEqual(cur[IMGSET], 3)                                   # NOIR's base: B&W
        self.assertEqual(list(cur[ROWS:ROWS + 9]), [3, 6, 2, 4, 4, 3, 0, 2, 3])   # NOIR's values in the editor rows
        self.wait(pg, "() => !!document.querySelector('label.card.on[data-look=noir]')", 10, "NOIR marked on the camera")
        self.assertEqual(pg.locator("label.card.on").count(), 1)
        self.assertIn("Now on the camera.", self.bar(pg))
        # the restart was this Apply's own: the photo is not asked for again on the new connection
        pg.wait_for_timeout(1500)
        self.assertEqual([c for c in self.calls if c[1] == "/photos"], [("GET", "/photos")])

    def test_standard_at_its_stock_values_is_plain_normal(self):
        br = self.bridge()
        pg = self.gallery(br)
        pg.click("label[data-look=standard]")
        pg.click("[data-testid=set-look]")
        self.apply_done(pg)
        cur = self.card_cur(br)
        self.assertEqual((cur[ACT], cur[IMGSET]), (0, 1))                  # no look id, IMG SET NORMAL
        self.wait(pg, "() => !!document.querySelector('label.card.on[data-look=standard]')", 10, "STANDARD on the camera")

    def test_standard_edited_takes_its_id_and_back_at_stock_is_normal(self):
        br = self.bridge()
        set_bytes(br, {ACT: 0, IMGSET: 1})                                 # plain NORMAL: STANDARD on the camera
        pg = self.open_editor(br, "standard")
        self.assertEqual(pg.locator(".ed-pic.on").count(), 1)              # the amber rule: STANDARD is on it
        pg.focus("#d-contrast")
        pg.keyboard.press("ArrowRight")
        pg.click("[data-testid=apply-look]")
        self.apply_done(pg)
        cur = self.card_cur(br)
        self.assertEqual((cur[ACT], cur[IMGSET]), (ID["STANDARD"], 1))     # edited, it has its look id
        self.assertTrue(record_ok(record(cur, ID["STANDARD"])))
        self.assertEqual(cur[ROWS + 1], 5)
        pg.click("[data-testid=return-to-stock]")
        pg.click("[data-testid=apply-look]")
        self.apply_done(pg)
        cur = self.card_cur(br)
        self.assertEqual((cur[ACT], cur[IMGSET]), (0, 1))                  # at stock: plain NORMAL, no look id
        self.assertNotEqual(record(cur, ID["STANDARD"])[10], 0xA5)

    def test_pending_camera_menu_edits_stay_pending_through_a_look(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/camera")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 5", 20, "the camera menu")
        pg.click("[data-row=iso] button.head")
        want = pg.eval_on_selector("[data-row=iso] select", "s => [...s.options].map(o => o.value).find(v => v !== s.value)")
        pg.select_option("[data-row=iso] select", want)
        self.wait(pg, "() => /1\\s*change/i.test(document.querySelector('[data-testid=pending-bar]')?.innerText || '')", 5)
        before_iso = self.card_cur(br)[0x059]
        pg.goto(br.url + "#/looks")
        self.wait(pg, READY, 40)
        pg.click("label[data-look=shinjuku]")
        pg.click("[data-testid=set-look]")
        self.apply_done(pg)
        cur = self.card_cur(br)
        self.assertEqual(cur[ACT], ID["SHINJUKU"])
        self.assertEqual(cur[0x059], before_iso)                           # the ISO edit was not sent with the look
        pg.goto(br.url + "#/settings/camera")
        self.wait(pg, "() => /1\\s*change/i.test(document.querySelector('[data-testid=pending-bar]')?.innerText || '')", 10,
                  "the ISO change still pending")

    # ---------------------------------------------------------------- the editor
    def open_editor(self, br, slug):
        pg = self.page(br, "#/looks/" + slug)
        self.wait(pg, "() => document.querySelector('.split .frame')?.classList.contains('ready')", 40, "the before / after")
        return pg

    def test_edit_a_look_that_is_not_on_the_camera(self):
        br = self.bridge()
        pg = self.open_editor(br, "hard-mono")
        self.assertEqual(pg.inner_text("h1").strip(), "HARD MONO")
        labels = pg.eval_on_selector_all(".ed-rows .lab", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(labels, ["CURVE", "CONTRAST", "KEY", "SHADOW", "HIGHLIGHT", "FILTER EFF.", "TONING", "TONE LEVEL",
                                  "SHARPNESS", "GRAIN", "VIGNETTE", "ADJ LEVER"])
        self.assertEqual(pg.eval_on_selector("#d-contrast", "e => e.getAttribute('aria-valuetext')"), "+2")
        pg.focus("#d-contrast")
        pg.keyboard.press("ArrowRight")
        self.wait(pg, "() => /1 change/i.test(document.querySelector('[data-testid=look-changes]')?.innerText || '')", 5)
        self.assertIn("The camera restarts to store it. MONO stays in use.", pg.text_content("[data-testid=look-changes]"))  # hidden on a phone by design
        pg.select_option("#s-p5", "3")                                     # FILTER EFF. RED
        self.wait(pg, "() => /2 changes/i.test(document.querySelector('[data-testid=look-changes]')?.innerText || '')", 5)
        self.assertIn("The camera restarts to store them. MONO stays in use.", pg.text_content("[data-testid=look-changes]"))  # hidden on a phone by design
        pg.click("[data-testid=look-changes] >> text=Review")
        review = pg.inner_text("[data-testid=look-review]")
        self.assertIn("CONTRAST", review)
        self.assertIn("FILTER EFF.", review)
        pg.click("[data-testid=look-review] >> text=Apply to camera")
        self.wait(pg, "() => /Applying 2 changes to HARD MONO/.test(document.querySelector('[data-testid=apply-sheet]')?.innerText || '')",
                  10, "the Apply sheet titled with what it does")
        self.wait(pg, "() => !!document.querySelector('[data-testid=apply-status] .ok')", 60, "the read-back")
        self.assertIn("Turn the camera off once so it keeps the changes.", pg.inner_text("[data-testid=apply-status]"))
        self.apply_done(pg, "Applied to HARD MONO.")
        cur = self.card_cur(br)
        rec = record(cur, ID["HARD MONO"])
        self.assertTrue(record_ok(rec))
        self.assertEqual(list(rec[:10]), [2, 7, 4, 4, 4, 3, 3, 0, 3, 0])   # contrast +3, RED; BLUE toning level 1 kept
        self.assertEqual(cur[ACT], ID["MONO"])                             # still MONO on the camera
        self.assertEqual(list(cur[ROWS:ROWS + 9]), [0, 4, 4, 4, 4, 0, 0, 2, 3])   # MONO's rows untouched
        self.assertEqual(pg.locator("[data-testid=look-changes]").count(), 0)

    def test_edit_the_look_on_the_camera_moves_its_rows_too(self):
        br = self.bridge()
        pg = self.open_editor(br, "mono")
        pg.select_option("#s-p6", "1")                                     # TONING SEPIA: the base becomes B&W (TE)
        pg.click("[data-testid=apply-look]")
        self.apply_done(pg)
        cur = self.card_cur(br)
        rec = record(cur, ID["MONO"])
        self.assertTrue(record_ok(rec))
        self.assertEqual(rec[6], 1)
        self.assertEqual(cur[ROWS + 6], 1)                                 # the editor row follows the record
        self.assertEqual((cur[ACT], cur[IMGSET]), (ID["MONO"], 6))         # IMG SET follows the toned base

    def test_adj_lever_and_grain_per_look(self):
        br = self.bridge()
        row_before = self.card_cur(br)[ADJROW]
        pg = self.open_editor(br, "noir")
        pg.check("[data-row=adj] input[value=true]")
        pg.focus("#d-grain")
        pg.keyboard.press("ArrowRight")
        pg.keyboard.press("ArrowRight")
        pg.click("[data-testid=apply-look]")
        self.apply_done(pg)
        cur = self.card_cur(br)
        mask = adj_mask(cur)
        self.assertIsNotNone(mask, "the ADJ LEVER mask's check byte")
        self.assertTrue(mask >> (ID["NOIR"] - 1) & 1)
        self.assertEqual(mask & ~(1 << (ID["NOIR"] - 1)), 1181711)         # the default looks stay ON
        self.assertEqual(cur[FXT + ID["NOIR"] - 1], 0x50 | 2)              # GRAIN 2, VIGNETTE OFF
        self.assertEqual(cur[ADJROW], row_before)                          # MONO's own row: untouched
        pg.click("a.back")
        self.wait(pg, READY, 40)
        tagged = pg.eval_on_selector("label[data-look=noir]", "e => !!e.querySelector('[data-testid=adj-tag]')")
        self.assertTrue(tagged)

    def test_undo_review_undo_and_return_to_stock(self):
        br = self.bridge()
        # the camera holds an edited CUSTOM 2 (a color look saved into the slot on the camera)
        set_bytes(br, dict(zip(range(REC + 12 * 18, REC + 12 * 19), [1, 6, 6, 1, 1, 2, 3, 3, 2, 1, 0xA5,
                                                                        0x5A ^ 1 ^ 6 ^ 6 ^ 1 ^ 1 ^ 2 ^ 3 ^ 3 ^ 2 ^ 1])))
        pg = self.open_editor(br, "custom-2")
        self.assertIn("SATURATION", pg.inner_text(".ed-rows"))             # its rows follow the color type
        pg.focus("#d-contrast")
        pg.keyboard.press("ArrowLeft")
        pg.focus("#d-key")
        pg.keyboard.press("ArrowLeft")
        self.wait(pg, "() => /2 changes/i.test(document.querySelector('[data-testid=look-changes]')?.innerText || '')", 5)
        pg.keyboard.press("Control+z")
        self.wait(pg, "() => /1 change/i.test(document.querySelector('[data-testid=look-changes]')?.innerText || '')", 5,
                  "Ctrl+Z undid one step")
        pg.click("[data-testid=look-changes] >> text=Review")
        pg.click("[data-testid=look-review] button[aria-label^=Undo]")
        self.wait(pg, "() => !document.querySelector('[data-testid=look-changes]')", 5, "nothing left to apply")
        pg.click("[data-testid=return-to-stock]")
        self.assertIn("FILTER EFF.", pg.inner_text(".ed-rows"))            # back to MONO's values: a B&W look again
        pg.click("[data-testid=apply-look]")
        self.apply_done(pg)
        self.assertNotEqual(record(self.card_cur(br), ID["CUSTOM 2"])[10], 0xA5)   # the record dropped, as RESET does
        self.assertTrue(pg.locator("[data-testid=return-to-stock]").is_disabled())

    # ---------------------------------------------------------------- the camera's other states
    def test_ghost_entry_for_a_stock_value(self):
        br = self.bridge()
        set_bytes(br, {ACT: 0, IMGSET: 3})                                 # IMG SET B&W, no look id
        pg = self.gallery(br)
        self.assertEqual(pg.inner_text("label.card.on .name").strip(), "B&W")
        self.assertIn("The camera's stock setting, not a look", pg.inner_text("label.card.on"))
        self.assertIn("Nothing to edit.", self.bar(pg))
        self.assertEqual(pg.locator("[data-testid=edit-look]").count(), 0)
        # the stock B&W setting makes no tone, so its card is neutral like MONO's
        self.assertLess(pg.evaluate(CHROMA, "label[data-look=ghost] canvas"), 1.0)
        self.assertLess(pg.evaluate(CHROMA, "label[data-look=mono] canvas"), 1.0)
        pg.click("label[data-look=mono]")
        pg.click("[data-testid=set-look]")
        self.apply_done(pg)
        self.assertEqual((self.card_cur(br)[ACT], self.card_cur(br)[IMGSET]), (ID["MONO"], 3))
        self.wait(pg, "() => !document.querySelector('label[data-look=ghost]')", 10, "the ghost gone")

    def test_official_firmware_shows_no_looks(self):
        br = self.bridge()
        br.body.card["/EXT0/PARAM.CUR"] = DEMO_CUR.read_bytes()            # a camera without Koma
        pg = self.page(br)
        self.wait(pg, "() => !!document.querySelector('[data-testid=looks-stock]')", 20, "the empty state")
        self.assertIn("The looks need Koma on the camera.", pg.inner_text("main"))
        # official firmware has no Koma to update: the one action is setup's entry
        self.assertIn(pg.inner_text("[data-testid=looks-setup]").strip(), ("Set up Koma", "Continue setup"))
        self.assertEqual(pg.get_attribute("[data-testid=looks-setup]", "href"), "#/setup")
        self.assertNotIn("Update Koma", pg.inner_text("[data-testid=looks-stock]"))
        self.assertEqual(pg.locator("label.card").count(), 0)
        self.assertEqual(pg.locator("[data-testid=looks-bar]").count(), 0)
        pg.wait_for_timeout(800)
        self.assertFalse([c for c in self.calls if c[1].startswith("/photos")])     # nothing to draw: no photo read

    def test_a_stock_camera_without_a_settings_file_shows_the_empty_state(self):
        """Official firmware never writes PARAM.CUR: the read answers no_param_cur, and Looks says so at once."""
        br = self.bridge(photo=False)
        del br.body.card["/EXT0/PARAM.CUR"]
        pg = self.page(br)
        self.wait(pg, "() => !!document.querySelector('[data-testid=looks-stock]')", 20, "the empty state")
        self.assertEqual(pg.get_attribute("[data-testid=looks-setup]", "href"), "#/setup")
        pg.wait_for_timeout(1500)
        self.assertLessEqual(sum(1 for c in self.calls if c[1] == "/settings"), 2)   # read once, not again and again

    def test_two_looks_edited_go_in_one_apply(self):
        br = self.bridge()
        pg = self.gallery(br)
        pg.click("label[data-look=shinjuku]")
        self.assertIn("On the ADJ lever.", self.bar(pg))
        pg.check("[data-testid=bar-adj] input[value=false]")             # SHINJUKU's ADJ LEVER, from the bar
        self.wait(pg, "() => /1 change to SHINJUKU/i.test(document.querySelector('[data-testid=look-changes]')?.innerText || '')", 5)
        pg.goto(br.url + "#/looks/noir")
        self.wait(pg, "() => !!document.querySelector('#d-contrast')", 20, "NOIR's editor")
        pg.focus("#d-contrast")
        pg.keyboard.press("ArrowRight")
        self.wait(pg, "() => /2 changes in 2 looks/i.test(document.querySelector('[data-testid=look-changes]')?.innerText || '')", 5)
        pg.click("[data-testid=look-changes] >> text=Review")
        heads = pg.eval_on_selector_all("[data-testid=look-review] h3", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(heads, ["SHINJUKU", "NOIR"])
        polls = br.body.poff_count
        pg.click("[data-testid=look-review] >> text=Apply to camera")
        self.apply_done(pg, "Applied to 2 looks.")
        self.assertEqual(br.body.poff_count, polls + 1)                   # one restart for both
        cur = self.card_cur(br)
        self.assertFalse(adj_mask(cur) >> (ID["SHINJUKU"] - 1) & 1)
        self.assertEqual(record(cur, ID["NOIR"])[1], 7)                   # NOIR's CONTRAST +3
        self.assertEqual(cur[ACT], ID["MONO"])                            # the look on the camera stays
        self.assertEqual(pg.locator("[data-testid=look-changes]").count(), 0)

    def test_no_photo_on_the_card_uses_the_sample(self):
        br = self.bridge(photo=False)
        for name, _, _ in __import__("mockcard").PHOTOS:
            br.body.card.pop(f"{FOLDER}/{name}", None)
        br.body.folder_entries.clear()
        pg = self.gallery(br)
        self.assertIn("Drawn on a sample photo.", pg.inner_text("[data-testid=looks-source]"))
        self.assertIn("The card holds no photos.", pg.inner_text("[data-testid=looks-source]"))
        self.assertEqual(pg.locator("[data-testid=photo-read-again]").count(), 0)   # nothing to read again

    def no_photos(self, br):
        for name, _, _ in __import__("mockcard").PHOTOS:
            br.body.card.pop(f"{FOLDER}/{name}", None)
        br.body.folder_entries.clear()
        return br

    def test_a_photo_from_another_day_gives_its_date(self):
        """The source line gives the time alone for a photo taken today, and the date with it otherwise."""
        br = with_photo(self.no_photos(self.bridge(photo=False)), stamp="2026:11:02 14:32:10", photo=SAMPLE)
        pg = self.gallery(br)
        self.assertIn("Drawn on the last photo, taken 2026/11/02 · 14:32.", pg.inner_text("[data-testid=looks-source]"))

    def test_no_photo_and_no_sample_lists_names_and_lines_only(self):
        """The card holds no photo and the bundled sample cannot be read either (its request fails here): no empty
        frames, the looks as a list of names and lines; choosing one still works; the editor's rows take the photo's
        place."""
        br = self.no_photos(self.bridge(photo=False))
        pg = self.page(br, abort=("**/assets/sample-*",))
        self.wait(pg, "() => /No photo to draw the looks on/.test(document.querySelector('[data-testid=looks-source]')?.innerText || '')",
                  30, "the no-photo line")
        self.assertEqual(pg.locator("[data-testid=looks] .frame").count(), 0)
        names = pg.eval_on_selector_all("[data-testid=looks] label.card .name", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(names, ORDER)
        self.assertNotIn("APPROXIMATE", pg.inner_text("[data-testid=looks-source]").upper())   # nothing drawn
        self.assertEqual(pg.inner_text("label.card.on .name").strip(), "MONO")
        pg.click("label[data-look=noir]")
        self.assertIn("NOIR", self.bar(pg))
        self.assertIn("The camera restarts to load it.", self.bar(pg))
        pg.click("[data-testid=edit-look]")
        self.wait(pg, "() => !!document.querySelector('[data-testid=look-editor] .ed-ctl.solo .ed-rows')", 10,
                  "the editor without a photo")
        self.assertEqual(pg.locator("[data-testid=look-editor] .split").count(), 0)

    def test_photo_read_error_then_read_again(self):
        br = with_photo(self.bridge(photo=False), on_card=False)          # listed, but the read fails
        pg = self.gallery(br)
        src = pg.inner_text("[data-testid=looks-source]")
        self.assertIn("Drawn on a sample photo.", src)
        self.assertIn("The last photo could not be read.", src)
        br.body.card[f"{FOLDER}/R0015710.JPG"] = PHOTO.read_bytes()      # readable now
        pg.click("[data-testid=photo-read-again]")
        self.wait(pg, "() => !document.querySelector('[data-testid=photo-read-again]') && "
                      "/Drawn on a sample photo\\./.test(document.querySelector('[data-testid=looks-source]')?.innerText || '')",
                  30, "the last photo read (black and white: the gallery stays on the sample)")
        self.wait(pg, READY, 40)
        self.assertEqual(pg.locator("[data-testid=photo-read-again]").count(), 0)

    def test_memory_card_file_shows_the_looks_and_writes_nothing(self):
        import tempfile
        from mockcard import koma_cur
        off = Bridge(port=free_port(), plugged=False)
        self.bridges.append(off)
        pg = self.page(off, "")
        pg.click("text=Work from a memory card instead")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "PARAM.CUR"
            f.write_bytes(koma_cur(DEMO_CUR.read_bytes()))
            pg.set_input_files("[data-testid=card-file]", str(f))
            self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 10", 10, "the file's camera menu")
        pg.click("nav[aria-label=Main] a[data-nav=looks]")
        self.wait(pg, READY, 40, "the looks on the sample photo")
        self.assertIn("Drawn on a sample photo.", pg.inner_text("[data-testid=looks-source]"))
        self.assertIn("Connect the camera to set a look.", self.bar(pg))
        pg.click("label[data-look=noir]")
        self.assertEqual(pg.locator("[data-testid=set-look]").count(), 0)
        pg.click("[data-testid=edit-look]")
        self.wait(pg, "() => document.querySelector('.split .frame')?.classList.contains('ready')", 40, "the editor")
        pg.focus("#d-contrast")
        pg.keyboard.press("ArrowRight")
        self.wait(pg, "() => /Connect the camera to apply edits/.test(document.querySelector('[data-testid=look-changes]')?.innerText || '')",
                  5, "no Apply without a camera")
        self.assertEqual(pg.locator("[data-testid=apply-look]").count(), 0)

    def test_camera_menu_img_set_links_into_looks(self):
        br = self.bridge()
        pg = self.page(br, "#/settings/camera")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 5", 20, "the camera menu")
        self.assertEqual(pg.inner_text("[data-row=img_set] .val").strip(), "MONO")
        pg.click("[data-row=img_set] button.head")
        help_ = pg.inner_text("[data-row=img_set] .field:first-child .help").strip()
        self.assertEqual(help_, "The look the camera shoots with.")       # not the stock list's help
        # the Look IA's list has the 21 looks alone: the hidden stock entries' values (SETTING1/2, B&W, B&W (TE)) have
        # no screen on the camera, so the row holds IMG SET alone, with no sub-label repeating its name
        self.assertEqual(pg.locator("[data-row=img_set] .field").count(), 1)
        self.assertEqual(pg.locator("[data-row=img_set] .sub").count(), 0)
        self.assertNotIn("SETTING1", pg.inner_text("[data-row=img_set]"))
        pg.fill("[data-testid=menu-search]", "setting1")
        self.assertEqual(pg.locator("[data-row=img_set]").count(), 0)     # nothing to find behind the row
        pg.fill("[data-testid=menu-search]", "")
        pg.click("[data-testid=img-set-link]")
        self.wait(pg, "() => !!document.querySelector('[data-testid=looks]')", 10, "Looks")

    def test_camera_menu_keeps_the_stock_entries_on_koma04w(self):
        """Builds before the Look IA: the IMG SET list still has the 7 stock entries and their screens: their values stay in the row."""
        br = self.bridge(look_ia=False)
        pg = self.page(br, "#/settings/camera")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 5", 20, "the camera menu")
        pg.click("[data-row=img_set] button.head")
        self.assertEqual(pg.inner_text("[data-testid=img-set-look]").strip(), "MONO")
        self.assertIn("SETTING1 CONTRAST", pg.inner_text("[data-row=img_set]"))
        self.assertGreater(pg.locator("[data-row=img_set] .field").count(), 1)

    def test_an_older_look_store_is_left_to_the_camera(self):
        br = self.bridge()
        set_bytes(br, {0x903: ord("5")})                                   # 'RCP5': a Koma 0.2 look store
        pg = self.page(br, "#/settings/camera")
        self.wait(pg, "() => document.querySelectorAll('[data-row]').length > 5", 20, "the camera menu")
        pg.click("[data-row=img_set] button.head")
        self.assertEqual(pg.locator("[data-testid=img-set-look]").count(), 0)   # the stock IMG SET row, editable
        self.assertGreaterEqual(pg.locator("[data-row=img_set] .field:first-child select").count(), 1)
        pg.goto(br.url + "#/looks")
        self.wait(pg, READY, 40)
        pg.click("label[data-look=noir]")
        self.assertIn("The Koma on the camera stores looks in an older form.", self.bar(pg))
        # the fact alone: no Koma package offers the newer store yet, so there is no Update Koma link to follow
        self.assertEqual(pg.locator("[data-testid=update-koma]").count(), 0)
        self.assertEqual(pg.locator("[data-testid=set-look]").count(), 0)
        pg.click("[data-testid=edit-look]")
        self.wait(pg, "() => document.querySelector('.split .frame')?.classList.contains('ready')", 40, "the editor")
        pg.focus("#d-contrast")
        pg.keyboard.press("ArrowRight")
        self.wait(pg, "() => /older form/.test(document.querySelector('[data-testid=look-changes]')?.innerText || '')",
                  5, "the reason in the changes bar")
        self.assertEqual(pg.locator("[data-testid=look-changes] a").count(), 0)
        self.assertEqual(pg.locator("[data-testid=apply-look]").count(), 0)

    def test_the_koma_before_the_look_ia_has_no_adj_lever_and_a_plain_standard(self):
        """Builds before the Look IA keep the same 'RCP6' store but have no ADJ LEVER setting and no
        STANDARD look: their blocks hold 0xff at the ADJ LEVER row 0x8ce (HW). No ADJ tags,
        no ADJ LEVER control, STANDARD as its photo and one line with no controls; a look's own values and setting a look
        still work, and nothing those builds lack is written."""
        br = self.bridge(look_ia=False)
        before = self.card_cur(br)
        self.assertEqual(before[ADJROW], 0xFF)
        pg = self.gallery(br)
        self.assertEqual(pg.locator("[data-testid=adj-tag]").count(), 0)
        pg.goto(br.url + "#/looks/noir")
        self.wait(pg, "() => !!document.querySelector('[data-testid=adj-older]') && "
                      "document.querySelector('.split .frame')?.classList.contains('ready')", 40, "NOIR's editor")
        self.assertEqual(pg.locator("[data-row=adj] input").count(), 0)
        self.assertEqual(pg.inner_text("[data-testid=adj-older]").strip(),
                         "The Koma on the camera has no ADJ LEVER setting.")
        self.assertEqual(pg.locator("[data-testid=adj-older] a").count(), 0)
        pg.focus("#d-grain")
        pg.keyboard.press("ArrowRight")
        pg.click("[data-testid=apply-look]")
        self.apply_done(pg)
        cur = self.card_cur(br)
        self.assertEqual(cur[FXT + ID["NOIR"] - 1], 0x50 | 1)              # GRAIN 1: a value those builds have
        self.assertEqual(bytes(cur[ADJM:ADJM + 4]), bytes(before[ADJM:ADJM + 4]))   # no ADJ LEVER mask written
        pg.goto(br.url + "#/looks")
        self.wait(pg, READY, 40)
        pg.click("label[data-look=noir]")
        pg.click("[data-testid=set-look]")
        self.apply_done(pg)
        cur = self.card_cur(br)
        self.assertEqual((cur[ACT], cur[IMGSET]), (ID["NOIR"], 3))
        self.assertEqual(cur[ADJROW], 0xFF)                                # the row those builds do not have: untouched
        pg.goto(br.url + "#/looks/standard")
        self.wait(pg, "() => !!document.querySelector('[data-testid=look-locked]')", 20, "STANDARD, not editable")
        self.assertEqual(pg.inner_text("[data-testid=look-locked]").strip(),
                         "The Koma on the camera keeps STANDARD as the stock NORMAL.")
        self.assertEqual(pg.locator("[data-testid=look-locked] a").count(), 0)
        self.assertEqual(pg.locator(".ed-rows, [data-testid=return-to-stock]").count(), 0)   # no dead controls

    def test_official_firmware_has_no_editor_either(self):
        br = self.bridge()
        br.body.card["/EXT0/PARAM.CUR"] = DEMO_CUR.read_bytes()
        pg = self.page(br, "#/looks/shinjuku")
        self.wait(pg, "() => !!document.querySelector('[data-testid=looks-stock]')", 20, "the empty state")
        self.assertEqual(pg.locator("[data-testid=look-editor]").count(), 0)

    # ---------------------------------------------------------------- drafts when the camera changes under them
    def test_drafts_follow_another_camera_and_keep_their_edits(self):
        br = self.bridge()
        pg = self.open_editor(br, "shinjuku")
        pg.focus("#d-contrast")
        pg.keyboard.press("ArrowRight")                                    # CONTRAST +2 -> +3 here
        self.wait(pg, "() => /1 change/i.test(document.querySelector('[data-testid=look-changes]')?.innerText || '')", 5)
        # another GR on the cable: its SHINJUKU has KEY +2 (a stored record), the rest at SHINJUKU's defaults
        set_bytes(br, record_bytes(ID["SHINJUKU"], [3, 6, 6, 2, 5, 3, 0, 2, 4, 0]))
        br.body.unplug()
        # the camera gone: the app stays on the editor with the shell's Disconnected line, the draft kept
        self.wait(pg, "() => /Camera disconnected/.test(document.body.innerText)", 20, "the camera gone")
        br.body.plug()
        self.wait(pg, "() => document.querySelector('#d-key')?.getAttribute('aria-valuetext') === '+2'", 30,
                  "the new camera's KEY in the editor")
        self.assertEqual(pg.eval_on_selector("#d-contrast", "e => e.getAttribute('aria-valuetext')"), "+3")
        self.assertIn("1 CHANGE", pg.inner_text("[data-testid=look-changes]").upper())
        pg.click("[data-testid=look-changes] >> text=Review")
        review = pg.inner_text("[data-testid=look-review]")
        self.assertIn("CONTRAST", review)
        self.assertNotIn("KEY", review)
        pg.click("[data-testid=look-review] >> text=Apply to camera")
        self.apply_done(pg)
        self.assertEqual(list(record(self.card_cur(br), ID["SHINJUKU"])[:10]), [3, 7, 6, 2, 5, 3, 0, 2, 4, 0])

    def test_a_late_apply_keeps_the_edits_made_since(self):
        br = self.bridge()
        br.body.reboot_s = 3.0
        pg = self.page(br, "#/looks/noir", wait_s=2)
        self.wait(pg, "() => document.querySelector('.split .frame')?.classList.contains('ready')", 40, "the editor")
        pg.focus("#d-contrast")
        pg.keyboard.press("ArrowRight")                                    # the edit that is sent
        pg.click("[data-testid=apply-look]")
        self.wait(pg, "() => /Waiting for the camera/.test(document.querySelector('[aria-current=step]')?.innerText || '')",
                  20, "the wait step")
        br.body.unplug()                                                   # it does not come back in time
        self.wait(pg, "() => /has not come back/.test(document.querySelector('[data-testid=apply-status]')?.innerText || '')",
                  30, "the not-back failure")
        pg.click("[data-testid=apply-sheet] >> text=Close")
        br.body.retr_bps = 400.0                                           # each settings read takes about 10 s
        mark = len(self.answers)
        br.body.plug()
        self.wait(pg, "() => !!document.querySelector('#d-key')", 30, "the editor again")
        pg.focus("#d-key")
        pg.keyboard.press("ArrowRight")                                    # an edit made before the late read ends
        self.wait(pg, "() => /^2 changes to NOIR$/i.test(document.querySelector('[data-testid=look-changes] .micro')?.innerText.trim() || '')",
                  5, "two edits before the late read ends")
        # two settings reads come back: the late Apply's check (it takes the camera's block in) and Looks' own read
        for _ in range(300):
            if sum(1 for m, u, s in self.answers[mark:] if m == "GET" and u.split("?")[0] == "/settings" and s == 200) >= 2:
                break
            pg.wait_for_timeout(200)
        else:
            self.fail("the two settings reads after the camera came back")
        pg.wait_for_timeout(600)
        self.wait(pg, "() => /^1 change to NOIR$/i.test(document.querySelector('[data-testid=look-changes] .micro')?.innerText.trim() || '') "
                      "&& document.querySelector('#d-contrast')?.getAttribute('aria-valuetext') === '+3'", 10,
                  "the late Apply taken in, the KEY edit still pending")
        rec = record(self.card_cur(br), ID["NOIR"])
        self.assertEqual(rec[1], 7)                                        # CONTRAST +3 reached the camera
        pg.click("[data-testid=look-changes] >> text=Review")
        review = pg.inner_text("[data-testid=look-review]")
        self.assertIn("KEY", review)
        self.assertNotIn("CONTRAST", review)

    def test_keyboard_moves_the_selection(self):
        br = self.bridge()
        pg = self.gallery(br)
        pg.focus("label[data-look=mono] input")
        pg.keyboard.press("ArrowRight")
        self.assertEqual(pg.evaluate("() => document.activeElement.closest('label').dataset.look"), "soft-mono")
        self.assertIn("SOFT MONO", self.bar(pg))
        self.assertEqual(br.body.poff_count, 0)                            # selecting writes nothing

    def test_a_short_phone_keeps_room_for_the_rows(self):
        """375 x 667: the pinned photo narrows, so with the rows scrolled under it two rows still show between it and
        the changes bar (not just one)."""
        br = self.bridge()
        pg = self.page(br, "#/looks/shinjuku", viewport={"width": 375, "height": 667})
        self.wait(pg, "() => document.querySelector('.split .frame')?.classList.contains('ready')", 40, "the editor")
        pg.focus("#d-contrast")
        pg.keyboard.press("ArrowRight")
        self.wait(pg, "() => !!document.querySelector('[data-testid=look-changes]')", 5)
        pg.evaluate("() => document.querySelector('main').scrollBy(0, 2000)")
        pg.wait_for_timeout(300)
        m = pg.evaluate("""() => { const r = s => document.querySelector(s).getBoundingClientRect();
            return {room: r('[data-testid=look-changes]').top - r('.ed-pic').bottom, photo: r('.split .frame').width,
                    column: r('.ed-pic').width, top: r('.ed-pic').top}; }""")
        self.assertGreaterEqual(m["room"], 140, m)                       # two rows: one scale and one list, at least
        self.assertLess(m["photo"], m["column"], m)                      # the photo narrowed, not the rows' room
        self.assertGreaterEqual(m["photo"], 240, m)
        self.assertLess(m["top"], 80, m)                                  # still pinned under the top line

    def test_phone_width_no_horizontal_scroll(self):
        br = self.bridge()
        for h in ("#/looks", "#/looks/shinjuku", "#/looks/positive"):
            pg = self.page(br, h, viewport=PHONE)
            pg.wait_for_timeout(1500)
            over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            self.assertLessEqual(over, 0, h)
            pg.context.close()
            self.contexts.remove(pg.context)


if __name__ == "__main__":
    unittest.main()
