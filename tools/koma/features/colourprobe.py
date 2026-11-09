"""colourprobe round 3: one-boot hardware probe of the live CC2 colour matrix.

Round 1 (O-hw): hook and write path live; IQ 0x28/0x2c/0x30 dead; MatR LIVE. Round 2 (O-hw, decoded): MatR/G/B byte roles (b3 = R, b2/b1 = G-like, b0 = B-like, weak), bit 6
ignored, linear in magnitude, gamma-domain, chroma only; pUVoffset low field = Cb coring; IQ + control bit 6 dead.
Round 3 settles sign coding, superposition, the b0 = B role (blue scene), the UV high field and the stage order of
chroma gain vs matrix. Shot k of a power-on gets frame k (k = shot counter in the module's RAM, 0 at every boot):

  k   write(s) (through the stock setters)                                 settles
  0   none (baselines captured through the stock getters)                  stock reference
  1   MatR  0x12018104 = 0xBF000000                                        b3 = -63 if bit 7 = sign (sign-magnitude)
  2   MatR  0x12018104 = 0x81000000                                        b3 = -63 if {b7,b5..b0} is 7-bit 2's c.
                                                                           (sign-magnitude reads it as -1 ~ stock)
  3   MatR  0x12018104 = 0x20909000  (+32, -16, -16, 0)                    superposition of a whole row ("red hue")
  4   MatR  = 0x0000003F  and  MatB 0x1201810c = 0x0000003F                b0 input role on a blue object (Cr, Cb)
  5   UV    0x120180d8 = 0x00001F00                                        high field = Cr coring by 31?
  6   gain  0x12018004 = current x 333/256 per byte (x1.30, sat. 0xff)     stage order: gain after or before the
        and MatB = 0x10000000 (+16)                                        matrix (Cr = gain check, Cb = order)
  7   none, but the restore runs                                           restore check (must match frame 0)
  8+  nothing                                                              stock

Mechanics (all D from the 2.40 disassembly):
  * Every setter is `set(u32 *p)`: one 32-bit `str` of *p (MatR/G/B 0x1725b0/c8/e0, UV 0x17231c, chroma gain
    0x171924, IQ 0x171a00 after its no-op range checks); every getter `get(u32 *p)` loads the register into *p.
  * Scale writes (k = 6): getter -> each byte b becomes min(0xff, (b * mult) >> 8) -> setter. The getter runs right
    after the restore wrote the k = 0 baseline, so pass 2 scales the baseline again (x1.3, not x1.69).
  * Pass 1 wraps `bl 0x8ff0e` at 0x96436 in 0x96350, after stock 0x92598 (MatR/G/B, UV) and 0x922bc (chroma gain).
    k := count, count++ (saturating), then apply k. Pass 2: 0xafd20 runs 0x92598(1)/0x922bc(1) again before its
    `bl 0x8ff0e` at 0xafd3e; the same k is applied there (no increment), so the shot's final values are the probe's.
  * Baselines: on k = 0 the module reads IQ 0x28, MatR, MatG, MatB, UV, chroma gain and the control word through the
    stock getters (0x1719f4, 0x1725a4/bc/d4, 0x172310, 0x171918, 0x172588). On every frame k = 1..7 it first writes
    the six data baselines back, and puts control bit 6 back to its baseline value (kept from round 2 as a guard:
    read-modify-write, only if it differs; round 3 never writes the control word). Frame 7 runs only the restore.

State (resident, 4-aligned): 'KCPROBE3' magic, count, cur, based, pad, base[6], base_ctrl, last_addr, last_val.
"""
from __future__ import annotations

import struct

from .. import firmware as fw
from ..feature import Check, Feature, Hook

NAME = "colourprobe"
SUMMARY = "hardware probe r3: shot k of a power-on gets colour probe k (0 stock, 1..6 probes, 7 restore)"
OPTIONS = {}
VERSION = "3.0"

MONO = 0x8FF0E
SITE_DEV1, SITE_DEV2 = 0x96436, 0xAFD3E
MAGIC = b"KCPROBE3"
CC2 = 0x12018000
R_IQ, R_MATR, R_MATG, R_MATB, R_UV, R_CTRL, R_GAIN = (0x12018028, 0x12018104, 0x12018108, 0x1201810C, 0x120180D8,
                                                      0x12018100, 0x12018004)
