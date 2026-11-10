"""ksettings: live look editor over USB. `-KSET <off>,<val>` writes one Koma settings byte in RAM while the camera runs,
`-KGET <off>,<len>` reads the settings block back.

Transport: two dead records of the command table 0x1fb400 (handlers are
`bx lr` at 0xcd398 / 0xcd39a and nothing in the image references the IDs 0x16 / 0x17 outside the table):

    -SDIS (ID 0x16) 0x1fb6d0 -> -KSET   Pointer name 000cb624 -> "-KSET", Patch [4] 0 -> 1 (parse `a,b`),
                                        Patch [7] 1 -> 0 (the stock -SDIS extra field would eat the arguments),
                                        Pointer handler 000cd399 (bx lr) -> kset_handler
    -GDIS (ID 0x17) 0x1fb6f4 -> -KGET   Pointer name 000cb62c -> "-KGET", Patch [4] 0 -> 1,
                                        Pointer handler 000cd39b (bx lr) -> kget_handler

Handlers run in tDcx (dispatcher 0xcc304), h(char **reply, int *len), as liveview's -LVFR. The parser 0xcbd64 leaves
a and b as NUL-terminated strings at 0x2df712 / 0x2df71c (1..7 characters) and their atoi (0xcd8a8, digits only, stops
at the first non-digit) at 0x1fb3e8 / 0x1fb3ec; both handlers re-scan the strings and answer 501 unless both are all
decimal digits.

-KSET off,val (off relative to 0x1b000):
    val > 255 -> "451 -KSET value"; off outside the window -> "451 -KSET range"; a Koma row byte of this build
    (link-time table: offset, option count) with val >= count -> "451 -KSET value"; else the byte is written and the
    reply is "200 -KSET <off>=<val>" (canonical decimal). Window (tools/komaset/settings-map.json):
        0x8b5..0x8cf  Koma rows (linker.ROW_BYTES; MENU LAYOUT / KOMA FEATURES act at the next boot)
        0x910..0x9ff  recipe records (20 x 12 B: p0..p8, kind, 0xa5, xor)
        0xa00..0xa14  look FX table (0xa14: STANDARD, Look IA)
        0xa15..0xa27  Look IA: pad, ADJ LEVER mask 0xa18..0xa1a + check 0xa1b, STANDARD's record 0xa1c..0xa27
    Never: below 0x8b5 (stock bytes and the Koma header), 0x8d0..0x8ff (settings-io sub-header and its reserve),
    0x900..0x90f (RCP6 header), 0xa28.. (incl. the checksum 0xffc).
    Live apply (option apply=on, the default): when idle (see v1.2 below; includes the monitor running, [0x1f502f] == 1;
    capture clears it before develop) and off >= 0x8c0 (recipe rows, records, FX table): $bwrecipes.on_change(val, addr) (the menu's own reconcile:
    edit rows -> record store, RECIPE row -> IMG SET base 1/3/6, SAVE TO CUSTOM / RESET), then
    $preview.pv_apply([0x23b08a] = the monitor's mono flag cp+0x22, site 2 = setting change), the routine preview's
    monitor hooks call. No per-frame re-read exists in preview; see the README for what this does not cover (a base
    change B&W <-> colour <-> toned needs the stock setting-change path).

-KGET off,len: 1 <= len <= 4096 and off + len <= 4096, else "451 -KGET range". Copies len bytes from 0x1b000 + off to
    the USB payload [0x1bd308] + 8 (memcpy 0x157bac, no header), then the -PRET state: [0x1bd2bc] = 0x8001,
    [0x1bd358] = [0x1bd35c] = len, [0x1bd344] = "250 -KGET", [0x1bd2d0] = 4, reply "150 -KGET". The stock data phase
    (0xcb834 state 4, 0xcb9cc state 6) only continues for IDs 2, 10, 13, 14 and 26, so the handler sets the parsed ID
    [0x1fb3e4] to 0x1a after the dispatch (the parser rewrites it for every command): the frame then takes exactly
    -LVFR's single-chunk path (bd348 = 0 -> close 0x8001 = no-op -> reply [0x1bd344]).

Stock whitelist: EV 0x053, ISO 0x059, WB 0x054, WB detail 0x0a6,
metering 0x050, IMG SET 0x030, SETTING1/2 0x031..0x036, TE tone 0x1a9 / density 0x1aa, each only with the values stock
writes (else 451 value), only when idle (app state 2, 0x6eb1c() in {0, 4}, 0x6ef7c() == 0: no menu / ADJ / detail
screen, 0x6f104() == 0, monitor running; else "451 -KSET busy"); written through the stock setter where one exists
(0x6dc18 / 0x6dfb0 / 0x6dc7c / 0x6e034), then the stock apply 0x553d4 once (snapshot -> 0x2103 -> 0x338cc). With
apply=on a recipe edit that moves the IMG SET base also calls 0x553d4 (idle only), so cp+0x22 follows.

v1.2: idle also needs RL1 (half-press) up: merged key scan 0x2b9d84[26] bit 0 clear and injected 0x2b9dd8[26] == 0.
The live apply of a Koma-window write (reconcile + pv_apply) runs only when idle (was: monitor running); otherwise the
byte is written, 200 is replied and the next menu / ADJ / shot sync takes it. Koma rows 0x8b5..0x8cf answer
"451 -KSET busy" while the menu (overlay 5) or ADJ (6; bwrecipes' ADJ IMG SET lever writes the RECIPE row 0x8c0) is
open, because their commit at close rewrites the rows; records and the FX table stay writable.

v1.3: -KSTA (diagnostic, read-only). The dead -FUPL record (ID 0x19; handler `bx lr` at 0xcd39c, nothing references
the ID outside the table) becomes -KSTA, no arguments: one data frame of big-endian u32, the version word
0x4b53LLNN ('KS', layout LL, NN words including itself) then KSTA_WORDS in order (word / halfword / byte loads, each
widened to u32). Plain loads only, no call, no wait, no state test: it answers in any app state (9, 2, ...) and in the
middle of a resolve. Same -PRET-shaped reply as -KGET (150, one frame, 250; ID rewritten to 0x1a for the data phase).

-KSTA layout 2: the 27 layout-1 words keep their offsets; 13 snapshot words follow (wait
candidates: LCD semaphore handles 0x1a8d14 / 18, 0x1a8d40 / 4c, 0x1a9210, 0x1a923c, 0x23a880, 0x1b97e4 / e8, 0x1bf1d0
and the handshake words 0x1bec44 / 48 / 4c). In layout 2 the version word's NN is the number of these fixed words (the
version word included); an extension block may follow: if the resident word `ksta_ext` (exported, 0 at install) holds
a routine address, the handler calls it with r0 = the next payload byte and appends the r0 bytes it returns (waitdiag
registers its wait ring there; without it the frame is the fixed part only). Hosts take the frame length from the data.

-PEEK <addr_hex>,<len> (read-only RAM peek): the dead -PWOF record (ID 0, handler `bx lr` at 0xcd396, no caller
outside the table) becomes -PEEK with `a,b` parsing. The parser takes verbs of 3..5 characters and answers 501 for a
record with ID 0, and the dispatcher 0xcc304 finds the handler by ID (first match), so the record gets the unused ID
0x25 (the IDs in use stop at 0x24; nothing compares the parsed ID with 0x25: 0x53a0c tests 3, 0xcb858 0x18, the data
phase 2/10/13/14/26). The handler rewrites the parsed ID to 0x1a for the data phase, as -KGET does. addr is 1..7 hex digits (either case, no 0x), len decimal
1..256, and [addr, addr + len) must lie inside [0x1a8000, 0x3e5000): the firmware state / pool RAM and the static
resident block (0x3dae00..), nothing below (unproven low regions) or above; anything else answers "501 -PEEK range" (bad
syntax: the parser's 501). The bytes are copied raw (memcpy 0x157bac) into the payload; same -PRET-shaped reply as
-KGET. Never writes outside the USB buffer.

No flash or store-write routine, no pool allocation, no hook on stock code. The power-off save persists RAM as always.
"""
from __future__ import annotations

