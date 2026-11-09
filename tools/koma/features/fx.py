"""fx: film GRAIN and VIGNETTE, one software pass over the finished master Y plane (Koma 0.3).

Per-recipe sub-settings (bwrecipes FX table, EDIT RECIPE rows GRAIN 0x1b8cc / VIGNETTE 0x1b8cd, OFF/1/2/3 each).
bwrecipes publishes the values of the recipe that applies to the next shot in its 'RCP6' store header: +11 GRAIN,
+12 VIGNETTE (0 when no recipe applies). Both 0 -> the hook returns after about 20 instructions.

Hook: 0x117382 `bl 0x1173e8` (r0 = cp), the site selcolour uses (the two chain in build order; selcolour works on UV,
fx on Y). The master resize 0x5e8f0 has fixed the geometry and the date imprint is written; the JPEG encode 0x10f984
runs after 0x11703c returns. Plane (D, CHECKS): cp+0x54 = master Y
(0x8605f00 at 8 MP), 8-bit planar; cp+0x5c u16 stride, cp+0x5e u16 width, cp+0x60 u16 lines.

GRAIN (no table, no clamp): per word of 4 pixels, mask m = 7 in each byte whose value is in 16..239 (top nibble not
0000 / 1111: bits 5..7 of v ^ (v << 1) not all 0), else 0; P = m & random bits (xorshift32, one step per two words, the
second word takes the bits rotated by 4: disjoint); noise = (2 P - m) << s, uniform odd values in -7..7 (s = 0) or
-14..14 (s = 1); the word gets + noise as one 32-bit add (every byte stays in 0..255, so the integer sum is exact).
GRAIN 1: +-7 (std 4.6 levels); 2: +-14 (std 9.2); 3: +-14 and the PRNG sequence repeats on each odd line (grain 1 x 2
pixels, coarser). Pure black (< 16) and white (> 239) get none; mean 0. Seed: shot counter x golden ratio ^ two plane
words (never 0).

VIGNETTE: f = (1 - k tx^2)(1 - k ty^2), tx / ty = offset from the centre / half diagonal: the cos^4-like parabola per
axis, separable, so no sqrt, no per-pixel distance and no table. k = 0.255 / 0.484 / 0.660 puts a 4:3 frame's corners
at 0.76 / 0.57 / 0.44 (-0.4 / -0.8 / -1.2 EV) for 1 / 2 / 3. The row factor is computed once per line; the column
factor walks a 16-pixel step as an exact integer second-difference recurrence (F += D, D += DD, with the row factor
folded into F / D / DD at the line start), so per 16 pixels it costs 3 instructions and no memory. One shift-subtract
division per shot (k * 2^46 / (width^2 + lines^2)); planes with width^2 + lines^2 < 2^22 (smaller than about 1660 x
1250) get no vignette. Pixels: two SWAR multiplies per word (even / odd bytes in 16-bit lanes), g in Q8 (256 = 1.0:
the centre is exactly unchanged).

One fused loop in ARM state (the Thumb hook bx's into it): per 16 pixels 75 instructions with both effects compiled in
(about 37.5 M for 8 MP, plus about 0.1 M of per-line work). Resident: code + 32 B state, no pool block. Options grain=off / vignette=off drop that half of the loop at build time; they never touch
the recipe store (its layout belongs to bwrecipes).
"""
from __future__ import annotations

import inspect
import struct

from .. import firmware as fw
from ..feature import Check, Feature, Hook

NAME = "fx"
SUMMARY = "film GRAIN and VIGNETTE on the master Y plane (recipe FX table; store header +11 / +12)"
OPTIONS = {"grain": "on (default) | off: build without the grain half of the pass (the GRAIN byte is then ignored)",
           "vignette": "on (default) | off: build without the vignette half of the pass"}
VERSION = "1.0"
ROWS = []                           # GRAIN / VIGNETTE rows belong to bwrecipes (per recipe)

