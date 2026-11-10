"""SIMULATED camera for komabridge --mock and the tests. Nothing here says anything about a real GR DIGITAL.

It reuses grdusb's MockTransport (framing, -NLST, RETR, -STOR, bring-up) and adds the four read verbs the bridge also
uses (PWD, -INFO, -FDAT) plus -CCTL poff, answered with the reply texts seen on hardware. The card holds tools/komaset/web/testdata/demo-main.cur as PARAM.CUR
and three small grayscale JPEGs drawn here (stdlib only), or with MockBody(samples=...) the sample frames
(load_samples). Every photo carries a SIMULATED MakerNote firmware tag (maker_app1): Rev0240, or MOCK_TAG with koma=True.

poff is modelled as: reply 200, the camera leaves the bus, and after MockBody.reboot_s seconds it comes back. On the
way back it runs the boot-time PARAM.DAT import (envelope check, copy, rename to PARAM.OK or PARAM.BAD, rewrite
PARAM.CUR). Whether a real camera returns to a USB session after poff is unknown; both are simulated here only so the
page flow can be exercised.

-CCTL remtin / diaset / keyset / remtout follow the accept rules of docs/internals/07-usb-protocol.md (remtin only from state 0,
diaset needs a non-zero state and N < 9, keyset needs state 2, remtout needs a non-zero state; 451 otherwise). With
MockBody(tether=True) (komabridge --mock-tether) the Koma `tether` feature is simulated: a full press (keyset,1,27 or
keyset,3,27) with the virtual dial on 4 (still) adds a new RIMGnnnn.JPG about shot_delay_s later. Without it every
-CCTL answers 200 and nothing fires, as on the stock camera.

-LVFR <mode>,<seq> (the Koma `liveview` feature) answers 150, one
data frame holding the 16-byte LVFR header plus the frame, then 250, and 451 unless remote control is in state 2 (after
diaset) with no shot being written. Frames follow the wall clock at 30 per second (a 30-frame loop of a moving disc).
Mode 1 frames are real baseline 4:2:2 JPEG scans, made here with the firmware's 193-byte header layout and then
stripped of it, so the bridge has to rebuild that header; the encoder is DC-only (flat 8x8 blocks), Annex K tables.
Mode 0 frames are 160x120 raw Y bytes.

-LVFR 2,<seq> (the OSD page pull) answers 150 in every remote state, stock USB state
included, and while the monitor is stopped. The page is one of three real OSD pages dumped from the tools/grdui
emulator of the 2.40 firmware (testdata/osd-*.bin, 360x240 at 4 bpp, see testdata/osd-pages.json): the shooting screen,
the menu after MENU/OK, and the menu after a DOWN. A tiny menu model picks the page: ENTER (23) opens the menu and
stops the monitor (mode 0/1 then answer 451), DOWN (12) / UP (11) move between the two menu pages, DISP (22) closes
the menu. That model is a stand-in for the test, not how the camera's menu works. The palette is appended (16 raw
register words, scale 16) unless MockBody(osd_palette="fixed") sends scale 0.

-KSET <off>,<val> / -KGET <off>,<len> (the Koma live-settings verbs)
work on MockBody.ram, the simulated RAM copy of the 4096-byte settings block: demo-main.cur with a Koma 0.3 recipe store
written into it (KOMA header, 'RCP6' store with all 20 records valid at their defaults, FX table OFF, MONO active and
its values in the EDIT RECIPE rows), as a camera running Koma 0.3 would hold it. -KSET answers "200 -KSET off=val"
inside the window (0x8b5..0x8cf, 0x910..0xa27; all of 0x8d0..0x8ff refused), "451 -KSET range" outside it, "451 -KSET
value" above 255 and 501 on bad syntax. The ksettings stock whitelist (EV 0x053, ISO 0x059, WB 0x054, WB detail
0x0a6, metering 0x050, IMG SET 0x030, 0x031..0x036, TE 0x1a9 / 0x1aa) takes only the stock values ("451 -KSET value"
otherwise) and answers "451 -KSET busy" unless remote control is in state 2 with the stand-in menu closed and no shot
being written; -KGET answers 150, one data frame, 250 (451 -KGET range outside the block). -KSTA (ksettings v1.3,
diagnostic) answers 150, one frame in the bridge's KSTA layout, 250, in any state: remote / live / tick words follow the
mock's model, the rest are fixed stand-ins (MockBody.ksta_overrides forces values; MockBody.ksta = False -> 501). The RAM
survives a simulated poff (the camera saves it at power-off); a PARAM.DAT import replaces it. The mock does not
re-export it to PARAM.CUR. Live view follows it: the mode 0/1 frames take the active recipe's CONTRAST (p1) and KEY
(p2) from its record (a cheap stand-in for the camera's preview feature), and get brighter / darker with the EV byte
0x053, so the Looks and Exposure demos close the loop.
"""
from __future__ import annotations

import re
import struct
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from grdusb import mock as grd_mock  # noqa: E402
from grdusb.transport import TransportError  # noqa: E402

DEMO_CUR = HERE.parent / "web" / "testdata" / "demo-main.cur"
OSD_DIR = HERE / "testdata"
NOTICE = grd_mock.NOTICE


# ---------------------------------------------------------------- tiny baseline JPEG (grayscale, DC-only)

def _huff_codes(bits, vals):
    codes, code, k = {}, 0, 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            codes[vals[k]] = (code, length)
            code += 1
            k += 1
        code <<= 1
    return codes


_DC_BITS = [0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]  # ITU T.81 Table K.3 (luminance DC)
_DC_VALS = list(range(12))
_AC_BITS = [1] + [0] * 15  # one symbol: EOB (0x00), code "0". Every block is flat, so EOB is all we ever write.
_AC_VALS = [0x00]


