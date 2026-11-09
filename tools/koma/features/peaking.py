"""peaking: focus peaking on the camera's own LCD (OSD plane 1), once per live-view frame. KOMA row PEAKING
{OFF, RED, YELLOW}, settings byte 0x1b8bf.

Mechanism (the hlalert live path's, moved from every 4th frame to every frame):
  hook  0x95af4 `bl 0x99a28` in the VD task loop 0x95ad4 (task 0x95f40, woken once per monitor VD: VD callback 0x9dfb4
        -> 0x959c4 activates HISR "VdTaskWait" 0x959e0 -> semaphore [0x1f4e6c]). Task context, the same task whose
        every-4th-frame call at 0x95b04 hlalert uses. The wrapped call (returns byte [0x1f5034]) runs first and its r0
        is returned unchanged.
  frame d = [0x1f501c] (display slot, stable for two VD periods), Y = [0x2cf640 + 4d], 640x244 at stride 672.
  work  STEP x STEP subsampling (default 4: 160 x 60 samples, the first 240 lines as hlalert maps them).
        g = |Y(x+1,y) - Y(x,y)| + |Y(x,y+1) - Y(x,y)|; g >= THRESH marks one 2x2 OSD block = one byte in each of
        OSD rows y, y+1 at byte floor(9x/16 / 2) (the 640 -> 360 width map hlalert uses). A block is painted only when
        both bytes are 0 (fully transparent), with colour index c in both nibbles. Before painting, each previously
        marked block of the same group has its nibbles equal to the old colour cleared (nothing else). Groups of 8
        samples share one bitmap byte (1 bit per sample, 20 B per sample row, 1,200 B).
  show  plane-1 shadow 0x302d58 (0xd4018(1)) then the firmware flush 0x6bbfc(1, rect) (Thumb stub 0x174c05), exactly
        as hlalert; rect = the sample rows that actually changed (no flush when nothing changed).
Gates, every call: one pass per VD (VD count [0x1f511c] moved). Not our screen (monitor stopped [0x1f502f] != 1, or app
state [0x23a874] not one of 2/3/5/10/12/13): forget the marks, plane untouched (the firmware owns that screen). Our
screen but PEAKING OFF / out of range, digital zoom on, or no frame: erase our marks (flush), stay idle.
Colour: the stock shooting palette's own entries, no VEN register writes (the firmware reloads all 15 entries per screen
through 0x2233c, so a private register value would not survive, and every index is written). RED = index 15 (as
hlalert), YELLOW = index 10 (0x98bd29, an amber). Shares hlalert's 2x2 grid, so a block is owned by one of the two.
Footprint: bitmap = one pool block allocated once by the init (STEP 4: 1,200 B; STEP 8: 300 B); alloc failure = off.
"""
from __future__ import annotations

from ..feature import Check, Feature, Hook
from .. import firmware as fw

NAME = "peaking"
SUMMARY = "Focus peaking on the LCD: per-frame edge marks in OSD plane 1 (setting 0x1b8bf)"
OPTIONS = {"threshold": "edge strength |dx|+|dy| at or above which a sample is marked, 1..255 (default 24)",
           "step": "subsampling of the 640x240 live Y in both axes: 4 (default, 160x60 samples) or 8 (80x30)",
           "red": "OSD palette index for RED, 1..15 (default 15)",
           "yellow": "OSD palette index for YELLOW, 1..15 (default 10)",
           "cap": "most marks drawn per frame, 1..9600 (default 1200); bounds the cost on dense detail"}

SETB = 0x1B8BF
ROW_OPTIONS = ["OFF", "RED", "YELLOW"]
ROWS = [dict(id="peaking", label="PEAKING", options=ROW_OPTIONS, byte=SETB, default=0, group="display")]