STB, STMAGIC = 0x1B900, b"RCP6"     # bwrecipes store header (bwrecipes.MAGIC); +11 GRAIN, +12 VIGNETTE
GRB, VIGB = STB + 11, STB + 12
SITE, TARGET = 0x117382, 0x1173E8
SDLO, SDHI = 0x08000000, 0x0C000000
MAGIC = b"KFXSTAT1"
BLK = 16                            # pixels per column-factor step (four words per loop iteration)
MINDIAG = 1 << 22                   # width^2 + lines^2 below this: no vignette (keeps k * 2^46 / D in 32 bits)
_PAD, O_RES, O_BUILD, O_COUNT, O_SEED, O_END, O_MAIN, O_RET = 8, 9, 10, 12, 16, 20, 24, 28
STATE_LEN = 32
RES_RAN, RES_OFF, RES_GEOM = 1, 2, 4
KQ8 = [0, 65, 124, 169]             # k x 256 (4:3 corners 0.76 / 0.57 / 0.44)
M00FF, K07 = 0x00FF00FF, 0x07070707
# instructions per 16-pixel iteration of the fused loop, by (grain, vignette) compiled in (checked by the tests)
LOOP_COST = {(True, True): 75, (True, False): 44, (False, True): 37}


def nblocks(stride, width):
    return width >> 4