import struct

from .. import firmware as fw
from ..feature import Check, Feature, Patch, Pointer

NAME = "ksettings"
VERSION = "1.4"
SUMMARY = ("USB live look editor: -KSET <off>,<val> writes a Koma settings byte (window-checked), -KGET <off>,<len> "
           "reads; -KSTA: read-only state snapshot (diagnostic)")
OPTIONS = {"apply": "on (default; requires bwrecipes + preview) | off: write only, no live reconcile / preview re-apply"}

REC_SET, REC_GET = 0x1FB6D0, 0x1FB6F4          # -SDIS, -GDIS records (36 B): [0] name [1] id [4] a,b [7] -SDIS [8] handler
SET_NAME_BEFORE, GET_NAME_BEFORE = bytes.fromhex("000cb624"), bytes.fromhex("000cb62c")
SET_HANDLER_BEFORE, GET_HANDLER_BEFORE = bytes.fromhex("000cd399"), bytes.fromhex("000cd39b")
ID_SET, ID_GET = 0x16, 0x17
REC_STA, ID_STA = 0x1FB760, 0x19                # v1.3: -FUPL record -> -KSTA (no arguments)
STA_NAME_BEFORE, STA_HANDLER_BEFORE = bytes.fromhex("000cb644"), bytes.fromhex("000cd39d")
REC_PEEK, ID_PEEK = 0x1FB6AC, 0x25               # -PWOF record (ID 0) -> -PEEK <addr_hex>,<len>, ID 0x25
PEEK_NAME_BEFORE, PEEK_HANDLER_BEFORE = bytes.fromhex("000cb61c"), bytes.fromhex("000cd397")
PEEK_MAX, PEEK_LO, PEEK_END = 256, 0x1A8000, 0x3E5000   # len 1..256; [addr, addr+len) inside [PEEK_LO, PEEK_END):
                                                     # state / pool RAM + the static resident block
S501_PEEK = "501 -PEEK range\r\n"

# -KSTA words (name, address, load width 4 / 2 / 1). Sources: tether.py (USB block 0x23a864, remtin
# flag 0x1bd1b8 block), the ERROR 03 path (0x1fb9bc; 0x29b28 end: entry flag 0x1bf0dc, then at 0x29c1c it copies
# 0x6eb7c = [0x1befac], 0x6eb94 = [0x1befb0] (u8), 0x6ebac = [0x1befb1] (u8) into the end record), the APO block
# 0x2b9e50 (0x52080), battlog's battery driver words and the
# RTOS tick word 0x1a8a00 (0x5df20 -> 0x1e1b0).
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
KSTA_WORDS_V1 = list(KSTA_WORDS)                 # layout 1 (v1.3): 27 words + version
# layout 2: snapshot words appended (wait candidates)
KSTA_WORDS += [
    ("lcd_sem_1a8d14", 0x1A8D14, 4), ("lcd_sem_1a8d18", 0x1A8D18, 4), ("lcd_1a8d40", 0x1A8D40, 4),
    ("lcd_1a8d4c", 0x1A8D4C, 4), ("sem_1a9210", 0x1A9210, 4), ("sem_1a923c", 0x1A923C, 4),
    ("disp_23a880", 0x23A880, 4), ("w_1b97e4", 0x1B97E4, 4), ("w_1b97e8", 0x1B97E8, 4), ("w_1bf1d0", 0x1BF1D0, 4),
    ("hs_1bec44", 0x1BEC44, 4), ("hs_1bec48", 0x1BEC48, 4), ("hs_1bec4c", 0x1BEC4C, 4),
]
KSTA_LAYOUT = 2
KSTA_N = len(KSTA_WORDS) + 1                     # fixed words in the frame, the version word included
KSTA_VERSION = 0x4B530000 | KSTA_LAYOUT << 8 | KSTA_N
KSTA_BYTES = 4 * KSTA_N
_WCODE = {4: 0, 2: 1, 1: 2}                      # table entry: address | code << 28