SET_IQ, GET_IQ = 0x171A00, 0x1719F4
SET_MATR, GET_MATR = 0x1725B0, 0x1725A4
SET_MATG, GET_MATG = 0x1725C8, 0x1725BC
SET_MATB, GET_MATB = 0x1725E0, 0x1725D4
SET_UV, GET_UV = 0x17231C, 0x172310
SET_GAIN, GET_GAIN = 0x171924, 0x171918
SET_CTRL, GET_CTRL = 0x172594, 0x172588
CTRL_BITS = 0x40                     # round-2 IQ-enable candidate (dead); its restore guard is kept
SETTER = {R_IQ: SET_IQ, R_MATR: SET_MATR, R_MATG: SET_MATG, R_MATB: SET_MATB, R_UV: SET_UV, R_CTRL: SET_CTRL,
          R_GAIN: SET_GAIN}
GETTER = {R_IQ: GET_IQ, R_MATR: GET_MATR, R_MATG: GET_MATG, R_MATB: GET_MATB, R_UV: GET_UV, R_CTRL: GET_CTRL,
          R_GAIN: GET_GAIN}
GAIN_MULT = 333                      # Q8: 333/256 = 1.301
# k -> writes [(register, value, scale)]; scale = read through the getter, each byte b -> min(0xff, b*value >> 8)
PROBES = {1: [(R_MATR, 0xBF000000, False)],
          2: [(R_MATR, 0x81000000, False)],
          3: [(R_MATR, 0x20909000, False)],
          4: [(R_MATR, 0x0000003F, False), (R_MATB, 0x0000003F, False)],
          5: [(R_UV, 0x00001F00, False)],
          6: [(R_GAIN, GAIN_MULT, True), (R_MATB, 0x10000000, False)]}
NPROBE = len(PROBES)
RESTORE_K = NPROBE + 1               # restore-only frame (the check)
WPF = 2                              # write slots per frame in the table
BASE_REGS = [R_IQ, R_MATR, R_MATG, R_MATB, R_UV, R_GAIN]   # restored by plain setter(&base); R_CTRL last, bit 6 only
# state offsets
O_COUNT, O_CUR, O_BASED, O_BASE = 8, 9, 10, 12
O_CTRL = O_BASE + 4 * len(BASE_REGS)
O_LAST = O_CTRL + 4
STATE_LEN = O_LAST + 8


def scaled(word, mult=GAIN_MULT):
    """What a scale write stores: each byte b of word -> min(0xff, (b * mult) >> 8)."""
    return int.from_bytes(bytes(min(0xFF, (b * mult) >> 8) for b in word.to_bytes(4, "big")), "big")


def _table():
    rows = []
    for k in range(1, NPROBE + 1):
        ws = PROBES[k]
        assert 1 <= len(ws) <= WPF
        for i in range(WPF):
            if i < len(ws):
                reg, val, scale = ws[i]
                g = (GETTER[reg] | 1) if scale else 0
                rows.append(f"    .word 0x{SETTER[reg] | 1:08x}, 0x{val:08x}, 0x{reg:08x}, 0x{g:08x}   @ k={k}")
            else:
                rows.append("    .word 0, 0, 0, 0")
    return "\n".join(rows)


def _btab():
    return "\n".join(f"    .word 0x{GETTER[r] | 1:08x}, 0x{SETTER[r] | 1:08x}   @ {r:#x}" for r in BASE_REGS) + \
        "\n    .word 0, 0"


