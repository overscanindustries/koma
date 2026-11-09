"""selcolour: selective colour. The picture goes mono except one chosen hue band, which keeps its colour.

Hardware facts it rests on (O-hw 2026-11-06):
the IQ hue stage is dead, so the hue selection is done in software on the finished master UV plane.

Koma 0.2: driven by the recipes' SELECT look (bwrecipes type 2: base NORMAL, COLOUR / RANGE are its p5 / p6). bwrecipes
publishes them per shot in its 'RCP6' store header: +9 COLOUR 1..6 (0 = no SELECT shot), +10 RANGE. No rows of its own
(0.1's SEL COLOUR / SEL RANGE rows at 0x1b8cb/cc collided with the recipe editor's RESET row and are gone).

Hook: 0x117382 `bl 0x1173e8` (r0 = cp) at the tail of CamStillRecGenMasterYUV 0x11703c, reached on every path of the
function that produced a master plane (all five callers pass r1 = 0). By then the master resize 0x5e8f0 has returned
and fixed the geometry, and the date imprint 0x13f0b4 (0x117372) has already written into the same plane; the JPEG
rate control / encode 0x10f984 runs after 0x11703c returns. The handler runs the pass first, then the original.

Plane (D): cp+0x58 = master UV (0x8f40e00 at 8 MP), interleaved 4:2:2 Cb,Cr byte pairs (one pair per 2 pixels) at the
Y stride; cp+0x5c u16 stride, cp+0x5e u16 width (final, set after the resize succeeded), cp+0x60 u16 lines. Byte order
inside a pair is I (Cb first, the JPEG component order); option order=crcb swaps it.

Settings (read at use, out of range = OFF): only while the store header at 0x1b900 reads 'RCP6': 0x1b909 COLOUR 0 OFF
1 RED 2 ORANGE 3 YELLOW 4 GREEN 5 BLUE 6 MAGENTA; 0x1b90a RANGE 0 NARROW 1 WIDE. Also skipped when the develop mode
byte 0x23b08a (cp+0x22) is not 0 (B&W / toning: the plane is already flat).

Algorithm: a 1 KiB class table of 32 x 32 cells, indexed by (first byte >> 3) << 5 | (second byte >> 3) of a UV pair
(8-code cells; 0.1 used a 16 KiB table of 256 x 64 cells), built in Thumb at first use and again only when (COLOUR,
RANGE) changes. A cell is classified at its centre (cb, cr in -124..124): neutral if |cb|+|cr| <= THR, else KEEP if
inside the core sector [a, b] of the band (cross(a,p) >= 0 and cross(p,b) >= 0, integer vectors at 64 scale: multiplies
and compares only), else HALF (chroma halved) if inside the soft sector (core widened by SOFT degrees each side), else
neutral. The pixel pass reads one word = two pairs, two table loads; when both are neutral it stores 0x80808080 (19
Thumb instructions per two pairs with the loop; 12 with the old table); otherwise each pair is kept, halved
((byte + 128) >> 1) or neutralised.

Table memory: one 1 KiB block from the pool at install (allocation failure = feature stays off).
"""
from __future__ import annotations

import inspect
import math
import struct

from .. import firmware as fw
from ..feature import Check, Feature, Hook

NAME = "selcolour"
SUMMARY = "selective colour: mono except one hue band (the SELECT recipe; bytes 0x1b909 colour, 0x1b90a range)"
OPTIONS = {"chroma": "minimum |cb|+|cr| for a pixel to keep colour (default 12)",
           "soft": "soft-edge width in degrees each side of the band, chroma halved there (default 8; 0 = hard edge)",
           "order": "cbcr (default) | crcb: byte order of the interleaved UV pair",
           "force": "0 (default): COLOUR from 0x1b909. 1..6 or a colour name: fixed COLOUR, for a card test without "
                    "the recipes (RANGE from 0x1b90a when the store header is 'RCP6', else NARROW)"}
