#!/usr/bin/env python3
"""Tests for phoneaccess (Settings > Use on your phone), against the SIMULATED camera. The network listener binds
127.0.0.1 on a second port here (phoneaccess.configure), so no test listens on this computer's network address; the
token checks, pairing and revoke are the same code either way. Never tried with a real phone.

Run: python3 -m pytest -q tools/komaset/bridge/test_phoneaccess.py
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import komabridge as KB  # noqa: E402
import mockcard  # noqa: E402
import phoneaccess as P  # noqa: E402


class _Served:
    def setUp(self):
        self.body = mockcard.MockBody(tether=True, koma=True)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.log = lambda text: None
        self.server = KB.make_server(self.link, port=0)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # the stand-in phone is this computer (127.0.0.1), so it may pair here; the bridge itself never sets this
        P.configure(self.link, host="127.0.0.1", bind_port=0, pair_from_self=True)

    def tearDown(self):
        P.shutdown(self.link)
        self.server.shutdown()
        self.server.server_close()

    def req(self, method, path, obj=None, port=None, headers=None):
        data = json.dumps(obj).encode() if obj is not None else None
        r = urllib.request.Request(f"http://127.0.0.1:{port or self.port}{path}", data=data, method=method,
                                   headers=headers or {})
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                raw = resp.read()
                return resp.status, dict(resp.headers), json.loads(raw) if raw.startswith(b"{") else raw
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, dict(e.headers), json.loads(raw) if raw.startswith(b"{") else raw

    def on(self):
        st = self.req("POST", "/phone/on")[2]
        self.assertTrue(st["on"], st)
        return st, int(st["address"].rsplit(":", 1)[1])

    def pair(self, lan, code=None):
        code = code or self.req("GET", "/phone")[2]["url"].split("pair=", 1)[1]
        status, _, d = self.req("POST", "/phone/pair", {"code": code}, port=lan)
        return status, d


class PhoneAccessTest(_Served, unittest.TestCase):
    def test_off_by_default(self):
        status, _, st = self.req("GET", "/phone")
        self.assertEqual(status, 200)
        self.assertEqual((st["on"], st["url"], st["qr"], st["paired"]), (False, None, None, 0))
        self.assertEqual(self.req("GET", "/status")[0], 200)  # the computer itself needs no token

    def test_on_shows_a_one_time_code_and_off_closes_the_listener(self):
        st, lan = self.on()
        self.assertEqual(st["address"], f"127.0.0.1:{lan}")
        self.assertTrue(st["url"].startswith(f"http://127.0.0.1:{lan}/?pair="))
        self.assertEqual(len(st["url"].split("pair=", 1)[1]), 22)  # 16 random bytes, URL-safe
        self.assertTrue(all(len(r) == len(st["qr"]) and set(r) <= {"0", "1"} for r in st["qr"]))
        self.assertEqual(self.req("POST", "/phone/on")[2]["url"], st["url"])  # on twice: the same code
        off = self.req("POST", "/phone/off")[2]
        self.assertEqual((off["on"], off["url"], off["address"]), (False, None, None))
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", lan), timeout=1).close()

    def test_pairing_works_once(self):
        st, lan = self.on()
        code = st["url"].split("pair=", 1)[1]
        status, d = self.pair(lan, code)
        self.assertEqual(status, 200)
        self.assertEqual(len(d["token"]), 22)
        self.assertEqual(self.pair(lan, code), (403, {"error": "This code has been used or is not current.",
                                                      "code": "pair_code"}))
        after = self.req("GET", "/phone")[2]
        self.assertNotEqual(after["url"], st["url"])  # a new code for the next phone
        self.assertEqual(after["paired"], 1)
        self.assertEqual(self.pair(lan)[0], 200)
        self.assertEqual(self.req("GET", "/phone")[2]["paired"], 2)
        self.assertEqual(self.req("POST", "/phone/pair", {"code": ""}, port=lan)[0], 403)
        self.assertEqual(self.req("POST", "/phone/pair", {}, port=lan)[0], 403)
        self.assertEqual(self.req("POST", "/phone/pair", {"code": 7}, port=lan)[0], 403)

    def test_the_token_on_every_request_as_in_lan_mode(self):
        _, lan = self.on()
        token = self.pair(lan)[1]["token"]
        status, _, d = self.req("GET", "/status", port=lan)
        self.assertEqual((status, d["code"]), (403, "token"))
        self.assertEqual(self.req("GET", "/status", port=lan, headers={"X-Koma-Token": "wrong"})[0], 403)
        self.assertEqual(self.req("GET", f"/status?token={token}", port=lan)[0], 200)
        status, headers, d = self.req("GET", "/status", port=lan, headers={"X-Koma-Token": token,
                                                                          "Origin": f"http://127.0.0.1:{lan}"})
        self.assertEqual(status, 200)
        self.assertTrue(d["connected"])
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), f"http://127.0.0.1:{lan}")
        self.assertEqual(self.req("POST", "/tether/stop", port=lan)[0], 403)
        if KB.app_index() is not None:  # the app's files load without the token, as in --lan mode
            self.assertEqual(self.req("GET", "/", port=lan)[0], 200)

    def test_management_stays_on_the_computer(self):
        _, lan = self.on()
        hdr = {"X-Koma-Token": self.pair(lan)[1]["token"]}
        for method, path in (("GET", "/phone"), ("POST", "/phone/off"), ("POST", "/phone/revoke"), ("POST", "/phone/on")):
            status, _, d = self.req(method, path, port=lan, headers=hdr)
            self.assertEqual((status, d["code"]), (403, "computer_only"), path)
        self.assertTrue(self.req("GET", "/phone")[2]["on"])

    def test_pairing_happens_only_on_the_network_listener(self):
        self.on()
        status, _, d = self.req("POST", "/phone/pair", {"code": "x"})
        self.assertEqual((status, d["code"]), (404, "not_lan"))
        self.assertEqual(self.req("GET", "/phone/pair")[0], 405)
        self.assertEqual(self.req("GET", "/phone/nope")[0], 404)

    def test_revoke_signs_every_phone_out_and_ends_its_open_live_view(self):
        st, lan = self.on()
        token = self.pair(lan)[1]["token"]
        hdr = {"X-Koma-Token": token}
        self.assertEqual(self.req("POST", "/tether/start", port=lan, headers=hdr)[0], 200)
        conn = http.client.HTTPConnection("127.0.0.1", lan, timeout=10)
        conn.request("GET", "/live?mode=1", headers=hdr)
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        r.read(4000)  # frames flow to the phone
        rv = self.req("POST", "/phone/revoke")[2]
        self.assertEqual((rv["on"], rv["paired"]), (True, 0))
        self.assertNotEqual(rv["url"], st["url"])
        t0 = time.monotonic()
        with self.assertRaises((OSError, http.client.HTTPException)):
            while time.monotonic() - t0 < 5:  # the stream ends: the connection was closed under it
                if not r.read(4000):
                    raise ConnectionResetError("stream ended")
        conn.close()
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual(self.req("GET", "/status", port=lan, headers=hdr)[0], 403)
        self.req("POST", "/tether/stop")

    def test_off_signs_out_too(self):
        _, lan = self.on()
        token = self.pair(lan)[1]["token"]
        self.req("POST", "/phone/off")
        _, lan2 = self.on()
        self.assertEqual(self.req("GET", "/status", port=lan2, headers={"X-Koma-Token": token})[0], 403)

    def test_no_network(self):
        P.configure(self.link)  # the real address finder ...
        self.link.phone_access = None
        old = KB.lan_address
        KB.lan_address = lambda: "127.0.0.1"  # ... finding no network
        try:
            st = self.req("POST", "/phone/on")[2]
        finally:
            KB.lan_address = old
        self.assertEqual((st["on"], st["error"]["code"]), (False, "no_network"))

    def test_port_in_use(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            P.configure(self.link, host="127.0.0.1", bind_port=s.getsockname()[1])
            self.link.phone_access = None
            st = self.req("POST", "/phone/on")[2]
        self.assertEqual((st["on"], st["error"]["code"]), (False, "bind"))

    def test_qr_carries_the_pairing_address(self):
        st, _ = self.on()
        self.assertEqual(st["qr"], ["".join("1" if v else "0" for v in row) for row in __import__("qrmini").matrix(st["url"])])
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV not installed: QR decode not checked")
        a = np.pad(np.array([[0 if c == "1" else 255 for c in r] for r in st["qr"]], dtype=np.uint8), 4,
                   constant_values=255)
        got, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.resize(a, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST))
        self.assertEqual(got, st["url"])

    def test_a_web_page_cannot_turn_access_on_or_read_the_code(self):
        """A sandboxed iframe on any website sends Origin "null", which the loopback listener lets through for the old
        page opened from disk (as file:// is). Phone access answers this computer's own pages only (QA B1)."""
        for origin in ("null", "file://", "file:///home/x/koma-setup.html"):
            for method, path in (("POST", "/phone/on"), ("GET", "/phone"), ("POST", "/phone/revoke"),
                                 ("POST", "/phone/off")):
                status, headers, d = self.req(method, path, headers={"Origin": origin})
                self.assertEqual((status, d["code"]), (403, "origin"), (origin, path))
        self.assertFalse(self.req("GET", "/phone")[2]["on"])  # nothing was switched on
        for origin in (f"http://127.0.0.1:{self.port}", "http://localhost:5173"):  # the app, and its dev server
            self.assertEqual(self.req("GET", "/phone", headers={"Origin": origin})[0], 200, origin)
        self.on()
        status, _, d = self.req("GET", "/phone", headers={"Origin": "null"})
        self.assertEqual(status, 403)
        self.assertNotIn("url", d)  # the pairing code stays on this computer

    def test_the_link_opened_on_this_computer_pairs_nothing(self):
        """The pairing link opened on the computer itself reaches the network listener from the listener's own address:
        it pairs nothing, keeps the code for the phone, and names this computer's own Koma address (QA S4)."""
        P.configure(self.link, host="127.0.0.1", bind_port=0)  # pair_from_self off, as in the bridge itself
        self.link.phone_access = None
        st, lan = self.on()
        status, d = self.pair(lan, st["url"].split("pair=", 1)[1])
        self.assertEqual((status, d["code"], d["local"]), (404, "not_lan", f"http://127.0.0.1:{self.port}/"))
        after = self.req("GET", "/phone")[2]
        self.assertEqual((after["url"], after["paired"]), (st["url"], 0))  # the code still works for the phone

    def test_an_oversized_body_closes_the_connection(self):
        """A body over 4 KB is never read, so the connection closes after the answer instead of reading the rest as the
        next request (QA N1)."""
        _, lan = self.on()
        for port, path in ((lan, "/phone/pair"), (self.port, "/phone/revoke")):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", path, body=b"{" + b" " * 5000 + b"}", headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            self.assertEqual((r.status, r.getheader("Connection")), (413, "close"), path)
            r.read()
            conn.close()
        self.assertEqual(self.req("GET", "/phone")[2]["paired"], 0)
        self.assertTrue(self.req("GET", "/phone")[2]["on"])  # the refused revoke did nothing

    def test_the_listener_follows_the_network_address(self):
        """The address is checked on every GET /phone. After a change (another Wi-Fi, a VPN) the listener moves with a
        new code and phones signed in on the old address are signed out; no network for a moment changes nothing
        (QA N5). 127.0.0.1 and localhost stand in for two network addresses."""
        P.configure(self.link, bind_port=0, pair_from_self=True)
        self.link.phone_access = None
        pa = P.for_link(self.link, self.port, KB)
        where = ["127.0.0.1"]
        pa._lan_host = lambda: where[0]
        st, lan = self.on()
        token = self.pair(lan)[1]["token"]
        self.assertEqual(self.req("GET", "/phone")[2]["address"], st["address"])  # the same address: nothing moves
        where[0] = "localhost"
        moved = self.req("GET", "/phone")[2]
        self.assertTrue(moved["on"])
        self.assertTrue(moved["address"].startswith("localhost:"), moved["address"])
        self.assertNotEqual(moved["url"], st["url"])
        self.assertEqual(moved["paired"], 0)
        lan2 = int(moved["address"].rsplit(":", 1)[1])
        self.assertEqual(self.req("GET", "/status", port=lan2, headers={"X-Koma-Token": token})[0], 403)
        where[0] = None
        self.assertEqual(self.req("GET", "/phone")[2]["address"], moved["address"])

    def test_terminal_note_for_the_command_line(self):
        st, _ = self.on()
        note = P.terminal_note(st["url"])
        self.assertIn(st["url"], note)
        self.assertIn("▀", note)


if __name__ == "__main__":
    unittest.main()
