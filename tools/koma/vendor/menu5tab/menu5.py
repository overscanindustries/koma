# Vendored from the earlier menu-5tab menu5.py (five-tab menu v1). Edits: imports, firmware/data paths, writers removed.
"""5-tab menu module for GR DIGITAL 2.40 (resident card module; offline build only).

Tabs (strip drawn by our own Thumb code in the menu renderer): STILL MOVIE PLAY CUSTOM SETUP.
The firmware has two menu "apps": the mode app (shooting / movie / playback page, firmware tab 1) and the SETUP app
(0x6d050, firmware tab 2). This module keeps both and makes the SETUP app host four virtual tabs:
  * slot = mode slot (STILL for still pages, MOVIE for the movie page, PLAY for the playback page): the stock mode
    page in the mode app, untouched (stock load/commit/actions);
  * every other slot: a display table in the module body, shown through descriptor slot 8 (0x1c23e0) and count
    0x1c23ec, in the SETUP app. Display rows point at stock SETUP rows (source index 1..30) or are placeholders.
Translation layer (stock tables, stock load/commit, stock action code stay authoritative):
  0xe34a8  bl 0x6bdea(2)  -> setup_enter: stock load, cursors stock->display, pick the tab, redraw
  0xe34b4  bl 0xe3698     -> classify: header LEFT/RIGHT cycle tabs (wrap; the mode slot exits to the mode app via
                             the stock LEFT-exit path); on rows, 0x1c23ee is translated display->stock row number
                             around the stock classifier, so FORMAT/LCD/DATE/LANGUAGE/REG and the AUTO POWER OFF,
                             COLOR SPACE and VIDEO OUT special cases fire for the right row
  0xe3550/0xe3564/0xe3602 bl 0x6c4bc -> commit: cursors display->stock, then the stock commit
  0xe3594/0xe35cc  'subs rX,#1; cmp rX,#7' (VOL. SETTINGS sample, row 8) -> bl volN (same flags, translated row)
  0x1a335c/0x1a3398 mode-app LEFT handlers (0x78dbc) -> lefth: on the header, wrap left into the SETUP app
Renderer: 0x14cd98 skip the stock 2-tab highlight box/arrows; 0x14d168/0x14d1bc stock tab labels -> nop;
  0x14d1e8 third label call -> strip (5 labels, highlight box 0x174c0c, styles as stock: 6 focused, 1 active, 4 not).
Data: page tab labels 0x1c1ec8.. -> STILL/MOVIE/PLAY; footer hints 0x1f3478/0x1f35e0 -> CHANGE TAB.
"""
from __future__ import annotations

import struct

from . import mbuild as MB
from . import stage1 as ST  # noqa: F401
from .glyphs import label_table_src, str_src

SETUP = 0x1C224C
VERSION = "V0.1"
ABOUT = [VERSION, "5 TABS", "MODULE", "ABI 1"]  # list values <= 9 chars   # Project Koma: value + about list (caps only)
SLOTS = ["STILL", "MOVIE", "PLAY", "CUSTOM", "SETUP"]
X0, GAP, ADV = 14, 14, 11

# (source SETUP row 1..30 or 0 = placeholder, new label or None = stock label)
TABS = {
    0: [(0, "STILL MODE ONLY")],
    1: [(0, "MOVIE MODE ONLY")],
    2: [(9, "INSTANT REVIEW"), (16, "ENLARGED ICON"), (13, None), (15, None)],
    3: [(26, "SAVE USER MODE"), (27, "START USER MODE"), (21, "ADJ SETTING 1"), (22, "ADJ SETTING 2"),
        (23, "ADJ SETTING 3"), (24, "ADJ SETTING 4"), (25, None), (20, "ZOOM BTN FN SET"), (4, None),
        (30, "INFO DISPLAY"), (14, "SETTINGS WARNING"), (17, "COLOR SPACE"), (28, "RAW+JPEG QUALITY"),
        (19, None), (18, "AF ASSIST LIGHT"), (29, None)],
    4: [(1, None), (2, None), (11, None), (12, None), (10, None), (3, None), (5, None), (6, None),
        (7, "SOUND EFFECTS"), (8, "VOLUME"), (0, "KOMA", ABOUT)],
}