VERSION = "2.0"

STB, STMAGIC = 0x1B900, b"RCP6"     # bwrecipes store header (bwrecipes.MAGIC); +9 COLOUR, +10 RANGE (bwrecipes.publish)
STMAGIC5 = b"RCP5"                  # Koma 0.2.x header, same +9 / +10; accepted too
COLB, RNGB = STB + 9, STB + 10
MODEB = 0x23B08A                    # cp+0x22: develop mode 0 colour, 1 B&W, 2 toning
COLOURS = ["OFF", "RED", "ORANGE", "YELLOW", "GREEN", "BLUE", "MAGENTA"]
RANGES = ["NARROW", "WIDE"]
ROWS = []                           # the SELECT recipe's COLOUR / RANGE rows replace 0.1's SEL COLOUR / SEL RANGE

SITE, TARGET = 0x117382, 0x1173E8
SDLO, SDHI = 0x08000000, 0x0C000000
LUTN = 0x400                        # 32 x 32 cells, one byte each
CELL = 3                            # cell = 8 codes: index = (b0 >> 3) << 5 | (b1 >> 3)
MAGIC = b"KSELCOL1"
# state layout (offsets from the magic)
O_LUT, O_KEY, O_RUNS, O_RES, O_UV, O_END, O_STRIDE, O_WIDTH, O_LINES, O_APPLY, O_RET = \
    8, 12, 13, 14, 16, 20, 24, 26, 28, 32, 36
STATE_LEN = 40
RES_RAN, RES_OFF, RES_MODE, RES_GEOM, RES_NOLUT = 1, 2, 3, 4, 5

# Hue bands: theta = atan2(cr, cb) in degrees (cb right, cr up). (centre, narrow half-width, wide half-width).
# BT.601 primaries: magenta 51.7, red 108.7, yellow 170.8, green 231.7, cyan 288.7, blue 350.8; orange (1, .5, 0) 139.
BANDS = {1: (105, 17, 27),     # RED      88..122  /  78..132
         2: (138, 11, 19),     # ORANGE  127..149  / 119..157
         3: (165, 14, 24),     # YELLOW  151..179  / 141..189
         4: (228, 28, 42),     # GREEN   200..256  / 186..270
         5: (325, 30, 42),     # BLUE    295..355  / 283..7 (takes cyan when WIDE)
         6: (50, 20, 30)}      # MAGENTA  30..70   /  20..80


def _vec(deg):
    r = math.radians(deg)
    return round(64 * math.cos(r)), round(64 * math.sin(r))


def vectors(soft=8):
    """12 entries (key = 2*(colour-1) + range) of 8 signed bytes: core a, core b, soft a, soft b (x = cb, y = cr)."""
    out = []
    for c in range(1, 7):
        centre, n, w = BANDS[c]
        for half in (n, w):
            lo, hi = centre - half, centre + half
            for d in (lo, hi, lo - soft, hi + soft):
                out.append(_vec(d))
    return out


def _inside(a, b, x, y):
    return a[0] * y - a[1] * x >= 0 and x * b[1] - y * b[0] >= 0


def reference_lut(key, thr=12, soft=8, order="cbcr"):
    """Python model of the Thumb table builder: class 0 neutral, 1 half, 2 keep, for idx = (b0>>3)<<5 | (b1>>3)."""
    v = vectors(soft)[4 * key:4 * key + 4]
    lut = bytearray(LUTN)
    for idx in range(LUTN):
        hi, lo = ((idx >> 5) << 3) + 4 - 128, ((idx & 31) << 3) + 4 - 128
        x, y = (hi, lo) if order == "cbcr" else (lo, hi)
        if abs(x) + abs(y) <= thr:
            continue
        lut[idx] = 2 if _inside(v[0], v[1], x, y) else 1 if _inside(v[2], v[3], x, y) else 0
    return bytes(lut)


