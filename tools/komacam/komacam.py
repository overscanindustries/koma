#!/usr/bin/env python3
"""komacam: a Ricoh GR DIGITAL (Koma firmware, `liveview` + `tether` features) as a webcam source on macOS.

It is a client of komabridge's HTTP server (tools/komaset/bridge/komabridge.py), never of USB directly:

    camera --USB--> komabridge  (/tether/start arms remote mode, /live?mode=1 is MJPEG, /tether/stop = remtout)
                       |
                       +--HTTP--> komacam --> (a) MJPEG  http://127.0.0.1:8770/stream.mjpg  (+ / page, /hd.mjpg)
                                          --> (b) virtual camera (pyvirtualcam; on macOS: OBS Virtual Camera)
                                          --> (c) frame dump folder

komacam either talks to a bridge that is already running (--bridge-port, default 8765) or starts one itself
(--spawn mock | mock-tether | real | emu) and stops it on exit. The supervisor arms live view (POST /tether/start, which
already does remtin + diaset,4 + the wait for -LVFR 150, up to 30 s with re-arms), streams GET /live?mode=1, re-arms when
the stream ends while the camera is still there, and waits for the camera when it is gone (unplug, power off). Ctrl-C
(or SIGTERM) ends the stream and sends POST /tether/stop (keyset,0,2 + remtout) before exiting.

stdlib only for the MJPEG pass-through; Pillow for looks, scaling, placeholders and the virtual camera; numpy +
pyvirtualcam for the virtual camera.
"""
from __future__ import annotations

import argparse
import collections
import http.client
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BRIDGE_PY = REPO / "tools" / "komaset" / "bridge" / "komabridge.py"
BRIDGE_PORT, CAM_PORT = 8765, 8770
BOUNDARY = "komacam"

try:  # optional: looks, scaling, placeholders, virtual camera
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps  # noqa: F401
    HAVE_PIL = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_PIL = False


def log(text: str) -> None:
    sys.stderr.write(f"{time.strftime('%H:%M:%S')} komacam: {text}\n")
    sys.stderr.flush()


# ------------------------------------------------------------------------------------------------ bridge HTTP client

class BridgeDown(Exception):
    """The bridge's HTTP server does not answer (not started, crashed, wrong port)."""


