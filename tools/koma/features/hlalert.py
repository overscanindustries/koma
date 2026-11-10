"""hlalert: Highlight Alert (blinkies) for the GR DIGITAL 2.40, three display paths, settings byte 0x1b8b9.

Histogram-mode blinkies at 0x2a0d8, extended to the normal live view and playback review, as the GR II has it.

Paths (all draw only into transparent OSD pixels and undo only their own marks):
  hist  0x2a0d8 `bl 0x82e98` (OSD task 0x6a770, element 4, DISP 5 histogram): stock bars, then blink. Plane 1.
  live  0x95b04 `bl 0x6e91c` (live-view monitor loop, every 4th frame, any DISP): returns the DISP mode unchanged; for
        DISP != 5 in a shooting app state (2/3/5/10/12/13, the set 0x2a090 uses) it blinks every DIV-th call. Plane 1.
        The OSD task is not usable here: its cyclic timer only runs while some OSD element is enabled (0x6a61c starts
        it on the first enable, 0x6a6a0 stops it on the last disable), and normal live view enables none.
  play  0x2a100 `bl 0x837f0` (OSD task, element 14 = plane-2 histogram, enabled at 0x837d6 by the widget histogram
        0x82f94, called from the widget dispatcher 0x13d72e): blinks from the playback display image (the buffer the
        stock playback histogram 0x82892 reads: *(0x2017fc + 4*(*(0x201804) & 1)), 704 B stride x 480 lines). Plane 2.
        Only outside the shooting app states.

Footprint: no shipped map. Init allocates one 4,200 B block from the pool (two x tables computed at install + a coarse
2x2-pixel map, 180 x 120 bits) and the resident code is about 1 KB. Alloc failure = feature stays off.
The setting byte 0x1b8b9 is read at every call: 1 = on, anything else = off (default 0).
"""
from __future__ import annotations

from ..feature import Check, Feature, Hook
from .. import firmware as fw

NAME = "hlalert"
SUMMARY = "Highlight Alert blinkies: histogram DISP, normal live view and playback (setting 0x1b8b9)"
OPTIONS = {"threshold": "Y at or above which a pixel is marked (default 250; 233 if the live Y is video range)",
           "colour": "OSD palette index of the marks, 1..15 (default 15 = red in the shooting palette)",
           "div": "live-view path: act on every Nth monitor call (calls come every 4th frame). Default 2",
           "lstep": "live lines per OSD line, Q8 (default 256 = 240-line live frame; 128 if it is 120 lines)"}

SETB = 0x1B8B9
ROWS = [dict(id="hilite", label="HIGHLIGHT ALRT", options=["OFF", "ON"], byte=SETB, default=0, group="display")]

PLANE1 = 0x302D58                 # 0xd4018(p) = 0x302d58 + (p-1) * 0xa8c0
PLANE2 = PLANE1 + 0xA8C0
LIVEPTR, TICK, ZOOM, APPST = 0x2CF640, 0x1BEDDC, 0x23B068, 0x23A874
PBTAB = 0x2017FC                  # 0xf1d34: playback display buffer = [PBTAB + 4 * ([PBTAB + 8] & 1)]
FLUSH = 0x174C05                  # Thumb stub -> ARM 0x6bbfc(plane, rect)
LIVE_STATES = (2, 3, 5, 10, 12, 13)
SITE_HIST, SITE_MON, SITE_PLAY = 0x2A0D8, 0x95B04, 0x2A100
XT_BYTES, MAPROW, MAPROWS = 720, 23, 120
MAP_OFF = 2 * XT_BYTES
MAP_BYTES = MAPROW * MAPROWS
HEAP_BYTES = MAP_OFF + MAP_BYTES  # 4200
LIVE_STRIDE, PLAY_STRIDE, PLAY_LSTEP = 0x2A0, 704, 512


