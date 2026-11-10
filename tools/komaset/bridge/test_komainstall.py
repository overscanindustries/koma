#!/usr/bin/env python3
"""Tests for komainstall.py (the Koma app's setup glue), against the SIMULATED camera. No USB device is opened and no
camera write is added: the card files are served to the browser, which writes the card on the computer.

Run: python3 -m pytest tools/komaset/bridge/test_komainstall.py
"""
import hashlib
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import komabridge as KB  # noqa: E402
import komainstall as KI  # noqa: E402
import mockcard  # noqa: E402
import selftest  # noqa: E402

STOCK = KI.REPO / "firmware" / "extracted" / "GR DIGITAL Update" / "Firmware rel 2.40" / "qrflect3"
HAVE_STOCK = STOCK.is_file() and hashlib.sha256(STOCK.read_bytes()).hexdigest() == KI._loader().STOCK_SHA256


class Server(unittest.TestCase):
    def setUp(self):
        KI.forget_upload()
        self.body = mockcard.MockBody(tether=True, koma=True)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.server = KB.make_server(self.link, port=0)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._env = os.environ.pop("KOMA_FIRMWARE", None)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        KI.forget_upload()
        if self._env is not None:
            os.environ["KOMA_FIRMWARE"] = self._env

    def req(self, method, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body, method=method)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def jreq(self, method, path, body=None):
        status, data = self.req(method, path, body)
        return status, json.loads(data)

    def test_komalog_absent_then_present(self):
        status, d = self.jreq("GET", "/komalog")
        self.assertEqual((status, d["present"], d["build_id"]), (200, False, None))
        self.body.card[KI.KOMALOG] = selftest.simulated_komalog(build=0x5429091F)
        status, d = self.jreq("GET", "/komalog")
        self.assertEqual(status, 200)
        self.assertEqual((d["present"], d["build_id"], d["stage"], d["header_ok"]), (True, "5429091F", 3, True))
        for t in self.body.transports:  # a read only: no write command reached the simulated camera
            self.assertEqual(t.violations, [])
            self.assertFalse(any(c.startswith(("-STOR", "DELE", "-CCTL")) for c in t.sent if isinstance(c, str)))

    def test_komalog_without_camera(self):
        self.body.unplug()
        status, d = self.jreq("GET", "/komalog")
        self.assertGreaterEqual(status, 500)

    def test_install_info_lists_the_release_package(self):
        status, d = self.jreq("GET", "/install")
        self.assertEqual(status, 200)
        p = d["package"]
        self.assertEqual((p["route"], p["name"]), ("/install/GRMOD.BIN", "koma-1.0.0.grmod"))
        sums = dict(reversed(l.split()) for l in (KI.REPO / "dist-release" / "SHA256SUMS").read_text().splitlines())
        self.assertEqual(p["published_sha256"], sums["koma-1.0.0.grmod"])   # the recorded build
        self.assertEqual(hashlib.sha256(KI.PACKAGE_DELTA.read_bytes()).hexdigest(), KI.PACKAGE_DELTA_SHA256)
        self.assertEqual(KI.PACKAGE_DELTA_SHA256, sums["koma-1.0.0.grmod.delta"])
        self.assertEqual(p["supersedes"], ["747F061F", "896AEBBC", "4113C2A7", "D4208A4F", "64CE9496", "898B5C3D"])
        self.assertEqual((p["version"], p["min_firmware_gen"], p["build_id"], p["bytes"]),
                         ("1.0.0", 1, "094F6569", 61784))
        self.assertEqual(p["ready"], HAVE_STOCK)
        self.assertEqual(d["firmware"]["koma_tag"], "240K1.0")

    @unittest.skipUnless(HAVE_STOCK, "Ricoh's 2.40 qrflect3 is not in this checkout")
    def test_package_is_rebuilt_from_the_delta_and_stock(self):
        status, d = self.jreq("GET", "/install")
        p = d["package"]
        status, data = self.req("GET", p["route"])
        self.assertEqual(status, 200)
        self.assertEqual((hashlib.sha256(data).hexdigest(), len(data)), (p["published_sha256"], p["bytes"]))
        self.assertIn(b"KOMA V1.0.0 094F6569 G1", data)         # docs/versioning.md manifest string
        length = int.from_bytes(data[8:12], "big")             # the installer's loader rules: format 1, flags 0,
        self.assertEqual((data[:4], data[4:6], data[6:8]), (b"GRMD", b"\0\1", b"\0\0"))   # 0 < L <= 0x10000, L % 4 == 0
        self.assertTrue(0 < length <= 0x10000 and length % 4 == 0 and len(data) == 20 + length)

    def test_package_needs_the_stock_file(self):
        orig = KI.REPO
        KI.REPO = HERE / "nowhere"   # no research checkout copy of the stock file
        try:
            status, d = self.jreq("GET", "/install")
            self.assertFalse(d["package"]["ready"])
            status, d = self.jreq("GET", "/install/GRMOD.BIN")
            self.assertEqual((status, d["code"]), (409, "no_stock"))
        finally:
            KI.REPO = orig

    @unittest.skipUnless(HAVE_STOCK, "Ricoh's 2.40 qrflect3 is not in this checkout")
    def test_package_refused_unless_it_is_the_recorded_build(self):
        for name in ("PACKAGE_SHA256", "PACKAGE_DELTA_SHA256"):
            orig = getattr(KI, name)
            setattr(KI, name, "0" * 64)
            try:
                status, d = self.jreq("GET", "/install/GRMOD.BIN")
                self.assertEqual((status, d["code"]), (409, "package_changed"))
            finally:
                setattr(KI, name, orig)

    def test_installer_is_served_for_the_internal_memory_route(self):
        status, d = self.jreq("GET", "/install")
        i = d["installer"]
        self.assertEqual((i["route"], i["name"], i["bytes"]), ("/install/KOMAINST.BIN", "irodump.grmod", 2864))
        self.assertEqual(i["sha256"], i["published_sha256"])
        status, data = self.req("GET", i["route"])
        self.assertEqual(status, 200)
        self.assertEqual(hashlib.sha256(data).hexdigest(), KI.INSTALLER_SHA256)
        self.assertEqual(data[:4], b"GRMD")
        self.assertTrue(KI.PACKAGE_BYTES - 20 <= 0x10000)        # the package body fits the installer's loader rules
        orig = KI.INSTALLER_SHA256
        KI.INSTALLER_SHA256 = "0" * 64
        try:
            status, d = self.jreq("GET", "/install/KOMAINST.BIN")
            self.assertEqual((status, d["code"]), (409, "installer_changed"))
        finally:
            KI.INSTALLER_SHA256 = orig

    def test_upload_with_a_negative_length_is_refused(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("POST", "/install/stock")
        c.putheader("Content-Length", "-1")
        c.endheaders()
        r = c.getresponse()
        self.assertEqual(r.status, 400)
        c.close()

    def test_firmware_image_needs_the_stock_file(self):
        os.environ["KOMA_FIRMWARE"] = str(HERE / "does-not-exist")
        orig = KI.REPO
        KI.REPO = HERE / "nowhere"   # no research checkout copy either
        try:
            status, d = self.jreq("GET", "/install")
            self.assertFalse(d["firmware"]["stock"])
            status, d = self.jreq("GET", "/install/QRFLECT3?image=koma")
            self.assertEqual((status, d["code"]), (409, "no_stock"))
        finally:
            KI.REPO = orig
            os.environ.pop("KOMA_FIRMWARE", None)
        status, d = self.jreq("GET", "/install/QRFLECT3?image=other")
        self.assertEqual(status, 400)

    def test_upload_refuses_a_file_that_is_not_ricohs(self):
        status, d = self.jreq("POST", "/install/stock", b"not a firmware file" * 10)
        self.assertEqual((status, d["code"]), (422, "not_stock"))
        status, d = self.jreq("POST", "/install/stock", b"")
        self.assertEqual(status, 400)

    @unittest.skipUnless(HAVE_STOCK, "Ricoh's 2.40 qrflect3 is not in this checkout")
    def test_koma_and_official_images(self):
        status, d = self.jreq("GET", "/install")
        self.assertTrue(d["firmware"]["stock"])
        status, koma = self.req("GET", "/install/QRFLECT3?image=koma")
        self.assertEqual(status, 200)
        self.assertEqual(hashlib.sha256(koma).hexdigest(), d["firmware"]["koma_sha256"])  # the pinned Koma 1.0 image
        self.assertEqual(d["firmware"]["koma_sha256"][:8], "ab52104c")
        self.assertEqual(koma[0x200 + 0x112578 - 0x1C000:][:9], b"240K1.0\0\0")
        status, official = self.req("GET", "/install/QRFLECT3?image=official")
        self.assertEqual(hashlib.sha256(official).hexdigest(), d["firmware"]["official_sha256"])

    @unittest.skipUnless(HAVE_STOCK, "Ricoh's 2.40 qrflect3 is not in this checkout")
    def test_upload_of_the_stock_file(self):
        status, d = self.jreq("POST", "/install/stock", STOCK.read_bytes())
        self.assertEqual(status, 200)
        self.assertEqual(d["sha256"], KI._loader().STOCK_SHA256)
        status, d = self.jreq("GET", "/install")
        self.assertEqual(d["firmware"]["source"], "upload")

    def test_no_new_write_in_the_allowlist(self):
        # the setup flow adds no USB write: the allowlist still holds only PARAM.DAT, poff and the tether keys
        verbs = {v for v, _ in KB.WRITE_ALLOWLIST}
        self.assertEqual(verbs, {"-STOR", "-CCTL"})
        self.assertEqual([a for v, a in KB.WRITE_ALLOWLIST if v == "-STOR"], [KB.PARAM_DAT])

    def test_app_never_shadows_the_new_roots(self):
        self.assertIsNone(KB.app_file("/install"))
        self.assertIsNone(KB.app_file("/install/GRMOD.BIN"))
        self.assertIsNone(KB.app_file("/komalog"))


if __name__ == "__main__":
    unittest.main()