def reference_pass(uv: bytes, stride, width, lines, lut):
    """Python model of the pixel pass over a UV plane (bytes from the plane base)."""
    out = bytearray(uv)
    for ln in range(lines):
        for o in range(ln * stride, ln * stride + (width & ~3), 2):
            c = lut[((out[o] >> 3) << 5) | (out[o + 1] >> 3)]
            if c == 0:
                out[o], out[o + 1] = 0x80, 0x80
            elif c == 1:
                out[o], out[o + 1] = (out[o] + 128) >> 1, (out[o + 1] + 128) >> 1
    return bytes(out)


SRC = r"""
.equ STB, @STB@
.equ STMAGIC, @STMAGIC@
.equ STMAGIC5, @STMAGIC5@
.equ MODEB, @MODEB@
.equ THR, @THR@
.equ LUTN, @LUTN@
.equ LUTM1, @LUTM1@
.equ SDLO, @SDLO@
.equ SDHI, @SDHI@

@ ---------------------------------------------------------------- hook 0x117382 (bl 0x1173e8, r0 = cp)
hook:
    push {r0, r1, r2, r3, r4, lr}
    bl apply
    pop {r0, r1, r2, r3}
    ldr r4, =next
    bl call_r4
    pop {r4}
    pop {r3}
    bx r3
call_r4:
    bx r4

@ ---------------------------------------------------------------- apply(r0 = cp): one pass over the master UV plane
apply:
    push {r4, r5, r6, r7, lr}
    mov r4, r8
    mov r5, r9
    mov r6, r10
    mov r7, r11
    push {r4, r5, r6, r7}
    movs r7, r0                 @ cp
    ldr r6, =state
    movs r5, #@RES_OFF@
    ldr r0, =STB                @ COLOUR / RANGE from the recipe store header, only when it is 'RCP6'
    ldr r3, [r0, #0]
    ldr r2, =STMAGIC
    movs r1, #0
    cmp r3, r2
    beq 3f
    ldr r2, =STMAGIC5           @ Koma 0.2.x's 'RCP5' header (same +9 / +10)
    cmp r3, r2
    bne 2f
3:  ldrb r1, [r0, #9]
    ldrb r2, [r0, #10]
    b 1f
2:  movs r2, #0
1:
@FORCE@
    subs r1, #1
    cmp r1, #6
    bhs ap_res                  @ 0 = OFF, 7..255 out of range = OFF
    cmp r2, #2
    bhs ap_res
    lsls r1, r1, #1
    adds r1, r1, r2             @ key 0..11
    movs r5, #@RES_MODE@
    ldr r0, =MODEB
    ldrb r0, [r0, #0]
    cmp r0, #0
    bne ap_res                  @ B&W or toning develop: chroma already flat
    movs r5, #@RES_NOLUT@
    ldr r0, [r6, #@O_LUT@]
    cmp r0, #0
    beq ap_res
@ geometry: uv = [cp+0x58], stride = h[cp+0x5c], width = h[cp+0x5e] & ~3, lines = h[cp+0x60]
    movs r5, #@RES_GEOM@
    ldr r0, [r7, #0x58]
    movs r2, #0x5c
    ldrh r2, [r7, r2]
    movs r3, #0x5e
    ldrh r3, [r7, r3]
    movs r4, #0x60
    ldrh r4, [r7, r4]
    lsrs r3, r3, #2
    lsls r3, r3, #2
    beq ap_res                  @ width < 4
    cmp r3, r2
    bhi ap_res                  @ width > stride
    movs r7, r0
    orrs r7, r2
    lsls r7, r7, #30
    bne ap_res                  @ uv or stride not word aligned
    movs r7, #1
    lsls r7, r7, #12
    cmp r2, r7
    bhi ap_res                  @ stride > 4096
    cmp r4, #0
    beq ap_res
    cmp r4, r7
    bhi ap_res                  @ lines > 4096
    ldr r7, =SDLO
    cmp r0, r7
    blo ap_res
    movs r7, r2
    muls r7, r4                 @ stride * lines (<= 16 M)
    adds r7, r0, r7
    bcs ap_res
    push {r0}
    ldr r0, =SDHI
    cmp r7, r0
    pop {r0}
    bhi ap_res                  @ plane end beyond SDRAM
@ table for this key (rebuilt only when the key changes)
    ldrb r7, [r6, #@O_KEY@]
    cmp r7, r1
    beq ap_have
    push {r0, r2, r3, r4}
    ldr r0, [r6, #@O_LUT@]
    bl build
    pop {r0, r2, r3, r4}
    strb r1, [r6, #@O_KEY@]
ap_have:
    str r0, [r6, #@O_UV@]
    strh r2, [r6, #@O_STRIDE@]
    strh r3, [r6, #@O_WIDTH@]
    strh r4, [r6, #@O_LINES@]
    mov r8, r4                  @ lines left
    mov r9, r2                  @ stride
    mov r10, r3                 @ width bytes (multiple of 4)
    ldr r2, [r6, #@O_LUT@]
    ldr r6, =0x80808080
ln_loop:
    mov r11, r0                 @ line start
    movs r1, r0
    add r1, r10                 @ line end
px:
    ldr r3, [r0, #0]            @ Cb0 Cr0 Cb1 Cr1 (big-endian word)
    lsrs r4, r3, #27            @ (Cb0 >> 3) << 5 | (Cr0 >> 3)
    lsls r4, r4, #5
    lsls r5, r3, #8
    lsrs r5, r5, #27
    adds r4, r4, r5
    ldrb r4, [r2, r4]
    lsls r5, r3, #16            @ (Cb1 >> 3) << 5 | (Cr1 >> 3)
    lsrs r5, r5, #27
    lsls r5, r5, #5
    lsls r7, r3, #24
    lsrs r7, r7, #27
    adds r5, r5, r7
    ldrb r5, [r2, r5]
    adds r7, r4, r5
    bne px_slow
    str r6, [r0, #0]            @ both neutral
px_next:
    adds r0, #4
    cmp r0, r1
    blo px
    mov r0, r11
    add r0, r9
    mov r7, r8
    subs r7, #1
    mov r8, r7
    bne ln_loop
    ldr r6, =state
    str r1, [r6, #@O_END@]      @ end of the last line walked
    ldrb r0, [r6, #@O_RUNS@]
    adds r0, #1
    beq ap_sat
    strb r0, [r6, #@O_RUNS@]
ap_sat:
    movs r5, #@RES_RAN@
ap_res:
    strb r5, [r6, #@O_RES@]
    pop {r4, r5, r6, r7}
    mov r8, r4
    mov r9, r5
    mov r10, r6
    mov r11, r7
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3

px_slow:                        @ r3 = word, r4 / r5 = class of pair 0 / 1 (0 neutral, 1 half, 2 keep)
    lsrs r7, r3, #16
    cmp r4, #1
    blo s0_n
    bne s0_k
    lsrs r4, r7, #8
    adds r4, #128
    lsrs r4, r4, #1
    lsls r7, r7, #24
    lsrs r7, r7, #24
    adds r7, #128
    lsrs r7, r7, #1
    lsls r4, r4, #8
    orrs r7, r4
    b s0_k
s0_n:
    lsrs r7, r6, #16
s0_k:
    lsls r3, r3, #16
    lsrs r3, r3, #16
    cmp r5, #1
    blo s1_n
    bne s1_k
    lsrs r5, r3, #8
    adds r5, #128
    lsrs r5, r5, #1
    lsls r3, r3, #24
    lsrs r3, r3, #24
    adds r3, #128
    lsrs r3, r3, #1
    lsls r5, r5, #8
    orrs r3, r5
    b s1_k
s1_n:
    lsrs r3, r6, #16
s1_k:
    lsls r7, r7, #16
    orrs r7, r3
    str r7, [r0, #0]
    b px_next

@ ---------------------------------------------------------------- build(r0 = table, r1 = key): class table
build:
    push {r1, r4, r5, r6, r7, lr}
    movs r7, r0
    lsls r1, r1, #3
    ldr r0, =vec
    adds r1, r1, r0             @ &vec[key]: core a, core b, soft a, soft b
    ldr r2, =LUTM1
b_loop:
    lsrs @HIREG@, r2, #5
    lsls @HIREG@, @HIREG@, #3
    subs @HIREG@, #124          @ (first byte bin << 3) + 4 - 128
    lsls @LOREG@, r2, #27
    lsrs @LOREG@, @LOREG@, #24
    subs @LOREG@, #124          @ (second byte bin << 3) + 4 - 128
    movs r5, r3                 @ |cb| + |cr|
    bpl b_a1
    negs r5, r3
b_a1:
    movs r6, r4
    bpl b_a2
    negs r6, r4
b_a2:
    adds r5, r5, r6
    cmp r5, #THR
    bls b_zero
    movs r6, r1
    bl insec
    cmp r0, #0
    bne b_keep
    adds r6, r1, #4
    bl insec                    @ r0 = 1 in the soft sector (half), 0 outside (neutral)
    b b_st
b_keep:
    movs r0, #2
    b b_st
b_zero:
    movs r0, #0
b_st:
    strb r0, [r7, r2]
    subs r2, #1
    bpl b_loop
    pop {r1, r4, r5, r6, r7}
    pop {r3}
    bx r3

insec:                          @ r3 = x (cb), r4 = y (cr), r6 = &(ax, ay, bx, by) -> r0 = inside; uses r5
    movs r5, #0
    ldrsb r0, [r6, r5]
    muls r0, r4                 @ ax * y
    movs r5, #1
    ldrsb r5, [r6, r5]
    muls r5, r3                 @ ay * x
    cmp r0, r5
    blt in_no
    movs r5, #3
    ldrsb r0, [r6, r5]
    muls r0, r3                 @ x * by
    movs r5, #2
    ldrsb r5, [r6, r5]
    muls r5, r4                 @ y * bx
    cmp r0, r5
    blt in_no
    movs r0, #1
    bx lr
in_no:
    movs r0, #0
    bx lr

@ ---------------------------------------------------------------- install: one 1 KiB pool block for the table
koma_init:
    push {r4, lr}
    sub sp, #8
    ldr r4, [r0, #8]            @ pool_alloc(pool, &p, size, 0)
    ldr r0, [r0, #0x10]
    mov r1, sp
    movs r3, #0
    str r3, [sp, #0]
    ldr r2, =LUTN
    bl call_r4
    cmp r0, #0
    bne i_out
    ldr r4, [sp, #0]
    lsls r0, r4, #30
    bne i_out                   @ null is aligned too; only a word-aligned non-null block is used
    ldr r0, =state
    str r4, [r0, #@O_LUT@]
i_out:
    add sp, #8
    movs r0, #0
    pop {r4}
    pop {r3}
    bx r3

ret_pad:                        @ smoke / test return trap: never executed on the camera
    nop
    nop

.ltorg
.p2align 2
state:
    .ascii "KSELCOL1"
    .word 0                     @ +8  table (pool block) or 0
    .byte 0xff, 0, 0, 0         @ +12 cached key (0xff none), +13 runs (saturating), +14 last result, +15 pad
    .word 0                     @ +16 last UV base
    .word 0                     @ +20 end of the last line walked
    .hword 0, 0, 0, 0           @ +24 stride, +26 width, +28 lines, +30 pad
    .word apply + 1             @ +32 (relocated) for the smoke / tests
    .word ret_pad               @ +36
vec:
@VEC@
"""