def xtab_live():
    return [2 * ((8 * x) // 9) for x in range(360)]


def xtab_play():
    return [(88 * x) // 45 for x in range(360)]


SRC = """
.equ SETB, 0x{setb:x}
.equ TICK, 0x{tick:x}
.equ LIVEPTR, 0x{liveptr:x}
.equ ZOOM, 0x{zoom:x}
.equ APPST, 0x{appst:x}
.equ PBTAB, 0x{pbtab:x}
.equ FLUSH, 0x{flush:x}
.equ THRESH, {thresh}
.equ COL, {col}
.equ COLB, {colb}
.equ DIV, {div}
.equ MAPROW, {maprow}
.equ MAPOFF, {mapoff}
.equ MAPBYTES, {mapbytes}
.equ HEAPBYTES, {heapbytes}
.equ XTBYTES, {xtbytes}
.equ STMASK, 0x{stmask:x}

@ ---------------------------------------------------------------- hooks
hook_hist:                      @ 0x2a0d8, OSD task, DISP 5: stock bars first
    push {{r4, lr}}
    ldr r3, =next_hist
    bl call_r3
    bl tick_fresh
    movs r4, r0
    bl live_img
    movs r1, r0
    movs r2, r4
    ldr r0, =ctx_hist
    bl blink
    pop {{r4}}
    pop {{r3}}
    bx r3

hook_mon:                       @ 0x95b04, monitor loop: r0 = DISP mode, returned unchanged
    push {{r4, lr}}
    ldr r3, =next_mon
    bl call_r3
    movs r4, r0
    cmp r0, #5
    beq hm_out                  @ histogram mode: the hist path owns it
    bl app_live
    cmp r0, #0
    beq hm_drop
    ldr r2, =hstate
    ldrb r0, [r2, #3]
    adds r0, #1
    cmp r0, #DIV
    blo hm_cnt
    movs r0, #0
    strb r0, [r2, #3]
    bl live_img
    movs r1, r0
    movs r2, #1
    ldr r0, =ctx_live
    bl blink
    b hm_out
hm_cnt:
    strb r0, [r2, #3]
    b hm_out
hm_drop:                        @ not a shooting screen: forget our marks, never touch the plane
    ldr r0, =ctx_live
    bl drop_if_owner
hm_out:
    movs r0, r4
    pop {{r4}}
    pop {{r3}}
    bx r3

hook_play:                      @ 0x2a100, OSD task, element 14 (plane-2 histogram)
    push {{r4, lr}}
    ldr r3, =next_play
    bl call_r3
    bl tick_fresh
    movs r4, r0
    bl app_live
    movs r1, #0
    cmp r0, #0
    bne hp_go                   @ shooting state: no playback marks (erase any)
    bl play_img
    movs r1, r0
hp_go:
    movs r2, r4
    ldr r0, =ctx_play
    bl blink
    pop {{r4}}
    pop {{r3}}
    bx r3

call_r3:
    bx r3
call_r4:
    bx r4

@ ---------------------------------------------------------------- leaves
tick_fresh:                     @ r0 = 1 if the OSD tick advanced by exactly 1 since the last OSD-task call
    ldr r2, =TICK
    ldr r0, [r2, #0]
    ldr r2, =hstate
    ldr r1, [r2, #4]
    str r0, [r2, #4]
    subs r0, r0, r1
    cmp r0, #1
    beq tf_yes
    movs r0, #0
tf_yes:
    bx lr

live_img:                       @ r0 = live Y base, or 0 (digital zoom active / no frame)
    ldr r2, =ZOOM
    ldrb r0, [r2, #5]
    lsrs r0, r0, #1
    bne li_no
    ldr r2, =LIVEPTR
    ldr r0, [r2, #0]
    bx lr
li_no:
    movs r0, #0
    bx lr

play_img:                       @ r0 = playback display buffer (0xf1d34)
    ldr r2, =PBTAB
    ldr r0, [r2, #8]
    lsls r0, r0, #31
    lsrs r0, r0, #29
    ldr r0, [r2, r0]
    bx lr

app_live:                       @ r0 = 1 if the app state is one 0x2a090 treats as shooting
    ldr r2, =APPST
    ldr r1, [r2, #0]
    movs r0, #0
    cmp r1, #16
    bhs al_out
    ldr r0, =STMASK
    lsrs r0, r1
    movs r1, #1
    ands r0, r1
al_out:
    bx lr

drop_if_owner:                  @ r0 = ctx: if its marks are the current ones, forget them (plane untouched)
    ldr r2, =hstate
    ldrb r1, [r2, #2]
    ldrb r0, [r0, #11]
    cmp r0, r1
    bne do_out
    movs r0, #0
    strb r0, [r2, #2]           @ owner 0: the next blink of any path starts clean
    strb r0, [r2, #1]
    strb r0, [r2, #0]
do_out:
    bx lr

@ ---------------------------------------------------------------- blink(r0 ctx, r1 image or 0, r2 fresh)
blink:
    push {{r4, r5, r6, r7, lr}}
    movs r4, r0
    movs r5, r1
    ldr r7, =hstate
    ldr r0, =heap_ptr
    ldr r0, [r0, #0]
    cmp r0, #0
    beq b_out                   @ no map (alloc failed): feature off
    ldrb r0, [r7, #2]
    ldrb r1, [r4, #11]
    cmp r0, r1
    beq b_same
    movs r2, #0                 @ another path's marks: that screen is gone
b_same:
    strb r1, [r7, #2]
    cmp r2, #0
    bne b_fresh
    bl bm_clear                 @ stale: OSD redrawn since, forget the map, plane untouched
    movs r0, #0
    strb r0, [r7, #0]
    strb r0, [r7, #1]
b_fresh:
    cmp r5, #0
    beq b_off
    ldr r0, =SETB
    ldrb r0, [r0, #0]
    cmp r0, #1
    bne b_off
    ldrb r0, [r7, #0]
    movs r1, #1
    eors r1, r0
    strb r1, [r7, #0]           @ toggle phase
    cmp r0, #0
    bne b_erase
    movs r0, r4
    movs r1, r5
    bl paint
    cmp r0, #0
    beq b_out
    movs r0, #1
    strb r0, [r7, #1]
    b b_flush
b_off:
    ldrb r0, [r7, #1]
    cmp r0, #0
    beq b_out
b_erase:
    movs r0, r4
    bl erase
    movs r1, #0
    strb r1, [r7, #1]
    cmp r0, #0
    beq b_out
b_flush:
    ldrb r0, [r4, #10]          @ plane number
    ldr r1, =rect
    ldr r3, =FLUSH
    bl call_r3                  @ 0x6bbfc(plane, {{0, 0, 360, 240}})
b_out:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

@ ---------------------------------------------------------------- paint(r0 ctx, r1 image) -> r0 blocks painted
@ 2x2 OSD-pixel blocks (180 x 120). A block is painted only if all 4 nibbles are transparent and any of its 4 sampled
@ image pixels is >= THRESH. r3 block x, r4 image row A, r5 OSD row, r6 map row, r7 x table, r8 image base,
@ r9 image row B - row A.
paint:
    push {{r4, r5, r6, r7, lr}}
    mov r2, r8
    mov r3, r9
    push {{r2, r3}}
    sub sp, #12                 @ [0] block row, [4] painted, [8] ctx
    str r0, [sp, #8]
    mov r8, r1
    movs r1, #0
    str r1, [sp, #0]
    str r1, [sp, #4]
    ldr r5, [r0, #0]
    ldr r6, =heap_ptr
    ldr r6, [r6, #0]
    ldrh r1, [r0, #4]
    adds r7, r6, r1
    ldr r1, =MAPOFF
    adds r6, r6, r1
p_row:
    ldr r2, [sp, #8]
    ldrh r3, [r2, #8]           @ image lines per OSD line, Q8
    ldr r0, [sp, #0]
    lsls r1, r0, #1
    muls r1, r3, r1             @ yA * L
    adds r0, r1, r3             @ yB * L
    lsrs r1, r1, #8
    lsrs r0, r0, #8
    subs r0, r0, r1
    ldrh r3, [r2, #6]           @ stride
    muls r0, r3, r0
    mov r9, r0
    muls r1, r3, r1
    mov r4, r8
    adds r4, r4, r1
    movs r3, #179
p_blk:
    adds r1, r5, r3
    ldrb r0, [r1, #0]
    adds r1, #180
    ldrb r2, [r1, #0]
    orrs r0, r2
    bne p_next                  @ an icon/text nibble in the block: leave it
    lsls r0, r3, #2
    ldrh r1, [r7, r0]
    adds r0, #2
    ldrh r2, [r7, r0]
    ldrb r0, [r4, r1]
    cmp r0, #THRESH
    bhs p_hit
    ldrb r0, [r4, r2]
    cmp r0, #THRESH
    bhs p_hit
    add r1, r9
    ldrb r0, [r4, r1]
    cmp r0, #THRESH
    bhs p_hit
    add r2, r9
    ldrb r0, [r4, r2]
    cmp r0, #THRESH
    bhs p_hit
p_next:
    subs r3, #1
    bpl p_blk
    adds r5, #180
    adds r5, #180
    adds r6, #MAPROW
    ldr r0, [sp, #0]
    adds r0, #1
    str r0, [sp, #0]
    cmp r0, #120
    blo p_row
    ldr r0, [sp, #4]
    add sp, #12
    pop {{r2, r3}}
    mov r8, r2
    mov r9, r3
    pop {{r4, r5, r6, r7}}
    pop {{r1}}
    bx r1
p_hit:
    adds r1, r5, r3
    movs r0, #COLB
    strb r0, [r1, #0]
    adds r1, #180
    strb r0, [r1, #0]
    movs r1, #7
    ands r1, r3
    movs r0, #1
    lsls r0, r1
    lsrs r2, r3, #3
    ldrb r1, [r6, r2]
    orrs r1, r0
    strb r1, [r6, r2]
    ldr r0, [sp, #4]
    adds r0, #1
    str r0, [sp, #4]
    b p_next

@ ---------------------------------------------------------------- erase(r0 ctx) -> r0 blocks erased
@ Every mapped block: each of its 4 nibbles that still holds COL goes back to 0. Clears the map.
erase:
    push {{r4, r5, r6, r7, lr}}
    ldr r5, [r0, #0]
    ldr r6, =heap_ptr
    ldr r6, [r6, #0]
    ldr r1, =MAPOFF
    adds r6, r6, r1
    movs r7, #0
    movs r4, #0
e_row:
    movs r3, #0
e_byte:
    ldrb r0, [r6, r3]
    cmp r0, #0
    bne e_bits
e_nb:
    adds r3, #1
    cmp r3, #MAPROW
    blo e_byte
    adds r6, #MAPROW
    adds r5, #180
    adds r5, #180
    adds r4, #1
    cmp r4, #120
    blo e_row
    movs r0, r7
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
e_bits:
    movs r1, #0
    strb r1, [r6, r3]
    push {{r3}}
    lsls r2, r3, #3             @ block x of bit 0
    movs r3, r0
e_bit:
    cmp r3, #0
    beq e_bdone
    lsrs r3, r3, #1
    bcc e_bnext
    adds r1, r5, r2
    bl e_fix
    adds r1, #180
    bl e_fix
    adds r7, #1
e_bnext:
    adds r2, #1
    b e_bit
e_bdone:
    pop {{r3}}
    b e_nb

e_fix:                          @ byte at r1: nibbles equal to COL -> 0. Keeps r1-r3.
    push {{r2}}
    ldrb r0, [r1, #0]
    lsrs r2, r0, #4
    cmp r2, #COL
    bne ef_lo
    lsls r0, r0, #28
    lsrs r0, r0, #28
ef_lo:
    lsls r2, r0, #28
    lsrs r2, r2, #28
    cmp r2, #COL
    bne ef_w
    lsrs r0, r0, #4
    lsls r0, r0, #4
ef_w:
    strb r0, [r1, #0]
    pop {{r2}}
    bx lr

bm_clear:                       @ zero the map (heap_ptr != 0)
    ldr r0, =heap_ptr
    ldr r0, [r0, #0]
    ldr r1, =MAPOFF
    adds r0, r0, r1
    ldr r1, =MAPBYTES
    movs r2, #0
bc_l:
    subs r1, #4
    str r2, [r0, r1]
    bne bc_l
    bx lr

@ ---------------------------------------------------------------- init (r0 = service table)
@ One pool block: [0, 720) live x table 2*floor(8x/9), [720, 1440) playback x table floor(88x/45), then the map.
koma_init:
    push {{r4, lr}}
    sub sp, #8
    ldr r4, [r0, #8]            @ pool_alloc(pool, &p, size, 0)
    ldr r0, [r0, #0x10]
    mov r1, sp
    movs r3, #0
    str r3, [sp, #0]
    ldr r2, =HEAPBYTES
    bl call_r4
    cmp r0, #0
    bne i_out
    ldr r4, [sp, #0]
    cmp r4, #0
    beq i_out
    lsls r0, r4, #30
    bne i_out                   @ not word aligned: stay off
    movs r0, r4
    movs r1, #1
    movs r2, #8
    movs r3, #9
    bl fill_xtab
    ldr r0, =XTBYTES
    adds r0, r4, r0
    movs r1, #0
    movs r2, #88
    movs r3, #45
    bl fill_xtab
    ldr r0, =heap_ptr
    str r4, [r0, #0]
    bl bm_clear
i_out:
    add sp, #8
    pop {{r4}}
    pop {{r3}}
    bx r3

fill_xtab:                      @ r0 dst, r1 shift, r2 num, r3 den: 360 x u16 (floor(num*x/den) << shift)
    push {{r4, r5, r6, r7}}
    movs r4, #0
    movs r5, #0
    ldr r6, =360
fx_l:
    movs r7, r4
    lsls r7, r1
    strh r7, [r0, #0]
    adds r0, #2
    adds r5, r5, r2
fx_c:
    cmp r5, r3
    blo fx_n
    subs r5, r5, r3
    adds r4, #1
    b fx_c
fx_n:
    subs r6, #1
    bne fx_l
    pop {{r4, r5, r6, r7}}
    bx lr

.ltorg
.p2align 2
hstate:
    .word 0, 0                  @ [0] phase, [1] marks shown, [2] owner ctx id, [3] monitor divider, [4] last tick
heap_ptr:
    .word 0
rect:
    .hword 0, 0, 360, 240
@ ctx: +0 plane shadow, +4 x-table offset, +6 image stride, +8 image lines per OSD line (Q8), +10 plane, +11 id
ctx_hist:
    .word 0x{plane1:x}
    .hword 0, 0x{lstride:x}, {lstep}
    .byte 1, 1
ctx_live:
    .word 0x{plane1:x}
    .hword 0, 0x{lstride:x}, {lstep}
    .byte 1, 2
ctx_play:
    .word 0x{plane2:x}
    .hword {xtbytes}, {pstride}, {plstep}
    .byte 2, 3
"""

# (addr, stock byte count or short hex, why), resolved by fw.pinned
CHECKS = [
    (0x2A0B8, 28,
     "0x2a090: DISP==5 && 0x1befe3 gate and histogram normalise before the hooked bl at 0x2a0d8"),
    (0x2A0E4, "0023a864", "0x2a090 literal: app-state struct (state at +0x10 = 0x23a874)"),
    (0x2A0E8, 24, "0x2a0e8: element-14 refresh before bl 0x837f0"),
    (0x174C04, 8, "Thumb stub -> 0x6bbfc (OSD flush)"),
    (0xD4018, 40,
     "0xd4018: plane p shadow = 0x302d58 + (p-1)*0xa8c0"),
    (0xD3D48, "00302d58", "literal of 0xd4018"),
    (0x6A78C, 16, "0x6a770: tick [0x1bed60+0x7c] += 1 per OSD-task wake"),
    (0x6A508, "001bed60", "literal of 0x6a770"),
    (0x82868, "002cf640", "0x825c8 literal: live Y plane pointer"),
    (0x92D1C, 8, "0x92d1c: zoom struct (step = byte +5 >> 1)"),
    (0x95AFC, 8, "monitor loop: every-4th-frame gate before the hooked bl 0x6e91c at 0x95b04"),
    (0x95B08, "2805d003", "monitor loop: DISP==5 test of the hooked call's result"),
    (0xF1D34, 20, "0xf1d34: playback display buffer getter + literal"),
    (0x8289A, 10, "0x82892: playback histogram reads 0x2c0 (704) B per line ..."),
    (0x828A4, "20ff", "... and 0xff + 0xe1 = 480 lines"),
]
CHECKS_SHA256 = "f8818a3a25e0d55c6674d1ba5bbf2b9954a0438609319504fad82aebf0e58fd6"


def make(opts):
    thresh = int(opts.get("threshold", 250))
    col = int(opts.get("colour", 15))
    div = int(opts.get("div", 2))
    lstep = int(opts.get("lstep", 256))
    if not 1 <= thresh <= 255 or not 1 <= col <= 15 or not 1 <= div <= 255 or lstep not in (128, 256):
        raise ValueError("hlalert: threshold 1..255, colour 1..15, div 1..255, lstep 128 or 256")
    src = SRC.format(setb=SETB, tick=TICK, liveptr=LIVEPTR, zoom=ZOOM, appst=APPST, pbtab=PBTAB, flush=FLUSH,
                     thresh=thresh, col=col, colb=col * 0x11, div=div, maprow=MAPROW, mapoff=MAP_OFF,
                     mapbytes=MAP_BYTES, heapbytes=HEAP_BYTES, xtbytes=XT_BYTES,
                     stmask=sum(1 << s for s in LIVE_STATES), plane1=PLANE1, plane2=PLANE2, lstride=LIVE_STRIDE,
                     lstep=lstep, pstride=PLAY_STRIDE, plstep=PLAY_LSTEP)
    checks = [Check(a, b, w) for a, b, w in fw.pinned(CHECKS, CHECKS_SHA256)]
    for c in checks:
        assert fw.ram(c.addr, len(c.before)) == c.before, f"hlalert check {c.addr:#x} does not match 2.40"
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY,
        options=dict(threshold=thresh, colour=col, div=div, lstep=lstep),
        source=src, init="koma_init", checks=checks,
        # smoke_reachable=False: the smoke never enters DISP 5 or playback, and the model has no monitor frames
        hooks=[Hook(SITE_HIST, 0x82E98, "hook_hist", "next_hist", "histogram element refresh (DISP 5), OSD task",
                    smoke_reachable=False),
               Hook(SITE_MON, 0x6E91C, "hook_mon", "next_mon", "live-view monitor loop, DISP getter, every 4th frame",
                    smoke_reachable=False),
               Hook(SITE_PLAY, 0x837F0, "hook_play", "next_play", "plane-2 histogram element 14 refresh, OSD task",
                    smoke_reachable=False)],
        smoke=smoke_checks)


def smoke_setup():
    return {SETB: 1}


def smoke_checks(obs):
    return [("setting byte on for the shot", obs.ram8(SETB) == 1, f"0x1b8b9={obs.ram8(SETB)}")]