def check_layout():
    used = sorted(r[0] for t in TABS.values() for r in t if r[0])
    assert used == list(range(1, 31)), used
    for t in TABS.values():
        assert len(t) <= 30
        for r in t:
            assert r[1] is None or len(r[1]) <= 16, r[1]


def tables_src():
    out = []
    extra = []
    labels = {}
    for slot, rows in TABS.items():
        words = []
        for j, spec in enumerate(rows):
            src, lab = spec[0], spec[1]
            opts = spec[2] if len(spec) > 2 else None
            if lab is not None:
                name = f"lab_{slot}_{j}"
                labels[name] = lab
                lab_ref = "&" + name
            if src:
                stock = MB.img(SETUP + 12 * (src - 1), 12)
                lab_w, opt_w = struct.unpack(">II", stock[:8])
                tail = stock[8:]
                words.append(f"    .word {lab_ref if lab is not None else hex(lab_w)}, {hex(opt_w)}\n"
                             f"    .byte {tail[0]}, 1, {tail[2]}, 0")
            elif opts:
                for i, o in enumerate(opts):
                    labels[f"opt_{slot}_{j}_{i}"] = o
                arr = f"optarr_{slot}_{j}"
                extra.append(f".p2align 2\n{arr}:\n    .word " + ", ".join(f"&opt_{slot}_{j}_{i}" for i in range(len(opts))))
                words.append(f"    .word {lab_ref}, &{arr}\n    .byte {len(opts)}, 1, 0, 0")
            else:
                words.append(f"    .word {lab_ref}, 0\n    .byte 0, 0, 1, 0")
        out.append(f".p2align 2\ntbl{slot}:\n" + "\n".join(words))
        out.append(f"map{slot}:\n    .byte {', '.join(str(r[0]) for r in rows)}")
    out += extra
    out.append(".p2align 2\nmetatab:\n" + "\n".join(
        f"    .word &tbl{s}, &map{s}, {len(TABS[s])}" for s in range(5)))
    xs, x = [], X0
    for name in SLOTS:
        xs.append(x)
        x += ADV * len(name) + GAP
    assert x - GAP + 4 <= 356, x
    out.append(".p2align 2\nstripdata:\n" + "\n".join(
        f"    .hword {xs[i]}, {ADV * len(SLOTS[i])}\n    .word {'0' if i == 0 else '&strip_' + SLOTS[i]}"
        for i in range(5)))
    for name in SLOTS[1:]:
        out.append(str_src("strip_" + name, name))
    for name, lab in labels.items():
        out.append(".p2align 1\n" + label_table_src(name, lab))
    for name, lab in (("lab_still", "STILL"), ("lab_movie", "MOVIE"), ("lab_play", "PLAY"), ("lab_tab", "CHANGE TAB")):
        out.append(".p2align 1\n" + label_table_src(name, lab))
    return "\n".join(out), xs


