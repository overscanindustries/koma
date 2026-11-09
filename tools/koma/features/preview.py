"""preview: the LCD live view shows the active recipe (Koma 0.3). Needs bwrecipes; shares its tables and leaf routines.


Why: Koma writes recipe registers only inside still develop. Every way back into live view re-runs Ricoh's monitor
preset after that (STATIC): monitor start 0x985a0, post-shot resume 0x95cd0, setting change 0x338cc. Each ends its
colour set-up with the mono/TE writer 0x8ff0e(cp+0x22); the setting change then rewrites the chroma gain once more
(0x8f18c -> 0x922bc(0) at 0x8f1f0). The preview wraps those four calls and, after the stock write, re-applies the
active recipe's colour registers. No per-frame cost: it runs only on those transitions.

Level A (default):
  hooks  0x98726 / 0x95e24 / 0x8f2e0  bl 0x8ff0e (r0 = the monitor's mono flag cp+0x22; r0 dead after the call)
         0x8f1f0                      bl 0x922bc (r0 = 0: monitor preset; return value dead)
         0x923fc                      bl 0x171924 inside 0x922bc: tap of the stock chroma word (r0 = &word)
  colour : chroma gain 0x12018004 = the monitor's own stock word (tapped) x the recipe SATURATION step, per byte,
           capped 0xff (the still develop does stock NORMAL word x the same step, so the screen moves by the same
           ratio as the photo); MatR/G/B 0x12018104/108/10c = the look's matrix + TINT (bwrecipes mtab, as on the shot)
  SELECT : chroma gain only (SATURATION p7): the hue isolation is selcolour's software pass, never on the live view
  B&W    : filter luma weights 0x1201800c (bwrecipes ftab; no red-filter WB tilt: the monitor pins CC2 WB to unity) and
           the toning word through the stock TE setter 0x171d80 (bwrecipes ttab), like develop pass 1
  none   : nothing, except the luma weights go back to 0x92050's value if a recipe had moved them (bwrecipes yw_restore,
           shared state byte): stock behaviour exactly
Level B (`curve=on`): the recipe curve (bwrecipes generator) streamed into CC2 (0x16d31c) and CC1 (0x166334) at the
  monitor start and post-shot resume sites, before the stream is re-enabled; both selector caches 0x1f4db0 and
  0x1f4db3 := 0xff afterwards so the next still uploads its own table. Recipe OFF after a preview upload: the stock
  monitor LUT load 0x92050 runs again (it reloads both LUTs and the luma weights), caches 0xff.
Level C (`warmth=on`): pointers in the two monitor WB veneers (0x174e9c -> 0x1725f8 RG, 0x174e90 -> 0x172610 B) go to
  handlers that scale R / B by the WARMTH step (+-3.5 % per step, as on the shot) while the monitor is active (0x1f502f
  != 0; capture clears it at 0x97644, so develop's call through 0x9b7e0 stays stock). The step is latched by the
  level-A apply, so the per-update cost is two byte loads.
Recipe change: opening the menu stops the monitor (0x98a32) and closing it runs a full monitor start 0x985a0 (EMU), so a KOMA-page or IMG SET change is applied by the 0x98726 hook on menu exit;
  the ADJ lever's live IMG SET step goes through the setting-change path (STATIC). No extra re-apply code.
"""
from __future__ import annotations

import struct

from .. import firmware as fw
from ..feature import Check, Feature, Hook, Pointer
from . import bwrecipes as B

NAME = "preview"
SUMMARY = "live view shows the active recipe: chroma, matrix, toning, B&W filter (curve, warmth: options)"
OPTIONS = {"live": "on (default) | off: build the feature with no records (drop it from a bundle with one option)",
           "curve": "off (default) | on: level B, the recipe curve on the live view (CC1 + CC2 uploads)",
           "warmth": "off (default) | on: level C, WARMTH on the live view (monitor WB veneers)"}
VERSION = "1.0"

