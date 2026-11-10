#!/usr/bin/env python3
"""End-to-end: komabridge's HTTP API against the real 2.40 firmware + koma04 in the emulator (`--emu` backend).
SIMULATED (tools/koma/emu, Unicorn); not camera evidence. Skipped unless KOMA_FIRMWARE names the firmware file and the
emulator imports (unicorn). One boot, one reboot; the steps run in order (test_1 .. test_5) and take a few minutes.

  KOMA_FIRMWARE=firmware/original/gr-240.exe .venv/bin/python tools/komaset/bridge/test_emu_integration.py

EMU_SHOTS=<dir> saves the menu frame (osd-menu.png, plus osd-menu-on-grey.png when Pillow is there) and the other
frames there. EMU_MODULE=<grmod> picks another module (default: emulink.DEFAULT_MODULE).
"""
import base64
import json
import os
import struct
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import emulink  # noqa: E402
import komabridge as KB  # noqa: E402
from test_komabridge import decode_png, read_part  # noqa: E402

DEPS, WHY = emulink.deps_ok()
SHOTS = Path(os.environ.get("EMU_SHOTS") or (HERE / "testdata" / ".emu-shots"))
LAYOUT = 0x8BB          # MENU LAYOUT (menu5), outside every merge exclude range
KSIO_STATUS = 0x8DC     # settingsio last-import status: 1 ok
TIMES = {}


def timed(name):
    def wrap(fn):
        def run(*a, **kw):
            t0 = time.time()
            try:
                return fn(*a, **kw)
            finally:
                TIMES.setdefault(name, []).append(round(time.time() - t0, 2))
        return run
    return wrap


