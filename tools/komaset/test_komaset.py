#!/usr/bin/env python3
"""Tests for komaset. Run: python3 tools/komaset/test_komaset.py"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import komaset as K  # noqa: E402

MAP = str(HERE / "testdata" / "sample-map.json")


def synth(seed=7):
    b = bytearray((i * seed + 13) & 0xFF for i in range(4096))
    b[0:4] = b"MG1H"
    b[0x059] = 1
    b[0x05c] = 3
    b[0x060] = 0b101
    b[0x070:0x072] = (1234).to_bytes(2, "big")
    b[0x074] = 0xFD  # -3
    b[0x8b0:0x8b5] = b"KOMA\x02"
    K.set_checksum(b, K.Map(MAP))
    return b


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = K.main(["--map", MAP, *argv])
    return rc, out.getvalue(), err.getvalue()


class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.m = K.Map(MAP)

    def w(self, name, data):
        p = self.d / name
        p.write_bytes(bytes(data)) if isinstance(data, (bytes, bytearray)) else p.write_text(data)
        return str(p)

    def test_round_trip(self):
        b = synth()
        src = self.w("a.cur", b)
        txt = self.d / "s.txt"
        self.assertEqual(run("dump", src, "-o", str(txt))[0], 0)
        self.assertIn("iso", txt.read_text())
        self.assertIn("AUTO-HI", txt.read_text())
        self.assertIn("FLAG_A|FLAG_C", txt.read_text())
        out = self.d / "o.dat"
        self.assertEqual(run("apply", str(txt), "-o", str(out))[0], 0)
        self.assertEqual(out.read_bytes(), bytes(b))

    def test_checksum_recompute(self):
        b = synth()
        b[0x200] ^= 0xFF  # invalidates stored checksum
        src = self.w("bad.cur", b)
        self.assertEqual(run("check", src)[0], 1)
        txt = self.d / "s.txt"
        run("dump", src, "-o", str(txt))
        out = self.d / "o.dat"
        run("apply", str(txt), "-o", str(out))
        self.assertEqual(run("check", str(out))[0], 0)
        o = out.read_bytes()
        self.assertEqual(int.from_bytes(o[4092:4096], "big"), sum(
            int.from_bytes(o[i:i + 2], "big") for i in range(0, 4092, 2)) & 0xFFFFFFFF)

    def test_apply_rejects_bad_enum(self):
        base = self.w("base.cur", synth())
        t = self.w("p.txt", "[shooting]\niso = NOPE\nbracket = 99\n")
        out = self.d / "o.dat"
        rc, _, err = run("apply", t, "--base", base, "-o", str(out))
        self.assertEqual(rc, 2)
        self.assertIn("iso", err)
        self.assertIn("bracket", err)
        self.assertFalse(out.exists())
        rc, _, _ = run("apply", t, "--base", base, "-o", str(out), "--force")
        self.assertEqual(rc, 0)

    def test_partial_apply_overlay(self):
        b = synth()
        base = self.w("base.cur", b)
        t = self.w("p.txt", "iso = 100\ntrim = -4\nflags = FLAG_B\n")
        out = self.d / "o.dat"
        self.assertEqual(run("apply", t, "--base", base, "-o", str(out))[0], 0)
        o = out.read_bytes()
        self.assertEqual((o[0x059], o[0x074], o[0x060]), (2, 0xFC, 2))
        self.assertEqual(o[0x100:0x200], bytes(b[0x100:0x200]))

    def test_merge_skips_excluded(self):
        t, s = synth(7), synth(11)
        s[0x059] = 2
        s[0x070:0x072] = b"\x99\x99"
        s[0x8b4] = 2
        t_p, s_p = self.w("t.cur", t), self.w("s.cur", s)
        out = self.d / "m.dat"
        rc, so, _ = run("merge", "--target", t_p, "--source", s_p, "-o", str(out))
        self.assertEqual(rc, 0)
        o = out.read_bytes()
        self.assertEqual(o[0x059], 2)
        self.assertEqual(o[0x070:0x072], bytes(t[0x070:0x072]))
        self.assertEqual(o[0:4], b"MG1H")
        self.assertEqual(run("check", str(out))[0], 0)
        self.assertIn("shutter counter", so)
        run("merge", "--target", t_p, "--source", s_p, "-o", str(out), "--include-excluded")
        self.assertEqual(out.read_bytes()[0x070:0x072], b"\x99\x99")

    def test_merge_accepts_param_sav(self):
        t, s = synth(7), synth(11)
        s[0x059] = 2
        K.set_checksum(s, self.m)
        t_p, s_p = self.w("t.cur", t), self.w("PARAM.SAV", s)
        out = self.d / "m.dat"
        rc, _, _ = run("merge", "--target", t_p, "--source", s_p, "-o", str(out))
        self.assertEqual(rc, 0)
        self.assertEqual(out.read_bytes()[0x059], 2)
        for cmd in (("check", s_p), ("dump", s_p), ("diff", s_p, t_p)):
            self.assertIn(run(*cmd)[0], (0, 1))

    def test_merge_refuses_koma_mismatch(self):
        t, s = synth(), synth(11)
        s[0x8b4] = 3
        rc, _, err = run("merge", "--target", self.w("t", t), "--source", self.w("s", s),
                         "-o", str(self.d / "m"))
        self.assertEqual(rc, 2)
        self.assertIn("Koma", err)

    def ksio(self, b, tag):
        b[0x8d0:0x8d4] = b"KSIO"
        b[0x8d4] = 1
        b[0x8d5:0x8dc] = tag
        K.set_checksum(b, self.m)
        return b

    def test_merge_tag_mismatch_warns_and_copies(self):
        # Rev024M -> 240K2: same Koma settings version, different firmware tags: copies with a warning
        t, s = self.ksio(synth(), b"240K1.0"), self.ksio(synth(11), b"Rev024M")
        tp, sp, out = self.w("t", t), self.w("s", s), str(self.d / "m")
        rc, _, err = run("merge", "--target", tp, "--source", sp, "-o", out)
        self.assertEqual(rc, 0, err)
        self.assertIn("warning: These cameras run different firmware tags (Rev024M, 240K1.0). Settings still copy; "
                      "Koma version matches.", err)
        self.assertNotIn("refusing", err)
        self.assertTrue(Path(out).exists())
        # the Koma version still refuses (and the tag warning then does not claim the versions match)
        s[0x8b4] = 3
        K.set_checksum(s, self.m)
        rc, _, err = run("merge", "--target", tp, "--source", self.w("s3", s), "-o", str(self.d / "m3"))
        self.assertEqual(rc, 2)
        self.assertIn("Koma versions differ", err)
        self.assertIn("different firmware tags", err)
        self.assertNotIn("Koma version matches", err)
        self.assertFalse((self.d / "m3").exists())

    def test_merge_same_tag_ok_and_one_sided_warns(self):
        t, s = self.ksio(synth(), b"Rev024M"), self.ksio(synth(11), b"Rev024M")
        rc, _, err = run("merge", "--target", self.w("t", t), "--source", self.w("s", s), "-o", str(self.d / "m"))
        self.assertEqual(rc, 0, err)
        t2 = self.ksio(synth(), b"Rev024M")
        rc, _, err = run("merge", "--target", self.w("t2", t2), "--source", self.w("s2", synth(11)), "-o", str(self.d / "m2"))
        self.assertEqual(rc, 0, err)
        self.assertIn("KSIO", err)

    def test_diff(self):
        a = synth()
        b = bytearray(a)
        b[0x059] = 0
        b[0x300] ^= 0x55
        K.set_checksum(b, self.m)
        rc, out, _ = run("diff", self.w("a", a), self.w("b", b))
        self.assertEqual(rc, 1)
        self.assertIn("iso", out)
        self.assertIn("AUTO", out)
        self.assertIn("0x300-0x300", out)
        self.assertEqual(run("diff", self.w("a", a), self.w("a2", a))[0], 0)

    def test_info(self):
        self.assertEqual(run("info")[0], 0)

    # ---- real block from a camera ----
    REAL_MAP = str(HERE / "settings-map.json")
    REAL = HERE / "testdata" / "main-2026-11-06.cur"

    def runreal(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = K.main(["--map", self.REAL_MAP, *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_real_check(self):
        rc, out, _ = self.runreal("check", str(self.REAL))
        self.assertEqual(rc, 0, out)
        self.assertEqual(bytes(self.REAL.read_bytes()[4092:]), bytes.fromhex("074490a0"))

    def test_real_round_trip(self):
        txt, out = self.d / "r.txt", self.d / "r.dat"
        self.assertEqual(self.runreal("dump", str(self.REAL), "-o", str(txt))[0], 0)
        self.assertEqual(self.runreal("apply", str(txt), "-o", str(out))[0], 0)
        self.assertEqual(out.read_bytes(), self.REAL.read_bytes())

    def test_real_info(self):
        rc, out, _ = self.runreal("info")
        self.assertEqual(rc, 0)
        self.assertIn("u32-sum-of-u16be", out)

    def test_real_merge_self_noop(self):
        out = self.d / "m.dat"
        p = str(self.REAL)
        self.assertEqual(self.runreal("merge", "--target", p, "--source", p, "-o", str(out))[0], 0)
        self.assertEqual(out.read_bytes(), self.REAL.read_bytes())


if __name__ == "__main__":
    unittest.main()