MONO, CHROMA_PRESET, CHROMA_SET = 0x8FF0E, 0x922BC, 0x171924
SITE_START, SITE_RESUME, SITE_SETTING, SITE_CHROMA, SITE_TAP = 0x98726, 0x95E24, 0x8F2E0, 0x8F1F0, 0x923FC
CC1_UP, CC1_CACHE, LUT_LOAD = 0x166334, 0x1F4DB0, 0x92050
MON_ACTIVE = 0x1F502F
VEN_RG, VEN_B, WB_RG, WB_B = 0x174E9C, 0x174E90, 0x1725F8, 0x172610
SAT_Q8 = [round(s * 256) for s in B.SAT_STEPS]            # 141 .. 371
WARM_Q16 = 2294                                           # 0.035 in Q16 (bwrecipes WK is the same step x 16/25)

# pv (resident, 36 B): +0 'KPV1', +4 applies (u8), +9 last site (0 start, 1 resume, 2 setting, 3 chroma 0x8f1f0,
# 4 unused), +6 last type (0 B&W, 1 colour, 2 SELECT, 0xff none), +7 curve uploads, +8 last monitor mono
# flag, +9 latched WARMTH step + 1 (0 none), +10 level B: a preview curve is loaded, +12 tapped stock chroma word,
# +16 SATURATION Q8 x 7 (u16). Read by the tests and the emulator run.
PL_MAGIC = b"KPV1"
O_N, O_SITE, O_TYPE, O_CURVE, O_MONO, O_WARM, O_DIRTY, O_MON, O_SATQ, PL_LEN = \
    4, 5, 6, 7, 8, 9, 10, 12, 16, 32