SRC = f"""
@ ================================================================ develop hooks (wrap bl 0x8ff0e; r0 = cp+0x22)
hook_dev1:
    push {{r4, r5, r6, r7, lr}}
    ldr r3, =next_dev1
    movs r6, #1
    b common
hook_dev2:
    push {{r4, r5, r6, r7, lr}}
    ldr r3, =next_dev2
    movs r6, #2
common:
    bl call_r3                       @ stock 0x8ff0e (or the next hook) with the original r0
    ldr r5, =state
    cmp r6, #1
    bne 2f
    ldrb r4, [r5, #{O_COUNT}]        @ pass 1: k = count, count++ (saturating at 0xff)
    strb r4, [r5, #{O_CUR}]
    cmp r4, #0xff
    beq 1f
    adds r0, r4, #1
    strb r0, [r5, #{O_COUNT}]
1:  cmp r4, #0
    bne 3f
    bl capture                       @ k = 0: read the baselines once
    b done
2:  ldrb r4, [r5, #{O_CUR}]          @ pass 2: same k as pass 1
3:  bl apply
done:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

@ r4 = k, r5 = state. k in 1..{RESTORE_K}: restore baselines (if captured); k in 1..{NPROBE}: then the probe writes.
@ r6/r7 are free here (saved by the hook prologue).
apply:
    push {{lr}}
    subs r0, r4, #1
    cmp r0, #{RESTORE_K - 1}
    bhi 9f
    ldrb r0, [r5, #{O_BASED}]
    cmp r0, #0
    beq 4f
    bl restore
4:  subs r0, r4, #1
    cmp r0, #{NPROBE - 1}
    bhi 9f                           @ k = {RESTORE_K}: restore only
    lsls r0, r0, #{(16 * WPF).bit_length() - 1}
    ldr r6, =ptab
    adds r6, r6, r0
    movs r7, #{WPF}
5:  ldr r3, [r6, #0]                 @ setter | 1 (0 = empty slot)
    cmp r3, #0
    beq 6f
    ldr r0, [r6, #4]                 @ value
    ldr r2, [r6, #8]                 @ register (logged for smoke/tests)
    str r2, [r5, #{O_LAST}]
    str r0, [r5, #{O_LAST + 4}]
    ldr r1, [r6, #12]                @ getter | 1 for a scale write, else 0
    bl write_mode
6:  adds r6, #16
    subs r7, #1
    bne 5b
9:  pop {{r3}}
    bx r3

@ r0 = value, r1 = getter|1 or 0, r3 = setter|1. setter(&w): w = value, or when r1 != 0 w = getter() with each
@ byte b -> min(0xff, (b * value) >> 8) (value = Q8 multiplier). r4 is saved (k).
write_mode:
    push {{r4, lr}}
    sub sp, #16
    str r0, [sp, #0]
    cmp r1, #0
    beq 1f
    str r0, [sp, #4]                 @ multiplier
    str r3, [sp, #8]                 @ setter
    mov r0, sp
    movs r3, r1
    bl call_r3                       @ [sp] = current register value
    movs r4, #0
2:  mov r0, sp
    ldrb r1, [r0, r4]
    ldr r2, [sp, #4]
    muls r1, r2, r1
    lsrs r1, r1, #8
    cmp r1, #0xff
    bls 3f
    movs r1, #0xff
3:  strb r1, [r0, r4]
    adds r4, #1
    cmp r4, #4
    bne 2b
    ldr r3, [sp, #8]
1:  mov r0, sp
    bl call_r3
    add sp, #16
    pop {{r4}}
    pop {{r3}}
    bx r3

@ r5 = state: setter(&base[i]) for the data registers; control word bit 6 back to baseline (only if it differs)
restore:
    push {{r6, r7, lr}}
    movs r6, r5
    adds r6, #{O_BASE}
    ldr r7, =btab
1:  ldr r3, [r7, #4]
    cmp r3, #0
    beq 2f
    movs r0, r6
    bl call_r3
    adds r6, #4
    adds r7, #8
    b 1b
2:  sub sp, #8
    mov r0, sp
    ldr r3, =0x{GET_CTRL | 1:x}
    bl call_r3
    ldr r0, [sp, #0]
    ldr r1, [r5, #{O_CTRL}]
    eors r1, r0
    movs r2, #0x{CTRL_BITS:x}
    ands r1, r2
    beq 3f
    eors r0, r1                      @ flip the differing candidate bits back
    str r0, [sp, #0]
    mov r0, sp
    ldr r3, =0x{SET_CTRL | 1:x}
    bl call_r3
3:  add sp, #8
    pop {{r6, r7}}
    pop {{r3}}
    bx r3

@ r5 = state: base[i] = getter_i(), then the control word
capture:
    push {{r6, r7, lr}}
    movs r6, r5
    adds r6, #{O_BASE}
    ldr r7, =btab
1:  ldr r3, [r7, #0]
    cmp r3, #0
    beq 2f
    movs r0, r6
    bl call_r3
    adds r6, #4
    adds r7, #8
    b 1b
2:  movs r0, r5
    adds r0, #{O_CTRL}
    ldr r3, =0x{GET_CTRL | 1:x}
    bl call_r3
    movs r0, #1
    strb r0, [r5, #{O_BASED}]
    pop {{r6, r7}}
    pop {{r3}}
    bx r3

call_r3:
    bx r3
.ltorg
.p2align 2
btab:
{_btab()}
ptab:
{_table()}
state:
    .ascii "{MAGIC.decode()}"
    .byte 0, 0xff, 0, 0              @ count, cur (0xff = no shot yet), based, pad
    .word {', '.join('0' for _ in BASE_REGS)}           @ base IQ, MatR, MatG, MatB, UV, chroma gain
    .word 0                          @ base control word
    .word 0, 0                       @ last probe write: register, value
"""


def _bytes(a, hexs):
    assert fw.ram(a, len(bytes.fromhex(hexs))) == bytes.fromhex(hexs), f"{a:#x}"
    return bytes.fromhex(hexs)