def ksta_decode(data: bytes) -> dict:
    """Host decode of a -KSTA frame: {name: value}, plus 'version' and, in layout 2 with an extension block, 'ext'
    (the raw big-endian words after the fixed part). Layout 1 (v1.3, 28 words) still decodes. ValueError otherwise."""
    if len(data) < 4 or len(data) % 4:
        raise ValueError(f"-KSTA frame of {len(data)} bytes")
    words = struct.unpack(f">{len(data) // 4}I", data)
    lay, n = (words[0] >> 8) & 0xFF, words[0] & 0xFF
    table = {1: KSTA_WORDS_V1, 2: KSTA_WORDS}.get(lay)
    if words[0] >> 16 != 0x4B53 or table is None or n != len(table) + 1 or len(words) < n or \
            (lay == 1 and len(words) != n):
        raise ValueError(f"-KSTA version word {words[0]:#010x} with {len(words)} words")
    out = {"version": words[0], **{nm: v for (nm, _, _), v in zip(table, words[1:n])}}
    if len(words) > n:
        out["ext"] = list(words[n:])
    return out

BLOCK = 0x1B000
BLOCK_LEN = 0x1000
WINDOW = [(0x8B5, 0x8D0, "Koma rows"), (0x910, 0xA00, "recipe records"), (0xA00, 0xA28, "look FX table, ADJ LEVER mask, STANDARD record")]
APPLY_FROM = 0x8C0                               # recipe rows, records and FX table: reconcile + preview re-apply
ROW_BASE = 0x8B0
KGET_MAX = 0x1000

ARG_A, ARG_B = 0x1FB3E8, 0x1FB3EC                # parser atoi results
STR_A, STR_B = 0x2DF712, 0x2DF71C                # parser argument strings (NUL-terminated, 1..7 chars)
CMD_ID = 0x1FB3E4                                # parsed record ID (the data-phase gate reads it)
DATA_ID = 0x1A                                   # an ID the stock data phase continues for (-FDWL/-LVFR's)
MONI_RUN = 0x1F502F                              # u8, 1 while the monitor runs
MONO_FLAG = 0x23B08A                             # cp 0x23b068 + 0x22: the monitor's mono flag (0x8f280 passes it)
USB_BUF = 0x1BD308
HANDLE, TOTAL, CHUNK, FINAL, XSTATE = 0x1BD2BC, 0x1BD358, 0x1BD35C, 0x1BD344, 0x1BD2D0
REPLY_FLAG, REPLY_FLAG2 = 0x1BD2C2, 0x1BD2C0
MEMCPY, STRLEN = 0x157BAC, 0x158090
FW_150, FW_250 = 0xCCD50, 0xCCD74
FW_501 = 0xCC15C                                 # parser string "501 Syntax error in parameters or arguments.\r\n"
OBUF = 0x2DF7BC                                  # parser's -SDIS field buffer (32 B before the verb buffer 0x2df7dc);
                                                 # read only for records with [7] = 1, and -SDIS was the only one
# v1.1: stock bytes. Whitelist: (offset, value class, stock setter or 0, name)
STOCK_APPLY = 0x553D4                            # stock apply: 0x54d84 snapshot -> message 0x2103 to tCameraMain
SETTERS = [0x6DC18, 0x6DFB0, 0x6DC7C, 0x6E034]   # EV, ISO, WB, metering (setter index 1..4)
CLASSES = [set(range(1, 26, 2)), {0, 1, 2, 4, 6, 7, 8}, {0, 1, 2, 3, 4, 5, 7}, set(range(17)), {0, 1, 2},
           set(range(7)), set(range(1, 6)), {1, 2, 3, 4, 6}]
STOCK = [(0x053, 0, 1, "EV comp"), (0x059, 1, 2, "ISO"), (0x054, 2, 3, "white balance"), (0x0A6, 3, 0, "WB detail"),
         (0x050, 4, 4, "metering"), (0x030, 5, 0, "IMG SET")] + \
        [(0x031 + i, 6, 0, n) for i, n in enumerate(["SETTING1 contrast", "SETTING1 sharpness", "SETTING1 colour",
                                                      "SETTING2 contrast", "SETTING2 sharpness", "SETTING2 colour"])] + \
        [(0x1A9, 7, 0, "TE tone"), (0x1AA, 6, 0, "TE density")]
APP_STATE = 0x1BD1C0                             # 2 = still record state (tether keeps it under USB)
UI_MODE = 0x1BEFA6                               # 0x6eb1c: 0 or 4 allowed (1 SCENE, 2 movie, ...)
UI_OVERLAY = 0x1BEFC0                            # 0x6ef7c: 0 none, 5 menu, 6 ADJ, 7 detail screens (EMU + STATIC)
UI_CMD = 0x1BEFC2                                # 0x6f104: command outstanding (set by 0x553d4, cleared by the reply)
SITE_SETTING = 2                                 # preview site id of the setting-change path
KEY_SCAN = 0x2B9D84                              # v1.2: merged key scan (u8 per code; 0x50fc0 GPIO 0x30 -> [26],
KEY_INJ = 0x2B9DD8                               # active low via 0x2571e; 0x50eb0 overlays injected 1/3), injected keys
KEY_RL1 = 26                                     # (sink 0x51740); RL1 = half-press