def source(curve=False, warmth=False):
    bw = "$bwrecipes"
    equ = "".join(f".equ {k}, {v}\n" for k, v in dict(
        P_FILT=B.P_FILT, P_TONE=B.P_TONE, P_DENS=B.P_DENS, P_SAT=B.P_SAT, P_SSAT=B.P_SSAT, P_WARM=B.P_WARM,
        P_TINT=B.P_TINT, O_N=O_N, O_SITE=O_SITE, O_TYPE=O_TYPE, O_CURVE=O_CURVE, O_MON=O_MON,
        O_SATQ=O_SATQ, O_MONO=O_MONO, O_WARM=O_WARM, O_DIRTY=O_DIRTY).items())
    return equ + f"""
@ ================================================================ hooks on the monitor's bl 0x8ff0e (r0 = mono flag)
pv_m0:
    push {{r4, r5, r6, lr}}
    movs r6, #0
    ldr r3, =next_m0
    b pv_mono
pv_m1:
    push {{r4, r5, r6, lr}}
    movs r6, #1
    ldr r3, =next_m1
    b pv_mono
pv_m2:
    push {{r4, r5, r6, lr}}
    movs r6, #2
    ldr r3, =next_m2
pv_mono:
    movs r4, r0
    bl call_r3                       @ stock mono / TE writer (or the next link), original r0
    movs r0, r4
    movs r1, r6
    bl pv_apply
    pop {{r4, r5, r6}}
    pop {{r3}}
    bx r3

@ hook on bl 0x922bc(0) at 0x8f1f0 (setting change: the monitor chroma is written once more, after 0x8f2e0)
pv_c:
    push {{r4, lr}}
    ldr r3, =next_c
    bl call_r3
    movs r4, r0
    movs r0, #0xff                   @ chroma only
    movs r1, #3
    bl pv_apply
    movs r0, r4
    pop {{r4}}
    pop {{r3}}
    bx r3

@ hook on bl 0x171924 at 0x923fc (end of 0x922bc, r0 = &word): remember the stock chroma word, then the setter
pv_tap:
    push {{r4, lr}}
    ldr r1, [r0, #0]
    ldr r2, =pv
    str r1, [r2, #O_MON]
    ldr r3, =next_tap
    bl call_r3
    pop {{r4}}
    pop {{r3}}
    bx r3

@ ================================================================ apply
@ pv_apply: r0 = monitor mono flag (0 colour, 1 / 2 B&W / TE) or 0xff (chroma only), r1 = site id (0..4).
pv_apply:
    push {{r4, r5, r6, r7, lr}}
    movs r4, r0
    ldr r7, =pv
    strb r1, [r7, #O_SITE]
    ldrb r0, [r7, #O_N]
    adds r0, #1
    strb r0, [r7, #O_N]
    bl {bw}.getcur
    ldr r6, ={bw}.cur
    movs r5, #0xff
    cmp r0, #0
    beq 1f
    ldr r0, ={bw}.ckind
    ldrb r5, [r0, #0]
    lsls r5, r5, #30
    lsrs r5, r5, #30                 @ type: 0 B&W, 1 colour, 2 SELECT
1:  strb r5, [r7, #O_TYPE]
    cmp r4, #0xff
    beq pa_chroma
    strb r4, [r7, #O_MONO]
    movs r0, #0
    strb r0, [r7, #O_WARM]
    cmp r5, #0
    beq pa_bw
    cmp r5, #0xff
    beq pa_none
    cmp r4, #0                       @ ---- colour / SELECT
    bne pa_curve                     @ monitor not in colour (never on base NORMAL): registers stay stock
    bl {bw}.yw_restore
    bl pv_chroma
    cmp r5, #1
    bne pa_curve
    ldr r0, ={bw}.ckind              @ ---- matrix: the look's rows + TINT (bwrecipes mtab: MatR, MatB, MatG x 7)
    ldrb r0, [r0, #0]
    lsrs r0, r0, #4
    movs r1, #36
    muls r0, r1, r0
    ldr r1, ={bw}.mtab
    adds r0, r0, r1
    ldrb r3, [r6, #P_TINT]
    lsls r3, r3, #2
    adds r3, r3, r0
    ldr r3, [r3, #8]
    ldr r1, [r0, #0]
    ldr r2, [r0, #4]
    ldr r0, =0x12018104
    str r1, [r0, #0]
    str r3, [r0, #4]
    str r2, [r0, #8]
    ldrb r0, [r6, #P_WARM]           @ latch WARMTH for level C (step + 1)
    adds r0, #1
    strb r0, [r7, #O_WARM]
    b pa_curve
pa_chroma:
    subs r0, r5, #1                  @ colour / SELECT only
    cmp r0, #1
    bhi pa_out
    bl pv_chroma
    b pa_out
pa_bw:
    subs r0, r4, #1
    cmp r0, #1
    bhi pa_none                      @ monitor in colour: no filter / toning
    ldrb r0, [r6, #P_FILT]           @ ---- filter luma weights
    subs r0, #1
    bmi 2f
    lsls r0, r0, #4
    ldr r1, ={bw}.ftab
    ldr r0, [r1, r0]
    ldr r1, =0x12018000
    str r0, [r1, #0xc]
    ldr r1, ={bw}.state
    movs r0, #1
    strb r0, [r1, #0]
    b 3f
2:  bl {bw}.yw_restore
3:  ldrb r1, [r6, #P_TONE]           @ ---- toning (Cb, Cr) through the stock TE setter, as develop pass 1
    subs r1, #1
    bmi pa_curve
    movs r2, #5
    muls r1, r2, r1
    ldrb r2, [r6, #P_DENS]
    adds r1, r1, r2
    lsls r1, r1, #1
    ldr r2, ={bw}.ttab
    adds r2, r2, r1
    movs r0, #0
    ldrsb r1, [r2, r0]
    movs r0, #1
    ldrsb r2, [r2, r0]
    lsls r1, r1, #22
    lsrs r1, r1, #22                 @ Cb & 0x3ff
    lsls r2, r2, #22
    lsrs r2, r2, #10                 @ (Cr & 0x3ff) << 12
    orrs r1, r2
    ldr r0, =0x{B.TE_SHADOW:x}
    ldr r0, [r0, #0]
    movs r2, #1
    lsls r2, r2, #24
    orrs r0, r2
    ldr r2, =0xffc00c00
    ands r0, r2
    orrs r0, r1
    sub sp, #8
    str r0, [sp, #0]
    mov r0, sp
    ldr r3, =0x{B.TE_SET | 1:x}
    bl call_r3
    add sp, #8
pa_curve:
{CURVE_APPLY if curve else ""}
    b pa_out
pa_none:
    bl {bw}.yw_restore               @ only if a recipe moved the luma weights (shared state byte)
{CURVE_RESTORE if curve else ""}
pa_out:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

@ pv_chroma: r5 = type (1 colour / 2 SELECT), r6 = cur, r7 = pv. 0x12018004 = tapped stock monitor word x SAT Q8 >> 8
@ per byte, capped 0xff.
pv_chroma:
    push {{r4, lr}}
    ldrb r0, [r6, #P_SAT]
    cmp r5, #1
    beq 1f
    ldrb r0, [r6, #P_SSAT]
1:  lsls r0, r0, #1
    adds r0, #O_SATQ
    ldrh r1, [r7, r0]
    ldr r0, [r7, #O_MON]
    movs r2, #0
    movs r3, #24
2:  movs r4, r0
    lsrs r4, r3
    lsls r4, r4, #24
    lsrs r4, r4, #24
    muls r4, r1, r4
    lsrs r4, r4, #8
    cmp r4, #0xff
    bls 3f
    movs r4, #0xff
3:  lsls r4, r3
    orrs r2, r4
    subs r3, #8
    bpl 2b
    ldr r0, =0x12018000
    str r2, [r0, #4]
    pop {{r4}}
    pop {{r3}}
    bx r3
{UPLOAD_SRC if curve else ""}
{WARMTH_SRC if warmth else ""}
call_r3:
    bx r3
.ltorg

.p2align 2
pv:
    .ascii "{PL_MAGIC.decode()}"
    .byte 0, 0, 0xff, 0, 0, 0, 0, 0
    .word 0x755f5960                 @ +12 until the first tap (research: the monitor preset word)
    .hword {", ".join(str(q) for q in SAT_Q8)}
    .byte 0, 0
.p2align 2
"""