def encode_gray_jpeg(width: int, height: int, pixel) -> bytes:
    """Baseline JPEG, one 8-bit component, each 8x8 block flat at the block's mean of pixel(x, y). Valid and tiny."""
    dc = _huff_codes(_DC_BITS, _DC_VALS)
    eob = _huff_codes(_AC_BITS, _AC_VALS)[0x00]
    out, acc, nbits = bytearray(), 0, 0

    def put(value, length):
        nonlocal acc, nbits
        acc = (acc << length) | (value & ((1 << length) - 1))
        nbits += length
        while nbits >= 8:
            nbits -= 8
            byte = (acc >> nbits) & 0xff
            out.append(byte)
            if byte == 0xff:
                out.append(0)
        acc &= (1 << nbits) - 1

    previous = 0
    for by in range(0, height, 8):
        for bx in range(0, width, 8):
            total = sum(pixel(min(bx + i, width - 1), min(by + j, height - 1)) for j in range(8) for i in range(8))
            value = round(total / 64) - 128  # DC with quantiser 8 is the block mean, level-shifted
            diff, previous = value - previous, value
            size = abs(diff).bit_length()
            code, length = dc[size]
            put(code, length)
            if size:
                put(diff if diff > 0 else diff + (1 << size) - 1, size)
            put(*eob)
    if nbits:
        put((1 << (8 - nbits)) - 1, 8 - nbits)

    def segment(marker, body):
        return struct.pack(">BBH", 0xff, marker, len(body) + 2) + body

    head = b"\xff\xd8"
    head += segment(0xe0, b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0")
    head += segment(0xdb, b"\0" + bytes([8] * 64))
    head += segment(0xc0, struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00")
    head += segment(0xc4, b"\x00" + bytes(_DC_BITS) + bytes(_DC_VALS))
    head += segment(0xc4, b"\x10" + bytes(_AC_BITS) + bytes(_AC_VALS))
    head += segment(0xda, b"\x01\x01\x00\x00\x3f\x00")
    return head + bytes(out) + b"\xff\xd9"


def _clamp(v):
    return max(0, min(255, int(v)))


def _scene_horizon(x, y):
    sky = 200 - y * 0.5
    sun = 255 if (x - 230) ** 2 + (y - 70) ** 2 < 28 ** 2 else 0
    sea = 70 + 25 * ((x // 24 + y // 8) % 2) if y > 150 else 0
    return _clamp(sea if y > 150 else max(sky, sun))


def _scene_street(x, y):
    buildings = [(0, 60, 40), (60, 120, 90), (120, 170, 20), (170, 250, 70), (250, 320, 50)]
    for left, right, top in buildings:
        if left <= x < right:
            if y < top:
                return _clamp(230 - y // 3)
            window = (x // 16 + y // 16) % 3 == 0
            return _clamp((60 if window else 35) + (right - left) // 4)
    return 128


def _scene_portrait(x, y):
    r2 = (x - 160) ** 2 + (y - 110) ** 2
    if r2 < 60 ** 2:
        return _clamp(210 - r2 // 60)
    if y > 170 and abs(x - 160) < 110 - (240 - y) // 3:
        return 50
    return _clamp(100 + (x + y) // 6)


PHOTOS = (  # name, scene, -FDAT date as the camera prints it
    ("RIMG0001.JPG", _scene_horizon, "2026:11:05 07:12:40"),
    ("RIMG0002.JPG", _scene_street, "2026:11:06 14:03:11"),
    ("RIMG0003.JPG", _scene_portrait, "2026:11:06 18:45:02"),
)
PHOTO_W, PHOTO_H = 320, 240


def _scene_shot(n):
    """A new tethered shot: a soft diagonal gradient with a dark disc, varied by the shot number."""
    cx, cy = 80 + (n * 47) % 160, 70 + (n * 31) % 100

    def pixel(x, y):
        if (x - cx) ** 2 + (y - cy) ** 2 < 40 ** 2:
            return 30
        return _clamp(60 + (x + y + n * 20) % 400 * 0.45)
    return pixel
FOLDER = "/EXT0/DCIM/100RICOH"


# ---------------------------------------------------------------- the firmware tag in a JPEG's MakerNote
# Layout as tools/spare_test/metadata.py and the app's onboardExif.js read it: APP1
# 'Exif\0\0', big-endian TIFF, IFD0 -> EXIF IFD (0x8769) -> MakerNote (0x927c) starting 'Ricoh\0\0\0' with a
# big-endian IFD at +8, value offsets from the TIFF start; note tag 0x2 is ASCII 'Rev%04x' ('Rev0240' on the official
# 2.40; '240K2' on earlier Koma builds). SIMULATED here: the mock's photos carry
# the official tag, or the Koma tag (MOCK_TAG, below) when the mock plays a Koma camera, so the app's classifier sees a
# real body. Nothing else of a camera's EXIF is made up.
OFFICIAL_TAG = b"Rev0240"
TAG_FIELD = 8  # the tag's ASCII field on the camera: 8 bytes, NUL-padded


def maker_app1(tag: bytes) -> bytes:
    """An APP1 Exif segment holding only IFD0 -> EXIF IFD -> the Ricoh MakerNote with tag 0x2."""
    ifd0 = 8
    exif = ifd0 + 2 + 12 + 4
    note = exif + 2 + 12 + 4
    note_ifd = 8 + 2 + 12 + 4
    value = note + note_ifd
    body = (struct.pack(">HHHI", 1, 0x8769, 4, 1) + struct.pack(">I", exif) + b"\0\0\0\0"
            + struct.pack(">HHHI", 1, 0x927C, 7, note_ifd + TAG_FIELD) + struct.pack(">I", note) + b"\0\0\0\0"
            + b"Ricoh\0\0\0" + struct.pack(">HHHI", 1, 0x0002, 2, TAG_FIELD) + struct.pack(">I", value) + b"\0\0\0\0"
            + tag[:TAG_FIELD - 1].ljust(TAG_FIELD, b"\0"))
    tiff = b"MM\0*" + struct.pack(">I", ifd0) + body
    return b"\xff\xe1" + struct.pack(">H", len(tiff) + 8) + b"Exif\0\0" + tiff


def _maker_tag_span(jpeg: bytes):
    """(start, length) of MakerNote tag 0x2's ASCII field inside `jpeg`, or None."""
    i = 2
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF:
        marker, length = jpeg[i + 1], int.from_bytes(jpeg[i + 2:i + 4], "big")
        if marker == 0xE1 and jpeg[i + 4:i + 10] == b"Exif\0\0":
            base, t = i + 10, jpeg[i + 10:i + 2 + length]
            o = ">" if t[:2] == b"MM" else "<"
            try:
                def entries(off, order=o):
                    n = struct.unpack_from(order + "H", t, off)[0]
                    return {struct.unpack_from(order + "H", t, off + 2 + 12 * k)[0]: off + 2 + 12 * k for k in range(n)}
                ifd0 = entries(struct.unpack_from(o + "I", t, 4)[0])
                exif = entries(struct.unpack_from(o + "I", t, ifd0[0x8769] + 8)[0])
                at = struct.unpack_from(o + "I", t, exif[0x927C] + 8)[0]
                if t[at:at + 5] != b"Ricoh":
                    return None
                e = entries(at + 8, ">")[0x0002]
                kind, count = struct.unpack_from(">HI", t, e + 2)
                if kind != 2:
                    return None
                start = e + 8 if count <= 4 else struct.unpack_from(">I", t, e + 8)[0]
                return base + start, count
            except (KeyError, struct.error):
                return None
        if marker in (0xDA, 0xD9):
            return None
        i += 2 + length
    return None


def with_maker_tag(jpeg: bytes, tag: bytes) -> bytes:
    """`jpeg` carrying firmware tag `tag`: rewritten in place when it has a Ricoh MakerNote (a real camera's file),
    otherwise with maker_app1 inserted after SOI (the mock's own drawings)."""
    span = _maker_tag_span(jpeg)
    if span:
        start, count = span
        out = bytearray(jpeg)
        out[start:start + count] = tag[:count - 1].ljust(count, b"\0")
        return bytes(out)
    return jpeg[:2] + maker_app1(tag) + jpeg[2:]


# ---------------------------------------------------------------- sample frames as the mock's card and live view
# tools/komaapp/design/samples: R00nnnnn.JPG photos taken on a GR DIGITAL (komabridge --mock serves them as
# DCIM unless --mock-patterns), and live-0n.jpg frames in the movie recorder's format (AVI1, 320x240 4:2:2 at scale
# LV_SCALE, Annex K tables), served as mode 1 live view one after another. A frame whose tables are not that format is
# left out, so the bridge's rebuilt header always decodes what it is sent.
SAMPLES_DIR = TOOLS / "komaapp" / "design" / "samples"
SAMPLE_HOLD_S = 4.0  # each live sample frame stays this long (a held camera, not a slideshow at 30 fps)


def _exif_date(jpeg: bytes) -> str | None:
    """DateTimeOriginal (0x9003) as 'YYYY:MM:DD HH:MM:SS', or None."""
    m = re.search(rb"(\d{4}:\d\d:\d\d \d\d:\d\d:\d\d)\0", jpeg[:65536])
    return m.group(1).decode() if m else None


def load_samples(folder: Path | None = None):
    """([(name, bytes, -FDAT date)], [mode 1 scans]) from the samples folder; ([], []) when it is not there."""
    folder = Path(folder or SAMPLES_DIR)
    if not folder.is_dir():
        return [], []
    photos = []
    for k, p in enumerate(sorted(folder.glob("*.JPG"))):
        if not re.fullmatch(r"[A-Z0-9_]{1,8}\.JPG", p.name):
            continue
        data = p.read_bytes()
        photos.append((p.name, data, _exif_date(data) or f"2026:11:0{1 + k % 8} 12:00:00"))
    frames = []
    for p in sorted(folder.glob("live-*.jpg")):
        scan = _avi1_scan(p.read_bytes())
        if scan is not None:
            frames.append(scan)
    return photos, frames


def _avi1_scan(jpeg: bytes) -> bytes | None:
    """The scan + EOI of a live sample when its DQT and SOF are what avi1_header() writes for LV_SCALE, else None."""
    want = avi1_header(LV_SCALE, LV_JPEG_W, LV_JPEG_H)
    dqt = want[want.index(b"\xff\xdb"):want.index(b"\xff\xc0")]
    sof = want[want.index(b"\xff\xc0"):want.index(b"\xff\xda")]
    if dqt not in jpeg or sof not in jpeg:
        return None
    i = jpeg.find(b"\xff\xda")
    if i < 0:
        return None
    return jpeg[i + 2 + int.from_bytes(jpeg[i + 2:i + 4], "big"):]


# ---------------------------------------------------------------- simulated live view (-LVFR)
# Written independently of komabridge (no import), from the same firmware facts, so the tests can compare the two.
LV_FPS, LV_LOOP = 30, 30
LV_JPEG_W, LV_JPEG_H = 320, 240
LV_Y_W, LV_Y_H = 160, 120
LV_SCALE = 448  # u16 scale in the LVFR header (the camera default); luma DC quantiser = ((448*16>>9)+1)>>1 = 7
_QT_LUMA_ZZ = bytes.fromhex("100b0c0e0c0a100e0d0e1211101318281a181616183123251d283a333d3c3933"
                            "383740485c4e404457453738506d51575f626768673e4d71797064785c656763")  # 0x18c3b0
_QT_CHROMA_ZZ = bytes.fromhex("1112121815182f1a1a2f634238426363") + b"\x63" * 48  # 0x18c3f0
_DC_CHROMA_BITS = [0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]  # T.81 Table K.4
_EOB_LUMA, _EOB_CHROMA = (0b1010, 4), (0b00, 2)  # EOB codes in Tables K.5 and K.6


def _scaled(base, scale):
    return bytes(max(1, min(255, ((scale * b >> 9) + 1) >> 1)) for b in base) if scale else bytes(base)


def avi1_header(scale, width, height) -> bytes:
    """The 193-byte per-frame header of the movie recorder (0x8986c): SOI, APP0 AVI1, DRI 0, DQT, SOF0 4:2:2, SOS."""
    return (b"\xff\xd8" + b"\xff\xe0\x00\x10AVI1" + b"\0" * 10 + b"\xff\xdd\x00\x04\x00\x00"
            + b"\xff\xdb\x00\x84\x00" + _scaled(_QT_LUMA_ZZ, scale) + b"\x01" + _scaled(_QT_CHROMA_ZZ, scale)
            + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03\x01\x21\x00\x02\x11\x01\x03\x11\x01"
            + b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00")


def encode_avi1_frame(width, height, block_mean, scale) -> bytes:
    """A whole movie-style frame: avi1_header + a baseline 4:2:2 scan (MCU = Y0 Y1 Cb Cr, 16x8) + EOI. Each luma 8x8
    block is flat at block_mean(bx, by) (block coordinates); chroma is neutral grey."""
    dc_l, dc_c = _huff_codes(_DC_BITS, _DC_VALS), _huff_codes(_DC_CHROMA_BITS, _DC_VALS)
    q0 = _scaled(_QT_LUMA_ZZ, scale)[0]
    out, acc, nbits = bytearray(), 0, 0

    def put(value, length):
        nonlocal acc, nbits
        acc = (acc << length) | (value & ((1 << length) - 1))
        nbits += length
        while nbits >= 8:
            nbits -= 8
            byte = (acc >> nbits) & 0xff
            out.append(byte)
            if byte == 0xff:
                out.append(0)
        acc &= (1 << nbits) - 1

    def dc(diff, table):
        size = abs(diff).bit_length()
        put(*table[size])
        if size:
            put(diff if diff > 0 else diff + (1 << size) - 1, size)

    previous = 0
    for my in range(height // 8):
        for mx in range(width // 16):
            for k in (0, 1):
                value = round(8 * (block_mean(2 * mx + k, my) - 128) / q0)  # F(0,0) = 8 x level-shifted mean
                dc(value - previous, dc_l)
                previous = value
                put(*_EOB_LUMA)
            for _ in (0, 1):  # Cb, Cr: DC 0 (neutral), predictor stays 0
                dc(0, dc_c)
                put(*_EOB_CHROMA)
    if nbits:
        put((1 << (8 - nbits)) - 1, 8 - nbits)
    return avi1_header(scale, width, height) + bytes(out) + b"\xff\xd9"


def _lv_pattern(n):
    """Frame n of the loop: a sky-to-ground gradient, a bright disc moving left to right and a dark bar moving down."""
    cx, bar = 20 + n * 280 / LV_LOOP, (n * 8) % 240

    def lum(x, y):  # x, y in 320x240 coordinates
        if (x - cx) ** 2 + (y - 100) ** 2 < 36 ** 2:
            return 240
        if bar <= y < bar + 12:
            return 30
        return _clamp(190 - y * 0.55 + 20 * ((int(x) // 40 + int(y) // 40) % 2))
    return lum


# ---------------------------------------------------------------- simulated RAM block with a Koma 0.3 recipe store
# Independent copy of tools/koma/features/bwrecipes.py RECIPES (p0..p8, kind): kind low 2 bits = type (0 B&W, 1 colour,
# 2 SELECT), bits 4..6 = the colour look. test_komabridge.py compares it with bwrecipes.
RECIPE_DEFAULTS = (
    (0, 4, 4, 4, 4, 0, 0, 2, 3, 0), (3, 6, 4, 2, 5, 3, 0, 2, 4, 0), (3, 4, 4, 4, 4, 0, 0, 2, 4, 0),
    (2, 6, 4, 4, 4, 0, 3, 0, 3, 0), (4, 4, 4, 4, 4, 0, 0, 2, 1, 0), (5, 5, 4, 4, 4, 1, 0, 2, 3, 0),
    (6, 4, 4, 4, 4, 0, 0, 2, 2, 0), (4, 5, 3, 4, 4, 0, 0, 2, 3, 0), (3, 6, 2, 4, 4, 3, 0, 2, 3, 0),
    (1, 1, 4, 7, 2, 0, 0, 2, 2, 0), (0, 4, 4, 4, 4, 0, 0, 3, 3, 2), (1, 6, 6, 1, 1, 2, 3, 3, 2, 1),
    (1, 3, 5, 6, 4, 2, 3, 3, 2, 17), (7, 4, 4, 4, 4, 0, 0, 3, 3, 33), (1, 2, 5, 8, 2, 2, 5, 3, 2, 49),
    (1, 2, 6, 6, 1, 3, 6, 2, 2, 65), (1, 7, 4, 2, 3, 6, 4, 3, 2, 81), (0, 4, 4, 4, 4, 0, 0, 2, 3, 0),
    (0, 4, 4, 4, 4, 0, 0, 2, 3, 0), (0, 4, 4, 4, 4, 0, 0, 2, 3, 0))
RCP_STORE, RCP_REC, RCP_FX, RCP_ACTIVE, RCP_EDIT = 0x900, 0x910, 0xA00, 0x8C0, 0x8C1
KSET_RANGES = ((0x8B5, 0x8CF), (0x910, 0xA27))  # written here independently of komabridge (0x8d0..0x8ff refused)
# The ksettings stock whitelist, independent copy: off -> stock values.
KSET_STOCK = {0x053: set(range(1, 26, 2)), 0x059: {0, 1, 2, 4, 6, 7, 8}, 0x054: {0, 1, 2, 3, 4, 5, 7},
              0x0A6: set(range(17)), 0x050: {0, 1, 2}, 0x030: set(range(7)), 0x1A9: {1, 2, 3, 4, 6},
              0x1AA: set(range(1, 6)), **{o: set(range(1, 6)) for o in range(0x031, 0x037)}}
EV_OFF = 0x053


def record_bytes(params_kind) -> bytes:
    """A valid 12-byte record: p0..p8, kind, 0xa5, xor(bytes 0..9) ^ 0x5a."""
    head = bytes(params_kind)
    x = 0x5A
    for b in head:
        x ^= b
    return head + bytes([0xA5, x])


def koma_ram(block: bytes) -> bytearray:
    ram = bytearray(block)
    ram[0x8B0:0x8B5] = b"KOMA\x01"
    ram[RCP_STORE:RCP_STORE + 16] = b"RCP6" + bytes([6, 1, 1, 20, 0, 0, 0, 0, 0, 1, 0, 0])
    for i, row in enumerate(RECIPE_DEFAULTS):
        ram[RCP_REC + 12 * i:RCP_REC + 12 * i + 12] = record_bytes(row)
    ram[RCP_FX:RCP_FX + 20] = bytes(20)
    ram[RCP_ACTIVE] = 1
    ram[RCP_EDIT:RCP_EDIT + 9] = bytes(RECIPE_DEFAULTS[0][:9])
    ram[0x8CA:0x8CE] = bytes(4)  # SAVE TO CUSTOM, RESET, GRAIN, VIGNETTE
    return ram


KSIO_OFF, KSIO_TAG_OFF, KSIO_STATUS = 0x8D0, 0x8D5, 0x8DC
MOCK_TAG = b"240K1.0"     # a Koma 1.0 camera's firmware tag (7 bytes)
# An earlier (pre-1.0) build's tag, NUL-padded to 7. The app shows such a camera as "An earlier Koma build" (--mock-earlier).
EARLIER_MOCK_TAG = b"240K2\0\0"


def block_checksum(blk) -> bytes:
    return (sum(int.from_bytes(blk[i:i + 2], "big") for i in range(0, 0xFFC, 2)) & 0xFFFFFFFF).to_bytes(4, "big")


def koma_cur(block: bytes, layout: int = 2, tag: bytes = MOCK_TAG) -> bytes:
    """SIMULATED PARAM.CUR of a camera running Koma with settingsio: koma_ram's block (KOMA header, look store, MONO
    active), MENU LAYOUT `layout` (0 5-TAB, 1 STOCK+KOMA, 2 GR II), KOMA FEATURES FULL, the KSIO sub-header with no
    import yet (status 0), checksum set."""
    cur = koma_ram(block)
    import json
    for f in json.loads((HERE.parent / "settings-map.json").read_text())["fields"]:
        if f.get("page") == "koma" and f.get("type") == "enum" and not f["name"].startswith("recipe_") \
                and isinstance(f.get("default"), int):
            cur[int(f["offset"], 16)] = f["default"]  # the Koma rows at their defaults, as a fresh install has them
    cur[0x8BB], cur[0x8BC] = layout, 0
    cur[KSIO_OFF:KSIO_OFF + 5] = b"KSIO\x01"
    cur[KSIO_TAG_OFF:KSIO_TAG_OFF + 7] = tag.ljust(7, b"\0")[:7]
    cur[KSIO_STATUS] = 0
    cur[0xFFC:] = block_checksum(cur)
    return bytes(cur)


def active_tone(ram) -> tuple[int, int]:
    """(CONTRAST, KEY) steps 0..8 of the active recipe: its record if valid, else its default; (4, 4) with none."""
    rid = ram[RCP_ACTIVE]
    if not 1 <= rid <= 20:
        return 4, 4
    rec = ram[RCP_REC + 12 * (rid - 1):RCP_REC + 12 * rid]
    x = 0x5A
    for b in rec[:10]:
        x ^= b
    if rec[10] == 0xA5 and rec[11] == x and rec[1] <= 8 and rec[2] <= 8:
        return rec[1], rec[2]
    return RECIPE_DEFAULTS[rid - 1][1], RECIPE_DEFAULTS[rid - 1][2]


def lvfr_payload(mode, flags, seq, width, height, scale, data) -> bytes:
    return struct.pack(">4sBBHHHHH", b"LVFR", mode, flags, seq, width, height, len(data), scale) + data


_OSD_PAGES: dict = {}


def osd_page(name: str) -> tuple[int, int, bytes, list]:
    """(width, height, 4 bpp rows, raw palette 0..15) of a stored emulator OSD page: ready, menu or down."""
    if name not in _OSD_PAGES:
        import json
        meta = json.loads((OSD_DIR / "osd-pages.json").read_text())
        page = meta["pages"][name]
        _OSD_PAGES[name] = (meta["width"], meta["height"], (OSD_DIR / page["file"]).read_bytes(),
                            page.get("palette_raw", meta["palette_raw"]))
    return _OSD_PAGES[name]


def _dirent(name: str, attr: int, size: int, stamp: str = "2026:11:06 12:00:00") -> bytes:
    stem, _, ext = name.partition(".")
    record = bytearray(grd_mock._dirent(stem, ext, attr, size))
    date, clock = stamp.split(" ")
    y, mo, d = (int(p) for p in date.split(":"))
    hh, mi, ss = (int(p) for p in clock.split(":"))
    struct.pack_into("<HH", record, 0x16, (hh << 11) | (mi << 5) | (ss // 2), ((y - 1980) << 9) | (mo << 5) | d)
    return bytes(record)


# ---------------------------------------------------------------- the simulated body

class MockBody:
    """One simulated camera: its card, its power state and the transports opened on it. Thread-safe enough for the
    bridge, which serialises camera access behind one lock anyway."""

    def __init__(self, reboot_s: float = 4.0, returns_after_poff: bool = True, version: str = "02.40",
                 clock=time.monotonic, tether: bool = False, shot_delay_s: float = 1.5, grow_s: float = 0.6,
                 koma: bool = False, samples: Path | None = None, koma_tag: bytes = MOCK_TAG):
        self.reboot_s = reboot_s
        self.returns_after_poff = returns_after_poff
        self.version = version
        self.clock = clock
        self.lock = threading.Lock()
        # koma=True: PARAM.CUR is a Koma camera's (koma_cur); default the stock demo block, as the older tests expect
        self.koma_tag = koma_tag
        self.card = {"/EXT0/PARAM.CUR": koma_cur(DEMO_CUR.read_bytes(), tag=koma_tag) if koma else DEMO_CUR.read_bytes()}
        self.photo_dates = {}
        # the firmware tag every photo carries: the Koma camera's or the official 2.40's (SIMULATED, see maker_app1)
        self.maker_tag = koma_tag.rstrip(b"\0") if koma else OFFICIAL_TAG
        # samples: the sample frames as the card and the live view (load_samples), else the drawings here
        self.sample_photos, self.lv_samples = load_samples(samples) if samples else ([], [])
        if self.sample_photos:
            listing = [(name, data, date) for name, data, date in self.sample_photos]
        else:
            listing = [(name, encode_gray_jpeg(PHOTO_W, PHOTO_H, scene), date) for name, scene, date in PHOTOS]
        for name, data, date in listing:
            self.card[f"{FOLDER}/{name}"] = with_maker_tag(data, self.maker_tag)
            self.photo_dates[f"{FOLDER}/{name}"] = date
        self.off_until = None  # monotonic time when a poff reboot finishes; inf when it stays off
        self.poff_count = 0
        self.boot_log: list[str] = []
        self.transports: list[BridgeMockTransport] = []
        # -CCTL remote control (shared by every transport, like the camera's RAM)
        self.tether = tether
        self.shot_delay_s = shot_delay_s
        self.grow_s = grow_s     # a new shot's file grows from a few bytes to full size over this long
        self.info_log: list = []  # (path, size) at each -INFO
        self.growing: dict = {}  # path -> (start, final bytes, folder_entries index, stamp)
        self.remote = 0          # -CCTL state 0x1bd2ec: 0 off, 1 after remtin, 2 after diaset
        self.dial = 9            # virtual dial; 9 = injection off
        self.keys: set[int] = set()  # injected keys currently held
        self.pending: list[tuple[float, str]] = []  # (due, name) shots being written
        self.fail_keys: set[str] = set()  # test hook: these -CCTL arguments answer 451
        self.folder_entries = [_dirent(name, 0x20, len(self.card[f"{FOLDER}/{name}"]), date) for name, _, date in listing]
        self.next_number = len(PHOTOS) + 1
        self.lv_cache: dict = {}  # (mode, n) -> frame bytes
        self.lv_scale = LV_SCALE
        self.lv_truncate = False  # test hook: set the truncated flag on every frame
        self.lv_epoch = clock()
        self.lv_off_until = 0.0  # the monitor restarts after a shot (rec-review): -LVFR answers 451 until then
        self.osd_screen = "ready"  # ready (shooting screen, monitor on), menu or down (menu open, monitor stopped)
        self.osd_palette = "appended"  # or "fixed" (scale 0)
        self.key_log: list = []    # (action, code) of every accepted keyset, for the tests
        self.ram = koma_ram(self.card["/EXT0/PARAM.CUR"])  # the settings block in RAM (-KSET / -KGET)
        self.kset_log: list = []   # (off, val) of every accepted -KSET
        # test hooks modelling hardware behaviour: after diaset the first lvfr_451_polls -LVFR 0/1 pulls answer
        # 451 (USB app state 9 until a key press), and a new shot shows in -INFO at once but in -NLST only nlst_lag_s
        # later (-NLST lagging within one USB session)
        self.lvfr_451_polls = 0
        self.lvfr_left = 0
        # remtin/diaset,4 can answer 200 and still not take; the next
        # diaset_duds diaset commands do that (-LVFR stays 451 and keyset presses fire nothing until a diaset takes)
        self.diaset_duds = 0
        self.armed = True
        self.nlst_lag_s = 0.0
        self.lagged: list = []     # (visible_at, name, stamp) shots on the card but not yet listed
        self.kset_busy_after_s = 0.0  # test hook: after a stock -KSET, busy this long (hardware: an EV change)
        self.kset_busy_until = 0.0
        self.ksta = bool(tether)   # -KSTA known (ksettings v1.3); False: 501 (stock, or an older Koma build)
        self.ksta_overrides: dict = {}  # test hook: name -> value forced into the -KSTA frame
        # SIMULATED shot timeline in -KSTA (measured on a camera: cam_state 6 on the half-press, 9 during capture and
        # write, back to 2 about 2.5 s after the press): after a full press cam_state is 6 for ksta_busy_start_s, then 9,
        # then 2 at shot_delay_s + grow_s + 0.1 s (about 2.2 s by default). monitor_run follows live view as before.
        self.ksta_busy_start_s = 0.3
        self.shot_ksta_t0 = None   # clock() of the last full press that fired a shot
        self.ksta_log: list = []   # (clock, cam_state) of every -KSTA answer
        # hardware timings (hw_timings() / komabridge --mock-hw); all off by default, so the default model
        # and its tests stay as they were. arm_*_s: after remtin + diaset the monitor needs this long before -LVFR 0/1
        # answer 150 and a full press fires (first arm since boot ~0.8 s; every later arm, a second session, ~16-20 s;
        # a diaset re-sent while one is pending does not restart it). post_shot_lv_s: live view returns this long after
        # a full press (None: shot_delay_s + 1 s by default). read_drops_live: a RETR / -INFO / -FDAT inside remote
        # control drops live view (-LVFR 451) until the next diaset.
        self.arm_first_s = self.arm_later_s = 0.0
        self.arms = 0
        self.arm_until = 0.0
        self.post_shot_lv_s = None
        self.read_drops_live = False
        self.lv_dropped = False
        self.unplugged = False   # test hook: the cable is out (or the camera switched off): off the bus, writes fail
        self.retr_bps = 0.0      # hw_timings: RETR paced to this many bytes/s (hardware ~3.9 MB/s); 0 = instant
        self.meta_s = 0.0        # hw_timings: each -INFO / -FDAT takes this long (hardware: 100 JPEGs listed in ~7.5 s)

    def hw_timings(self, scale: float = 1.0) -> "MockBody":
        """SIMULATED: timings measured on a camera, scaled by `scale` (tests use < 1). Fresh-boot arm 0.8 s, a second
        remote session 20 s, a stock -KSET busy 7 s after the last one, live view back 7 s after a shot, a card read
        inside remote control drops live view until diaset is sent again, RETR at 3.9 MB/s and -INFO + -FDAT 75 ms a photo (100 listed in 7.5 s)."""
        self.arm_first_s, self.arm_later_s = 0.8 * scale, 20.0 * scale
        self.kset_busy_after_s = 7.0 * scale
        self.post_shot_lv_s = 7.0 * scale
        self.read_drops_live = True
        self.retr_bps = 3.9e6 / scale if scale > 0 else 0.0
        self.meta_s = 0.0375 * scale  # -INFO + -FDAT per photo: 100 photos ~7.5 s
        return self

    def unplug(self):
        """SIMULATED cable pull / power switch: the camera leaves the bus; open transports fail on the next write."""
        self.unplugged = True

    def plug(self):
        """SIMULATED: back on the bus, as a fresh boot (remote control off, keys up, live view state reset)."""
        self.unplugged = False
        self.remote, self.dial, self.keys = 0, 9, set()
        self.lv_dropped, self.arm_until, self.arms = False, 0.0, 0

    def arming(self) -> bool:
        return self.clock() < self.arm_until

    def live_frame_bytes(self, mode: int, n: int) -> bytes:
        """Mode 1: the JPEG scan + EOI only (header stripped). Mode 0: raw Y. Built once per loop frame."""
        con, key_step = active_tone(self.ram)
        ev = self.ram[EV_OFF] if self.ram[EV_OFF] in KSET_STOCK[EV_OFF] else 13
        key = (mode, n, con, key_step, ev)
        if key not in self.lv_cache:
            if len(self.lv_cache) > 600:
                self.lv_cache.clear()
            # EV comp: (ev - 13) / 6 stops, about 45 levels a stop (a stand-in for the camera's exposure)
            base, gain, lift = _lv_pattern(n), 1 + 0.22 * (con - 4), 14 * (key_step - 4) + 7.5 * (ev - 13)
            lum = lambda x, y: _clamp(128 + (base(x, y) - 128) * gain + lift)  # noqa: E731
            if mode == 1 and self.lv_samples:  # the sample frames, each held SAMPLE_HOLD_S (tone and EV not applied)
                return self.lv_samples[int((self.clock() - self.lv_epoch) / SAMPLE_HOLD_S) % len(self.lv_samples)]
            if mode == 1:
                full = encode_avi1_frame(LV_JPEG_W, LV_JPEG_H, lambda bx, by: lum(bx * 8 + 4, by * 8 + 4), self.lv_scale)
                self.lv_cache[key] = full[193:]
            else:
                self.lv_cache[key] = bytes(lum(x * 2 + 1, y * 2 + 1) for y in range(LV_Y_H) for x in range(LV_Y_W))
        return self.lv_cache[key]

    def lvfr(self, argument: str) -> tuple[str, bytes | None]:
        """(reply line, payload or None) for -LVFR <mode>,<seq>; None for a malformed argument."""
        m = re.fullmatch(r"(\d),(\d{1,5})", argument)
        if not m or int(m.group(2)) > 0xFFFF:
            return None, None
        if m.group(1) not in "012":
            return "501 -LVFR mode", None
        mode, seq = int(m.group(1)), int(m.group(2))
        if mode == 2:  # the OSD page answers in every state, monitor stopped or not
            w, h, rows, raw = osd_page(self.osd_screen)
            pal = b"" if self.osd_palette == "fixed" else b"".join(struct.pack(">I", v or 0) for v in raw)
            return "150 -LVFR", lvfr_payload(2, 0, seq, w, h, len(pal) // 4, rows + pal)
        if (self.remote != 2 or self.pending or self.clock() < self.lv_off_until or self.osd_screen != "ready"
                or self.arming() or self.lv_dropped):
            return "451 -LVFR off", None
        if self.lvfr_left > 0:
            self.lvfr_left -= 1
            return "451 -LVFR off", None
        n = int((self.clock() - self.lv_epoch) * LV_FPS) % LV_LOOP
        w, h = (LV_JPEG_W, LV_JPEG_H) if mode == 1 else (LV_Y_W, LV_Y_H)
        data = self.live_frame_bytes(mode, n)
        return "150 -LVFR", lvfr_payload(mode, 1 if self.lv_truncate else 0, seq, w, h,
                                                               self.lv_scale if mode == 1 else 0, data)

    def kset(self, argument: str) -> str | None:
        m = re.fullmatch(r"(\d{1,4}),(\d{1,3})", argument)
        if not m:
            return None
        off, val = int(m.group(1)), int(m.group(2))
        if val > 255:
            return "451 -KSET value"
        if off in KSET_STOCK:  # firmware order: value, then busy (ks_idle), then the write and the stock apply
            if val not in KSET_STOCK[off]:
                return "451 -KSET value"
            if self.kset_busy():
                return "451 -KSET busy"
        elif not any(lo <= off <= hi for lo, hi in KSET_RANGES):
            return "451 -KSET range"
        self.ram[off] = val
        self.kset_log.append((off, val))
        if off in KSET_STOCK and self.kset_busy_after_s:
            self.kset_busy_until = self.clock() + self.kset_busy_after_s
        return f"200 -KSET {off}={val}"

    def kset_busy(self) -> bool:
        """The camera's ks_idle, as the mock can see it: the stand-in menu is open (overlay set, monitor stopped), a shot
        is being written or rec-review runs (monitor stopped), or remote control is not in state 2 (no still-record
        app state under USB)."""
        return (self.osd_screen != "ready" or bool(self.pending) or self.clock() < self.lv_off_until
                or self.remote != 2 or self.clock() < self.kset_busy_until or self.arming())

    def kget(self, argument: str) -> tuple[str | None, bytes | None]:
        m = re.fullmatch(r"(\d{1,4}),(\d{1,4})", argument)
        if not m:
            return None, None
        off, n = int(m.group(1)), int(m.group(2))
        if not 1 <= n or off + n > 4096:
            return "451 -KGET range", None
        return "150 -KGET", bytes(self.ram[off:off + n])

    def ksta_frame(self) -> bytes:
        """SIMULATED -KSTA frame (ksettings v1.3 layout). Only the remote / live / tick words follow the mock's model;
        the rest are fixed stand-ins. Nothing here says what a real camera holds."""
        import komabridge as KB  # the bridge owns the layout table (no import cycle at module load)
        live = self.remote == 2 and not self.pending and self.clock() >= self.lv_off_until and self.osd_screen == "ready"
        state = 2 if self.remote == 2 and self.dial == 4 and not self.arming() else 9
        v = {n: 0 for n, _, _ in KB.KSTA_WORDS}
        v.update(usb_attached=1, run_state=state, app_state=state, remtin_flag=1 if self.remote else 0,
                 monitor_run=1 if live else 0, rec_dispatch=1, batt_raw=0xB4, batt_end_thr=0x96, batt_level=3,
                 batt_type=0, cam_state=state, tick=int(self.clock() * 1000) & 0xFFFFFFFF)
        if self.shot_ksta_t0 is not None:
            age = self.clock() - self.shot_ksta_t0
            if age < self.shot_delay_s + self.grow_s + 0.1:
                v.update(cam_state=6 if age < min(self.ksta_busy_start_s, self.shot_delay_s) else 9)
            else:
                self.shot_ksta_t0 = None
        v.update(self.ksta_overrides)
        self.ksta_log.append((self.clock(), v["cam_state"]))
        return struct.pack(f">{KB.KSTA_N}I", KB.KSTA_VERSION, *(v[n] for n, _, _ in KB.KSTA_WORDS))

    def tick(self):
        """Lands shots whose simulated write has finished."""
        with self.lock:
            due = [p for p in self.pending if p[0] <= self.clock()]
            self.pending = [p for p in self.pending if p[0] > self.clock()]
        for _, name in due:
            path = f"{FOLDER}/{name}"
            if self.sample_photos:  # a new shot is one of the sample frames, in turn
                full = with_maker_tag(self.sample_photos[self.next_number % len(self.sample_photos)][1], self.maker_tag)
            else:
                full = with_maker_tag(encode_gray_jpeg(PHOTO_W, PHOTO_H, _scene_shot(self.next_number)), self.maker_tag)
            stamp = time.strftime("%Y:%m:%d %H:%M:%S")
            self.photo_dates[path] = stamp
            if self.nlst_lag_s > 0:  # on the card (-INFO sees it), not in -NLST yet
                self.card[path] = full
                self.lagged.append((self.clock() + self.nlst_lag_s, name, stamp))
                continue
            if self.grow_s > 0:
                self.growing[path] = (self.clock(), full, len(self.folder_entries), stamp)
                self.card[path] = full[:max(1, len(full) // 10)]
            else:
                self.card[path] = full
            self.folder_entries.append(_dirent(name, 0x20, len(self.card[path]), stamp))
        for item in [x for x in self.lagged if x[0] <= self.clock()]:
            self.lagged.remove(item)
            self.folder_entries.append(_dirent(item[1], 0x20, len(self.card[f"{FOLDER}/{item[1]}"]), item[2]))
        for path, (start, full, idx, stamp) in list(self.growing.items()):
            frac = (self.clock() - start) / self.grow_s
            if frac >= 1:
                del self.growing[path]
                frac = 1.0
            n = max(1, int(len(full) * (0.1 + 0.9 * frac)))
            self.card[path] = full[:n]
            self.folder_entries[idx] = _dirent(path.rsplit("/", 1)[1], 0x20, n, stamp)

    def cctl(self, argument: str) -> str | None:
        """-CCTL remote-control subcommands; None for anything else (the transport logs it as a violation)."""
        name = argument.split(",")[0]
        ok, bad = f"200 -CCTL {name} command successful.", f"451 -CCTL {name} command failure"
        if argument in self.fail_keys:
            return bad
        if argument == "remtin":
            if self.remote:
                return bad
            self.remote = 1
            return ok
        if argument == "remtout":
            if not self.remote:
                return bad
            self.remote = 0
            return ok
        m = re.fullmatch(r"diaset,(\d{1,4})", argument)
        if m:
            if not self.remote or int(m.group(1)) >= 9:
                return bad
            fresh = self.remote == 1  # remtin then the first diaset: the camera arms (slow on a second session)
            self.dial, self.remote = int(m.group(1)), 2
            self.lv_dropped = False
            if fresh:
                self.arms += 1
                delay = self.arm_first_s if self.arms == 1 else self.arm_later_s
                if delay > 0:
                    self.arm_until = self.clock() + delay
            self.lvfr_left = self.lvfr_451_polls
            self.armed = self.diaset_duds <= 0
            if not self.armed:
                self.diaset_duds -= 1
                self.lvfr_left = 10 ** 9
            return ok
        m = re.fullmatch(r"keyset,(\d{1,4}),(\d{1,4})", argument)
        if m:
            action, code = int(m.group(1)), int(m.group(2))
            if self.remote != 2 or action >= 4 or code >= 42:
                return bad
            self.key_log.append((action, code))
            if code in (2, 42):
                self.keys.clear()
                self.dial = 9
            elif action == 0:
                self.keys.discard(code)
            elif action == 1:
                self.keys.add(code)
            if action in (1, 3):  # stand-in menu model for the OSD page (not the camera's real menu logic)
                screen = self.osd_screen
                if code == 23 and screen == "ready":
                    self.osd_screen = "menu"
                elif code == 22 and screen != "ready":
                    self.osd_screen = "ready"
                elif code == 12 and screen == "menu":
                    self.osd_screen = "down"
                elif code == 11 and screen == "down":
                    self.osd_screen = "menu"
            if code == 27 and action in (1, 3) and self.tether and self.dial == 4 and self.armed and not self.arming():
                name = f"RIMG{self.next_number:04d}.JPG"
                self.next_number += 1
                self.pending.append((self.clock() + self.shot_delay_s, name))
                self.shot_ksta_t0 = self.clock()
                back = self.shot_delay_s + 1.0 if self.post_shot_lv_s is None else self.post_shot_lv_s
                self.lv_off_until = self.clock() + back
            return ok
        return None

    # bus presence, as a descriptor-only enumeration would see it
    def present(self) -> bool:
        with self.lock:
            if self.unplugged:
                return False
            if self.off_until is None:
                return True
            if self.clock() < self.off_until:
                return False
            self.off_until = None
            self._boot()
            return True

    def device(self):
        return grd_mock.DESCRIPTOR_VARIANTS["original"]

    def open(self):
        if not self.present():
            raise TransportError("simulated: no camera on the bus")
        transport = BridgeMockTransport(self)
        self.remote = 0  # a session event zeroes the -CCTL state (0x539b4), not the injected keys (STATIC)
        self.transports.append(transport)
        return transport

    def power_off(self):
        with self.lock:
            self.poff_count += 1
            self.off_until = self.clock() + self.reboot_s if self.returns_after_poff else float("inf")

    def _boot(self):
        """SIMULATED Koma boot import (PARAM.DAT import at boot)."""
        self.arms, self.arm_until, self.lv_dropped = 0, 0.0, False
        dat = self.card.pop("/EXT0/PARAM.DAT", None)
        if dat is None:
            self.boot_log.append("boot: no PARAM.DAT")
            return
        ok = len(dat) == 4096 and dat[:4] == b"MG1H" and int.from_bytes(dat[0xffc:], "big") == (
            sum(int.from_bytes(dat[i:i + 2], "big") for i in range(0, 0xffc, 2)) & 0xffffffff)
        self.card["/EXT0/PARAM.OK" if ok else "/EXT0/PARAM.BAD"] = dat
        if ok:
            # the export after an import (SETTINGS-EXTERNAL.md): the file's settings, this body's KSIO sub-header with
            # the import status (1 OK, 3 OK with a differing firmware tag), checksum rewritten
            old = self.card.get("/EXT0/PARAM.CUR", b"")
            tag = old[KSIO_TAG_OFF:KSIO_TAG_OFF + 7] if old[KSIO_OFF:KSIO_OFF + 4] == b"KSIO" else self.koma_tag
            file_tag = dat[KSIO_TAG_OFF:KSIO_TAG_OFF + 7] if dat[KSIO_OFF:KSIO_OFF + 4] == b"KSIO" else None
            cur = bytearray(dat)
            cur[KSIO_OFF:KSIO_OFF + 5] = b"KSIO\x01"
            cur[KSIO_TAG_OFF:KSIO_TAG_OFF + 7] = tag
            cur[KSIO_STATUS] = 3 if file_tag is not None and file_tag != tag else 1
            cur[0xFFC:] = block_checksum(cur)
            self.card["/EXT0/PARAM.CUR"] = bytes(cur)
            self.ram = bytearray(cur)
        self.boot_log.append(f"boot: PARAM.DAT -> {'PARAM.OK' if ok else 'PARAM.BAD'}")


class BridgeMockTransport(grd_mock.MockTransport):
    """grdusb's writable mock on the MockBody's card, plus PWD, -INFO, -FDAT, -CCTL poff and the -CCTL remote-control
    subcommands. Any other -CCTL subcommand, and every verb grdusb's mock does not know, is logged as a violation."""

    EXTRA_VERBS = {"PWD", "-INFO", "-FDAT", "-CCTL", "-LVFR", "-KSET", "-KGET", "-KSTA", "-PEEK"}

    def __init__(self, body: MockBody):
        super().__init__(writable=True, version=body.version)
        self.body = body
        self.files = body.card  # shared: writes land on the body's card and survive reconnects
        self.directories = {
            "/EXT0": [],
            "/EXT0/DCIM": [_dirent(".", 0x10, 0), _dirent("..", 0x10, 0), _dirent("100RICOH", 0x10, 0)],
            FOLDER: body.folder_entries,  # shared, so shots taken over -CCTL appear in every session
        }
        self.dead = False

    def write(self, data: bytes) -> int:
        if self.dead:
            raise TransportError("simulated: the camera left the bus (poff)")
        if self.body.unplugged:
            self.dead = True
            raise TransportError("simulated: the camera left the bus (cable out or switched off)")
        start, channel, _reserved, length = struct.unpack_from("<BBHI", data, 0)
        self.body.tick()
        if channel == 1 and self._upload is None:
            text = data[8:8 + length].decode("latin-1")
            verb = text.split(" ")[0].strip()
            if self.body.read_drops_live and self.body.remote == 2 and verb in ("RETR", "-INFO", "-FDAT"):
                self.body.lv_dropped = True  # on hardware a card read inside remote control drops live view
            if verb in ("-INFO", "-FDAT") and self.body.meta_s > 0:
                time.sleep(self.body.meta_s)
            elif verb == "RETR" and self.body.retr_bps > 0:
                size = len(self.files.get(text[len(verb):].strip(), b""))
                time.sleep(size / self.body.retr_bps)
            if verb in self.EXTRA_VERBS:
                self.sent.append(text)
                self._queue(self._extra(verb, text[len(verb):].strip()))
                return len(data)
        return super().write(data)

    def _extra(self, verb: str, argument: str) -> bytes:
        if verb == "PWD":
            return self._reply('257 "/EXT0" directory.')
        if verb == "-INFO":
            body = self.files.get(argument)
            if body is None:
                return self._reply("450 Open error")
            self.body.info_log.append((argument, len(body)))
            is_jpeg = body[:2] == b"\xff\xd8"
            w, h = (PHOTO_W, PHOTO_H) if is_jpeg else (0, 0)
            return self._reply(f"200 {len(body)} byte W={w} H={h} K=0 for -INFO.")
        if verb == "-FDAT":
            if argument not in self.files:
                return self._reply("450 Open error")
            return self._reply(f"200 date={self.body.photo_dates.get(argument, '')} for -FDAT.")
        if verb == "-LVFR":
            reply, payload = self.body.lvfr(argument)
            if reply is None:
                self.violations.append(f"-LVFR {argument}")
                return self._reply("501 Syntax error in parameters or arguments.")
            if payload is None:
                return self._reply(reply)
            return self._reply(reply) + grd_mock._frame(2, payload) + self._reply("250 -LVFR")
        if verb == "-KSET":
            reply = self.body.kset(argument)
            if reply is None:
                self.violations.append(f"-KSET {argument}")
                return self._reply("501 Syntax error in parameters or arguments.")
            return self._reply(reply)
        if verb == "-KGET":
            reply, data = self.body.kget(argument)
            if reply is None:
                self.violations.append(f"-KGET {argument}")
                return self._reply("501 Syntax error in parameters or arguments.")
            if data is None:
                return self._reply(reply)
            return self._reply(reply) + grd_mock._frame(2, data) + self._reply("250 -KGET")
        if verb == "-PEEK":   # SIMULATED RAM: byte i of the address space reads (i * 7 + 3) & 0xff
            m = re.fullmatch(r"([0-9a-fA-F]{1,7}),(\d{1,7})", argument or "")
            if not m:
                self.violations.append(f"-PEEK {argument}")
                return self._reply("501 Syntax error in parameters or arguments.")
            a, n = int(m.group(1), 16), int(m.group(2))
            if not 1 <= n <= 256 or a < 0x1A8000 or a + n > 0x3E5000:   # ksettings PEEK_LO / PEEK_END
                return self._reply("501 -PEEK range")
            return (self._reply("150 data connection for -PRET.") +
                    grd_mock._frame(2, bytes((i * 7 + 3) & 0xFF for i in range(a, a + n))) +
                    self._reply("250 -PRET command success."))
        if verb == "-KSTA":
            if argument or not self.body.ksta:
                if argument:
                    self.violations.append(f"-KSTA {argument}")
                return self._reply("501 Syntax error in parameters or arguments.")
            return (self._reply("150 data connection for -PRET.") + grd_mock._frame(2, self.body.ksta_frame())
                    + self._reply("250 -PRET command success."))
        if verb == "-CCTL":
            if argument.startswith("poff,"):
                self.dead = True
                self.body.power_off()
                return self._reply("200 -CCTL poff command successful.")
            reply = self.body.cctl(argument)
            if reply is not None:
                return self._reply(reply)
            self.violations.append(f"-CCTL {argument}")
            return self._reply("501 Syntax error in parameters or arguments.")
        raise AssertionError(verb)