CODE = """
init:
    bx lr

vars_ref:
    .p2align 2

@ ---------------------------------------------------------------- helpers
meta:                                   @ r0 = slot -> r0 tbl, r1 map, r2 n (clobbers r3)
    ldr r3, =&metatab
    lsls r1, r0, #2
    lsls r2, r0, #3
    adds r3, r3, r1
    adds r3, r3, r2
    ldr r0, [r3, #0]
    ldr r1, [r3, #4]
    ldr r2, [r3, #8]
    bx lr

mode_slot:                              @ -> r0: 1 movie page, 2 playback page, else 0 (still pages)
    ldr r0, =0x1c23b8
    ldrb r0, [r0]
    cmp r0, #1
    beq 1f
    cmp r0, #5
    beq 2f
    movs r0, #0
    bx lr
1:  movs r0, #1
    bx lr
2:  movs r0, #2
    bx lr

select:                                 @ r0 = slot: v = slot; descriptor slot 8 + count = its display table
    push {r4, lr}
    ldr r1, =&vars
    strb r0, [r1, #0]
    bl meta
    ldr r3, =0x1c23e0
    str r0, [r3]
    ldr r3, =0x1c23ec
    strb r2, [r3]
    pop {r4}
    pop {r3}
    bx r3

redraw:                                 @ same redraw request the UI key handler makes (0x13cd5a):
    push {r4, lr}                       @ 0xd3a68([0x1fb9da], [[0x202a48]], [0x202a4c], 0)
    ldr r3, =0x202a44
    ldr r0, [r3, #4]
    ldr r2, [r3, #8]
    ldrb r1, [r0]
    ldr r0, =0x1fb9da
    ldrb r0, [r0]
    movs r3, #0
    ldr r4, =0xd3a69
    bl call_r4
    pop {r4}
    pop {r3}
    bx r3
call_r4:
    bx r4

xlate:                                  @ r0 = displayed row -> stock SETUP row (31 = no stock row)
    push {r4, lr}
    movs r4, r0
    ldr r1, =0x1c23b9
    ldrb r1, [r1]
    cmp r1, #2
    bne 9f
    cmp r4, #0
    beq 9f
    ldr r1, =&vars
    ldrb r0, [r1, #0]
    bl meta
    cmp r4, r2
    bhi 9f
    subs r3, r4, #1
    ldrb r0, [r1, r3]
    cmp r0, #0
    bne 8f
    movs r0, #31
8:  pop {r4}
    pop {r3}
    bx r3
9:  movs r0, r4
    pop {r4}
    pop {r3}
    bx r3

sync:                                   @ r0 = 0: stock -> display cursors, 1: display -> stock (all tabs)
    push {r4, r5, r6, r7, lr}
    sub sp, #8
    str r0, [sp, #0]
    movs r7, #0
s_slot:
    movs r0, r7
    bl meta
    movs r4, r0
    movs r5, r1
    movs r6, r2
s_row:
    cmp r6, #0
    beq s_next
    subs r6, #1
    ldrb r0, [r5, r6]
    cmp r0, #0
    beq s_row
    cmp r0, #30
    bhi s_row
    subs r0, #1
    movs r1, #12
    muls r0, r1
    ldr r1, =0x1c2255                   @ stock SETUP row 1 cursor (0x1c224c + 9)
    adds r0, r0, r1
    movs r1, #12
    muls r1, r6
    adds r1, r1, r4
    adds r1, #9
    ldr r2, [sp, #0]
    cmp r2, #0
    bne s_out
    ldrb r2, [r0]
    strb r2, [r1]
    b s_row
s_out:
    ldrb r2, [r1]
    strb r2, [r0]
    b s_row
s_next:
    adds r7, #1
    cmp r7, #5
    blo s_slot
    add sp, #8
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3

@ ---------------------------------------------------------------- SETUP-app hooks
setup_enter:                            @ replaces bl 0x6bdea at 0xe34a8 (r0 = 2)
    push {r4, lr}
    ldr r3, =0x6bdeb
    bl call_r3
    movs r4, r0
    movs r0, #0
    bl sync
    ldr r1, =&vars
    ldrb r0, [r1, #1]                   @ pending slot + 1 (set by the mode-app LEFT wrap)
    movs r2, #0
    strb r2, [r1, #1]
    cmp r0, #0
    beq 1f
    subs r0, #1
    b 2f
1:  bl mode_slot                        @ stock RIGHT entry: the tab after the mode slot
    adds r0, #1
    cmp r0, #5
    blo 2f
    movs r0, #0
2:  bl select
    bl redraw
    movs r0, r4
    pop {r4}
    pop {r3}
    bx r3

classify:                               @ replaces bl 0xe3698 at 0xe34b4; r0 = key (caller keeps it in r6)
    push {r4, r5, lr}
    movs r4, r0
    ldr r1, =0x1c23f0
    ldrb r2, [r1]
    cmp r2, #0
    bne c_rows
    cmp r4, #0x10
    beq c_left
    cmp r4, #0x12
    beq c_left
    cmp r4, #0x20
    beq c_right
    cmp r4, #0x22
    beq c_right
    b c_plain
c_left:
    movs r5, #4
    b c_go
c_right:
    movs r5, #1
c_go:
    ldr r1, =&vars
    ldrb r0, [r1, #0]
    adds r5, r5, r0
    cmp r5, #5
    blo 1f
    subs r5, #5
1:  bl mode_slot
    cmp r0, r5
    beq c_leave
    movs r0, r5
    bl select
    bl redraw
    movs r0, #0xff                      @ >= 0xd: e343c does nothing else
    b c_out
c_leave:                                @ target is the mode page: take the stock LEFT exit (classifier 9)
    ldr r1, =&vars
    strb r5, [r1, #0]
    movs r6, #0x10                      @ caller's key register: case 9 sends LEFT to the UI (tab -> 1)
    movs r4, #0x10
c_plain:
    movs r0, r4
    ldr r3, =0xe3699
    bl call_r3
    b c_out
c_rows:
    ldr r1, =0x1c23ee
    ldrb r5, [r1]
    movs r0, r5
    bl xlate
    ldr r1, =0x1c23ee
    strb r0, [r1]
    movs r0, r4
    ldr r3, =0xe3699
    bl call_r3
    ldr r1, =0x1c23ee
    strb r5, [r1]
c_out:
    pop {r4, r5}
    pop {r3}
    bx r3

commit:                                 @ replaces bl 0x6c4bc (3 sites)
    push {r4, lr}
    movs r0, #1
    bl sync
    ldr r3, =0x6c4bd
    bl call_r3
    pop {r4}
    pop {r3}
    bx r3

vol6:                                   @ replaces 'subs r6,#1; cmp r6,#7' at 0xe3594 (flags returned)
    push {r0, r1, r2, r3, lr}
    movs r0, r6
    bl xlate
    subs r6, r0, #1
    cmp r6, #7
    pop {r0, r1, r2, r3, pc}

vol5:                                   @ replaces 'subs r5,#1; cmp r5,#7' at 0xe35cc
    push {r0, r1, r2, r3, lr}
    movs r0, r5
    bl xlate
    subs r5, r0, #1
    cmp r5, #7
    pop {r0, r1, r2, r3, pc}

@ ---------------------------------------------------------------- mode-app LEFT on the header: wrap
lefth:                                  @ handler-table entry for events 0x16/0x24 (stock 0x78dbc)
    push {r4, r5, r6, lr}
    movs r4, r0
    movs r5, r1
    movs r6, r2
    ldr r1, =0x1c23f0
    ldrb r1, [r1]
    cmp r1, #0
    bne l_stock
    ldr r1, =0x1c23b9
    ldrb r1, [r1]
    cmp r1, #1
    bne l_stock
    bl mode_slot
    adds r0, #4
    cmp r0, #5
    blo 1f
    subs r0, #5
1:  adds r0, #1
    ldr r1, =&vars
    strb r0, [r1, #1]
    movs r0, #0x17                      @ run the stock RIGHT handler: UI tab -> 2, enter the SETUP app
    ldr r3, =0x79119
    bl call_r3
    b l_out
l_stock:
    movs r0, r4
    movs r1, r5
    movs r2, r6
    ldr r3, =0x78dbd
    bl call_r3
l_out:
    pop {r4, r5, r6}
    pop {r3}
    bx r3

@ ---------------------------------------------------------------- the tab strip (called from 0x14d118)
strip:                                  @ r0 = layer
    push {r4, r5, r6, r7, lr}
    sub sp, #32                         @ sp+0 element (0x14), sp+0x14 rect (8)
    movs r7, r0
    ldr r1, =0x1c23b9
    ldrb r0, [r1]
    cmp r0, #2
    beq 1f
    bl mode_slot
    b 2f
1:  ldr r1, =&vars
    ldrb r0, [r1, #0]
2:  movs r6, r0                         @ active slot
    ldr r5, =&stripdata
    lsls r0, r6, #3
    adds r0, r5, r0
    ldrh r1, [r0, #0]
    ldrh r2, [r0, #2]
    lsrs r2, r2, #1
    adds r1, r1, r2
    subs r1, #5                         @ 'back to header' marker x for both firmware tabs
    ldr r2, =0x235570
    str r1, [r2]
    ldr r2, =0x235580
    str r1, [r2]
    movs r4, #0
st_loop:
    ldr r5, =&stripdata
    lsls r0, r4, #3
    adds r5, r5, r0
    cmp r4, r6
    bne st_nobox
    ldr r1, =0x1c23f0
    ldrb r0, [r1]
    cmp r0, #0
    bne st_nobox
    mov r1, sp
    ldrh r0, [r5, #0]
    subs r0, #5
    strh r0, [r1, #0x14]
    movs r0, #7
    strh r0, [r1, #0x16]
    ldrh r0, [r5, #2]
    adds r0, #9
    strh r0, [r1, #0x18]
    movs r0, #0x1c
    strh r0, [r1, #0x1a]
    movs r0, r7
    add r1, sp, #0x14
    movs r2, #0xb
    ldr r3, =0x174c0d                   @ fill rect (stock header highlight, colour 0xb)
    bl call_r3
st_nobox:
    mov r1, sp
    ldrh r0, [r5, #0]
    strh r0, [r1, #0]
    movs r0, #12
    cmp r4, r6
    bne 3f
    movs r0, #10
3:  strh r0, [r1, #2]
    movs r0, #1
    strb r0, [r1, #4]
    ldr r0, [r5, #4]
    cmp r0, #0
    bne 4f
    ldr r3, =0x10f115                   @ language index
    bl call_r3
    lsls r2, r0, #2
    ldr r1, =0x1c23b8
    ldrb r0, [r1, #0]
    lsls r0, r0, #2
    ldr r3, [r1, #4]                    @ tab label table (0x1c1ec4): [page + 1]
    adds r3, r3, r0
    ldr r3, [r3, #4]
    ldr r0, [r3, r2]
4:  mov r1, sp
    str r0, [r1, #8]
    movs r0, #4
    cmp r4, r6
    bne 5f
    movs r0, #1
    ldr r2, =0x1c23f0
    ldrb r2, [r2]
    cmp r2, #0
    bne 5f
    movs r0, #6
5:  strh r0, [r1, #0xc]
    movs r0, #0x10
    strh r0, [r1, #0xe]
    strh r0, [r1, #0x10]
    strh r0, [r1, #0x12]
    movs r0, r7
    ldr r3, =0x174c1d                   @ draw element (thunk of 0x6ba80)
    bl call_r3
    adds r4, #1
    cmp r4, #5
    blo st_loop
    add sp, #32
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3

.p2align 2
.ltorg
.p2align 2
vars:
    .byte 0, 0, 0, 0
"""


