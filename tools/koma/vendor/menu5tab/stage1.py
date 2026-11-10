# Vendored from analysis/menu-5tab/stage1.py (stage-1 snap-distance row). Edits: imports, firmware/data paths, writers removed.
"""Stage 1 module (offline build): ONE new row on the still (AUTO) shooting page, persisted in the settings tail.

Row 15 "SNAP FOCUS DIST" (1M 1.5M 2M 2.5M 3.5M 5M INF) on still page table 0x1c1ee8 (14 -> 15 rows).
Setting byte 0x1b1c1 (marker 0x1b1c0 = 'U'), in the free tail of the settings block that the stock power-off save
0x4949c checksums and writes (emu-frontier-2 exp5). The module's init does the 'U'-group migration at load time
(module runs at 0x281ee, after the settings load). This stage stores the value only: no consumer is wired (the
SNAP distance word is a separate patch, module-loader snap15).

Mechanism (as menu-redesign proto, now as a resident card module): the 15-row table lives in the module body;
the two table literals 0x5ae70/0x5b6ec and descriptor slot 0x1c23c0 are pointed at it (PTR patches), the page-0
count byte 0x1c23e4 becomes 15, and the still load BLs 0x5b394/0x5b446 and commit BL 0x58882 are redirected to
stubs that call the stock function and then copy settings byte <-> row cursor (clamped to 1..7).
"""
from __future__ import annotations

import struct

from . import mbuild as MB
from .glyphs import enc, label_table_src

STILL, STILL_N = 0x1C1EE8, 14
MARK, VAL = 0x1B1C0, 0x1B1C1
OPTS = ["1M", "1.5M", "2M", "2.5M", "3.5M", "5M", "∞"]
DEFAULT = 4                                  # 2.5M


def source():
    rows = MB.img(STILL, 12 * STILL_N)
    row_words = ", ".join(f"0x{w:08x}" for (w,) in struct.iter_unpack(">I", rows))
    opt_tables = "\n".join(label_table_src(f"o{i}", o) for i, o in enumerate(OPTS))
    return f"""
init:                                   @ 'U' group migration (runs once per boot, before patches)
    ldr r0, ={MARK:#x}
    ldrb r1, [r0]
    cmp r1, #0x55
    beq 1f
    movs r1, #0x55
    strb r1, [r0]
    movs r1, #{DEFAULT}
    strb r1, [r0, #1]
1:  bx lr

clamp:                                  @ r1 -> r1 in 1..7 else default
    subs r2, r1, #1
    cmp r2, #{len(OPTS) - 1}
    bls 2f
    movs r1, #{DEFAULT}
2:  bx lr

load_stub:                              @ replaces bl 0x5aaf0 (still page load)
    push {{r4, lr}}
    ldr r3, =0x5aaf1
    bl call_r3
    ldr r0, ={VAL:#x}
    ldrb r1, [r0]
    bl clamp
    ldr r0, =&newrow
    strb r1, [r0, #9]
    pop {{r4}}
    pop {{r3}}
    bx r3

commit_stub:                            @ replaces bl 0x5b4e4 (still page commit), r0 = its argument
    push {{r4, lr}}
    ldr r3, =0x5b4e5
    bl call_r3
    ldr r0, =&newrow
    ldrb r1, [r0, #9]
    bl clamp
    strb r1, [r0, #9]
    ldr r0, ={VAL:#x}
    strb r1, [r0]
    pop {{r4}}
    pop {{r3}}
    bx r3

.p2align 2
table:
    .word {row_words}
newrow:
    .word &lab_snap, &optarr
    .byte {len(OPTS)}, {DEFAULT}, 0, 0
optarr:
    .word {", ".join(f"&o{i}" for i in range(len(OPTS)))}
{label_table_src("lab_snap", "SNAP FOCUS DIST")}
{opt_tables}
"""


def patches():
    P, B, R = MB.PTR_K, MB.BL_K, MB.RAW_K
    lits = []
    for a in (0x5AE70, 0x5B6EC):
        v = int.from_bytes(MB.img(a, 4), "big")
        assert STILL - 128 <= v < STILL + 12 * STILL_N
        lits.append((a, v - STILL))
    out = []
    for a, d in lits:
        out.append((a, P, ("table", d), None))
    out += [(0x1C23C0, P, ("table", 0), struct.pack(">I", STILL)),
            (0x1C23E4, R, int.from_bytes(bytes([15]) + MB.img(0x1C23E5, 3), "big"), bytes([14]) + MB.img(0x1C23E5, 3)),
            (0x5B394, B, "load_stub", MB.thumb_bl(0x5B394, 0x5AAF0)),
            (0x5B446, B, "load_stub", MB.thumb_bl(0x5B446, 0x5AAF0)),
            (0x58882, B, "commit_stub", MB.thumb_bl(0x58882, 0x5B4E4))]
    return out


def resolve(patch_list, syms):
    res = []
    for site, kind, arg, before in patch_list:
        if isinstance(arg, tuple):
            arg = syms[arg[0]] + arg[1]
        res.append((site, kind, arg, before))
    return res