SITE, TARGET = 0x95AF4, 0x99A28
PLANE1 = 0x302D58                 # 0xd4018(1)
FLUSH = 0x174C05                  # Thumb stub -> ARM 0x6bbfc(plane, rect)
LIVEPTR = 0x2CF640                # Y slot pointers [0..2]
DSLOT = 0x1F501C                  # display slot index (word), set by 0x991fa
VDCNT = 0x1F511C                  # monitor VD count (word), 0x9dfb4
MONRUN = 0x1F502F                 # monitor running (byte)
ZOOM, APPST = 0x23B068, 0x23A874
LIVE_STATES = (2, 3, 5, 10, 12, 13)
YSTRIDE, OSDROW = 672, 180
SAMPLES_X = 160                   # STEP 4; STEP 8 = 80


def geometry(step):
    """Per-step constants: groups per sample row, sample rows, plane byte offset of sample k in its group, group stride
    in plane bytes (bx = floor(9 * step * i / 32))."""
    nx = 640 // step
    ny = 240 // step
    groups = nx // 8
    boff = [(9 * step * k) // 32 for k in range(8)]
    gstride = (9 * step * 8) // 32
    return dict(nx=nx, ny=ny, groups=groups, boff=boff, gstride=gstride, ygroup=8 * step, bm_bytes=groups * ny)


def block_of(i, j, step):
    """Host model: sample (i, j) -> (plane byte column, OSD row) of its 2x2 block."""
    return (9 * step * i) // 32, step * j


def _samples(step):
    """The 8 unrolled samples of one group: r4 = Y row y at the group's first x, r3 = row y+1, r6 |= bit k on a hit.
    ldrb immediates stop at 31, so with step 8 the pointers move by 32 after sample 3 (and back after the group)."""
    out, base = [], 0
    for k in range(8):
        off = step * k - base
        if off + 1 > 31:
            out += ["    adds r4, #32", "    adds r3, #32"]
            base += 32
            off -= 32
        out += [f"    ldrb r0, [r4, #{off}]",
                f"    ldrb r1, [r4, #{off + 1}]",
                f"    ldrb r2, [r3, #{off}]",
                "    subs r1, r1, r0",
                f"    bpl s{k}a",
                "    negs r1, r1",
                f"s{k}a:",
                "    subs r2, r2, r0",
                f"    bpl s{k}b",
                "    negs r2, r2",
                f"s{k}b:",
                "    adds r1, r1, r2",
                "    cmp r1, #THRESH",
                f"    blo s{k}n",
                f"    adds r6, #{1 << k}",
                f"s{k}n:"]
    if base:
        out += [f"    subs r4, #{base}", f"    subs r3, #{base}"]
    return "\n".join(out)


SRC = """
.equ SETB, 0x{setb:x}
.equ LIVEPTR, 0x{liveptr:x}
.equ DSLOT, 0x{dslot:x}
.equ VDCNT, 0x{vdcnt:x}
.equ MONRUN, 0x{monrun:x}
.equ ZOOM, 0x{zoom:x}
.equ APPST, 0x{appst:x}
.equ PLANE1, 0x{plane1:x}
.equ FLUSH, 0x{flush:x}
.equ STMASK, 0x{stmask:x}
.equ THRESH, {thresh}
.equ STEP, {step}
.equ NOPT, {nopt}
.equ NY, {ny}
.equ GROUPS, {groups}
.equ GSTRIDE, {gstride}
.equ YGROUP, {ygroup}
.equ YROWSTEP, {yrowstep}
.equ PROWSKIP, {prowskip}
.equ BMBYTES, {bmbytes}
.equ MAXMARKS, {cap}

@ ---------------------------------------------------------------- hook
hook_vd:                        @ 0x95af4, VD task 0x95ad4: once per monitor VD. r0 of the wrapped call returned as is
    push {{r4, lr}}
    ldr r3, =next_vd
    bl call_r3
    movs r4, r0
    bl peak
    movs r0, r4
    pop {{r4}}
    pop {{r3}}
    bx r3

call_r3:
    bx r3

@ ---------------------------------------------------------------- peak: gates, then scan
peak:
    push {{r4, r5, r6, r7, lr}}
    ldr r7, =pstate
    ldr r0, =heap_ptr
    ldr r0, [r0, #0]
    cmp r0, #0
    beq pk_out                  @ no bitmap (alloc failed): feature off
    ldr r0, =VDCNT
    ldr r0, [r0, #0]
    ldr r1, [r7, #4]
    cmp r0, r1
    beq pk_out                  @ this VD already handled
    str r0, [r7, #4]
    ldr r0, =MONRUN
    ldrb r0, [r0, #0]
    cmp r0, #1
    bne pk_drop
    ldr r2, =APPST
    ldr r1, [r2, #0]
    cmp r1, #16
    bhs pk_drop
    ldr r0, =STMASK
    lsrs r0, r1
    lsrs r0, r0, #1
    bcc pk_drop
    ldr r0, =SETB
    ldrb r0, [r0, #0]
    cmp r0, #NOPT
    bhs pk_off
    ldr r1, =coltab
    ldrb r0, [r1, r0]
    cmp r0, #0
    beq pk_off
    ldr r2, =ZOOM
    ldrb r1, [r2, #5]
    lsrs r1, r1, #1
    bne pk_off                  @ digital zoom: the display crops, the map would be wrong
    ldr r2, =DSLOT
    ldr r1, [r2, #0]
    cmp r1, #2
    bhi pk_off
    lsls r1, r1, #2
    ldr r2, =LIVEPTR
    ldr r1, [r2, r1]
    cmp r1, #0
    bne pk_run
pk_off:
    movs r0, #0
    movs r1, #0
    ldrb r2, [r7, #0]
    cmp r2, #0
    beq pk_out                  @ off and no marks: nothing to do
pk_run:
    bl scan
    ldrb r0, [r7, #1]
    strb r0, [r7, #0]           @ the marks now carry the new colour (0 = none)
pk_out:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
pk_drop:                        @ not our screen: forget the marks, never touch the plane
    ldrb r0, [r7, #0]
    cmp r0, #0
    beq pk_out                  @ no marks (colour 0 means an empty bitmap)
    bl bm_clear
    movs r0, #0
    strb r0, [r7, #0]
    b pk_out

@ ---------------------------------------------------------------- scan(r0 colour or 0, r1 Y base or 0 = erase only)
@ r3/r4 Y rows y+1/y at the group, r5 plane byte of the group's sample 0 (OSD row y), r6 new bits, r7 bitmap byte,
@ r8 groups left, r9 row changed, r10 Y (0 = erase only), r11 j.
scan:
    push {{r4, r5, r6, r7, lr}}
    mov r2, r8
    mov r3, r9
    push {{r2, r3}}
    mov r2, r10
    mov r3, r11
    push {{r2, r3}}
    sub sp, #16                 @ [0] Y row base, [4] rows left, [8] first changed j (255 none), [12] last changed j
    ldr r2, =pstate
    strb r0, [r2, #1]
    lsls r3, r0, #4
    orrs r3, r0
    strb r3, [r2, #2]           @ colour in both nibbles
    movs r3, #0
    str r3, [r2, #8]            @ marks this pass
    str r1, [sp, #0]
    mov r10, r1
    ldr r5, =PLANE1
    ldr r7, =heap_ptr
    ldr r7, [r7, #0]
    movs r0, #NY
    str r0, [sp, #4]
    movs r0, #255
    str r0, [sp, #8]
    movs r0, #0
    str r0, [sp, #12]
    mov r11, r0
sc_row:
    ldr r4, [sp, #0]
    ldr r0, ={ystride}
    adds r3, r4, r0
    movs r0, #GROUPS
    mov r8, r0
    movs r0, #0
    mov r9, r0
sc_grp:
    movs r6, #0
    mov r0, r10
    cmp r0, #0
    beq sc_skip                 @ erase only
    ldr r2, =pstate
    ldr r0, [r2, #8]
    ldr r1, =MAXMARKS
    cmp r0, r1
    blo sc_samp
sc_skip:                        @ cap reached: no more sampling this pass, old marks still go through grp
    b sc_eval
sc_samp:
{samples}
sc_eval:
    ldrb r0, [r7, #0]
    movs r1, r0
    orrs r1, r6
    beq sc_next                 @ no old marks, no hits: the common case
    bl grp
    strb r0, [r7, #0]
    cmp r1, #0
    beq sc_next
    mov r9, r1
sc_next:
    adds r7, #1
    adds r4, #YGROUP
    adds r3, #YGROUP
    adds r5, #GSTRIDE
    mov r0, r8
    subs r0, #1
    mov r8, r0
    beq sc_rowend
    b sc_grp
sc_rowend:
    mov r0, r9
    cmp r0, #0
    beq sc_rownext
    mov r0, r11
    str r0, [sp, #12]
    ldr r1, [sp, #8]
    cmp r1, #255
    bne sc_rownext
    str r0, [sp, #8]
sc_rownext:
    ldr r0, [sp, #0]
    ldr r1, =YROWSTEP
    adds r0, r0, r1
    str r0, [sp, #0]
    ldr r1, =PROWSKIP
    adds r5, r5, r1
    mov r0, r11
    adds r0, #1
    mov r11, r0
    ldr r0, [sp, #4]
    subs r0, #1
    str r0, [sp, #4]
    beq sc_flush
    b sc_row
sc_flush:
    ldr r0, [sp, #8]
    cmp r0, #255
    beq sc_out                  @ nothing changed: no flush
    ldr r2, =rect
    movs r1, #STEP
    muls r0, r1, r0
    strh r0, [r2, #2]           @ y = STEP * first
    ldr r3, [sp, #12]
    muls r3, r1, r3
    subs r3, r3, r0
    adds r3, #2
    strh r3, [r2, #6]           @ h = STEP * (last - first) + 2
    movs r0, #1
    movs r1, r2
    ldr r3, =FLUSH
    bl call_r3                  @ 0x6bbfc(1, rect)
sc_out:
    add sp, #16
    pop {{r2, r3}}
    mov r10, r2
    mov r11, r3
    pop {{r2, r3}}
    mov r8, r2
    mov r9, r3
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

@ ---------------------------------------------------------------- grp(r0 old bits, r6 new bits, r5 group plane byte)
@ -> r0 bits now marked, r1 1 if any plane byte changed. Per sample k (plane byte r5 + boff[k], rows y and y+1):
@ keep: old and new bit, same colour, both bytes already the colour byte -> nothing written.
@ else old bit: nibbles equal to the old colour -> 0; new bit: if both bytes are 0, both get the colour byte.
@ Marks stop at MAXMARKS per pass (pstate+8 counts them): past the cap a hit counts as no hit.
grp:
    push {{r3, r4, r5, r6, r7, lr}}
    sub sp, #12                 @ [0] bits marked, [4] changed, [8] bytes before (row y | row y+1 << 8)
    mov r12, r5
    movs r4, r0
    movs r0, #0
    str r0, [sp, #0]
    str r0, [sp, #4]
    movs r3, #0                 @ k
    ldr r7, =pstate
g_loop:
    movs r0, r4
    orrs r0, r6
    lsrs r0, r3
    lsrs r0, r0, #1
    bcc g_next                  @ neither old nor new mark here
    ldr r1, =boff
    ldrb r0, [r1, r3]
    mov r5, r12
    adds r5, r5, r0
    movs r0, r6
    lsrs r0, r3
    lsrs r0, r0, #1
    bcc g_slow                  @ no new mark: erase
    ldr r0, [r7, #8]
    ldr r2, =MAXMARKS
    cmp r0, r2
    blo g_room
    movs r0, #1                 @ cap reached: this hit counts as none
    lsls r0, r3
    bics r6, r0
    b g_slow
g_room:
    movs r0, r4
    lsrs r0, r3
    lsrs r0, r0, #1
    bcc g_slow                  @ new mark only: paint
    ldrb r0, [r7, #0]
    ldrb r2, [r7, #1]
    cmp r0, r2
    bne g_slow
    ldrb r2, [r7, #2]
    ldrb r0, [r5, #0]
    cmp r0, r2
    bne g_slow
    movs r1, #180
    ldrb r0, [r5, r1]
    cmp r0, r2
    bne g_slow
    bl g_mark                   @ keep: unchanged
    b g_next
g_slow:
    movs r1, #180
    ldrb r0, [r5, #0]
    ldrb r2, [r5, r1]
    lsls r2, r2, #8
    orrs r0, r2
    str r0, [sp, #8]
    movs r0, r4
    lsrs r0, r3
    lsrs r0, r0, #1
    bcc g_paint
    ldrb r2, [r7, #0]
    movs r1, r5
    bl nib_clear
    adds r1, #180
    bl nib_clear
g_paint:
    movs r0, r6
    lsrs r0, r3
    lsrs r0, r0, #1
    bcc g_cmp
    ldrb r0, [r5, #0]
    movs r1, #180
    ldrb r2, [r5, r1]
    orrs r0, r2
    bne g_cmp                   @ an icon/text nibble (or someone's mark): leave it
    ldrb r0, [r7, #2]
    strb r0, [r5, #0]
    strb r0, [r5, r1]
    bl g_mark
g_cmp:
    movs r1, #180
    ldrb r0, [r5, #0]
    ldrb r2, [r5, r1]
    lsls r2, r2, #8
    orrs r0, r2
    ldr r2, [sp, #8]
    cmp r0, r2
    beq g_next
    movs r0, #1
    str r0, [sp, #4]
g_next:
    adds r3, #1
    cmp r3, #8
    blo g_loop
    mov r5, r12
    ldr r0, [sp, #0]
    ldr r1, [sp, #4]
    add sp, #12
    pop {{r3, r4, r5, r6, r7}}
    pop {{r2}}
    bx r2

g_mark:                         @ bit k (r3) -> marked bits ([sp, #0] of grp's frame; bl keeps sp), count += 1. Keeps r3-r7.
    movs r0, #1
    lsls r0, r3
    ldr r2, [sp, #0]
    orrs r2, r0
    str r2, [sp, #0]
    ldr r0, [r7, #8]
    adds r0, #1
    str r0, [r7, #8]
    bx lr

nib_clear:                      @ byte at r1: nibbles equal to r2 -> 0. Clobbers r0; keeps r1-r3.
    push {{r3}}
    ldrb r0, [r1, #0]
    lsrs r3, r0, #4
    cmp r3, r2
    bne nc_lo
    lsls r0, r0, #28
    lsrs r0, r0, #28
nc_lo:
    lsls r3, r0, #28
    lsrs r3, r3, #28
    cmp r3, r2
    bne nc_w
    lsrs r0, r0, #4
    lsls r0, r0, #4
nc_w:
    strb r0, [r1, #0]
    pop {{r3}}
    bx lr

bm_clear:                       @ zero the bitmap (heap_ptr != 0)
    ldr r0, =heap_ptr
    ldr r0, [r0, #0]
    ldr r1, =BMBYTES
    movs r2, #0
bc_l:
    subs r1, #4
    str r2, [r0, r1]
    bne bc_l
    bx lr

@ ---------------------------------------------------------------- init (r0 = service table): one pool block, the bitmap
koma_init:
    push {{r4, lr}}
    sub sp, #8
    ldr r4, [r0, #8]            @ pool_alloc(pool, &p, size, 0)
    ldr r0, [r0, #0x10]
    mov r1, sp
    movs r3, #0
    str r3, [sp, #0]
    ldr r2, =BMBYTES
    bl call_r4
    cmp r0, #0
    bne i_out
    ldr r4, [sp, #0]
    cmp r4, #0
    beq i_out
    lsls r0, r4, #30
    bne i_out                   @ not word aligned: stay off
    ldr r0, =heap_ptr
    str r4, [r0, #0]
    bl bm_clear
i_out:
    add sp, #8
    pop {{r4}}
    pop {{r3}}
    bx r3

call_r4:
    bx r4

.ltorg
.p2align 2
pstate:
    .word 0                     @ [0] colour of the marks in the bitmap (0 none), [1] new colour, [2] new colour byte
    .word 0xffffffff            @ [4] VD count of the last pass
    .word 0                     @ [8] marks this pass (cap MAXMARKS)
heap_ptr:
    .word 0
rect:
    .hword 0, 0, 360, 2
boff:
    .byte {boff}
coltab:
    .byte {coltab}
"""

CHECKS = [
    (0x95AD4, "b5f0487c25008ac2270021002a00d0014c79e005220182c2628162c16301e7f7f003ff982801d01e",
     "0x95ad4 VD-task body up to the hooked bl 0x99a28 at 0x95af4 and its cmp r0,#1"),
    (0x99A28, "4801780047700000", "0x99a28: return byte [0x1f5034] (the wrapped call)"),
    (0x95F68, "1c20f7feffe189f82800d002f7fffdae", "task 0x95f40: calls 0x95ad4 while [0x1f4e76] != 0"),
    (0x959C4, "b500480489802800d0024803f788fb92bc084718001f4e68002cf408",
     "0x959c4: activates HISR VdTaskWait 0x2cf408 (posts the VD-task semaphore)"),
    (0x95A30, "002cf460000959e1002cf40856645461", "0x959fc: HISR VdTaskWait, entry 0x959e1"),
    (0x9DFE8, "f000fa86f7f7fcea", "VD callback 0x9dfb4 calls 0x959c4 every monitor VD"),
    (0x9DFBE, "6a6030016260", "VD callback: VD count [0x1f50f8+0x24] = 0x1f511c += 1"),
    (0x9931C, "60b9", "0x991fa stores the display slot at 0x1f501c"),
    (0x98814, "76f8", "monitor start sets 0x1f502f = 1"),
    (0x2A0E4, "0023a864", "0x2a090 literal: app-state struct (state at +0x10 = 0x23a874)"),
    (0x174C04, "47780000eafbdbfb", "Thumb stub -> 0x6bbfc (OSD flush)"),
    (0xD3D48, "00302d58", "literal of 0xd4018: plane 1 shadow 0x302d58"),
    (0x82868, "002cf640", "0x825c8 literal: live Y plane pointers"),
    (0x92D1C, "480047700023b068", "0x92d1c: zoom struct (step = byte +5 >> 1)"),
]


def make(opts):
    thresh = int(opts.get("threshold", 24))
    step = int(opts.get("step", 4))
    red = int(opts.get("red", 15))
    yellow = int(opts.get("yellow", 10))
    cap = int(opts.get("cap", 1200))
    if not 1 <= thresh <= 255 or step not in (4, 8) or not 1 <= red <= 15 or not 1 <= yellow <= 15 \
            or not 1 <= cap <= 9600:
        raise ValueError("peaking: threshold 1..255, step 4 or 8, red/yellow 1..15, cap 1..9600")
    g = geometry(step)
    src = SRC.format(setb=SETB, liveptr=LIVEPTR, dslot=DSLOT, vdcnt=VDCNT, monrun=MONRUN, zoom=ZOOM, appst=APPST,
                     plane1=PLANE1, flush=FLUSH, stmask=sum(1 << s for s in LIVE_STATES), thresh=thresh, step=step,
                     nopt=len(ROW_OPTIONS), ny=g["ny"], groups=g["groups"], gstride=g["gstride"], ygroup=g["ygroup"],
                     yrowstep=step * YSTRIDE, prowskip=step * OSDROW - g["groups"] * g["gstride"],
                     bmbytes=g["bm_bytes"], cap=cap, ystride=YSTRIDE, samples=_samples(step),
                     boff=", ".join(str(b) for b in g["boff"]), coltab=f"0, {red}, {yellow}, 0")
    checks = [Check(a, bytes.fromhex(h), w) for a, h, w in CHECKS]
    for c in checks:
        assert fw.ram(c.addr, len(c.before)) == c.before, f"peaking check {c.addr:#x} does not match 2.40"
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY,
        options=dict(threshold=thresh, step=step, red=red, yellow=yellow, cap=cap),
        source=src, init="koma_init", checks=checks,
        # smoke_reachable=False: koma smoke's capture model is passive (no VD interrupts), so the VD task never wakes
        hooks=[Hook(SITE, TARGET, "hook_vd", "next_vd", "VD task 0x95ad4, once per monitor VD (task context)",
                    smoke_reachable=False)])
