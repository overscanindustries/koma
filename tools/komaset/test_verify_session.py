#!/usr/bin/env python3
"""Tests for verify_session. Run: python3 tools/komaset/test_verify_session.py"""
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_session as V  # noqa: E402

SAMPLE = HERE / "testdata" / "session-sample"
BASE = HERE / "testdata" / "main-2026-11-06.cur"


def run(root, *extra):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = V.main([str(root), *extra])
    return rc, buf.getvalue()


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.card = self.tmp / "card"
        shutil.copytree(SAMPLE, self.card)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean(self):
        rc, out = run(self.card, "--expect-tag", "Rev024M", "--baseline", str(BASE))
        self.assertEqual(rc, 0, out)
        self.assertIn("STAGE=3", out)
        self.assertIn("POOL: 3,904 B", out)
        self.assertIn("4-byte DONE marker", out)
        self.assertIn("import status 1 = OK", out)
        self.assertIn("diff vs baseline", out)
        self.assertIn("KOMALOG OK; PARAM.CUR OK; Import files OK", out)

    def test_bad_session(self):
        (self.card / "PARAM.OK").unlink()
        (self.card / "PARAM.BAD").write_bytes(b"BAD TAG\r\n")
        blk = bytearray((self.card / "PARAM.CUR").read_bytes())
        import komaset as K
        blk[0x8DC] = 2
        K.set_checksum(blk, K.Map(HERE / "settings-map.json"))
        (self.card / "PARAM.CUR").write_bytes(blk)
        rc, out = run(self.card)
        self.assertEqual(rc, 1, out)
        self.assertIn("reason: BAD TAG", out)
        self.assertIn("import status 2 = BAD", out)
        self.assertIn("Import files: ATTENTION", out)

    def test_status_3_is_ok(self):
        blk = bytearray((self.card / "PARAM.CUR").read_bytes())
        import komaset as K
        blk[0x8DC] = 3
        K.set_checksum(blk, K.Map(HERE / "settings-map.json"))
        (self.card / "PARAM.CUR").write_bytes(blk)
        rc, out = run(self.card, "--expect-tag", "Rev024M")
        self.assertEqual(rc, 0, out)
        self.assertIn("import status 3 = OK", out)
        self.assertIn("different firmware tag", out)

    def test_missing_komalog(self):
        (self.card / "KOMALOG.TXT").unlink()
        rc, out = run(self.card)
        self.assertEqual(rc, 2, out)
        self.assertIn("KOMALOG: MISSING", out)

    def test_unconsumed_dat_and_wrong_tag(self):
        (self.card / "PARAM.DAT").write_bytes(b"\x01" * 4096)
        (self.card / "PARAM.OK").unlink()
        rc, out = run(self.card, "--expect-tag", "Rev0240")
        self.assertEqual(rc, 1)
        self.assertIn("NOT consumed", out)
        self.assertIn("tag mismatch", out)

    def test_deleted_dat_and_lowercase_names(self):
        (self.card / "PARAM.DAT").unlink()
        (self.card / "PARAM.OK").rename(self.card / "param.ok")
        rc, out = run(self.card)
        self.assertEqual(rc, 0, out)
        self.assertIn("PARAM.DAT: absent", out)
        self.assertIn("PARAM.OK: present", out)

    def test_low_pool_is_attention(self):
        p = self.card / "KOMALOG.TXT"
        p.write_text(p.read_text().replace("POOL =00000F40", "POOL =00000400"))
        rc, out = run(self.card)
        self.assertEqual(rc, 1, out)


if __name__ == "__main__":
    unittest.main()
