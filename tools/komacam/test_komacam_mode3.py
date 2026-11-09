"""komacam with -LVFR mode 3 (liveview 1.3: 640x240 JPEG straight from the live-view ring, non-square pixels) against
komabridge's SIMULATED camera. The mock body here answers mode 3 with a DC-only 4:2:2 scan of a test card drawn the way
the ring holds a picture: a 4:3 scene in 240 lines, so a circle is twice as wide as tall in the raw 640x240 frame.
Nothing here says anything about a real GR DIGITAL.

Run:  python3 -m pytest -q tools/komacam/test_komacam_mode3.py
"""
from __future__ import annotations

import io
import re

import pytest

from test_komacam import Rig, StampedBody, komabridge, komacam  # noqa: F401  (sys.path set up there)
from mockcard import encode_avi1_frame, lvfr_payload

CX, CY, R = 320, 120, 96          # display-space circle centre/radius on a 640x480 4:3 card; ring lines are y / 2


def card_luma(x, y_line):
    """Luma at ring pixel (x, line): a bright circle of radius R in 4:3 display space on a dark ground."""
    dy = (y_line * 2 + 1) - CY * 2
    return 235 if (x - CX) ** 2 + dy ** 2 <= R * R else 20


class Mode3Body(StampedBody):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.m3_args: list = []
        self.m3_scan = encode_avi1_frame(640, 240, lambda bx, by: card_luma(bx * 8 + 4, by * 8 + 4), 448)[193:]

    def lvfr(self, argument):
        m = re.fullmatch(r"(\d{1,7}),(\d{1,5})", argument)
        if m and int(m.group(1)) > 2 and int(m.group(1)) & 0xF == 3:
            self.m3_args.append(int(m.group(1)))
            reply, payload = super().lvfr(f"1,{m.group(2)}")     # the mock's gate (remote state, arm, shot)
            if payload is None:
                return reply, None
            scale = int(m.group(1)) >> 4 or 448
            return reply, lvfr_payload(3, 0, int(m.group(2)), 640, 240, scale, self.m3_scan)
        return super().lvfr(argument)


def _extent(img, horizontal):
    """Bright run length through the image centre (pixels > 128)."""
    from PIL import Image  # noqa: F401
    g = img.convert("L")
    w, h = g.size
    if horizontal:
        return sum(1 for x in range(w) if g.getpixel((x, h // 2)) > 128)
    return sum(1 for y in range(h) if g.getpixel((w // 2, y)) > 128)


def test_parser_and_wiring():
    a = komacam.build_parser().parse_args(["--mode", "3"])
    assert a.mode == 3 and komacam.MODE_NATIVE_SIZE[3] == (640, 480) and komacam.MODE_LINE_SCALE[3] == 2.0
    with pytest.raises(SystemExit):
        komacam.build_parser().parse_args(["--mode", "2"])


@pytest.mark.parametrize("fit,size,content", [("letterbox", (1280, 720), (960, 720)),
                                              ("letterbox", (640, 480), (640, 480)),
                                              ("crop", (1280, 720), (1280, 960))])
def test_mode3_stream_is_shown_at_4_3(fit, size, content):
    from PIL import Image
    body = Mode3Body()
    with Rig(body, argv=["--mode", "3", "--fit", fit, "--size", f"{size[0]}x{size[1]}"]) as rig:
        assert rig.wait(lambda: rig.app.hub.frames > 3, 15)
        f = rig.app.hub.latest
        raw = Image.open(io.BytesIO(f.data))
        assert raw.size == (640, 240)                              # what the camera sends
        nat = Image.open(io.BytesIO(rig.app.proc.native_jpeg(f)))
        assert nat.size == (640, 480)                              # native output: square pixels, 4:3
        hd = Image.open(io.BytesIO(rig.app.proc.hd_jpeg(f)))
        assert hd.size == size
        # the test card's circle is round again after the stretch: diameter 2R in 640x480 space, scaled to content
        k = content[0] / 640
        hw, vw = _extent(hd, True), _extent(hd, False)
        assert abs(hw - 2 * R * k) <= 10 * k and abs(vw - 2 * R * k) <= 10 * k, (hw, vw)
        if fit == "letterbox" and size == (1280, 720):
            assert hd.convert("L").getpixel((40, 360)) < 8         # 4:3 in 16:9: side bars
        assert body.m3_args and all(a & 0xF == 3 for a in body.m3_args)   # the bridge sent mode 3 (+ its scale)
        assert {a >> 4 for a in body.m3_args} <= set(range(komabridge.M3_SCALE_MIN, komabridge.M3_SCALE_MAX + 1))


def test_mode1_untouched_by_line_scale():
    from PIL import Image
    with Rig(StampedBody(), argv=["--mode", "1"]) as rig:
        assert rig.wait(lambda: rig.app.hub.frames > 3, 10)
        f = rig.app.hub.latest
        assert rig.app.proc.line_scale == 1.0
        assert rig.app.proc.native_jpeg(f) is f.data
        assert Image.open(io.BytesIO(f.data)).size == (320, 240)
