#!/usr/bin/env python3
"""-LVFR mode 3 (liveview 1.3, 640x240 JPEG straight from the live-view ring) on the bridge side: the command builder,
the scale argument, host rate control, the rebuilt JPEG and the pull loop. SIMULATED: no USB device is opened; the
scans are made by mockcard's DC-only 4:2:2 encoder, not by the camera.

Run: python3 tools/komaset/bridge/test_komabridge_mode3.py
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import komabridge as KB  # noqa: E402
import mockcard  # noqa: E402
from test_komabridge import jpeg_segments, walk_scan  # noqa: E402


def m3_scan(scale=448, n=3):
    full = mockcard.encode_avi1_frame(640, 240, lambda bx, by: mockcard._lv_pattern(n)(bx * 4 + 2, by * 8 + 4), scale)
    return full[193:]   # the camera sends scan + EOI only


class FakeSession:
    def __init__(self, lengths=None, reencode=()):
        self.asked, self.lengths, self.reencode = [], list(lengths or []), set(reencode)

    def live_frame(self, mode, seq, scale=None):
        self.asked.append((mode, seq, scale))
        used = scale or 448
        flags = 0
        if len(self.asked) - 1 in self.reencode:
            used, flags = min(0xFFFF, used * 2), KB.LVFR_REENCODED
        n = self.lengths.pop(0) if self.lengths else 30000
        data = b"\x11" * (n - 2) + b"\xff\xd9"
        return "150 -LVFR", mockcard.lvfr_payload(3, flags, seq, 640, 240, used, data)


class FakeLink:
    def __init__(self, session):
        self.s, self.live_seq = session, 0

    def run(self, fn):
        return fn(self.s)


class Mode3Test(unittest.TestCase):
    def test_command_builder(self):
        self.assertEqual(KB.read_command("-LVFR", "3,0"), b"-LVFR 3,0\r\n")
        self.assertEqual(KB.read_command("-LVFR", f"{KB.lvfr_arg(3, 448)},9"), b"-LVFR 7171,9\r\n")
        self.assertEqual(KB.read_command("-LVFR", f"{KB.lvfr_arg(3, 0xFFFF)},9"), b"-LVFR 1048563,9\r\n")
        for bad in ("4,0", "19,0x", "20,0", "1048579,0", "03,0", "35,65536", "5,0"):
            with self.assertRaises(KB.CommandRefused, msg=bad):
                KB.read_command("-LVFR", bad)
        self.assertEqual([KB.lvfr_arg(m) for m in (0, 1, 2, 3)], [0, 1, 2, 3])
        self.assertEqual(KB.lvfr_arg(3, 448), 448 * 16 + 3)
        with self.assertRaises(ValueError):
            KB.lvfr_arg(1, 448)
        with self.assertRaises(ValueError):
            KB.lvfr_arg(3, 0x10000)

    def test_rate_control_stays_in_band(self):
        lo, hi = KB.M3_TARGET
        self.assertEqual(KB.next_m3_scale(448, (lo + hi) // 2), 448)
        self.assertGreater(KB.next_m3_scale(448, hi + 1), 448)
        self.assertLess(KB.next_m3_scale(448, lo - 1), 448)
        self.assertEqual(KB.next_m3_scale(896, 70000, reencoded=True), 896)
        self.assertEqual(KB.next_m3_scale(KB.M3_SCALE_MIN, 1000), KB.M3_SCALE_MIN)
        self.assertEqual(KB.next_m3_scale(KB.M3_SCALE_MAX, 65000), KB.M3_SCALE_MAX)
        s = 448
        for _ in range(40):                    # a scene that needs a coarser scale converges and stops moving
            s = KB.next_m3_scale(s, int(60000 * 448 / s))
        self.assertTrue(lo <= int(60000 * 448 / s) <= hi, s)

    def test_live_pull_sends_scale_and_adapts(self):
        sess = FakeSession(lengths=[60000, 60000, 30000, 10000], reencode={1})
        link = FakeLink(sess)
        frames = [KB.live_pull(link, 3) for _ in range(4)]
        self.assertEqual([a[2] for a in sess.asked][0], KB.M3_SCALE_DEFAULT)
        scales = [a[2] for a in sess.asked]
        self.assertGreater(scales[1], scales[0])          # 60 KB: coarser
        self.assertEqual(frames[1]["scale"], scales[1] * 2)
        self.assertTrue(frames[1]["reencoded"])
        self.assertEqual(scales[2], frames[1]["scale"])   # a re-encode keeps the doubled scale
        self.assertEqual(scales[3], scales[2])            # 30 KB: in band
        self.assertLess(link.m3_scale, scales[3])         # 10 KB: finer next time
        self.assertEqual([f["width"] for f in frames], [640] * 4)

    def test_mode1_pull_sends_no_scale(self):
        sess = FakeSession()
        sess.live_frame = lambda mode, seq, *rest: (sess.asked.append((mode, seq, rest)) or
                                                      ("150 -LVFR", mockcard.lvfr_payload(1, 0, seq, 320, 240, 448,
                                                                                          b"\xff\xd9")))
        KB.live_pull(FakeLink(sess), 1)
        self.assertEqual(sess.asked, [(1, 0, ())])

    def test_jpeg_is_640x240_422_and_decodes(self):
        scan = m3_scan()
        jpeg, ctype = KB.frame_image(KB.parse_lvfr(mockcard.lvfr_payload(3, 0, 1, 640, 240, 448, scan)))
        self.assertEqual(ctype, "image/jpeg")
        segs, _ = jpeg_segments(jpeg)
        sof = dict(segs)[0xC0]
        self.assertEqual((int.from_bytes(sof[1:3], "big"), int.from_bytes(sof[3:5], "big")), (240, 640))
        self.assertEqual(sof[6:], bytes.fromhex("012100021101031101"))
        self.assertEqual(walk_scan(jpeg), 1200)          # 40 x 30 MCUs of 16x8: the encoder's (h>>3)*(w>>4)
        self.assertEqual(KB.LVFR_DISPLAY_SIZE[3], (640, 480))
        if shutil.which("sips"):
            with tempfile.TemporaryDirectory() as d:
                src, dst = Path(d) / "f.jpg", Path(d) / "f.png"
                src.write_bytes(jpeg)
                subprocess.run(["sips", "-s", "format", "png", str(src), "--out", str(dst)], capture_output=True)
                self.assertTrue(dst.exists() and dst.stat().st_size > 100)

    def test_parse_mode_accepts_3(self):
        self.assertEqual(KB._parse_mode({"mode": ["3"]}), 3)
        with self.assertRaises(KB.BridgeError):
            KB._parse_mode({"mode": ["4"]})


if __name__ == "__main__":
    unittest.main()