class BridgeClient:
    """Tiny JSON client for komabridge. Sends Host: 127.0.0.1:PORT (the bridge's DNS-rebinding guard wants that)."""

    def __init__(self, host: str = "127.0.0.1", port: int = BRIDGE_PORT):
        self.host, self.port = host, port
        self.extra_headers: dict = {}  # sent with every request (the Koma app's webcam marks its own requests)

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def request(self, method: str, path: str, timeout: float = 5.0, data=None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            body = (json.dumps(data).encode() if data is not None else b"{}") if method == "POST" else None
            headers = {"Host": f"127.0.0.1:{self.port}", **self.extra_headers}
            if body is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                obj = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                obj = {"raw": raw[:200].decode("latin-1")}
            return resp.status, obj if isinstance(obj, dict) else {"value": obj}
        except (ConnectionError, socket.timeout, OSError) as error:
            raise BridgeDown(f"{type(error).__name__}: {error}") from error
        finally:
            conn.close()

    def status(self) -> dict:
        code, obj = self.request("GET", "/status", timeout=3.0)
        if code != 200:
            raise BridgeDown(f"/status answered {code}")
        return obj

    def open_live(self, mode: int, timeout: float):
        """GET /live?mode=N. Returns (conn, resp); resp.status is 200 for a running stream."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request("GET", f"/live?mode={mode}", headers={"Host": f"127.0.0.1:{self.port}", **self.extra_headers})
            conn.stream_sock = conn.sock  # http.client drops conn.sock for a Connection: close reply; keep it to abort
            return conn, conn.getresponse()
        except (ConnectionError, socket.timeout, OSError) as error:
            conn.close()
            raise BridgeDown(f"{type(error).__name__}: {error}") from error


def read_exact(resp, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = resp.read(n - len(out))
        if not chunk:
            raise EOFError("stream closed mid-frame")
        out += chunk
    return bytes(out)


def iter_multipart(resp, boundary: str):
    """Yields (headers dict lower-case, body bytes) per part; returns at the closing boundary or EOF."""
    start, close = f"--{boundary}".encode(), f"--{boundary}--".encode()
    while True:
        line = resp.readline(65537)
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        if line == close:
            return
        if line != start:
            continue  # tolerate preamble or junk between parts
        headers = {}
        while True:
            h = resp.readline(65537)
            if not h:
                return
            h = h.strip()
            if not h:
                break
            k, _, v = h.decode("latin-1").partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", "-1"))
        if length < 0:
            raise ValueError("part without Content-Length")
        yield headers, read_exact(resp, length)


# ------------------------------------------------------------------------------------------------ frames

class Frame:
    __slots__ = ("id", "data", "ctype", "seq", "t", "wall", "placeholder")

    def __init__(self, fid, data, ctype, seq, placeholder=False):
        self.id, self.data, self.ctype, self.seq = fid, data, ctype, seq
        self.t, self.wall, self.placeholder = time.monotonic(), time.time(), placeholder


class Hub:
    """Latest-frame mailbox shared by every output. Outputs never queue: a slow consumer just skips frames."""

    def __init__(self):
        self.cond = threading.Condition()
        self.latest: Frame | None = None
        self.next_id = 1
        self.recent = collections.deque()
        self.frames = 0

    def publish(self, data: bytes, ctype: str, seq: int | None) -> Frame:
        with self.cond:
            frame = Frame(self.next_id, data, ctype, seq)
            self.next_id += 1
            self.latest = frame
            self.frames += 1
            self.recent.append(frame.t)
            while self.recent and frame.t - self.recent[0] > 2.0:
                self.recent.popleft()
            self.cond.notify_all()
            return frame

    def wait_next(self, last_id: int, timeout: float) -> Frame | None:
        with self.cond:
            self.cond.wait_for(lambda: self.latest is not None and self.latest.id != last_id, timeout)
            f = self.latest
            return f if f is not None and f.id != last_id else None

    def fps(self) -> float:
        with self.cond:
            if len(self.recent) < 2 or time.monotonic() - self.recent[-1] > 1.0:
                return 0.0
            span = self.recent[-1] - self.recent[0]
            return round((len(self.recent) - 1) / span, 1) if span > 0 else 0.0

    def age(self) -> float:
        with self.cond:
            return time.monotonic() - self.latest.t if self.latest else float("inf")


# ------------------------------------------------------------------------------------------------ looks and scaling

LOOKS = ("none", "bw", "hicon", "film")
FITS = ("letterbox", "crop", "stretch")
# -LVFR modes komacam streams: source size as sent, and display lines per source line (mode 3: the live-view ring holds
# a 4:3 picture in 244 lines, so a 640x240 frame is shown at 640x480; HW for the proportions via mode 1, which squeezes
# the same ring slot to 320x240 and looks right)
MODE_NATIVE_SIZE = {0: (160, 120), 1: (320, 240), 3: (640, 480)}
MODE_LINE_SCALE = {3: 2.0}


def _curve(contrast: float, lift: float = 0.0):
    """A smooth S-curve LUT (0..255): contrast 0 = identity, 1 = strong."""
    out = []
    for i in range(256):
        x = i / 255.0
        s = x * x * (3 - 2 * x)  # smoothstep
        y = (1 - contrast) * x + contrast * s + lift
        out.append(max(0, min(255, int(round(y * 255)))))
    return out


class Processor:
    """Turns a camera frame into the variants the outputs need, once per frame (cached for the latest frame).
    variant "native": source size, look applied (JPEG pass-through when the look is none and the frame is a JPEG);
    variant "hd": out_size, fitted (letterbox/crop/stretch), look, optional mirror and status overlay."""

    def __init__(self, look="none", fit="letterbox", out_size=(1280, 720), mirror=False, overlay=False,
                 quality=85, grain=0.6, status=None, line_scale=1.0):
        self.look, self.fit, self.out_size, self.mirror, self.overlay = look, fit, tuple(out_size), mirror, overlay
        # line_scale: display lines per source line. -LVFR mode 3 is 640x240 with pixels about twice as tall as wide (the
        # live-view ring holds a 4:3 picture in 244 lines), so it is 2.0 there: 640x240 -> 640x480 (4:3) before fitting.
        self.line_scale = float(line_scale)
        self.quality, self.grain, self.status = quality, grain, status or (lambda: {})
        self.lock = threading.Lock()
        self.cache: dict = {}  # (frame id, kind) -> value; only the latest frame's entries are kept
        self.cache_id = None
        self._grain_cache: dict = {}
        self._vignette_cache: dict = {}
        self._grain_i = 0
        self.timings = collections.deque(maxlen=120)  # seconds per hd frame
        if (look != "none") and not HAVE_PIL:
            raise SystemExit("--look needs Pillow: pip install Pillow")

    # -- helpers
    def _get(self, frame: Frame, kind: str, make):
        with self.lock:
            if self.cache_id != frame.id:
                self.cache, self.cache_id = {}, frame.id
            if kind in self.cache:
                return self.cache[kind]
        value = make()
        with self.lock:
            if self.cache_id == frame.id:
                self.cache[kind] = value
        return value

    def decode(self, frame: Frame):
        """The frame as square-pixel RGB: mode 3's 640x240 is stretched to 640x480 here (line_scale 2)."""
        def make():
            img = Image.open(io.BytesIO(frame.data))
            img.load()
            img = img.convert("RGB")
            if self.line_scale != 1.0 and not frame.placeholder:
                w, h = img.size
                img = img.resize((w, max(1, round(h * self.line_scale))), Image.BICUBIC)
            return img
        return self._get(frame, "decoded", make)

    def _tone(self, img):
        if self.look == "none":
            return img
        grey = img.convert("L")
        if self.look == "bw":
            grey = grey.point(_curve(0.35))
        elif self.look == "hicon":
            grey = ImageOps.autocontrast(grey, cutoff=1).point(_curve(0.95))
        elif self.look == "film":
            grey = grey.point(_curve(0.55, 0.0))
        return grey.convert("RGB")

    def _grain_tile(self, size):
        """A handful of precomputed noise frames at half resolution (clumpier, film-like grain), cycled."""
        key = tuple(size)
        if key not in self._grain_cache:
            w, h = max(2, size[0] // 2), max(2, size[1] // 2)
            sigma = 40 * self.grain
            tiles = []
            for _ in range(6):
                n = Image.effect_noise((w, h), sigma).resize(size, Image.BILINEAR).convert("RGB")
                tiles.append(n)
            self._grain_cache[key] = tiles
        tiles = self._grain_cache[key]
        self._grain_i = (self._grain_i + 1) % len(tiles)
        return tiles[self._grain_i]

    def _vignette(self, size):
        key = tuple(size)
        if key not in self._vignette_cache:
            w, h = 64, 36
            m = Image.new("L", (w, h))
            px = m.load()
            for y in range(h):
                for x in range(w):
                    dx, dy = (x - (w - 1) / 2) / (w / 2), (y - (h - 1) / 2) / (h / 2)
                    r2 = dx * dx + dy * dy
                    px[x, y] = int(255 * max(0.0, min(1.0, 1.0 - 0.28 * max(0.0, r2 - 0.25))))
            self._vignette_cache[key] = m.resize(size, Image.BICUBIC).convert("RGB")
        return self._vignette_cache[key]

    def _finish(self, img):
        """Grain and vignette at the output resolution (film look only)."""
        if self.look == "film":
            img = ImageChops.add(img, self._grain_tile(img.size), 1.0, -128)
            img = ImageChops.multiply(img, self._vignette(img.size))
        return img

    def fit_to(self, img, size=None):
        W, H = size or self.out_size
        w, h = img.size
        if self.fit == "stretch":
            return img.resize((W, H), Image.BICUBIC)
        if self.fit == "crop":
            s = max(W / w, H / h)
            nw, nh = max(W, round(w * s)), max(H, round(h * s))
            big = img.resize((nw, nh), Image.BICUBIC)
            x, y = (nw - W) // 2, (nh - H) // 2
            return big.crop((x, y, x + W, y + H))
        s = min(W / w, H / h)
        nw, nh = max(1, round(w * s)), max(1, round(h * s))
        canvas = Image.new("RGB", (W, H))
        canvas.paste(img.resize((nw, nh), Image.BICUBIC), ((W - nw) // 2, (H - nh) // 2))
        return canvas

    def _draw_overlay(self, img):
        st = self.status()
        text = (f"GR  {st.get('fps_in', 0):4.1f} fps  {st.get('state', '?')}  arms {st.get('arms', 0)}  "
                f"re-arms {st.get('rearms', 0)}  reconnects {st.get('reconnects', 0)}")
        d = ImageDraw.Draw(img)
        x, y = 12, img.size[1] - 26
        d.rectangle((x - 6, y - 4, x + 7 * len(text) + 6, y + 16), fill=(0, 0, 0))
        d.text((x, y), text, fill=(160, 255, 160))
        return img

    # -- variants
    def hd_image(self, frame: Frame):
        def make():
            t0 = time.perf_counter()
            img = self._tone(self.decode(frame))
            img = self._finish(self.fit_to(img))
            if self.mirror:
                img = ImageOps.mirror(img)
            if self.overlay:
                img = self._draw_overlay(img)
            self.timings.append(time.perf_counter() - t0)
            return img
        return self._get(frame, "hd_img", make)

    def hd_jpeg(self, frame: Frame) -> bytes:
        return self._get(frame, "hd_jpg", lambda: _encode(self.hd_image(frame), self.quality))

    def native_jpeg(self, frame: Frame) -> bytes:
        if self.look == "none" and frame.ctype == "image/jpeg" and (self.line_scale == 1.0 or not HAVE_PIL):
            return frame.data  # (mode 3 without Pillow: passed through non-square, 640x240)
        if not HAVE_PIL:
            return frame.data  # PNG (mode 0) stays PNG without Pillow; the multipart part says so

        def make():
            img = self._finish(self._tone(self.decode(frame)))
            return _encode(img, self.quality)
        return self._get(frame, "native_jpg", make)

    def native_ctype(self, frame: Frame) -> str:
        if frame.ctype == "image/jpeg":
            return "image/jpeg"
        return "image/jpeg" if HAVE_PIL else frame.ctype

    def ms_per_hd_frame(self) -> float | None:
        if not self.timings:
            return None
        return round(1000 * sum(self.timings) / len(self.timings), 1)


def _encode(img, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# The Koma app's typeface for the state card (Archivo, SIL Open Font License): the built app's copy (the bridge serves
# the app from it), else the one in its npm package. Without either the card falls back to Pillow's own font.
APP_DIR = REPO / "tools" / "komaapp"
CARD_FONT_GLOBS = ("dist/assets/archivo-latin-wdth-normal-*.woff2",
                   "node_modules/@fontsource-variable/archivo/files/archivo-latin-wdth-normal.woff2")
CARD_WEIGHTS = (500, 400)     # the first line (GR DIGITAL) as the app's titles, the second as its body
_card_font_path: list = []    # [path or None], looked up once


def card_font_file() -> str | None:
    """The Archivo file the state card is set in, or None when this checkout has no built app and no npm package."""
    if not _card_font_path:
        hits = [p for g in CARD_FONT_GLOBS for p in sorted(APP_DIR.glob(g))]
        _card_font_path.append(str(hits[0]) if hits else None)
    return _card_font_path[0]


def _card_font(px: int, weight: int):
    """Archivo at px and weight (its Weight axis; Width stays 100), or Pillow's default font at px. None when neither
    can be drawn at a size (Pillow before 10.1 without the file)."""
    from PIL import ImageFont
    path = card_font_file()
    if path:
        try:
            font = ImageFont.truetype(path, px)
            try:
                axes = font.get_variation_axes()
                font.set_variation_by_axes([weight if a["name"] in (b"Weight", "Weight") else
                                            100 if a["name"] in (b"Width", "Width") else a["default"] for a in axes])
            except (OSError, AttributeError, KeyError):
                pass   # a FreeType without variations draws the file's default instance
            return font
        except OSError:    # unreadable here (a FreeType without WOFF2): Pillow's own font below
            pass
    try:
        return ImageFont.load_default(size=px)
    except TypeError:      # Pillow before 10.1 has no sizes
        return None


def placeholder_jpeg(size, lines) -> bytes | None:
    """A dark card with the current state, shown by every output while there is no live frame. Set in the app's
    Archivo when a source checkout has it, else in Pillow's default font (Pillow 10.1+ draws it at any size); 60 and
    36 px at 720 lines, legible in a call's tile. Older Pillow without the file uses the small bitmap font."""
    if not HAVE_PIL:
        return None
    img = Image.new("RGB", size, (12, 12, 12))
    d = ImageDraw.Draw(img)
    try:
        fonts = [_card_font(max(12, size[1] // (12 if i == 0 else 20)), CARD_WEIGHTS[min(i, 1)])
                 for i in range(len(lines))]
        if any(f is None for f in fonts):
            fonts = None
    except (OSError, ImportError, AttributeError):
        fonts = None
    if fonts is None:
        y = size[1] // 2 - 12 * len(lines)
        for i, line in enumerate(lines):
            d.text((size[0] // 2 - 3 * len(line), y + 24 * i), line, fill=(200, 200, 200) if i == 0 else (130, 130, 130))
        return _encode(img, 80)
    step = [round(f.size * 1.6) for f in fonts]
    y = (size[1] - sum(step)) // 2
    for i, (line, font) in enumerate(zip(lines, fonts)):
        x = (size[0] - d.textlength(line, font=font)) / 2
        d.text((x, y), line, font=font, fill=(200, 200, 200) if i == 0 else (130, 130, 130))
        y += step[i]
    return _encode(img, 80)


# ------------------------------------------------------------------------------------------------ camera controls
# Everything here goes through komabridge endpoints that sit on the bridge's USB allowlist: POST /keys/tap
# (-CCTL keyset,3,C), POST /ram (-KSET), POST /tether/focus (keyset,1,26 / keyset,0,26), and the read verbs GET /ram
# (-KGET), GET /ram/peek (-PEEK) and GET /ram/state (-KSTA).

KEY_DISP = 22                 # key table 0x18108c "DISP"; the firmware's own USB test string is "keyset,3,22" (STATIC)
DISP_LIVE = 0x1BEF92          # live display mode, read by 0x6e91c, switched on by the DISP handler 0x7e260 (STATIC, EMU)
DISP_LCD_OFF = 4              # 0x7e330: live 3 -> 4, stored byte 0 and UI 0x7f0d0(8,0); EMU: LCD level 0, monitor on
DISP_CYCLE = 5                # live 0 -> 5 -> 2 -> 3 -> 4 -> 0 (EMU trace, 0x7e260 branches; 0 may skip to 2)
OFF_FOCUS, OFF_SNAP, OFF_EV, OFF_IMG, OFF_TE_TONE = 0x058, 0x8B5, 0x053, 0x030, 0x1A9
FOCUS_SNAP = 2                # settings-map focus: commit table 0x1bd40b, SNAP = 2 (STATIC)
SNAP_DISTANCES = {"1": 0, "1.5": 1, "2": 2, "2.5": 3, "5": 4, "inf": 5}   # tools/koma/features/snapdist.py ROWS
IMG_SETS = {"hard": 0, "normal": 1, "soft": 2, "bw": 3, "setting1": 4, "setting2": 5, "bwte": 6}  # 0x030 (HW table)
TE_TONES = {"sepia": 6, "red": 1, "green": 2, "blue": 3, "purple": 4}                           # 0x1a9 (STATIC)
KSET_SPACING_S = 8.0          # HW: -KSET answered busy 6-8 s after an EV change; one stock write per 8 s


def ev_value(ev: float) -> int:
    """EV -2.0..+2.0 in 1/3 steps -> settings byte 0x053 (odd 1..25, 13 = 0.0; commit table 0x1bd3ce, HW)."""
    steps = round(float(ev) * 3)
    if not -6 <= steps <= 6 or abs(steps / 3 - float(ev)) > 0.06:
        raise ValueError(f"EV {ev} is not one of -2.0..+2.0 in 1/3 steps")
    return 13 + 2 * steps


def parse_ev(text: str) -> float:
    try:
        ev = float(text)
        ev_value(ev)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return ev


class Controls:
    """Host-side camera controls applied after every arm and undone before /tether/stop.

    lcd_off: tap DISP (keyset,3,22) until the live display byte 0x1bef92 (read with -PEEK) is 4, the stock LCD-off
             position. Closed loop: never taps blind; gives up if -PEEK is missing. Restores the starting byte at exit.
    focus:   -KSET 0x8b5 (Koma snapdist byte) then one half-press; with FOCUS = SNAP the lens goes there.
    af_once: one half-press after the first arm only.
    ev, img_set, te_tone: stock bytes through -KSET (+ the camera's own apply), one write per KSET_SPACING_S.
    Runtime changes (POST /control on komacam) are queued, latest value wins, applied by this thread."""

    def __init__(self, client: "BridgeClient", lcd_off=False, focus=None, af_once=False, ev=None, img_set=None,
                 te_tone=None, health_every=60.0, settle_s=0.6, kset_spacing_s=KSET_SPACING_S, event=None):
        self.client = client
        self.lcd_off, self.focus, self.af_once = lcd_off, focus, af_once
        self.want = {}            # pending stock writes: name -> (offset, value)
        if ev is not None:
            self.want["ev"] = (OFF_EV, ev_value(ev))
        if img_set is not None:
            self.want["img_set"] = (OFF_IMG, IMG_SETS[img_set])
        if te_tone is not None:
            self.want["te_tone"] = (OFF_TE_TONE, TE_TONES[te_tone])
        self.applied = {}         # name -> value last confirmed 200
        self.health_every, self.settle_s, self.kset_spacing_s = health_every, settle_s, kset_spacing_s
        self.event = event or log
        self.lock = threading.Lock()
        self.disp_start = None    # live display byte before komacam touched it (restored at exit)
        self.disp_now = None
        self.lcd_state = "off-request" if lcd_off else "untouched"
        self.af_done = False
        self.last_kset = -1e9
        self.health = {}
        self.health_ok = True
        self.warnings: list = []

    # -- small HTTP helpers (BridgeDown propagates)
    def _peek_disp(self) -> int | None:
        code, obj = self.client.request("GET", f"/ram/peek?addr={DISP_LIVE:#x}&len=1", timeout=10.0)
        if code != 200 or "hex" not in obj:
            return None
        return int(obj["hex"][:2], 16)

    def _kget(self, off: int) -> int | None:
        code, obj = self.client.request("GET", f"/ram?off={off:#x}&len=1", timeout=10.0)
        return int(obj["hex"][:2], 16) if code == 200 and obj.get("hex") else None

    def _kset(self, off: int, val: int) -> tuple[int, dict]:
        code, obj = self.client.request("POST", "/ram", timeout=30.0, data={"off": off, "val": val})
        self.last_kset = time.monotonic()
        return code, obj

    def _warn(self, text: str):
        if text not in self.warnings:
            self.warnings.append(text)
        self.event(text)

    # -- LCD
    def set_disp(self, target: int, stop=None) -> bool:
        """Tap DISP until the live display byte is target (at most DISP_CYCLE + 1 taps). True when reached."""
        cur = self._peek_disp()
        if cur is None:
            self._warn("LCD: cannot read the display mode (-PEEK 0x1bef92 needs ksettings v1.4); not tapping DISP blind")
            return False
        if self.disp_start is None:
            self.disp_start = cur
        self.disp_now = cur
        for _ in range(DISP_CYCLE + 1):
            if cur == target:
                return True
            if stop is not None and stop.is_set() and target == DISP_LCD_OFF:
                return False
            code, obj = self.client.request("POST", "/keys/tap", timeout=15.0, data={"code": KEY_DISP})
            if code != 200 or not obj.get("ok"):
                self._warn(f"LCD: DISP tap refused: HTTP {code} {obj.get('message') or obj.get('error')}")
                return False
            time.sleep(self.settle_s)
            nxt = self._peek_disp()
            if nxt is None:
                return False
            cur = self.disp_now = nxt
        self._warn(f"LCD: display mode did not reach {target} after {DISP_CYCLE + 1} DISP taps (now {cur})")
        return cur == target

    def ensure_lcd_off(self, stop=None) -> bool:
        ok = self.set_disp(DISP_LCD_OFF, stop)
        self.lcd_state = "off" if ok else "failed"
        if ok:
            self.event("LCD off (live display mode 4, the stock DISP LCD-off position; UNTESTED on hardware)")
        return ok

    def restore_lcd(self) -> bool:
        if self.disp_start is None or self.disp_now == self.disp_start:
            return True
        ok = self.set_disp(self.disp_start)
        self.lcd_state = "restored" if ok else "restore failed"
        self.event(f"LCD restored to display mode {self.disp_start}" if ok else
                   "LCD restore failed: press DISP on the camera until the screen is back")
        return ok

    # -- focus
    def apply_focus(self, first_arm: bool):
        if self.focus is not None:
            code, obj = self._kset(OFF_SNAP, SNAP_DISTANCES[self.focus])
            if code != 200:
                self._warn(f"focus: -KSET snap distance refused: HTTP {code} {obj.get('message') or obj.get('error')} "
                           "(needs Koma with snapdist + ksettings)")
            mode = self._kget(OFF_FOCUS)
            if mode is not None and mode != FOCUS_SNAP:
                self._warn(f"focus: FOCUS is {mode}, not SNAP (2); set FOCUS = SNAP in the camera menu once, or the "
                           "half-press autofocuses instead of going to the snap distance")
        if self.focus is not None or (self.af_once and not self.af_done):
            code, obj = self.client.request("POST", "/tether/focus", timeout=60.0)
            self.af_done = self.af_done or (code == 200 and bool(obj.get("ok")))
            self.event(f"half-press: HTTP {code} {obj.get('message') or ''}".rstrip())

    # -- stock bytes (EV, IMG SET, TE tone)
    def request(self, name: str, off: int, val: int):
        with self.lock:
            self.want[name] = (off, val)

    def pending(self) -> dict:
        with self.lock:
            return {k: v for k, v in self.want.items() if self.applied.get(k) != v[1]}

    def apply_stock(self, stop=None) -> bool:
        """Writes pending stock bytes, one per kset_spacing_s. True when nothing is left pending."""
        for name, (off, val) in self.pending().items():
            wait = self.kset_spacing_s - (time.monotonic() - self.last_kset)
            if wait > 0:
                return False
            code, obj = self._kset(off, val)
            if code == 200:
                with self.lock:
                    self.applied[name] = val
                self.event(f"{name}: -KSET 0x{off:03x} = {val} ok")
            elif code == 409:
                self.event(f"{name}: camera busy, retrying in {self.kset_spacing_s:g} s")
                return False
            else:
                self._warn(f"{name}: -KSET 0x{off:03x}={val} refused: HTTP {code} "
                           f"{obj.get('message') or obj.get('error')}")
                with self.lock:
                    self.applied[name] = val  # do not retry a refusal forever
        return not self.pending()

    # -- session health (-KSTA)
    def poll_health(self) -> dict | None:
        if not self.health_ok:
            return None
        code, obj = self.client.request("GET", "/ram/state", timeout=10.0)
        if code != 200:
            self.health_ok = False
            self.event(f"health: -KSTA unavailable (HTTP {code}); no battery/APO/LCD readout this session")
            return None
        keep = ("batt_level", "batt_raw", "apo_2b9e58", "apo_2b9e50", "monitor_run", "lcd_1a8d40", "lcd_1a8d4c",
                "w_1b97e4", "app_state", "run_state")
        h = {k: obj.get(k) for k in keep if k in obj}
        h["t"] = round(time.time(), 1)
        prev, self.health = self.health, h
        if h.get("apo_2b9e58") == 1 and prev.get("apo_2b9e58") == 1:
            self._warn("health: Auto Power Off countdown is enabled on two polls in a row (expected 0 while live "
                       "view streams; 0x290de(2) per frame). The bridge's idle keepalive is the fallback")
        return h

    def snapshot(self) -> dict:
        return {"lcd": self.lcd_state, "disp_start": self.disp_start, "disp_now": self.disp_now,
                "focus": self.focus, "applied": dict(self.applied), "pending": sorted(self.pending()),
                "health": dict(self.health), "warnings": list(self.warnings[-5:])}


# ------------------------------------------------------------------------------------------------ the supervisor

class Supervisor:
    """Owns the bridge conversation: wait for the camera, arm, stream, re-arm on a drop, reconnect after an unplug."""

    def __init__(self, client: BridgeClient, hub: Hub, mode: int = 1, frame_timeout: float = 15.0,
                 arm_timeout: float = 75.0, spawner=None, controls: Controls | None = None,
                 wake_gap_s: float = 5.0):
        self.client, self.hub, self.mode = client, hub, mode
        self.controls = controls  # LCD / focus / EV / IMG SET / health, applied after each arm (Controls)
        self.wall, self.mono = time.time, time.monotonic  # sleep detection: wall clock runs on in sleep, monotonic not
        self.wake_gap_s = wake_gap_s
        self.wakes = 0
        self.force_reset = False  # after a host sleep: /tether/stop, then a fresh arm
        self.frame_timeout, self.arm_timeout = frame_timeout, arm_timeout
        self.spawner = spawner
        self.stop_event = threading.Event()
        self.state = "starting"
        self.message = ""
        self.arms = self.rearms = self.reconnects = self.arm_failures = self.live_off = self.streams = 0
        self.stalls = 0
        self.last_arm_s = None
        self.last_end = None
        self.connected_once = False
        self.was_connected = False
        self.remote_on = False
        self.backend = None
        self._conn = None
        self._conn_lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True, name="supervisor")
        self.aux = threading.Thread(target=self._aux, daemon=True, name="controls")
        self.events: list = []  # (monotonic, text) for the tests and the log

    def event(self, text: str):
        self.events.append((time.monotonic(), text))
        log(text)

    def set_state(self, state: str, message: str = ""):
        if state != self.state or message != self.message:
            self.state, self.message = state, message
            log(f"[{state}] {message}" if message else f"[{state}]")

    def snapshot(self) -> dict:
        return {"state": self.state, "message": self.message, "fps_in": self.hub.fps(), "frames": self.hub.frames,
                "arms": self.arms, "rearms": self.rearms, "reconnects": self.reconnects, "stalls": self.stalls,
                "arm_failures": self.arm_failures, "live_off": self.live_off, "last_arm_s": self.last_arm_s,
                "last_end": self.last_end, "backend": self.backend, "remote_on": self.remote_on,
                "frame_age_s": None if self.hub.age() == float("inf") else round(self.hub.age(), 2),
                "wakes": self.wakes, "controls": self.controls.snapshot() if self.controls else None}

    def start(self):
        self.thread.start()
        self.aux.start()
        return self

    # -- the second thread: host sleep/wake, queued control writes, session health
    def check_wake(self, last: tuple) -> tuple:
        """last = (wall, mono) of the previous tick. A wall-clock jump the monotonic clock did not see means the host
        slept (macOS: time.monotonic is mach_absolute_time, which stops in sleep). Returns the new (wall, mono)."""
        now = (self.wall(), self.mono())
        gap = (now[0] - last[0]) - (now[1] - last[1])
        if gap > self.wake_gap_s and not self.stop_event.is_set():
            self.on_wake(gap)
        return now

    def on_wake(self, gap: float):
        self.wakes += 1
        self.event(f"host slept ~{gap:.0f} s; resetting the USB session (stream off, /tether/stop, arm again)")
        self.force_reset = True
        self._drop_stream()

    def _drop_stream(self):
        with self._conn_lock:
            conn = self._conn
        sock = getattr(conn, "stream_sock", None) or getattr(conn, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _aux(self):
        last = (self.wall(), self.mono())
        next_health = self.mono() + 15.0
        while not self.stop_event.wait(1.0):
            last = self.check_wake(last)
            c = self.controls
            if c is None or not self.remote_on or self.state != "streaming":
                continue
            try:
                if c.pending():
                    c.apply_stock(self.stop_event)
                if c.health_every and self.mono() >= next_health:
                    next_health = self.mono() + c.health_every
                    h = c.poll_health()
                    if h:
                        log("health: " + " ".join(f"{k}={v}" for k, v in h.items() if k != "t"))
            except BridgeDown as error:
                log(f"controls: bridge did not answer ({error})")

    def _sleep(self, s: float) -> bool:
        """Sleep unless stopping; True if stop was requested."""
        return self.stop_event.wait(s)

    def _run(self):
        need_arm = True
        consecutive_fail = 0
        while not self.stop_event.is_set():
            # 1. bridge reachable?
            try:
                st = self.client.status()
            except BridgeDown as error:
                if self.spawner and not self.spawner.alive():
                    self.set_state("no bridge", "the bridge process exited; restarting it")
                    self.spawner.restart()
                else:
                    self.set_state("no bridge", f"no komabridge at {self.client.base} ({error})")
                self.remote_on = False
                need_arm = True
                self._sleep(1.0)
                continue
            self.backend = st.get("backend")
            # 2. camera there?
            if not st.get("connected"):
                if st.get("busy"):  # the bridge holds its lock (another client's request); try again shortly
                    self._sleep(0.5)
                    continue
                if self.was_connected:
                    self.event("camera lost; waiting for it to come back (power it on, then plug USB)")
                self.was_connected = False
                self.remote_on = False
                need_arm = True
                self.set_state("no camera", st.get("message", "No camera on USB."))
                self._sleep(1.0)
                continue
            if not self.was_connected:
                if self.connected_once:
                    self.reconnects += 1
                    self.event(f"camera back (reconnect {self.reconnects})")
                self.connected_once = self.was_connected = True
            # 3. arm
            if need_arm:
                if not self._arm():
                    consecutive_fail += 1
                    if consecutive_fail >= 2:
                        self.event("arming failed twice; full reset: /tether/stop, then arm again")
                        self._tether_stop(quiet=True)
                        consecutive_fail = 0
                    self._sleep(min(10.0, 2.0 * (1 + self.arm_failures % 4)))
                    continue
                consecutive_fail = 0
                need_arm = False
            # 4. stream until it ends
            why = self._stream()
            if self.stop_event.is_set():
                break
            if self.force_reset:
                self.force_reset = False
                self._tether_stop(quiet=True)
                why = f"host wake ({why})"
            self.last_end = why
            if why == "live_off":
                self.live_off += 1
                self._sleep(min(5.0, 0.5 * self.live_off))
            need_arm = True
            self.rearms += 1
            self.event(f"live view ended ({why}); re-arming (re-arm {self.rearms})")
            self._sleep(0.3)
        self.set_state("stopped")

    def _arm(self) -> bool:
        self.set_state("arming", "remtin + diaset,4, waiting for -LVFR 150 (up to 30 s on a second session)")
        t0 = time.monotonic()
        try:
            code, obj = self.client.request("POST", "/tether/start", timeout=self.arm_timeout)
        except BridgeDown as error:
            self.arm_failures += 1
            self.event(f"arm: bridge did not answer ({error})")
            return False
        dt = round(time.monotonic() - t0, 2)
        if self.stop_event.is_set():
            self.remote_on = bool(obj.get("active"))
            return False
        if code == 200 and obj.get("ok") and obj.get("armed"):
            self.arms += 1
            self.remote_on = True
            self.last_arm_s = dt
            self.event(f"armed in {dt:.1f} s (arm {self.arms})")
            self._after_arm()
            return True
        self.arm_failures += 1
        self.remote_on = bool(obj.get("active"))
        replies = "; ".join(f"{r.get('command')} -> {r.get('reply')}" for r in obj.get("replies", [])[-4:])
        self.event(f"arm failed after {dt:.1f} s: HTTP {code} {obj.get('message') or obj.get('error')} [{replies}]")
        return False

    def _after_arm(self):
        """Controls after each arm: focus first (a half-press may light the LCD for Synchro Monitor), then LCD off,
        then the queued stock bytes. Failures are logged and never block streaming."""
        c = self.controls
        if c is None:
            return
        try:
            c.apply_focus(first_arm=self.arms == 1)
            if c.lcd_off and not self.stop_event.is_set():
                c.ensure_lcd_off(self.stop_event)
            c.applied.clear()  # a re-arm may follow a power cycle: write the stock bytes again
            c.apply_stock(self.stop_event)
        except BridgeDown as error:
            self.event(f"controls: bridge did not answer ({error})")

    def _stream(self) -> str:
        self.set_state("streaming" if self.hub.age() < 1 else "starting stream")
        try:
            conn, resp = self.client.open_live(self.mode, timeout=self.frame_timeout)
        except BridgeDown as error:
            return f"bridge: {error}"
        with self._conn_lock:
            self._conn = conn
        try:
            if resp.status != 200:
                try:
                    obj = json.loads(resp.read().decode("utf-8") or "{}")
                except ValueError:
                    obj = {}
                if resp.status == 409:
                    self.event(f"/live refused: {obj.get('reply') or obj.get('error')}")
                    return "live_off"
                return f"/live HTTP {resp.status}: {obj.get('error')}"
            ctype = resp.getheader("Content-Type", "")
            boundary = ctype.split("boundary=", 1)[1].strip('" ') if "boundary=" in ctype else "komalive"
            self.streams += 1
            self.live_off = 0
            first = True
            for headers, body in iter_multipart(resp, boundary):
                seq = headers.get("x-frame-seq")
                self.hub.publish(body, headers.get("content-type", "image/jpeg"), int(seq) if seq else None)
                if first:
                    self.set_state("streaming")
                    first = False
                if self.stop_event.is_set():
                    return "stopped"
            return "stream ended by the bridge"
        except socket.timeout:
            self.stalls += 1
            return f"no frame for {self.frame_timeout:g} s"
        except (EOFError, ConnectionError, OSError, ValueError, http.client.HTTPException) as error:
            return "stopped" if self.stop_event.is_set() else f"{type(error).__name__}: {error}"
        finally:
            with self._conn_lock:
                self._conn = None
            conn.close()

    def _tether_stop(self, quiet=False) -> dict | None:
        try:
            code, obj = self.client.request("POST", "/tether/stop", timeout=45.0)
        except BridgeDown as error:
            if not quiet:
                log(f"/tether/stop: bridge did not answer ({error})")
            return None
        self.remote_on = False
        if not quiet:
            replies = "; ".join(f"{r.get('command')} -> {r.get('reply')}" for r in obj.get("replies", []))
            log(f"/tether/stop HTTP {code}: {obj.get('message') or obj.get('error')} [{replies}]")
        return obj

    def stop(self) -> dict | None:
        """End the stream and leave remote mode (keyset,0,2 + remtout). Safe to call twice."""
        self.stop_event.set()
        self.set_state("stopping", "closing live view, sending remtout")
        self._drop_stream()
        try:
            self.client.request("POST", "/live/stop?mode=1", timeout=5.0)
        except BridgeDown:
            pass
        if self.controls is not None and self.remote_on:  # keys need remote control: restore before remtout
            try:
                self.controls.restore_lcd()
            except BridgeDown as error:
                log(f"LCD restore: bridge did not answer ({error})")
        if self.thread.is_alive() and self.state == "arming":
            log("an arm is in progress; /tether/stop waits for it (up to 30 s)")
        result = self._tether_stop()
        self.thread.join(timeout=50)
        return result


# ------------------------------------------------------------------------------------------------ outputs

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GR DIGITAL</title>
<style>
html,body{margin:0;height:100%;background:#000;overflow:hidden;cursor:none}
img,canvas{position:fixed;inset:0;width:100vw;height:100vh;object-fit:contain;display:block;background:#000}
body.cover img,body.cover canvas{object-fit:cover} body.mirror img,body.mirror canvas{transform:scaleX(-1)}
#rc{position:fixed;left:50%;top:10px;transform:translateX(-50%);font:12px ui-monospace,Menlo,monospace;color:#ccc;
 background:#000a;padding:3px 9px;border-radius:4px;display:none}
</style></head><body>
<canvas id="c" width="1280" height="720"></canvas><img id="v" alt="" style="display:none">
<div id="rc">reconnecting...</div>
<script>
// The OBS Browser source: the picture and nothing else. Looking and switching happen in the Koma app (Webcam).
// URL flags for the source: ?mirror=1, ?fill=1 (crop to fill), ?src=hd (the 1280x720 stream).
const q=new URLSearchParams(location.search), b=document.body, v=document.getElementById('v');
const src=(q.get('src')||'stream')+'.mjpg';
b.classList.toggle('mirror',q.get('mirror')==='1');b.classList.toggle('cover',q.get('fill')==='1');
// Self-healing reader. STALL_MS: no complete frame for this long aborts the fetch. RETRY_MS/RETRY_MAX: reconnect backoff.
// SHOW_MS: "reconnecting" label delay. IMG_RELOAD_MS: reload period of the old <img> fallback (no ReadableStream).
const STALL_MS=3000, RETRY_MS=1000, RETRY_MAX=3000, SHOW_MS=3000, IMG_RELOAD_MS=5000;
const cv=document.getElementById('c'), cx=cv.getContext('2d'), rc=document.getElementById('rc');
let lastFrame=Date.now(), gen=0, ctl=null, tries=0, busy=false;
function swapShown(on){cv.style.display=on?'block':'none';v.style.display=on?'none':'block'}
function cat(a,c){const r=new Uint8Array(a.length+c.length);r.set(a);r.set(c,a.length);return r}
function find(h,n,from){const m=n.length;outer:for(let i=from||0;i<=h.length-m;i++){for(let j=0;j<m;j++)if(h[i+j]!==n[j])continue outer;return i}return -1}
const enc=s=>new TextEncoder().encode(s), dec=a=>new TextDecoder('latin1').decode(a);
async function draw(blob){
 if(busy)return; busy=true;   // drop frames while one is decoding; only the newest matters
 try{
  if(self.createImageBitmap){const bm=await createImageBitmap(blob);
   if(cv.width!==bm.width||cv.height!==bm.height){cv.width=bm.width;cv.height=bm.height}
   cx.drawImage(bm,0,0);bm.close&&bm.close()}
  else{const u=URL.createObjectURL(blob);await new Promise((ok,no)=>{const im=new Image();im.onload=()=>{
    if(cv.width!==im.width||cv.height!==im.height){cv.width=im.width;cv.height=im.height}
    cx.drawImage(im,0,0);ok()};im.onerror=no;im.src=u}).finally(()=>URL.revokeObjectURL(u))}
  lastFrame=Date.now();tries=0;rc.style.display='none';
 }catch(e){}finally{busy=false}
}
// Pulls complete JPEGs out of buf. With a boundary: header block + Content-Length (or next boundary). Without: FFD8..FFD9.
function nextFrame(buf,bnd){
 if(bnd){
  const d=enc('--'+bnd), a=find(buf,d,0); if(a<0)return null;
  const he=find(buf,enc('\r\n\r\n'),a); if(he<0)return null;
  const m=/content-length:\s*(\d+)/i.exec(dec(buf.subarray(a,he))), st=he+4;
  let en;
  if(m){en=st+parseInt(m[1],10);if(buf.length<en)return null}
  else{en=find(buf,d,st);if(en<0)return null}
  return {jpg:buf.subarray(st,en),rest:buf.subarray(en)};
 }
 const s0=find(buf,[0xFF,0xD8],0); if(s0<0)return null;
 const e0=find(buf,[0xFF,0xD9],s0+2); if(e0<0)return null;
 return {jpg:buf.subarray(s0,e0+2),rest:buf.subarray(e0+2)};
}
function retry(){ctl=null;const d=Math.min(RETRY_MS*Math.max(1,++tries),RETRY_MAX);setTimeout(connect,d)}
async function connect(){
 const my=++gen, c=ctl=new AbortController(); lastFrame=Date.now();
 try{
  const r=await fetch(src+'?t='+Date.now(),{signal:c.signal,cache:'no-store'});
  if(!r.ok||!r.body)throw new Error('http '+r.status);
  const m=/boundary="?([^";\s]+)"?/i.exec(r.headers.get('content-type')||''), bnd=m?m[1]:null;
  const rd=r.body.getReader(); let buf=new Uint8Array(0);
  for(;;){
   const {done,value}=await rd.read(); if(done)break;
   buf=cat(buf,value);
   let f; while((f=nextFrame(buf,bnd))){buf=f.rest;draw(new Blob([f.jpg],{type:'image/jpeg'}))}
   if(buf.length>8e6)buf=new Uint8Array(0);   // runaway guard
  }
 }catch(e){}
 if(my===gen)retry();
}
setInterval(()=>{const idle=Date.now()-lastFrame;
 if(idle>SHOW_MS)rc.style.display='block';
 if(idle>STALL_MS&&ctl){const c=ctl;ctl=null;gen++;c.abort();tries=0;setTimeout(connect,RETRY_MS)}},500);
if(window.ReadableStream&&window.fetch&&window.AbortController){swapShown(true);connect()}
else{swapShown(false);const load=()=>{v.src=src+'?t='+Date.now()};v.onerror=()=>setTimeout(load,1000);load();setInterval(load,IMG_RELOAD_MS)}
</script></body></html>
"""


# The state card's second line, by supervisor state (the first is GR DIGITAL). A call sees it, so it is plain words.
CARD_WORDS = {"starting": "Starting", "arming": "Waking the camera", "starting stream": "Waking the camera",
              "streaming": "Waiting for the picture", "no camera": "Camera not connected",
              "no bridge": "Koma is not running", "stopping": "Stopping", "stopped": "Stopped"}
NO_LIVE_VIEW = "No live view"


def card_word(st: dict) -> str:
    """The card's second line for a supervisor snapshot. While it re-arms a camera that refuses live view (/live answers
    409 since the last stream, or no arm has ever given a frame) the card says so, as the Koma app's Webcam page does."""
    if st["state"] in ("starting", "arming", "starting stream") and (
            st.get("live_off", 0) > 0 or (st.get("arm_failures", 0) > 0 and not st.get("frames"))):
        return NO_LIVE_VIEW
    return CARD_WORDS.get(st["state"], "Waiting for the picture")


class Outputs:
    """The MJPEG server's view of the world: frames (hub), processing, supervisor status, placeholders."""

    def __init__(self, hub: Hub, proc: Processor, sup: Supervisor, native_size=(320, 240)):
        self.hub, self.proc, self.sup = hub, proc, sup
        self.clients = 0
        self.clients_lock = threading.Lock()
        self.native_size = native_size
        self.stale_s = 1.5  # no live frame for this long: outputs show the placeholder card
        self._ph_cache: dict = {}

    def placeholder(self, size) -> bytes | None:
        st = self.sup.snapshot()
        lines = ["GR DIGITAL", card_word(st)]
        key = (tuple(size), tuple(lines))
        if key not in self._ph_cache:
            if len(self._ph_cache) > 16:
                self._ph_cache.clear()
            self._ph_cache[key] = placeholder_jpeg(size, lines)
        return self._ph_cache[key]

    def control(self, obj: dict) -> dict:
        """Runtime controls (POST /control). Stock bytes are queued (latest wins, one -KSET per 8 s); LCD and the
        half-press run once in a background thread. Raises ValueError on a bad request."""
        c = self.sup.controls
        if c is None:
            raise ValueError("controls are off")
        done = {}
        if "ev" in obj:
            c.request("ev", OFF_EV, ev_value(obj["ev"]))
            done["ev"] = "queued"
        if "img_set" in obj:
            if obj["img_set"] not in IMG_SETS:
                raise ValueError(f"img_set is one of {sorted(IMG_SETS)}")
            c.request("img_set", OFF_IMG, IMG_SETS[obj["img_set"]])
            done["img_set"] = "queued"
        if "te_tone" in obj:
            if obj["te_tone"] not in TE_TONES:
                raise ValueError(f"te_tone is one of {sorted(TE_TONES)}")
            c.request("te_tone", OFF_TE_TONE, TE_TONES[obj["te_tone"]])
            done["te_tone"] = "queued"
        once = []
        if obj.get("lcd") in ("off", "restore"):
            c.lcd_off = obj["lcd"] == "off"
            once.append(c.ensure_lcd_off if c.lcd_off else c.restore_lcd)
            done["lcd"] = "started"
        elif "lcd" in obj:
            raise ValueError('lcd is "off" or "restore"')
        if obj.get("af"):
            once.append(lambda: self.sup.client.request("POST", "/tether/focus", timeout=60.0))
            done["af"] = "started"
        if not done:
            raise ValueError("nothing to do: ev, img_set, te_tone, lcd or af")
        if once:
            def run():
                for fn in once:
                    try:
                        fn()
                    except BridgeDown as error:
                        log(f"control: bridge did not answer ({error})")
            threading.Thread(target=run, daemon=True, name="control").start()
        return {"ok": True, **done}

    def status(self) -> dict:
        s = self.sup.snapshot()
        s.update(clients=self.clients, ms_per_hd_frame=self.proc.ms_per_hd_frame(), look=self.proc.look,
                 fit=self.proc.fit, out_size=list(self.proc.out_size), version=VERSION)
        return s


def make_handler(out: Outputs, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"komacam/{VERSION}"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()  # no Access-Control-Allow-Origin: no other website may read the picture or the state
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get("Host") not in hosts:  # DNS-rebinding guard, as komabridge does
                return self._send(403, b'{"error":"unexpected Host header"}', "application/json")
            url = urlsplit(self.path)
            route = url.path
            if route in ("/", "/index.html"):
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if route == "/status.json":
                return self._send(200, json.dumps(out.status()).encode(), "application/json")
            if route == "/snapshot.jpg":
                f = out.hub.latest
                if f is None:
                    ph = out.placeholder(out.native_size)
                    return self._send(503, ph or b"no frame yet", "image/jpeg" if ph else "text/plain")
                return self._send(200, out.proc.native_jpeg(f), out.proc.native_ctype(f))
            if route in ("/stream.mjpg", "/hd.mjpg", "/raw.mjpg"):
                if route == "/hd.mjpg" and not HAVE_PIL:
                    return self._send(501, b"/hd.mjpg needs Pillow", "text/plain")
                return self._mjpeg(route[1:-5])
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            """POST /control {"ev": 0.3} | {"img_set": "bw"} | {"te_tone": "sepia"} | {"lcd": "off"|"restore"} |
            {"af": true}. Loopback only; a request carrying a foreign Origin is refused (no cross-site writes)."""
            if self.headers.get("Host") not in hosts:
                return self._send(403, b'{"error":"unexpected Host header"}', "application/json")
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc not in hosts:
                return self._send(403, b'{"error":"cross-origin request refused"}', "application/json")
            if urlsplit(self.path).path != "/control":
                return self._send(404, b"not found", "text/plain")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                obj = json.loads(self.rfile.read(min(n, 4096)).decode() or "{}")
                if not isinstance(obj, dict):
                    raise ValueError("send a JSON object")
                result = out.control(obj)
            except (ValueError, KeyError) as error:
                return self._send(400, json.dumps({"error": str(error)}).encode(), "application/json")
            return self._send(200, json.dumps(result).encode(), "application/json")

        def _mjpeg(self, kind: str):
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()  # same origin only: the OBS Browser source page reads it from this server
            self.close_connection = True
            with out.clients_lock:
                out.clients += 1
            last_id, last_ph = 0, 0.0
            size = out.proc.out_size if kind == "hd" else out.native_size
            try:
                while not out.sup.stop_event.is_set() or out.hub.age() < 0.2:
                    f = out.hub.wait_next(last_id, timeout=0.5)
                    if f is not None:
                        last_id = f.id
                        if kind == "hd":
                            body, ctype = out.proc.hd_jpeg(f), "image/jpeg"
                        elif kind == "raw":
                            body, ctype = f.data, f.ctype
                        else:
                            body, ctype = out.proc.native_jpeg(f), out.proc.native_ctype(f)
                        self._part(body, ctype, f)
                        continue
                    if out.sup.stop_event.is_set():
                        break
                    # no new frame: keep the client alive with the state card (twice a second)
                    if out.hub.age() > out.stale_s and time.monotonic() - last_ph > 0.5:
                        last_ph = time.monotonic()
                        ph = out.placeholder(size)
                        if ph is not None:
                            self._part(ph, "image/jpeg", None)
                        elif out.hub.latest is not None:
                            self._part(out.hub.latest.data, out.hub.latest.ctype, None)
                self.wfile.write(f"--{BOUNDARY}--\r\n".encode())
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with out.clients_lock:
                    out.clients -= 1

        def _part(self, body: bytes, ctype: str, f: Frame | None):
            head = [f"--{BOUNDARY}", f"Content-Type: {ctype}", f"Content-Length: {len(body)}"]
            if f is not None:
                if f.seq is not None:
                    head.append(f"X-Frame-Seq: {f.seq}")
                head.append(f"X-Komacam-Id: {f.id}")
                head.append(f"X-Komacam-Recv: {f.wall:.6f}")
            else:
                head.append("X-Komacam-Placeholder: 1")
            self.wfile.write(("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body + b"\r\n")
            self.wfile.flush()

    return Handler


def start_mjpeg_server(out: Outputs, host: str, port: int) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("komacam serves loopback only (127.0.0.1)")
    server = ThreadingHTTPServer((host, port), make_handler(out, port))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="mjpeg").start()
    return server


VCAM_HELP = """\
Virtual camera unavailable: {why}
  komacam keeps serving MJPEG; the virtual camera is optional. Two ways to get the GR into Zoom:
  1. OBS (simplest): install OBS Studio 30+ (https://obsproject.com or `brew install --cask obs`), add a Browser
     Source with URL {url}, click Start Virtual Camera, then pick "OBS Virtual Camera" in Zoom.
  2. pyvirtualcam: `{py} -m pip install pyvirtualcam numpy`, install OBS 30+ and click Start Virtual Camera once
     (that registers the macOS camera extension; System Settings may ask you to allow it), quit OBS, then run komacam
     with --vcam. Pick "OBS Virtual Camera" in Zoom.
"""


class VirtualCam(threading.Thread):
    """Feeds the hd variant to pyvirtualcam at a fixed rate (repeats the last frame; state card when stale).
    Never fails hard: missing modules or a missing OBS camera extension print instructions and retry slowly."""

    def __init__(self, out: Outputs, fps: float = 30.0, backend: str | None = None, page_url: str = "",
                 module=None, retry_s: float = 20.0):
        super().__init__(daemon=True, name="vcam")
        self.out, self.fps, self.backend, self.page_url = out, fps, backend, page_url
        self.module, self.retry_s = module, retry_s
        self.sent = 0
        self.device = None
        self.error = None

    def _help(self, why: str):
        self.error = why
        log(VCAM_HELP.format(why=why, url=self.page_url, py=sys.executable).rstrip())

    def run(self):
        if not HAVE_PIL:
            return self._help("Pillow is not installed")
        try:
            import numpy as np
            pvc = self.module or __import__("pyvirtualcam")
        except ImportError as error:
            return self._help(f"{error.name} is not installed")
        W, H = self.out.proc.out_size
        stop = self.out.sup.stop_event
        while not stop.is_set():
            try:
                kw = {"backend": self.backend} if self.backend else {}
                with pvc.Camera(width=W, height=H, fps=self.fps, fmt=pvc.PixelFormat.RGB, **kw) as cam:
                    self.device, self.error = cam.device, None
                    log(f"virtual camera on: {cam.device} {W}x{H} @ {self.fps:g} fps")
                    last_id, arr = 0, None
                    while not stop.is_set():
                        f = self.out.hub.latest
                        if f is not None and self.out.hub.age() <= self.out.stale_s:
                            if f.id != last_id:
                                arr, last_id = np.asarray(self.out.proc.hd_image(f)), f.id
                        else:
                            ph = self.out.placeholder((W, H))
                            if ph is not None:
                                arr, last_id = np.asarray(Image.open(io.BytesIO(ph)).convert("RGB")), 0
                        if arr is not None:
                            cam.send(arr)
                            self.sent += 1
                        cam.sleep_until_next_frame()
                return
            except Exception as error:  # noqa: BLE001  (RuntimeError: no OBS camera extension, etc.)
                self._help(f"{type(error).__name__}: {error}")
                if stop.wait(self.retry_s):
                    return


class Dumper(threading.Thread):
    """Writes frames to a folder: raw camera frames (default) or the hd variant. index.csv: id, seq, recv time."""

    def __init__(self, out: Outputs, folder: Path, variant: str = "raw", max_frames: int = 0):
        super().__init__(daemon=True, name="dump")
        self.out, self.folder, self.variant, self.max_frames = out, Path(folder), variant, max_frames
        self.written = 0

    def run(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        index = (self.folder / "index.csv").open("a")
        if index.tell() == 0:
            index.write("file,id,seq,recv_unix\n")
        last_id = 0
        stop = self.out.sup.stop_event
        try:
            while not stop.is_set() and (not self.max_frames or self.written < self.max_frames):
                f = self.out.hub.wait_next(last_id, 0.5)
                if f is None:
                    continue
                last_id = f.id
                if self.variant == "hd":
                    data, ext = self.out.proc.hd_jpeg(f), "jpg"
                else:
                    data, ext = f.data, "png" if f.ctype == "image/png" else "jpg"
                name = f"frame_{f.id:07d}.{ext}"
                (self.folder / name).write_bytes(data)
                index.write(f"{name},{f.id},{'' if f.seq is None else f.seq},{f.wall:.6f}\n")
                self.written += 1
            index.flush()
        finally:
            index.close()
        if self.max_frames and self.written >= self.max_frames:
            log(f"dump: wrote {self.written} frames to {self.folder}")


# ------------------------------------------------------------------------------------------------ bridge process

class BridgeSpawner:
    """Starts komabridge as a child process (and restarts it if it dies); stops it with SIGINT at exit."""

    KINDS = {"mock": ["--mock"], "mock-tether": ["--mock", "--mock-tether"], "real": ["--real"], "emu": ["--emu"]}

    def __init__(self, kind: str, port: int, python: str = sys.executable, extra=()):
        self.kind, self.port, self.python, self.extra = kind, port, python, list(extra)
        self.proc: subprocess.Popen | None = None
        self.restarts = 0

    def start(self):
        args = [self.python, str(BRIDGE_PY), *self.KINDS[self.kind], "--port", str(self.port), "--quiet", *self.extra]
        log(f"starting bridge: {' '.join(args[1:])}")
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True, name="bridge-log").start()
        return self

    def _pump(self, proc):
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                sys.stderr.write(f"  bridge| {line}\n")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def restart(self):
        self.restarts += 1
        if self.restarts > 5:
            return
        self.start()

    def stop(self):
        if not self.alive():
            return
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def wait_for_bridge(client: BridgeClient, seconds: float) -> dict | None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            return client.status()
        except BridgeDown:
            time.sleep(0.2)
    return None


# ------------------------------------------------------------------------------------------------ app

class App:
    """Everything wired together; main() and the tests both use this."""

    def __init__(self, a):
        self.a = a
        self.client = BridgeClient("127.0.0.1", a.bridge_port)
        self.hub = Hub()
        self.spawner = None
        self.controls = Controls(self.client, lcd_off=a.lcd_off, focus=a.focus, af_once=a.af_once, ev=a.ev,
                                 img_set=a.img_set, te_tone=a.te_tone, health_every=a.health_every)
        self.sup = Supervisor(self.client, self.hub, mode=a.mode, frame_timeout=a.frame_timeout,
                              arm_timeout=a.arm_timeout, controls=self.controls)
        self.proc = Processor(look=a.look, fit=a.fit, out_size=a.size, mirror=a.mirror, overlay=a.overlay,
                              quality=a.quality, grain=a.grain, status=self.sup.snapshot,
                              line_scale=MODE_LINE_SCALE.get(a.mode, 1.0))
        self.out = Outputs(self.hub, self.proc, self.sup, native_size=MODE_NATIVE_SIZE[a.mode])
        self.server = None
        self.vcam = None
        self.dumper = None
        self.caffeinate = None
        self._stopped = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.a.port}/"

    def start(self):
        a = self.a
        if a.spawn:
            existing = wait_for_bridge(self.client, 0.5)
            if existing is not None:
                want = "mock" if a.spawn.startswith("mock") else a.spawn
                if existing.get("backend") != want:
                    raise SystemExit(f"a komabridge ({existing.get('backend')}) already runs on port {a.bridge_port}; "
                                     f"stop it or drop --spawn")
                log(f"a {want} komabridge already runs on port {a.bridge_port}; using it")
            else:
                self.spawner = BridgeSpawner(a.spawn, a.bridge_port, a.bridge_python or sys.executable).start()
                self.sup.spawner = self.spawner
                if wait_for_bridge(self.client, 120.0 if a.spawn == "emu" else 15.0) is None:
                    self.spawner.stop()
                    raise SystemExit("the bridge did not come up (see its log above)")
        if "mjpeg" in a.outputs:
            self.server = start_mjpeg_server(self.out, "127.0.0.1", a.port)
            log(f"MJPEG: {self.url}stream.mjpg (camera size), {self.url}hd.mjpg ({a.size[0]}x{a.size[1]}), "
                f"OBS Browser source {self.url}")
        if "vcam" in a.outputs:
            self.vcam = VirtualCam(self.out, fps=a.vcam_fps, backend=a.vcam_backend, page_url=self.url)
            self.vcam.start()
        if a.dump:
            self.dumper = Dumper(self.out, Path(a.dump), a.dump_variant, a.dump_max)
            self.dumper.start()
        if getattr(a, "caffeinate", False) and sys.platform == "darwin" and shutil.which("caffeinate"):
            # keep the Mac awake (idle and system sleep) for as long as komacam runs; -w ends it with this process
            self.caffeinate = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
            log("caffeinate: the Mac will not idle-sleep while komacam runs (--no-caffeinate to allow it)")
        self.sup.start()
        return self

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        result = self.sup.stop()
        ok = bool(result and result.get("ok"))
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.spawner:
            self.spawner.stop()
        if self.caffeinate is not None and self.caffeinate.poll() is None:
            self.caffeinate.terminate()
        log("stopped; remote mode off" if ok else "stopped (remtout not confirmed: is the bridge still running?)")
        return result

    def status_line(self) -> str:
        s = self.out.status()
        extra = f"  hd {s['ms_per_hd_frame']} ms" if s["ms_per_hd_frame"] else ""
        vc = f"  vcam {self.vcam.sent}" if self.vcam and self.vcam.device else ""
        return (f"{s['state']:<10} in {s['fps_in']:4.1f} fps  frames {s['frames']}  clients {s['clients']}  arms "
                f"{s['arms']}  re-arms {s['rearms']}  reconnects {s['reconnects']}{extra}{vc}")


def parse_size(text: str):
    w, _, h = text.lower().partition("x")
    return int(w), int(h)


def build_parser():
    ap = argparse.ArgumentParser(description="GR DIGITAL live view (via komabridge) as an MJPEG stream / virtual "
                                             "camera / frame dump. Camera on first, then plug USB.")
    ap.add_argument("--spawn", choices=sorted(BridgeSpawner.KINDS),
                    help="start komabridge here (mock, mock-tether, real, emu); default: use a running bridge")
    ap.add_argument("--bridge-port", type=int, default=BRIDGE_PORT, help="komabridge port (default 8765)")
    ap.add_argument("--bridge-python", help="python for --spawn (default: this one; needs pyusb for real)")
    ap.add_argument("--port", type=int, default=CAM_PORT, help="komacam MJPEG port (default 8770)")
    ap.add_argument("--no-mjpeg", action="store_true", help="no MJPEG server")
    ap.add_argument("--vcam", action="store_true", help="also feed a virtual camera (pyvirtualcam / OBS Virtual Camera)")
    ap.add_argument("--vcam-backend", help="pyvirtualcam backend (macOS: obs)")
    ap.add_argument("--vcam-fps", type=float, default=30.0)
    ap.add_argument("--dump", help="write frames to this folder")
    ap.add_argument("--dump-variant", choices=("raw", "hd"), default="raw")
    ap.add_argument("--dump-max", type=int, default=0, help="stop dumping after N frames (0 = no limit)")
    ap.add_argument("--mode", type=int, choices=(0, 1, 3), default=1,
                    help="-LVFR mode: 1 JPEG 320x240 (default), 0 grey 160x120, 3 JPEG 640x240 straight from the "
                         "live-view ring (liveview 1.3; non-square pixels, shown at 640x480 = 4:3; needs Pillow)")
    ap.add_argument("--size", type=parse_size, default=(1280, 720), help="hd / virtual camera size (default 1280x720)")
    ap.add_argument("--fit", choices=FITS, default="letterbox", help="4:3 into 16:9: letterbox (bars), crop, stretch")
    ap.add_argument("--look", choices=LOOKS, default="none", help="none, bw, hicon (hard B&W), film (B&W + grain)")
    ap.add_argument("--grain", type=float, default=0.6, help="film grain strength 0..1.5 (default 0.6)")
    ap.add_argument("--mirror", action="store_true", help="mirror the hd / virtual camera output")
    ap.add_argument("--overlay", action="store_true", help="draw fps / state on the hd / virtual camera output")
    ap.add_argument("--quality", type=int, default=85, help="JPEG quality of re-encoded frames")
    ap.add_argument("--focus", choices=sorted(SNAP_DISTANCES, key=lambda k: SNAP_DISTANCES[k]),
                    help="snap distance in metres: -KSET 0x8b5, then one half-press after each arm (needs FOCUS = SNAP "
                         "set on the camera, and Koma snapdist + ksettings)")
    ap.add_argument("--af-once", action="store_true", help="one half-press after the first arm (locks AF once)")
    ap.add_argument("--lcd-off", action="store_true",
                    help="UNTESTED on hardware: tap DISP to the stock LCD-off position while streaming, restore at exit "
                         "(reads the display mode with -PEEK; needs ksettings v1.4)")
    ap.add_argument("--ev", type=parse_ev, help="exposure compensation -2.0..+2.0 in 1/3 steps (-KSET 0x053)")
    ap.add_argument("--img-set", choices=sorted(IMG_SETS), help="IMG SET over -KSET 0x030 (bw = camera B&W)")
    ap.add_argument("--te-tone", choices=sorted(TE_TONES), help="B&W (TE) toning colour over -KSET 0x1a9")
    ap.add_argument("--health-every", type=float, default=60.0,
                    help="seconds between -KSTA health reads (battery, APO, LCD); 0 = off")
    ap.add_argument("--no-caffeinate", dest="caffeinate", action="store_false",
                    help="macOS: allow the Mac to sleep (default: caffeinate -i -s while komacam runs)")
    ap.add_argument("--frame-timeout", type=float, default=15.0,
                    help="no frame this long = stall, re-arm (keep it above the bridge's 451 grace, 10 s)")
    ap.add_argument("--arm-timeout", type=float, default=75.0, help="HTTP timeout for /tether/start")
    ap.add_argument("--status-every", type=float, default=5.0, help="status log interval in s (0 = off)")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (tests)")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    a.outputs = set() if a.no_mjpeg else {"mjpeg"}
    if a.vcam:
        a.outputs.add("vcam")
    if not a.outputs and not a.dump:
        print("nothing to do: enable MJPEG, --vcam or --dump", file=sys.stderr)
        return 2
    if not HAVE_PIL and (a.vcam or a.look != "none" or a.mode in (0, 3)):
        log("Pillow is missing: looks, scaling, the virtual camera, mode 0 and mode 3's 4:3 stretch need it "
            "(pip install Pillow)")
    app = App(a)
    stop = threading.Event()
    hits = []

    def on_signal(signum, _frame):
        hits.append(signum)
        if len(hits) > 1:
            log("second interrupt: exiting without waiting (the camera may stay in remote mode; "
                "run `curl -X POST http://127.0.0.1:%d/tether/stop` or power-cycle it)" % a.bridge_port)
            os._exit(130)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)  # the terminal window closed: same clean stop
    app.start()
    log(f"komacam {VERSION}: Ctrl-C stops cleanly (live view off, keyset,0,2 + remtout)")
    t0 = time.monotonic()
    next_status = t0 + a.status_every if a.status_every else float("inf")
    while not stop.wait(0.25):
        now = time.monotonic()
        if a.duration and now - t0 >= a.duration:
            break
        if now >= next_status:
            log(app.status_line())
            next_status = now + a.status_every
    app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