CURVE_APPLY = """
    ldrb r0, [r7, #O_SITE]           @ ---- level B: start / resume only (before the stream is re-enabled)
    cmp r0, #1
    bhi pa_out
    ldrb r0, [r6, #0]
    bl pv_upload
    movs r0, #1
    strb r0, [r7, #O_DIRTY]"""
CURVE_RESTORE = f"""
    ldrb r0, [r7, #O_DIRTY]          @ level B: a preview curve is still loaded: the stock monitor LUT load again
    cmp r0, #0
    beq pa_out
    movs r0, #0
    strb r0, [r7, #O_DIRTY]
    ldr r3, =0x{LUT_LOAD | 1:x}
    bl call_r3
    bl pv_inval"""

UPLOAD_SRC = f"""
@ pv_upload: r0 = base id (prm filled by getcur): 16 x (CC2 0x16d31c, CC1 0x166334)(offset, 256, buf); caches 0xff.
pv_upload:
    push {{r4, lr}}
    ldr r1, =$bwrecipes.prm          @ gen_chunk reads bwrecipes' private copy prmu (bwrecipes 4.2, review F2)
    ldr r2, =$bwrecipes.prmu
    movs r4, #12
2:  ldr r3, [r1, r4]
    str r3, [r2, r4]
    subs r4, #4
    bpl 2b
    lsls r0, r0, #10
    ldr r1, =$bwrecipes.bases
    adds r0, r0, r1
    ldr r3, =$bwrecipes.gs
    str r0, [r3, #0]
    movs r0, #0
    str r0, [r3, #4]
    str r0, [r3, #8]
    str r0, [r3, #12]
    movs r4, #0
1:  bl $bwrecipes.gen_chunk
    movs r0, r4
    movs r1, #1
    lsls r1, r1, #8
    ldr r2, =$bwrecipes.buf
    ldr r3, =0x{B.CC2_UP | 1:x}
    bl call_r3
    movs r0, r4
    movs r1, #1
    lsls r1, r1, #8
    ldr r2, =$bwrecipes.buf
    ldr r3, =0x{CC1_UP | 1:x}
    bl call_r3
    movs r0, #1
    lsls r0, r0, #8
    adds r4, r4, r0
    lsrs r0, r4, #12
    beq 1b
    ldr r0, =pv
    ldrb r1, [r0, #O_CURVE]
    adds r1, #1
    strb r1, [r0, #O_CURVE]
    bl pv_inval
    pop {{r4}}
    pop {{r3}}
    bx r3

@ leaf: both selector caches (CC1 0x1f4db0, CC2 0x1f4db3) := 0xff, so the next still uploads its own table
pv_inval:
    ldr r0, =0x{CC1_CACHE:x}
    movs r1, #0xff
    strb r1, [r0, #0]
    strb r1, [r0, #3]
    bx lr
"""