def source(thr=12, soft=8, order="cbcr", force=0):
    v = vectors(soft)
    vec_lines = []
    for k in range(12):
        b = [c for xy in v[4 * k:4 * k + 4] for c in xy]
        vec_lines.append("    .byte " + ", ".join(str(x & 0xFF) for x in b) +
                         f"    @ {COLOURS[k // 2 + 1]} {RANGES[k % 2]}")
    hi, lo = ("r3", "r4") if order == "cbcr" else ("r4", "r3")
    rep = dict(STB=hex(STB), STMAGIC="0x" + STMAGIC.hex(), STMAGIC5="0x" + STMAGIC5.hex(), MODEB=hex(MODEB), THR=str(thr), LUTN=hex(LUTN), LUTM1=hex(LUTN - 1), SDLO=hex(SDLO), SDHI=hex(SDHI),
               RES_OFF=str(RES_OFF), RES_MODE=str(RES_MODE), RES_NOLUT=str(RES_NOLUT), RES_GEOM=str(RES_GEOM),
               RES_RAN=str(RES_RAN), O_LUT=str(O_LUT), O_KEY=str(O_KEY), O_RUNS=str(O_RUNS), O_RES=str(O_RES),
               O_UV=str(O_UV), O_END=str(O_END), O_STRIDE=str(O_STRIDE), O_WIDTH=str(O_WIDTH), O_LINES=str(O_LINES),
               HIREG=hi, LOREG=lo, VEC="\n".join(vec_lines),
               FORCE=f"    movs r1, #{force}             @ force={COLOURS[force]}: build option" if force else "")
    s = SRC
    for k, val in rep.items():
        s = s.replace(f"@{k}@", val)
    return s


