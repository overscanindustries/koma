# Vendored from analysis/recipe-slots/build.py (recipe-slots module builder). Edits: imports, firmware/data paths, writers removed.
"""Recipe slots for GR DIGITAL 2.40: build a resident GRMOD card module. Offline: writes bytes, touches no card/camera.

One module = installer + per-shot hook + slot defaults + tone curves (see README.md for the design and evidence).
  * Persistent slot data: settings tail 0x1b800..0x1b8af (inside the 4 KB block 0x1b000 that 0x4949c saves whole and
    0x3d9b8 sums), marker 'RCP1'. Active slot byte 0x1b806 (0 = off, 1..5).
  * Install (boot, MainCtrl, via the loader): ABI/version check, then ALL before-bytes verified (hook site, the two
    firmware functions called, the CC2 selector-cache compare and its literal) -> nothing is written if any differs.
    Then tail seed, recall of the active slot into the stock settings fields, then (IRQ/FIQ masked) the 4-byte
    `bl 0x8ff0e` at 0x96436 becomes `bl <hook in pool>`. Returns 1 (resident) only after patching; 0 otherwise.
  * Hook (every still develop, 0x96350): stock 0x8ff0e first, then if the active slot still matches IMG SET:
    chroma 0x12018004, MatR/G/B 0x12018104..10c (& 0xbfbfbfbf), UV 0x120180d8 (& 0x3f3f), and a CC2 tone LUT upload
    0x16d31c(0, 0x1000, curve-in-pool) followed by invalidating CC2 selector cache byte 0x1f4db3 (= 0xff).

"""
from __future__ import annotations

import csv
import hashlib
import json
import struct

from .. import firmware as fw
from . import grmod
from .asm_legacy import assemble

RAW = fw.payload()
LOAD = 0x1C000

SITE = 0x96436                  # bl 0x8ff0e inside still develop 0x96350
TAIL = 0x1B800                  # settings-block tail, see README §1
MAGIC = 0x52435031              # 'RCP1'
NSLOTS, SLOTSZ, T_SLOTS = 5, 32, 0x10
TAIL_LEN = T_SLOTS + NSLOTS * SLOTSZ
CC2_CACHE = 0x1F4DB3            # [0x1f4da8 + 0xb], compared at 0x9031e
F_VALID, F_CURVE, F_CHROMA, F_MATRIX, F_UV = 1, 2, 4, 8, 16

# IMG SET -> settings offsets (from 0x1b000) of (contrast, sharpness, depth); 0 = mode has no such field.
FIELDS = {0: (0, 0, 0), 1: (0, 0, 0), 2: (0, 0, 0), 3: (0x197, 0x198, 0), 4: (0x031, 0x032, 0x033),
          5: (0x034, 0x035, 0x036), 6: (0x1A7, 0x1A8, 0)}


def ram(addr, n):
    return RAW[addr - LOAD:addr - LOAD + n]


