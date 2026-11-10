#!/usr/bin/env python3
"""Tests for the Photos import (photoimport.py and its routes in komabridge.py), all against the SIMULATED camera
(mockcard.py). No USB device is opened; every file lands in a temporary folder.

Run: python3 tools/komaset/bridge/test_photoimport.py
"""
from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import komabridge as KB  # noqa: E402
import mockcard  # noqa: E402
import photoimport as PI  # noqa: E402


def add_photo(body: mockcard.MockBody, name: str, data: bytes, stamp: str) -> str:
    """A JPEG on the simulated card in 100RICOH, listed with its -FDAT date (as a shot that has finished writing)."""
    path = f"{mockcard.FOLDER}/{name}"
    body.card[path] = data
    body.photo_dates[path] = stamp
    body.folder_entries.append(mockcard._dirent(name, 0x20, len(data), stamp))
    return path


def card_jpeg(body: mockcard.MockBody, name: str) -> bytes:
    return body.card[f"{mockcard.FOLDER}/{name}"]


class _Importing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="koma-import-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        (self.home / "Pictures").mkdir(parents=True)
        self.folder = self.home / "Pictures" / "Koma"
        self.body = mockcard.MockBody(tether=True, shot_delay_s=0.05)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.link.af_wait_s = self.link.press_s = 0.0
        self.imp = KB.importer(self.link, state_dir=self.tmp / "state", folder=self.folder, home=self.home)

    def run_import(self, timeout=20) -> dict:
        self.imp.start()
        self.imp.wait(timeout)
        job = self.imp.snapshot(keys=False)["job"]
        self.assertNotEqual(job["state"], "running", "the import did not finish")
        return job

    def sent(self) -> list[str]:
        return [c.strip() for t in self.body.transports for c in t.sent]

    def ledger(self) -> dict:
        return json.loads((self.tmp / "state" / PI.LEDGER_FILE).read_text())["photos"]


