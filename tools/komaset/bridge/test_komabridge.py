#!/usr/bin/env python3
"""Tests for komabridge, all against the SIMULATED camera (mockcard.py on grdusb's mock). No USB device is opened.

Run: python3 tools/komaset/bridge/test_komabridge.py
"""
import base64
import struct
import json
import time
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import komabridge as KB  # noqa: E402
import mockcard  # noqa: E402

DEMO = mockcard.DEMO_CUR.read_bytes()


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.body = mockcard.MockBody(reboot_s=4.0, clock=self.clock)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.server = KB.make_server(self.link, port=0)
        self.server.quiet = True
        # make_server's handler was built for port 0; rebuild it for the real port so the Host guard matches
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def req(self, method, path, body=None, headers=None, host=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        r = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        if host:
            r.add_unredirected_header("Host", host)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def jreq(self, method, path, obj=None, **kw):
        body = json.dumps(obj).encode() if obj is not None else None
        status, headers, data = self.req(method, path, body, **kw)
        return status, headers, json.loads(data)

    def transports_clean(self):
        for t in self.body.transports:
            self.assertEqual(t.violations, [], t.sent)

    # ---- endpoints
    def test_status_connected(self):
        status, headers, s = self.jreq("GET", "/status", headers={"Origin": "null"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "null")
        self.assertTrue(s["connected"])
        self.assertEqual(s["backend"], "mock")
        self.assertTrue(s["simulated"])
        self.assertEqual(s["camera"]["firmware"], "02.40")
        self.assertEqual(s["camera"]["firmware_display"], "2.40")
        self.assertEqual(s["camera"]["usb_mode"], "ORIGINAL")
        self.assertEqual(s["bridge_version"], KB.BRIDGE_VERSION)

    def test_status_polling_sends_ver_once(self):
        for _ in range(5):
            self.jreq("GET", "/status")
        sent = [c for t in self.body.transports for c in t.sent]
        self.assertEqual(sent.count("-VER\r\n"), 1, sent)

    def test_get_settings(self):
        status, _, s = self.jreq("GET", "/settings")
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(s["data"]), DEMO)
        self.assertTrue(s["check"]["ok"])
        self.assertTrue(s["check"]["checksum_ok"])
        self.assertIn("checksum: stored", s["check"]["text"])
        self.transports_clean()

    def test_get_settings_missing_file(self):
        del self.body.card["/EXT0/PARAM.CUR"]
        status, _, s = self.jreq("GET", "/settings")
        self.assertEqual(status, 404)
        self.assertEqual(s["code"], "no_param_cur")

    def test_get_settings_by_retr_when_root_listing_would_time_out(self):
        # Big card (widefs FAT32): -NLST /EXT0 hangs (HW 2026-11-08). GET and POST /settings must never list the root.
        def boom(self, path):
            raise AssertionError(f"listed {path}")
        orig = KB.BridgeSession.list_raw
        KB.BridgeSession.list_raw = boom
        try:
            status, _, s = self.jreq("GET", "/settings")
            self.assertEqual(status, 200)
            self.assertEqual(base64.b64decode(s["data"]), DEMO)
            status, _, _ = self.jreq("POST", "/settings", {"data": base64.b64encode(DEMO).decode()})
            self.assertEqual(status, 200)
        finally:
            KB.BridgeSession.list_raw = orig

    def test_read_root_file_450_is_absent(self):
        self.link.run(lambda s: self.assertIsNone(KB.read_root_file(s, "/EXT0/KOMALOG.TXT")))
        self.link.run(lambda s: self.assertEqual(KB.read_root_file(s, KB.PARAM_CUR), DEMO))

    def test_post_settings_writes_param_dat_only(self):
        blk = bytearray(DEMO)
        blk[0x100] ^= 1
        m = KB.load_map()
        KB.komaset.set_checksum(blk, m)
        status, _, s = self.jreq("POST", "/settings", {"data": base64.b64encode(bytes(blk)).decode()},
                                 headers={"Content-Type": "application/json", "Origin": "null"})
        self.assertEqual(status, 200, s)
        self.assertTrue(s["verified"])
        self.assertEqual(self.body.card["/EXT0/PARAM.DAT"], bytes(blk))
        self.assertEqual(self.body.card["/EXT0/PARAM.CUR"], DEMO)  # untouched
        writes = [c for t in self.body.transports for c in t.sent if c.startswith(("-STOR", "DELE", "-CCTL"))]
        self.assertEqual(writes, ["-STOR 4096 /EXT0/PARAM.DAT\r\n"])
        self.transports_clean()

    def test_post_settings_rejects_bad_block(self):
        bad = bytearray(DEMO)
        bad[0x100] ^= 1  # checksum now wrong
        cases = {"bad checksum": bytes(bad), "bad magic": b"XXXX" + DEMO[4:], "short": DEMO[:4000]}
        for label, data in cases.items():
            status, _, s = self.jreq("POST", "/settings", {"data": base64.b64encode(data).decode()})
            self.assertEqual(status, 422, label)
            self.assertFalse(s["check"]["ok"], label)
        status, _, s = self.jreq("POST", "/settings", {"data": "not base64!!"})
        self.assertEqual(status, 400)
        self.assertNotIn("/EXT0/PARAM.DAT", self.body.card)
        sent = [c for t in self.body.transports for c in t.sent]
        self.assertFalse([c for c in sent if c.startswith("-STOR")], sent)

    def test_post_settings_tag_mismatch(self):
        # camera = spare (240K2), file = main's block (Rev024M): same Koma version -> written, with a warning
        m = KB.load_map()
        cur = bytearray(DEMO)
        cur[0x8b0:0x8b5] = b"KOMA\x01"
        cur[0x8d0:0x8d4] = b"KSIO"
        cur[0x8d5:0x8dc] = b"240K2.4"
        KB.komaset.set_checksum(cur, m)
        self.body.card["/EXT0/PARAM.CUR"] = bytes(cur)
        new = bytearray(cur)
        new[0x8d5:0x8dc] = b"Rev024M"
        KB.komaset.set_checksum(new, m)
        b64 = base64.b64encode(bytes(new)).decode()
        status, _, s = self.jreq("POST", "/settings", {"data": b64})
        self.assertEqual(status, 200, s)
        self.assertEqual(s["warnings"], ["These cameras run different firmware tags (Rev024M, 240K2.4). "
                                         "Settings still copy; Koma version matches."])
        self.assertEqual(self.body.card["/EXT0/PARAM.DAT"], bytes(new))
        # same tag: no warning
        self.body.card.pop("/EXT0/PARAM.DAT")
        status, _, s = self.jreq("POST", "/settings", {"data": base64.b64encode(bytes(cur)).decode()})
        self.assertEqual(status, 200, s)
        self.assertEqual(s["warnings"], [])
        # a differing Koma settings version still refuses unless force
        self.body.card.pop("/EXT0/PARAM.DAT")
        kv = bytearray(new)
        kv[0x8b4] = (kv[0x8b4] + 1) & 0xff
        KB.komaset.set_checksum(kv, m)
        kb64 = base64.b64encode(bytes(kv)).decode()
        status, _, s = self.jreq("POST", "/settings", {"data": kb64})
        self.assertEqual(status, 422, s)
        self.assertEqual(s.get("code"), "koma_mismatch", s)
        self.assertNotIn("/EXT0/PARAM.DAT", self.body.card)
        status, _, s = self.jreq("POST", "/settings", {"data": kb64, "force": True})
        self.assertEqual(status, 200, s)
        self.assertEqual(self.body.card["/EXT0/PARAM.DAT"], bytes(kv))
        self.transports_clean()

    def test_reboot_and_return(self):
        self.jreq("GET", "/status")
        blk = bytearray(DEMO)
        blk[0x100] ^= 1
        KB.komaset.set_checksum(blk, KB.load_map())
        self.jreq("POST", "/settings", {"data": base64.b64encode(bytes(blk)).decode()})
        status, _, r = self.jreq("POST", "/reboot")
        self.assertEqual(status, 200, r)
        self.assertEqual(r["reply"], "200 -CCTL poff command successful.")
        self.assertIn("unknown", r["note"])
        sent = [c for t in self.body.transports for c in t.sent]
        self.assertIn(f"-CCTL poff,{KB.POFF_ARG}\r\n", sent)
        _, _, s = self.jreq("GET", "/status")
        self.assertFalse(s["connected"])
        self.clock.t += 5
        _, _, s = self.jreq("GET", "/status")
        self.assertTrue(s["connected"])
        # simulated boot import: PARAM.DAT became PARAM.OK and PARAM.CUR
        self.assertNotIn("/EXT0/PARAM.DAT", self.body.card)
        self.assertEqual(self.body.card["/EXT0/PARAM.OK"], bytes(blk))
        _, _, g = self.jreq("GET", "/settings")
        self.assertEqual(base64.b64decode(g["data"]), bytes(blk))
        self.transports_clean()

    def test_photos_list(self):
        status, _, p = self.jreq("GET", "/photos")
        self.assertEqual(status, 200)
        self.assertEqual(p["count"], 3)
        first = p["photos"][0]
        self.assertEqual(first["name"], "RIMG0003.JPG")  # newest first
        self.assertEqual(first["date"], "2026-11-06 18:45:02")
        self.assertEqual((first["width"], first["height"]), (320, 240))
        self.assertEqual(first["path"], "100RICOH/RIMG0003.JPG")
        self.assertGreater(first["size"], 100)
        # second listing reuses the -INFO/-FDAT results
        self.jreq("GET", "/photos")
        sent = [c for t in self.body.transports for c in t.sent]
        self.assertEqual(sum(c.startswith("-INFO") for c in sent), 3)
        self.transports_clean()

    def test_photo_get(self):
        status, headers, data = self.req("GET", "/photos/100RICOH/RIMG0001.JPG")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(data, self.body.card["/EXT0/DCIM/100RICOH/RIMG0001.JPG"])
        self.assertEqual(data[:2], b"\xff\xd8")
        status, _, thumb = self.req("GET", "/photos/100RICOH/RIMG0001.JPG?thumb=1")
        self.assertEqual(status, 200)
        self.assertEqual(thumb, data)  # no EXIF thumbnail in the mock JPEGs: full file
        sent = [c for t in self.body.transports for c in t.sent]
        self.assertEqual(sum(c.startswith("RETR") for c in sent), 1)  # cached
        for bad in ("../PARAM.CUR", "100RICOH/x/y.JPG", "100RICOH/PARAM.CUR", "100RICOH/a%20b.JPG"):
            status, _, _ = self.req("GET", "/photos/" + bad)
            self.assertIn(status, (400, 404), bad)
        status, _, _ = self.req("GET", "/photos/100RICOH/NOPE0001.JPG")
        self.assertEqual(status, 502)

    def test_exif_thumbnail(self):
        thumb = b"\xff\xd8thumb\xff\xd9"
        tiff = bytearray(b"MM\x00\x2a\x00\x00\x00\x08")
        tiff += b"\x00\x00" + (26).to_bytes(4, "big")  # IFD0: 0 entries, next IFD at 14
        ifd1_off = len(tiff)
        assert ifd1_off == 14
        data_off = ifd1_off + 2 + 2 * 12 + 4
        tiff += (2).to_bytes(2, "big")
        tiff += b"\x02\x01\x00\x04" + (1).to_bytes(4, "big") + data_off.to_bytes(4, "big")
        tiff += b"\x02\x02\x00\x04" + (1).to_bytes(4, "big") + len(thumb).to_bytes(4, "big")
        tiff += b"\x00\x00\x00\x00" + thumb
        tiff[10:14] = ifd1_off.to_bytes(4, "big")
        app1 = b"Exif\0\0" + bytes(tiff)
        jpeg = b"\xff\xd8\xff\xe1" + (len(app1) + 2).to_bytes(2, "big") + app1 + b"\xff\xd9"
        self.assertEqual(KB.exif_thumbnail(jpeg), thumb)
        self.assertIsNone(KB.exif_thumbnail(self.body.card["/EXT0/DCIM/100RICOH/RIMG0001.JPG"]))

    # ---- guards
    def test_bad_origin_and_host_refused(self):
        status, _, _ = self.jreq("GET", "/status", headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.jreq("GET", "/status", host="evil.example:8765")
        self.assertEqual(status, 403)
        for origin in ("null", "file://", "http://localhost:8000", "http://127.0.0.1:9000"):
            status, headers, _ = self.jreq("GET", "/status", headers={"Origin": origin})
            self.assertEqual(status, 200, origin)
            self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

    def test_preflight(self):
        status, headers, _ = self.req("OPTIONS", "/settings", headers={
            "Origin": "null", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(status, 204)
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])
        self.assertEqual(headers.get("Access-Control-Allow-Private-Network"), "true")

    def test_unknown_endpoint(self):
        status, _, _ = self.jreq("POST", "/photos")
        self.assertEqual(status, 404)


class AllowlistTest(unittest.TestCase):
    def test_table_is_exactly_decision_11_plus_tether(self):
        # keyset: every code 0..41 except power (6, 7, 8), AV jack (9) and PTP switch (24, 36), actions 0/1/3 only
        codes = set(range(42)) - {6, 7, 8, 9, 24, 36}
        tether = {"remtin", "remtout"} | {f"diaset,{n}" for n in range(9)} | {
            f"keyset,{a},{c}" for a in (0, 1, 3) for c in codes}
        self.assertEqual(KB.WRITE_ALLOWLIST[:2], (("-STOR", "/EXT0/PARAM.DAT"), ("-CCTL", "poff," + KB.POFF_ARG)))
        self.assertEqual(set(KB.WRITE_ALLOWLIST[2:]), {("-CCTL", a) for a in tether})
        self.assertEqual(len(KB.WRITE_ALLOWLIST), 2 + len(tether))
        for a in tether:
            self.assertEqual(KB.write_command("-CCTL", a), f"-CCTL {a}\r\n".encode())

    def test_allowed_rows_build(self):
        self.assertEqual(KB.write_command("-STOR", "/EXT0/PARAM.DAT", 4096), b"-STOR 4096 /EXT0/PARAM.DAT\r\n")
        self.assertEqual(KB.write_command("-CCTL", "poff," + KB.POFF_ARG), f"-CCTL poff,{KB.POFF_ARG}\r\n".encode())

    def test_every_other_write_raises(self):
        refused = [
            ("DELE", "/EXT0/PARAM.DAT"), ("DELE", "/EXT0/DCIM/100RICOH/RIMG0001.JPG"), ("RMD", "/EXT0/DCIM"),
            ("RNFR", "/EXT0/PARAM.DAT"), ("RNTO", "/EXT0/PARAM.OK"), ("MKD", "/EXT0/X"), ("-ABOR", ""),
            ("CWD", "/EXT0"), ("-FMT", "/EXT0"), ("-STOR", "/EXT0/PARAM.CUR"), ("-STOR", "/EXT0/QRFLECT3"),
            ("-STOR", "/EXT0/SCRIPT.MG1"), ("-STOR", "/ATA1/PARAM.DAT"), ("-STOR", "/IROM/PARAM.DAT"),
            ("-STOR", "/EXT0/param.dat"), ("-CCTL", "poff,1"), ("-CCTL", "keyset,3,6"), ("-CCTL", "keyset,1,7"),
            ("-CCTL", "keyset,3,9"), ("-CCTL", "keyset,3,42"), ("-CCTL", "keyset,1,99"), ("-CCTL", "keyset,4,11"),
            ("-CCTL", "keyset,1,8"), ("-CCTL", "keyset,1,24"), ("-CCTL", "keyset,1,36"), ("-CCTL", "keyset,2,26"),
            ("-CCTL", "keyset,1,26,1"), ("-CCTL", "keyset,1,026"), ("-CCTL", "keyset 1 26"), ("-CCTL", "diaset,9"),
            ("-CCTL", "diaset,-1"), ("-CCTL", "diaset,4\r\n-CCTL poff,5"), ("-CCTL", "REMTIN"), ("-CCTL", "remtin "),
            ("-SDIS", ""), ("-FUPL", ""), ("-PWOF", ""),
        ]
        for verb, arg in refused:
            with self.assertRaises(KB.WriteRefused, msg=f"{verb} {arg}"):
                KB.write_command(verb, arg, 4096)
        with self.assertRaises(KB.WriteRefused):
            KB.write_command("-STOR", "/EXT0/PARAM.DAT", 4095)

    def test_read_builder_refuses_writes_and_internal(self):
        for verb in ("DELE", "-STOR", "-CCTL", "RMD", "RNFR", "RNTO", "MKD", "CWD", "-ABOR"):
            with self.assertRaises(KB.CommandRefused):
                KB.read_command(verb, "/EXT0/PARAM.DAT")
        for path in ("/IROM/DATA/ONE.BIN", "/EXT0/../IROM", "/EXT0/a b.JPG", "/ATA1/PARAM.CUR"):
            with self.assertRaises(KB.CommandRefused):
                KB.read_command("RETR", path)

    def test_session_has_no_generic_send(self):
        public = {n for n in dir(KB.BridgeSession) if not n.startswith("_")}
        self.assertFalse({"send", "command", "raw", "delete", "rename", "store"} & public, public)


class TetherTest(unittest.TestCase):
    """Tether endpoints against the simulated camera, with and without the simulated Koma tether feature."""

    def make(self, tether, grow_s=0.0):
        self.body = mockcard.MockBody(tether=tether, shot_delay_s=0.05, grow_s=grow_s)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.af_wait_s = self.link.press_s = self.link.first_poll_s = 0.0
        self.link.poll_s, self.link.shot_timeout_s = 0.02, 0.3
        self.link.stable_poll_s = 0.02
        l = self.link
        l.ksta_poll_s = l.settle_unseen_s = l.settle_delay_s = l.list_retry_s = 0.02
        self.server = KB.make_server(self.link, port=0)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=b"", method="POST")
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read())

    def sent(self):
        return [c.strip() for t in self.body.transports for c in t.sent if c.startswith("-CCTL")]

    def test_start_shoot_stop_with_feature(self):
        self.make(tether=True)
        r = self.post("/tether/start")
        self.assertTrue(r["ok"], r)
        self.assertEqual([x["command"] for x in r["replies"]], ["-CCTL remtin", "-CCTL diaset,4", "-LVFR 0 (wait)"])
        r = self.post("/tether/shoot?fetch=1")
        self.assertEqual(r["outcome"], "shot", r)
        self.assertEqual(r["photo"]["name"], "RIMG0004.JPG")
        self.assertEqual(r["photo"]["path"], "100RICOH/RIMG0004.JPG")
        self.assertEqual(base64.b64decode(r["jpeg"]), self.body.card["/EXT0/DCIM/100RICOH/RIMG0004.JPG"])
        self.assertEqual([x["command"] for x in r["replies"]],
                         ["-CCTL keyset,1,26", "-CCTL keyset,1,27", "-CCTL keyset,0,27", "-CCTL keyset,0,26"])
        self.assertTrue(all(x["reply"].startswith("200") for x in r["replies"]))
        self.assertEqual(self.body.keys, set())
        self.assertEqual(self.post("/tether/shoot")["photo"]["name"], "RIMG0005.JPG")
        r = self.post("/tether/stop")
        self.assertEqual([x["command"] for x in r["replies"]],
                         ["-CCTL keyset,0,27", "-CCTL keyset,0,26", "-CCTL keyset,0,2", "-CCTL remtout"])
        self.assertEqual((self.body.remote, self.body.dial, self.body.keys), (0, 9, set()))
        for t in self.body.transports:
            self.assertEqual(t.violations, [])

    def test_shoot_waits_for_file_to_stop_growing(self):
        self.make(tether=True, grow_s=0.6)
        self.link.shot_timeout_s = 5.0
        self.post("/tether/start")
        r = self.post("/tether/shoot")
        self.assertEqual(r["outcome"], "shot", r)
        path = "/EXT0/DCIM/100RICOH/RIMG0004.JPG"
        final = len(self.body.card[path])
        self.assertEqual(r["photo"]["size"], final)
        self.assertEqual(self.body.growing, {})
        self.assertGreater(final, 1000)
        # -INFO was not sent before the file was complete: every -INFO on it saw the final size
        self.assertEqual([x for x in self.body.info_log if "RIMG0004" in x[0]], [(path, final)])

    def test_stock_camera_no_file(self):
        self.make(tether=False)
        self.post("/tether/start")
        r = self.post("/tether/shoot")
        self.assertEqual(r["outcome"], "no_file", r)
        self.assertFalse(r["ok"])
        self.assertIn("stock firmware", r["message"])
        self.assertNotIn("photo", r)
        self.assertEqual(self.body.keys, set())
        self.assertEqual(r["replies"][-2:], [{"command": "-CCTL keyset,0,27", "reply": "200 -CCTL keyset command successful."},
                                              {"command": "-CCTL keyset,0,26", "reply": "200 -CCTL keyset command successful."}])

    def test_keys_released_on_failure(self):
        self.make(tether=True)
        self.post("/tether/start")
        # a 451 on the full press: half-press already held, must come back up
        self.body.fail_keys = {"keyset,1,27"}
        r = self.post("/tether/shoot")
        self.assertEqual(r["outcome"], "refused", r)
        self.assertIn({"command": "-CCTL keyset,1,27", "reply": "451 -CCTL keyset command failure"}, r["replies"])
        self.assertEqual(self.body.keys, set())
        self.assertEqual(self.sent()[-2:], ["-CCTL keyset,0,27", "-CCTL keyset,0,26"])
        # an exception between the presses (anything: here the AF wait blows up): keys still released
        self.body.fail_keys = set()

        def boom(_):
            raise RuntimeError("interrupted")
        self.link.sleep = boom
        with self.link.lock:
            with self.assertRaises(RuntimeError):
                KB._press(self.link, self.link.session, [], full=True)
        self.assertEqual(self.body.keys, set())
        self.assertEqual(self.sent()[-3:], ["-CCTL keyset,1,26", "-CCTL keyset,0,27", "-CCTL keyset,0,26"])

    def test_release_order_before_stop_and_remtout(self):
        self.make(tether=True)
        self.post("/tether/start")
        self.post("/tether/shoot")
        self.post("/tether/stop")
        sent = self.sent()
        # in stop itself (the last four commands): 0,27 then 0,26 then 0,2 then remtout
        self.assertEqual(sent[-4:], ["-CCTL keyset,0,27", "-CCTL keyset,0,26", "-CCTL keyset,0,2", "-CCTL remtout"])