# Bytes the pass depends on (2.40, all D from the disassembly; verified against the payload in make()).
CHECKS = [
    (0x11706C, "1de51de636593539", "0x11703c: r5 = cp+0x40, r6 = cp+0x60 (geometry fields below are [r5+0x1c/0x1e], [r6])"),
    (0x1171F6, "6d60900a6da0900b8ba8", "0x11703c: resize job args: Y = [cp+0x54], UV = [cp+0x58], stride = h[cp+0x5c]"),
    (0x117206, "8be8901188309012", "... width = h[cp+0x5e], lines = h[cp+0x60]"),
    (0x117270, "a805f747fb3d2800d00d", "0x11703c: master resize 0x5e8f0; non-zero = error return (skips the hook)"),
    (0x117358, "6d6191016da191028ba98199", "date imprint 0x13f0b4 args: Y [cp+0x54], UV [cp+0x58], stride h[cp+0x5c]"),
    (0x117380, "1c20", "0x117380: r0 = cp, then the hooked bl 0x1173e8 at 0x117382"),
    (0x117386, "481778002801d102", "after the hooked site: [0x1bed50] == 1 -> second bl 0x1173e8 (not hooked)"),
]


def _checks():
    out = []
    for a, h, w in CHECKS:
        b = fw.ram(a, len(h) // 2)
        if b.hex() != h:
            raise AssertionError(f"selcolour check {a:#x}: payload {b.hex()} != {h}")
        out.append(Check(a, b, w))
    return out


def make(opts):
    thr = int(opts.get("chroma", 12))
    soft = int(opts.get("soft", 8))
    order = str(opts.get("order", "cbcr")).lower()
    force = str(opts.get("force", 0)).upper()
    force = COLOURS.index(force) if force in COLOURS else int(force) if force.isdigit() else -1
    if not 0 <= thr <= 200 or not 0 <= soft <= 20 or order not in ("cbcr", "crcb") or not 0 <= force <= 6:
        raise ValueError("selcolour: chroma 0..200, soft 0..20, order cbcr|crcb, force 0..6 or a colour name")
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options=dict(chroma=thr, soft=soft, order=order, force=force),
        source=source(thr, soft, order, force), init="koma_init", checks=_checks(),
        # smoke_reachable=False: `koma smoke` stops right after the develop step (0x9643a), before the master YUV
        # step; smoke_checks runs the installed pass over a full 8 MP plane instead.
        hooks=[Hook(SITE, TARGET, "hook", "next", "tail of CamStillRecGenMasterYUV 0x11703c: master UV final, "
                    "before the encode", smoke_reachable=False)],
        smoke=smoke_checks)