def source():
    check_layout()
    data, xs = tables_src()
    return CODE + "\n" + data + "\n", xs


def patches():
    P, B, R = MB.PTR_K, MB.BL_K, MB.RAW_K
    bl = lambda s, t: MB.thumb_bl(s, t)  # noqa: E731
    h = bytes.fromhex
    out = [
        (0xE34A8, B, "setup_enter", bl(0xE34A8, 0x6BDEA)),
        (0xE34B4, B, "classify", bl(0xE34B4, 0xE3698)),
        (0xE3550, B, "commit", bl(0xE3550, 0x6C4BC)),
        (0xE3564, B, "commit", bl(0xE3564, 0x6C4BC)),
        (0xE3602, B, "commit", bl(0xE3602, 0x6C4BC)),
        (0xE3594, B, "vol6", h("3e012e07")),
        (0xE35CC, B, "vol5", h("3d012d07")),
        (0x1A335C, P, ("lefth", 1), h("00078dbd")),
        (0x1A3398, P, ("lefth", 1), h("00078dbd")),
        (0x14CD98, R, 0xE0517868, h("d1517868")),
        (0x14D168, R, 0x46C046C0, bl(0x14D168, 0x174C1C)),
        (0x14D1BC, R, 0x46C046C0, bl(0x14D1BC, 0x174C1C)),
        (0x14D1E8, B, "strip", bl(0x14D1E8, 0x174C1C)),
        (0x1F3478, P, ("lab_tab", 0), h("00190510")),
        (0x1F35E0, P, ("lab_tab", 0), h("0019057c")),
    ]
    for page in range(8):
        a = 0x1C1EC8 + 4 * page
        name = {1: "lab_movie", 5: "lab_play"}.get(page, "lab_still")
        out.append((a, P, (name, 0), MB.img(a, 4)))
    return out