# ---------------------------------------------------------------------------------------------- live view helpers

def jpeg_segments(jpeg: bytes):
    """(marker, body) up to and including SOS, then the scan bytes. Asserts SOI and a final EOI."""
    assert jpeg[:2] == b"\xff\xd8", "no SOI"
    assert jpeg[-2:] == b"\xff\xd9", "no EOI"
    i, segs = 2, []
    while True:
        assert jpeg[i] == 0xFF, f"marker expected at {i}"
        marker, length = jpeg[i + 1], int.from_bytes(jpeg[i + 2:i + 4], "big")
        segs.append((marker, jpeg[i + 4:i + 2 + length]))
        i += 2 + length
        if marker == 0xDA:
            return segs, jpeg[i:-2]


def walk_scan(jpeg: bytes) -> int:
    """Huffman-decodes the whole baseline scan (no IDCT) with the file's own DHT, SOF0 sampling and SOS table choice.
    Returns the MCU count; fails if the bits run out early or the scan has bytes left over."""
    segs, scan = jpeg_segments(jpeg)
    tables, comps, order = {}, {}, []
    for marker, body in segs:
        if marker == 0xC4:
            j = 0
            while j < len(body):
                tc_th, bits = body[j], body[j + 1:j + 17]
                vals = body[j + 17:j + 17 + sum(bits)]
                codes, code, k = {}, 0, 0
                for length in range(1, 17):
                    for _ in range(bits[length - 1]):
                        codes[(length, code)] = vals[k]
                        code += 1
                        k += 1
                    code <<= 1
                tables[tc_th] = codes
                j += 17 + sum(bits)
        if marker == 0xC0:
            h, w, n = int.from_bytes(body[1:3], "big"), int.from_bytes(body[3:5], "big"), body[5]
            for c in range(n):
                comps[body[6 + 3 * c]] = (body[7 + 3 * c] >> 4, body[7 + 3 * c] & 15)
        if marker == 0xDA:
            order = [(body[1 + 2 * c], body[2 + 2 * c] >> 4, body[2 + 2 * c] & 15) for c in range(body[0])]
    data = bytearray()
    j = 0
    while j < len(scan):
        data.append(scan[j])
        j += 2 if scan[j] == 0xFF else 1  # FF 00 stuffing
    pos = 0

    def bit():
        nonlocal pos
        b = (data[pos >> 3] >> (7 - (pos & 7))) & 1
        pos += 1
        return b

    def symbol(table):
        code = 0
        for length in range(1, 17):
            code = (code << 1) | bit()
            if (length, code) in table:
                return table[(length, code)]
        raise AssertionError("bad Huffman code")

    hmax = max(hv[0] for hv in comps.values())
    vmax = max(hv[1] for hv in comps.values())
    mcus = ((w + 8 * hmax - 1) // (8 * hmax)) * ((h + 8 * vmax - 1) // (8 * vmax))
    for _ in range(mcus):
        for cid, td, ta in order:
            for _ in range(comps[cid][0] * comps[cid][1]):
                s = symbol(tables[td])
                pos += s
                k = 1
                while k < 64:
                    rs = symbol(tables[0x10 | ta])
                    if rs == 0:
                        break
                    k += 16 if rs == 0xF0 else (rs >> 4) + 1
                    pos += rs & 15
    assert (len(data) * 8 - pos) < 8, f"{len(data) * 8 - pos} bits left over after {mcus} MCUs"
    return mcus


def read_part(resp, boundary=b"--komalive"):
    """One multipart part: (headers dict, body), or None at the closing boundary or end of stream."""
    line = resp.readline()
    while line in (b"\r\n",):
        line = resp.readline()
    if not line or line.strip() == boundary + b"--":
        return None
    assert line.strip() == boundary, line
    headers = {}
    while True:
        h = resp.readline().strip()
        if not h:
            break
        k, _, v = h.decode().partition(":")
        headers[k.strip().lower()] = v.strip()
    body = resp.read(int(headers["content-length"]))
    assert resp.read(2) == b"\r\n"
    return headers, body


class LiveTest(unittest.TestCase):
    """-LVFR live view against the simulated camera (mockcard's `liveview` simulation)."""

    def make(self, tether=True):
        self.body = mockcard.MockBody(tether=tether, shot_delay_s=0.05)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.af_wait_s = self.link.press_s = self.link.first_poll_s = 0.0
        self.link.poll_s, self.link.shot_timeout_s = 0.02, 2.0
        l = self.link
        l.ksta_poll_s = l.settle_unseen_s = l.settle_delay_s = l.list_retry_s = 0.02
        self.link.live_max_fps = 200.0
        self.server = KB.make_server(self.link, port=0)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.link.stop_live)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def post(self, path):
        with urllib.request.urlopen(urllib.request.Request(self.url(path), data=b"", method="POST"), timeout=10) as r:
            return json.loads(r.read())

    def get_json(self, path):
        with urllib.request.urlopen(self.url(path), timeout=10) as r:
            return json.loads(r.read())

    def lvfr_sent(self):
        return [c.strip() for t in self.body.transports for c in t.sent if c.startswith("-LVFR")]

    def wait_for(self, cond, timeout=5.0):
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.01)
        return False

    def test_read_builder_and_not_a_write(self):
        self.assertEqual(KB.read_command("-LVFR", "1,0"), b"-LVFR 1,0\r\n")
        self.assertEqual(KB.read_command("-LVFR", "0,65535"), b"-LVFR 0,65535\r\n")
        self.assertEqual(KB.read_command("-LVFR", "2,7"), b"-LVFR 2,7\r\n")
        for bad in ("4,0", "1,65536", "1,01", "1, 0", "1,0\r\nDELE /EXT0/X", "", None, "1", "-1,0", "1,0,0"):
            with self.assertRaises(KB.CommandRefused, msg=bad):
                KB.read_command("-LVFR", bad)
        self.assertNotIn("-LVFR", {v for v, _ in KB.WRITE_ALLOWLIST})
        with self.assertRaises(KB.WriteRefused):
            KB.write_command("-LVFR", "1,0")

    def test_header_reconstruction_matches_firmware_layout(self):
        for scale in (0, 256, 448, 640, 1024, 4000):
            hdr = KB.firmware_jpeg_header(scale)
            self.assertEqual(len(hdr), 193)
            self.assertEqual(hdr, mockcard.avi1_header(scale, 320, 240))
        self.assertEqual(KB.scale_table(KB.QT_LUMA, 0), KB.QT_LUMA)
        self.assertEqual(KB.scale_table(KB.QT_LUMA, 1024)[0], 16)
        self.assertEqual(KB.scale_table(b"\x63", 100000), b"\xff")
        self.assertEqual(KB.scale_table(b"\x01", 1), b"\x01")
        body = mockcard.MockBody()
        scan = body.live_frame_bytes(1, 7)
        full = mockcard.encode_avi1_frame(320, 240, lambda bx, by: mockcard._lv_pattern(7)(bx * 8 + 4, by * 8 + 4), 448)
        self.assertEqual(full[193:], scan)  # the mock really strips the header
        jpeg = KB.frame_image(KB.parse_lvfr(mockcard.lvfr_payload(1, 0, 5, 320, 240, 448, scan)))[0]
        segs, _ = jpeg_segments(jpeg)
        self.assertEqual([m for m, _ in segs], [0xE0, 0xDD, 0xDB, 0xC0, 0xC4, 0xDA])
        sof = dict(segs)[0xC0]
        self.assertEqual((int.from_bytes(sof[1:3], "big"), int.from_bytes(sof[3:5], "big")), (240, 320))
        self.assertEqual(sof[6:], bytes.fromhex("012100021101031101"))  # 4:2:2
        self.assertEqual(walk_scan(jpeg), 600)  # 20 x 30 MCUs of 16x8, as the encoder probe saw
        import shutil
        import subprocess
        import tempfile
        if shutil.which("sips"):
            with tempfile.TemporaryDirectory() as d:
                src, dst = Path(d) / "f.jpg", Path(d) / "f.png"
                src.write_bytes(jpeg)
                subprocess.run(["sips", "-s", "format", "png", str(src), "--out", str(dst)], capture_output=True)
                self.assertTrue(dst.exists() and dst.stat().st_size > 100, "sips could not decode the rebuilt JPEG")

    def test_parse_lvfr_rejects_bad_payloads(self):
        good = mockcard.lvfr_payload(0, 0, 1, 2, 2, 0, b"abcd")
        self.assertEqual(KB.parse_lvfr(good)["data"], b"abcd")
        self.assertEqual(KB.LVFR_HEADER.size, 16)
        for bad in (good[:10], b"XXXX" + good[4:], good + b"x", good[:-1]):
            with self.assertRaises(KB.ProtocolError):
                KB.parse_lvfr(bad)
        png = KB.frame_image(KB.parse_lvfr(good))
        self.assertEqual((png[1], png[0][:8]), ("image/png", b"\x89PNG\r\n\x1a\n"))

    def test_live_needs_remote_control(self):
        self.make()
        try:
            urllib.request.urlopen(self.url("/live?mode=1"), timeout=10)
            self.fail("expected 409")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 409)
            d = json.loads(e.read())
            self.assertEqual(d["code"], "live_off")
            self.assertTrue(d["reply"].startswith("451"))
        self.assertFalse(self.get_json("/live/status")["running"])

    def test_frame_endpoint(self):
        self.make()
        self.post("/tether/start")
        with urllib.request.urlopen(self.url("/live/frame?mode=1"), timeout=10) as r:
            self.assertEqual(r.headers["Content-Type"], "image/jpeg")
            self.assertEqual(walk_scan(r.read()), 600)
        with urllib.request.urlopen(self.url("/live/frame?mode=0"), timeout=10) as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
        with urllib.request.urlopen(self.url("/live/frame?mode=2"), timeout=10) as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.url("/live/frame?mode=3"), timeout=10)

    def test_stream_yields_parts_both_modes(self):
        self.make()
        self.post("/tether/start")
        for mode, ctype in ((1, "image/jpeg"), (0, "image/png")):
            resp = urllib.request.urlopen(self.url(f"/live?mode={mode}"), timeout=10)
            self.assertTrue(resp.headers["Content-Type"].startswith("multipart/x-mixed-replace; boundary=komalive"))
            parts = [read_part(resp) for _ in range(12)]
            self.assertTrue(all(p and p[0]["content-type"] == ctype for p in parts))
            seqs = [int(p[0]["x-frame-seq"]) for p in parts]
            self.assertEqual(seqs, sorted(seqs))
            if mode == 1:
                self.assertEqual(walk_scan(parts[-1][1]), 600)
            st = self.get_json("/status")["live"]
            self.assertTrue(st["running"])
            self.assertEqual(st["mode"], mode)
            self.assertGreater(st["fps"], 0)
            self.assertGreater(st["bytes_per_s"], 0)
            resp.close()  # client goes away: the loop notices on its next write
            self.assertTrue(self.wait_for(lambda: not self.link.live.running), "stream did not end on disconnect")
            self.assertEqual(self.link.live.ended, "client")
            self.assertTrue(self.link.lock.acquire(timeout=1))
            self.link.lock.release()
        for t in self.body.transports:
            self.assertEqual(t.violations, [])

    def test_451_ends_stream_cleanly(self):
        self.make()
        self.link.live_off_grace_s = 0.6  # the real grace is 3 s; shortened to keep the test quick
        self.post("/tether/start")
        resp = urllib.request.urlopen(self.url("/live?mode=1"), timeout=10)
        self.assertIsNotNone(read_part(resp))
        self.body.remote = 0  # the camera leaves state 2 (monitor stops): next -LVFR answers 451
        parts = 0
        while read_part(resp) is not None:
            parts += 1
            self.assertLess(parts, 50)
        self.assertEqual(resp.read(), b"")  # closing boundary, then the connection closes
        self.assertTrue(self.wait_for(lambda: not self.link.live.running))
        self.assertTrue(self.link.live.ended.startswith("camera: 451"), self.link.live.ended)
        self.assertEqual(self.post("/tether/stop")["ok"], True)

    def test_451_is_transient_up_to_grace(self):
        self.make()
        self.post("/tether/start")
        resp = urllib.request.urlopen(self.url("/live?mode=1"), timeout=10)
        self.assertIsNotNone(read_part(resp))
        self.body.remote = 0
        self.assertTrue(self.wait_for(lambda: self.link.live.snapshot()["paused"]))
        snap = self.link.live.snapshot()
        self.assertEqual((snap["running"], snap["reason"]), (True, "camera busy"))
        self.body.remote = 2  # back before the 3 s are up: the stream carries on
        self.assertTrue(self.wait_for(lambda: not self.link.live.snapshot()["paused"]))
        self.assertIsNotNone(read_part(resp))
        self.assertTrue(self.link.live.running)
        resp.close()

    def test_stream_survives_shoot_451_window(self):
        self.make(tether=True)
        self.post("/tether/start")
        resp = urllib.request.urlopen(self.url("/live?mode=1"), timeout=10)
        self.assertIsNotNone(read_part(resp))
        self.assertEqual(self.post("/tether/shoot")["outcome"], "shot")
        # the mock's monitor restarts: -LVFR says 451 for about a second after the shot, the stream waits it out
        self.assertTrue(self.wait_for(lambda: self.link.live.snapshot()["reason"] == "camera busy"
                                      or self.link.live.snapshot()["frames"] > 1, 3))
        frames = self.link.live.snapshot()["frames"]
        self.assertTrue(self.wait_for(lambda: self.link.live.snapshot()["frames"] > frames + 2, 6))
        self.assertTrue(self.link.live.running)
        self.assertIsNone(self.link.live.ended)
        resp.close()

    def test_tether_stop_ends_stream(self):
        self.make()
        self.post("/tether/start")
        resp = urllib.request.urlopen(self.url("/live?mode=0"), timeout=10)
        self.assertIsNotNone(read_part(resp))
        self.post("/tether/stop")
        while read_part(resp) is not None:
            pass
        self.assertTrue(self.wait_for(lambda: not self.link.live.running))
        self.assertIn(self.link.live.ended, ("stopped", "camera: 451 -LVFR not in live view"))

    def test_shoot_interleaves_with_stream(self):
        self.make(tether=True)
        self.link.live_max_fps = 60.0
        self.post("/tether/start")
        resp = urllib.request.urlopen(self.url("/live?mode=1"), timeout=10)
        got, stop = [], threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    p = read_part(resp)
                    if p is None:
                        break
                    got.append(p)
            except Exception:  # noqa: BLE001  (the test closed the response under the reader)
                pass
        threading.Thread(target=reader, daemon=True).start()
        self.assertTrue(self.wait_for(lambda: len(got) >= 3))
        seen_paused = []
        real_sleep = self.link.sleep

        def spy(s):  # runs inside the shoot, while it holds the camera lock
            seen_paused.append((self.link.live_hold, self.link.live.snapshot()["paused"]))
            real_sleep(s)
        self.link.sleep = spy
        r = self.post("/tether/shoot")
        self.assertEqual(r["outcome"], "shot", r)
        self.assertTrue(seen_paused and all(h == 1 and p for h, p in seen_paused), seen_paused)
        # the stream asked nothing between the first and last key of the shoot
        sent = [c.strip() for t in self.body.transports for c in t.sent]
        first = sent.index("-CCTL keyset,1,26")
        last = len(sent) - 1 - sent[::-1].index("-CCTL keyset,0,26")
        self.assertFalse([c for c in sent[first:last] if c.startswith("-LVFR")])
        self.assertEqual((self.link.live_hold, self.link.live.snapshot()["paused"]), (0, False))
        n = len(got)
        self.assertTrue(self.wait_for(lambda: len(got) >= n + 3), "stream did not resume after the shoot")
        self.assertTrue(self.link.live.running)
        stop.set()
        resp.close()
        for t in self.body.transports:
            self.assertEqual(t.violations, [])

    def test_interval_shoots_on_schedule_and_cancels(self):
        """POST /tether/interval runs shoots through tether_shoot (mock tether: each makes a JPEG), GET reports
        progress, DELETE cancels between shoots; a running live stream is paused around each shoot."""
        self.make()
        self.post("/tether/start")
        r = urllib.request.Request(self.url("/tether/interval"), data=json.dumps({"every_s": 1, "count": 2}).encode(),
                                   method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            self.assertTrue(json.loads(resp.read())["interval"]["running"])
        with self.assertRaises(urllib.error.HTTPError) as busy:   # one run at a time
            urllib.request.urlopen(urllib.request.Request(self.url("/tether/interval"), data=b'{"every_s":1,"count":1}',
                                                          method="POST"), timeout=10)
        self.assertEqual(busy.exception.code, 409)
        self.assertTrue(self.wait_for(lambda: not self.get_json("/tether/interval")["running"], 15))
        st = self.get_json("/tether/interval")
        self.assertEqual((st["done"], st["shots"], st["failed"], st["ended"]), (2, 2, 0, "finished"))
        self.assertEqual(st["last"]["outcome"], "shot")
        self.assertEqual(len(KB.list_photos(self.link)["photos"]), 5)   # 3 on the card + 2 (HTTP /photos is 409 in Remote)
        # a long run, cancelled after its first shoot
        r = urllib.request.Request(self.url("/tether/interval"), data=b'{"every_s": 30, "count": 5}', method="POST")
        urllib.request.urlopen(r, timeout=10).close()
        self.assertTrue(self.wait_for(lambda: self.get_json("/tether/interval")["done"] == 1, 10))
        with urllib.request.urlopen(urllib.request.Request(self.url("/tether/interval"), method="DELETE"), timeout=10) as resp:
            self.assertEqual(json.loads(resp.read())["interval"]["ended"], "cancelled")
        self.assertTrue(self.wait_for(lambda: not self.get_json("/tether/interval")["running"], 5))
        self.assertEqual(self.get_json("/status")["interval"]["done"], 1)
        for bad in ({"every_s": 0.1, "count": 2}, {"every_s": 5, "count": 0}, {"count": 3}):
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(self.url("/tether/interval"),
                                                              data=json.dumps(bad).encode(), method="POST"), timeout=10)
            self.assertEqual(e.exception.code, 400)
        self.assertTrue(self.link.lock.acquire(timeout=1))
        self.link.lock.release()

    def test_new_stream_supersedes_old(self):
        self.make()
        self.post("/tether/start")
        a = urllib.request.urlopen(self.url("/live?mode=1"), timeout=10)
        self.assertIsNotNone(read_part(a))
        b = urllib.request.urlopen(self.url("/live?mode=0"), timeout=10)
        self.assertIsNotNone(read_part(b))
        while read_part(a) is not None:
            pass
        self.assertTrue(self.link.live.running)
        self.assertEqual(self.post("/live/stop"), {"ok": True})
        while read_part(b) is not None:
            pass
        self.assertTrue(self.wait_for(lambda: not self.link.live.running))