# ------------------------------------------------------------------ smoke
def state_addr(read):
    """Resident state block: Koma publish slot -> manifest (resident start), then the magic."""
    if read(fw.PUBLISH, 4) != b"KOMA":
        return None
    base = struct.unpack(">I", read(fw.PUBLISH + 4, 4))[0]
    blob = read(base, 0x10000)          # the whole resident segment (< the loader's 64 KB body limit)
    i = blob.find(MAGIC)
    return None if i < 0 else base + i


def smoke_setup():
    """Nothing: the shot belongs to the recipes' smoke look; smoke_checks sets SELECT RED / NARROW for its own run."""
    return {}


def rgb_uv(r, g, b):
    cb = round(128 - 0.168736 * r - 0.331264 * g + 0.5 * b)
    cr = round(128 + 0.5 * r - 0.418688 * g - 0.081312 * b)
    return max(0, min(255, cb)), max(0, min(255, cr))


# test patches: name -> RGB (hue angle in the comment)
PATCHES = [("red", (200, 30, 30)),          # 108.6
           ("orange", (230, 120, 20)),      # 137.3
           ("yellow", (220, 210, 30)),      # 167.9
           ("green", (30, 160, 40)),        # 235.4
           ("blue", (30, 60, 200)),         # 340.6
           ("magenta", (200, 40, 180)),     # 58
           ("warmgrey", (136, 128, 122)),   # chroma below the threshold
           ("grey", (128, 128, 128))]