# Level C. Entered from ARM code through the veneer (ldr r12, =handler+1; bx r12) with r0 = &word; returns to ARM via
# bx. Monitor active and a WARMTH step latched: R (bits 16..24) or B (bits 0..8) x (1 +- 0.035 s), capped 0x1ff, into
# a stack copy, then the stock setter.
WARMTH_SRC = f"""
pv_wb_rg:
    push {{r4, r5, lr}}
    ldr r4, [r0, #0]
    movs r5, #0                      @ R: + step
    bl pv_warm
    beq 1f
    lsrs r2, r4, #16
    ldr r3, =0x1ff
    ands r2, r3
    muls r2, r0, r2
    lsrs r2, r2, #16
    cmp r2, r3
    bls 2f
    movs r2, r3
2:  lsls r3, r3, #16
    bics r4, r3
    lsls r2, r2, #16
    orrs r4, r2
1:  ldr r3, =0x{WB_RG | 1:x}
    b pv_wb_out
pv_wb_b:
    push {{r4, r5, lr}}
    ldr r4, [r0, #0]
    movs r5, #1                      @ B: - step
    bl pv_warm
    beq 1f
    ldr r3, =0x1ff
    movs r2, r4
    ands r2, r3
    muls r2, r0, r2
    lsrs r2, r2, #16
    cmp r2, r3
    bls 2f
    movs r2, r3
2:  bics r4, r3
    orrs r4, r2
1:  ldr r3, =0x{WB_B | 1:x}
pv_wb_out:
    sub sp, #8
    str r4, [sp, #0]
    mov r0, sp
    bl call_r3
    add sp, #8
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

@ pv_warm: r5 = 0 (R) / 1 (B). r0 = Q16 multiplier, Z clear; or r0 = 0, Z set (monitor off, nothing latched, step 0).
pv_warm:
    ldr r0, =0x{MON_ACTIVE:x}
    ldrb r0, [r0, #0]
    cmp r0, #0
    beq 9f
    ldr r0, =pv
    ldrb r0, [r0, #O_WARM]
    cmp r0, #0
    beq 9f
    subs r0, #4                      @ latched step + 1: 1..7 -> -3..+3
    beq 9f
    cmp r5, #0
    beq 5f
    negs r0, r0
5:  ldr r1, ={WARM_Q16}
    muls r0, r1, r0
    movs r1, #1
    lsls r1, r1, #16
    adds r0, r0, r1                  @ 0.895 .. 1.105 in Q16: never 0, Z clear
    bx lr
9:  movs r0, #0
    bx lr
"""

