#!/usr/bin/env python3
"""komabridge: a local HTTP bridge between the Koma Setup page and a GR DIGITAL on USB (ORIGINAL mode).

  python3 tools/komaset/bridge/komabridge.py --mock     simulated camera (tools/grdusb mock); safe anywhere
  python3 tools/komaset/bridge/komabridge.py --real     a real camera; prints a warning; never run in tests
  python3 tools/komaset/bridge/komabridge.py --real --selftest --out ~/koma-session/   the USB steps of the hardware
                                                         session in one run (selftest.py), no server

Stdlib only, plus tools/grdusb (pyusb + libusb are
needed for --real only) and tools/komaset/komaset.py for the settings envelope checks.

Serves 127.0.0.1:8765. Phone mode (--lan at start, or the app's Settings > Use on your phone at runtime; phoneaccess.py)
adds this computer's network address, with a one-time pairing code by QR and a token required on every request there.
Endpoints (JSON unless noted):
  GET  /                  the Koma app (tools/komaapp/dist, built with `npm run build`; without a build the old page)
  GET  /legacy            the Koma Setup page (web/koma-setup.html); GET /settings-map.json the map; GET /api the list
  GET  /status            bridge and camera state; touches no camera unless a new one has to be opened
  GET  /settings          RETR /EXT0/PARAM.CUR, base64 plus the komaset `check` summary
  POST /settings          {"data": base64 of 4096 bytes}; envelope-checked; -STOR to /EXT0/PARAM.DAT; read back
  POST /reboot            -CCTL poff,N (whether the camera comes back to a USB session afterwards is unknown, U)
  GET  /photos            the JPEGs under /EXT0/DCIM/*/ with size, date (-FDAT) and dimensions (-INFO)
  GET  /photos/<F>/<N>    one JPEG (image/jpeg) by RETR; ?thumb=1 serves the EXIF thumbnail if the file has one
  GET  /photos/import     the import folder, the photos already imported (name|size|date keys) and the last import job;
                          ?job=1 leaves out the imported list. No camera traffic (photoimport.py)
  POST /photos/import     copy every photo not imported yet into <folder>/YYYY/MM/DD/, each read back and compared before
                          it counts (a background job; 409 remote_on while remote control is on); DELETE stops it
  POST /photos/folder     {"path": "~/Pictures/Koma"}: the import folder (made if its parent exists, write-checked);
                          phone mode (--lan) refuses it from anything but this computer
  GET  /photos?newest=1   the newest JPEG alone (by the -NLST file time): one -INFO and -FDAT, not one per photo
  POST /tether/start      -CCTL remtin + diaset,4 (still): remote control on (needs the Koma `tether` feature to fire)
  POST /tether/shoot      half-press, AF wait, full-press, release; then -NLST until a new JPEG appears or time runs out
  POST /tether/focus      half-press hold, AF wait, release
  POST /tether/stop       -CCTL keyset,0,2 (clear every injected key and the dial) + remtout
  POST /tether/interval   {"every_s": N, "count": M}: M shoots every N s in the background; GET status, DELETE cancels
  GET  /live?mode=1|0     live view as multipart/x-mixed-replace: -LVFR mode,seq per frame (a READ verb; needs the Koma
                          `liveview` feature and tether state 2); mode 1 = JPEG (host-built header), mode 0 = grey PNG
  GET  /live?mode=3       liveview 1.3: the full live-view slot, 640x240 JPEG (camera encoder, no resize). The pixels
                          are NOT square (one ring line is about two picture lines): display it at 4:3, e.g. 640x480;
                          the bridge picks the JPEG scale per frame to stay inside one 65,520-byte chunk
  GET  /live?mode=2       the camera's OSD page (menus, icons) as 4-bit indexed PNG parts, ~5 a second, index 0
                          transparent; its own stream, so it runs next to a mode 1/0 stream (Remote task)
  GET  /live/frame?mode=  one live frame (image/jpeg or image/png), for debugging
  GET  /live/status       the live-view counters only (fps, bytes/s); no USB traffic. ?mode=2 for the OSD stream
  POST /live/stop         end any running live stream (both); ?mode=2 or ?mode=1 ends only that one
  GET  /keys              the key codes the bridge may press, with names, and whether remote control is on
  POST /keys              {"code": C, "action": A}: -CCTL keyset,A,C (A 0 release, 1 hold, 3 tap); remote control only
  POST /keys/tap          {"code": C}: -CCTL keyset,3,C (a tap presses and releases in one poll; no release is sent)
  POST /dial              {"n": N}: -CCTL diaset,N, N 0..8 (virtual mode dial); remote control only
  GET  /webcam            the Webcam area (webcamhost.py: komacam in this process); POST /webcam/start, /webcam/stop;
                          GET /webcam/preview, the picture the call receives. While it holds the camera, /tether/stop,
                          /live/stop and GET /live (video) from anyone else answer 409 webcam_on (webcamhost.refuse)
  GET  /phone             Use on your phone (phoneaccess.py): network access switched at runtime, one-time pairing
                          code by QR; POST /phone/on, /phone/off, /phone/revoke (loopback), POST /phone/pair (network)
  GET  /ram?off=&len=     -KGET: block bytes from the camera's RAM (Looks task; a READ verb)
  GET  /ram/state         -KSTA decoded (READ verb); GET /ram/peek?addr=&len= -PEEK 1..256 bytes (READ verb)
  POST /ram               {"off": N, "val": V}: -KSET N,V, one byte, KSET_WINDOW (map-checked) or a KSET_STOCK byte
                          with one of its stock values (422 otherwise); camera `451 -KSET busy` -> 409 camera_busy
  POST /ram/batch         [{"off": N, "val": V}, ...]: up to 64, checked whole, sent in order (a busy reply retried up to 5 s)

On the wire the card is /EXT0; the camera maps it to /ATA1 (docs/internals/07-usb-protocol.md), so /EXT0/PARAM.DAT is
/ATA1/PARAM.DAT on the camera. Verified on a real camera: the selftest steps (connect, PARAM.CUR, photos, RETR, tether
shot, -LVFR 0/1/2, -KGET/-KSET EV); POST /reboot (poff) has never been sent.
"""
from __future__ import annotations

import argparse
import base64
import collections
import hmac
import json
import re
import secrets
import socket
import socketserver
import struct
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