S150 = "150 data connection for -PRET.\r\n"    # firmware strings, reused (0xccd50 / 0xccd74)
S250 = "250 -PRET command success.\r\n"
S451_GET = "451 -KGET range\r\n"
S451_RANGE = "451 -KSET range\r\n"
S451_VALUE = "451 -KSET value\r\n"
S451_BUSY = "451 -KSET busy\r\n"
S501 = "501 Syntax error in parameters or arguments.\r\n"


def in_window(off: int) -> bool:
    return any(lo <= off < hi for lo, hi, _ in WINDOW)


def row_counts(rows) -> dict:
    """{offset from 0x1b000: option count} for the build's Koma rows (the settingsio clamp table's counts)."""
    return {r["byte"] - BLOCK: len(r["options"]) for r in rows if in_window(r["byte"] - BLOCK)}


def stock_entry(off: int):
    return next((e for e in STOCK if e[0] == off), None)


def model(off: int, val: int, counts: dict, idle: bool = True, overlay: int = 0) -> str:
    """Host model of the -KSET verdict (numbers already parsed): the reply line. idle = the stock preconditions,
    overlay = [0x1befc0] (5 menu / 6 ADJ refuse Koma row writes)."""
    if val > 255:
        return S451_VALUE
    e = stock_entry(off)
    if e:
        if val not in CLASSES[e[1]]:
            return S451_VALUE
        if not idle:
            return S451_BUSY
        return f"200 -KSET {off}={val}\r\n"
    if not in_window(off):
        return S451_RANGE
    if off in counts and val >= counts[off]:
        return S451_VALUE
    if WINDOW[0][0] <= off < WINDOW[0][1] and overlay in (5, 6):
        return S451_BUSY
    return f"200 -KSET {off}={val}\r\n"     # the camera echoes the argument strings as sent


def _esc(s: str) -> str:
    return s.encode("unicode_escape").decode()


def count_table(rows) -> bytes:
    """Option count per byte 0x8b5..0x8cf (0 = no Koma row of this build: any 0..255)."""
    lo, hi, _ = WINDOW[0]
    t = bytearray(hi - lo)
    for off, n in row_counts(rows).items():
        if lo <= off < hi:
            t[off - lo] = n
    return bytes(t)


