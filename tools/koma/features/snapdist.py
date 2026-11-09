"""snapdist: GR II "Snap Focus Distance". SNAP distance from Koma settings byte 0x1b8b5, read at the point of use.

The firmware works in inverse distance: the calibration block (pointer at 0x175378, 0x08010000) holds the SNAP word
+0x892 (ROM default 410 = 1024/2.5 m) and the infinity word +0x894 (ROM default 0 = 1/inf). Stock reads the SNAP word
at three places, all redirected here:

  0x3c84a  bl 0x71314   SNAP focus-mode routine 0x3c83c (lens move); 0x3c850 reload of the same word -> 5 nops,
                        the handler returns the chosen word in r1, so 0x2d24fc stores it (as stock stores the SNAP word)
  0x3701c  bl 0x71314   0x36ff0, focus mode 2 (SNAP) re-apply
  0x703fa  bl 0x712a4   0x70280 type 1 (fixed preset): for SNAP and for the AF modes 0x712a4 returns the SNAP word;
                        the handler keeps every other branch (inf mode, scene words, the 0xaa calibration override)

Value for byte b (anything >5 counts as the default 3):
  3 (2.5 m)  the calibration SNAP word, unchanged (exactly stock)
  5 (inf)    the calibration infinity word, exactly what stock focus mode inf uses
  0,1,2,4    inf + (snap - inf) * 2.5 / d, i.e. the line through the two calibrated points (1/d units).
             ROM defaults give 1025, 683, 512, 205 (1, 1.5, 2, 5 m). A body-calibrated SNAP/inf pair is honoured.
"""
from __future__ import annotations

from .. import firmware as fw
from ..feature import Check, Feature, Hook, Patch

NAME = "snapdist"
SUMMARY = "SNAP focus distance 1/1.5/2/2.5/5 m/inf from settings byte 0x1b8b5 (3 hooks)"
OPTIONS = {}
BYTE = 0x1B8B5
DEFAULT = 3
ROWS = [dict(id="snapdist", label="SNAP DIST", options=["1M", "1.5M", "2M", "2.5M", "5M", "∞"],
             byte=BYTE, default=DEFAULT, group="focus")]
MUL_Q16 = [163840, 109227, 81920, 65536, 32768, 0]     # 2.5/d in Q16 for 1, 1.5, 2, 2.5, 5 m (inf handled apart)

SRC = f"""
@ r0 = inverse-distance word for the chosen SNAP distance. Clobbers r1-r3. Exported for other features.
snapval:
    ldr r3, =0x175378
    ldr r3, [r3]
    ldr r1, =0x892
    adds r3, r3, r1             @ r3 = &calibration SNAP word; +2 = infinity word
    ldrh r0, [r3]               @ stock SNAP word (2.5 m)
    ldr r2, ={BYTE:#x}
    ldrb r2, [r2]
    cmp r2, #5
    bhi 9f                      @ out of range -> default (stock)
    beq 8f                      @ inf
    cmp r2, #3
    beq 9f                      @ 2.5 m: stock word as is
    ldrh r1, [r3, #2]           @ infinity word
    subs r0, r0, r1             @ span = snap - inf
    ble 7f                      @ implausible calibration: keep stock
    lsrs r3, r0, #12
    bne 7f                      @ span >= 4096 would overflow the Q16 product: keep stock
    lsls r2, r2, #2
    adr r3, mul
    ldr r2, [r3, r2]
    muls r0, r2, r0
    lsrs r0, r0, #16
7:  adds r0, r0, r1
    bx lr
8:  ldrh r0, [r3, #2]
9:  bx lr

@ 0x3c84a: SNAP routine, r0 = stock SNAP word -> chosen word; returns chosen word in r1 for 0x3c85e (strh r1)
snap_move:
    push {{r4, lr}}
    bl snapval
    movs r4, r0
    ldr r3, =next_move
    bl call_r3
    movs r1, r4
    pop {{r4}}
    pop {{r3}}
    bx r3

@ 0x3701c: 0x36ff0 focus mode 2 re-apply, r0 = stock SNAP word -> chosen word
snap_reapply:
    push {{r4, lr}}
    bl snapval
    ldr r3, =next_reapply
    bl call_r3
    pop {{r4}}
    pop {{r3}}
    bx r3

@ 0x703fa: 0x70280 type 1 preset. Replace the result only where 0x712a4 returned the SNAP word.
snap_preset:
    push {{r4, lr}}
    ldr r3, =next_preset
    bl call_r3
    movs r4, r0
    ldr r2, =0x175378
    ldr r2, [r2]
    adds r2, #0x40
    ldrb r1, [r2]
    cmp r1, #0xaa               @ calibration override: 0x712a4 returned +0x42
    beq 1f
    ldr r2, =0x23b068           @ 0x92d1c()
    ldrb r1, [r2, #4]           @ focus mode
    cmp r1, #2                  @ SNAP
    beq 2f
    cmp r1, #3                  @ inf
    beq 1f
    ldrb r1, [r2, #0x1d]        @ scene
    cmp r1, #3
    beq 1f
    cmp r1, #4
    beq 1f
2:  bl snapval
    movs r4, r0
1:  movs r0, r4
    pop {{r4}}
    pop {{r3}}
    bx r3

call_r3:
    bx r3
.ltorg
.p2align 2
mul:
    .word {", ".join(str(v) for v in MUL_Q16)}
"""


def make(opts):
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=opts, source=SRC, exports=["snapval"],
        conflicts=["snap15"],
        # smoke_reachable=False: SNAP mode is greyed in the model; the preset fires only on a quick full press
        hooks=[Hook(0x3C84A, 0x71314, "snap_move", "next_move", "SNAP routine lens move", smoke_reachable=False,
                    rewrites_r0=True),
               Hook(0x3701C, 0x71314, "snap_reapply", "next_reapply", "0x36ff0 SNAP re-apply", smoke_reachable=False,
                    rewrites_r0=True),
               Hook(0x703FA, 0x712A4, "snap_preset", "next_preset", "0x70280 type-1 preset (SNAP / AF modes)",
                    smoke_reachable=False)],
        patches=[Patch(0x3C850, fw.ram(0x3C850, 10), bytes.fromhex("46c0") * 5,
                       "SNAP routine reload of 0x08010892 -> nops; r1 = chosen word from snap_move")],
        checks=[Check(0x3C83C, fw.ram(0x3C83C, 0x0E), "SNAP routine head: r0 = SNAP word at the hook"),
                Check(0x3C84E, fw.ram(0x3C84E, 2), "adds r7,r0,#0 keeps r1 from the hook"),
                Check(0x3C85A, fw.ram(0x3C85A, 6), "ldr r0,=0x2d24e4; movs r2,#2; strh r1,[r0,#0x18]"),
                Check(0x36FF0, fw.ram(0x36FF0, 0x2C), "0x36ff0 focus-mode switch around the re-apply"),
                Check(0x712A4, fw.ram(0x712A4, 0x70), "0x712a4 branch logic replicated by snap_preset"),
                Check(0x92D1C, fw.ram(0x92D1C, 8), "0x92d1c returns 0x23b068")])


def smoke_setup():
    return {BYTE: 1}                       # 1.5 m, so a SNAP-word use is distinguishable from stock 410