# Bytes the hooks and calls depend on (all verified against 2.40 at install).
CHECKS = [
    (0x96426, "f7fcf8b72001f7fbff46", "0x96350: bl 0x92598; movs r0,#1; bl 0x922bc before the hooked site"),
    (0x96430, "1df830197880", "0x96350: r0 = [cp+0x22] before the hooked bl 0x8ff0e at 0x96436"),
    (0xAFD2E, "f7e2fc332001f7e2fac2", "0xafd20: pass 2 rewrites MatR/UV (0x92598) and chroma gain (0x922bc)"),
    (0xAFD38, "1df830197880", "0xafd20: r0 = [cp+0x22] before the hooked bl 0x8ff0e at 0xafd3e"),
    (SET_IQ, "b5801c0768001600", "IQ matrix setter 0x171a00 prologue (range checks, then str)"),
    (0x171A7A, "683849396288", "0x171a00: str *r0 -> [0x12018000 + 0x28]"),
    (0x171B64, "12018000", "0x171a00 literal: CC2 base"),
    (GET_IQ, "49016a896001477012018000", "getter: *r0 = 0x12018028"),
    (GET_MATR, "490168496001477012018100", "getter: *r0 = 0x12018104 (MatR)"),
    (SET_MATR, "680049016048477012018100", "setter: 0x12018104 (MatR) = *r0"),
    (GET_MATG, "490168896001477012018100", "getter: *r0 = 0x12018108 (MatG)"),
    (SET_MATG, "680049016088477012018100", "setter: 0x12018108 (MatG) = *r0"),
    (GET_MATB, "490168c96001477012018100", "getter: *r0 = 0x1201810c (MatB)"),
    (SET_MATB, "6800490160c8477012018100", "setter: 0x1201810c (MatB) = *r0"),
    (GET_UV, "4901698960014770120180c0", "getter: *r0 = 0x120180d8 (pUVoffset)"),
    (SET_UV, "6800490161884770120180c0", "setter: 0x120180d8 (pUVoffset) = *r0"),
    (GET_CTRL, "490168096001477012018100", "getter: *r0 = 0x12018100 (control word)"),
    (GET_GAIN, "490168496001477012018000", "getter: *r0 = 0x12018004 (chroma gain)"),
    (SET_GAIN, "680049016048477012018000", "setter: 0x12018004 (chroma gain) = *r0"),
    (SET_CTRL, "68002380490143986008477012018100", "setter: 0x12018100 = *r0 & ~0x80 (bit 7 force-cleared)"),
]


def make(opts):
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options=opts, source=SRC,
        hooks=[Hook(SITE_DEV1, MONO, "hook_dev1", "next_dev1", "still develop pass 1: k = shot count, probe k"),
               Hook(SITE_DEV2, MONO, "hook_dev2", "next_dev2", "still develop pass 2 (0xafd20 rewrites MatR/G/B and "
                    "UV, chroma gain): same probe k", smoke_reachable=False)],
        checks=[Check(a, _bytes(a, h), w) for a, h, w in CHECKS],
        smoke=smoke_checks)


# ------------------------------------------------------------------ smoke
def _state_addr(read):
    """Find the resident state block: Koma publish slot -> manifest (resident start), then the magic."""
    if read(fw.PUBLISH, 4) != b"KOMA":
        return None
    base = struct.unpack(">I", read(fw.PUBLISH + 4, 4))[0]
    blob = read(base, 0x8000)
    i = blob.find(MAGIC)
    return None if i < 0 else base + i


def smoke_setup():
    """`koma smoke` takes ONE shot. Make it k = 1 (MatR = 0xBF000000, byte 3 = -63 sign-magnitude) by poking count = 1 in
    the installed module. smoke_setup() gets no machine handle, so the running smoke's Machine `m` is taken from the
    caller's frame (smoke only; no effect on the module)."""
    import inspect
    fr = inspect.currentframe().f_back
    m = fr.f_locals.get("m") if fr else None
    if m is None:
        return {}
    st = _state_addr(lambda a, n: bytes(m.uc.mem_read(a, n)))
    return {} if st is None else {st + O_COUNT: 1}


def smoke_checks(obs):
    st = _state_addr(obs.ram)
    out = [("probe state found in the resident block", st is not None, f"state={st and hex(st)}")]
    if st is None:
        return out
    s = obs.ram(st, STATE_LEN)
    count, cur = s[O_COUNT], s[O_CUR]
    last = struct.unpack(">II", s[O_LAST:O_LAST + 8])
    m = obs._m
    regs = m.page_model.get(R_MATR >> 12, m.default_model).regs
    matr = regs.get(R_MATR)
    out += [("shot was k = 1 (count poked 1 -> 2)", cur == 1 and count == 2, f"cur={cur} count={count}"),
            ("module logged write MatR 0x12018104 = 0xbf000000", last == (R_MATR, 0xBF000000),
             f"last=({last[0]:#x}, {last[1]:#x})"),
            ("CC2 MatR 0x12018104 last written value is 0xbf000000 at the end of pass 1", matr == 0xBF000000,
             f"0x12018104={'unwritten' if matr is None else hex(matr)}")]
    return out