class ImportTest(_Importing):
    def test_snapshot_before_anything_touches_nothing(self):
        s = self.imp.snapshot()
        self.assertEqual((s["display"], s["default"], s["imported"], s["job"]), ("~/Pictures/Koma", True, {}, None))
        self.assertEqual(s["folder"], str(self.folder))
        self.assertFalse((self.tmp / "state").exists())  # reads create nothing
        self.assertEqual(self.body.transports, [])         # and touch no camera

    def test_import_copies_checks_and_remembers(self):
        job = self.run_import()
        self.assertEqual((job["state"], job["total"], job["copied"], job["already"], job["skipped"]), ("done", 3, 3, 0, []))
        self.assertEqual(job["bytes_done"], job["bytes_total"])
        for name, _, stamp in mockcard.PHOTOS:
            y, mo, d = stamp[:10].split(":")
            f = self.folder / y / mo / d / name
            self.assertEqual(f.read_bytes(), card_jpeg(self.body, name), name)
            want = time.mktime(time.strptime(stamp, "%Y:%m:%d %H:%M:%S"))
            self.assertAlmostEqual(f.stat().st_mtime, want, delta=1)  # the file keeps the capture time
        keys = set(self.ledger())
        self.assertEqual(len(keys), 3)
        self.assertEqual(set(job["keys"]), keys)
        snap = self.imp.snapshot()
        self.assertEqual(snap["imported"]["RIMG0001.JPG|%d|2026-11-05 07:12:40" % len(card_jpeg(self.body, "RIMG0001.JPG"))],
                         "~/Pictures/Koma/2026/11/05/RIMG0001.JPG")
        self.assertEqual(list(self.folder.rglob("*.part")), [])
        # nothing new: a second import copies nothing and reads no file
        retr = sum(c.startswith("RETR") for c in self.sent())
        again = self.run_import()
        self.assertEqual((again["state"], again["total"], again["copied"]), ("done", 0, 0))
        self.assertEqual(sum(c.startswith("RETR") for c in self.sent()), retr)
        # remembered across a restart of the bridge
        imp2 = PI.Importer(list_photos=lambda: [], read=None, blocked=lambda: False, state_dir=self.tmp / "state",
                           folder=self.folder, home=self.home)
        self.assertEqual(set(imp2.snapshot()["imported"]), keys)

    def test_only_reads_the_card(self):
        self.run_import()
        verbs = {c.split(" ")[0] for c in self.sent()}
        self.assertLessEqual(verbs, {"-VER", "-NLST", "-INFO", "-FDAT", "RETR"})
        self.assertNotIn("-NLST /EXT0", self.sent())  # never the card root (it hangs on a 64 GB card)
        for t in self.body.transports:
            self.assertEqual(t.violations, [])

    def test_only_new_photos_after_a_new_shot(self):
        self.run_import()
        new = mockcard.encode_gray_jpeg(64, 48, lambda x, y: (x * 3 + y) % 256)
        add_photo(self.body, "RIMG0004.JPG", new, "2026:11:08 09:30:00")
        job = self.run_import()
        self.assertEqual((job["total"], job["copied"]), (1, 1))
        self.assertEqual((self.folder / "2026" / "11" / "08" / "RIMG0004.JPG").read_bytes(), new)
        self.assertEqual(len(self.ledger()), 4)

    def test_newest_first_in_the_sheets_order(self):
        add_photo(self.body, "RIMG0004.JPG", mockcard.encode_gray_jpeg(64, 48, lambda x, y: (x + 2 * y) % 256),
                  "2026:11:08 09:30:00")
        job = self.run_import()
        listed = [p["name"] for p in KB.list_photos(self.link)["photos"]]  # the contact sheet's order
        self.assertEqual([k.split("|")[0] for k in job["keys"]], listed)
        self.assertEqual(listed[0], "RIMG0004.JPG")

    def test_same_name_same_bytes_counts_as_imported(self):
        d = self.folder / "2026" / "11" / "05"
        d.mkdir(parents=True)
        (d / "RIMG0001.JPG").write_bytes(card_jpeg(self.body, "RIMG0001.JPG"))
        job = self.run_import()
        self.assertEqual((job["copied"], job["already"], job["renamed"]), (2, 1, []))
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["RIMG0001.JPG"])
        self.assertEqual(len(self.ledger()), 3)

    def test_same_name_other_file_is_never_overwritten(self):
        d = self.folder / "2026" / "11" / "05"
        d.mkdir(parents=True)
        (d / "RIMG0001.JPG").write_bytes(b"another camera's photo")
        job = self.run_import()
        self.assertEqual((d / "RIMG0001.JPG").read_bytes(), b"another camera's photo")
        self.assertEqual((d / "RIMG0001-2.JPG").read_bytes(), card_jpeg(self.body, "RIMG0001.JPG"))
        self.assertEqual(job["renamed"], [{"name": "RIMG0001.JPG", "saved_as": "RIMG0001-2.JPG",
                                           "file": "~/Pictures/Koma/2026/11/05/RIMG0001-2.JPG"}])
        self.assertEqual(job["copied"], 3)

    def test_a_photo_still_being_written_waits_for_the_next_import(self):
        whole = card_jpeg(self.body, "RIMG0002.JPG")
        path = f"{mockcard.FOLDER}/RIMG0002.JPG"
        self.body.card[path] = whole[:len(whole) // 2]  # the camera is still writing it: listed at this size, no EOI
        self.body.folder_entries[1] = mockcard._dirent("RIMG0002.JPG", 0x20, len(whole) // 2, "2026:11:06 14:03:11")
        job = self.run_import()
        self.assertEqual(job["skipped"], [{"name": "RIMG0002.JPG", "why": "incomplete"}])
        self.assertEqual(job["copied"], 2)
        self.assertFalse((self.folder / "2026" / "11" / "06" / "RIMG0002.JPG").exists())
        self.body.card[path] = whole  # written now
        self.body.folder_entries[1] = mockcard._dirent("RIMG0002.JPG", 0x20, len(whole), "2026:11:06 14:03:11")
        job = self.run_import()
        self.assertEqual((job["total"], job["copied"], job["skipped"]), (1, 1, []))
        self.assertEqual((self.folder / "2026" / "11" / "06" / "RIMG0002.JPG").read_bytes(), whole)

    def test_a_cached_partial_read_is_read_again(self):
        path = f"{mockcard.FOLDER}/RIMG0003.JPG"
        whole = self.body.card[path]
        self.link.run(lambda s: None)  # open the session
        self.link.cache[path] = whole[:100]  # a thumbnail read while the file was still being written
        self.link.cache_bytes += 100
        job = self.run_import()
        self.assertEqual((job["copied"], job["skipped"]), (3, []))
        self.assertEqual(self.link.cache[path], whole)

    def test_jpeg_complete(self):
        for tail, ok in ((b"\xff\xd9", True), (b"\xff\xd9\xff", True), (b"\xff\xd9\xff\xff", True),
                         (b"\xff\xd9\x00\x00", True), (b"\x12\x34", False), (b"\xd9\xff", False), (b"", False)):
            self.assertEqual(PI.jpeg_complete(b"\xff\xd8" + b"\x01" * 20 + tail), ok, tail)
        self.assertFalse(PI.jpeg_complete(b"\x00\x00" + b"\x01" * 20 + b"\xff\xd9"))

    def test_remote_on_refuses_then_stops_between_files(self):
        self.assertTrue(KB.tether_start(self.link)["ok"])
        job = self.run_import()
        self.assertEqual((job["state"], job["stop"]["why"], job["copied"]), ("stopped", "remote_on", 0))
        KB.tether_stop(self.link)
        real = self.imp.blocked
        self.imp.blocked = lambda: len(self.imp.job["keys"]) >= 1 or real()  # live view starts after the first copy
        job = self.run_import()
        self.assertEqual((job["state"], job["stop"]["why"], job["copied"]), ("stopped", "remote_on", 1))
        self.imp.blocked = real
        job = self.run_import()
        self.assertEqual((job["state"], job["total"], job["copied"]), ("done", 2, 2))

    def test_live_view_starting_mid_import_lets_no_read_through(self):
        """tether_start (live view in another tab) waits on the link's lock while the import reads. The importer's own
        check runs outside that lock, so its next read can queue up behind tether_start; that read must be refused,
        not sent with remote control on (a card read then drops live view)."""
        for i in range(6):
            add_photo(self.body, f"RIMG{10 + i:04d}.JPG",
                      mockcard.encode_gray_jpeg(64, 48, lambda x, y, i=i: (x * 2 + y * i) % 256), f"2026:11:07 10:{i:02d}:00")
        self.body.retr_bps = max(len(v) for k, v in self.body.card.items() if k.endswith(".JPG")) / 0.15
        events, real_run = [], self.link.run

        def run(fn):  # every command the session sends, with remote control's state while it ran (under the lock)
            def wrapped(s):
                mark = len(s.history)
                try:
                    return fn(s)
                finally:
                    events.extend((c.split(" ")[0], self.link.remote_on) for c in s.history[mark:])
            return real_run(wrapped)
        self.link.run = run
        asking, real_read = threading.Event(), self.imp.read

        def read(rel, size):
            asking.set()  # the importer has just checked: remote control was off
            return real_read(rel, size)
        self.imp.read = read
        real_start = KB._start

        def start(link, s, log):  # holds the lock until the import has queued its next read behind it
            asking.clear()
            asking.wait(3)
            time.sleep(0.05)
            return real_start(link, s, log)
        self.imp.start()
        for _ in range(300):
            if self.imp.job["keys"]:
                break
            time.sleep(0.01)
        with mock.patch.object(KB, "_start", start):
            self.assertTrue(KB.tether_start(self.link)["ok"])
        self.imp.wait(20)
        job = self.imp.snapshot(keys=False)["job"]
        self.assertEqual((job["state"], job["stop"]["why"]), ("stopped", "remote_on"))
        self.assertGreaterEqual(job["copied"], 1)
        self.assertLess(job["copied"], 9)
        self.assertEqual([c for c, on in events if on and c in ("RETR", "-INFO", "-FDAT", "-NLST")], [])
        KB.tether_stop(self.link)

    def test_each_bridge_process_names_its_run(self):
        run = self.imp.snapshot()["run"]
        self.assertRegex(run, r"^[0-9a-f]{8}$")
        self.run_import()
        snap = self.imp.snapshot(keys=False)
        self.assertEqual((snap["run"], snap["job"]["id"]), (run, 1))
        again = KB.importer(self.link, state_dir=self.tmp / "state", folder=self.folder, home=self.home)  # a restart
        snap = again.snapshot()
        self.assertNotEqual(snap["run"], run)  # job ids start again at 1; the page tells the two apart by run
        self.assertIsNone(snap["job"])
        self.assertEqual(len(snap["imported"]), 3)

    def test_a_drive_unplugged_mid_import_is_not_recreated(self):
        drive = self.tmp / "Volumes" / "Photos"
        drive.mkdir(parents=True)
        self.imp.set_folder(str(drive / "Koma"))
        real = self.imp.read

        def read(rel, size):
            if self.imp.job["keys"]:
                shutil.rmtree(drive, ignore_errors=True)  # unplugged after the first copy, while the next one is read
            return real(rel, size)
        self.imp.read = read
        job = self.run_import()
        self.assertEqual((job["state"], job["stop"]["why"], job["copied"]), ("failed", "folder_missing", 1))
        self.assertFalse(drive.exists())  # neither the folder nor the drive's mount point came back
        self.assertEqual(len(self.ledger()), 1)

    def test_a_drive_gone_between_files_stops_before_the_next_card_read(self):
        drive = self.tmp / "Volumes" / "Photos"
        drive.mkdir(parents=True)
        self.imp.set_folder(str(drive / "Koma"))
        real_write, real_read, reads = PI._write_json, self.imp.read, []

        def write(path, obj):
            real_write(path, obj)
            if path.name == PI.LEDGER_FILE:
                shutil.rmtree(drive, ignore_errors=True)  # unplugged right after the first copy was remembered
        self.imp.read = lambda rel, size: (reads.append(rel), real_read(rel, size))[1]
        with mock.patch.object(PI, "_write_json", write):
            job = self.run_import()
        self.assertEqual((job["state"], job["stop"]["why"], job["copied"]), ("failed", "folder_missing", 1))
        self.assertEqual(len(reads), 1)
        self.assertFalse(drive.exists())

    def test_camera_lost_mid_import(self):
        real = self.imp.read

        def read(rel, size):
            if self.imp.job["keys"]:
                self.body.unplug()
            return real(rel, size)
        self.imp.read = read
        job = self.run_import()
        self.assertEqual((job["state"], job["stop"]["why"], job["copied"]), ("failed", "camera", 1))
        self.assertEqual(len(self.ledger()), 1)  # what was checked stays remembered

    def test_stop_between_files(self):
        gate = threading.Event()
        real = self.imp.read

        def read(rel, size):
            if self.imp.job["keys"]:
                gate.wait(5)
            return real(rel, size)
        self.imp.read = read
        self.imp.start()
        for _ in range(100):
            if self.imp.job["keys"]:
                break
            time.sleep(0.02)
        self.imp.stop()
        gate.set()
        self.imp.wait(10)
        job = self.imp.snapshot(keys=False)["job"]
        self.assertEqual((job["state"], job["stop"]["why"], job["copied"]), ("stopped", "cancelled", 2))

    def test_disk_full(self):
        with mock.patch.object(PI, "store", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            job = self.run_import()
        self.assertEqual((job["state"], job["stop"]["why"], job["copied"]), ("failed", "disk_full", 0))
        self.assertIn("No space left", job["stop"]["detail"])

    def test_read_back_mismatch_removes_the_copy(self):
        real = PI._sha256_file
        with mock.patch.object(PI, "_sha256_file", side_effect=lambda p: "0" * 64 if p.suffix == ".JPG" else real(p)):
            job = self.run_import()
        self.assertEqual((job["state"], job["stop"]["why"]), ("failed", "verify"))
        self.assertEqual([p for p in self.folder.rglob("*") if p.is_file()], [])
        self.assertFalse((self.tmp / "state" / PI.LEDGER_FILE).exists())

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX permissions and a non-root user")
    def test_folder_not_writable(self):
        self.folder.mkdir()
        self.folder.chmod(0o500)
        self.addCleanup(self.folder.chmod, 0o700)
        job = self.run_import()
        self.assertEqual((job["state"], job["stop"]["why"]), ("failed", "no_write"))

    def test_unplugged_drive_is_not_recreated(self):
        gone = self.tmp / "Volumes" / "Photos" / "Koma"   # chosen earlier; its drive is not there now
        (self.tmp / "state2").mkdir()
        (self.tmp / "state2" / PI.STATE_FILE).write_text(json.dumps({"folder": str(gone)}))
        imp = KB.importer(self.link, state_dir=self.tmp / "state2", folder=self.folder, home=self.home)
        self.assertEqual(imp.snapshot()["folder"], str(gone))
        imp.start()
        imp.wait(10)
        job = imp.snapshot(keys=False)["job"]
        self.assertEqual((job["state"], job["stop"]["why"]), ("failed", "folder_missing"))
        self.assertFalse((self.tmp / "Volumes").exists())

    def test_default_folder_is_made_when_pictures_exists(self):
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.run_import()["state"], "done")
        self.assertTrue(self.folder.is_dir())

    def test_default_folder_is_made_even_without_pictures(self):
        home = self.tmp / "bare-home"
        home.mkdir()
        imp = PI.Importer(list_photos=self.imp.list_photos, read=self.imp.read, blocked=self.imp.blocked,
                          state_dir=self.tmp / "state3", home=home)
        self.assertEqual(imp.snapshot()["display"], "~/Pictures/Koma")
        imp.start()
        imp.wait(20)
        self.assertEqual(imp.snapshot(keys=False)["job"]["state"], "done")
        self.assertTrue((home / "Pictures" / "Koma" / "2026" / "11" / "06" / "RIMG0003.JPG").is_file())

    def test_simulated_default_works_out_of_the_box(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"KOMA_STATE_DIR": d}):
            imp = PI.Importer(list_photos=self.imp.list_photos, read=self.imp.read, blocked=self.imp.blocked,
                              simulated=True)
            imp.start()
            imp.wait(20)
            self.assertEqual(imp.snapshot(keys=False)["job"]["state"], "done")
            self.assertTrue((Path(d) / "Pictures" / "Koma" / "2026" / "11" / "05" / "RIMG0001.JPG").is_file())

    def test_one_job_at_a_time(self):
        gate = threading.Event()
        real = self.imp.read
        self.imp.read = lambda rel, size: (gate.wait(5), real(rel, size))[1]
        self.imp.start()
        with self.assertRaises(PI.Refused) as cm:
            self.imp.start()
        self.assertEqual((cm.exception.status, cm.exception.code), (409, "import_running"))
        gate.set()
        self.imp.wait(10)

    def test_unreadable_ledger_is_kept_aside(self):
        state = self.tmp / "state"
        state.mkdir()
        (state / PI.LEDGER_FILE).write_text("{not json")
        imp = PI.Importer(list_photos=lambda: [], read=None, blocked=lambda: False, state_dir=state, home=self.home)
        self.assertEqual(imp.snapshot()["imported"], {})
        self.assertEqual((state / (PI.LEDGER_FILE + ".bad")).read_text(), "{not json")

    def test_simulated_state_never_lands_in_the_home_folder(self):
        with mock.patch.dict(os.environ, {"KOMA_STATE_DIR": ""}):
            os.environ.pop("KOMA_STATE_DIR")
            imp = PI.Importer(list_photos=lambda: [], read=None, blocked=lambda: False, simulated=True)
        self.assertTrue(str(imp.state_dir).startswith(tempfile.gettempdir()))
        self.assertTrue(str(imp.folder).startswith(str(imp.state_dir)))


class FolderTest(_Importing):
    def test_refusals(self):
        for text, code in (("", "folder_empty"), ("   ", "folder_empty"), (None, "folder_empty"),
                           ("Pictures/Koma", "folder_relative"), ("./x", "folder_relative")):
            with self.assertRaises(PI.Refused, msg=repr(text)) as cm:
                self.imp.set_folder(text)
            self.assertEqual((cm.exception.status, cm.exception.code), (400, code), text)
        (self.home / "afile").write_text("x")
        for text, code in (("~/afile", "folder_is_file"), (str(self.tmp / "no" / "such" / "Koma"), "folder_no_parent")):
            with self.assertRaises(PI.Refused) as cm:
                self.imp.set_folder(text)
            self.assertEqual(cm.exception.code, code, text)
        self.assertFalse((self.tmp / "no").exists())

    def test_choose_makes_checks_and_keeps_it(self):
        s = self.imp.set_folder("~/Pictures/GR")
        self.assertEqual((s["display"], s["default"]), ("~/Pictures/GR", False))
        self.assertTrue((self.home / "Pictures" / "GR").is_dir())
        self.assertEqual(list((self.home / "Pictures" / "GR").iterdir()), [])  # the write probe cleaned up
        imp2 = PI.Importer(list_photos=lambda: [], read=None, blocked=lambda: False, state_dir=self.tmp / "state",
                           folder=self.folder, home=self.home)
        self.assertEqual(imp2.snapshot()["display"], "~/Pictures/GR")
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        self.assertEqual(self.imp.set_folder(str(outside) + "/")["display"], str(outside))
        job = self.run_import()
        self.assertEqual(job["copied"], 3)
        self.assertTrue((outside / "2026" / "11" / "06" / "RIMG0003.JPG").exists())

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX permissions and a non-root user")
    def test_not_writable(self):
        ro = self.home / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        self.addCleanup(ro.chmod, 0o700)
        with self.assertRaises(PI.Refused) as cm:
            self.imp.set_folder("~/ro")
        self.assertEqual(cm.exception.code, "folder_cannot_write")


class RoutesTest(unittest.TestCase):
    token = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="koma-import-http-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "home" / "Pictures").mkdir(parents=True)
        self.body = mockcard.MockBody(tether=True)
        self.link = KB.Link(KB.MockBackend(self.body))
        self.imp = KB.importer(self.link, state_dir=self.tmp / "state", folder=self.tmp / "home" / "Pictures" / "Koma",
                               home=self.tmp / "home")
        host = "0.0.0.0" if self.token else "127.0.0.1"
        self.server = KB.make_server(self.link, host=host, port=0, token=self.token)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = KB.make_handler(self.link, self.port, self.token)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def jreq(self, method, path, obj=None, host="127.0.0.1"):
        sep = "&" if "?" in path else "?"
        url = f"http://{host}:{self.port}{path}" + (f"{sep}token={self.token}" if self.token else "")
        data = json.dumps(obj).encode() if obj is not None else None
        r = urllib.request.Request(url, data=data, method=method,
                                   headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_routes(self):
        status, s = self.jreq("GET", "/photos/import")
        self.assertEqual(status, 200, s)
        self.assertEqual((s["display"], s["choose"], s["imported"], s["job"]), ("~/Pictures/Koma", True, {}, None))
        status, s = self.jreq("GET", "/photos/import?job=1")
        self.assertNotIn("imported", s)
        status, s = self.jreq("POST", "/photos/folder", {"path": "Pictures"})
        self.assertEqual((status, s["code"]), (400, "folder_relative"))
        status, s = self.jreq("POST", "/photos/folder", {"path": "~/Pictures/GR"})
        self.assertEqual((status, s["display"]), (200, "~/Pictures/GR"))
        status, s = self.jreq("POST", "/photos/import")
        self.assertEqual((status, s["job"]["state"]), (200, "running"))
        for _ in range(200):
            status, s = self.jreq("GET", "/photos/import?job=1")
            if s["job"]["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual((s["job"]["state"], s["job"]["copied"]), ("done", 3))
        status, s = self.jreq("GET", "/photos/import")
        self.assertEqual(len(s["imported"]), 3)
        self.assertTrue(all(v.startswith("~/Pictures/GR/2026/11/") for v in s["imported"].values()))
        status, s = self.jreq("DELETE", "/photos/import")
        self.assertEqual((status, s["job"]["state"]), (200, "done"))
        status, _ = self.jreq("GET", "/photos/folder")
        self.assertEqual(status, 404)
        status, _ = self.jreq("POST", "/photos")
        self.assertEqual(status, 404)
        # photo paths are untouched by the new routes
        status, p = self.jreq("GET", "/photos")
        self.assertEqual((status, p["count"]), (200, 3))

    def test_remote_on(self):
        self.assertTrue(KB.tether_start(self.link)["ok"])
        status, s = self.jreq("GET", "/photos/import")          # the state reads no card: still answered
        self.assertEqual(status, 200)
        status, s = self.jreq("POST", "/photos/import")         # an import would read the card: refused
        self.assertEqual((status, s["code"]), (409, "remote_on"))
        self.assertIsNone(self.imp.job)
        KB.tether_stop(self.link)

    def test_listed_in_api(self):
        status, s = self.jreq("GET", "/api")
        self.assertIn("POST /photos/folder", s["endpoints"])

    def test_a_photo_read_queued_behind_tether_start_is_refused(self):
        """GET /photos/<F>/<N> (a thumbnail, the full view, Download selected) checks remote control before it waits
        for the link; tether_start can switch it on meanwhile. The read must answer 409, not run with it on."""
        real_start, holding, got = KB._start, threading.Event(), []

        def start(link, s, log):
            holding.set()
            time.sleep(0.4)  # the photo request arrives now: past the route's own check, queued on the lock
            return real_start(link, s, log)

        def get(path):
            holding.wait(5)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}" +
                                            (f"?token={self.token}" if self.token else ""), timeout=10) as r:
                    got.append(r.status)
            except urllib.error.HTTPError as e:
                got.append(e.code)
        threads = [threading.Thread(target=get, args=(p,)) for p in ("/photos/100RICOH/RIMG0001.JPG", "/photos")]
        for t in threads:
            t.start()
        with mock.patch.object(KB, "_start", start):
            self.assertTrue(KB.tether_start(self.link)["ok"])
        for t in threads:
            t.join(10)
        self.assertEqual(got, [409, 409])
        self.assertFalse(any(c.startswith(("RETR", "-NLST")) for t in self.body.transports for c in t.sent))
        KB.tether_stop(self.link)


def _lan_ip() -> str | None:
    ip = KB.lan_address()
    if ip.startswith("127."):
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((ip, 0))
    except OSError:
        return None
    return ip


class PhoneModeTest(RoutesTest):
    token = "test-token"

    def test_routes(self):  # loopback with the token: same as above, folder changes allowed here
        status, s = self.jreq("GET", "/photos/import")
        self.assertEqual((status, s["choose"]), (200, True))

    test_remote_on = None
    test_listed_in_api = None

    @unittest.skipIf(_lan_ip() is None, "no local network address to connect from")
    def test_folder_only_from_this_computer(self):
        ip = _lan_ip()
        status, s = self.jreq("GET", "/photos/import", host=ip)
        self.assertEqual((status, s["choose"]), (200, False))
        status, s = self.jreq("POST", "/photos/folder", {"path": "~/Pictures/GR"}, host=ip)
        self.assertEqual((status, s["code"]), (403, "folder_here_only"))
        self.assertFalse((self.tmp / "home" / "Pictures" / "GR").exists())


if __name__ == "__main__":
    unittest.main()