# Every byte the module depends on. Any mismatch at runtime -> module applies nothing and is freed.
CHECKS = [
    (0x96430, "1df830197880f7f9fd6a", "hook site: cp+0x22 load + bl 0x8ff0e (stock 0x96350)"),
    (0x8FF0E, "b5884929", "prologue of 0x8ff0e (mono/fixed-chroma writer the hook still calls)"),
    (0x16D31C, "b5f0b08427001c0c", "prologue of 0x16d31c (CC2 LUT SDL upload the hook calls)"),
    (0x9031A, "4c0d21007ae042b8d103", "CC2 capture selector-cache compare ldrb [r4,#0xb] (0x90278)"),
    (0x90350, "001f4da8", "literal r4 = 0x1f4da8 -> CC2 cache byte 0x1f4db3"),
]
for a, h, _ in CHECKS:
    assert ram(a, len(h) // 2).hex() == h, hex(a)


def r3_curve():
    rows = list(csv.DictReader((fw.DATA / "tone-pinned.csv").open()))   # stock-c5 column not shipped
    v = [int(r["sib-R3-18c3c4"]) for r in rows]
    return v


def lut_csv(p):
    rows = list(csv.DictReader((fw.DATA / p).open()))
    return [int(r["value"]) for r in rows]


def curve_bytes(v):
    assert len(v) == 4096 and all(0 <= x <= 1023 for x in v) and all(a <= b for a, b in zip(v, v[1:]))
    return struct.pack(">4096H", *v)


CURVES = [
    ("sib-R3-18c3c4", "data/tone-pinned.csv column sib-R3-18c3c4", r3_curve()),
    ("fuji-classic-chrome-pinned", "data/colour-recipes/fuji-classic-chrome-pinned.csv",
     lut_csv("colour-recipes/fuji-classic-chrome-pinned.csv")),
]
CURVE_BYTES = [curve_bytes(v) for _, _, v in CURVES]
assert hashlib.sha256(CURVE_BYTES[0]).hexdigest().startswith("9aad2861")      # = tone-r3 / candidate-2 table


def sm6(v):
    """sign + 6-bit magnitude byte (MatR/G/B, I from mask 0xbfbfbfbf)."""
    assert -63 <= v <= 63
    return (0x80 if v < 0 else 0) | abs(v)


def mat_row(r):
    return (sm6(r[0]) << 24) | (sm6(r[1]) << 16) | (sm6(r[2]) << 8)


def chroma_scaled(sat, base=0x8470697C):
    """Scale stock NORMAL's four chroma bytes by the recipe saturation (keeps Ricoh's per-byte balance, I)."""
    b = [min(255, round(((base >> s) & 0xFF) * sat)) for s in (24, 16, 8, 0)]
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def uv6(cb, cr):
    """pUVoffset 0x120180d8: two 6-bit fields; assumed Cb [13:8], Cr [5:0], two's complement (field roles U)."""
    f = lambda x: round(x) & 0x3F   # noqa: E731
    return (f(cb) << 8) | f(cr)


def recipe_cc():
    t = json.loads((fw.DATA / "colour-recipes/fuji-classic-chrome.json").read_text())
    b = t["tiers"]["B"]
    return b


CC = recipe_cc()
SLOTS = [
    dict(name="BW-R3", flags=F_VALID | F_CURVE, imgset=3, contrast=5, sharp=5, depth=0, curve=0,
         chroma=0, mat=(0, 0, 0), uv=0,
         why="Owner's B&W JPEG: IMG SET B&W, contrast 5 (gate), sharpness 5 (owner's hard), pinned R3 curve on CC2"),
    dict(name="CLASSIC-CHROME", flags=F_VALID | F_CURVE | F_CHROMA | F_MATRIX | F_UV, imgset=5, contrast=3, sharp=3,
         depth=3, curve=1, chroma=chroma_scaled(CC["saturation"]),
         mat=tuple(mat_row(r) for r in CC["bytes"]["rgb_matrix_delta_x64"]),
         uv=uv6(*CC["offset_cbcr_codes"]),
         why="colour-recipes fuji-classic-chrome tier B (matrix + sat + offset + pinned LUT), on IMG SET SETTING2"),
    dict(name="STOCK-CONTROL", flags=F_VALID, imgset=1, contrast=0, sharp=0, depth=0, curve=0xFF, chroma=0,
         mat=(0, 0, 0), uv=0, why="NORMAL with no overrides: hook must be a no-op (control)"),
]


def slot_bytes(s):
    b = struct.pack(">BBBBBBBB", s["flags"], s["imgset"], s["contrast"], s["sharp"], s["depth"], s["curve"], 0, 0)
    b += struct.pack(">IIIIII", s["chroma"], *s["mat"], s["uv"], 0)
    assert len(b) == SLOTSZ
    for m in s["mat"]:
        assert m & ~0xBFBFBFBF == 0
    assert s["uv"] & ~0x3F3F == 0
    return b


def tail_default():
    t = struct.pack(">IBBBB", MAGIC, 1, NSLOTS, 0, 0) + bytes(T_SLOTS - 8)
    for i in range(NSLOTS):
        t += slot_bytes(SLOTS[i]) if i < len(SLOTS) else bytes(SLOTSZ)
    assert len(t) == TAIL_LEN
    return t


def bytes_directive(b):
    return "\n".join("    .byte " + ", ".join("0x%02x" % x for x in b[i:i + 16]) for i in range(0, len(b), 16))


def checks_directive():
    out = []
    for a, h, _ in CHECKS:
        b = bytes.fromhex(h)
        out.append("    .word 0x%08x, %d" % (a, len(b)))
        out.append(bytes_directive(b + bytes(-len(b) % 4)))
    out.append("    .word 0")
    return "\n".join(out)


def source(force, reseed):
    ft = "\n".join("    .hword 0x%03x, 0x%03x, 0x%03x" % FIELDS[i] for i in range(7))
    return f"""
.equ TAIL, 0x{TAIL:x}
.equ MAGIC, 0x{MAGIC:08x}
.equ NSLOTS, {NSLOTS}
.equ T_ACTIVE, 6
.equ T_SLOTS, 0x{T_SLOTS:x}
.equ TAIL_LEN, {TAIL_LEN}
.equ NCURVES, {len(CURVES)}
.equ SITE, 0x{SITE:x}

@ ---------------- installer: r0 = service table, r1 = body base, r2 = length ----------------
entry:
    push {{r4, r5, r6, r7, lr}}
    sub sp, #8                  @ sp+0 svc, sp+4 old irq mask
    str r0, [sp, #0]
    ldr r1, [r0, #0]
    cmp r1, #1                  @ ABI 1
    bne fail
    ldr r1, [r0, #4]
    ldr r2, =0x240
    cmp r1, r2                  @ image version word
    bne fail
    adr r4, checks              @ every before-byte must match RAM, else nothing is written
v:  ldr r5, [r4, #0]
    cmp r5, #0
    beq checked
    ldr r6, [r4, #4]
    adds r4, #8
    movs r2, #0
vc: cmp r2, r6
    bhs vn
    ldrb r0, [r5, r2]
    ldrb r1, [r4, r2]
    cmp r0, r1
    bne fail
    adds r2, #1
    b vc
vn: adds r0, r6, #3
    lsrs r0, r0, #2
    lsls r0, r0, #2
    adds r4, r4, r0
    b v
checked:
    adr r0, hook
    ldr r1, =SITE+4
    subs r7, r0, r1             @ r7 = BL displacement
    ldr r1, =0x400000
    adds r0, r7, r1
    lsrs r0, r0, #23            @ must be within +-4 MB
    bne fail
    bl tail_init
    bl recall
    ldr r0, [sp, #0]
    ldr r3, [r0, #0x28]         @ irq_set (ARM)
    movs r0, #0xc0
    bl call_r3
    str r0, [sp, #4]
    asrs r0, r7, #12
    ldr r1, =0x7ff
    ands r0, r1
    ldr r2, =0xf000
    orrs r0, r2                 @ BL high half
    asrs r3, r7, #1
    ands r3, r1
    ldr r2, =0xf800
    orrs r3, r2                 @ BL low half
    ldr r1, =SITE
    strh r0, [r1, #0]
    strh r3, [r1, #2]
    ldr r1, [sp, #0]
    ldr r3, [r1, #0x28]
    ldr r0, [sp, #4]
    bl call_r3                  @ restore IRQ/FIQ mask
    movs r0, #1                 @ resident: the hook lives in this body
    b out
fail:
    movs r0, #0                 @ nothing written; loader frees the body
out:
    add sp, #8
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
call_r3:
    bx r3

@ ---------------- tail: seed / keep the user's active byte / optional force ----------------
tail_init:
    push {{r4, lr}}
    ldr r0, =TAIL
    movs r4, #0                 @ active byte to keep (0 = off when the tail was not ours)
    ldr r1, [r0, #0]
    ldr r2, =MAGIC
    cmp r1, r2
    bne seed
    ldrb r1, [r0, #4]
    cmp r1, #1
    bne seed
    ldrb r4, [r0, #T_ACTIVE]
    adr r1, config
    ldrb r1, [r1, #1]           @ reseed: module slot data overrides the tail
    cmp r1, #0
    beq forced
seed:
    adr r1, tail_default
    movs r2, #0
sl: ldr r3, [r1, r2]
    str r3, [r0, r2]
    adds r2, #4
    cmp r2, #TAIL_LEN
    blo sl
    strb r4, [r0, #T_ACTIVE]
forced:
    adr r1, config
    ldrb r1, [r1, #0]           @ force active slot (0xff = keep)
    cmp r1, #0xff
    beq ti_done
    strb r1, [r0, #T_ACTIVE]
ti_done:
    pop {{r4}}
    pop {{r1}}
    bx r1

@ ---------------- r0 = active slot record, or 0 (leaf, r0-r3) ----------------
active_slot:
    ldr r3, =TAIL
    ldr r0, [r3, #0]
    ldr r1, =MAGIC
    cmp r0, r1
    bne as_no
    ldrb r0, [r3, #T_ACTIVE]
    subs r1, r0, #1
    cmp r1, #NSLOTS-1
    bhi as_no                   @ 0 (off) or out of range
    lsls r0, r1, #5
    adds r0, r0, r3
    adds r0, #T_SLOTS
    ldrb r1, [r0, #0]
    lsrs r1, r1, #1             @ valid bit -> C
    bcc as_no
    ldrb r1, [r0, #1]
    cmp r1, #6                  @ IMG SET 0..6
    bhi as_no
    bx lr
as_no:
    movs r0, #0
    bx lr

@ ---------------- recall: active slot -> stock settings fields (boot only) ----------------
recall:
    push {{r4, r5, r6, lr}}
    bl active_slot
    cmp r0, #0
    beq rc_done
    movs r5, r0
    ldrb r1, [r5, #1]           @ IMG SET
    ldr r4, =0x1b000
    movs r2, #0x30
    strb r1, [r4, r2]           @ 0x1b030
    adr r2, fieldtab
    lsls r3, r1, #1
    adds r3, r3, r1
    lsls r3, r3, #1             @ IMG SET * 6
    adds r6, r2, r3
    movs r3, #0
rc_l:
    lsls r0, r3, #1
    ldrh r0, [r6, r0]           @ field offset, 0 = none
    cmp r0, #0
    beq rc_n
    adds r1, r5, r3
    ldrb r1, [r1, #2]           @ contrast / sharpness / depth
    subs r2, r1, #1
    cmp r2, #4
    bhi rc_n                    @ only 1..5 are written
    strb r1, [r4, r0]
rc_n:
    adds r3, #1
    cmp r3, #3
    blo rc_l
rc_done:
    pop {{r4, r5, r6}}
    pop {{r3}}
    bx r3

@ ---------------- per-shot hook, reached by the patched bl at 0x96436 (r0 = cp+0x22) ----------------
.p2align 2
hook:
    push {{r4, r5, r6, r7, lr}}
    ldr r3, =0x8ff0f
    bl call_r3                  @ stock mono / fixed-chroma write, unchanged
    bl active_slot
    cmp r0, #0
    beq h_out
    movs r5, r0
    ldr r4, =0x1b000
    movs r1, #0x30
    ldrb r1, [r4, r1]           @ current IMG SET
    ldrb r2, [r5, #1]
    cmp r1, r2
    bne h_out                   @ user left the recipe's IMG SET: stock shot
    ldrb r6, [r5, #0]           @ flags
    ldr r7, =0x12018000
    lsrs r0, r6, #3             @ bit2 chroma -> C
    bcc h_mat
    ldr r0, [r5, #8]
    str r0, [r7, #4]            @ 0x12018004 chroma gain
h_mat:
    lsrs r0, r6, #4             @ bit3 matrix -> C
    bcc h_uv
    ldr r1, =0xbfbfbfbf
    ldr r2, =0x12018100
    ldr r0, [r5, #12]
    ands r0, r1
    str r0, [r2, #4]            @ MatR 0x12018104
    ldr r0, [r5, #16]
    ands r0, r1
    str r0, [r2, #8]            @ MatG 0x12018108
    ldr r0, [r5, #20]
    ands r0, r1
    str r0, [r2, #12]           @ MatB 0x1201810c
h_uv:
    lsrs r0, r6, #5             @ bit4 UV -> C
    bcc h_curve
    ldr r0, [r5, #24]
    ldr r1, =0x3f3f
    ands r0, r1
    ldr r2, =0x120180d8
    str r0, [r2, #0]            @ pUVoffset
h_curve:
    lsrs r0, r6, #2             @ bit1 curve -> C
    bcc h_out
    ldrb r1, [r5, #1]
    adr r2, fieldtab
    lsls r3, r1, #1
    adds r3, r3, r1
    lsls r3, r3, #1
    ldrh r3, [r2, r3]           @ this IMG SET's contrast field
    cmp r3, #0
    beq h_cv
    ldrb r0, [r5, #2]
    cmp r0, #0
    beq h_cv
    ldrb r1, [r4, r3]
    cmp r0, r1
    bne h_out                   @ user changed contrast: keep the stock curve
h_cv:
    ldrb r0, [r5, #5]           @ curve id
    cmp r0, #NCURVES
    bhs h_out
    lsls r0, r0, #13            @ * 8192
    adr r2, curves
    adds r2, r2, r0
    movs r0, #0                 @ offset 0
    movs r1, #1
    lsls r1, r1, #12            @ 4096 entries
    ldr r3, =0x16d31d
    bl call_r3                  @ 0x16d31c(0, 0x1000, curve): CC2 LUT, this shot
    ldr r1, =0x{CC2_CACHE:x}
    movs r0, #0xff
    strb r0, [r1, #0]           @ CC2 selector cache invalid -> next stock capture re-uploads
h_out:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
code_end:

.ltorg
.p2align 2
checks:
{checks_directive()}
.p2align 2
fieldtab:
{ft}
.p2align 2
config:
    .byte 0x{force:02x}, {reseed}, 0, 0
.p2align 2
tail_default:
{bytes_directive(tail_default())}
.p2align 2
curves:
"""