def _source(rows, apply: bool) -> str:
    (w0, w1, _), (w2, _, _), (_, w3, _) = WINDOW
    cnt = ", ".join(str(b) for b in count_table(rows))
    masks = "".join(f"    .word {sum(1 << v for v in c):#x}\n" for c in CLASSES)
    setters = "".join(f"    .word {a | 1:#x}\n" for a in SETTERS)
    stock = "".join(f"    .hword {o:#x}\n    .byte {c}, {i}\n" for o, c, i, _ in STOCK)
    ksta_tab = "".join(f"    .word {a | _WCODE[w] << 28:#x}    @ {n}\n" for n, a, w in KSTA_WORDS)
    apply_src = f"""
    cmp r3, #{APPLY_FROM - w0:#x}   @ off >= 0x8c0: recipe rows, records, FX table
    blo 4f
    bl ks_idle                      @ v1.2: live apply only when idle (monitor running, no menu / ADJ / detail screen,
    bne 4f                          @ no command outstanding, RL1 up); else the byte stays and the next sync takes it
    ldr r2, ={BLOCK + 0x30:#x}
    ldrb r2, [r2]
    push {{r2}}                      @ IMG SET before the reconcile
    movs r0, r5                     @ r0 = value, r1 = byte address (menu5's on_change convention)
    bl $bwrecipes.on_change
    ldr r0, ={MONO_FLAG:#x}
    ldrb r0, [r0]
    movs r1, #{SITE_SETTING}
    bl $preview.pv_apply
    pop {{r2}}
    ldr r0, ={BLOCK + 0x30:#x}
    ldrb r0, [r0]
    cmp r0, r2
    beq 4f                          @ base unchanged: pv_apply was enough
    bl ks_idle                      @ base moved (B&W / colour / toned): the stock setting change refreshes cp+0x22
    bne 4f
    ldr r3, ={STOCK_APPLY + 1:#x}
    bl ks_call_r3
4:""" if apply else ""
    return f"""
@ kset_handler / kget_handler(r0 = char **reply, r1 = int *len), run in tDcx by the -SDIS / -GDIS records.
@ r4 = a, r5 = b, r6 = reply ptr, r7 = len ptr.
kset_handler:
    push {{r4, r5, r6, r7, lr}}
    bl ks_pre
    beq 0f
    b ks_501                        @ (out of bne range since v1.2)
0:    adr r0, s451v
    cmp r5, #0xff
    bhi ks_rep
    adr r1, ks_stock                @ stock whitelist (v1.1)
5:  ldrh r2, [r1]
    cmp r2, #0
    beq ks_koma
    cmp r2, r4
    beq 51f
    adds r1, #4
    b 5b
51: ldrb r2, [r1, #2]               @ value class: bit v of its mask = a value stock writes
    lsls r2, r2, #2
    adr r3, ks_masks
    ldr r2, [r3, r2]
    cmp r5, #31
    bhi ks_rep
    lsrs r2, r5
    lsrs r2, r2, #1
    bcc ks_rep
    bl ks_idle
    adr r0, s451b
    bne ks_rep
    ldrb r3, [r1, #3]               @ stock setter index, 0 = plain byte
    movs r0, r5
    cmp r3, #0
    bne 6f
    ldr r1, ={BLOCK:#x}
    strb r5, [r1, r4]
    b 7f
6:  subs r3, #1
    lsls r3, r3, #2
    adr r2, ks_setters
    ldr r3, [r2, r3]
    bl ks_call_r3                   @ the stock setter (r0 = value)
7:  ldr r3, ={STOCK_APPLY + 1:#x}
    bl ks_call_r3                   @ 0x553d4: snapshot 0x54d84 -> 0x2103 -> 0x338cc (cp, live view), flag 0x1befc2
    b ks_ok
ks_koma:
    adr r0, s451v
    ldr r1, ={w0:#x}
    subs r3, r4, r1                 @ r3 = off - 0x8b5 (kept for the apply test)
    cmp r3, #{w1 - w0}
    bhs 1f
    adr r1, ks_cnt                  @ Koma row of this build: val < option count
    ldrb r1, [r1, r3]
    cmp r1, #0
    beq 2f
    cmp r5, r1
    bhs ks_rep
2:  ldr r1, ={UI_OVERLAY:#x}        @ v1.2: menu (5) or ADJ (6) open: their cursor commit at close rewrites the rows
    ldrb r1, [r1]
    subs r1, #5
    cmp r1, #1
    adr r0, s451b                   @ (adr leaves the flags)
    bls ks_rep
    b 3f
1:  adr r0, s451r
    movs r1, r3
    subs r1, #{w2 - w0:#x}
    movs r2, #{(w3 - w2) // 2:#x}
    lsls r2, r2, #1
    cmp r1, r2
    bhs ks_rep                      @ not 0x910..0xa27
3:  ldr r1, ={BLOCK:#x}
    adds r1, r1, r4
    strb r5, [r1]                   @ the one write to the settings block
{apply_src}
ks_ok:
    ldr r0, ={OBUF:#x}              @ "200 -KSET <a>=<b>\\r\\n" (the argument strings, all digits)
    adr r1, s200
    bl ks_puts
    ldr r1, ={STR_A:#x}
    bl ks_puts
    movs r1, #0x3d                  @ '='
    strb r1, [r0]
    adds r0, #1
    ldr r1, ={STR_B:#x}
    bl ks_puts
    movs r1, #13
    strb r1, [r0]
    movs r1, #10
    strb r1, [r0, #1]
    movs r1, #0
    strb r1, [r0, #2]
    ldr r0, ={OBUF:#x}
ks_rep:                             @ r0 = reply line: reply only (state 3)
    str r0, [r6]
    ldr r3, ={STRLEN + 1:#x}
    bl ks_call_r3
    str r0, [r7]
    ldr r0, ={HANDLE:#x}
    movs r1, #1
    strb r1, [r0, #{REPLY_FLAG - HANDLE}]
    movs r1, #0
    strb r1, [r0, #{REPLY_FLAG2 - HANDLE}]
    movs r1, #3
ks_state:                           @ r0 = 0x1bd2bc, r1 = state
    str r1, [r0, #{XSTATE - HANDLE:#x}]
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
ks_501:
    ldr r0, ={FW_501:#x}            @ the parser's own "501 Syntax error in parameters or arguments."
    b ks_rep

kget_handler:
    push {{r4, r5, r6, r7, lr}}
    bl ks_pre
    bne ks_501
    adr r0, s451g
    subs r1, r5, #1
    movs r2, #1
    lsls r2, r2, #12                @ 4096
    cmp r1, r2
    bhs ks_rep                      @ len 0 or > 4096
    adds r1, r4, r5                 @ a, b < 10^7: no overflow
    cmp r1, r2
    bhi ks_rep                      @ off + len > 4096
    ldr r3, ={HANDLE:#x}
    ldr r0, [r3, #{USB_BUF - HANDLE:#x}]
    adds r0, #8                     @ payload of TX half 0
    ldr r1, ={BLOCK:#x}
    adds r1, r1, r4
    movs r2, r5
    ldr r3, ={MEMCPY + 1:#x}
    bl ks_call_r3
ks_data:
    ldr r0, ={FW_150:#x}           @ the firmware's -PRET 150 / 250 lines (hosts read the code)
    str r0, [r6]
    movs r1, #{len(S150)}
    str r1, [r7]
    ldr r0, ={HANDLE:#x}
    ldr r1, ={0x8001:#x}
    str r1, [r0]                    @ RAM-sourced: the end-of-transfer close of 0x8001 is a no-op
    movs r1, r0
    adds r1, #{FINAL - HANDLE:#x}
    ldr r2, ={FW_250:#x}
    str r2, [r1]                    @ [0x1bd344] = final reply
    str r5, [r1, #{TOTAL - FINAL:#x}]
    str r5, [r1, #{CHUNK - FINAL:#x}]
    ldr r1, ={CMD_ID:#x}
    movs r2, #{DATA_ID:#x}
    str r2, [r1]                    @ data phase: states 4/6 continue only for IDs 2/10/13/14/26
    movs r1, #4                     @ state 4: "150 sent" -> one data frame -> 250
    b ks_state

@ ksta_handler(r0 = char **reply, r1 = int *len), the -FUPL record (v1.3). No arguments, no call, no wait: plain loads of
@ ksta_tab into the TX payload as big-endian u32, then -KGET's -PRET-shaped reply. r4 = table cursor.
ksta_handler:
    push {{r4, r5, r6, r7, lr}}
    movs r6, r0
    movs r7, r1
    ldr r3, ={HANDLE:#x}
    ldr r0, [r3, #{USB_BUF - HANDLE:#x}]
    adds r0, #8                     @ payload of TX half 0
    adr r4, ksta_tab
    ldr r1, ={KSTA_VERSION:#x}
    b 2f
1:  ldr r1, [r4]
    adds r4, #4
    lsls r2, r1, #4
    lsrs r2, r2, #4                 @ address
    lsrs r1, r1, #28                @ 0 word, 1 halfword, 2 byte
    beq 3f
    cmp r1, #1
    beq 4f
    ldrb r1, [r2]
    b 2f
3:  ldr r1, [r2]
    b 2f
4:  ldrh r1, [r2]
2:  lsrs r3, r1, #24                @ big-endian, byte stores (no alignment assumption on the buffer)
    strb r3, [r0]
    lsrs r3, r1, #16
    strb r3, [r0, #1]
    lsrs r3, r1, #8
    strb r3, [r0, #2]
    strb r1, [r0, #3]
    adds r0, #4
    adr r2, ksta_end
    cmp r4, r2
    blo 1b
    movs r5, #{KSTA_BYTES}
    ldr r3, =ksta_ext
    ldr r3, [r3]
    cmp r3, #0
    beq 5f
    bl ks_call_r3                   @ v1.4 extension (waitdiag): r0 = destination -> r0 = bytes appended
    adds r5, r5, r0
5:  ldr r0, ={FW_150:#x}
    str r0, [r6]
    movs r1, #{len(S150)}
    str r1, [r7]
    ldr r0, ={HANDLE:#x}
    ldr r1, ={0x8001:#x}
    str r1, [r0]                    @ RAM-sourced: the end-of-transfer close of 0x8001 is a no-op
    movs r1, r0
    adds r1, #{FINAL - HANDLE:#x}
    ldr r2, ={FW_250:#x}
    str r2, [r1]
    str r5, [r1, #{TOTAL - FINAL:#x}]
    str r5, [r1, #{CHUNK - FINAL:#x}]
    ldr r1, ={CMD_ID:#x}
    movs r2, #{DATA_ID:#x}
    str r2, [r1]
    movs r1, #4
    b ks_state

@ kpeek_handler(r0 = char **reply, r1 = int *len), the -PWOF record (v1.4): -PEEK <addr_hex>,<len>. a = 1..7 hex
@ digits (the parser's string), b = decimal len (all digits, the parser's atoi). RAM only, read-only.
kpeek_handler:
    push {{r4, r5, r6, r7, lr}}
    movs r6, r0
    movs r7, r1
    ldr r0, ={STR_B:#x}
    movs r2, #0
1:  ldrb r1, [r0, r2]
    cmp r1, #0
    beq 2f
    subs r1, #0x30
    cmp r1, #9
    bhi kp_501
    adds r2, #1
    b 1b
2:  cmp r2, #0
    beq kp_501
    ldr r0, ={STR_A:#x}
    movs r4, #0                     @ addr
    movs r2, #0
3:  ldrb r1, [r0, r2]
    cmp r1, #0
    beq 4f
    subs r1, #0x30
    cmp r1, #9
    bls 6f
    movs r3, #0x20
    orrs r1, r3                     @ (c - '0') | 0x20: 'A'..'F' and 'a'..'f' -> 0x31..0x36
    subs r1, #0x31
    cmp r1, #5
    bhi kp_501
    adds r1, #10
6:  lsls r4, r4, #4
    orrs r4, r1
    adds r2, #1
    b 3b
4:  cmp r2, #0
    beq kp_501
    ldr r1, ={CMD_ID:#x}
    ldr r5, [r1, #{ARG_B - CMD_ID}]  @ len (decimal atoi, < 10^7)
    adr r0, s501p
    subs r1, r5, #1
    cmp r1, #{PEEK_MAX - 1}
    bhi kp_rep                      @ len 0 or > 256
    ldr r2, ={PEEK_LO:#x}
    cmp r4, r2
    blo kp_rep                      @ below 0x1a8000
    adds r1, r4, r5
    ldr r2, ={PEEK_END:#x}
    cmp r1, r2
    bhi kp_rep                      @ end beyond 0x3e5000 (addr < 2^28: no overflow)
    ldr r3, ={HANDLE:#x}
    ldr r0, [r3, #{USB_BUF - HANDLE:#x}]
    adds r0, #8
    movs r1, r4
    movs r2, r5
    ldr r3, ={MEMCPY + 1:#x}
    bl ks_call_r3
    b ks_data                       @ -KGET's -PRET-shaped tail (r5 = len, r6 / r7 = reply / len pointers)
kp_501:
    b ks_501
kp_rep:
    b ks_rep

@ ks_pre (leaf): r6 = r0, r7 = r1; Z set when both argument strings are all decimal digits (1..7 of them, the parser's
@ limit), then r4 = a, r5 = b (the parser's atoi). r0-r3.
ks_pre:
    movs r6, r0
    movs r7, r1
    ldr r0, ={STR_A:#x}
    movs r3, #2
1:  movs r2, #0
2:  ldrb r1, [r0, r2]
    cmp r1, #0
    beq 3f
    subs r1, #0x30
    cmp r1, #9
    bhi 9f                          @ Z clear
    adds r2, #1
    b 2b
3:  subs r2, #1
    bmi 9f                          @ empty: Z clear
    adds r0, #{STR_B - STR_A}
    subs r3, #1
    bne 1b
    ldr r1, ={CMD_ID:#x}
    ldr r4, [r1, #{ARG_A - CMD_ID}]
    ldr r5, [r1, #{ARG_B - CMD_ID}]  @ Z still set from subs r3
9:  bx lr

@ r0 = dst, r1 = NUL-terminated src: copy with the NUL, r0 -> the NUL. r2.
ks_puts:
    ldrb r2, [r1]
    strb r2, [r0]
    cmp r2, #0
    beq 1f
    adds r0, #1
    adds r1, #1
    b ks_puts
1:  bx lr

@ ks_idle (leaf): Z set when stock bytes may change: app state 2, mode 0x6eb1c() in {{0, 4}}, no menu / ADJ / detail
@ screen (0x6ef7c() == 0), no command outstanding (0x6f104() == 0), monitor running, RL1 (half-press) up: merged key
@ scan 0x2b9d84[26] bit 0 clear (1 held, 3 injected tap; 0 up, 4 not yet scanned) and injected 0x2b9dd8[26] == 0. r0, r3.
ks_idle:
    ldr r3, ={APP_STATE:#x}
    ldr r0, [r3]
    cmp r0, #2
    bne 9f
    ldr r3, ={UI_MODE:#x}
    ldrb r0, [r3]
    cmp r0, #0
    beq 1f
    cmp r0, #4
    bne 9f
1:  ldrb r0, [r3, #{UI_OVERLAY - UI_MODE:#x}]
    cmp r0, #0
    bne 9f
    ldrb r0, [r3, #{UI_CMD - UI_MODE:#x}]
    cmp r0, #0
    bne 9f
    ldr r3, ={MONI_RUN:#x}
    ldrb r0, [r3]
    cmp r0, #1
    bne 9f
    ldr r3, ={KEY_SCAN + KEY_RL1:#x}
    ldrb r0, [r3]
    lsls r0, r0, #31
    bne 9f
    adds r3, #{KEY_INJ - KEY_SCAN:#x}
    ldrb r0, [r3]
    cmp r0, #0
9:  bx lr

ks_call_r3:
    bx r3
.ltorg
.p2align 2
ks_cnt:                             @ option count per byte 0x8b5..0x8cf (0 = no row)
    .byte {cnt}
.p2align 2
ks_masks:
{masks}.p2align 2
ks_setters:
{setters}ks_stock:                           @ (u16 offset, value class, setter index) per stock byte, 0
{stock}    .hword 0
.p2align 2
s451b:
    .asciz "{_esc(S451_BUSY)}"
.p2align 2
s451r:
    .asciz "{_esc(S451_RANGE)}"
.p2align 2
s451v:
    .asciz "{_esc(S451_VALUE)}"
.p2align 2
s451g:
    .asciz "{_esc(S451_GET)}"
.p2align 2
s501p:
    .asciz "{_esc(S501_PEEK)}"
.p2align 2
s200:
    .asciz "200 -KSET "
kset_name:
    .asciz "-KSET"
kget_name:
    .asciz "-KGET"
ksta_name:
    .asciz "-KSTA"
kpeek_name:
    .asciz "-PEEK"
.p2align 2
ksta_ext:                           @ v1.4: -KSTA extension routine (waitdiag init sets it), 0 = none
    .word 0
ksta_tab:                           @ address | width code << 28 (0 word, 1 halfword, 2 byte), KSTA_WORDS order
{ksta_tab}ksta_end:
"""