@unittest.skipUnless(DEPS, f"--emu deps missing: {WHY}")
class EmuBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = Path(os.environ["EMU_MODULE"]).read_bytes() if os.environ.get("EMU_MODULE") else None
        t0 = time.time()
        cls.backend = emulink.EmuBackend(module, wait=False, log=lambda t: print(t, flush=True))
        cls.link = KB.Link(cls.backend)
        cls.server = KB.make_server(cls.link, port=0)
        cls.server.quiet = True
        cls.port = cls.server.server_address[1]
        cls.server.RequestHandlerClass = KB.make_handler(cls.link, cls.port)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.wait_connected(boots=1)
        TIMES["boot (to /status connected)"] = [round(time.time() - t0, 1)]
        SHOTS.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.link.stop_live()
        cls.server.shutdown()
        cls.server.server_close()
        cls.backend.cam.close()
        print("TIMINGS (wall s, through HTTP):", json.dumps(TIMES), flush=True)
        print("EMU per command (wall s):", json.dumps(cls.backend.info()["timings"]), flush=True)
        print("EMU routes:", json.dumps(cls.backend.info()["routes"]), flush=True)

    @classmethod
    def req(cls, method, path, obj=None, timeout=300, raw=False):
        body = json.dumps(obj).encode() if obj is not None else (b"" if method == "POST" else None)
        r = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", data=body, method=method,
                                   headers={"Content-Type": "application/json"} if obj is not None else {})
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                data = resp.read()
                return resp.status, dict(resp.headers), (data if raw else json.loads(data))
        except urllib.error.HTTPError as e:
            data = e.read()
            return e.code, dict(e.headers), (data if raw else json.loads(data or b"{}"))

    @classmethod
    def wait_connected(cls, boots, limit=600):
        end = time.time() + limit
        while time.time() < end:
            _, _, st = cls.req("GET", "/status", timeout=30)
            if st.get("connected") and st["emu"]["boots"] >= boots:
                return st
            time.sleep(2)
        raise AssertionError(f"the emulated camera did not come up (boot {boots})")

    # ------------------------------------------------------------------ settings
    def test_1_status_and_settings(self):
        _, _, st = self.req("GET", "/status")
        self.assertEqual((st["backend"], st["simulated"], st["connected"]), ("emu", True, True))
        self.assertEqual(st["camera"]["firmware"], "02.40")
        code, _, s = timed("GET /settings")(self.req)("GET", "/settings")
        self.assertEqual(code, 200, s)
        cur = base64.b64decode(s["data"])
        self.assertEqual(len(cur), 4096)
        self.assertEqual(cur[:4], b"MG1H")
        self.assertEqual(cur[0x8D0:0x8D4], b"KSIO", "PARAM.CUR is the module's export (KSIO sub-header)")
        self.assertTrue(s["check"]["magic_ok"], s["check"])
        # The model's PARAM.CUR carries the checksum word as stored in RAM, which does not match the bytes (the
        # emulator's flash is erased, so the block is firmware defaults with a stale word); recorded, not asserted.
        print("PARAM.CUR check:", s["check"]["text"].replace("\n", "; "), flush=True)
        type(self).cur = cur

    def test_2_push_reboot_import(self):
        m = KB.load_map()
        blk = bytearray(self.cur)
        before = blk[LAYOUT]
        blk[LAYOUT] = 1 if before != 1 else 0
        KB.komaset.set_checksum(blk, m)
        code, _, r = timed("POST /settings")(self.req)("POST", "/settings", {"data": base64.b64encode(bytes(blk)).decode()})
        self.assertEqual(code, 200, r)
        self.assertTrue(r["verified"])
        self.assertEqual(self.backend.cam.card().get("PARAM.DAT"), bytes(blk))
        t0 = time.time()
        code, _, r = self.req("POST", "/reboot")
        self.assertEqual(code, 200, r)
        print("poff reply:", r["reply"], "emu poff log:", self.backend.info()["poff"], flush=True)
        self.assertTrue(r["reply"][:3] in ("200", "451"), r)
        self.wait_connected(boots=2)
        TIMES["POST /reboot -> connected again"] = [round(time.time() - t0, 1)]
        code, _, s = timed("GET /settings")(self.req)("GET", "/settings")
        self.assertEqual(code, 200, s)
        new = base64.b64decode(s["data"])
        card = self.backend.cam.card()
        print("after reboot: card", sorted(card), "PARAM.OK", card.get("PARAM.OK"), "layout", before, "->", new[LAYOUT],
              "KSIO status", new[KSIO_STATUS], flush=True)
        self.assertEqual(card.get("PARAM.OK"), b"OK\r\n")
        self.assertNotIn("PARAM.BAD", card)
        self.assertEqual(new[KSIO_STATUS], 1, "KSIO last-import status: ok")
        self.assertEqual(new[LAYOUT], blk[LAYOUT], "the pushed MENU LAYOUT byte was imported")

    # ------------------------------------------------------------------ remote + live
    def test_4_tether_menu_osd(self):
        code, hdr, before = timed("GET /live/frame?mode=2")(self.req)("GET", "/live/frame?mode=2", raw=True)
        self.assertEqual(code, 200)
        (SHOTS / "osd-before-menu.png").write_bytes(before)
        code, _, r = timed("POST /keys/tap MENU")(self.req)("POST", "/keys/tap", {"code": 23})
        self.assertTrue(r["ok"], r)
        time.sleep(5)      # the emulator advances by the idle wall time (capped), so the menu gets drawn
        t0 = time.time()
        resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/live?mode=2", timeout=300)
        part = read_part(resp)
        resp.close()
        TIMES["GET /live?mode=2 first part"] = [round(time.time() - t0, 2)]
        self.link.stop_live("osd")
        self.assertEqual(part[0]["content-type"], "image/png")
        png = part[1]
        (SHOTS / "osd-menu.png").write_bytes(png)
        d = decode_png(png)
        drawn = sum(1 for i in d["index"] if i)
        print("menu frame", d["w"], "x", d["h"], "drawn pixels", drawn, flush=True)
        self.assertEqual((d["w"], d["h"]), (360, 240))
        self.assertGreater(drawn, 0.02 * 360 * 240)
        self.assertNotEqual(decode_png(before)["index"], d["index"], "the menu changed the screen layer")
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(png)).convert("RGBA")
            bg = Image.new("RGBA", img.size, (128, 128, 128, 255))
            Image.alpha_composite(bg, img).convert("RGB").resize((720, 480), Image.NEAREST).save(
                SHOTS / "osd-menu-on-grey.png")
        except ImportError:
            pass

    def test_3_live_frames(self):
        code, _, r = timed("POST /tether/start")(self.req)("POST", "/tether/start")
        self.assertTrue(r["ok"], r)
        # the monitor starts some time after diaset,4 (in the model, 4e7+ instructions); idle time lets the emulator
        # run that far before the next pull (an immediate pull answers 451 -LVFR off)
        time.sleep(5)
        for mode, ctype in ((0, "image/png"), (1, "image/jpeg")):
            code, hdr, img = timed(f"GET /live/frame?mode={mode}")(self.req)("GET", f"/live/frame?mode={mode}", raw=True)
            self.assertEqual(code, 200, img[:200])
            self.assertEqual(hdr["Content-Type"], ctype)
            (SHOTS / f"live-mode{mode}.{'png' if mode == 0 else 'jpg'}").write_bytes(img)
            print(f"mode {mode}: {len(img)} bytes, flags {hdr.get('X-Frame-Flags')}", flush=True)
            if mode == 0:
                d = struct.unpack(">II", img[16:24])
                self.assertEqual(d, (160, 120))
            else:   # header rebuilt by the bridge; the scan is whatever the stubbed engine left (not decodable)
                self.assertEqual(img[:2], b"\xff\xd8")
                self.assertEqual(img[-2:], b"\xff\xd9")

    def test_5_shoot_keyset_path(self):
        """The shot keys run through the firmware's -CCTL keyset handler; the model completes no shot (no JPEG)."""
        code, _, r = timed("POST /tether/shoot")(self.req)("POST", "/tether/shoot")
        print("shoot:", r.get("outcome"), [x["reply"][:3] for x in r.get("replies", [])], flush=True)
        self.assertEqual(code, 200, r)
        keysets = [x for x in r["replies"] if "keyset" in x["command"]]
        self.assertTrue(keysets and all(x["reply"].startswith("200") for x in keysets), r["replies"])
        code, _, r = timed("POST /tether/stop")(self.req)("POST", "/tether/stop")
        self.assertTrue(r["ok"], r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
