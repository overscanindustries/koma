"""adjdiag: diagnostic for the ADJ hang Not a release feature.

What it watches: every CPU1 (sub-CPU) exchange waits for CPU1's answer at 0xca564 `bl 0x5d978(sem, timeout)` inside
0xca524, with timeout = the key-scan period word 0x2c9e58 (50 stock, 20 under fastkeys poll=on). The callers retry
without bound (0xca76a loops while 0xca524 returns -1; 0x51a18 loops on 0xca76a), so a frame CPU1 cannot answer in
time spins its task forever. The hook wraps that wait. On every timeout it counts; on an answer it clears the
consecutive count. The first time CONSEC timeouts happen in a row it writes /ATA1/KOMADIAG.TXT ONCE per boot (latched)
and then keeps returning the stock result, so the camera behaves exactly as without the hook (it still hangs if it
would have). No write ever happens on a healthy camera, and the file is never written at boot, so a record from a
hung session survives the next power-on.

Record (ASCII, fixed length; decode by eye): KOMA ADJDIAG V1, then NAME =XXXXXXXX lines:
  CONSEC  consecutive timeouts at the moment of writing        TOTAL  timeouts since boot
  TMO     timeout passed to the wait (ticks)                    PERIOD scan period word 0x2c9e58
  XFER    0x2debe4 word: state, bytes done, frame length, byte0  FRAME  frame bytes 1..4 (register hi/lo, data)
  TASK    current task 0x1a89a8                                 TICK   tick counter 0x2358dc
  ADJD0   ADJ descriptor word 0x1c249c (ADJ item)               ADJD8  0x1c24a4 (+8 cur, +9 cursor, +10, +11)
  ATYPE   ADJ item type word 0x1bf090                           OKS    exchanges answered since boot
Reading it: XFER byte1 (bytes done) < byte2 (frame length) means CPU1 was still clocking the frame when the 20-tick
budget ran out (budget too short); bytes done = 0 means CPU1 never answered the first byte.
Hook site 0xca564 is in 0xca524's body, which the emulator stubs (so it is absent from the phase-A line set); on the
camera it runs before the loader hook, and a CPU store to already-executed code is fetched fresh on hardware, so the
patched bl takes effect.
"""
from __future__ import annotations

from ..feature import Check, Feature, Hook
from .. import firmware as fw

NAME = "adjdiag"
SUMMARY = "diagnostic: log a CPU1 exchange stuck in timeouts to /ATA1/KOMADIAG.TXT (once per boot)"
OPTIONS = {"consec": "consecutive timeouts that trigger the record (default 3)"}

SITE, WAIT = 0xCA564, 0x5D978
CREATE, WRITE, CLOSE = 0x55CF4, 0x560EC, 0x55F0C
PATH = "/ATA1/KOMADIAG.TXT"
HEADER = b"KOMA ADJDIAG V1\n"
TRAILER = b"END\n"
FIELDS = ["CONSEC", "TOTAL", "TMO", "PERIOD", "XFER", "FRAME", "TASK", "TICK", "ADJD0", "ADJD8", "ATYPE", "OKS"]
LINE = 16                        # 'NAME  =XXXXXXXX\n'


def template() -> bytes:
    t = bytearray(HEADER)
    for n in FIELDS:
        t += n.ljust(6).encode() + b"=--------\n"
    return bytes(t + TRAILER)