def _rec_checks():
    out = []
    for rec, rid, name in ((REC_SET, ID_SET, "-SDIS"), (REC_GET, ID_GET, "-GDIS")):
        out += [(rec + 4, 4, f"{name} record id word = {rid:#x}"),
                (rec + 8, 8, f"{name} record path / size words = 0"),
                (rec + 0x14, 8, f"{name} record -CCTL / -ATTR words = 0")]
    out += [(REC_GET + 0x1C, 4, "-GDIS record [7] = 0"),
            (0xCD398, 4, "-SDIS / -GDIS handlers 0xcd398 / 0xcd39a: bx lr ; bx lr (dead)"),
            (REC_STA + 4, 0x1C, "-FUPL record: id 0x19, every argument / path / -CCTL / -ATTR / -SDIS word 0"),
            (0xCD39C, 2, "-FUPL handler 0xcd39c: bx lr (dead)"),
            (0xCB644, 6, "verb string -FUPL at 0xcb644"),
            (REC_PEEK + 8, 0x08, "-PWOF record: path / size words 0"),
            (REC_PEEK + 0x14, 0x0C, "-PWOF record: -CCTL / -ATTR / -SDIS words 0"),
            (0xCD396, 2, "-PWOF handler 0xcd396: bx lr (dead)"),
            (0xCB61C, 6, "verb string -PWOF at 0xcb61c")]
    return out