def decode_png(png: bytes) -> dict:
    """Minimal PNG reader for the tests (indexed, filter 0 rows): header, palette, tRNS and the index of each pixel."""
    import struct
    import zlib
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    i, chunks, idat = 8, {}, b""
    while i < len(png):
        n, tag = struct.unpack_from(">I4s", png, i)
        body = png[i + 8:i + 8 + n]
        assert struct.unpack_from(">I", png, i + 8 + n)[0] == zlib.crc32(tag + body) & 0xffffffff, tag
        if tag == b"IDAT":
            idat += body
        else:
            chunks[tag] = body
        i += 12 + n
    w, h, depth, ctype = struct.unpack_from(">IIBB", chunks[b"IHDR"])
    raw, stride = zlib.decompress(idat), (w * depth + 7) // 8
    index = []
    for r in range(h):
        row = raw[r * (stride + 1):(r + 1) * (stride + 1)]
        assert row[0] == 0
        for x in range(w):
            b = row[1 + x // 2]
            index.append(b >> 4 if x % 2 == 0 else b & 0xf)
    plte = chunks.get(b"PLTE", b"")
    return {"w": w, "h": h, "depth": depth, "ctype": ctype, "index": index,
            "palette": [tuple(plte[k:k + 3]) for k in range(0, len(plte), 3)], "trns": chunks.get(b"tRNS")}


class RemoteTest(unittest.TestCase):
    """The Remote task: -LVFR mode 2 (OSD page) decode and stream, the key/dial endpoints and the key allowlist."""

    NAV = (11, 12, 13, 14, 15, 16, 20, 22, 23, 26, 27)
    REFUSED = (6, 7, 8, 9, 24, 36)

    def make(self):
        self.body = mockcard.MockBody(tether=False, shot_delay_s=0.05)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.live_max_fps = 200.0
        self.link.osd_max_fps = 200.0
        self.server = KB.make_server(self.link, port=0)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.link.stop_live)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def call(self, method, path, obj=None):
        data = json.dumps(obj).encode() if obj is not None else (b"" if method == "POST" else None)
        r = urllib.request.Request(self.url(path), data=data, method=method,
                                   headers={"Content-Type": "application/json"} if obj is not None else {})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def keysets(self):
        return [c.strip() for t in self.body.transports for c in t.sent if c.startswith("-CCTL keyset")]

    # ---- decode
    @unittest.skipUnless(mockcard.osd_pages_present(), "the emulator OSD dumps (testdata/osd-*.bin) are not shipped")
    def test_mode2_decode_known_page(self):
        meta = json.loads((mockcard.OSD_DIR / "osd-pages.json").read_text())
        rows = (mockcard.OSD_DIR / "osd-menu.bin").read_bytes()
        self.assertEqual(len(rows), 180 * 240)
        hist = [0] * 16
        for b in rows:
            hist[b >> 4] += 1
            hist[b & 15] += 1
        pal = b"".join(__import__("struct").pack(">I", v or 0) for v in meta["palette_raw"])
        payload = mockcard.lvfr_payload(2, 0, 5, 360, 240, 16, rows + pal)
        frame = KB.parse_lvfr(payload)
        png, ctype = KB.frame_image(frame)
        self.assertEqual(ctype, "image/png")
        d = decode_png(png)
        self.assertEqual((d["w"], d["h"], d["depth"], d["ctype"]), (360, 240, 4, 3))
        self.assertEqual(d["trns"], b"\x00")  # index 0 fully transparent, every other index opaque
        self.assertEqual(len(d["palette"]), 16)
        counts = [d["index"].count(k) for k in range(16)]
        self.assertEqual(counts, hist)  # indexes survive unchanged, so transparency is exactly the index-0 pixels
        self.assertEqual(counts[0], 56241)
        self.assertEqual(d["palette"][1], (254, 254, 254))   # 0xda0000: white menu text
        self.assertEqual(d["palette"][4], (58, 58, 58))      # 0x320000: menu background
        self.assertEqual(d["palette"][11], (253, 254, 194))  # 0xd4e604: selected row
        # fixed palette (scale 0) at the size the firmware lane expects, 320x240 = 38,400 bytes
        page = bytes(((x // 8) % 16) << 4 | ((x // 8) % 16) for x in range(160)) * 240
        self.assertEqual(len(page), 38400)
        d2 = decode_png(KB.osd_png(KB.parse_lvfr(mockcard.lvfr_payload(2, 0, 0, 320, 240, 0, page))))
        self.assertEqual((d2["w"], d2["h"]), (320, 240))
        self.assertEqual(d2["palette"][1:], [KB.ycbcr_to_rgb((v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff)
                                             for v in KB.OSD_FIXED_PALETTE_RAW[1:]])
        self.assertEqual(d2["index"].count(0), 240 * 32)  # bytes 0..7 and 128..135 of each row: 32 pixels of index 0
        # sizes are checked against the header
        for bad in (mockcard.lvfr_payload(2, 0, 0, 320, 240, 0, page[:-1]),
                    mockcard.lvfr_payload(2, 0, 0, 320, 240, 16, page),
                    mockcard.lvfr_payload(2, 0, 0, 320, 240, 65, page + bytes(260))):
            with self.assertRaises(KB.ProtocolError):
                KB.osd_png(KB.parse_lvfr(bad))
        # a short appended palette: entries past it render magenta, as tools/koma/emu/grdui/display.py does
        d3 = decode_png(KB.osd_png(KB.parse_lvfr(mockcard.lvfr_payload(2, 0, 0, 320, 240, 2, page + bytes(4) + pal[4:8]))))
        self.assertEqual(d3["palette"][1], (254, 254, 254))
        self.assertEqual(d3["palette"][2], (255, 0, 255))

    @unittest.skipUnless(mockcard.osd_pages_present(), "the emulator OSD dumps (testdata/osd-*.bin) are not shipped")
    def test_mode2_zero_palette_falls_back_to_rom_table(self):
        """Register readback of entries 1..15 all zero (may happen on hardware): the ROM default table 0x1fba30 is used
        and the OSD status carries the note; a real appended palette is used as sent, with no note."""
        rows = (mockcard.OSD_DIR / "osd-menu.bin").read_bytes()
        frame = KB.parse_lvfr(mockcard.lvfr_payload(2, 0, 7, 360, 240, 16, rows + bytes(64)))
        d = decode_png(KB.osd_png(frame))
        self.assertEqual(frame["palette_note"], "palette: ROM default")
        self.assertEqual(d["palette"][1:], [KB.ycbcr_to_rgb((v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff)
                                            for v in KB.OSD_ROM_PALETTE_RAW[1:]])
        stats = KB.LiveStats(lambda: 0.0)
        stats.reset(2)
        stats.flags(frame)
        self.assertEqual(stats.snapshot()["note"], "palette: ROM default")
        good = KB.parse_lvfr(mockcard.lvfr_payload(2, 0, 8, 360, 240, 16, rows + bytes(4) + b"\x00\xda\x00\x00" * 15))
        KB.osd_png(good)
        self.assertNotIn("palette_note", good)
        stats.flags(good)
        self.assertIsNone(stats.snapshot()["note"])

    def test_header_flags_counted(self):
        """bit 1 VD sync timed out and bit 3 torn are counted per stream for /status; bits 2 and 4 are parsed."""
        stats = KB.LiveStats(lambda: 0.0)
        stats.reset(1)
        for flags in (0, KB.LVFR_TORN, KB.LVFR_TORN | KB.LVFR_SYNC_TIMEOUT, KB.LVFR_SYNC_TIMEOUT):
            stats.flags(KB.parse_lvfr(mockcard.lvfr_payload(0, flags, 1, 2, 1, 0, b"ab")))
        snap = stats.snapshot()
        self.assertEqual((snap["torn"], snap["sync_timeouts"]), (2, 2))
        f = KB.parse_lvfr(mockcard.lvfr_payload(2, KB.LVFR_PAGE_B | KB.LVFR_OSD_BASE_UNKNOWN, 1, 2, 1, 0, b"a"))
        self.assertTrue(f["page_b"] and f["osd_base_unknown"] and not f["torn"])

    # ---- allowlist
    def test_allowlist_nav_keys_and_refused_codes(self):
        for code in self.NAV + (2,):
            for action in (0, 1, 3):
                self.assertEqual(KB.write_command("-CCTL", f"keyset,{action},{code}"),
                                 f"-CCTL keyset,{action},{code}\r\n".encode())
            with self.assertRaises(KB.WriteRefused):
                KB.write_command("-CCTL", f"keyset,2,{code}")
        for code in self.REFUSED:
            for action in range(5):
                with self.assertRaises(KB.WriteRefused, msg=f"keyset,{action},{code}"):
                    KB.write_command("-CCTL", f"keyset,{action},{code}")
            self.assertNotIn(code, KB.KEYSET_CODES)

    # ---- endpoints
    def test_keys_refused_outside_remote_mode(self):
        self.make()
        status, listing = self.call("GET", "/keys")
        self.assertEqual(status, 200)
        self.assertFalse(listing["remote"])
        codes = {k["code"] for k in listing["keys"]}
        self.assertTrue(set(self.NAV) <= codes)
        self.assertFalse(set(self.REFUSED) & codes)
        self.assertEqual({k["code"] for k in listing["refused"]}, set(self.REFUSED))
        for path, obj in (("/keys/tap", {"code": 11}), ("/keys", {"code": 23, "action": 1}), ("/dial", {"n": 4})):
            status, d = self.call("POST", path, obj)
            self.assertEqual((status, d.get("code")), (409, "remote_off"), path)
        self.assertEqual(self.keysets(), [])
        # after tether/stop it is refused again
        self.assertTrue(self.call("POST", "/tether/start")[1]["ok"])
        self.assertTrue(self.call("POST", "/keys/tap", {"code": 22})[1]["ok"])
        self.call("POST", "/tether/stop")
        status, d = self.call("POST", "/keys/tap", {"code": 22})
        self.assertEqual(status, 409)
        self.assertEqual(self.keysets().count("-CCTL keyset,3,22"), 1)

    def test_refused_codes_and_bad_bodies(self):
        self.make()
        self.call("POST", "/tether/start")
        for code in self.REFUSED:
            for action in (0, 1, 3):
                status, d = self.call("POST", "/keys", {"code": code, "action": action})
                self.assertEqual((status, d.get("code")), (403, "key_refused"))
            self.assertEqual(self.call("POST", "/keys/tap", {"code": code})[0], 403)
        for obj in ({"code": 11, "action": 2}, {"code": 42, "action": 3}, {"code": "11"}, {"code": True}, {}):
            path = "/keys" if "action" in obj else "/keys/tap"
            self.assertEqual(self.call("POST", path, obj)[0], 400, obj)
        for n in (9, -1, "4"):
            self.assertEqual(self.call("POST", "/dial", {"n": n})[0], 400)
        self.assertEqual(self.keysets(), [])
        for t in self.body.transports:
            self.assertEqual(t.violations, [])

    def test_keys_tap_hold_release_and_dial(self):
        self.make()
        self.call("POST", "/tether/start")
        for code in self.NAV:
            status, d = self.call("POST", "/keys/tap", {"code": code})
            self.assertEqual((status, d["ok"]), (200, True))
        status, d = self.call("POST", "/keys", {"code": 13, "action": 1})
        self.assertEqual((d["ok"], d["action"], d["name"]), (True, "hold", "RIGHT"))
        self.assertIn(13, self.body.keys)
        self.call("POST", "/keys", {"code": 13, "action": 0})
        self.assertNotIn(13, self.body.keys)
        status, d = self.call("POST", "/dial", {"n": 2})
        self.assertTrue(d["ok"])
        self.assertEqual(self.body.dial, 2)
        sent = self.keysets()
        self.assertEqual(sent[:len(self.NAV)], [f"-CCTL keyset,3,{c}" for c in self.NAV])  # a tap sends no release
        self.assertEqual(sent[len(self.NAV):], ["-CCTL keyset,1,13", "-CCTL keyset,0,13"])

    @unittest.skipUnless(mockcard.osd_pages_present(), "the emulator OSD dumps (testdata/osd-*.bin) are not shipped")
    def test_mode2_parts_while_monitor_stopped(self):
        self.make()
        # stock USB state (no remote control at all): mode 2 still answers, mode 1 does not
        resp = urllib.request.urlopen(self.url("/live?mode=2"), timeout=10)
        part = read_part(resp)
        self.assertEqual(part[0]["content-type"], "image/png")
        self.assertEqual(decode_png(part[1])["w"], 360)
        resp.close()
        # open the menu: the monitor stops, mode 1 answers 451, mode 2 keeps going and shows the menu page
        self.call("POST", "/tether/start")
        self.assertTrue(self.call("POST", "/keys/tap", {"code": 23})[1]["ok"])
        self.assertEqual(self.body.osd_screen, "menu")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url("/live/frame?mode=1"), timeout=10)
        self.assertEqual(cm.exception.code, 409)
        resp = urllib.request.urlopen(self.url("/live?mode=2"), timeout=10)
        self.assertTrue(resp.headers["Content-Type"].startswith("multipart/x-mixed-replace"))
        parts = [read_part(resp) for _ in range(6)]
        self.assertTrue(all(p and p[0]["content-type"] == "image/png" for p in parts))
        self.assertEqual(decode_png(parts[-1][1])["index"].count(4), 20028)  # the menu page's background
        self.assertTrue(self.call("GET", "/live/status?mode=2")[1]["running"])
        # DOWN changes the page the stream sends
        self.call("POST", "/keys/tap", {"code": 12})
        for _ in range(20):
            p = read_part(resp)
            if decode_png(p[1])["index"].count(11) == 4618:  # the highlight moved (osd-down.bin)
                break
        else:
            self.fail("the OSD stream never showed the DOWN page")
        resp.close()
        self.assertTrue(self.wait_for(lambda: not self.link.osd.running))

    def test_osd_and_video_streams_coexist(self):
        self.make()
        self.call("POST", "/tether/start")
        osd = urllib.request.urlopen(self.url("/live?mode=2"), timeout=10)
        video = urllib.request.urlopen(self.url("/live?mode=1"), timeout=10)
        for _ in range(4):
            self.assertEqual(read_part(video)[0]["content-type"], "image/jpeg")
            self.assertEqual(read_part(osd)[0]["content-type"], "image/png")
        self.assertTrue(self.link.live.running and self.link.osd.running)
        self.call("POST", "/live/stop?mode=1")  # ends the video only
        while read_part(video) is not None:
            pass
        self.assertIsNotNone(read_part(osd))
        self.call("POST", "/tether/stop")  # ends both
        while read_part(osd) is not None:
            pass
        self.assertTrue(self.wait_for(lambda: not self.link.osd.running and not self.link.live.running))

    def wait_for(self, cond, timeout=5.0):
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.01)
        return False


class _Served:
    """A bridge on an ephemeral port over a fresh MockBody (loopback mode, or phone mode with a token)."""

    token = None

    def setUp(self):
        self.body = mockcard.MockBody(reboot_s=4.0)
        self.link = KB.Link(KB.MockBackend(self.body))
        host = "0.0.0.0" if self.token else "127.0.0.1"
        self.server = KB.make_server(self.link, host=host, port=0, token=self.token)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port, self.token)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    req = BridgeTest.req
    jreq = BridgeTest.jreq

    def ksets(self):
        return [c.strip() for t in self.body.transports for c in t.sent if c.startswith("-KSET")]


class KsetAllowlistTest(unittest.TestCase):
    def test_window_is_a_range_rule(self):
        # 0x900 (recipe store header) and ALL of 0x8d0..0x8ff (KSIO header + reserve) refused, as the firmware does;
        # the Koma rows, a record and the FX table's end ok
        for off in (0x8C0, 0x910, 0xA13, 0x8B5, 0x8CF, 0x9FF, 0xA00):
            self.assertEqual(KB.write_command("-KSET", f"{off},3"), f"-KSET {off},3\r\n".encode())
            self.assertTrue(KB.kset_writable(off))
        for off in list(range(0x8D0, 0x900)) + [0x900, 0x90F, 0x8B4, 0x8B0, 0xA14, 0x000, 0xFFC, 0x05A, 0x058, 4095]:
            self.assertFalse(KB.kset_writable(off), hex(off))
            with self.assertRaises(KB.WriteRefused, msg=hex(off)):
                KB.write_command("-KSET", f"{off},3")
        for arg in ("2240,256", "2240,-1", "2240", "02240,1", "2240,01", "2240, 1", "2240,1,2", "0x8c0,1",
                    "2240,1\r\n-CCTL poff,5", ""):
            with self.assertRaises(KB.WriteRefused, msg=arg):
                KB.write_command("-KSET", arg)
        # the row table is unchanged: -KSET is not a row
        self.assertFalse(any(v == "-KSET" for v, _ in KB.WRITE_ALLOWLIST))

    def test_stock_whitelist_exact_values(self):
        want = {0x053: set(range(1, 26, 2)), 0x059: {0, 1, 2, 4, 6, 7, 8}, 0x054: {0, 1, 2, 3, 4, 5, 7},
                0x0A6: set(range(17)), 0x050: {0, 1, 2}, 0x030: set(range(7)), 0x1A9: {1, 2, 3, 4, 6},
                0x1AA: set(range(1, 6)), **{o: set(range(1, 6)) for o in range(0x031, 0x037)}}
        self.assertEqual({o: set(v) for o, v in KB.KSET_STOCK.items()}, want)
        self.assertEqual({o: v for o, v in mockcard.KSET_STOCK.items()}, want)  # the mock's independent copy
        for off, vals in want.items():
            for val in range(256):
                if val in vals:
                    self.assertEqual(KB.write_command("-KSET", f"{off},{val}"), f"-KSET {off},{val}\r\n".encode())
                else:
                    with self.assertRaises(KB.WriteRefused, msg=(hex(off), val)):
                        KB.write_command("-KSET", f"{off},{val}")
        # the named edges
        self.assertFalse(KB.kset_value_ok(0x053, 14))
        self.assertTrue(KB.kset_value_ok(0x053, 15))
        self.assertFalse(KB.kset_value_ok(0x059, 3))
        self.assertFalse(KB.kset_writable(0x05A))
        self.assertTrue(KB.kset_value_ok(0x8CF, 0))
        self.assertFalse(KB.kset_value_ok(0x8D0, 0))
        self.assertFalse(KB.kset_value_ok(0x8E0, 0))

    def test_mock_stock_and_busy(self):
        body = mockcard.MockBody()
        self.assertEqual(body.kset("83,15"), "451 -KSET busy")   # not tethered: no still-record app state
        body.remote = 2
        self.assertEqual(body.kset("83,15"), "200 -KSET 83=15")
        self.assertEqual(body.ram[0x53], 15)
        self.assertEqual(body.kset("83,14"), "451 -KSET value")
        self.assertEqual(body.kset("89,3"), "451 -KSET value")
        self.assertEqual(body.kset("90,0"), "451 -KSET range")   # 0x05a: legacy, unmapped
        self.assertEqual(body.kset(f"{0x8CF},0"), f"200 -KSET {0x8CF}=0")
        for off in (0x8D0, 0x8E0, 0x8FF):
            self.assertEqual(body.kset(f"{off},0"), "451 -KSET range")
        body.osd_screen = "menu"
        self.assertEqual(body.kset("83,17"), "451 -KSET busy")
        self.assertEqual(body.kset("83,14"), "451 -KSET value")  # value is checked before busy
        self.assertEqual(body.ram[0x53], 15)
        self.assertEqual(body.kset("2240,3"), "200 -KSET 2240=3")  # Koma bytes are not gated

    def test_kget_is_a_read(self):
        self.assertEqual(KB.read_command("-KGET", "2224,356"), b"-KGET 2224,356\r\n")
        self.assertEqual(KB.read_command("-KGET", "0,4096"), b"-KGET 0,4096\r\n")
        for arg in ("0,0", "4000,97", "0,4097", "1,4096", "x", "2224", "02224,1"):
            with self.assertRaises(KB.CommandRefused, msg=arg):
                KB.read_command("-KGET", arg)
        with self.assertRaises(KB.CommandRefused):
            KB.read_command("-KSET", "2240,1")  # never through the read builder

    def test_mock_rules_match(self):
        body = mockcard.MockBody()
        self.assertEqual(body.kset("2240,5"), "200 -KSET 2240=5")
        self.assertEqual(body.ram[0x8C0], 5)
        self.assertEqual(body.kset(f"{0x900},1"), "451 -KSET range")
        self.assertEqual(body.kset(f"{0x8D0},1"), "451 -KSET range")
        self.assertEqual(body.kset(f"{0x8E0},1"), "451 -KSET range")
        self.assertEqual(body.kset("2240,300"), "451 -KSET value")
        self.assertIsNone(body.kset("nonsense"))

    def test_mock_defaults_match_bwrecipes(self):
        sys.path.insert(0, str(HERE.parents[1]))
        try:
            from koma.features import bwrecipes as B
        except Exception as error:  # noqa: BLE001
            self.skipTest(f"bwrecipes not importable: {error}")
        want = tuple(tuple(r["params"]) + (r["type"],) for r in B.RECIPES)
        self.assertEqual(mockcard.RECIPE_DEFAULTS, want)


class PeekTest(unittest.TestCase):
    """-PEEK (ksettings v1.4): read-only RAM peek; builder limits and the session / Link call on the mock."""

    def test_read_command_limits(self):
        self.assertEqual(KB.read_command("-PEEK", "1a8000,16"), b"-PEEK 1a8000,16\r\n")
        self.assertEqual(KB.read_command("-PEEK", "3e4f00,256"), b"-PEEK 3e4f00,256\r\n")
        for arg in ("", "1a8000", "1a8000,0", "1a8000,257", "3e4fff,2", "3e5000,1", "3fff00,1", "400000,1",
                    "1a7fff,1", "1b000,16", "0,1", "0x1a8000,4", "g0,4", "12345678,1", "1a8000,016"):
            with self.assertRaises(KB.CommandRefused, msg=arg):
                KB.read_command("-PEEK", arg)
        with self.assertRaises(KB.WriteRefused):
            KB.write_command("-PEEK", "1b000,4")

    def test_peek_on_the_mock(self):
        body = mockcard.MockBody()
        link = KB.Link(KB.MockBackend(body))
        self.assertEqual(KB.peek(link, 0x1A8A00, 8), bytes((i * 7 + 3) & 0xFF for i in range(0x1A8A00, 0x1A8A08)))
        self.assertEqual(len(link.run(lambda s: s.peek(0x3E4F00, 256))), 256)
        for a, n in ((0x3E4FFF, 2), (0x1A7FFF, 1), (0x1B000, 4)):
            with self.assertRaises(KB.CommandRefused):
                KB.peek(link, a, n)
        sent = [c.strip() for t in body.transports for c in t.sent]
        self.assertEqual([c for c in sent if c.startswith("-PEEK")], ["-PEEK 1a8a00,8", "-PEEK 3e4f00,256"])
        self.assertEqual([v for t in body.transports for v in t.violations], [])


class KstaTest(unittest.TestCase):
    """-KSTA (ksettings v1.3): read builder, session decode, mock frames in state 9 and 2."""

    def test_read_command(self):
        self.assertEqual(KB.read_command("-KSTA"), b"-KSTA\r\n")
        for arg in ("1", "0,0", " "):
            with self.assertRaises(KB.CommandRefused, msg=arg):
                KB.read_command("-KSTA", arg)
        with self.assertRaises(KB.WriteRefused):
            KB.write_command("-KSTA", "")

    def test_layout(self):
        self.assertEqual(KB.KSTA_N, len(KB.KSTA_WORDS) + 1)
        self.assertEqual(KB.KSTA_VERSION >> 16, 0x4B53)
        names = [n for n, _, _ in KB.KSTA_WORDS]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("run_state", names)
        self.assertIn("tick", names)

    def test_decode_refuses_other_layouts(self):
        good = struct.pack(f">{KB.KSTA_N}I", KB.KSTA_VERSION, *range(KB.KSTA_N - 1))
        self.assertEqual(KB.ksta_decode(good)["usb_23a86c"], 0)
        for bad in (good[:-4], good[:-2], struct.pack(">I", KB.KSTA_VERSION + 0x100) + good[4:]):
            with self.assertRaises(KB.ProtocolError):
                KB.ksta_decode(bad)
        self.assertEqual(KB.ksta_decode(good + bytes(4))["ext"], [0])          # layout 2: an extension may follow
        v1 = struct.pack(f">{len(KB.KSTA_WORDS_V1) + 1}I", KB.KSTA_V1_VERSION, *range(len(KB.KSTA_WORDS_V1)))
        d = KB.ksta_decode(v1)                                                 # ksettings v1.3 frames still decode
        self.assertEqual((d["tick"], "lcd_sem_1a8d14" in d), (len(KB.KSTA_WORDS_V1) - 1, False))
        with self.assertRaises(KB.ProtocolError):
            KB.ksta_decode(v1 + bytes(4))

    def test_waitdiag_block_decodes(self):
        n = KB.WD_HDR_WORDS + KB.WD_NREC * KB.WD_RECW
        blk = [0] * n
        blk[0] = KB.WD_MAGIC_HI << 16 | 1 << 8 | n
        blk[2] = 3                                                             # overflow
        r = KB.WD_HDR_WORDS + 5 * KB.WD_RECW                                   # slot 5: a done 10 s sem wait
        blk[r:r + KB.WD_RECW] = [7 << 8 | 2, 1, 0x22957, 0, 0x1234, 0x40, 10001, 1000, 11000, 0, 0, 0]
        good = struct.pack(f">{KB.KSTA_N + n}I", KB.KSTA_VERSION, *range(KB.KSTA_N - 1), *blk)
        wd = KB.ksta_decode(good)["waitdiag"]
        self.assertEqual((wd["overflow"], wd["version"], len(wd["records"])), (3, 1, 1))
        rec = wd["records"][0]
        self.assertEqual((rec["slot"], rec["seq"], rec["state"], rec["kind_name"], rec["exit"] - rec["entry"]),
                         (5, 7, "done", "sem_wait", 10000))
        with self.assertRaises(KB.ProtocolError):
            KB.waitdiag_decode(blk[:-1])

    def test_ram_state_across_remote_states(self):
        body = mockcard.MockBody(tether=True)
        link = KB.Link(KB.MockBackend(body))
        st = KB.ram_state(link)
        self.assertEqual((st["version"], st["app_state"], st["run_state"], st["remtin_flag"]), (KB.KSTA_VERSION, 9, 9, 0))
        KB.tether_start(link)
        st = KB.ram_state(link)
        self.assertEqual((st["app_state"], st["remtin_flag"], st["monitor_run"]), (2, 1, 1))
        body.ksta_overrides["rec_error"] = 0x300000
        self.assertEqual(link.run(lambda s: s.ram_state())["rec_error"], 0x300000)
        KB.tether_stop(link)
        self.assertEqual(KB.ram_state(link)["app_state"], 9)
        sent = [c.strip() for t in body.transports for c in t.sent]
        self.assertEqual(sent.count("-KSTA"), 4)
        self.assertEqual([v for t in body.transports for v in t.violations], [])

    def test_older_build_answers_501(self):
        body = mockcard.MockBody()
        body.ksta = False
        link = KB.Link(KB.MockBackend(body))
        with self.assertRaises(KB.BridgeError) as ctx:
            link.run(lambda s: s.ram_state())
        self.assertIn("501", ctx.exception.message)


class RamTest(_Served, unittest.TestCase):
    def test_get_ram(self):
        status, _, d = self.jreq("GET", "/ram?off=0x8b0&len=356")
        self.assertEqual(status, 200, d)
        data = base64.b64decode(d["data"])
        self.assertEqual(len(data), 356)
        self.assertEqual(data[:4], b"KOMA")
        self.assertEqual(data[0x900 - 0x8B0:0x904 - 0x8B0], b"RCP6")
        self.assertEqual(d["hex"], data.hex())
        for bad in ("/ram?off=4000&len=200", "/ram?off=x&len=1", "/ram?off=0&len=0"):
            self.assertEqual(self.jreq("GET", bad)[0], 400, bad)

    def test_post_ram_writes_one_byte(self):
        status, _, d = self.jreq("POST", "/ram", {"off": 0x8C2, "val": 7})
        self.assertEqual(status, 200, d)
        self.assertEqual(d["results"][0]["reply"], f"200 -KSET {0x8C2}=7")
        self.assertEqual(self.body.ram[0x8C2], 7)
        self.assertEqual(self.ksets(), [f"-KSET {0x8C2},7"])
        status, _, d = self.jreq("POST", "/ram", {"off": "0x910", "val": 200})  # a record byte: any value
        self.assertEqual(status, 200, d)
        self.transports_clean()

    def test_post_ram_refusals_send_nothing(self):
        cases = [({"off": 0x900, "val": 1}, 403, "out_of_window"), ({"off": 0x8D0, "val": 1}, 403, "out_of_window"),
                 ({"off": 0x5A, "val": 1}, 403, "out_of_window"), ({"off": 0x8E0, "val": 1}, 403, "out_of_window"),
                 ({"off": 0x53, "val": 14}, 422, "bad_value"),    # EV: odd values only
                 ({"off": 0x59, "val": 3}, 422, "bad_value"),     # ISO: no stock value 3
                 ({"off": 0x8C0, "val": 21}, 422, "bad_value"),   # RECIPE enum is 0..20
                 ({"off": 0x8C2, "val": 9}, 422, "bad_value"),    # CONTRAST enum is 0..8
                 ({"off": 0x8CC, "val": 4}, 422, "bad_value"),    # GRAIN 0..3
                 ({"off": 0x8BB, "val": 3}, 422, "bad_value"),    # MENU LAYOUT 0..2
                 ({"off": 0x910, "val": 256}, 422, "bad_value"),
                 ({"off": 0x8C0}, 400, None), ({"off": True, "val": 1}, 400, None)]
        for body, want, code in cases:
            status, _, d = self.jreq("POST", "/ram", body)
            self.assertEqual(status, want, (body, d))
            if code:
                self.assertEqual(d["code"], code, body)
        self.assertEqual(self.ksets(), [])

    def test_ram_window_matches_map_bytes_fields(self):
        # every mapped one-byte field in the window has a value list or range (so the check means something)
        for start, size, f in KB.map_fields():
            if size == 1 and KB.kset_writable(start) and f["type"] in ("enum", "u8"):
                self.assertTrue(f.get("values") or f.get("range"), f["name"])

    def test_batch(self):
        writes = [{"off": 0x8C0, "val": 2}, {"off": 0x8C2, "val": 8}, {"off": 0x91C + 1, "val": 8}, {"off": 0xA01, "val": 0x55}]
        status, _, d = self.jreq("POST", "/ram/batch", writes)
        self.assertEqual(status, 200, d)
        self.assertEqual(d["done"], 4)
        self.assertEqual(self.ksets(), [f"-KSET {w['off']},{w['val']}" for w in writes])  # in order
        self.assertEqual((self.body.ram[0x8C0], self.body.ram[0x8C2], self.body.ram[0x91D], self.body.ram[0xA01]),
                         (2, 8, 8, 0x55))
        # {"writes": [...]} is accepted too
        self.assertEqual(self.jreq("POST", "/ram/batch", {"writes": [{"off": 0x8C0, "val": 1}]})[0], 200)

    def test_batch_refused_whole(self):
        before = len(self.ksets())
        status, _, d = self.jreq("POST", "/ram/batch", [{"off": 0x8C0, "val": 2}, {"off": 0x905, "val": 1}])
        self.assertEqual(status, 403, d)
        status, _, d = self.jreq("POST", "/ram/batch", [{"off": 0x8C0, "val": 2}, {"off": 0x8C3, "val": 99}])
        self.assertEqual(status, 422, d)
        self.assertEqual(self.jreq("POST", "/ram/batch", [])[0], 400)
        self.assertEqual(self.jreq("POST", "/ram/batch", [{"off": 0x910, "val": 1}] * 65)[0], 413)
        self.assertEqual(len(self.ksets()), before)

    def test_batch_stops_at_camera_refusal(self):
        real = self.body.kset
        self.body.kset = lambda a: "451 -KSET value" if a.startswith(f"{0x8C3},") else real(a)
        status, _, d = self.jreq("POST", "/ram/batch", [{"off": 0x8C2, "val": 5}, {"off": 0x8C3, "val": 5},
                                                         {"off": 0x8C4, "val": 5}])
        self.assertEqual(status, 502, d)
        self.assertEqual(d["code"], "camera_refused")
        self.assertEqual(d["done"], 1)
        self.assertEqual(len(d["results"]), 2)
        self.assertEqual(self.ksets(), [f"-KSET {0x8C2},5", f"-KSET {0x8C3},5"])

    def test_stock_bytes_and_busy_409(self):
        self.assertEqual(self.jreq("POST", "/tether/start")[0], 200)
        status, _, d = self.jreq("POST", "/ram", {"off": 0x53, "val": 15})
        self.assertEqual(status, 200, d)
        self.assertEqual(d["results"][0]["reply"], "200 -KSET 83=15")
        self.assertEqual(self.body.ram[0x53], 15)
        for off, val in ((0x59, 6), (0x54, 7), (0x50, 2), (0x30, 3), (0xA6, 16)):
            self.assertEqual(self.jreq("POST", "/ram", {"off": off, "val": val})[0], 200, (off, val))
        sent = len(self.ksets())
        for body, want, code in (({"off": 0x53, "val": 14}, 422, "bad_value"), ({"off": 0x59, "val": 3}, 422, "bad_value"),
                                 ({"off": 0x5A, "val": 0}, 403, "out_of_window"), ({"off": 0x8E0, "val": 0}, 403, "out_of_window")):
            status, _, d = self.jreq("POST", "/ram", body)
            self.assertEqual((status, d.get("code")), (want, code), body)
        self.assertEqual(len(self.ksets()), sent)  # refused by the bridge: nothing sent
        # the camera's menu is open: 451 -KSET busy (retried for the window) -> 409 camera_busy, the byte unchanged
        self.link.kset_busy_wait_s, self.link.kset_busy_poll_s = 0.2, 0.05
        self.body.osd_screen = "menu"
        status, _, d = self.jreq("POST", "/ram", {"off": 0x53, "val": 17})
        self.assertEqual(status, 409, d)
        self.assertEqual(d["code"], "camera_busy")
        self.assertIn("Close the camera's menu and try again", d["error"])
        self.assertEqual(d["reply"], "451 -KSET busy")
        self.assertGreater(d["busy_retries"], 0)
        self.assertGreaterEqual(d["busy_s"], 0.2)
        self.assertEqual(self.body.ram[0x53], 15)
        # a batch stops there too, with how far it got
        status, _, d = self.jreq("POST", "/ram/batch", [{"off": 0x8C2, "val": 5}, {"off": 0x53, "val": 17}])
        self.assertEqual((status, d["code"], d["done"]), (409, "camera_busy", 1))
        self.body.osd_screen = "ready"
        self.assertEqual(self.jreq("POST", "/ram", {"off": 0x53, "val": 17})[0], 200)
        # GET /ram reads the stock range the page opens with
        status, _, d = self.jreq("GET", "/ram?off=0x30&len=43")
        self.assertEqual(status, 200, d)
        self.assertEqual(base64.b64decode(d["data"])[0x53 - 0x30], 17)

    def test_busy_after_ev_change_is_retried(self):
        # hardware (session 7 step 8): right after an EV change the camera answers 451 -KSET busy for a moment
        self.assertEqual(self.jreq("POST", "/tether/start")[0], 200)
        self.link.kset_busy_wait_s, self.link.kset_busy_poll_s = 1.0, 0.05
        self.body.kset_busy_after_s = 0.3
        self.assertEqual(self.jreq("POST", "/ram", {"off": 0x53, "val": 15})[0], 200)
        status, _, d = self.jreq("POST", "/ram", {"off": 0x53, "val": 13})  # busy, then 200 inside the window
        self.assertEqual(status, 200, d)
        self.assertGreater(d["busy_retries"], 0)
        self.assertTrue(0.1 < d["busy_s"] < 1.0, d)
        self.assertEqual(self.body.ram[0x53], 13)
        # busy for longer than the window: 409 camera_busy, the byte unchanged
        self.body.kset_busy_after_s = 2.0
        self.link.kset_busy_wait_s = 0.3
        self.assertEqual(self.jreq("POST", "/ram", {"off": 0x53, "val": 15})[0], 200)
        status, _, d = self.jreq("POST", "/ram", {"off": 0x53, "val": 13})
        self.assertEqual((status, d["code"]), (409, "camera_busy"), d)
        self.assertGreater(d["busy_retries"], 0)
        self.assertEqual(self.body.ram[0x53], 15)

    def test_live_frames_follow_ev(self):
        self.jreq("POST", "/tether/start")
        import time
        self.body.lv_epoch = time.monotonic() + 1e6  # freeze the loop on one frame
        frames = {}
        for ev in (1, 13, 25):  # -2.0, 0.0, +2.0 through the bridge, then the frame the bridge serves
            status, _, d = self.jreq("POST", "/ram", {"off": 0x53, "val": ev})
            self.assertEqual(status, 200, d)
            frames[ev] = self.req("GET", "/live/frame?mode=0")[2]
            self.assertEqual(self.req("GET", "/live/frame?mode=1")[0], 200)
        self.assertEqual(len({frames[1], frames[13], frames[25]}), 3)
        avg = {}
        for ev in (1, 13, 25):  # mode 0 raw Y: darker at -2.0, brighter at +2.0
            self.body.ram[0x53] = ev
            y = self.body.live_frame_bytes(0, 0)
            avg[ev] = sum(y) / len(y)
        self.assertLess(avg[1] + 30, avg[13])
        self.assertLess(avg[13] + 30, avg[25])

    def test_stock_camera_answer(self):
        self.body.kset = lambda a: "500 Syntax error, command unrecognized."
        status, _, d = self.jreq("POST", "/ram", {"off": 0x8C2, "val": 5})
        self.assertEqual(status, 502)
        self.assertEqual(d["code"], "not_supported")

    def test_live_frames_follow_active_contrast(self):
        self.jreq("POST", "/tether/start")
        import time
        self.body.lv_epoch = time.monotonic() + 1e6  # freeze the loop on one frame
        for off, val in ((0x8C0, 1),):
            self.jreq("POST", "/ram", {"off": off, "val": val})
        flat = self.req("GET", "/live/frame?mode=0")[2]
        rec = 0x910  # MONO's record: CONTRAST p1 -> +4, then fix the xor
        rb = bytearray(self.body.ram[rec:rec + 12])
        rb[1] = 8
        rb[11] = mockcard.record_bytes(rb[:10])[11]
        status, _, d = self.jreq("POST", "/ram/batch", [{"off": rec + 1, "val": 8}, {"off": rec + 11, "val": rb[11]}])
        self.assertEqual(status, 200, d)
        hard = self.req("GET", "/live/frame?mode=0")[2]
        self.assertNotEqual(flat, hard)
        self.assertEqual(mockcard.active_tone(self.body.ram), (8, 4))

    def test_page_and_map_served(self):
        status, headers, page = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn(b"<title>Koma Setup</title>", page)
        status, headers, data = self.req("GET", "/settings-map.json")
        self.assertEqual(status, 200)
        self.assertIn("fields", json.loads(data))
        self.assertIn("POST /ram/batch", self.jreq("GET", "/api")[2]["endpoints"])

    def test_static_files_and_traversal(self):
        for path, ctype in STATIC_CASES:
            status, headers, data = self.req("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(headers["Content-Type"].startswith(ctype), (path, headers["Content-Type"]))
            self.assertTrue(data, path)
        self.assertIn("Koma Setup", json.loads(self.req("GET", "/manifest.webmanifest")[2])["name"])
        for path in ("/build/", "/build/koma04.grmod.json", "/build/../README.md", "/build/%2e%2e/README.md",
                     "/build/koma04.grmod/../SHA256SUMS"):
            self.assertIn(self.req("GET", path)[0], (403, 404), path)
        self.assertEqual(self.req("GET", "/docs/install.md")[0], 404)
        self.assertEqual(self.req("POST", "/sw.js")[0], 404)

    def test_module_routes(self):
        """Both Koma packages and the sums are exact static routes: koma04 (Koma 1) and koma04w (Koma 2)."""
        routes = KB.STATIC_ROUTES
        for name in ("koma04.grmod", "koma04w.grmod", "SHA256SUMS"):
            self.assertEqual(routes["/build/" + name][0], KB.REPO_ROOT / "build" / name)

    transports_clean = BridgeTest.transports_clean


STATIC_CASES = [
    ("/", "text/html"),
    ("/manifest.webmanifest", "application/manifest+json"),
    ("/sw.js", "application/javascript"),
]


class LanTest(_Served, unittest.TestCase):
    token = "test-token-0123456789"

    def test_token_refused_without(self):
        for path in ("/status", "/settings", "/ram?off=2240&len=1", "/live/status", "/build/",
                     "/build/../README.md", "/build/koma04.grmod.json"):
            status, _, d = self.jreq("GET", path)
            self.assertEqual(status, 403, path)
            self.assertEqual(d["code"], "token")
        status, _, _ = self.req("GET", "/status?token=wrong")
        self.assertEqual(status, 403)
        status, _, _ = self.req("GET", "/status", headers={"X-Koma-Token": "wrong"})
        self.assertEqual(status, 403)
        status, _, _ = self.req("POST", "/tether/stop")
        self.assertEqual(status, 403)
        self.assertEqual([c for t in self.body.transports for c in t.sent], [])  # nothing reached the camera

    def test_static_shell_without_token(self):
        for path, ctype in STATIC_CASES:
            status, headers, data = self.req("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(headers["Content-Type"].startswith(ctype), (path, headers["Content-Type"]))
            self.assertTrue(data, path)
        self.assertEqual([c for t in self.body.transports for c in t.sent], [])
        for method, path in (("POST", "/"), ("POST", "/sw.js"), ("GET", "/api"), ("GET", "/settings-map.json")):
            self.assertEqual(self.req(method, path)[0], 403, (method, path))

    def test_token_accepted(self):
        status, headers, page = self.req("GET", f"/?token={self.token}")
        self.assertEqual(status, 200)
        self.assertIn(b"Koma Setup", page)
        # any Host, any Origin that presents the token (a phone on the LAN)
        status, headers, d = self.jreq("GET", "/status", headers={"X-Koma-Token": self.token,
                                                                 "Origin": "http://192.168.1.23:8765"},
                                       host="192.168.1.23:8765")
        self.assertEqual(status, 200, d)
        self.assertTrue(d["connected"])
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "http://192.168.1.23:8765")
        status, _, d = self.jreq("POST", f"/ram?token={self.token}", {"off": 0x8C2, "val": 6})
        self.assertEqual(status, 200, d)

    def test_preflight_without_token(self):
        status, headers, _ = self.req("OPTIONS", "/ram", headers={
            "Origin": "http://192.168.1.23:8765", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, x-koma-token"})
        self.assertEqual(status, 204)
        self.assertIn("X-Koma-Token", headers.get("Access-Control-Allow-Headers", ""))

    def test_lan_needs_token_to_bind_everywhere(self):
        with self.assertRaises(SystemExit):
            KB.make_server(self.link, host="0.0.0.0", port=0)

    def test_qr_of_the_url(self):
        import qrmini
        url = f"http://192.168.1.23:8765/?token={self.token}"
        m = qrmini.matrix(url)
        self.assertEqual(len(m), 33)  # version 4
        self.assertIn("▀", qrmini.terminal(url))
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV not installed: QR decode not checked")
        a = np.pad(np.array([[0 if v else 255 for v in r] for r in m], dtype=np.uint8), 4, constant_values=255)
        got, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.resize(a, None, fx=8, fy=8,
                                                                     interpolation=cv2.INTER_NEAREST))
        self.assertEqual(got, url)


class KeepaliveTest(unittest.TestCase):
    def test_keepalive_after_idle_in_remote_control_only(self):
        clock = FakeClock()
        body = mockcard.MockBody(clock=clock)
        link = KB.Link(KB.MockBackend(body))
        link.clock = clock
        logged = []
        link.log = logged.append
        link.keepalive_lock_wait_s = 0.0
        sent = lambda: [c.strip() for t in body.transports for c in t.sent if c.startswith("-CCTL")]  # noqa: E731
        clock.t += 100
        self.assertFalse(link.keepalive_tick())            # remote control off: nothing
        KB.tether_start(link)
        n = len(sent())
        clock.t += 19
        self.assertFalse(link.keepalive_tick())            # not idle long enough
        clock.t += 2
        with link.lock:                                    # a stream pull or shoot holds the camera: skipped
            self.assertFalse(link.keepalive_tick())
        link.run(lambda s: s.pwd())                        # other traffic does not renew APO: still due
        link.hold_live()
        self.assertFalse(link.keepalive_tick())            # a shoot / key press is waiting for the lock: skipped
        link.release_live()
        self.assertTrue(link.keepalive_tick())
        self.assertEqual(sent()[n:], ["-CCTL keyset,0,27"])
        self.assertEqual(body.key_log[-1], (0, 27))
        self.assertIn("keyset,0,27", logged[-1])
        self.assertFalse(link.keepalive_tick())            # it counts as traffic: the next one is 20 s later
        clock.t += 21
        self.assertTrue(link.keepalive_tick())
        KB.tether_stop(link)
        clock.t += 21
        self.assertFalse(link.keepalive_tick())            # off again after /tether/stop
        self.assertEqual(link.keepalives, 2)
        for t in body.transports:
            self.assertEqual(t.violations, [])


class DefaultsTest(unittest.TestCase):
    def test_timing_defaults(self):
        link = KB.Link(KB.MockBackend(mockcard.MockBody()))
        self.assertEqual(link.keepalive_idle_s, 20.0)
        self.assertEqual(link.shot_timeout_s, 30.0)
        self.assertEqual(link.lvfr_wait_s, 30.0)
        self.assertEqual((link.lvfr_rearm_s, link.lvfr_rearms), (5.0, 5))
        self.assertTrue(0.25 <= link.lvfr_poll_s <= 0.5)


def fake_time_link(body):
    """A Link on a FakeClock whose sleep advances that clock (no real waiting)."""
    clock = FakeClock()
    link = KB.Link(KB.MockBackend(body))
    link.clock = clock
    link.sleep = lambda s: setattr(clock, "t", clock.t + s)
    link.af_wait_s = link.press_s = link.first_poll_s = 0.0
    link.stable_poll_s = 0.02
    link.ksta_poll_s = link.settle_unseen_s = link.settle_delay_s = link.list_retry_s = 0.02
    return link, clock


class LvfrWaitTest(unittest.TestCase):
    def cmds(self, body):
        return [c.strip() for t in body.transports for c in t.sent]

    def test_waits_for_150_before_any_key(self):
        body = mockcard.MockBody(tether=True)
        body.lvfr_451_polls = 3
        link, clock = fake_time_link(body)
        t0 = clock.t
        r = KB.tether_start(link)
        self.assertTrue(r["ok"])
        wait = r["replies"][-1]
        self.assertEqual(wait["command"], "-LVFR 0 (wait)")
        self.assertTrue(wait["reply"].startswith("150 -LVFR after"), wait)
        cmds = [c for c in self.cmds(body) if c.startswith(("-CCTL", "-LVFR"))]
        self.assertEqual(cmds, ["-CCTL remtin", "-CCTL diaset,4"] + ["-LVFR 0,0"] * 4)  # 3 x 451, then 150
        self.assertAlmostEqual(clock.t - t0, 3 * link.lvfr_poll_s)
        self.assertAlmostEqual(link.lvfr_150_s, 3 * link.lvfr_poll_s)
        self.assertFalse(any("keyset" in c for c in cmds))

    def test_timeout_proceeds_and_is_recorded(self):
        body = mockcard.MockBody(tether=True)
        body.lvfr_451_polls = 10 ** 9
        link, clock = fake_time_link(body)
        logged = []
        link.log = logged.append
        t0 = clock.t
        r = KB.tether_start(link)
        self.assertTrue(r["ok"])           # not a hard failure
        self.assertTrue(link.tether["active"])
        self.assertIn("no 150 after", r["replies"][-1]["reply"])
        self.assertIn("451", r["replies"][-1]["reply"])
        self.assertGreaterEqual(clock.t - t0, link.lvfr_wait_s)
        self.assertLess(clock.t - t0, link.lvfr_wait_s + 2 * link.lvfr_poll_s)
        self.assertTrue(any("never answered 150" in x for x in logged))
        self.assertIsNone(link.lvfr_150_s)
        self.assertFalse(any("keyset" in c for c in self.cmds(body)))

    def test_shorter_timeout_is_respected(self):
        body = mockcard.MockBody(tether=True)
        body.lvfr_451_polls = 10 ** 9
        link, clock = fake_time_link(body)
        link.lvfr_wait_s = 1.0
        t0 = clock.t
        KB.tether_start(link)
        self.assertLess(clock.t - t0, 1.0 + 2 * link.lvfr_poll_s)

    def test_non_451_answer_ends_the_wait_at_once(self):
        body = mockcard.MockBody(tether=True)
        link, clock = fake_time_link(body)
        link.lvfr_wait_s = 6.0
        orig = KB.BridgeSession.live_frame
        KB.BridgeSession.live_frame = lambda self, mode, seq: ("501 -LVFR unsupported", None)
        self.addCleanup(setattr, KB.BridgeSession, "live_frame", orig)
        t0 = clock.t
        r = KB.tether_start(link)
        self.assertTrue(r["ok"])
        self.assertEqual(clock.t, t0)


class LvfrRearmTest(unittest.TestCase):
    """Hardware 2026-11-07 (sessions 8/9): remtin + diaset,4 can answer 200 and not take. The -LVFR wait re-arms
    (remtin + diaset,4) after lvfr_rearm_s without a 150, logs a -KSTA snapshot each time, and no shutter key is
    pressed unless a 150 was seen."""

    def cmds(self, body):
        return [c.strip() for t in body.transports for c in t.sent]

    def test_one_dud_then_rearm_gets_150(self):
        body = mockcard.MockBody(tether=True)
        body.diaset_duds = 1
        link, clock = fake_time_link(body)
        t0 = clock.t
        r = KB.tether_start(link)
        self.assertTrue(r["ok"] and r["armed"], r)
        cmds = [c for c in self.cmds(body) if c.startswith(("-CCTL", "-KSTA"))]
        self.assertEqual(cmds, ["-CCTL remtin", "-CCTL diaset,4", "-KSTA", "-CCTL remtin", "-CCTL diaset,4"])
        ksta = [x for x in r["replies"] if x["command"].startswith("-KSTA (re-arm")]
        self.assertEqual(len(ksta), 1)
        for word in KB.KSTA_SNAPSHOT:
            self.assertIn(f"{word}=", ksta[0]["reply"])
        self.assertIn("(1 re-arm)", r["replies"][-1]["reply"])
        self.assertGreaterEqual(clock.t - t0, link.lvfr_rearm_s)
        self.assertLess(link.lvfr_150_s, link.lvfr_wait_s)
        self.assertEqual(link.lvfr_rearms_used, 1)

    def test_never_armed_rearms_three_times_and_no_key_is_pressed(self):
        body = mockcard.MockBody(tether=True)
        body.diaset_duds = 10 ** 6
        link, clock = fake_time_link(body)
        link.log = lambda m: None
        t0 = clock.t
        r = KB.tether_start(link)
        self.assertTrue(r["ok"])
        self.assertFalse(r["armed"])
        self.assertIn("Remote mode never armed", r["message"])
        self.assertLess(clock.t - t0, link.lvfr_wait_s + 2 * link.lvfr_poll_s)
        cmds = self.cmds(body)
        self.assertEqual(cmds.count("-CCTL diaset,4"), 1 + link.lvfr_rearms)
        self.assertEqual(sum(1 for x in r["replies"] if x["command"].startswith("-KSTA (re-arm")), link.lvfr_rearms)
        self.assertTrue(any(x["command"] == "-KSTA (no 150)" for x in r["replies"]))
        shot = KB.tether_shoot(link, False)       # re-arms once more, still no 150: refuses before any key
        self.assertFalse(shot["ok"])
        self.assertEqual(shot["outcome"], "not_armed")
        self.assertIn("Remote mode never armed", shot["message"])
        self.assertNotIn("tether feature", shot["message"])
        self.assertFalse(KB.tether_focus(link)["ok"])
        self.assertFalse(any(c.startswith("-CCTL keyset") for c in self.cmds(body)))
        self.assertEqual(body.key_log, [])

    def test_not_armed_start_then_shoot_rearms_and_shoots(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.0)
        link, clock = fake_time_link(body)
        body.diaset_duds = 1 + link.lvfr_rearms   # the start and all its re-arms miss
        link.log = lambda m: None
        self.assertFalse(KB.tether_start(link)["armed"])
        shot = KB.tether_shoot(link, False)       # the shoot's re-arm takes
        self.assertTrue(link.tether["armed"])
        self.assertTrue(any(c == "-CCTL keyset,1,27" for c in self.cmds(body)))
        self.assertNotEqual(shot["outcome"], "not_armed")

    def test_no_lvfr_verb_does_not_block_the_shutter(self):
        body = mockcard.MockBody(tether=True)
        link, clock = fake_time_link(body)
        orig = KB.BridgeSession.live_frame
        KB.BridgeSession.live_frame = lambda self, mode, seq: ("501 -LVFR unsupported", None)
        self.addCleanup(setattr, KB.BridgeSession, "live_frame", orig)
        r = KB.tether_start(link)
        self.assertTrue(r["armed"])               # nothing to check arming with (no liveview feature)


class VerSettleTest(unittest.TestCase):
    """Remote control starts no sooner than ver_settle_s after the connection's -VER (its 3 s timer posts 0xa201)."""

    def cmds(self, body):
        return [c.strip() for t in body.transports for c in t.sent]

    def test_default_is_off_for_every_backend(self):
        """Tether 1.6 swallows 0xa201 in record state 2, and the old wait let it retract the lens in state 9 instead
        (hardware 2026-11-07): remtin follows connect at once, real camera included."""
        self.assertEqual(KB.Link(KB.MockBackend(mockcard.MockBody())).ver_settle_s, 0.0)
        real = type("RealLike", (), {"simulated": False, "name": "x"})()
        self.assertEqual(KB.Link(real).ver_settle_s, 0.0)

    def test_default_start_sends_remtin_right_after_ver(self):
        body = mockcard.MockBody(tether=True)
        link, clock = fake_time_link(body)
        self.assertEqual(link.ver_settle_s, 0.0)
        stamps = []
        orig = KB._cctl
        def spy(s, argument, log):
            stamps.append((argument, clock.t))
            return orig(s, argument, log)
        KB._cctl = spy
        self.addCleanup(setattr, KB, "_cctl", orig)
        r = KB.tether_start(link)
        self.assertTrue(r["ok"])
        self.assertEqual(stamps[0], ("remtin", link.ver_at))
        self.assertFalse(any(x["command"] == "(wait after -VER)" for x in r["replies"]))

    def test_start_right_after_connect_waits_out_the_rest(self):
        body = mockcard.MockBody(tether=True)
        link, clock = fake_time_link(body)
        link.ver_settle_s = 5.0
        stamps = []
        orig = KB._cctl
        def spy(s, argument, log):
            stamps.append((argument, clock.t))
            return orig(s, argument, log)
        KB._cctl = spy
        self.addCleanup(setattr, KB, "_cctl", orig)
        t_ver = clock.t
        r = KB.tether_start(link)
        self.assertTrue(r["ok"])
        self.assertEqual(link.ver_at, t_ver)
        self.assertEqual(stamps[0][0], "remtin")
        self.assertAlmostEqual(stamps[0][1] - t_ver, 5.0)
        self.assertEqual(r["replies"][0]["command"], "(wait after -VER)")
        cmds = [c for c in self.cmds(body) if c.startswith(("-VER", "-CCTL"))]
        self.assertEqual(cmds[:2], ["-VER", "-CCTL remtin"])

    def test_partial_wait_and_no_wait_after_settle(self):
        body = mockcard.MockBody(tether=True)
        link, clock = fake_time_link(body)
        link.ver_settle_s = 5.0
        link.status()                       # connects: -VER at t
        t_ver = link.ver_at
        clock.t += 3.0
        log = []
        self.assertAlmostEqual(KB.settle_after_ver(link, log), 2.0)
        self.assertAlmostEqual(clock.t - t_ver, 5.0)
        self.assertEqual(KB.settle_after_ver(link, []), 0.0)   # already past: no wait, no log entry
        clock.t += 10.0
        r = KB.tether_start(link)
        self.assertTrue(r["ok"])
        self.assertNotEqual(r["replies"][0]["command"], "(wait after -VER)")

    def test_reconnect_resets_the_stamp(self):
        body = mockcard.MockBody(tether=True)
        link, clock = fake_time_link(body)
        link.ver_settle_s = 5.0
        link.status()
        self.assertIsNotNone(link.ver_at)
        link._drop("test")
        self.assertIsNone(link.ver_at)
        self.assertEqual(KB.settle_after_ver(link, []), 0.0)


class InfoShotTest(unittest.TestCase):
    def test_predict_next(self):
        p = KB.predict_next
        self.assertEqual(p({("100RICOH", "R0015700.JPG")}), ("100RICOH", "R0015701.JPG"))
        self.assertEqual(p({("100RICOH", "RIMG0003.JPG"), ("100RICOH", "RIMG0002.JPG")}), ("100RICOH", "RIMG0004.JPG"))
        self.assertEqual(p({("100RICOH", "RIMG0009.JPG")}), ("100RICOH", "RIMG0010.JPG"))
        self.assertEqual(p({("100RICOH", "RIMG9999.JPG")}), None)  # rollover is not predicted, and does not crash
        self.assertEqual(p({("100RICOH", "PARAM.CUR"), ("100RICOH", "X.JPG")}), None)
        self.assertEqual(p(set()), None)
        self.assertEqual(p({("100RICOH", "RIMG0500.JPG"), ("101RICOH", "RIMG0001.JPG")}), ("101RICOH", "RIMG0002.JPG"))

    def test_missing_file_is_450_and_not_a_crash(self):
        body = mockcard.MockBody()
        link, _ = fake_time_link(body)
        self.assertIsNone(link.run(lambda s: KB.info_size(s, "100RICOH", "RIMG0099.JPG")))
        self.assertTrue(any("450" in x for x in link.run(lambda s: [s._ask(KB.read_command("-INFO", "/EXT0/DCIM/100RICOH/RIMG0099.JPG"))])))
        self.assertGreater(link.run(lambda s: KB.info_size(s, "100RICOH", "RIMG0001.JPG")), 0)

        def boom(self, path):
            raise KB.ProtocolError("garbled")
        orig = KB.BridgeSession.info
        KB.BridgeSession.info = boom
        self.addCleanup(setattr, KB.BridgeSession, "info", orig)
        self.assertIsNone(link.run(lambda s: KB.info_size(s, "100RICOH", "RIMG0001.JPG")))

    def test_shot_found_by_info_while_nlst_lags(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.05, grow_s=0.0)
        body.nlst_lag_s = 1000.0   # -NLST never shows it within this session
        link, _ = fake_time_link(body)
        link.clock, link.sleep = time.monotonic, time.sleep
        link.first_poll_s = link.poll_s = 0.02
        link.ksta_poll_s = link.settle_unseen_s = link.settle_delay_s = link.list_retry_s = 0.02
        link.shot_timeout_s = 3.0
        KB.tether_start(link)
        r = KB.tether_shoot(link, True)
        self.assertEqual(r["outcome"], "shot", r)
        self.assertEqual(r["photo"]["path"], "100RICOH/RIMG0004.JPG")
        self.assertEqual(base64.b64decode(r["jpeg"]), body.card["/EXT0/DCIM/100RICOH/RIMG0004.JPG"])
        self.assertNotIn("RIMG0004.JPG", [bytes(e)[:8].decode("latin1") for e in body.folder_entries] )
        self.assertEqual(body.lagged and body.lagged[0][1], "RIMG0004.JPG")

    def test_no_shot_polls_info_until_30_s(self):
        body = mockcard.MockBody(tether=False)   # stock: nothing fires
        link, clock = fake_time_link(body)
        link.poll_s = 0.5
        t0 = clock.t
        KB.tether_start(link)
        t1 = clock.t
        r = KB.tether_shoot(link, False)
        self.assertEqual(r["outcome"], "no_file")
        self.assertGreaterEqual(clock.t - t1, 30.0)
        self.assertLess(clock.t - t1, 32.0)
        infos = [c.strip() for t in body.transports for c in t.sent if c.startswith("-INFO")]
        self.assertGreater(len(infos), 30)
        self.assertEqual(set(infos), {"-INFO /EXT0/DCIM/100RICOH/RIMG0004.JPG"})


class KeepaliveStreamTest(unittest.TestCase):
    def test_keepalive_fires_during_a_long_stream(self):
        # a stream sends -LVFR all the time; the keepalive keys on the last -CCTL keyset, so it still fires between pulls
        for idle, expect in ((1.0, 70), (20.0, 5)):
            clock = FakeClock()
            body = mockcard.MockBody(tether=True, clock=clock)
            link = KB.Link(KB.MockBackend(body))
            link.clock = clock
            link.keepalive_lock_wait_s = 0.0
            link.keepalive_idle_s = idle
            KB.tether_start(link)
            sent = lambda: [c.strip() for t in body.transports for c in t.sent]  # noqa: E731
            n = len(sent())
            for _ in range(130):                           # 130 s of simulated time, one pull a second
                KB.live_pull(link, 0)
                clock.t += 1
                link.keepalive_tick()
            cmds = sent()[n:]
            self.assertGreaterEqual(link.keepalives, expect - 1 if idle == 1.0 else expect)
            self.assertEqual(sum(c == "-CCTL keyset,0,27" for c in cmds), link.keepalives)
            self.assertTrue(all(c.startswith("-LVFR") or c.startswith("-CCTL keyset,0,27") for c in cmds))
            for t in body.transports:
                self.assertEqual(t.violations, [])


class CliTest(unittest.TestCase):
    def test_needs_explicit_mode(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(KB.main([]), 2)
        self.assertIn("--real", err.getvalue())

    def test_real_backend_not_opened_by_tests(self):
        # The real path exists but is reached only through --real; constructing it opens nothing.
        backend = KB.RealBackend()
        self.assertFalse(backend.simulated)
        self.assertIn("talks to a REAL camera", KB.REAL_WARNING)
        self.assertIn("Power the camera on BEFORE", KB.REAL_WARNING)


class ShotSettleTest(unittest.TestCase):
    """Shot settle (HW 2026-11-08): no card command until -KSTA shows cam_state 2 and monitor_run 1."""

    def make(self):
        self.body = mockcard.MockBody(tether=True, shot_delay_s=0.3, grow_s=0.2)
        self.link = l = KB.Link(KB.MockBackend(self.body))
        l.af_wait_s = l.press_s = l.first_poll_s = 0.0
        l.poll_s, l.shot_timeout_s = 0.02, 10.0
        l.ksta_poll_s, l.settle_unseen_s, l.settle_delay_s, l.list_retry_s = 0.02, 0.5, 0.05, 0.05
        self.logs = []
        l.log = self.logs.append
        self.assertTrue(KB.tether_start(l)["ok"])
        return l

    def test_settle_waits_for_cam_state_2(self):
        out = KB.tether_shoot(self.make(), False)
        self.assertEqual(out["outcome"], "shot")
        states = [c for _, c in self.body.ksta_log]
        self.assertIn(9, states)  # the poll saw the camera busy, then settled
        self.assertEqual(states[-1], 2)
        self.assertTrue(any("shot settled after" in x for x in self.logs))

    def test_no_listing_before_settle(self):
        l = self.make()
        calls, real = [], KB.dcim_jpegs
        KB.dcim_jpegs = lambda s: (calls.append((l.clock(), self.body.ksta_log[-1][1] if self.body.ksta_log else None)),
                                   real(s))[1]
        self.addCleanup(setattr, KB, "dcim_jpegs", real)
        t0 = l.clock()
        self.assertEqual(KB.tether_shoot(l, False)["outcome"], "shot")
        # calls[0] is the pre-press listing; the next one comes after -KSTA said cam_state 2 and not before the shot ended
        self.assertEqual(calls[1][1], 2)
        self.assertGreaterEqual(calls[1][0] - t0, self.body.shot_delay_s + self.body.grow_s)


class PhotosWhileRemoteTest(unittest.TestCase):
    def test_photos_409_while_tether_on(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.05)
        link = KB.Link(KB.MockBackend(body))
        link.af_wait_s = link.press_s = 0.0
        server = KB.make_server(link, port=0)
        server.quiet = True
        port = server.server_address[1]
        server.RequestHandlerClass = KB.make_handler(link, port)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertTrue(KB.tether_start(link)["ok"])
        for path in ("/photos", "/photos/100RICOH/RIMG0001.JPG", "/photos/100RICOH/RIMG0001.JPG?thumb=1"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10)
            self.assertEqual(cm.exception.code, 409)
            self.assertIn("Leave Remote before reading photos", json.loads(cm.exception.read())["error"])


if __name__ == "__main__":
    unittest.main()