def source(consec: int) -> str:
    text = template()
    off = {n: len(HEADER) + i * LINE + 7 for i, n in enumerate(FIELDS)}
    lines = []

    def put(name, load):          # load: asm that leaves the value in r0
        lines.append(load)
        lines.append(f"    ldr r1, =text + {off[name]}\n    bl hex8")
    put("CONSEC", "    ldr r0, =st\n    ldr r0, [r0, #0]")
    put("TOTAL", "    ldr r0, =st\n    ldr r0, [r0, #4]")
    put("TMO", "    ldr r0, =st\n    ldr r0, [r0, #12]")
    put("PERIOD", "    ldr r0, =0x2c9e58\n    ldr r0, [r0]")
    put("XFER", "    ldr r0, =0x2debe4\n    ldr r0, [r0]")
    put("FRAME", "    ldr r0, =0x2debe8\n    ldr r0, [r0]")
    put("TASK", "    ldr r0, =0x1a89a8\n    ldr r0, [r0]")
    put("TICK", "    ldr r0, =0x2358dc\n    ldr r0, [r0]")
    put("ADJD0", "    ldr r0, =0x1c249c\n    ldr r0, [r0]")
    put("ADJD8", "    ldr r0, =0x1c24a4\n    ldr r0, [r0]")
    put("ATYPE", "    ldr r0, =0x1bf090\n    ldr r0, [r0]")
    put("OKS", "    ldr r0, =st\n    ldr r0, [r0, #8]")
    fill = "\n".join(lines)
    return f"""
@ wraps bl 0x5d978 at 0xca564 (CPU1 exchange: wait for CPU1's answer; r0 = sem, r1 = timeout). -1 = timed out.
h_wait:
    push {{r4, r5, r6, r7, lr}}
    ldr r4, =st
    str r1, [r4, #12]
    ldr r3, =next_wait
    bl call_r3
    movs r5, r0
    ldr r4, =st
    adds r0, #1
    bne w_ok
    ldr r0, [r4, #0]
    adds r0, #1
    str r0, [r4, #0]
    ldr r1, [r4, #4]
    adds r1, #1
    str r1, [r4, #4]
    cmp r0, #{consec}
    blo w_out
    ldr r0, [r4, #16]           @ latch: one record per boot
    cmp r0, #0
    bne w_out
    movs r0, #1
    str r0, [r4, #16]
    bl record
    b w_out
w_ok:
    movs r0, #0
    str r0, [r4, #0]
    ldr r0, [r4, #8]
    adds r0, #1
    str r0, [r4, #8]
w_out:
    movs r0, r5
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

record:
    push {{r4, r5, lr}}
{fill}
    ldr r0, =path
    movs r1, #2                 @ as stock callers; 0x55cf4 ignores it
    ldr r3, =0x{CREATE | 1:x}
    bl call_r3
    movs r4, r0
    adds r0, #1
    beq rec_out
    movs r0, r4
    ldr r1, =text
    ldr r2, ={len(text)}
    ldr r3, =0x{WRITE | 1:x}
    bl call_r3
    movs r0, r4
    ldr r3, =0x{CLOSE | 1:x}
    bl call_r3
rec_out:
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

@ r0 = value, r1 = destination: 8 upper-case hex digits. Clobbers r0-r3.
hex8:
    movs r2, #28
1:  movs r3, r0
    lsrs r3, r2
    movs r3, r3
    lsls r3, r3, #28
    lsrs r3, r3, #28
    cmp r3, #10
    blo 2f
    adds r3, #7
2:  adds r3, #48
    strb r3, [r1]
    adds r1, #1
    subs r2, #4
    bpl 1b
    bx lr

call_r3:
    bx r3
.ltorg
.p2align 2
st:
    .word 0, 0, 0, 0, 0         @ consec, total, oks, last timeout, latched
path:
    .asciz "{PATH}"
.p2align 2
text:
    .byte {", ".join(str(b) for b in text)}
.p2align 2
"""


def make(opts):
    consec = int((opts or {}).get("consec", 3))
    if not 1 <= consec <= 255:
        raise ValueError("adjdiag.consec: 1..255")
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=opts, source=source(consec),
        hooks=[Hook(SITE, WAIT, "h_wait", "next_wait", "0xca524 CPU1 exchange: sem wait for CPU1's answer",
                    smoke_reachable=False)],   # the emulator stubs 0xca524 whole: never reached in smoke
        checks=[Check(CREATE, fw.ram(CREATE, 4), "file create 0x55cf4 prologue"),
                Check(WRITE, fw.ram(WRITE, 4), "file write 0x560ec prologue"),
                Check(CLOSE, fw.ram(CLOSE, 4), "file close 0x55f0c prologue")])