# (addr, length, why): stock bytes this feature depends on, verified at install, never written
CHECKS = _rec_checks() + [
    (0xCB64C - 0x28, 0x10, "verb strings -SDIS / -GDIS at 0xcb624 / 0xcb62c"),
    (0xCC1E4, 0x14, "parser 0xcbd64: a, b strings 0x2df712 / 0x2df71c -> atoi 0xcd8a8 -> 0x1fb3e8 / 0x1fb3ec"),
    (0xCC2DC, 0x0C, "parser literals 0x2df712, 0x1fb3dc, 0x2df71c"),
    (0xCB904, 4, "0xcb834 literal 0x1fb3dc (the parsed ID is at +8 = 0x1fb3e4)"),
    (0xCB8A6, 8, "0xcb834 state 4: `cmp r2,#0xe / cmp r2,#0x1a` lets ID 26 start the data frame"),
    (0xCBA42, 8, "0xcb9cc state 6: `cmp r0,#0xe / cmp r0,#0x1a` lets ID 26 finish with [0x1bd344]"),
    (0xCC304, 0x10, "dispatcher 0xcc304: [0x1bd350] = 0x10000 (chunk limit)"),
    (0xCCCB4, 0x16, "-PRET success tail: reply 150, [0x1bd344] = 250, state 4 (the contract mirrored)"),
    (MEMCPY, 4, "0x157bac memcpy prologue"),
    (STRLEN, 4, "0x158090 strlen prologue"),
    (FW_150, len(S150) + 1, "-PRET's 150 line, reused by -KGET"),
    (FW_250, len(S250) + 1, "-PRET's 250 line, reused by -KGET"),
    (FW_501, len(S501) + 1, "parser reply string \"501 Syntax error in parameters or arguments.\\r\\n\" + NUL"),
    (0xCC1AC, 4, "parser literal 0x2df7bc: the -SDIS field buffer (reply buffer here), read only when [7] = 1"),
    (0xCBFA4, 0x1E, "parser 0xcbd64: `[7] == 1` test and its field loop, the only writer of 0x2df7bc"),
    (0xCC158, 4, "parser literal 0x2df7dc: the verb buffer, 32 B after 0x2df7bc"),
]
STOCK_CHECKS = [
    (0x6DC18, 12, "EV setter 0x6dc18: strb r0, [0x1b050 + 3]"),
    (0x6DFB0, 12, "ISO setter 0x6dfb0: strb r0, [0x1b050 + 9]"),
    (0x6DC7C, 0x20, "WB setter 0x6dc7c: SCENE (0x6eb1c() == 1) -> 0x1bef70, else 0x1b054"),
    (0x6E034, 12, "metering setter 0x6e034: strb r0, [0x1b050]"),
    (STOCK_APPLY, 42, "0x553d4: 0x54d84 snapshot, 0x67c4c post, 0x6f0f8(1)"),
    (0x67C4C, 48, "0x67c4c: message 0x2103 (len 0x2c) to queue [0x1b96cc], non-blocking"),
    (0x6EB1C, 12, "0x6eb1c: ldrb [0x1befa0 + 6] (mode)"),
    (0x6EF7C, 12, "0x6ef7c: ldrb [0x1befc0] (overlay screen)"),
    (0x6F104, 12, "0x6f104: ldrb [0x1befc0 + 2] (command outstanding)"),
    (0x50FC0, 0x16, "0x50fc0: GPIO 0x30 (HKEY_RL1) via 0x2571e (active low) -> strb [0x2b9d90 + 0xe] = scan[26]"),
    (0x510E8, 4, "0x50fc0 literal 0x2b9d90 (scan array 0x2b9d84 + 12)"),
    (0x50EC4, 0x18, "0x50eb0: injected 1 / 3 from 0x2b9dd8 overlaid on the scan array 0x2b9d84"),
    (0x517A8, 6, "sink 0x51740: 0x2b9dd8[code] = action"),
]
APPLY_CHECKS = [
    (0x98814, 2, "0x985a0: monitor-active 0x1f502f := 1"),
    (0x97644, 2, "capture: monitor-active 0x1f502f := 0"),
    (0x8F2DA, 6, "0x8f280: r0 = cp+0x22 (cp 0x23b068) before its bl 0x8ff0e: the mono flag passed to pv_apply"),
]