HERE = Path(__file__).resolve().parent
KOMASET_DIR = HERE.parent
TOOLS = KOMASET_DIR.parent
for p in (str(TOOLS), str(KOMASET_DIR), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import komaset  # noqa: E402  (checksum, magic and Koma header code; one implementation)
import photoimport  # noqa: E402  (Photos import: folder, imported list, copy and check; host side only)
from grdusb import descriptors  # noqa: E402
from grdusb.protocol import (CHANNEL_COMMAND, CommandRefused, DirEntry, ProtocolError, encode_frame, fixed_command,  # noqa: E402
                             path_command, reply_code)
from grdusb.session import CameraError, Session, ShortWrite  # noqa: E402
from grdusb.stage import STOR_DONE, STOR_OPEN, chunks, encode_data_frame  # noqa: E402
from grdusb.transport import GateRefused, TransportError  # noqa: E402

BRIDGE_VERSION = "1.0.0"
HOST, PORT = "127.0.0.1", 8765
MAP_PATH = KOMASET_DIR / "settings-map.json"

PARAM_CUR = "/EXT0/PARAM.CUR"  # written by the camera at every boot
PARAM_DAT = "/EXT0/PARAM.DAT"  # imported by the camera at the next boot
DCIM = "/EXT0/DCIM"
POFF_ARG = "5"  # the value the emulator harness used; its meaning is unknown

# ------------------------------------------------------------------------------------------------ write allowlist
# USB writes are allowlisted to the PARAM.DAT push and -CCTL poff, plus the tether key sequence: -CCTL remtin,
# diaset,0..8, keyset,A,C with A in {0 release, 1 hold, 3 tap} and C in {2 clear-all, 26 RL1 half-press, 27 RL2
# full-press}, and remtout. The Remote task (a camera with a broken LCD) adds keyset,A,C for every key code C in 0..41
# except the refused ones below, with the same three actions. This table is the only place a write command can come
# from. Each row is
# (verb, the one argument it may carry), spelled out in full so the table is the audit. Nothing else is built, with no
# override. Refused key codes are absent in every action.
#
# What the Remote extension lets the host reach: MENU/OK and the arrows drive the whole camera menu, including FORMAT
# and RESTORE DEFAULTS. That is the reason card-script key macros were rejected before; here the OSD overlay
# (-LVFR mode 2) makes it sighted. All of it is RAM-only injection; none of it has fired on hardware.
TETHER_DIAL = 4  # diaset,4 -> app state 2, still shooting (dial map: 4 = still shooting)
KEY_CLEAR, KEY_HALF, KEY_FULL = 2, 26, 27
KEY_RELEASE, KEY_HOLD, KEY_TAP = 0, 1, 3
KEY_ACTIONS = {KEY_RELEASE: "release", KEY_HOLD: "hold", KEY_TAP: "tap"}
# Key table 0x18108c. Names are the firmware's; physical labels are inferred from the names only.
KEY_NAMES = ("MODE0", "MODE1", "MODE2", "MODE3", "SLIDE1", "SLIDE2", "POWER", "PLAY", "USB", "AVJACK", "DCJACK", "UP",
             "DOWN", "RIGHT", "LEFT", "TELE", "WIDE", "MACRO", "FLASH", "MENU", "TRASH", "CANCEL", "DISP", "ENTER",
             "QACCESS", "SHAKE", "RL1", "RL2", "DIAL_AC", "DIAL_BC", "PLUS", "MINUS", "DIAL2_AC", "DIAL2_BC", "PLUS2",
             "MINUS2", "DPUSH", "RING1", "RING2", "LENS1", "LENS2", "HEADUP")
assert len(KEY_NAMES) == 42
KEY_REFUSED = {
    6: "POWER: a press also posts power-off 0x720d",
    7: "PLAY: a long hold can post power-off (0x5129c); with 8 the firmware's power exclusion",
    8: "USB: the firmware's power exclusion (7 pressed / 8 released -> 0x720d)",
    9: "AVJACK: a jack level, not a button; injecting it may move the picture to the AV output (I)",
    24: "QACCESS: action 1 in state 9 calls 0xc4b64, switch to PTP mode",
    36: "DPUSH (ADJ push): action 1 in state 9 calls 0xc4b64, switch to PTP mode",
}
# Inert when injected (polarity 2) or not injectable (generated from the wheel inputs): allowed, but they do nothing.
KEY_INERT = {3, 4, 5, 10, 17, 18, 19, 21, 25, 30, 31, 34, 35, 39, 40}
KEYSET_CODES = tuple(c for c in range(42) if c not in KEY_REFUSED)
# The Remote panel, after tools/grdui/playground.py KEYS (the emulator's key map; ADJ there is 36, refused here).
PANEL_KEYS = {11: "Up", 12: "Down", 13: "Right", 14: "Left", 23: "MENU/OK", 22: "DISP", 15: "Zoom in (T)",
              16: "Zoom out (W)", 20: "Delete", 26: "Half press", 27: "Full press"}
TETHER_ARGS = (("remtin",) + tuple(f"diaset,{n}" for n in range(9))
               + tuple(f"keyset,{a},{c}" for a in (KEY_RELEASE, KEY_HOLD, KEY_TAP) for c in KEYSET_CODES)
               + ("remtout",))
WRITE_ALLOWLIST = (
    ("-STOR", PARAM_DAT),          # "-STOR <size> /EXT0/PARAM.DAT"; the firmware deletes an existing file first
    ("-CCTL", "poff," + POFF_ARG),  # saves CONFIG and arms a reboot (mg1_poff 0x661b0); never sent on hardware
) + tuple(("-CCTL", a) for a in TETHER_ARGS)  # RAM-only key/dial injection (S class); 200 on hardware, firing unproven
SETTINGS_SIZE = 4096

# -KSET <off>,<val>: one byte of the settings block in RAM (offsets relative to 0x1b000, decimal on the wire), the Koma
# live-settings verb (`ksettings` v1.1). The one write that is a RANGE rule rather than a
# row of WRITE_ALLOWLIST, and it matches the firmware's own check exactly:
#   KSET_WINDOW: the Koma rows 0x8b5..0x8cf, the look records 0x910..0x9ff, the FX table 0xa00..0xa14 and (Look IA)
#     the ADJ LEVER mask 0xa18..0xa1b and STANDARD's record 0xa1c..0xa27 (0xa15..0xa17 pad), any value
#     0..255 here (the bridge adds the map's per-row check). ALL of 0x8d0..0x8ff is refused (KSIO sub-header and its
#     reserve), as are the recipe store header 0x900..0x90f (a bad header makes Koma reset every recipe), everything
#     below 0x8b5 and from 0xa28 (checksum 0xffc included).
#   KSET_STOCK: the stock whitelist, a second table of single stock bytes with the exact values stock writes; after
#     each one the camera runs the stock apply (0x553d4), so exposure / ISO / WB / metering / IMG SET take effect
#     live. The camera answers `451 -KSET busy` while its menu or ADJ screen is open, while it is not idle in still
#     record, or while its monitor is stopped (the bridge maps that to HTTP 409 camera_busy).
# RAM only: the camera saves the block itself at a normal power-off. Never sent on hardware yet.
KSET_WINDOW = ((0x8B5, 0x8CF), (0x910, 0x9FF), (0xA00, 0xA27))
KSET_STOCK = {
    0x053: frozenset(range(1, 26, 2)),          # EXPOSURE COMP.: odd 1..25 (13 = 0.0, -2.0..+2.0 in 1/3 EV)
    0x059: frozenset((0, 1, 2, 4, 6, 7, 8)),    # ISO SETTING: AUTO, 64, 100, 200, 400, 800, 1600
    0x054: frozenset((0, 1, 2, 3, 4, 5, 7)),    # WHITE BALANCE (7 = DETAIL)
    0x0A6: frozenset(range(0, 17)),             # WB DETAIL 0..16
    0x050: frozenset((0, 1, 2)),                # PHOTOMETRY: CENTER 0, SPOT 1, MULTI 2
    0x030: frozenset(range(0, 7)),              # IMG SET 0..6
    **{off: frozenset(range(1, 6)) for off in range(0x031, 0x037)},  # SETTING1 / SETTING2 contrast, sharpness, color
    0x1A9: frozenset((1, 2, 3, 4, 6)),          # B&W (TE) TONE
    0x1AA: frozenset(range(1, 6)),              # B&W (TE) DENSITY
}
KGET_MAX = 4096  # -KGET <off>,<len>: len 1..4096, off + len within the 4096-byte block
# -PEEK <addr_hex>,<len>: len 1..256, [addr, addr + len) inside [PEEK_LO, PEEK_END) = the firmware state / pool RAM and
# the static resident block (ksettings v1.4; the camera answers "501 -PEEK range" outside it)
PEEK_MAX, PEEK_LO, PEEK_END = 256, 0x1A8000, 0x3E5000
_KSET_ARG = re.compile(r"(0|[1-9]\d{0,3}),(0|[1-9]\d{0,2})")
_KGET_ARG = re.compile(r"(0|[1-9]\d{0,3}),([1-9]\d{0,3})")
# -KSTA (ksettings v1.3+, diagnostic): no arguments; one data frame of big-endian u32, the version word 0x4b53LLNN
# ('KS', layout LL, NN fixed words including itself) then these, in order. Mirrors tools/koma/features/ksettings.py
# KSTA_WORDS (test_ksettings checks the two match). (name, address, load width on the camera). Layout 1 (v1.3) is the
# first 27; layout 2 (v1.4) appends the snapshot words and may be followed by an extension block (waitdiag's ring).
KSTA_LAYOUT = 2
KSTA_WORDS = [
    ("usb_23a86c", 0x23A86C, 4), ("usb_23a870", 0x23A870, 4), ("run_state", 0x23A874, 4),
    ("usb_23a884", 0x23A884, 4), ("usb_attached", 0x23A888, 4), ("service_boot", 0x23A88C, 4),
    ("poweroff_reason", 0x23A890, 4), ("usb_23a89c", 0x23A89C, 4),
    ("remtin_flag", 0x1BD1B8, 4), ("app_state", 0x1BD1C0, 4), ("rec_dispatch", 0x1FB9BC, 4),
    ("monitor_run", 0x1F502F, 1),
    ("apo_2b9e50", 0x2B9E50, 4), ("apo_2b9e54", 0x2B9E54, 4), ("apo_2b9e58", 0x2B9E58, 4),
    ("end_entered", 0x1BF0DC, 4), ("rec_error", 0x1BEFAC, 4), ("rec_status_b0", 0x1BEFB0, 1),
    ("rec_status_b1", 0x1BEFB1, 1),
    ("batt_raw", 0x2B9E66, 2), ("batt_end_thr", 0x2B9E80, 2), ("batt_level", 0x2B9E92, 1),
    ("batt_index", 0x2B9E93, 1), ("batt_type", 0x2B9E95, 1), ("batt_strobe", 0x2B9E74, 1),
    ("cam_state", 0x1A918C, 4), ("tick", 0x1A8A00, 4),
]
KSTA_WORDS_V1 = list(KSTA_WORDS)
KSTA_WORDS += [
    ("lcd_sem_1a8d14", 0x1A8D14, 4), ("lcd_sem_1a8d18", 0x1A8D18, 4), ("lcd_1a8d40", 0x1A8D40, 4),
    ("lcd_1a8d4c", 0x1A8D4C, 4), ("sem_1a9210", 0x1A9210, 4), ("sem_1a923c", 0x1A923C, 4),
    ("disp_23a880", 0x23A880, 4), ("w_1b97e4", 0x1B97E4, 4), ("w_1b97e8", 0x1B97E8, 4), ("w_1bf1d0", 0x1BF1D0, 4),
    ("hs_1bec44", 0x1BEC44, 4), ("hs_1bec48", 0x1BEC48, 4), ("hs_1bec4c", 0x1BEC4C, 4),
]
KSTA_N = len(KSTA_WORDS) + 1
KSTA_VERSION = 0x4B530000 | KSTA_LAYOUT << 8 | KSTA_N
KSTA_V1_VERSION = 0x4B530100 | len(KSTA_WORDS_V1) + 1

# waitdiag (tools/koma/features/waitdiag.py, temporary): the -KSTA extension block. Mirrors waitdiag's layout constants
# (test_waitdiag checks they match).
WD_MAGIC_HI = 0x5744
WD_HEADER = ["magic", "seq", "overflow", "stale_evict", "lost"]
WD_PHASES = ["msg_idcode", "msg_t0", "msg_t1", "msgslow_idcode", "msgslow_t0", "msgslow_t1",
             "res_idcode", "res_msg_t0", "res_t0", "res_t1", "res_ret",
             "h7347c_t0", "h7347c_t1", "h7347c_ret", "slow_target", "slow_t0", "slow_t1",
             "t73770_t0", "t73770_t1", "t73770_ret", "dec_idret", "dec_tick", "dec4b_tick"]
WD_HANDLES = [0x1A8D14, 0x1A8D18, 0x1A9210, 0x1A923C, 0x1F4C5C, 0x1C324C]
WD_HDR_WORDS, WD_NREC, WD_RECW = 60, 16, 12
WD_RECORD = ["status", "kind", "caller", "parent", "task", "object", "timeout", "entry", "exit", "ret", "detail",
             "dispatch"]
WD_KINDS = {1: "sem_wait", 3: "queue_send", 4: "queue_recv", 5: "flag_wait", 6: "sem_84bd0", 7: "hs_670d8",
            8: "hs_67178", 9: "hs_672f4", 10: "hs_8e882"}


def waitdiag_decode(words: list) -> dict:
    """waitdiag's block (the -KSTA 'ext' words) -> {header..., "phases": {...}, "prov": {handle_var: {...}},
    "records": [non-free records in ring order, each a dict with "state" pending/done and "seq"]}."""
    if not words or words[0] >> 16 != WD_MAGIC_HI or (words[0] & 0xFF) != len(words) or \
            len(words) != WD_HDR_WORDS + WD_NREC * WD_RECW:
        raise ProtocolError(f"waitdiag block {words[0] if words else None!r} with {len(words)} words")
    out = {n: words[i] for i, n in enumerate(WD_HEADER)}
    out["version"] = (words[0] >> 8) & 0xFF
    p0 = len(WD_HEADER)
    out["phases"] = {n: words[p0 + i] for i, n in enumerate(WD_PHASES)}
    q0 = p0 + len(WD_PHASES)
    out["prov"] = {f"{h:#x}": dict(zip(("acq_lr", "acq_tick", "rel_lr", "rel_tick"), words[q0 + 4 * i:q0 + 4 * i + 4]))
                   for i, h in enumerate(WD_HANDLES)}
    recs = []
    for i in range(WD_NREC):
        r = dict(zip(WD_RECORD, words[WD_HDR_WORDS + i * WD_RECW:WD_HDR_WORDS + (i + 1) * WD_RECW]))
        st = r["status"] & 0xFF
        if st == 0:
            continue
        r.update(slot=i, seq=r["status"] >> 8, state={1: "pending", 2: "done"}.get(st, f"?{st}"),
                 kind_name=WD_KINDS.get(r["kind"], f"kind{r['kind']}"))
        recs.append(r)
    out["records"] = recs
    return out


def ksta_decode(data: bytes) -> dict:
    """A -KSTA frame -> {"version": word, name: value, ...}, plus "ext" (raw words after the fixed part, layout 2) and
    "waitdiag" (decoded, when the extension is waitdiag's block). Layout 1 (ksettings v1.3, 28 words) still decodes.
    ProtocolError on a length or layout this bridge does not know (a newer firmware layout must be added here first)."""
    if len(data) < 4 or len(data) % 4:
        raise ProtocolError(f"-KSTA returned {len(data)} bytes")
    words = struct.unpack(f">{len(data) // 4}I", data)
    if words[0] == KSTA_V1_VERSION and len(words) == len(KSTA_WORDS_V1) + 1:
        return {"version": words[0], **{n: v for (n, _, _), v in zip(KSTA_WORDS_V1, words[1:])}}
    if words[0] != KSTA_VERSION or len(words) < KSTA_N:
        raise ProtocolError(f"-KSTA layout {words[0]:#010x} with {len(words)} words (bridge knows {KSTA_VERSION:#010x})")
    out = {"version": words[0], **{n: v for (n, _, _), v in zip(KSTA_WORDS, words[1:KSTA_N])}}
    if len(words) > KSTA_N:
        out["ext"] = list(words[KSTA_N:])
        if words[KSTA_N] >> 16 == WD_MAGIC_HI:
            out["waitdiag"] = waitdiag_decode(out["ext"])
    return out


def kset_writable(off: int) -> bool:
    """off is inside the Koma window or a stock whitelist byte (the value is checked separately)."""
    return off in KSET_STOCK or any(lo <= off <= hi for lo, hi in KSET_WINDOW)


def kset_value_ok(off: int, val: int) -> bool:
    """val may go to off: 0..255 in the Koma window, exactly the stock set for a whitelist byte."""
    if off in KSET_STOCK:
        return val in KSET_STOCK[off]
    return kset_writable(off) and 0 <= val <= 255


def _kset_where() -> str:
    return (", ".join(f"0x{a:x}..0x{b:x}" for a, b in KSET_WINDOW) + " or the stock bytes "
            + ", ".join(f"0x{o:03x}" for o in sorted(KSET_STOCK)))


class WriteRefused(CommandRefused):
    """The bridge will not build this write command."""


def write_command(verb: str, argument: str, size: int | None = None) -> bytes:
    """Builds a write command only if (verb, argument) is a row of WRITE_ALLOWLIST, or a -KSET kset_value_ok accepts.
    Raises WriteRefused otherwise."""
    if verb == "-KSET":
        m = _KSET_ARG.fullmatch(argument or "")
        if not m or int(m.group(2)) > 255 or not kset_value_ok(int(m.group(1)), int(m.group(2))):
            raise WriteRefused(f"-KSET takes <off>,<val 0..255> with off in {_kset_where()} (stock bytes only with "
                               f"their stock values), not {argument!r}")
        return f"-KSET {argument}\r\n".encode("ascii")
    if (verb, argument) not in WRITE_ALLOWLIST:
        raise WriteRefused(f"write not allowlisted: {verb} {argument!r} (allowed: "
                           + ", ".join(f"{v} {a}" for v, a in WRITE_ALLOWLIST) + ")")
    if verb == "-STOR":
        if size != SETTINGS_SIZE:
            raise WriteRefused(f"-STOR of {PARAM_DAT} must be exactly {SETTINGS_SIZE} bytes, not {size}")
        return f"-STOR {size} {argument}\r\n".encode("ascii")
    return f"{verb} {argument}\r\n".encode("ascii")


# ------------------------------------------------------------------------------------------------ read commands
_NAME83 = re.compile(r"^[A-Z0-9_~$!#%&'()@^{}-]{1,8}(\.[A-Z0-9_~$!#%&'()@^{}-]{1,3})?$")


def _card_path(path: str) -> str:
    """Card-only read paths: /EXT0 and 8.3 components. Internal memory (/IROM) is never read by the bridge."""
    parts = path.split("/")
    if parts[:2] != ["", "EXT0"] or len(path) > 255 or not all(_NAME83.match(p) for p in parts[2:]):
        raise CommandRefused(f"not a card path the bridge reads: {path!r}")
    return path


def read_command(verb: str, path: str | None = None) -> bytes:
    """Read-only verbs, each hardware-confirmed. -VER/-NLST/RETR come from grdusb itself."""
    if verb == "-VER":
        return fixed_command("version")
    if verb == "PWD":
        return b"PWD\r\n"
    if verb == "-NLST":
        return path_command("list", _card_path(path))
    if verb == "RETR":
        return path_command("retrieve", _card_path(path))
    if verb in ("-INFO", "-FDAT"):
        return f"{verb} {_card_path(path)}\r\n".encode("ascii")
    if verb == "-LVFR":  # live-view frame pull; reads RAM, writes nothing
        # mode 2 = OSD page pull; mode 3 (liveview 1.3) = a with a & 0xf == 3, a >> 4 = JPEG scale (0 = camera default)
        m = re.fullmatch(r"(0|[1-9]\d{0,6}),(0|[1-9]\d{0,4})", path or "")
        a = int(m.group(1)) if m else -1
        if not m or int(m.group(2)) > 0xFFFF or not (a <= 2 or (a & 0xF == 3 and a >> 4 <= 0xFFFF)):
            raise CommandRefused(f"-LVFR takes <mode 0|1|2|3+16*scale>,<seq 0..65535>, not {path!r}")
        return f"-LVFR {path}\r\n".encode("ascii")
    if verb == "-KGET":  # settings block bytes from RAM (Looks task); reads, writes nothing
        m = _KGET_ARG.fullmatch(path or "")
        if not m or int(m.group(2)) > KGET_MAX or int(m.group(1)) + int(m.group(2)) > KGET_MAX:
            raise CommandRefused(f"-KGET takes <off>,<len 1..4096> inside the 4096-byte block, not {path!r}")
        return f"-KGET {path}\r\n".encode("ascii")
    if verb == "-KSTA":  # ksettings v1.3 state snapshot: plain RAM loads on the camera, no argument, writes nothing
        if path:
            raise CommandRefused(f"-KSTA takes no argument, not {path!r}")
        return b"-KSTA\r\n"
    if verb == "-PEEK":  # ksettings v1.4 RAM peek: <addr hex, 1..7 digits>,<len 1..256> inside RAM; writes nothing
        m = re.fullmatch(r"([0-9a-f]{1,7}),([1-9]\d{0,2})", path or "")
        if not m or int(m.group(2)) > PEEK_MAX or int(m.group(1), 16) < PEEK_LO or \
                int(m.group(1), 16) + int(m.group(2)) > PEEK_END:
            raise CommandRefused(f"-PEEK takes <addr hex>,<len 1..{PEEK_MAX}> inside RAM {PEEK_LO:#x}..{PEEK_END - 1:#x}, "
                                 f"not {path!r}")
        return f"-PEEK {path}\r\n".encode("ascii")
    raise CommandRefused(f"not a read command the bridge sends: {verb!r}")


def parse_info(reply: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"200 (\d+) byte W=(\d+) H=(\d+) K=\d+ for -INFO\.", reply.strip())
    if not m:
        raise ProtocolError(f"unexpected -INFO reply: {reply!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def parse_fdat(reply: str) -> str:
    m = re.fullmatch(r"200 date=(.*) for -FDAT\.", reply.strip())
    if not m:
        raise ProtocolError(f"unexpected -FDAT reply: {reply!r}")
    d = re.fullmatch(r"(\d{4}):(\d\d):(\d\d) (\d\d:\d\d:\d\d)", m.group(1).strip())
    return f"{d.group(1)}-{d.group(2)}-{d.group(3)} {d.group(4)}" if d else ""


class BridgeSession(Session):
    on_keyset = None  # called after every -CCTL keyset (the Auto Power Off keepalive keys on these)

    """grdusb's Session plus the bridge's few extra verbs. Commands come from grdusb's read-only builders (-VER, -NLST,
    RETR), read_command (PWD, -INFO, -FDAT) or write_command (the allowlist). There is no generic send."""

    def __init__(self, transport):
        super().__init__(transport)
        self.history: list[str] = []

    def _ask(self, command: bytes) -> str:
        self.history.append(command.decode("ascii").rstrip("\r\n"))
        return super()._ask(command)

    def pwd(self) -> str:
        return self._ask(read_command("PWD")).strip()

    def info(self, path: str) -> tuple[int, int, int]:
        reply = self._ask(read_command("-INFO", path))
        if reply_code(reply) != "200":
            raise CameraError(reply.strip())
        return parse_info(reply)

    def fdat(self, path: str) -> str:
        reply = self._ask(read_command("-FDAT", path))
        if reply_code(reply) != "200":
            raise CameraError(reply.strip())
        return parse_fdat(reply)

    def live_frame(self, mode: int, seq: int, scale: int | None = None) -> tuple[str, bytes | None]:
        """One -LVFR pull: (reply, payload). payload is None when the camera did not answer 150 (451 = monitor not
        running). On 150 the data frame(s) are read up to the closing 250, the same framing as RETR/-PRET.
        scale (mode 3 only): the JPEG scale to ask for, sent as a = 3 | scale << 4; None = the camera's default."""
        reply = self._ask(read_command("-LVFR", f"{lvfr_arg(mode, scale)},{seq}"))
        if reply_code(reply) != "150":
            return reply.strip(), None
        out = bytearray()
        closing = self._data_until_reply(None, out.extend)
        if reply_code(closing) != "250":
            raise CameraError(closing.strip())
        return reply.strip(), bytes(out)

    def ram_read(self, off: int, length: int) -> bytes:
        """-KGET off,len: 150, one data frame of the raw bytes, 250. The length must match what was asked."""
        reply = self._ask(read_command("-KGET", f"{off},{length}"))
        if reply_code(reply) != "150":
            raise CameraError(reply.strip())
        out = bytearray()
        closing = self._data_until_reply(None, out.extend)
        if reply_code(closing) != "250":
            raise CameraError(closing.strip())
        if len(out) != length:
            raise ProtocolError(f"-KGET {off},{length} returned {len(out)} bytes")
        return bytes(out)

    def ram_state(self) -> dict:
        """-KSTA: 150, one data frame of big-endian words, 250 -> the decoded dict (ksta_decode). Read-only on the
        camera and answered in any app state; a camera without ksettings v1.3 answers 501 (CameraError)."""
        reply = self._ask(read_command("-KSTA"))
        if reply_code(reply) != "150":
            raise CameraError(reply.strip())
        out = bytearray()
        closing = self._data_until_reply(None, out.extend)
        if reply_code(closing) != "250":
            raise CameraError(closing.strip())
        return ksta_decode(bytes(out))

    def peek(self, addr: int, length: int) -> bytes:
        """-PEEK addr,len (ksettings v1.4): raw RAM bytes, read-only on the camera. 150, one data frame, 250."""
        reply = self._ask(read_command("-PEEK", f"{addr:x},{length}"))
        if reply_code(reply) != "150":
            raise CameraError(reply.strip())
        out = bytearray()
        closing = self._data_until_reply(None, out.extend)
        if reply_code(closing) != "250":
            raise CameraError(closing.strip())
        if len(out) != length:
            raise ProtocolError(f"-PEEK {addr:x},{length} returned {len(out)} bytes")
        return bytes(out)

    def ram_write(self, off: int, value: int) -> str:
        """-KSET off,val (KSET_WINDOW or KSET_STOCK). Returns the reply line, 200 or not."""
        return self._ask(write_command("-KSET", f"{off},{value}")).strip()

    def read_file(self, path: str) -> bytes:
        out = bytearray()
        self.retrieve(_card_path(path), out.extend)
        return bytes(out)

    def store_settings(self, data: bytes) -> None:
        """-STOR of PARAM.DAT (allowlist row 1), the same frames as grdusb stage-update."""
        reply = self._ask(write_command("-STOR", PARAM_DAT, len(data)))
        if reply.rstrip("\r\n") != STOR_OPEN:
            raise CameraError(f"-STOR refused: {reply.strip()}")
        for chunk in chunks(data):
            frame = encode_data_frame(chunk)
            self.requests_attempted += 1
            written = self._transport.write(frame)
            if written != len(frame):
                raise ShortWrite(f"bulk write accepted {written!r} of {len(frame)} bytes")
        closing = self._reply()
        if closing.rstrip("\r\n") != STOR_DONE:
            raise CameraError(f"-STOR did not complete: {closing.strip()}")

    def remote(self, argument: str) -> str:
        """One -CCTL remote-control subcommand from the allowlist. Returns the reply line, 200 or not."""
        reply = self._ask(write_command("-CCTL", argument)).strip()
        if argument.startswith("keyset,") and self.on_keyset:
            self.on_keyset()
        return reply

    def power_off(self) -> str:
        """-CCTL poff,N (allowlist row 2). 200 and 451 are both returned: in 2.40 the handler 0xcd000 answers 200 only
        when mg1_poff returns 0, but mg1_poff returns 2 after it saved CONFIG and posted the restart (0x66ab4) and -1
        when it failed, so a 451 does not tell an armed restart from a failure (static reading of the decompile; in the
        emulator mg1_poff failed before 0x66ab4, saving CONFIG.TST, and answered 451). Anything else is an error."""
        reply = self._ask(write_command("-CCTL", "poff," + POFF_ARG))
        if reply_code(reply) not in ("200", "451"):
            raise CameraError(reply.strip())
        return reply.strip()


# ------------------------------------------------------------------------------------------------ settings envelope

def load_map():
    return komaset.Map(MAP_PATH)


def check_summary(data: bytes) -> dict:
    """The komaset `check` result as a dict (size, magic, checksum, Koma header), via komaset's own functions."""
    m = load_map()
    out = {"size": len(data), "size_ok": len(data) == m.size}
    if not out["size_ok"]:
        out.update(ok=False, text=f"size: {len(data)} BAD (expected {m.size})")
        return out
    blk = bytearray(data)
    magic = bytes(blk[m.magic_off:m.magic_off + len(m.magic)])
    comp, stored = komaset.compute_checksum(blk, m), komaset.stored_checksum(blk, m)
    kv = komaset.koma_info(blk)
    out.update(magic=magic.hex(), magic_ok=magic == m.magic, checksum_stored=f"0x{stored:08x}",
               checksum_computed=f"0x{comp:08x}", checksum_ok=comp == stored, koma_version=kv)
    out["ok"] = out["magic_ok"] and out["checksum_ok"]
    out["text"] = "\n".join([
        f"size: {len(data)} ok", f"magic: {magic.hex()} {'ok' if out['magic_ok'] else 'BAD'}",
        f"checksum: stored 0x{stored:08x} computed 0x{comp:08x} {'ok' if out['checksum_ok'] else 'BAD'}",
        f"koma: {'absent' if kv is None else 'version ' + str(kv)}"])
    return out


# ------------------------------------------------------------------------------------------------ backends

def usb_mode_name(product: int) -> str:
    return {descriptors.PRODUCT_ORIGINAL: "ORIGINAL", 0x0109: "MASS STORAGE", 0x0331: "PTP"}.get(product, "UNKNOWN")


class MockBackend:
    simulated = True
    name = "mock"

    def __init__(self, body=None):
        from mockcard import MockBody
        self.body = body or MockBody()

    def scan(self) -> list:
        return [self.body.device()] if self.body.present() else []

    def open(self):
        transport = self.body.open()
        transport.bring_up()
        return transport


class RealBackend:
    """pyusb/libusb via grdusb.transport. Enumeration is descriptor-only and opens nothing."""
    simulated = False
    name = "real"

    def scan(self) -> list:
        from grdusb.transport import enumerate_ricoh
        return enumerate_ricoh()

    def open(self):
        from grdusb.transport import open_gated
        transport = open_gated()
        try:
            transport.bring_up()
        except Exception:
            transport.close()
            raise
        return transport


class BridgeError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


class Link:
    """One camera connection, opened lazily and kept. -VER is sent once per connection, never by polling: on hardware
    a -VER is followed by the camera's screen going dark, so /status must not repeat it.
    Polling only enumerates descriptors. All camera traffic is serialised by one lock."""

    def __init__(self, backend):
        self.backend = backend
        self.lock = threading.Lock()
        self.transport = None
        self.session: BridgeSession | None = None
        self.firmware = None
        self.message = "No camera yet."
        # why no camera is open, for the app's words: "usb" (no usable USB library), "two" (more than one Ricoh device),
        # "open" (on the bus, could not be opened), "mode" (USB CONNECTION not ORIGINAL), None otherwise
        self.problem = None
        self.usb_mode = None
        self.photo_meta: dict = {}  # (path, size, stamp) -> (date, w, h)
        self.cache: "collections.OrderedDict[str, bytes]" = collections.OrderedDict()
        self.cache_bytes = 0
        self.tether = None  # {"active": bool} after /tether/start
        self.tether_stops = 0  # bumped by every /tether/stop: a start that waited for the lock behind one is dropped
        # tether timings (seconds); tests shorten them
        self.af_wait_s, self.press_s, self.first_poll_s, self.poll_s, self.shot_timeout_s = 0.7, 0.3, 1.0, 0.5, 30.0
        # after diaset,4 the camera can sit in USB app state 9 and answer -LVFR 0 with 451 until a key press:
        # _start polls -LVFR 0 until it answers 150, up to this long, before any key is pressed. 30 s: a second-session
        # arm stalls ~16 s (5.6 s in state 9 + a 10.0 s record-init wait) and a first arm can take over 6 s.
        # lvfr_150_s is the last wait's measured time to the first 150 (None: that wait timed out or ended on another
        # reply)
        self.lvfr_wait_s, self.lvfr_poll_s = 30.0, 0.4
        self.lvfr_150_s, self.lvfr_rearms_used = None, 0
        # re-arm: remtin + diaset,4 can both answer 200 and not take (-LVFR stays
        # 451, keyset fires nothing; a later diaset,4 got 150 at once). With no 150 lvfr_rearm_s after the last arm,
        # the wait logs a -KSTA snapshot and sends remtin + diaset,4 again, at most lvfr_rearms times in lvfr_wait_s
        self.lvfr_rearm_s, self.lvfr_rearms = 5.0, 5
        self.stable_poll_s = 0.3  # unused by the shot path; kept for callers that set it
        # shot settle: no card command until -KSTA shows the camera done (cam_state 2, monitor_run 1)
        self.ksta_poll_s, self.settle_unseen_s = SETTLE_KSTA_POLL_S, SETTLE_UNSEEN_S
        self.settle_delay_s, self.list_retry_s = SETTLE_DELAY_S, SETTLE_LIST_RETRY_S
        # the connect-time -VER arms a 3 s firmware timer (0xccb5c -> 0x114824) that posts message 0xa201; in record
        # state 2 stock ends the runner with 12-30-05, tether 1.5+ swallows it (in USB state 9 the stock runner
        # handles it: LCD off / lens retract per the static read). With tether 1.6 remtin follows connect at once.
        # ver_settle_s > 0 delays remote control until that long after -VER; it is off for every backend (tests set it
        # explicitly).
        self.ver_settle_s = 0.0
        self.ver_at = None        # link.clock() when this connection's -VER was answered
        self.sleep, self.clock = time.sleep, time.monotonic
        # live view: the stream loop takes the lock per frame only; a shoot/focus raises live_hold first so the loop
        # stops asking (Python locks are not fair, so the flag is what lets the shoot in)
        self.live = LiveStats(time.monotonic)
        self.live_gen = 0         # bumped to end the running stream (a new stream, /live/stop, /tether/stop)
        self.live_seq = 0
        self.live_owner = None    # the generation whose stream owns self.live
        self.live_hold = 0        # > 0 while a shoot or focus wants the camera
        self.live_max_fps = 30.0  # the loop never asks faster than this
        self.live_pause_poll_s = 0.05
        # a 451 mid-stream is transient for this long: 10 s, since live view comes back ~7 s after a tethered
        # shot on hardware; the page shows "camera busy" meanwhile (/live/status paused, reason)
        self.live_off_grace_s, self.live_off_retry_s = 10.0, 0.1
        # a RETR / -INFO / -FDAT inside remote control drops live view on hardware: with
        # rearm_after_read on (the HTTP server sets it; the selftest re-arms itself) run() sends diaset,<dial> again
        # after any request that read the card while remote control was on. read_rearms counts them
        self.rearm_after_read = False
        self.read_rearms = 0
        # the OSD stream (-LVFR mode 2, Remote task) is a second, independent channel with its own generation
        self.osd = LiveStats(time.monotonic)
        self.osd_gen = 0
        self.osd_owner = None
        self.osd_max_fps = 5.0    # menus change at key-press speed; ~5 a second is enough
        self.interval = Interval(self)
        # Auto Power Off keepalive (tether v1.3 renews APO on every -CCTL keyset; a record-side re-enable can still fire
        # in a long idle after a shot): while remote control is on and nothing was sent for keepalive_idle_s, send
        # keyset,0,27 (release of the unpressed full-press key; allowlisted; harmless). Skipped while anything holds the
        # camera lock or a shoot/key hold is pending (a running stream sends every frame, so it never idles anyway).
        # It keys on the age of the last -CCTL keyset (any A,C), not the last command: a live stream or an editing session
        # sends -LVFR/-KSET all the time and never renews APO. It takes the lock between stream pulls (ka_wait makes the
        # stream loop stop asking for a moment, without touching the UI's paused state); keyset takes well under a second.
        self.last_keyset = self.clock()
        self.ka_wait = 0
        self.keepalive_lock_wait_s = 1.0
        self.keepalive_idle_s, self.keepalive_check_s = 20.0, 5.0
        self.keepalives = 0
        self.log = lambda text: None
        # -KSET busy retry: right after an EV change the camera answers
        # `451 -KSET busy` for a moment (the EV overlay / AE redraw sets the flag ks_idle checks). ram_write retries a
        # busy reply every kset_busy_poll_s for up to kset_busy_wait_s before reporting camera_busy.
        self.kset_busy_wait_s, self.kset_busy_poll_s = 5.0, 0.25

    def keepalive_tick(self) -> bool:
        """One keepalive check; True if keyset,0,27 was sent. Called every keepalive_check_s by start_keepalive."""
        if not self.remote_on or self.live_hold or self.clock() - self.last_keyset < self.keepalive_idle_s:
            return False
        self.ka_wait += 1  # the stream loop pauses its pulls until we have had the lock
        try:
            got = self.lock.acquire(timeout=self.keepalive_lock_wait_s)  # a shoot has it: give up, try next tick
        finally:
            self.ka_wait -= 1
        if not got:
            return False
        try:
            if self.session is None or not self.remote_on:
                return False
            log = []
            try:
                _cctl(self.session, f"keyset,{KEY_RELEASE},{KEY_FULL}", log)
            except (TransportError, ProtocolError) as error:
                self._drop(f"Lost the camera: {type(error).__name__}: {error}")
                return False
            self.keepalives += 1
            self.log(f"komabridge: keepalive after {self.keepalive_idle_s:g} s idle in remote control: "
                     f"{log[0]['command']} -> {log[0]['reply']}")
            return True
        finally:
            self.lock.release()

    def _note_keyset(self):
        self.last_keyset = self.clock()

    def start_keepalive(self):
        if getattr(self, "_keepalive_started", False):  # one thread per Link (main() and the webcam both ask)
            return
        self._keepalive_started = True

        def loop():
            while True:
                time.sleep(self.keepalive_check_s)
                try:
                    self.keepalive_tick()
                except Exception as error:  # noqa: BLE001  (never let the keepalive thread die)
                    self.log(f"komabridge: keepalive error: {type(error).__name__}: {error}")
        threading.Thread(target=loop, daemon=True, name="keepalive").start()

    def hold_live(self):
        with self.live.lock:
            self.live_hold += 1
            self.live.paused = self.live.running

    def release_live(self):
        with self.live.lock:
            self.live_hold -= 1
            if not self.live_hold:
                self.live.paused = False

    def stop_live(self, channel: str | None = None):
        """End the running stream(s): channel "video" (modes 1/0), "osd" (mode 2) or None for both."""
        if channel in (None, "video"):
            self.live_gen += 1
        if channel in (None, "osd"):
            self.osd_gen += 1

    @property
    def remote_on(self) -> bool:
        return bool(self.tether and self.tether.get("active"))

    def _drop(self, message: str, problem: str | None = None):
        if self.transport is not None:
            try:
                self.transport.close()
            except Exception:
                pass
        self.transport = self.session = None
        self.firmware = None
        self.ver_at = None
        self.cache.clear()
        self.cache_bytes = 0
        self.photo_meta.clear()
        self.tether = None
        self.message = message
        self.problem = problem

    def _ensure(self) -> bool:
        """Caller holds the lock. Returns True with an open session, else False with self.message set."""
        try:
            devices = self.backend.scan()
        except Exception as error:  # libusb missing, permissions, ...
            self._drop(f"USB is not available: {type(error).__name__}: {error}", "usb")
            return False
        if not devices:
            self.usb_mode = None
            self._drop("No camera on USB.")
            return False
        if len(devices) > 1:
            self._drop(f"{len(devices)} Ricoh devices on USB; connect one camera only.", "two")
            return False
        self.usb_mode = usb_mode_name(devices[0].product)
        if self.usb_mode != "ORIGINAL":
            self._drop(f"A Ricoh camera is connected in {self.usb_mode} mode; set USB CONNECTION to ORIGINAL.", "mode")
            return False
        if self.session is not None:
            return True
        try:
            self.transport = self.backend.open()
            self.session = BridgeSession(self.transport)
            self.session.on_keyset = self._note_keyset
            self.firmware = self.session.version()
            self.ver_at = self.clock()
            self.message = "Camera connected."
            self.problem = None
            return True
        except (GateRefused, TransportError, ProtocolError, CameraError, CommandRefused) as error:
            self._drop(f"Could not open the camera: {type(error).__name__}: {error}", "open")
            return False

    def status(self) -> dict:
        busy = not self.lock.acquire(timeout=0.2)
        try:
            if not busy:
                self._ensure()
            connected = self.session is not None
            problem = None if connected else self.problem
            if not connected and problem is None and self.usb_mode and self.usb_mode != "ORIGINAL":
                problem = "mode"
            return {
                "bridge": "komabridge", "bridge_version": BRIDGE_VERSION, "backend": self.backend.name,
                "simulated": self.backend.simulated, "connected": connected, "busy": busy,
                "camera": {"model": "GR DIGITAL", "firmware": self.firmware,
                           "firmware_display": self.firmware.lstrip("0") if self.firmware else None,
                           "usb_mode": self.usb_mode} if connected else None,
                "tether": self.remote_on, "interval": self.interval.snapshot(),
                "live": self.live.snapshot(), "osd": self.osd.snapshot(),
                "usb_mode": self.usb_mode, "message": self.message, "problem": problem,
                "webcam": bool(getattr(self, "webcam", None) and self.webcam.holds),
                **({"emu": self.backend.info()} if hasattr(self.backend, "info") else {}),
            }
        finally:
            if not busy:
                self.lock.release()

    def run(self, fn):
        with self.lock:
            if not self._ensure():
                raise BridgeError(503, self.message)
            mark = len(self.session.history)
            try:
                out = fn(self.session)
                self._rearm_if_read(mark)
                return out
            except (TransportError, ProtocolError) as error:
                self._drop(f"Lost the camera: {type(error).__name__}: {error}")
                raise BridgeError(503, self.message)
            except CameraError as error:
                raise BridgeError(502, f"The camera answered: {error}")
            finally:
                self.last_sent = self.clock()

    def _rearm_if_read(self, mark: int):
        """Caller holds the lock. After a card read inside remote control, diaset,<dial> (allowlisted) brings live view
        back; the stream's 451 grace covers the camera's restart. No wait for -LVFR 150 here: the stream retries."""
        if not (self.rearm_after_read and self.remote_on and self.session is not None):
            return
        if not any(c.split(" ")[0] in ("RETR", "-INFO", "-FDAT") for c in self.session.history[mark:]):
            return
        dial = self.tether.get("dial", TETHER_DIAL)
        log = []
        _cctl(self.session, f"diaset,{dial}", log)
        self.read_rearms += 1
        self.log(f"komabridge: card read inside remote control drops live view; {log[0]['command']} -> {log[0]['reply']}")

    # cache of whole files read this connection (photos only), so the lightbox reuses the thumbnail's transfer
    CACHE_LIMIT = 64 << 20

    def cached_read(self, session: BridgeSession, path: str) -> bytes:
        if path in self.cache:
            self.cache.move_to_end(path)
            return self.cache[path]
        data = session.read_file(path)
        self.cache[path] = data
        self.cache_bytes += len(data)
        while self.cache_bytes > self.CACHE_LIMIT and len(self.cache) > 1:
            _, old = self.cache.popitem(last=False)
            self.cache_bytes -= len(old)
        return data

    def forget_after_poff(self):
        self._drop("The camera was told to restart.")


# ------------------------------------------------------------------------------------------------ endpoint logic

def read_root_file(s: BridgeSession, path: str) -> bytes | None:
    """RETR of a card-root file; None when the camera answers 450 (no such file). Never lists /EXT0: on a big card
    (widefs FAT32) -NLST /EXT0 times out and poisons the session.
    The Koma feature rootlist 1.0 (tools/koma/features/rootlist.py) fixes this in firmware; it is verified only in the
    emulator and the mock, so keep this workaround until it is proven on a 64 GB card."""
    try:
        return s.read_file(path)
    except CameraError as error:
        if str(error).startswith("450"):
            return None
        raise


def get_settings(link: Link) -> dict:
    def fn(s: BridgeSession):
        data = read_root_file(s, PARAM_CUR)
        if data is None:
            raise BridgeError(404, "The card has no PARAM.CUR. The camera writes it at start-up once Koma's settings "
                                   "export is installed.", code="no_param_cur")
        return data
    data = link.run(fn)
    return {"name": "PARAM.CUR", "path": PARAM_CUR, "size": len(data), "data": base64.b64encode(data).decode(),
            "check": check_summary(data)}


def put_settings(link: Link, body: bytes) -> dict:
    try:
        text = body.decode("ascii").strip()
        if text.startswith("{"):
            text = json.loads(text)["data"]
        data = base64.b64decode(text, validate=True)
        force = bool(json.loads(body.decode("ascii").strip()).get("force")) if body.decode("ascii").strip().startswith("{") else False
    except Exception:
        raise BridgeError(400, 'Send JSON {"data": "<base64 of the 4096-byte settings file>"}.')
    summary = check_summary(data)
    if not summary["ok"]:
        raise BridgeError(422, "The settings file failed the envelope check; nothing was sent.", check=summary)

    warnings = []

    def fn(s: BridgeSession):
        # Same rules as komaset merge, against the camera's PARAM.CUR: a differing Koma settings version refuses
        # (unless force); a differing firmware tag is only a warning (the camera imports it with KSIO status 3).
        cur = read_root_file(s, PARAM_CUR)
        if cur is not None:
            if len(cur) == len(data):
                kc, kd = komaset.koma_info(bytearray(cur)), komaset.koma_info(bytearray(data))
                if kc != kd and not force:
                    raise BridgeError(422, "Koma settings version differs from camera", check=summary,
                                      detail=f"Koma versions differ (camera {kc}, file {kd})", code="koma_mismatch")
                tp = komaset.tag_problem(bytearray(cur), bytearray(data))
                if tp:
                    warnings.append(tp[1])
        s.store_settings(data)
        back = s.read_file(PARAM_DAT)
        if back != data:
            raise BridgeError(502, "PARAM.DAT read back different from what was sent.", check=summary)
        return True
    link.run(fn)
    return {"ok": True, "written": PARAM_DAT, "bytes": len(data), "verified": True, "check": summary,
            "warnings": warnings,
            "note": "The camera loads PARAM.DAT at its next start (Koma settings import; not built yet)."}


def reboot(link: Link) -> dict:
    reply = link.run(lambda s: s.power_off())
    with link.lock:
        link.forget_after_poff()
    out = {"ok": True, "reply": reply,
           "note": "poff saves CONFIG and arms a reboot. Whether the camera comes back to a USB session afterwards "
                   "is unknown (U); it may need to be turned on by hand and reconnected."}
    if reply.startswith("451"):
        out["note"] += (" The camera answered 451: in 2.40 that is also the answer when the restart was armed "
                        "(mg1_poff returns 2 after arming it), so watch whether the camera restarts.")
    return out


def dcim_jpegs(s: BridgeSession) -> list:
    """(folder, entry) for every JPEG under /EXT0/DCIM/*/ with 8.3 names. A missing DCIM or folder is just empty."""
    try:
        folders = [e.name for e in s.list_directory(DCIM) if e.is_dir]
    except CameraError:
        return []
    out = []
    for folder in sorted(folders):
        if not _NAME83.match(folder) or folder in (".", ".."):
            continue
        try:
            entries = s.list_directory(f"{DCIM}/{folder}")
        except CameraError:
            continue
        out += [(folder, e) for e in entries
                if not e.is_dir and e.name.upper().endswith(".JPG") and _NAME83.match(e.name)]
    return out


def photo_record(link: Link, s: BridgeSession, folder: str, e) -> dict:
    path = f"{DCIM}/{folder}/{e.name}"
    key = (path, e.size, e.modified)
    if key not in link.photo_meta:
        try:
            _, w, h = s.info(path)
        except CameraError:
            w = h = 0
        try:
            date = s.fdat(path)
        except CameraError:
            date = ""
        link.photo_meta[key] = (date or e.modified, w, h)
    date, w, h = link.photo_meta[key]
    return {"path": f"{folder}/{e.name}", "folder": folder, "name": e.name, "size": e.size,
            "date": date, "width": w, "height": h}


REMOTE_ON_PHOTOS_MESSAGE = "Remote mode is on. Leave Remote before reading photos."


def _photos_blocked(link: Link):
    """409 while tether is active: a card read in shooting state drops live view (the stream runs at ~1 fps afterwards)."""
    if link.remote_on:
        raise BridgeError(409, REMOTE_ON_PHOTOS_MESSAGE, code="remote_on")


def _guarded(link: Link, guard: bool, fn):
    """fn for link.run, refused (409 remote_on) under the link's lock when guard is set. The route's own check runs
    before the lock; remote control can come on while a request waits for it (tether_start queued behind a RETR), and
    only a check under the lock sees that. The selftest reads inside remote control on purpose and passes no guard."""
    if not guard:
        return fn

    def checked(s: BridgeSession):
        _photos_blocked(link)
        return fn(s)
    return checked


def list_photos(link: Link, guard: bool = False) -> dict:
    photos = link.run(_guarded(link, guard, lambda s: [photo_record(link, s, f, e) for f, e in dcim_jpegs(s)]))
    photos.sort(key=lambda p: (p["date"], p["path"]), reverse=True)
    return {"count": len(photos), "photos": photos}


def newest_photo(link: Link, guard: bool = False) -> dict:
    """GET /photos?newest=1 (the Looks try-on's one photo): the same -NLST walk as list_photos, then -INFO and -FDAT for
    the newest JPEG only, picked by the file time in its -NLST record (the time the camera wrote it; list_photos falls
    back to it when -FDAT fails). A full listing costs both per photo (75 ms on HW: a big card outlasts a page's
    timeout). "count" is every JPEG listed; "photos" holds the newest one, or none."""
    def newest(s):
        found = dcim_jpegs(s)
        if not found:
            return 0, []
        folder, e = max(found, key=lambda fe: (fe[1].modified, fe[0], fe[1].name))
        return len(found), [photo_record(link, s, folder, e)]
    count, photos = link.run(_guarded(link, guard, newest))
    return {"count": count, "photos": photos}


def exif_thumbnail(jpeg: bytes) -> bytes | None:
    """The JPEG thumbnail in APP1 Exif IFD1 (JPEGInterchangeFormat 0x0201/0x0202), or None."""
    i = 2
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF:
        marker, length = jpeg[i + 1], int.from_bytes(jpeg[i + 2:i + 4], "big")
        if marker == 0xE1 and jpeg[i + 4:i + 10] == b"Exif\0\0":
            t = jpeg[i + 10:i + 2 + length]
            end = "big" if t[:2] == b"MM" else "little"
            u16 = lambda o: int.from_bytes(t[o:o + 2], end)  # noqa: E731
            u32 = lambda o: int.from_bytes(t[o:o + 4], end)  # noqa: E731
            try:
                ifd0 = u32(4)
                ifd1 = u32(ifd0 + 2 + 12 * u16(ifd0))
                if not ifd1:
                    return None
                tags = {u16(ifd1 + 2 + 12 * k): u32(ifd1 + 2 + 12 * k + 8) for k in range(u16(ifd1))}
                off, n = tags.get(0x0201), tags.get(0x0202)
                if off and n and t[off:off + 2] == b"\xff\xd8":
                    return bytes(t[off:off + n])
            except (IndexError, ValueError):
                return None
            return None
        if marker in (0xDA, 0xD9):
            return None
        i += 2 + length
    return None


def get_photo(link: Link, rel: str, thumb: bool, guard: bool = False) -> bytes:
    parts = rel.split("/")
    if len(parts) != 2 or not all(_NAME83.match(p) for p in parts) or not parts[1].upper().endswith(".JPG"):
        raise BridgeError(400, "Photo paths look like 100RICOH/RIMG0001.JPG.")
    path = f"{DCIM}/{parts[0]}/{parts[1]}"
    data = link.run(_guarded(link, guard, lambda s: link.cached_read(s, path)))
    if thumb:
        return exif_thumbnail(data) or data
    return data


def import_read(link: Link, rel: str, size: int) -> bytes:
    """One photo for the import (photoimport.py), by RETR through the per-connection cache. A cached copy of another
    size (read while the camera was still writing it) is dropped and read again. Guarded like the routes: live view
    can start between the importer's own check and this read."""
    path = f"{DCIM}/{rel}"

    def fn(s: BridgeSession) -> bytes:
        old = link.cache.get(path)
        if old is not None and len(old) != size:
            del link.cache[path]
            link.cache_bytes -= len(old)
        return link.cached_read(s, path)
    return link.run(_guarded(link, True, fn))


_IMPORTER_LOCK = threading.Lock()


def importer(link: Link, **kw) -> "photoimport.Importer":
    """The Photos import of this bridge, made on first use. kw (tests, screenshots) sets state_dir, folder and home; a
    simulated camera gets a temporary state directory unless KOMA_STATE_DIR names one."""
    with _IMPORTER_LOCK:
        imp = getattr(link, "importer", None)
        if imp is None or kw:
            imp = link.importer = photoimport.Importer(
                list_photos=lambda: list_photos(link, guard=True)["photos"],
                read=lambda rel, size: import_read(link, rel, size),
                blocked=lambda: link.remote_on, simulated=link.backend.simulated, **kw)
        return imp


# ------------------------------------------------------------------------------------------------ tether
# Hardware-unproven, all of it. On stock firmware every -CCTL below answers 200 and nothing fires; with the Koma
# `tether` feature loaded the camera should leave the USB state and shoot.
# The bridge cannot tell the two apart from the replies, so a shot counts only when a new JPEG shows up in -NLST or
# when -INFO on the predicted next file name answers 200 (-NLST can lag within one USB session).

NO_FILE_MESSAGE = ("The camera answered but no new photo appeared. It is probably running stock firmware without the "
                   "Koma tether feature, so the shutter did not fire.")


def _cctl(s: BridgeSession, argument: str, log: list) -> bool:
    reply = s.remote(argument)
    log.append({"command": f"-CCTL {argument}", "reply": reply})
    return reply_code(reply) == "200"


KSTA_SNAPSHOT = ("run_state", "app_state", "remtin_flag", "rec_dispatch", "usb_attached")
NOT_ARMED_MESSAGE = ("Remote mode never armed: remtin and diaset,{dial} answered but -LVFR 0 never answered 150 "
                     "(re-armed {n} times in {s:.0f} s). No shutter key was pressed; see the -KSTA snapshots in the replies.")


def ksta_snapshot(s: BridgeSession) -> str:
    """One-line -KSTA summary for the logs (the words that say which state remote mode is in); never raises on a
    camera reply (an older build answers 501)."""
    try:
        st = s.ram_state()
    except (CameraError, ProtocolError, CommandRefused) as error:
        return f"unavailable ({type(error).__name__}: {error})"
    return " ".join(f"{k}={st[k]:#x}" for k in KSTA_SNAPSHOT)


def wait_lvfr(link: Link, s: BridgeSession, log: list) -> bool | None:
    """Poll -LVFR 0 until it answers 150 (the monitor is up, remote mode took), at most link.lvfr_wait_s, before any
    key is pressed. With no 150 link.lvfr_rearm_s after the last arm it logs a -KSTA snapshot and re-arms (remtin +
    diaset,4), up to link.lvfr_rearms times. True on a 150 (link.lvfr_150_s: seconds to it); False on a timeout (the
    caller must not press the shutter: remote mode never armed); None when a reply that is neither 150 nor 451 says
    the camera has no -LVFR to wait on (5xx, e.g. 501: no liveview feature), so arming cannot be checked."""
    t0 = last_arm = link.clock()
    link.lvfr_150_s = None
    link.lvfr_rearms_used = 0
    reply = ""
    while True:
        try:
            reply, payload = s.live_frame(0, 0)
        except (CameraError, ProtocolError, CommandRefused) as error:
            reply, payload = f"{type(error).__name__}: {error}", None
        if payload is not None:
            link.lvfr_150_s = round(link.clock() - t0, 2)
            note = f" ({link.lvfr_rearms_used} re-arm{'s' * (link.lvfr_rearms_used != 1)})" if link.lvfr_rearms_used else ""
            log.append({"command": "-LVFR 0 (wait)", "reply": f"{reply} after {link.lvfr_150_s:.1f} s{note}"})
            return True
        if not reply.startswith("451"):
            log.append({"command": "-LVFR 0 (wait)", "reply": f"no 150 (last: {reply}); not waiting"})
            return None if reply[:1] == "5" else False
        if link.clock() - t0 >= link.lvfr_wait_s:
            break
        if link.clock() - last_arm >= link.lvfr_rearm_s and link.lvfr_rearms_used < link.lvfr_rearms:
            link.lvfr_rearms_used += 1
            log.append({"command": f"-KSTA (re-arm {link.lvfr_rearms_used})",
                        "reply": f"{ksta_snapshot(s)} at {link.clock() - t0:.1f} s"})
            _cctl(s, "remtin", log)  # 451 here: the camera already thinks remote is on; diaset decides
            _cctl(s, f"diaset,{TETHER_DIAL}", log)
            last_arm = link.clock()
            continue
        link.sleep(link.lvfr_poll_s)
    waited = link.clock() - t0
    log.append({"command": "-KSTA (no 150)", "reply": ksta_snapshot(s)})
    log.append({"command": "-LVFR 0 (wait)", "reply": f"no 150 after {waited:.1f} s, {link.lvfr_rearms_used} re-arms "
                f"(last: {reply}); remote mode never armed, no shutter key will be pressed"})
    link.log(f"komabridge: -LVFR 0 never answered 150 after diaset,{TETHER_DIAL} within {waited:.1f} s and "
             f"{link.lvfr_rearms_used} re-arms (last: {reply})")
    return False


def settle_after_ver(link: Link, log: list) -> float:
    """Wait until link.ver_settle_s has passed since this connection's -VER (its 3 s timer message 0xa201 must reach
    the USB runner in state 9, not the record runner after remtin). Returns the seconds waited."""
    if link.ver_at is None or link.ver_settle_s <= 0:
        return 0.0
    wait = link.ver_settle_s - (link.clock() - link.ver_at)
    if wait <= 0:
        return 0.0
    log.append({"command": "(wait after -VER)", "reply": f"{wait:.1f} s before remtin"})
    link.sleep(wait)
    return wait


def _start(link: Link, s: BridgeSession, log: list) -> bool:
    settle_after_ver(link, log)
    _cctl(s, "remtin", log)  # 451 here means remote mode is already on (state != 0); diaset decides
    ok = _cctl(s, f"diaset,{TETHER_DIAL}", log)
    armed = False
    if not ok:
        _cctl(s, "remtout", log)  # leave nothing half-on
    else:
        armed = wait_lvfr(link, s, log) is not False
    # active: the camera accepted remote control; armed: -LVFR 0 answered 150 (or the build has no -LVFR to ask).
    # Not armed stays active so a later shoot/focus (or the selftest's re-entry) can re-arm
    link.tether = {"active": ok, "armed": armed}
    link.last_keyset = link.clock()  # APO renewal counts from the start of remote mode
    return ok


def _release(s: BridgeSession, log: list) -> None:
    """Full-press up, then half-press up; keyset,0,2 (clear all) if either is refused. Tries every step even if one
    raises, then re-raises the first error. Idempotent, so it is safe to run more than once."""
    first = None
    for argument in (f"keyset,{KEY_RELEASE},{KEY_FULL}", f"keyset,{KEY_RELEASE},{KEY_HALF}"):
        try:
            if not _cctl(s, argument, log):
                _cctl(s, f"keyset,{KEY_RELEASE},{KEY_CLEAR}", log)
        except Exception as error:  # noqa: BLE001  (transport trouble: still try the next release)
            first = first or error
    if first:
        raise first


def _press(link: Link, s: BridgeSession, log: list, full: bool) -> bool:
    """Half-press, wait for AF, optionally full-press; keys are released on every path, including exceptions."""
    try:
        if not _cctl(s, f"keyset,{KEY_HOLD},{KEY_HALF}", log):
            return False
        link.sleep(link.af_wait_s)
        if full:
            if not _cctl(s, f"keyset,{KEY_HOLD},{KEY_FULL}", log):
                return False
            link.sleep(link.press_s)
        return True
    finally:
        _release(s, log)


def _ensure_armed(link: Link, s: BridgeSession, log: list) -> bool:
    """Before any shutter key: start remote control if it is off, and if it is on but never armed (no -LVFR 150),
    re-arm and wait again. False: refused or still not armed; the caller must not press a key."""
    if not (link.tether and link.tether["active"]):
        return _start(link, s, log) and link.tether["armed"]
    if link.tether.get("armed", True):
        return True
    _cctl(s, "remtin", log)
    _cctl(s, f"diaset,{TETHER_DIAL}", log)
    link.tether["armed"] = wait_lvfr(link, s, log) is not False
    return link.tether["armed"]


def not_armed_message(link: Link) -> str:
    return NOT_ARMED_MESSAGE.format(dial=TETHER_DIAL, n=getattr(link, "lvfr_rearms_used", 0), s=link.lvfr_wait_s)


def tether_start(link: Link) -> dict:
    # Requests run in lock order, not arrival order: a start sent before a stop (a page closing while live view retried)
    # can win the lock after it and leave remote control on with nobody to turn it off. Such a start does nothing.
    stops = link.tether_stops

    def fn(s):
        if link.tether_stops != stops:
            return None, []
        log = []
        return _start(link, s, log), log
    ok, log = link.run(fn)
    if ok is None:
        return {"ok": False, "active": False, "armed": False, "replies": [], "stopped": True,
                "message": "Remote control was turned off before it started."}
    armed = bool(ok and link.tether and link.tether["armed"])
    message = ("Remote control is on." if armed else not_armed_message(link) if ok else
               "The camera refused remote control (see replies).")
    return {"ok": ok, "active": ok, "armed": armed, "replies": log, "message": message}


def tether_focus(link: Link) -> dict:
    def fn(s):
        log = []
        if not _ensure_armed(link, s, log):
            return False, log
        return _press(link, s, log, full=False), log
    link.hold_live()
    try:
        ok, log = link.run(fn)
    finally:
        link.release_live()
    if ok:
        message = "Focused."
    elif link.tether and link.tether["active"] and not link.tether["armed"]:
        message = not_armed_message(link)
    else:
        message = "The camera refused the half-press (see replies)."
    return {"ok": ok, "replies": log, "message": message}


_SEQ_NAME = re.compile(r"^([A-Za-z]+)(\d+)(\.[A-Za-z0-9]+)$")


def predict_next(names) -> tuple | None:
    """(folder, name) the next shot should get: the highest letters+digits name in the highest folder holding one,
    plus 1, same prefix, digit width and extension (R0015700.JPG -> R0015701.JPG). None if there is none or the number
    would outgrow its width (folder rollover at 9999 is not predicted)."""
    best = None
    for folder, name in names:
        m = _SEQ_NAME.match(name)
        if m and (best is None or (folder, int(m.group(2))) > best[:2]):
            best = (folder, int(m.group(2)), m)
    if best is None:
        return None
    folder, num, m = best
    width = len(m.group(2))
    if len(str(num + 1)) > width:
        return None
    return folder, f"{m.group(1)}{num + 1:0{width}d}{m.group(3)}"


def info_size(s: BridgeSession, folder: str, name: str) -> int | None:
    """Size from -INFO of a card file, or None when it is not there (any non-200 reply, e.g. 450 Open error, or a
    protocol error). Transport errors still propagate: that is a lost camera, not a missing file."""
    try:
        return s.info(f"{DCIM}/{folder}/{name}")[0]
    except (CameraError, ProtocolError, CommandRefused):
        return None


# Shot settle (measured on a camera, -KSTA every 0.3 s): before the press cam_state 2, rec_dispatch 5, monitor_run 1;
# half-press cam_state 6; full press cam_state 9 (capture and write); about 2.5 s after the press cam_state is back to 2
# and monitor_run 1. A -NLST / -INFO on the card while the camera writes corrupted 1 of 3 shots (JPEG header
# at byte 1 MiB). So after the keys: touch no card file until -KSTA says settled, wait SETTLE_DELAY_S more, then list.
SETTLE_KSTA_POLL_S = 0.3      # -KSTA poll interval while waiting
SETTLE_UNSEEN_S = 1.5         # cam_state 6 / 9 never seen: accept cam_state 2 + monitor_run 1 after this long
SETTLE_DELAY_S = 1.5          # extra wait after the camera looks settled, before the first card command
SETTLE_LIST_RETRY_S = 2.0     # listing retry interval when the new file is not there yet


def wait_settled(link: Link, s: BridgeSession, deadline: float) -> float:
    """Poll -KSTA until cam_state == 2 and monitor_run == 1 (after cam_state 6 or 9 was seen, or after
    link.settle_unseen_s if it never was), then wait link.settle_delay_s. Returns seconds since the keys were pressed
    and logs "shot settled after N s". A camera without -KSTA (stock, 501) skips the poll and just waits."""
    t0 = link.clock()
    seen = False
    while link.clock() < deadline:
        try:
            st = s.ram_state()
        except (CameraError, ProtocolError, CommandRefused):
            link.sleep(link.settle_unseen_s)
            break
        if st.get("cam_state") in (6, 9):
            seen = True
        if st.get("cam_state") == 2 and st.get("monitor_run") == 1 and (
                seen or link.clock() - t0 >= link.settle_unseen_s):
            break
        link.sleep(link.ksta_poll_s)
    link.sleep(link.settle_delay_s)
    took = link.clock() - t0
    link.log(f"komabridge: shot settled after {took:.1f} s")
    return took


def tether_shoot(link: Link, fetch: bool) -> dict:
    def fn(s):
        log = []
        if not _ensure_armed(link, s, log):
            if link.tether and link.tether["active"]:
                return {"ok": False, "outcome": "not_armed", "replies": log, "message": not_armed_message(link)}
            return {"ok": False, "outcome": "refused", "replies": log,
                    "message": "The camera refused remote control (see replies)."}
        before = {(f, e.name) for f, e in dcim_jpegs(s)}
        predicted = predict_next(before)
        if not _press(link, s, log, full=True):
            return {"ok": False, "outcome": "refused", "replies": log,
                    "message": "The camera refused the shutter press (see replies). Keys were released."}
        deadline = link.clock() + link.shot_timeout_s
        via_info = False
        wait_settled(link, s, deadline)  # no card command before this returns
        while True:
            new = [(f, e) for f, e in dcim_jpegs(s) if (f, e.name) not in before]
            if not new and predicted:  # -NLST can lag the file within one USB session: ask for the next name directly
                size = info_size(s, predicted[0], predicted[1])
                if size is not None:
                    new, via_info = [(predicted[0], DirEntry(predicted[1], False, size, ""))], True
            if new and max(e.size for _, e in new) <= 0 and link.clock() < deadline:
                new = []  # listed with size 0: not finished after all, ask again later
            if new or link.clock() >= deadline:
                break
            link.sleep(link.list_retry_s)  # SETTLE_LIST_RETRY_S in production, never faster
        if not new:
            _release(s, log)  # watchdog: make sure nothing is left held, then report
            return {"ok": False, "outcome": "no_file", "replies": log, "message": NO_FILE_MESSAGE}
        folder, entry = max(new, key=lambda fe: (fe[0], fe[1].name))
        if entry.size <= 0:
            _release(s, log)
            return {"ok": False, "outcome": "incomplete", "replies": log,
                    "message": "A new photo appeared but the camera did not finish writing it in time."}
        out = {"ok": True, "outcome": "shot", "replies": log, "photo": photo_record(link, s, folder, entry),
               "message": "Photo taken."}
        if fetch:
            out["jpeg"] = base64.b64encode(link.cached_read(s, f"{DCIM}/{folder}/{entry.name}")).decode()
        return out
    link.hold_live()  # the live stream stops asking for frames and resumes when the shoot is done
    try:
        return link.run(fn)
    finally:
        link.release_live()


def tether_stop(link: Link) -> dict:
    link.tether_stops += 1
    link.stop_live()  # live view needs remote control; end the stream first
    link.interval.stop("tether stopped")

    def fn(s):
        log = []
        # release first, always: a crashed request must never leave a half-press (26) or full press (27) held
        _release(s, log)
        _cctl(s, f"keyset,{KEY_RELEASE},{KEY_CLEAR}", log)  # all keys up, virtual dial back to the real one
        _cctl(s, "remtout", log)
        link.tether = None
        return log
    log = link.run(fn)
    return {"ok": True, "active": False, "replies": log, "message": "Remote control is off."}


# ------------------------------------------------------------------------------------------------ interval shooting
# POST /tether/interval {"every_s": N, "count": M}: M shoots, one every N seconds from the start, in a background thread,
# each through tether_shoot (so each pauses the live stream and releases its keys on every path). A shoot that runs
# past the next slot delays that one (no catch-up burst). DELETE cancels between shoots (a shoot in progress finishes);
# /tether/stop cancels too. Untested on hardware like the rest of /tether/*.
INTERVAL_EVERY_S = (1.0, 86400.0)
INTERVAL_COUNT = (1, 9999)


class Interval:
    def __init__(self, link: "Link"):
        self.link = link
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread = None
        self.state = {"running": False, "every_s": None, "count": 0, "done": 0, "shots": 0, "failed": 0,
                      "next_in_s": None, "last": None, "ended": None}

    def snapshot(self) -> dict:
        with self.lock:
            out = dict(self.state)
            nxt = out.pop("_next", None)
            out["next_in_s"] = round(max(0.0, nxt - self.link.clock()), 1) if out["running"] and nxt else None
            return out

    def start(self, every_s: float, count: int) -> dict:
        with self.lock:
            if self.state["running"]:
                raise BridgeError(409, "An interval run is already going; stop it first.", code="interval_running")
            self.cancel.clear()
            self.state = {"running": True, "every_s": every_s, "count": count, "done": 0, "shots": 0, "failed": 0,
                          "last": None, "ended": None, "_next": self.link.clock()}
            self.thread = threading.Thread(target=self._run, args=(every_s, count), daemon=True, name="interval")
            self.thread.start()
        return self.snapshot()

    def stop(self, why: str = "cancelled") -> dict:
        self.cancel.set()
        with self.lock:
            if self.state["running"]:
                self.state["ended"] = why
        return self.snapshot()

    def _run(self, every_s: float, count: int):
        t0 = self.link.clock()
        why = "finished"
        try:
            for i in range(count):
                due = t0 + i * every_s
                while not self.cancel.is_set():
                    left = due - self.link.clock()
                    if left <= 0:
                        break
                    self.cancel.wait(min(left, 0.25))
                if self.cancel.is_set():
                    why = "cancelled"
                    break
                lost = False
                try:
                    r = tether_shoot(self.link, False)
                    last = {"n": i + 1, "outcome": r.get("outcome"), "ok": r.get("ok"),
                            "photo": (r.get("photo") or {}).get("path"), "message": r.get("message")}
                except BridgeError as error:
                    last = {"n": i + 1, "outcome": "error", "ok": False, "message": error.message}
                    lost = error.status == 503
                with self.lock:
                    st = self.state
                    st["done"] = i + 1
                    st["shots"] += bool(last["ok"])
                    st["failed"] += not last["ok"]
                    st["last"] = last
                    st["_next"] = t0 + (i + 1) * every_s if i + 1 < count else None
                if lost:
                    why = "camera lost"
                    break
        finally:
            with self.lock:
                self.state["running"] = False
                self.state["ended"] = self.state["ended"] if why == "cancelled" and self.state["ended"] else why


def interval_start(link: "Link", obj: dict) -> dict:
    try:
        every_s, count = float(obj.get("every_s")), int(obj.get("count"))
    except (TypeError, ValueError):
        raise BridgeError(400, 'Send JSON {"every_s": seconds, "count": photos}.')
    if not (INTERVAL_EVERY_S[0] <= every_s <= INTERVAL_EVERY_S[1]) or not (INTERVAL_COUNT[0] <= count <= INTERVAL_COUNT[1]):
        raise BridgeError(400, f"every_s is {INTERVAL_EVERY_S[0]:g}..{INTERVAL_EVERY_S[1]:g} seconds and count "
                               f"{INTERVAL_COUNT[0]}..{INTERVAL_COUNT[1]}.")
    return {"ok": True, "interval": link.interval.start(every_s, count)}


# ------------------------------------------------------------------------------------------------ live settings (RAM)
# The Looks task: GET /ram reads block bytes from the camera's RAM (-KGET), POST /ram and /ram/batch write single bytes
# (-KSET, KSET_WINDOW or KSET_STOCK). Each value is checked against the window / stock table AND the settings map
# before anything is sent: a byte that belongs to a one-byte enum / u8 / i8 field must be one of its values / inside
# its range, a stock byte one of its stock values; bytes of `bytes` fields (the recipe records, the FX table) and unmapped bytes in the window take any 0..255. A batch is checked whole
# and refused whole (nothing sent) if one write fails the check; on the camera it runs in order (the live stream held
# off) and stops at the first refusal; a busy reply is retried first (Link.kset_busy_wait_s). Nothing here is persistent: the camera saves the block itself at a normal power-off.
# A `451 -KSET busy` (stock byte while the camera's menu / ADJ is open or it is not idle) is HTTP 409 camera_busy.
# Hardware status: mock-tested only.
RAM_BATCH_MAX = 64
_MAP_CACHE: dict = {}


def map_fields() -> list:
    """(offset, size, field dict) for every field of settings-map.json, cached by file mtime."""
    stamp = MAP_PATH.stat().st_mtime
    if _MAP_CACHE.get("stamp") != stamp:
        fields = json.loads(MAP_PATH.read_text())["fields"]
        _MAP_CACHE.update(stamp=stamp, fields=[(int(str(f["offset"]), 0), int(f.get("size", 1)), f) for f in fields])
    return _MAP_CACHE["fields"]


def ram_value_problem(off: int, val: int) -> str | None:
    """None if (off, val) may be written, else why not (window first, then the map)."""
    if not kset_writable(off):
        return f"0x{off:03x} is outside the live-settings window ({_kset_where()})"
    if not 0 <= val <= 255:
        return "values are 0..255"
    if off in KSET_STOCK and val not in KSET_STOCK[off]:
        return f"stock byte 0x{off:03x} takes only {sorted(KSET_STOCK[off])}, not {val}"
    for start, size, f in map_fields():
        if not start <= off < start + size or size != 1:
            continue
        kind, name = f.get("type", "u8"), f["name"]
        if kind == "enum" and f.get("values") and str(val) not in {str(int(str(k), 0)) for k in f["values"]}:
            return f"{name} ({f.get('label', name)}) has no value {val}"
        if kind in ("u8", "i8") and f.get("range"):
            v = val - 256 if kind == "i8" and val > 127 else val
            lo, hi = f["range"]
            if not lo <= v <= hi:
                return f"{name} ({f.get('label', name)}) takes {lo}..{hi}, not {v}"
    return None


def _ram_int(obj, name: str) -> int:
    value = obj.get(name) if isinstance(obj, dict) else None
    if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{1,4}|\d{1,5}", value):
        value = int(value, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BridgeError(400, f'Send JSON with an integer "{name}".')
    return value


def _ram_checked(writes) -> list:
    if not isinstance(writes, list) or not writes:
        raise BridgeError(400, 'Send a JSON list [{"off": N, "val": V}, ...].')
    if len(writes) > RAM_BATCH_MAX:
        raise BridgeError(413, f"At most {RAM_BATCH_MAX} writes per batch.")
    out = []
    for w in writes:
        off, val = _ram_int(w, "off"), _ram_int(w, "val")
        why = ram_value_problem(off, val)
        if why:
            code = "out_of_window" if not kset_writable(off) else "bad_value"
            raise BridgeError(403 if code == "out_of_window" else 422, f"Refused, nothing sent: {why}.", code=code,
                              off=off, val=val)
        out.append((off, val))
    return out


RAM_NOT_SUPPORTED = ("The camera does not know live settings (-KSET / -KGET). It needs Koma firmware with the live "
                     "settings feature.")


CAMERA_BUSY_MESSAGE = "Camera is busy. Close the camera's menu and try again."


def _ram_refusal(reply: str) -> BridgeError:
    if _is_busy(reply):
        return BridgeError(409, CAMERA_BUSY_MESSAGE, code="camera_busy", reply=reply)
    if reply_code(reply) == "451":
        return BridgeError(502, f"The camera refused the value: {reply}", code="camera_refused", reply=reply)
    return BridgeError(502, RAM_NOT_SUPPORTED, code="not_supported", reply=reply)


def ram_read(link: Link, off: int, length: int) -> dict:
    if not (0 <= off < KGET_MAX and 1 <= length <= KGET_MAX and off + length <= KGET_MAX):
        raise BridgeError(400, "off is 0..4095 and len 1..4096, inside the 4096-byte block.")

    def fn(s: BridgeSession):
        try:
            return s.ram_read(off, length)
        except CameraError as error:
            raise _ram_refusal(str(error))
    link.hold_live()
    try:
        data = link.run(fn)
    finally:
        link.release_live()
    return {"off": off, "len": length, "data": base64.b64encode(data).decode(), "hex": data.hex()}


def ram_state(link: Link) -> dict:
    """-KSTA under the camera lock: the decoded state snapshot (diagnostic; see KSTA_WORDS)."""
    link.hold_live()
    try:
        return link.run(lambda s: s.ram_state())
    finally:
        link.release_live()


def peek(link: Link, addr: int, length: int) -> bytes:
    """-PEEK under the camera lock: length (1..256) raw RAM bytes from addr (diagnostic, read-only)."""
    link.hold_live()
    try:
        return link.run(lambda s: s.peek(addr, length))
    finally:
        link.release_live()


def ram_state_json(link: Link) -> dict:
    """GET /ram/state: -KSTA decoded, for host-side health checks (LCD, APO, battery words). Read-only."""
    try:
        return ram_state(link)
    except CameraError as error:
        raise _ram_refusal(str(error))


def peek_json(link: Link, addr: int, length: int) -> dict:
    """GET /ram/peek: -PEEK inside [PEEK_LO, PEEK_END), 1..PEEK_MAX bytes. Read-only."""
    if not (1 <= length <= PEEK_MAX and PEEK_LO <= addr and addr + length <= PEEK_END):
        raise BridgeError(400, f"-PEEK reads 1..{PEEK_MAX} bytes inside 0x{PEEK_LO:x}..0x{PEEK_END:x}.")
    try:
        data = peek(link, addr, length)
    except CameraError as error:
        raise _ram_refusal(str(error))
    return {"addr": addr, "len": length, "hex": data.hex()}


def _is_busy(reply: str) -> bool:
    return reply_code(reply) == "451" and reply.split()[-1:] == ["busy"]


def ram_write(link: Link, writes: list, busy_wait_s: float | None = None) -> dict:
    """writes: [(off, val)], already checked. Sent in order; stops at the first non-200 reply. A `451 -KSET busy` is
    retried every link.kset_busy_poll_s for up to busy_wait_s (default link.kset_busy_wait_s) per write, the lock
    released between tries; the retry count and the time spent busy are in the result (and the error) and logged."""
    wait = link.kset_busy_wait_s if busy_wait_s is None else busy_wait_s
    results, bad, retries, busy_s = [], None, 0, 0.0
    link.hold_live()  # in ahead of the live stream's next pull, like a key press
    try:
        for off, val in writes:
            t0, tries = link.clock(), 0
            while True:
                reply = link.run(lambda s, off=off, val=val: s.ram_write(off, val))
                if not _is_busy(reply) or link.clock() - t0 >= wait:
                    break
                tries += 1
                link.release_live()  # live view runs on while the camera is busy (EV overlay, up to ~9 s on hardware)
                try:
                    link.sleep(link.kset_busy_poll_s)
                finally:
                    link.hold_live()
            retries += tries
            if tries:
                busy_s += link.clock() - t0
            results.append({"off": off, "val": val, "reply": reply})
            if reply_code(reply) != "200":
                bad = reply
                break
    finally:
        link.release_live()
    busy = {"busy_retries": retries, "busy_s": round(busy_s, 3)}
    if retries:
        link.log(f"komabridge: -KSET busy, {retries} retries over {busy_s:.2f} s"
                 + (f", gave up: {bad}" if bad is not None else ", then 200"))
    if bad is not None:  # 502 with how far it got: the writes before the refused one are in the camera's RAM
        err = _ram_refusal(bad)
        err.extra.update(done=len(results) - 1, results=results, **busy)
        raise err
    return {"ok": True, "done": len(results), "results": results, **busy,
            "message": "Written to the camera's memory. It keeps it when it is switched off normally."}


# ------------------------------------------------------------------------------------------------ remote keys
# The Remote task (a camera with a broken LCD): press the camera's keys over -CCTL keyset, set the virtual dial with
# diaset. Refused unless remote control is on (POST /tether/start ran on this connection), so every press happens
# inside the remtin ... keyset,0,2 + remtout bracket that /tether/stop closes. Tap (action 3) is one poll, press and
# release, so a tap sends no release. Holds stay held until a release, or until
# /tether/stop clears every injected key. Hardware status: never sent beyond keyset,3,22 / 1,26 / 1,27; what the camera's UI does with an injected key is unobserved (its LCD is broken).

REMOTE_OFF_MESSAGE = "Remote control is off. Start it first (POST /tether/start)."


def key_listing(link: Link) -> dict:
    """No camera traffic: the allowlisted codes with names, the refused ones with the reason."""
    return {
        "remote": link.remote_on,
        "actions": {str(a): name for a, name in KEY_ACTIONS.items()},
        "keys": [{"code": c, "name": KEY_NAMES[c], "panel": PANEL_KEYS.get(c), "inert": c in KEY_INERT}
                 for c in KEYSET_CODES],
        "refused": [{"code": c, "name": KEY_NAMES[c], "why": why} for c, why in sorted(KEY_REFUSED.items())],
        "dial": {"positions": list(range(9)), "tether_default": TETHER_DIAL},
    }


def _int_field(obj: dict, name: str) -> int:
    value = obj.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BridgeError(400, f'Send JSON with an integer "{name}".')
    return value


def press_key(link: Link, code: int, action: int) -> dict:
    if code in KEY_REFUSED:
        raise BridgeError(403, f"Key {code} is refused: {KEY_REFUSED[code]}", code="key_refused")
    if not 0 <= code < len(KEY_NAMES):
        raise BridgeError(400, f"Key codes are 0..{len(KEY_NAMES) - 1}.")
    if action not in KEY_ACTIONS:
        raise BridgeError(400, "action is 0 (release), 1 (hold) or 3 (tap).")
    if not link.remote_on:
        raise BridgeError(409, REMOTE_OFF_MESSAGE, code="remote_off")
    argument = f"keyset,{action},{code}"

    def fn(s):
        log = []
        return _cctl(s, argument, log), log
    link.hold_live()  # let the key in ahead of the streams' next frame
    try:
        ok, log = link.run(fn)
    finally:
        link.release_live()
    return {"ok": ok, "code": code, "name": KEY_NAMES[code], "action": KEY_ACTIONS[action], "replies": log,
            "message": "Pressed." if ok else "The camera refused the key (see replies)."}


def set_dial(link: Link, n: int) -> dict:
    if not 0 <= n <= 8:
        raise BridgeError(400, "The virtual dial takes 0..8.")
    if not link.remote_on:
        raise BridgeError(409, REMOTE_OFF_MESSAGE, code="remote_off")

    def fn(s):
        log = []
        ok = _cctl(s, f"diaset,{n}", log)
        if ok:
            link.tether["dial"] = n
        return ok, log
    link.hold_live()
    try:
        ok, log = link.run(fn)
    finally:
        link.release_live()
    return {"ok": ok, "n": n, "replies": log,
            "message": "Dial set." if ok else "The camera refused the dial (see replies)."}


# ------------------------------------------------------------------------------------------------ live view
# -LVFR payload: 16 bytes big-endian, then the frame bytes.
#   0 magic 'LVFR', 4 mode u8, 5 flags u8 (bit0 truncated, bit1 VD sync timed out, bit2 OSD page B sent, bit3 torn frame
#   (modes 0/1: the display slot moved on during the read), bit4 OSD base register unrecognised (page A sent)), 6 seq u16,
#   8 width u16, 10 height u16,
#   12 length u16 (bytes after the header), 14 scale u16.
# Mode 1: the JPEG scan + EOI only; the host prepends the 193-byte header the movie recorder writes per AVI frame
# (0x8986c): SOI, APP0 "AVI1", DRI 0, DQT (0x114260 scaled by 0x11422c), SOF0 4:2:2, SOS; no DHT (Annex K implied).
# Mode 0: raw Y, width x height bytes (160x120 = 19,200).
LVFR_HEADER = struct.Struct(">4sBBHHHHH")
LVFR_MAGIC = b"LVFR"
LVFR_TRUNCATED = 0x01
LVFR_SYNC_TIMEOUT, LVFR_PAGE_B, LVFR_TORN, LVFR_OSD_BASE_UNKNOWN = 0x02, 0x04, 0x08, 0x10
LVFR_REENCODED = 0x20  # mode 3: the first encode failed or overran one chunk; the camera re-encoded at 2x the scale
# Mode 3 (liveview 1.3): the display slot of the live-view ring (640x244 at stride 672, tables 0x193838/0x193876/
# 0x1935c4), JPEG-encoded straight from the ring as 640x240 4:2:2, scan + EOI as mode 1. The ring holds a 4:3 picture
# in 244 lines, so one pixel is about twice as tall as wide (HW: mode 1 squeezes the same slot to 320x240 and the
# picture has normal proportions). Display mode 3 at 4:3: 640x480, i.e. every line doubled.
LVFR_DISPLAY_SIZE = {3: (640, 480)}
M3_SCALE_DEFAULT, M3_SCALE_MIN, M3_SCALE_MAX = 448, 192, 4095
M3_TARGET = (24_000, 48_000)  # bytes per scan: under 24 KB step finer, over 48 KB step coarser (chunk is 65,520)


def lvfr_arg(mode: int, scale: int | None = None) -> int:
    """The -LVFR `a` argument: 0/1/2 as they are; mode 3 is 3 | scale << 4 (scale 0 or None: the camera's scale3)."""
    if mode != 3:
        if scale is not None:
            raise ValueError("a scale is only sent with mode 3")
        return mode
    scale = scale or 0
    if not 0 <= scale <= 0xFFFF:
        raise ValueError(f"mode 3 scale {scale} is outside 0..65535")
    return 3 | scale << 4


def next_m3_scale(used: int, length: int, reencoded: bool = False) -> int:
    """Host rate control for mode 3: from the scale the camera reports and the scan length, the scale to ask for next.
    The encoder's DQT is q = clamp(((scale*base >> 9)+1)>>1, 1, 255) (0x11422c), so bytes fall as the scale rises.
    Steps of 1/8 keep it smooth; a re-encode (flag 0x20) means the asked scale overran, so the doubled one is kept."""
    lo, hi = M3_TARGET
    s = used
    if not reencoded:
        if length > hi:
            s = used + max(16, used // 4)
        elif length < lo:
            s = used - max(8, used // 8)
    return max(M3_SCALE_MIN, min(M3_SCALE_MAX, s))

# Base quantisation tables at 0x18c3b0 (luma) and 0x18c3f0 (chroma) in grd240-armbe.bin, stored in zigzag order (they
# are ITU T.81 Tables K.1 and K.2). scale 1024 reproduces them; the camera default is 448; scale 0 is never sent in mode 1.
QT_LUMA = bytes.fromhex("100b0c0e0c0a100e0d0e1211101318281a181616183123251d283a333d3c3933"
                        "383740485c4e404457453738506d51575f626768673e4d71797064785c656763")
QT_CHROMA = bytes.fromhex("1112121815182f1a1a2f634238426363") + bytes([0x63] * 48)
SOF0_TEMPLATE = bytes.fromhex("ffc00011080000000003012100021101031101")  # 0x1789dc
SOS_SEGMENT = bytes.fromhex("ffda000c03010002110311003f00")  # 0x1789ef
assert len(QT_LUMA) == len(QT_CHROMA) == 64 and len(SOF0_TEMPLATE) == 19


def scale_table(base: bytes, scale: int) -> bytes:
    """0x11422c: q = clamp(((scale * base >> 9) + 1) >> 1, 1, 255); 1024 = the base table, the camera default is 448.
    The camera never sends scale 0 in mode 1; if it did, the base table is used as it is."""
    if not scale:
        return bytes(base)
    return bytes(max(1, min(255, ((scale * b >> 9) + 1) >> 1)) for b in base)


def firmware_jpeg_header(scale: int, width: int = 320, height: int = 240) -> bytes:
    """The exact 193 bytes 0x8986c writes in front of each movie frame, for this scale and size."""
    sof = bytearray(SOF0_TEMPLATE)
    struct.pack_into(">HH", sof, 5, height, width)
    return (b"\xff\xd8"
            + b"\xff\xe0\x00\x10AVI1" + bytes(10)
            + b"\xff\xdd\x00\x04\x00\x00"
            + b"\xff\xdb\x00\x84\x00" + scale_table(QT_LUMA, scale) + b"\x01" + scale_table(QT_CHROMA, scale)
            + bytes(sof) + SOS_SEGMENT)


# ITU T.81 Annex K.3 tables (bits, values), the ones the hardware encoder uses. Inserted before SOS for the browser:
# decoders that follow the AVI1 convention fill them in themselves, others (ImageIO) refuse a JPEG without DHT.
_K_DC_BITS = (bytes([0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]), bytes([0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]))
_K_DC_VALS = bytes(range(12))
_K_AC_LUMA = (bytes([0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d]), bytes.fromhex(  # firmware copy at 0x17885a
    "01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a43"
    "4445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2"
    "b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa"))
_K_AC_CHROMA = (bytes([0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77]), bytes.fromhex(  # firmware copy at 0x17892a
    "000102031104052131061241510761711322328108144291a1b1c109233352f0156272d10a162434e125f11718191a262728292a35363738393a"
    "434445464748494a535455565758595a636465666768696a737475767778797a82838485868788898a92939495969798999aa2a3a4a5a6a7a8a9"
    "aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9fa"))
assert len(_K_AC_LUMA[1]) == sum(_K_AC_LUMA[0]) == 162 and len(_K_AC_CHROMA[1]) == sum(_K_AC_CHROMA[0]) == 162


def _dht() -> bytes:
    body = (b"\x00" + _K_DC_BITS[0] + _K_DC_VALS + b"\x10" + _K_AC_LUMA[0] + _K_AC_LUMA[1]
            + b"\x01" + _K_DC_BITS[1] + _K_DC_VALS + b"\x11" + _K_AC_CHROMA[0] + _K_AC_CHROMA[1])
    return b"\xff\xc4" + struct.pack(">H", len(body) + 2) + body


DHT_SEGMENT = _dht()
_HEADER_CACHE: dict = {}


def display_jpeg_header(scale: int, width: int, height: int) -> bytes:
    """firmware_jpeg_header with the Annex K DHT inserted before SOS. Cached per (scale, width, height)."""
    key = (scale, width, height)
    if key not in _HEADER_CACHE:
        fw = firmware_jpeg_header(scale, width, height)
        _HEADER_CACHE[key] = fw[:-len(SOS_SEGMENT)] + DHT_SEGMENT + SOS_SEGMENT
    return _HEADER_CACHE[key]


def parse_lvfr(payload: bytes) -> dict:
    if len(payload) < LVFR_HEADER.size:
        raise ProtocolError(f"-LVFR payload of {len(payload)} bytes is shorter than its header")
    magic, mode, flags, seq, width, height, length, scale = LVFR_HEADER.unpack_from(payload)
    if magic != LVFR_MAGIC:
        raise ProtocolError(f"-LVFR payload magic {magic!r}, expected b'LVFR'")
    data = payload[LVFR_HEADER.size:]
    if len(data) != length:
        raise ProtocolError(f"-LVFR header says {length} bytes, {len(data)} arrived")
    return {"mode": mode, "flags": flags, "truncated": bool(flags & LVFR_TRUNCATED),
            "sync_timeout": bool(flags & LVFR_SYNC_TIMEOUT), "page_b": bool(flags & LVFR_PAGE_B),
            "torn": bool(flags & LVFR_TORN), "osd_base_unknown": bool(flags & LVFR_OSD_BASE_UNKNOWN),
            "reencoded": bool(flags & LVFR_REENCODED),
            "seq": seq, "width": width, "height": height, "scale": scale, "data": data}


def grey_png(width: int, height: int, y: bytes) -> bytes:
    """8-bit greyscale PNG (stdlib zlib), for mode 0. Browsers draw it in an <img> like the JPEG parts."""
    if len(y) != width * height:
        raise ProtocolError(f"mode 0 frame of {len(y)} bytes is not {width}x{height}")
    raw = b"".join(b"\x00" + y[r * width:(r + 1) * width] for r in range(height))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


# ---- mode 2: the OSD page (-LVFR 2,<seq> = OSD page pull)
# Pixels: the 4 bpp OSD page rows, high nibble = left pixel, ceil(width / 2) bytes per row, width x height from the
# header (authoritative; the firmware page is 320x240 = 38,400 bytes, the emulator's is 360x240 at stride 0xb4).
# Palette: header scale 0 = none sent, use OSD_FIXED_PALETTE; scale N > 0 = N entries of 4 bytes appended after the
# pixels, each the raw VEN palette register for index 0..N-1 (u32 big-endian like the header, Y<<16 | Cb<<8 | Cr,
# 0x161d30). Index 0 has no palette register (0x161f18 rejects it) and is transparent over the video plane: the
# inference tools/grdui/display.py render_page makes, reused here. Entries past N render magenta, as display.py does.
OSD_TRANSPARENT = 0
OSD_MAX_ENTRIES = 64  # the VEN has palette registers 0x12020100 + i*4 for i < 64 (0x161f18)
# Raw VEN palette 0..15 read from the emulator (tools/grdui Session.ven.palette() after a MENU/OK tap; the dump is in
# testdata/osd-pages.json). Emulator RAM state, not a camera reading. The firmware rewrites some entries per screen: on
# the shooting screen indexes 2, 7, 8, 9, 12 and 13 differ, so a fixed palette is approximate; prefer an appended one.
OSD_FIXED_PALETTE_RAW = (None, 0xda0000, 0xcc0000, 0x2000000, 0x320000, 0x200000, 0x0, 0x3320000, 0x2000000, 0x8c0001,
                         0x98bd29, 0xd4e604, 0xda0000, 0x320000, 0x7fb7a3, 0x4be74a)
OSD_UNSET = (255, 0, 255)
# The firmware's ROM default OSD palette, the table at 0x1fba30 in the 2.40 image (words read from the KOMA_FIRMWARE
# payload, raw VEN register format Y<<16 | Cb<<8 | Cr). Used when a mode 2 frame's appended palette arrives with entries
# 1..15 all zero (the camera reads the 16 VEN registers back, and readback may not work on hardware). Index mapping is
# an inference (I): word i is palette index i + 1 (15 entries), because word 1 equals the emulator's register 2 exactly
# and word 13 (0x80b5a2) is within rounding of register 14 (0x7fb7a3); the firmware rewrites some entries per screen.
OSD_ROM_PALETTE_RAW = (None, 0xdb0000, 0xb10000, 0x840400, 0x520401, 0x3e0301, 0x4d2fc8, 0x6f1baf, 0x543ae2, 0x6b1efc,
                       0x4b2cf9, 0x4dd3c7, 0x4ed246, 0x60e159, 0x80b5a2, 0x82b440)
PALETTE_ROM_NOTE = "palette: ROM default"


def ycbcr_to_rgb(y, cb, cr):
    """Copied from tools/grdui/display.py ycbcr_to_rgb (0x161d30 constants, signed Cb/Cr, Y scale 219/255). Panel
    response unknown, so colors are approximate. Bits 24-25 of a register are ignored, as there."""
    cb = cb - 256 if cb > 127 else cb
    cr = cr - 256 if cr > 127 else cr
    y = y / 0.859
    r = y + cr / 0.6263
    g = y - 0.1942 * cb / 0.4957 - 0.5094 * cr / 0.6263
    b = y + cb / 0.4957
    return tuple(max(0, min(255, int(round(v)))) for v in (r, g, b))


def osd_palette(raw) -> list:
    """16 RGB triples for a raw register list (None or missing = unset, magenta; index 0 is transparent anyway)."""
    out = []
    for i in range(16):
        value = raw[i] if i < len(raw) else None
        out.append((0, 0, 0) if i == OSD_TRANSPARENT else OSD_UNSET if value is None
                   else ycbcr_to_rgb((value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff))
    return out


def split_osd(frame: dict) -> tuple[bytes, list]:
    """(pixel rows, raw palette) of a mode 2 frame, checking the sizes against the header."""
    w, h, n, data = frame["width"], frame["height"], frame["scale"], frame["data"]
    stride = (w + 1) // 2
    if not (0 < w <= 1024 and 0 < h <= 1024) or n > OSD_MAX_ENTRIES:
        raise ProtocolError(f"mode 2 header {w}x{h}, {n} palette entries is out of range")
    if len(data) != stride * h + 4 * n:
        raise ProtocolError(f"mode 2 frame of {len(data)} bytes is not {w}x{h} at 4 bpp + {n} palette entries")
    pixels = data[:stride * h]
    raw = list(OSD_FIXED_PALETTE_RAW) if n == 0 else [
        struct.unpack_from(">I", data, stride * h + 4 * i)[0] for i in range(n)]
    if n >= 16 and not any(raw[1:16]):
        raw = list(OSD_ROM_PALETTE_RAW) + raw[16:]   # register readback gave nothing: the firmware's ROM table
        frame["palette_note"] = PALETTE_ROM_NOTE
    return pixels, raw


def _png(width: int, height: int, depth: int, ctype: int, raw: bytes, extra: bytes = b"") -> bytes:
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, ctype, 0, 0, 0))
            + extra + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


def osd_png(frame: dict) -> bytes:
    """A 4-bit indexed PNG of a mode 2 frame. PNG packs 4-bit pixels high nibble first, the OSD's own order, so each
    page row goes in unchanged behind a filter byte. PLTE holds the 16 colors, tRNS makes index 0 fully transparent
    (every other index opaque), so the browser composites it over the live picture as the panel does."""
    w, h = frame["width"], frame["height"]
    stride = (w + 1) // 2
    pixels, raw = split_osd(frame)
    plte = b"".join(bytes(c) for c in osd_palette(raw))
    trns = bytes(0 if i == OSD_TRANSPARENT else 255 for i in range(OSD_TRANSPARENT + 1))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff)
    rows = b"".join(b"\x00" + pixels[r * stride:(r + 1) * stride] for r in range(h))
    return _png(w, h, 4, 3, rows, chunk(b"PLTE", plte) + chunk(b"tRNS", trns))


def frame_image(frame: dict) -> tuple[bytes, str]:
    """(image bytes, content type) for one parsed -LVFR frame."""
    if frame["mode"] in (1, 3):  # mode 3: SOF0 640x240 as sent; viewers stretch to LVFR_DISPLAY_SIZE[3] (4:3)
        return display_jpeg_header(frame["scale"], frame["width"], frame["height"]) + frame["data"], "image/jpeg"
    if frame["mode"] == 0:
        return grey_png(frame["width"], frame["height"], frame["data"]), "image/png"
    if frame["mode"] == 2:
        return osd_png(frame), "image/png"
    raise ProtocolError(f"-LVFR mode {frame['mode']} is unknown")


class _StreamStop(Exception):
    """Internal: the running stream was superseded or stopped."""


class LiveOff(Exception):
    """The camera answered -LVFR with something other than 150 (451: live view / monitor not running)."""

    def __init__(self, reply: str):
        super().__init__(reply)
        self.reply = reply


class LiveStats:
    """Counters for the running stream, read by /status and /live/status without the camera lock."""

    WINDOW_S = 2.0

    def __init__(self, clock):
        self.clock = clock
        self.lock = threading.Lock()
        self.reset(None)

    def reset(self, mode):
        with self.lock:
            self.running, self.mode, self.paused = mode is not None, mode, False
            self.busy = False  # paused by a 451 retry (not by a shoot hold)
            self.frames = self.dropped = self.bytes = 0
            self.torn = self.sync_timeouts = 0
            self.note = None  # e.g. "palette: ROM default" (mode 2)
            self.recent: collections.deque = collections.deque()
            self.ended = None
            self.client = None  # the page's ?cid= of the running stream (two tabs: which one owns the picture)

    def add(self, size: int):
        with self.lock:
            now = self.clock()
            self.frames += 1
            self.bytes += size
            self.recent.append((now, size))
            while self.recent and now - self.recent[0][0] > self.WINDOW_S:
                self.recent.popleft()

    def flags(self, frame: dict):
        """Count the header flags of one frame: torn (bit 3), VD sync timed out (bit 1); keep the palette note."""
        with self.lock:
            self.torn += bool(frame.get("torn"))
            self.sync_timeouts += bool(frame.get("sync_timeout"))
            if frame.get("mode") == 2:
                self.note = frame.get("palette_note")

    def set_busy(self, on: bool):
        """The camera answered 451 mid-stream and the bridge is retrying: reported as paused, reason "camera busy"."""
        with self.lock:
            self.busy = on

    def end(self, why: str):
        with self.lock:
            self.running, self.paused, self.busy, self.ended = False, False, False, why

    def snapshot(self) -> dict:
        with self.lock:
            fps = bps = 0.0
            if self.running and len(self.recent) >= 2:
                span = self.recent[-1][0] - self.recent[0][0]
                if span > 0:
                    fps = (len(self.recent) - 1) / span
                    bps = sum(n for _, n in list(self.recent)[1:]) / span
            paused = self.running and (self.paused or self.busy)
            reason = "camera busy" if self.running and self.busy else ("shoot" if paused else None)
            return {"running": self.running, "mode": self.mode, "paused": paused, "reason": reason, "fps": round(fps, 1),
                    "bytes_per_s": int(bps), "frames": self.frames, "dropped": self.dropped, "torn": self.torn,
                    "sync_timeouts": self.sync_timeouts, "note": self.note, "ended": self.ended, "client": self.client}


def live_pull(link: "Link", mode: int) -> dict:
    """One -LVFR under the camera lock (taken and released here). Raises LiveOff on a non-150 reply."""
    def fn(s: BridgeSession):
        seq = link.live_seq
        link.live_seq = (link.live_seq + 1) & 0xFFFF
        if mode == 3:  # host rate control: the scale to ask for comes from the last mode 3 frame
            return s.live_frame(mode, seq, getattr(link, "m3_scale", M3_SCALE_DEFAULT))
        return s.live_frame(mode, seq)
    reply, payload = link.run(fn)
    if payload is None:
        raise LiveOff(reply)
    frame = parse_lvfr(payload)
    if frame["mode"] != mode:
        raise ProtocolError(f"asked -LVFR mode {mode}, got mode {frame['mode']}")
    if mode == 3:
        link.m3_scale = next_m3_scale(frame["scale"], len(frame["data"]), frame["reencoded"])
    return frame


def _parse_mode(query) -> int:
    value = query.get("mode", ["1"])[0]
    if value not in ("0", "1", "2", "3"):
        raise BridgeError(400, "mode is 1 (JPEG), 0 (grey), 2 (the camera's menus and icons) or 3 (640x240 JPEG, "
                               "show at 4:3).")
    return int(value)


LIVE_OFF_MESSAGE = ("The camera is not sending live view. It needs Koma firmware with the liveview and tether features, "
                    "and remote control on (POST /tether/start).")
OSD_OFF_MESSAGE = ("The camera is not sending its screen (-LVFR mode 2). It needs Koma firmware with the liveview "
                   "feature's OSD pull.")


def live_off_message(mode: int) -> str:
    return OSD_OFF_MESSAGE if mode == 2 else LIVE_OFF_MESSAGE


# ------------------------------------------------------------------------------------------------ HTTP

_ORIGIN = re.compile(r"^(null|file://.*|https?://(localhost|127\.0\.0\.1)(:\d+)?)$")
PAGE_PATH = KOMASET_DIR / "web" / "koma-setup.html"  # served at / so a phone needs no file:// (phone mode, --lan)
TOKEN_HEADER = "X-Koma-Token"
REPO_ROOT = KOMASET_DIR.parent.parent
# Read-only static files, exact paths only (no directory serving, so no traversal): route -> (file, content type).
# The page shell holds no camera access, so --lan serves it without the token.
STATIC_ROUTES = {
    "/": (PAGE_PATH, "text/html; charset=utf-8"),  # the Koma app (tools/komaapp/dist) instead, once it is built
    "/legacy": (PAGE_PATH, "text/html; charset=utf-8"),  # the pre-1.0 page: a checkout only (not in the koma-gr wheel)
    "/manifest.webmanifest": (KOMASET_DIR / "web" / "manifest.webmanifest", "application/manifest+json"),
    "/sw.js": (KOMASET_DIR / "web" / "sw.js", "application/javascript"),
}

# The Koma app: Vite + Svelte compiled to static files in tools/komaapp/dist, served at /
# with its assets under /assets/. Without a build, / stays the old page. Files are looked up by name only: one path
# segment at the top or under assets/, plain characters, no dot files, resolved inside dist (no traversal).
APP_DIST = TOOLS / "komaapp" / "dist"
_APP_NAME = re.compile(r"^/(assets/)?[A-Za-z0-9][A-Za-z0-9._-]*$")
_APP_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css",
              ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml", ".png": "image/png",
              ".json": "application/json", ".webmanifest": "application/manifest+json", ".ico": "image/x-icon",
              ".txt": "text/plain; charset=utf-8", ".jpg": "image/jpeg"}  # .jpg: the Looks try-on's sample photo
# First path segments that belong to the bridge (endpoints and its own static files). No app file is ever served in
# their place, so a file added to the app's public/ can neither shadow an endpoint nor skip the --lan token check.
_API_ROOTS = {"status", "settings", "settings-map.json", "reboot", "photos", "tether", "live", "ram", "keys", "dial",
              "api", "analysis", "docs", "legacy", "manifest.webmanifest", "sw.js", "install", "komalog", "webcam",
              "phone"}


def app_index() -> Path | None:
    index = APP_DIST / "index.html"
    return index if index.is_file() else None


def app_file(route: str) -> tuple[Path, str] | None:
    """(file, content type) of a built app file for an exact request path, or None."""
    if route in ("/", "/index.html"):
        index = app_index()
        return (index, _APP_TYPES[".html"]) if index else None
    if not _APP_NAME.match(route) or ".." in route or route.strip("/").split("/", 1)[0] in _API_ROOTS:
        return None
    path = (APP_DIST / route.lstrip("/")).resolve()
    try:
        path.relative_to(APP_DIST.resolve())
    except ValueError:
        return None
    if not path.is_file() or path.suffix.lower() not in _APP_TYPES or path.name == "index.html" and route != "/index.html":
        return None
    return path, _APP_TYPES[path.suffix.lower()]


# No fallback to the app's index for other addresses: the app routes by the hash (#/settings/camera), so the only
# page address is /.


def make_handler(link: Link, port: int, token: str | None = None):
    """token None: loopback mode (loopback clients, Host and Origin guards). token set (--lan): any client and any
    Origin, but every request must carry the token as ?token= or an X-Koma-Token header (CORS preflights excepted:
    they cannot carry it and get no data)."""
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"komabridge/{BRIDGE_VERSION}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if not getattr(self.server, "quiet", False):
                sys.stderr.write("komabridge: " + (fmt % args) + "\n")

        def _token_ok(self) -> bool:
            given = self.headers.get(TOKEN_HEADER) or parse_qs(urlsplit(self.path).query).get("token", [""])[0]
            return bool(token) and hmac.compare_digest(given.encode(), token.encode())

        def _cors(self):
            origin = self.headers.get("Origin")
            if origin and token and (self.command == "OPTIONS" or self._token_ok()):
                self.send_header("Access-Control-Allow-Origin", origin)  # phone mode: whoever holds the token
                self.send_header("Vary", "Origin")
            elif origin and not token and _ORIGIN.match(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            if self.headers.get("Access-Control-Request-Private-Network"):
                self.send_header("Access-Control-Allow-Private-Network", "true")

        def _send(self, status: int, body: bytes, ctype: str, extra=()):
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _live_stream(self, mode: int, head_only: bool):
            """multipart/x-mixed-replace, one part per -LVFR frame. The first frame is pulled before any header goes
            out, so a camera without live view gets a plain JSON 409 (an <img> sees an error). Mode 2 (OSD) is its own
            channel: it supersedes only an older mode 2 stream, and runs at link.osd_max_fps."""
            channel = "osd" if mode == 2 else "video"
            stats = link.osd if mode == 2 else link.live
            current = (lambda: link.osd_gen) if mode == 2 else (lambda: link.live_gen)
            link.stop_live(channel)
            gen = current()
            try:
                first = live_pull(link, mode)
            except LiveOff as off:
                raise BridgeError(409, live_off_message(mode), code="live_off", reply=off.reply)
            boundary = "komalive"
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if head_only:
                return
            stats.reset(mode)
            stats.client = self._cid
            setattr(link, channel == "osd" and "osd_owner" or "live_owner", gen)
            max_fps = link.osd_max_fps if mode == 2 else link.live_max_fps
            why, frame, period = "client", first, 1.0 / max_fps
            try:
                while True:
                    started = time.monotonic()
                    stats.flags(frame)
                    if frame["truncated"]:
                        with stats.lock:
                            stats.dropped += 1
                    else:
                        image, ctype = frame_image(frame)
                        self.wfile.write(f"--{boundary}\r\nContent-Type: {ctype}\r\nContent-Length: {len(image)}\r\n"
                                         f"X-Frame-Seq: {frame['seq']}\r\n\r\n".encode("ascii") + image + b"\r\n")
                        self.wfile.flush()
                        stats.add(len(frame["data"]) + LVFR_HEADER.size)
                    frame = None
                    while frame is None:
                        if current() != gen:
                            why = "stopped"
                            raise _StreamStop
                        if link.live_hold or link.ka_wait:
                            time.sleep(link.live_pause_poll_s)
                            continue
                        wait = period - (time.monotonic() - started)
                        if wait > 0:
                            time.sleep(wait)
                        if link.live_hold or link.ka_wait or current() != gen:
                            continue
                        frame = self._pull_retrying(mode, gen, stats, current)
            except _StreamStop:
                pass
            except LiveOff as off:
                why = "camera: " + off.reply
            except BridgeError as error:
                why = "camera: " + error.message
            except (ProtocolError, CommandRefused) as error:
                why = f"{type(error).__name__}: {error}"
            except OSError:
                why = "client"  # the page went away (img src cleared, tab closed)
            if why != "client":
                try:
                    self.wfile.write(f"--{boundary}--\r\n".encode("ascii"))
                    self.wfile.flush()
                except OSError:
                    pass
            if getattr(link, channel == "osd" and "osd_owner" or "live_owner") == gen:  # not replaced by a newer one
                stats.end(why)

        def _pull_retrying(self, mode: int, gen: int, stats: "LiveStats", current) -> dict:
            """live_pull, but a 451 is transient for up to link.live_off_grace_s (the monitor restarts after a tethered
            shot, rec-review): retry every live_off_retry_s, /live/status says paused / "camera busy" meanwhile.
            LiveOff only escapes after that long of 451 in a row."""
            since = None
            try:
                while True:
                    try:
                        frame = live_pull(link, mode)
                        break
                    except LiveOff:
                        now = time.monotonic()
                        since = now if since is None else since
                        if now - since >= link.live_off_grace_s:
                            raise
                        stats.set_busy(True)
                        time.sleep(link.live_off_retry_s)
                        if current() != gen:
                            raise _StreamStop
            finally:
                stats.set_busy(False)
            return frame

        def _body_json(self, allow_list: bool = False):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4096:
                raise BridgeError(413, "Body too large.")
            try:
                obj = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                raise BridgeError(400, "Send a JSON object.")
            if not isinstance(obj, dict) and not (allow_list and isinstance(obj, list)):
                raise BridgeError(400, "Send a JSON object.")
            return obj

        def _photos_import(self, method: str, route: str, query):
            """GET|POST|DELETE /photos/import and POST /photos/folder (photoimport.py). `choose` tells the app whether
            this client may change the folder: in phone mode (--lan) only this computer may."""
            imp = importer(link)
            here = not token or self.client_address[0].startswith("127.") or self.client_address[0] == "::1"
            try:
                if route == "/photos/import" and method == "GET":
                    out = imp.snapshot(keys=query.get("job", ["0"])[0] != "1")
                elif route == "/photos/import" and method == "POST":
                    _photos_blocked(link)
                    out = imp.start()
                elif route == "/photos/import" and method == "DELETE":
                    out = imp.stop()
                elif route == "/photos/folder" and method == "POST":
                    if not here:
                        raise BridgeError(403, photoimport.FOLDER_HERE_ONLY, code="folder_here_only")
                    out = imp.set_folder(self._body_json().get("path"))
                else:
                    raise BridgeError(404, f"No such endpoint: {method} {route}")
            except photoimport.Refused as error:
                raise BridgeError(error.status, error.message, code=error.code)
            out["choose"] = here
            return self._json(200, out)

        def _guard(self) -> bool:
            """Loopback mode: loopback clients, our own Host header (DNS-rebinding guard), and only allowed Origins.
            Phone mode (--lan): the token, nothing else (a rebinding page cannot know it)."""
            if token:
                if self.command == "OPTIONS" or self._token_ok():
                    return True
                path = urlsplit(self.path).path
                if self.command in ("GET", "HEAD") and (path in STATIC_ROUTES or path == "/legacy/" or app_file(path)):
                    return True  # page shell, the app's files: no camera access
                self._json(403, {"error": "token required (?token= or X-Koma-Token)", "code": "token"})
                return False
            if not (self.client_address[0].startswith("127.") or self.client_address[0] == "::1"):
                self._json(403, {"error": "loopback only"})
                return False
            if self.headers.get("Host") not in hosts:
                self._json(403, {"error": "unexpected Host header"})
                return False
            origin = self.headers.get("Origin")
            if origin and not _ORIGIN.match(origin):
                self._json(403, {"error": f"origin not allowed: {origin}"})
                return False
            return True

        def do_OPTIONS(self):
            if not self._guard():
                return
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, " + TOKEN_HEADER)
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _dispatch(self, method: str):
            if not self._guard():
                return
            url = urlsplit(self.path)
            route = url.path.rstrip("/") or "/"
            query = parse_qs(url.query)
            if method in ("GET", "HEAD") and url.path == "/legacy/":  # the old page's relative links need /legacy
                self.send_response(301)
                self.send_header("Location", "/legacy" + ("?" + url.query if url.query else ""))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                if route == "/webcam" or route.startswith("/webcam/"):  # the Webcam area (webcamhost.py)
                    import webcamhost
                    return webcamhost.dispatch(self, link, port, method, route, query)
                if route == "/phone" or route.startswith("/phone/"):  # Use on your phone (phoneaccess.py)
                    import phoneaccess
                    return phoneaccess.dispatch(self, link, port, method, route, sys.modules[__name__])
                if getattr(link, "webcam", None) is not None:  # the webcam holds the camera: only it ends live view
                    import webcamhost
                    if webcamhost.refuse(self, link, method, route, query):
                        return None
                if method == "GET" and route == "/status":
                    return self._json(200, link.status())
                if method == "GET" and route == "/settings":
                    return self._json(200, get_settings(link))
                if method == "POST" and route == "/settings":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > 64 * 1024:
                        raise BridgeError(413, "Body too large.")
                    return self._json(200, put_settings(link, self.rfile.read(length)))
                if method == "POST" and route == "/reboot":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length:
                        self.rfile.read(min(length, 4096))
                    return self._json(200, reboot(link))
                if route == "/tether/interval":
                    if method == "GET":
                        return self._json(200, link.interval.snapshot())
                    if method == "DELETE":
                        return self._json(200, {"ok": True, "interval": link.interval.stop()})
                    if method == "POST":
                        return self._json(200, interval_start(link, self._body_json()))
                if method == "POST" and route.startswith("/tether/"):
                    length = int(self.headers.get("Content-Length") or 0)
                    if length:
                        self.rfile.read(min(length, 4096))  # sendBeacon may carry a small body; ignored
                    action = route[len("/tether/"):]
                    if action == "start":
                        return self._json(200, tether_start(link))
                    if action == "shoot":
                        return self._json(200, tether_shoot(link, query.get("fetch", ["0"])[0] == "1"))
                    if action == "focus":
                        return self._json(200, tether_focus(link))
                    if action == "stop":
                        return self._json(200, tether_stop(link))
                if method == "GET" and route == "/live":
                    self._cid = re.sub(r"[^A-Za-z0-9_-]", "", query.get("cid", [""])[0])[:32] or None
                    return self._live_stream(_parse_mode(query), self.command == "HEAD")
                if method == "GET" and route == "/live/frame":
                    mode = _parse_mode(query)
                    try:
                        frame = live_pull(link, mode)
                    except LiveOff as off:
                        raise BridgeError(409, live_off_message(mode), code="live_off", reply=off.reply)
                    image, ctype = frame_image(frame)
                    extra = [("X-Frame-Seq", str(frame["seq"])), ("X-Frame-Truncated", "1" if frame["truncated"] else "0"),
                             ("X-Frame-Flags", str(frame["flags"]))]
                    if frame.get("palette_note"):
                        extra.append(("X-Frame-Note", frame["palette_note"]))
                    return self._send(200, image, ctype, extra)
                if method == "GET" and route == "/live/status":
                    osd = query.get("mode", [""])[0] == "2"
                    return self._json(200, (link.osd if osd else link.live).snapshot())
                if method == "POST" and route == "/live/stop":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length:
                        self.rfile.read(min(length, 4096))
                    which = query.get("mode", [""])[0]
                    link.stop_live({"2": "osd", "1": "video", "0": "video", "3": "video"}.get(which))
                    return self._json(200, {"ok": True})
                if route == "/ram" and method == "GET":
                    try:
                        off, length = int(query.get("off", [""])[0], 0), int(query.get("len", ["1"])[0], 0)
                    except ValueError:
                        raise BridgeError(400, "GET /ram?off=N&len=L (decimal or 0x hex, offsets in the block).")
                    return self._json(200, ram_read(link, off, length))
                if route == "/ram/state" and method == "GET":  # -KSTA, read-only (komacam session health)
                    return self._json(200, ram_state_json(link))
                if route == "/ram/peek" and method == "GET":  # -PEEK, read-only, PEEK_LO..PEEK_END only
                    try:
                        addr, length = int(query.get("addr", [""])[0], 0), int(query.get("len", ["1"])[0], 0)
                    except ValueError:
                        raise BridgeError(400, "GET /ram/peek?addr=0xADDR&len=L (1..256).")
                    return self._json(200, peek_json(link, addr, length))
                if route == "/ram" and method == "POST":
                    return self._json(200, ram_write(link, _ram_checked([self._body_json()])))
                if route == "/ram/batch" and method == "POST":
                    body = self._body_json(allow_list=True)
                    return self._json(200, ram_write(link, _ram_checked(body.get("writes") if isinstance(body, dict)
                                                                         else body)))
                if method == "GET" and route == "/keys":
                    return self._json(200, key_listing(link))
                if method == "POST" and route in ("/keys", "/keys/tap", "/dial"):
                    obj = self._body_json()
                    if route == "/dial":
                        return self._json(200, set_dial(link, _int_field(obj, "n")))
                    action = KEY_TAP if route == "/keys/tap" else _int_field(obj, "action")
                    return self._json(200, press_key(link, _int_field(obj, "code"), action))
                if route in ("/photos/import", "/photos/folder"):  # Photos import: no card read in the request itself
                    return self._photos_import(method, route, query)
                if route in ("/komalog", "/install", "/install/QRFLECT3", "/install/GRMOD.BIN", "/install/KOMAINST.BIN",
                             "/install/stock"):
                    import komainstall as KI  # the app's setup glue: card files and KOMALOG.TXT; no camera writes
                    try:
                        if method == "GET" and route == "/komalog":
                            return self._json(200, KI.get_komalog(link))
                        if method == "GET" and route == "/install":
                            return self._json(200, KI.install_info())
                        if method == "GET" and route == "/install/QRFLECT3":
                            return self._send(200, KI.firmware_image(query.get("image", [""])[0]), "application/octet-stream")
                        if method == "GET" and route == "/install/GRMOD.BIN":
                            return self._send(200, KI.package_bytes(), "application/octet-stream")
                        if method == "GET" and route == "/install/KOMAINST.BIN":
                            return self._send(200, KI.installer_bytes(), "application/octet-stream")
                        if method == "POST" and route == "/install/stock":
                            length = int(self.headers.get("Content-Length") or 0)
                            if length < 0:
                                raise BridgeError(400, "Bad Content-Length.")
                            if length > KI.STOCK_MAX:
                                raise BridgeError(413, "Body too large.")
                            return self._json(200, KI.take_stock(self.rfile.read(length)))
                    except KI.InstallError as error:
                        raise BridgeError(error.status, error.message, **error.extra)
                if method == "GET" and (route == "/photos" or route.startswith("/photos/")):
                    _photos_blocked(link)
                if method == "GET" and route == "/photos" and query.get("newest", ["0"])[0] == "1":
                    return self._json(200, newest_photo(link, guard=True))
                if method == "GET" and route == "/photos":
                    return self._json(200, list_photos(link, guard=True))
                if method == "GET" and route.startswith("/photos/"):
                    rel = unquote(route[len("/photos/"):])
                    data = get_photo(link, rel, query.get("thumb", ["0"])[0] == "1", guard=True)
                    return self._send(200, data, "image/jpeg",
                                      [("Content-Disposition", f'inline; filename="{rel.rsplit("/", 1)[-1]}"')])
                if method == "GET" and (route == "/" or route.startswith("/assets/") or route.count("/") == 1):
                    found = app_file(url.path if url.path.startswith("/assets/") else route)
                    if found:  # the Koma app (tools/komaapp/dist); / falls back to the old page without a build
                        return self._send(200, found[0].read_bytes(), found[1])
                if method == "GET" and route in STATIC_ROUTES and STATIC_ROUTES[route][0].is_file():
                    # the page itself (phone mode needs no file://), app shell
                    path, ctype = STATIC_ROUTES[route]
                    return self._send(200, path.read_bytes(), ctype)
                if method == "GET" and route == "/settings-map.json":
                    return self._send(200, MAP_PATH.read_bytes(), "application/json")
                if method == "GET" and route == "/api":
                    return self._json(200, {"bridge": "komabridge", "endpoints": [
                        "GET / (the Koma app)", "GET /legacy (the Koma Setup page)", "GET /settings-map.json", "GET /ram?off=&len=", "POST /ram",
                        "POST /ram/batch",
                        "GET /status", "GET /settings", "POST /settings", "POST /reboot", "GET /photos",
                        "GET /photos/<folder>/<name>[?thumb=1]", "GET|POST|DELETE /photos/import[?job=1]",
                        "POST /photos/folder", "POST /tether/start", "POST /tether/shoot[?fetch=1]",
                        "POST /tether/focus", "POST /tether/stop", "POST|GET|DELETE /tether/interval",
                        "GET /live?mode=1|0|2", "GET /live/frame?mode=1|0|2", "GET /live/status[?mode=2]",
                        "POST /live/stop[?mode=1|2]", "GET /keys", "POST /keys", "POST /keys/tap", "POST /dial",
                        "GET /komalog", "GET /install", "GET /install/QRFLECT3?image=koma|official", "GET /install/GRMOD.BIN",
                        "GET /install/KOMAINST.BIN",
                        "POST /install/stock",
                        "GET /webcam[?check=1]", "POST /webcam/start", "POST /webcam/stop", "GET /webcam/preview",
                        "GET /phone", "POST /phone/on", "POST /phone/off", "POST /phone/revoke", "POST /phone/pair"]})
                raise BridgeError(404, f"No such endpoint: {method} {route}")
            except BridgeError as error:
                return self._json(error.status, {"error": error.message, **error.extra})
            except ProtocolError as error:
                return self._json(502, {"error": f"{type(error).__name__}: {error}"})
            except (CommandRefused, komaset.MapError, FileNotFoundError) as error:
                return self._json(500, {"error": f"{type(error).__name__}: {error}"})

        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer without the name lookup. HTTPServer.server_bind asks DNS for the bound address's name
    (socket.getfqdn) after binding and before listening; where DNS does not answer (no network, a captive portal, some
    CI Macs) that holds the port bound but unanswered for 30 s or more. Nothing here uses server_name."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = str(self.server_address[0]), self.server_address[1]


def make_server(link: Link, host: str = HOST, port: int = PORT, token: str | None = None) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1") and not (host == "0.0.0.0" and token):
        raise SystemExit("komabridge listens on loopback only (or 0.0.0.0 with a token: --lan)")
    server = Server((host, port), make_handler(link, port, token))
    server.daemon_threads = True
    return server


REAL_WARNING = """\
WARNING: komabridge --real talks to a REAL camera over USB.
  Its selftest passed 9/9 on a camera with the Koma tether feature: reads (-VER, PWD, -NLST, RETR, -INFO, -FDAT,
  -LVFR 0/1/2, -KGET), a tethered shot, an EV change by -KSET. It can write: -STOR of /EXT0/PARAM.DAT, -CCTL poff,{poff}
  (never sent on hardware), -KSET, and the remote keys (-CCTL remtin, diaset,0..8, keyset,0|1|3 with any key code 0..41 except the
  power, PTP-switch and AV-jack codes 6, 7, 8, 9, 24, 36, remtout). The keys reach the whole camera menu, FORMAT and
  RESTORE DEFAULTS included. The shutter fires only with the Koma tether feature loaded. Power the camera on BEFORE
  plugging in USB. Stop with Ctrl-C.
""".format(poff=POFF_ARG)


LAN_NOTE = """\
PHONE MODE (--lan): the bridge also listens on this computer's network address. The first phone that opens the address
  below (or scans its QR code) pairs, and the code then stops working. A paired phone can read the card and press the
  camera's buttons. Revoke or turn it off in the Koma app: Settings > Use on your phone. Use it on a network you trust.
"""


def lan_address() -> str:
    """This machine's address on the local network (the interface a datagram to a public address would leave by; a UDP
    connect sends nothing). Falls back to the host name's address, then 127.0.0.1."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Local USB bridge for the Koma Setup page (127.0.0.1 only).")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="serve a simulated camera (grdusb mock)")
    mode.add_argument("--real", action="store_true", help="open a real camera over USB (off by default)")
    mode.add_argument("--emu", action="store_true",
                      help="the real 2.40 firmware + a Koma module in the emulator (needs KOMA_FIRMWARE and unicorn)")
    ap.add_argument("--module", help="--emu: the GRMOD to load (default: emulink.DEFAULT_MODULE)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--mock-reboot-s", type=float, default=4.0, help="mock: seconds the camera is away after poff")
    ap.add_argument("--mock-stays-off", action="store_true", help="mock: after poff the camera does not come back")
    ap.add_argument("--mock-hw", action="store_true",
                    help="mock: timings measured on a camera (20 s second-session arm, 7 s -KSET busy after an EV change, "
                         "live view back 7 s after a shot, a card read in remote control drops live view)")
    ap.add_argument("--mock-tether", action="store_true",
                    help="mock: the Koma tether feature is loaded, so a full press makes a new JPEG (~1.5 s later)")
    ap.add_argument("--mock-koma", action="store_true",
                    help="mock: PARAM.CUR is a Koma camera's (KOMA header, look store, KSIO sub-header, GR II layout)")
    ap.add_argument("--mock-earlier", action="store_true",
                    help="mock: as --mock-koma, on an earlier (pre-1.0) Koma build: firmware tag 240K2, not 240K1.0")
    ap.add_argument("--mock-patterns", action="store_true",
                    help="mock: the drawn test photos and moving live pattern, not the sample frames from "
                         "tools/komaapp/design/samples (the default when that folder is there)")
    ap.add_argument("--quiet", action="store_true", help="no request log")
    ap.add_argument("--lan", action="store_true",
                    help="phone mode at start: also listen on this computer's network address, with a one-time pairing "
                         "code (the app's Settings > Use on your phone switches the same thing at runtime)")
    ap.add_argument("--mock-vcam", action="store_true",
                    help="mock: the Webcam area sees a simulated computer with OBS Virtual Camera ready and feeds a "
                         "simulated virtual camera (webcamhost.MockProbe), so it never touches this computer's own")
    ap.add_argument("--selftest", action="store_true",
                    help="no server: run the hardware USB steps once (selftest.py), "
                         "write report.md + report.json + artifacts to --out, exit 0 if nothing failed")
    ap.add_argument("--out", default=str(Path.home() / "koma-session"),
                    help="--selftest: the directory for the report and saved files (default ~/koma-session)")
    a = ap.parse_args(argv)
    if not (a.mock or a.real or a.emu):
        print("refused: choose --mock (simulated camera), --emu (firmware in the emulator) or --real (a real camera; "
              "untested on hardware).", file=sys.stderr)
        return 2
    if a.emu:
        import emulink
        ok, why = emulink.deps_ok()
        if not ok:
            print(f"--emu needs the emulator: {why}", file=sys.stderr)
            return 2
        module = Path(a.module).read_bytes() if a.module else None
        backend = emulink.EmuBackend(module, log=lambda text: sys.stderr.write(text + "\n"))
        sys.stderr.write("komabridge --emu: SIMULATED. The firmware runs in the emulator; booting (about 70 s), "
                         "/status says connected when it is up.\n")
    elif a.real:
        sys.stderr.write(REAL_WARNING)
        backend = RealBackend()
    else:
        import mockcard
        from mockcard import MockBody, NOTICE, SAMPLES_DIR
        backend = MockBackend(MockBody(reboot_s=a.mock_reboot_s, returns_after_poff=not a.mock_stays_off,
                                       tether=a.mock_tether, koma=a.mock_koma or a.mock_earlier,
                                       **({"koma_tag": mockcard.EARLIER_MOCK_TAG} if a.mock_earlier else {}),
                                       samples=None if a.mock_patterns or not SAMPLES_DIR.is_dir() else SAMPLES_DIR))
        if a.mock_hw:
            backend.body.hw_timings()
        sys.stderr.write(NOTICE + "\n")
    if a.selftest:
        import selftest
        if a.mock and a.mock_tether:
            selftest.seed_mock_koma(backend.body)  # the mock plays a Koma camera: a simulated KOMALOG.TXT
        link = Link(backend)
        link.log = lambda text: sys.stderr.write(text + "\n")
        return selftest.run(link, a.out, log=link.log, connect_wait_s=180.0 if a.emu else 0.0)
    link = Link(backend)
    link.log = lambda text: sys.stderr.write(text + "\n")
    link.rearm_after_read = True  # the page reads photos inside remote control; the selftest re-arms itself
    link.start_keepalive()
    if a.mock and a.mock_vcam:
        import webcamhost
        webcamhost.configure_mock(link)
    server = make_server(link, host=HOST, port=a.port)
    server.quiet = a.quiet
    sys.stderr.write(f"komabridge {BRIDGE_VERSION} ({backend.name}) on http://127.0.0.1:{a.port}/ "
                     f"(the page is served there too)\n")
    if a.lan:  # phone mode: the runtime switch of Settings > Use on your phone, turned on at start (phoneaccess.py)
        import phoneaccess
        snap = phoneaccess.for_link(link, a.port, sys.modules[__name__]).turn_on()
        if snap["on"]:
            sys.stderr.write(LAN_NOTE)
            print(f"komabridge {BRIDGE_VERSION} ({backend.name}), phone mode. Open this on the phone (same Wi-Fi):\n\n"
                  + phoneaccess.terminal_note(snap["url"]), flush=True)
        else:
            print(f"phone mode did not start: {snap['error']['text']}", file=sys.stderr, flush=True)
    if threading.current_thread() is threading.main_thread():  # Quit (SIGTERM) and a closed terminal stop cleanly too
        import signal

        def _stop(signum, frame):
            raise KeyboardInterrupt
        for sig in ("SIGTERM", "SIGHUP"):
            if hasattr(signal, sig):
                signal.signal(getattr(signal, sig), _stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ends = [sys.modules[n] for n in ("webcamhost", "phoneaccess") if n in sys.modules]
        if ends:  # the webcam ends remote control through this server's own endpoints, so it keeps serving meanwhile
            threading.Thread(target=server.serve_forever, daemon=True).start()
            for mod in ends:
                try:
                    mod.shutdown(link)
                except Exception as error:  # noqa: BLE001
                    sys.stderr.write(f"komabridge: {mod.__name__} shutdown: {type(error).__name__}: {error}\n")
            server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
