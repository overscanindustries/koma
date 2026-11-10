#!/usr/bin/env python3
"""GET /photos?newest=1 (the Looks try-on's one photo), against the SIMULATED camera (mockcard.py). No USB device.

The full listing sends -INFO and -FDAT for every JPEG on the card (75 ms a pair on hardware); the newest-only form
walks the same folders and sends them for one JPEG. MOCK: the simulated camera's -NLST times are the -FDAT dates.

Run: python3 tools/komaset/bridge/test_komabridge_newest.py
"""
import json
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


class NewestPhotoTest(unittest.TestCase):
    def setUp(self):
        self.body = mockcard.MockBody(reboot_s=0.2, tether=True)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.server = KB.make_server(self.link, port=0)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def sent(self, verb):
        return sum(c.startswith(verb) for t in self.body.transports for c in t.sent)

    def add(self, name, stamp, data=b"\xff\xd8newest\xff\xd9"):
        full = f"{mockcard.FOLDER}/{name}"
        self.body.card[full] = data
        self.body.photo_dates[full] = stamp
        self.body.folder_entries.append(mockcard._dirent(name, 0x20, len(data), stamp))

    def test_newest_alone_with_one_info_and_fdat(self):
        status, p = self.get("/photos?newest=1")
        self.assertEqual(status, 200)
        self.assertEqual(p["count"], 3)                            # every JPEG listed
        self.assertEqual([x["name"] for x in p["photos"]], ["RIMG0003.JPG"])
        self.assertEqual(self.sent("-INFO"), 1)
        self.assertEqual(self.sent("-FDAT"), 1)
        # the same record as the full listing's first entry
        status, full = self.get("/photos")
        self.assertEqual(full["photos"][0], p["photos"][0])

    def test_newest_follows_the_file_time_not_the_name(self):
        self.add("R0015710.JPG", "2026:11:08 14:32:10")            # sorts before RIMG.... by name, newer by time
        _, p = self.get("/photos?newest=1")
        self.assertEqual(p["photos"][0]["path"], "100RICOH/R0015710.JPG")
        self.assertEqual(p["photos"][0]["date"], "2026-11-08 14:32:10")
        self.assertEqual(p["count"], 4)

    def test_an_empty_card_has_no_newest(self):
        for name, _, _ in mockcard.PHOTOS:
            self.body.card.pop(f"{mockcard.FOLDER}/{name}", None)
        self.body.folder_entries.clear()
        status, p = self.get("/photos?newest=1")
        self.assertEqual((status, p["count"], p["photos"]), (200, 0, []))

    def test_other_values_list_every_photo(self):
        _, p = self.get("/photos?newest=0")
        self.assertEqual(len(p["photos"]), 3)

    def test_409_while_remote_is_on(self):
        self.assertTrue(KB.tether_start(self.link)["ok"])
        status, p = self.get("/photos?newest=1")
        self.assertEqual(status, 409)
        self.assertIn("Leave Remote before reading photos", p["error"])
        self.assertEqual(self.sent("-INFO"), 0)


if __name__ == "__main__":
    unittest.main()