def make(opts):
    opts = dict(opts or {})
    apply = str(opts.get("apply", "on")).lower()
    if apply not in ("on", "off"):
        raise ValueError(f"ksettings.apply={apply!r}: use on or off")
    on = apply == "on"
    checks = CHECKS + STOCK_CHECKS + (APPLY_CHECKS if on else [])
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options=opts, source=_source([], on),
        late_source=lambda rows: _source(rows, on),
        pointers=[Pointer(REC_SET, SET_NAME_BEFORE, "kset_name", why="-SDIS record name -> \"-KSET\""),
                  Pointer(REC_SET + 0x20, SET_HANDLER_BEFORE, "kset_handler", thumb=True,
                          why="-SDIS record handler (bx lr 0xcd398) -> kset_handler"),
                  Pointer(REC_GET, GET_NAME_BEFORE, "kget_name", why="-GDIS record name -> \"-KGET\""),
                  Pointer(REC_GET + 0x20, GET_HANDLER_BEFORE, "kget_handler", thumb=True,
                          why="-GDIS record handler (bx lr 0xcd39a) -> kget_handler"),
                  Pointer(REC_STA, STA_NAME_BEFORE, "ksta_name", why="-FUPL record name -> \"-KSTA\" (v1.3)"),
                  Pointer(REC_STA + 0x20, STA_HANDLER_BEFORE, "ksta_handler", thumb=True,
                          why="-FUPL record handler (bx lr 0xcd39c) -> ksta_handler (read-only state snapshot)"),
                  Pointer(REC_PEEK, PEEK_NAME_BEFORE, "kpeek_name", why="-PWOF record name -> \"-PEEK\" (v1.4)"),
                  Pointer(REC_PEEK + 0x20, PEEK_HANDLER_BEFORE, "kpeek_handler", thumb=True,
                          why="-PWOF record handler (bx lr 0xcd396) -> kpeek_handler (read-only RAM peek)")],
        patches=[Patch(REC_SET + 0x10, bytes(4), struct.pack(">I", 1), "-SDIS record [4]: parse `a,b` (off, val)"),
                 Patch(REC_SET + 0x1C, struct.pack(">I", 1), bytes(4),
                       "-SDIS record [7]: no stock -SDIS field (it would consume the arguments)"),
                 Patch(REC_GET + 0x10, bytes(4), struct.pack(">I", 1), "-GDIS record [4]: parse `a,b` (off, len)"),
                 Patch(REC_PEEK + 4, bytes(4), struct.pack(">I", ID_PEEK),
                       "-PWOF record id 0 -> 0x25 (the parser refuses id 0; the dispatcher looks handlers up by id)"),
                 Patch(REC_PEEK + 0x10, bytes(4), struct.pack(">I", 1), "-PWOF record [4]: parse `a,b` (addr, len)")],
        exports=["ksta_ext"],
        checks=[Check(a, fw.ram(a, n), w) for a, n, w in checks],
        requires=["bwrecipes", "preview"] if on else [])


# `koma smoke` never attaches USB; the emulator scenario drives the handlers through the stock dispatcher.