def synthetic_plane(stride, width, lines, order="cbcr"):
    """UV plane: len(PATCHES) vertical bands across the width, padding bytes past the width = 0x5a (must survive)."""
    line = bytearray([0x5A]) * stride
    band = (width // 4 // len(PATCHES)) * 4
    for i, (_, rgb) in enumerate(PATCHES):
        cb, cr = rgb_uv(*rgb)
        pair = bytes([cb, cr]) if order == "cbcr" else bytes([cr, cb])
        line[i * band:(i + 1) * band] = pair * (band // 2)
    tail = len(PATCHES) * band
    line[tail:width] = bytes([0x80, 0x80]) * ((width - tail) // 2)
    return bytes(line) * lines, band


def run_apply(uc, apply_addr, ret_addr, cp, sp, count=0):
    """Call apply(cp) in Thumb state on a Unicorn instance; returns when it reaches ret_addr."""
    from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_SP
    uc.reg_write(UC_ARM_REG_R0, cp)
    uc.reg_write(UC_ARM_REG_SP, sp)
    uc.reg_write(UC_ARM_REG_LR, ret_addr | 1)
    uc.reg_write(UC_ARM_REG_CPSR, (uc.reg_read(UC_ARM_REG_CPSR) & ~0xFF) | 0xD3 | 0x20)
    uc.emu_start(apply_addr | 1, ret_addr, count=count)


UV8, STRIDE8, WIDTH8, LINES8 = 0x8F40E00, 3328, 3264, 2448


def smoke_checks(obs):
    """The smoke stops at the develop step (before the master YUV step), so run the installed, relocated pass here on
    the stopped machine: fake cp with the 8 MP geometry, synthetic full UV plane at 0x8f40e00, COLOUR RED (poked)."""
    from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_SP
    m = obs._m
    fr = inspect.currentframe().f_back
    while fr is not None and "ev" not in fr.f_locals:
        fr = fr.f_back
    ev = fr.f_locals["ev"] if fr is not None else {}
    out = [("smoke develop step completed (0x9643a reached)", ev.get("develop_done") is not None,
            f"develop={ev.get('develop')} done={ev.get('develop_done')}")]
    st = state_addr(obs.ram)
    out.append(("state block found in the resident block", st is not None, f"state={st and hex(st)}"))
    if st is None:
        return out
    s = obs.ram(st, STATE_LEN)
    lut, apply_addr, ret_addr = (struct.unpack(">I", s[o:o + 4])[0] for o in (O_LUT, O_APPLY, O_RET))
    out.append(("1 KiB table allocated from the pool at install", lut != 0 and lut & 3 == 0, f"table={lut:#x}"))
    if not lut:
        return out
    uc = m.uc
    cp, sp = 0x0BF10000, 0x0BF0F000
    plane, band = synthetic_plane(STRIDE8, WIDTH8, LINES8)
    size = STRIDE8 * LINES8
    regions = [(UV8, size), (cp, 0x100), (sp - 0x1000, 0x1000), (MODEB, 1), (lut, LUTN), (STB, 11)]
    saved = [(a, bytes(uc.mem_read(a, n))) for a, n in regions]
    regs = {r: uc.reg_read(r) for r in range(UC_ARM_REG_R0, UC_ARM_REG_R0 + 13)}
    regs.update({r: uc.reg_read(r) for r in (UC_ARM_REG_SP, UC_ARM_REG_CPSR, UC_ARM_REG_PC)})
    vt, nd, ws = m.vt, m.next_due, m.want_stop
    try:
        uc.mem_write(UV8, plane)
        uc.mem_write(cp, bytes(0x100))
        uc.mem_write(cp + 0x58, struct.pack(">I", UV8))
        uc.mem_write(cp + 0x5C, struct.pack(">HHH", STRIDE8, WIDTH8, LINES8))
        uc.mem_write(MODEB, b"\x00")
        uc.mem_write(STB, STMAGIC)                   # SELECT RED / NARROW, as bwrecipes.publish would leave it
        uc.mem_write(COLB, b"\x01\x00")
        m.next_due, m.want_stop = 1 << 62, False
        run_apply(uc, apply_addr, ret_addr, cp, sp)
        insns = m.vt - vt                            # grdemu's block hook counts Thumb instructions in vt
        after = bytes(uc.mem_read(UV8, size))
        s2 = bytes(uc.mem_read(st, STATE_LEN))
        lutb = bytes(uc.mem_read(lut, LUTN))
    finally:
        for a, b in saved:
            uc.mem_write(a, b)
        for r, v in regs.items():
            uc.reg_write(r, v)
        m.vt, m.next_due, m.want_stop = vt, nd, ws
    res, end = s2[O_RES], struct.unpack(">I", s2[O_END:O_END + 4])[0]
    full_end = UV8 + (LINES8 - 1) * STRIDE8 + WIDTH8
    out.append(("pass ran (result 1)", res == RES_RAN, f"result={res}"))
    out.append(("walked the full 3264x2448 plane (end of last line)", end == full_end,
                f"end={end:#x} expected={full_end:#x}"))
    out.append(("table == reference (RED NARROW)", lutb == reference_lut(0), ""))
    exp = reference_pass(plane, STRIDE8, WIDTH8, LINES8, reference_lut(0))
    out.append(("plane == reference pass", after == exp, ""))
    first = after[:STRIDE8]
    kept = [n for i, (n, rgb) in enumerate(PATCHES) if first[i * band:i * band + 2] != b"\x80\x80"]
    out.append(("only the red patch kept colour", kept == ["red"], f"kept={kept}"))
    out.append(("stride padding untouched", all(after[ln * STRIDE8 + WIDTH8:(ln + 1) * STRIDE8] ==
                                                 b"\x5a" * (STRIDE8 - WIDTH8) for ln in (0, LINES8 // 2, LINES8 - 1)), ""))
    pairs = WIDTH8 * LINES8 // 2
    out.append((f"cost: {insns:,} Thumb instructions for {pairs:,} UV pairs ({insns / pairs:.2f}/pair)", True, ""))
    obs.selcolour = dict(instructions=insns, pairs=pairs, end=hex(end), result=res)
    return out