def vig_ik(width, lines, level):
    d = width * width + lines * lines
    return 0 if not level or d < MINDIAG else ((KQ8[level] << 46) // d) & 0xFFFFFFFF


def vig_tables(width, lines, level, nblk):
    """(g per 16-pixel block for each line, row factors) as the kernel computes them (ik 0 -> all 256)."""
    ik = vig_ik(width, lines, level)

    def e(u):                       # k (u / diagonal)^2 in Q22 (u = doubled pixel offset)
        return ((u * u & 0xFFFFFFFF) * ik) >> 32
    u0 = 16 - width
    e0, e1, e2 = e(u0), e(u0 + 32), e(u0 + 64)
    f0, d0, dd = (1 << 22) - e0, e0 - e1, 2 * e1 - e0 - e2
    rows = [((1 << 22) + 0x2000 - e(2 * y + 1 - lines)) >> 14 for y in range(lines)]
    gs = []
    for rf in rows:
        f, d, dd_ = f0 * rf, d0 * rf, dd * rf
        line = []
        for _ in range(nblk):
            line.append((f & 0xFFFFFFFF) >> 22)
            f, d = f + d, d + dd_
        gs.append(line)
    return gs, rows


def xorshift(x):
    x ^= (x << 13) & 0xFFFFFFFF
    x ^= x >> 17
    x ^= (x << 5) & 0xFFFFFFFF
    return x


def _ror(x, n):
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


def seeds(seed, nblk, lines, pair):
    """PRNG value at the start of each 16-pixel block (two steps per block; GRAIN 3: a line with an odd number of
    lines left, this one included, repeats the sequence saved at the line before)."""
    out, x, saved = [], seed, seed
    for ln in range(lines):
        if pair:
            if (lines - ln) & 1:
                x = saved
            else:
                saved = x
        row = []
        for _ in range(nblk):
            row.append(x)
            x = xorshift(xorshift(x))
        out.append(row)
    return out


def _grain_word(w, r, s):
    t = w ^ ((w << 1) & 0xFFFFFFFF)
    t = (t >> 5) & K07
    t = (t + K07) & ~K07 & 0xFFFFFFFF
    m = t - (t >> 3)
    p = m & r
    d = ((p << 1) - m) & 0xFFFFFFFF
    return (w + (d << s)) & 0xFFFFFFFF


def _vig_word(w, g):
    a = ((w & M00FF) * g) & 0xFFFFFFFF
    b = (((w >> 8) & M00FF) * g) & 0xFFFFFFFF
    return ((a >> 8) & M00FF) | (b & ~M00FF & 0xFFFFFFFF)


def reference_pass(plane, stride, width, lines, grain, vig, seed, only=None, build=(True, True)):
    """Python model of the kernel over a Y plane (bytes from the base). build = (grain compiled, vignette compiled).
    `only`: set of lines to compute (the PRNG still walks every line)."""
    out = bytearray(plane)
    grain = grain if build[0] else 0
    vig = vig if build[1] else 0
    if not grain and not vig:
        return bytes(out)
    nblk = nblocks(stride, width)
    gs, _ = vig_tables(width, lines, vig, nblk)
    s = 1 if grain >= 2 else 0
    xs = seeds(seed, nblk, lines, grain == 3)
    for ln in range(lines):
        if only is not None and ln not in only:
            continue
        p = ln * stride
        for j in range(nblk):
            g = gs[ln][j]
            x = xs[ln][j]
            for k in range(4):
                w = int.from_bytes(out[p:p + 4], "big")
                if build[1]:
                    w = _vig_word(w, g)
                if build[0] and grain:
                    xx = x if k < 2 else xorshift(x)
                    w = _grain_word(w, xx if k % 2 == 0 else _ror(xx, 4), s)
                out[p:p + 4] = w.to_bytes(4, "big")
                p += 4
    return bytes(out)


def reference_np(y, grain, vig, seed):
    """Bit-exact numpy model for a whole (lines, width) uint8 Y array, width a multiple of 16 (renders, tests)."""
    import numpy as np
    lines, width = y.shape
    assert width % 16 == 0
    nblk = width // 16
    if not grain and not vig:
        return y.copy()
    v = y.astype(np.int64)
    gs, _ = vig_tables(width, lines, vig, nblk)
    v = (v * np.repeat(np.array(gs, np.int64), 16, axis=1)) >> 8
    if grain:
        xs = np.array(seeds(seed, nblk, lines, grain == 3), np.uint64)
        x2 = xs ^ ((xs << 13) & 0xFFFFFFFF)
        x2 ^= x2 >> 17
        x2 ^= (x2 << 5) & 0xFFFFFFFF
        r = np.zeros((lines, width), np.int64)
        for k in range(4):
            src = xs if k < 2 else x2
            if k % 2:
                src = ((src >> 4) | (src << 28)) & 0xFFFFFFFF
            for bi in range(4):
                r[:, k * 4 + bi::16] = ((src >> np.uint64(24 - 8 * bi)) & np.uint64(7)).astype(np.int64)
        m = np.where((v >> 4 != 0) & (v >> 4 != 15), 7, 0)
        v = v + ((2 * (r & m) - m) << (1 if grain >= 2 else 0))
    return v.astype(np.uint8)


def _vig_ops(w):
    return [f"    and r4, r11, {w}", f"    and r5, r11, {w}, lsr #8", "    mul r4, r8, r4", "    mul r5, r8, r5",
            "    and r4, r11, r4, lsr #8", "    bic r5, r5, r11", f"    orr {w}, r4, r5"]


def _grain_ops(w, rnd):
    return [f"    eor r4, {w}, {w}, lsl #1", "    and r4, r12, r4, lsr #5", "    add r4, r4, r12", "    bic r4, r4, r12",
            "    sub r4, r4, r4, lsr #3", f"    and r5, r4, {rnd}", "    rsb r5, r4, r5, lsl #1",
            f"    add {w}, {w}, r5, lsl lr"]


XORSHIFT = ["    eor r7, r7, r7, lsl #13", "    eor r7, r7, r7, lsr #17", "    eor r7, r7, r7, lsl #5"]


def loop_src(grain, vig):
    ops = ["f_blk:"]
    if vig:
        ops += ["    mov r8, r9, lsr #22", "    add r9, r9, r10", "    add r10, r10, r6"]
    for _ in range(2):
        ops += ["    ldmia r0, {r2, r3}"]
        for w, rnd in (("r2", "r7"), ("r3", "r7, ror #4")):
            if vig:
                ops += _vig_ops(w)
            if grain:
                ops += _grain_ops(w, rnd)
        ops += ["    stmia r0!, {r2, r3}"]
        if grain:
            ops += XORSHIFT
    ops += ["    subs r1, r1, #1", "    bne f_blk"]
    return ops


SRC = r"""
@ ---------------------------------------------------------------- hook 0x117382 (bl 0x1173e8, r0 = cp)
hook:
    push {r0, r1, r2, r3, r4, lr}
    ldr r4, =fxmain             @ ARM state (no Thumb bit): bx switches
    bl call_r4
    pop {r0, r1, r2, r3}
    ldr r4, =next
    bl call_r4
    pop {r4}
    pop {r3}
    bx r3
call_r4:
    bx r4
ret_pad:                        @ test / smoke return trap: never executed on the camera
    nop
    nop
.ltorg

.p2align 2
.arm
@ fxmain(r0 = cp). Frame: +0 state, +4 line pointer, +8 stride, +12 lines left, +16 blocks, +20 line index,
@ +24 GRAIN 3 flag, +28 PRNG (saved for GRAIN 3), +32 ik, +36 dy2, +40 F0, +44 D0, +48 DD, +52 grain shift
fxmain:
    stmfd sp!, {r4-r11, lr}
    sub sp, sp, #56
    ldr r11, =state
    str r11, [sp, #0]
    mov r10, #@RES_OFF@
    ldr r1, =@STB@
    ldr r2, [r1]
    ldr r3, =@STMAGIC@
    mov r4, #0
    mov r5, #0
    cmp r2, r3
    ldrbeq r4, [r1, #11]        @ GRAIN
    ldrbeq r5, [r1, #12]        @ VIGNETTE
    cmp r4, #3
    movhi r4, #0                @ out of range = OFF
    cmp r5, #3
    movhi r5, #0
@NOGRAIN@
@NOVIG@
    orrs r1, r4, r5
    beq f_out                   @ both OFF
    mov r10, #@RES_GEOM@        @ geometry: y [cp+0x54], stride h[cp+0x5c], width h[cp+0x5e], lines h[cp+0x60]
    ldr r6, [r0, #0x54]
    ldrh r7, [r0, #0x5c]
    ldrh r8, [r0, #0x5e]
    ldrh r9, [r0, #0x60]
    movs r1, r8, lsr #4         @ whole 16-pixel blocks (every master width is a multiple of 16)
    beq f_out
    cmp r8, r7
    bhi f_out                   @ width > stride
    orr r2, r6, r7
    tst r2, #3
    bne f_out                   @ y or stride not word aligned
    cmp r7, #4096
    bhi f_out
    sub r2, r9, #1
    cmp r2, #4096
    bhs f_out                   @ lines 0 or > 4096
    cmp r6, #@SDLO@
    blo f_out
    mla r2, r7, r9, r6
    cmp r2, #@SDHI@
    bhi f_out                   @ plane end beyond SDRAM
    str r6, [sp, #4]
    str r7, [sp, #8]
    str r9, [sp, #12]
    str r1, [sp, #16]
    ldr r0, [r11, #@O_COUNT@]   @ seed: shot counter x golden ratio ^ the plane's first word, never 0
    add r0, r0, #1
    str r0, [r11, #@O_COUNT@]
    ldr r1, =0x9e3779b9
    mul r2, r0, r1
    ldr r1, [r6]
    eors r2, r2, r1
    moveq r2, #1
    str r2, [r11, #@O_SEED@]
    str r2, [sp, #28]
    mov r0, #0                  @ grain: shift s (0 for 1, 1 for 2 and 3), line repeat for 3, mask constant
    mov r1, #0
    mov r12, #0
    cmp r4, #0
    ldrne r12, =@K07@
    cmp r4, #2
    movhs r0, #1
    cmp r4, #3
    moveq r1, #1
    str r0, [sp, #52]
    str r1, [sp, #24]
    mov r2, #0                  @ vignette: ik = k * 2^46 / D (0 = flat)
    cmp r5, #0
    beq v_done
    mul r0, r8, r8
    mla r0, r9, r9, r0          @ D = width^2 + lines^2
    cmp r0, #@MINDIAG@
    blo v_done
    cmp r5, #2
    movlo r1, #@KQ1@
    moveq r1, #@KQ2@
    movhi r1, #@KQ3@
    mov r3, #46
v_div:
    mov r1, r1, lsl #1
    mov r2, r2, lsl #1
    cmp r1, r0
    subhs r1, r1, r0
    orrhs r2, r2, #1
    subs r3, r3, #1
    bne v_div
v_done:
    str r2, [sp, #32]
    rsb r0, r9, #1
    str r0, [sp, #36]           @ dy2 of line 0
    rsb r5, r8, #16             @ u0 = doubled offset of block 0's centre; E(u) = (u^2 ik) >> 32 (Q22)
    mul r0, r5, r5
    umull r1, r3, r2, r0        @ r3 = E(u0)
    add r5, r5, #32
    mul r0, r5, r5
    umull r1, r4, r2, r0        @ r4 = E(u0 + 32)
    add r5, r5, #32
    mul r0, r5, r5
    umull r1, r6, r2, r0        @ r6 = E(u0 + 64)
    rsb r0, r3, #0x400000
    str r0, [sp, #40]           @ F0 = 2^22 - E0
    sub r0, r3, r4
    str r0, [sp, #44]           @ D0 = E0 - E1
    rsb r0, r3, r4, lsl #1
    sub r0, r0, r6
    str r0, [sp, #48]           @ DD = 2 E1 - E0 - E2
    ldr r7, [sp, #28]           @ x
    mov r11, #0xff
    orr r11, r11, #0xff0000     @ 0x00ff00ff
f_line:
    ldr r0, [sp, #24]           @ GRAIN 3: a line with an odd count of lines left repeats the sequence saved
    cmp r0, #0                  @ at the previous (even) one
    ldrne r0, [sp, #12]
    tstne r0, #1
    ldrne r7, [sp, #28]
    beq 1f
    b 2f
1:  str r7, [sp, #28]
2:  ldr r0, [sp, #36]           @ row factor rf = 256 - round(E(dy2) / 2^14); F, D, DD scaled by it
    mul r1, r0, r0
    add r0, r0, #2
    str r0, [sp, #36]
    ldr r0, [sp, #32]
    umull r2, r3, r0, r1
    rsb r3, r3, #0x400000
    add r3, r3, #0x2000
    mov r3, r3, lsr #14         @ = round(256 - E / 2^14), 256 when ik = 0
    ldr r0, [sp, #40]
    mul r9, r0, r3
    ldr r0, [sp, #44]
    mul r10, r0, r3
    ldr r0, [sp, #48]
    mul r6, r0, r3
    ldr lr, [sp, #52]
    ldr r0, [sp, #4]
    ldr r1, [sp, #16]
@LOOP@
    ldr r1, [sp, #4]
    ldr r2, [sp, #8]
    add r1, r1, r2
    str r1, [sp, #4]
    ldr r1, [sp, #12]
    subs r1, r1, #1
    str r1, [sp, #12]
    bne f_line
    ldr r11, [sp, #0]
    str r0, [r11, #@O_END@]     @ end of the last line walked
    mov r10, #@RES_RAN@
f_out:
    ldr r11, [sp, #0]
    strb r10, [r11, #@O_RES@]
    add sp, sp, #56
    ldmfd sp!, {r4-r11, lr}
    bx lr

.ltorg
.p2align 2
.thumb
state:
    .ascii "KFXSTAT1"
    .byte 0, 0, @BUILD@, 0       @ +8 pad, +9 result, +10 build (1 grain, 2 vignette compiled in)
    .word 0, 0, 0               @ +12 shot count, +16 seed, +20 end of the last line walked
    .word fxmain                @ +24 (relocated, ARM) for the smoke / tests
    .word ret_pad               @ +28
"""


def source(grain=True, vig=True):
    rep = dict(STB=hex(STB), STMAGIC="0x" + STMAGIC.hex(), SDLO=hex(SDLO), SDHI=hex(SDHI),
               MINDIAG=hex(MINDIAG), K07=hex(K07), RES_OFF=str(RES_OFF), RES_GEOM=str(RES_GEOM), RES_RAN=str(RES_RAN),
               O_RES=str(O_RES), O_COUNT=str(O_COUNT), O_SEED=str(O_SEED), O_END=str(O_END),
               NOGRAIN="" if grain else "    mov r4, #0                  @ grain=off build",
               NOVIG="" if vig else "    mov r5, #0                  @ vignette=off build",
               LOOP="\n".join(loop_src(grain, vig)), KQ1=str(KQ8[1]), KQ2=str(KQ8[2]), KQ3=str(KQ8[3]),
               BUILD=str(int(grain) | int(vig) << 1))
    s = SRC
    for k, val in rep.items():
        s = s.replace(f"@{k}@", val)
    return s


# Bytes the pass depends on (2.40, D; the same as selcolour's, verified against the payload in make()).
CHECKS = [
    (0x11706C, "1de51de636593539", "0x11703c: r5 = cp+0x40, r6 = cp+0x60 (geometry fields below are [r5+0x1c/0x1e], [r6])"),
    (0x1171F6, "6d60900a6da0900b8ba8", "0x11703c: resize job args: Y = [cp+0x54], UV = [cp+0x58], stride = h[cp+0x5c]"),
    (0x117206, "8be8901188309012", "... width = h[cp+0x5e], lines = h[cp+0x60]"),
    (0x117270, "a805f747fb3d2800d00d", "0x11703c: master resize 0x5e8f0; non-zero = error return (skips the hook)"),
    (0x117358, "6d6191016da191028ba98199", "date imprint 0x13f0b4 args: Y [cp+0x54], UV [cp+0x58], stride h[cp+0x5c]"),
    (0x117380, "1c20", "0x117380: r0 = cp, then the hooked bl 0x1173e8 at 0x117382"),
]


def _checks():
    out = []
    for a, h, w in CHECKS:
        b = fw.ram(a, len(h) // 2)
        if b.hex() != h:
            raise AssertionError(f"fx check {a:#x}: payload {b.hex()} != {h}")
        out.append(Check(a, b, w))
    return out


def _onoff(opts, name):
    v = str(opts.get(name, "on")).lower()
    if v not in ("on", "off"):
        raise ValueError(f"fx.{name}={v!r}: use on or off")
    return v == "on"


def make(opts):
    opts = dict(opts or {})
    g, v = _onoff(opts, "grain"), _onoff(opts, "vignette")
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY,
        options=dict(grain="on" if g else "off", vignette="on" if v else "off"),
        source=source(g, v), checks=_checks(),
        # smoke_reachable=False: `koma smoke` stops right after the develop step, before the master YUV step;
        # smoke_checks runs the installed pass over a full 8 MP plane instead.
        hooks=[Hook(SITE, TARGET, "hook", "next", "tail of CamStillRecGenMasterYUV 0x11703c: master Y final, before "
                    "the encode", smoke_reachable=False)],
        smoke=smoke_checks)


# ------------------------------------------------------------------ smoke
def state_addr(read):
    """Resident state block: Koma publish slot -> manifest (resident start), then the magic."""
    if read(fw.PUBLISH, 4) != b"KOMA":
        return None
    base = struct.unpack(">I", read(fw.PUBLISH + 4, 4))[0]
    blob = read(base, 0x10000)
    i = blob.find(MAGIC)
    return None if i < 0 else base + i


def smoke_setup():
    """Nothing: the shot belongs to the recipes' smoke look; smoke_checks sets GRAIN / VIGNETTE for its own run."""
    return {}


def synthetic_plane(stride, width, lines):
    """Y plane: a horizontal ramp 0..255 across the width plus a slow vertical wave; padding 0x5a (must survive)."""
    out = bytearray()
    for ln in range(lines):
        row = bytearray([0x5A]) * stride
        off = (ln * 7) % 64
        row[:width] = bytes(((x * 256 // width) + off) & 0xFF for x in range(width))
        out += row
    return bytes(out)


def run_main(uc, main, ret, cp, sp, count=0):
    """Call fxmain(cp) in ARM state on a Unicorn instance; it returns (bx lr) to the Thumb trap ret."""
    from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_SP
    uc.reg_write(UC_ARM_REG_R0, cp)
    uc.reg_write(UC_ARM_REG_SP, sp)
    uc.reg_write(UC_ARM_REG_LR, ret | 1)
    uc.reg_write(UC_ARM_REG_CPSR, (uc.reg_read(UC_ARM_REG_CPSR) & ~0xFF) | 0xD3)     # ARM state, IRQ/FIQ masked
    uc.emu_start(main, ret, count=count)


Y8, STRIDE8, WIDTH8, LINES8 = 0x8605F00, 3328, 3264, 2448


def smoke_checks(obs):
    """The smoke stops at the develop step (before the master YUV step), so run the installed, relocated pass here on
    the stopped machine: fake cp with the 8 MP geometry, synthetic full Y plane at 0x8605f00, GRAIN 3 + VIGNETTE 3
    (poked into the store header). Lines 0, the middle and the last are compared with the Python model; the
    instruction count is the emulator's own."""
    from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_SP
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
    main, ret = (struct.unpack(">I", s[o:o + 4])[0] for o in (O_MAIN, O_RET))
    build = (bool(s[O_BUILD] & 1), bool(s[O_BUILD] & 2))
    uc = m.uc
    cp, sp = 0x0BF10000, 0x0BF0F000
    plane = synthetic_plane(STRIDE8, WIDTH8, LINES8)
    size = STRIDE8 * LINES8
    regions = [(Y8, size), (cp, 0x100), (sp - 0x1000, 0x1000), (STB, 16)]
    saved = [(a, bytes(uc.mem_read(a, n))) for a, n in regions]
    regs = {r: uc.reg_read(r) for r in range(UC_ARM_REG_R0, UC_ARM_REG_R0 + 13)}
    regs.update({r: uc.reg_read(r) for r in (UC_ARM_REG_SP, UC_ARM_REG_CPSR, UC_ARM_REG_PC, UC_ARM_REG_LR)})
    vt, nd, ws = m.vt, m.next_due, m.want_stop
    try:
        uc.mem_write(Y8, plane)
        uc.mem_write(cp, bytes(0x100))
        uc.mem_write(cp + 0x54, struct.pack(">I", Y8))
        uc.mem_write(cp + 0x5C, struct.pack(">HHH", STRIDE8, WIDTH8, LINES8))
        hdr = bytearray(uc.mem_read(STB, 16))
        hdr[0:4], hdr[11], hdr[12] = STMAGIC, 3, 3
        uc.mem_write(STB, bytes(hdr))
        m.next_due, m.want_stop = 1 << 62, False
        run_main(uc, main, ret, cp, sp)
        insns = m.vt - vt
        check = (0, LINES8 // 2, LINES8 - 1)
        after = {ln: bytes(uc.mem_read(Y8 + ln * STRIDE8, STRIDE8)) for ln in check}
        s2 = bytes(uc.mem_read(st, STATE_LEN))
    finally:
        for a, b in saved:
            uc.mem_write(a, b)
        for r, v in regs.items():
            uc.reg_write(r, v)
        m.vt, m.next_due, m.want_stop = vt, nd, ws
    res, end = s2[O_RES], struct.unpack(">I", s2[O_END:O_END + 4])[0]
    seed = struct.unpack(">I", s2[O_SEED:O_SEED + 4])[0]
    full_end = Y8 + (LINES8 - 1) * STRIDE8 + WIDTH8
    out.append(("pass ran (result 1)", res == RES_RAN, f"result={res}"))
    out.append(("walked the full 3264x2448 plane (end of last line)", end == full_end,
                f"end={end:#x} expected={full_end:#x}"))
    exp = reference_pass(plane, STRIDE8, WIDTH8, LINES8, 3, 3, seed, only=set(check), build=build)
    out.append(("lines 0 / 1224 / 2447 == Python model (GRAIN 3, VIGNETTE 3)",
                all(after[ln] == exp[ln * STRIDE8:(ln + 1) * STRIDE8] for ln in check), f"seed={seed:#x}"))
    blocks = WIDTH8 // BLK * LINES8
    out.append((f"cost: vt {insns:,} (grdemu counts 2-byte units: about {insns // 2:,} ARM instructions) for "
                f"{blocks:,} 16-pixel blocks; loop model {LOOP_COST[build] * blocks:,} ARM", True, ""))
    obs.fx = dict(instructions=insns, blocks=blocks, end=hex(end), result=res, seed=hex(seed))
    return out