# (addr, bytes as the 2.40 disassembly shows them, why): verified at install, never written
CHECKS = [
    (0x98718, "1df83019784129 01d10070817880", "0x985a0: r0 = cp+0x22 (r7 = cp) before bl 0x8ff0e at 0x98726"),
    (0x95E16, "1df83019784129 01d10070857880", "0x95cd0: r0 = cp+0x22 before bl 0x8ff0e at 0x95e24"),
    (0x8F2DA, "1df830197880", "0x8f280: r0 = cp+0x22 before bl 0x8ff0e at 0x8f2e0"),
    (0x8F1EE, "2000", "0x8f18c: r0 = 0 (monitor preset) before bl 0x922bc at 0x8f1f0"),
    (0x8F1F4, "1c28bcb8", "0x8f18c: return value of 0x922bc unused (r0 = r5 after)"),
    (0x923FA, "a801", "0x922bc: r0 = &word (sp+4) for the setter call at 0x923fc"),
    (CHROMA_SET, "680049016048477012018000", "0x171924 chroma setter: 0x12018004 = *r0"),
    (0x98814, "76f8", "0x985a0: monitor-active 0x1f502f := 1"),
    (0x97644, "7008", "capture: monitor-active 0x1f502f := 0"),
]
CURVE_CHECKS = [
    (CC1_UP, "b5f0b0841c0c1c151c06", "0x166334(offset, size, buf) CC1 LUT upload prologue"),
    (0x90350, "001f4da8", "cache base: CC1 cache 0x1f4db0 = base + 8 (CC2 0x1f4db3 = + 0xb)"),
    (LUT_LOAD, "b590b082", "0x92050 stock monitor LUT load prologue (no arguments)"),
]
WARM_CHECKS = [
    (WB_RG, "6800490161084770", "0x1725f8: 0x12018110 = *r0"),
    (WB_B, "6800490205c00dc06148", "0x172610: 0x12018114 = *r0 & 0x1ff"),
    (0x174E94, "e59fc000e12fff1c", "veneer 0x174e94: ldr r12, [pc]; bx r12"),
    (0x174E88, "e59fc000e12fff1c", "veneer 0x174e88: ldr r12, [pc]; bx r12"),
]


def make(opts):
    opts = dict(opts or {})
    for k, allowed in (("live", ("on", "off")), ("curve", ("on", "off")), ("warmth", ("on", "off"))):
        if opts.get(k, allowed[0] if k == "live" else "off") not in allowed:
            raise ValueError(f"preview.{k}={opts[k]!r}: use on or off")
    if opts.get("live", "on") == "off":
        return Feature(NAME, VERSION, SUMMARY + " [live=off: no records]", options=opts)
    curve, warmth = opts.get("curve") == "on", opts.get("warmth") == "on"
    ns = dict(smoke_reachable=False)       # smoke stops in develop
    hooks = [Hook(SITE_START, MONO, "pv_m0", "next_m0", "monitor start 0x985a0: recipe after the stock mono writer", **ns),
             Hook(SITE_RESUME, MONO, "pv_m1", "next_m1", "post-shot resume 0x95cd0: recipe after the mono writer", **ns),
             Hook(SITE_SETTING, MONO, "pv_m2", "next_m2", "setting change 0x8f280: recipe after the mono writer", **ns),
             Hook(SITE_CHROMA, CHROMA_PRESET, "pv_c", "next_c", "setting change 0x8f18c: chroma after 0x922bc(0)", **ns),
             Hook(SITE_TAP, CHROMA_SET, "pv_tap", "next_tap", "0x922bc's chroma setter: remember the stock word")]
    pointers = []
    checks = CHECKS + (CURVE_CHECKS if curve else []) + (WARM_CHECKS if warmth else [])
    if warmth:
        pointers = [Pointer(VEN_RG, struct.pack(">I", WB_RG | 1), "pv_wb_rg", thumb=True,
                            why="monitor WB veneer 0x174e94 (RG 0x12018110) -> WARMTH while live"),
                    Pointer(VEN_B, struct.pack(">I", WB_B | 1), "pv_wb_b", thumb=True,
                            why="monitor WB veneer 0x174e88 (B 0x12018114) -> WARMTH while live")]
    return Feature(NAME, VERSION, SUMMARY, source=source(curve, warmth), options=opts, hooks=hooks, pointers=pointers,
                   checks=[Check(a, bytes.fromhex(h.replace(" ", "")), w) for a, h, w in checks],
                   requires=["bwrecipes"], exports=["pv_apply"])
