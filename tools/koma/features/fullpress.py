"""fullpress: GR II "Full Press Snap" ON/OFF from Koma settings byte 0x1b8b6, read at the point of use.

RL1 focus handler 0x956a0 picks the focus type 0x1f4e7a = 0x701c0() and calls
0x70280(type) at one of several sites. When the shutter was already fully pressed at focus start (RL2 pending,
0x1f4e7b == 2) and the byte is ON, the focus mode is an AF mode (cp+4 = 0x23b06c: 0 multi, 6 spot) and macro is off
(cp+3 = 0x23b06b == 0; inferred macro flag), the handler substitutes type 1 (fixed preset, no AF scan) and sets the
focus-state byte 0x1bf028 = 1 as 0x70994 does. Type 1 runs 0x712a4, which returns the SNAP word for AF modes; with
`snapdist` installed that is the chosen distance, else stock 2.5 m. A half press (RL2 not pending) is untouched.

Sites (both `bl 0x70280`):
  0x95780  RL2-pending branch (stock type 2 = stored distance; the model's quick-full-press path)
  0x95738  external-sensor branch (type 3/4), taken before the RL2 check; covered only when RL2 is pending
"""
from __future__ import annotations

from .. import firmware as fw
from ..feature import Check, Feature, Hook

NAME = "fullpress"
SUMMARY = "Full press snap: quick full press in AF uses the SNAP distance, no AF scan (byte 0x1b8b6)"
OPTIONS = {}
BYTE = 0x1B8B6
ROWS = [dict(id="fullsnap", label="FULL SNAP", options=["OFF", "ON"], byte=BYTE, default=0, group="focus")]

SRC = f"""
@ r0 = stock type -> 1 when full-press snap applies. Preserves r1 (0x70280's buffer pointer). Clobbers r2, r3.
decide:
    ldr r2, ={BYTE:#x}
    ldrb r2, [r2]
    cmp r2, #1
    bne 9f                      @ OFF or out of range -> stock
    ldr r2, =0x1f4e7b
    ldrb r2, [r2]
    cmp r2, #2                  @ RL2 already pending at focus start
    bne 9f
    ldr r2, =0x23b068
    ldrb r3, [r2, #3]
    cmp r3, #0                  @ macro (inferred) -> stock
    bne 9f
    ldrb r3, [r2, #4]           @ focus mode
    cmp r3, #0                  @ multi AF
    beq 1f
    cmp r3, #6                  @ spot AF
    bne 9f
1:  movs r0, #1
    ldr r2, =0x1bf028
    strb r0, [r2]
9:  bx lr

fps_pending:                    @ 0x95780
    push {{r4, lr}}
    bl decide
    ldr r3, =next_pending
    bl call_r3
    pop {{r4}}
    pop {{r3}}
    bx r3

fps_sensor:                     @ 0x95738
    push {{r4, lr}}
    bl decide
    ldr r3, =next_sensor
    bl call_r3
    pop {{r4}}
    pop {{r3}}
    bx r3

call_r3:
    bx r3
.ltorg
"""


def make(opts):
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=opts, source=SRC,
        # smoke_reachable=False: both fire only on a quick full press; `koma smoke` settles RL1 first
        hooks=[Hook(0x95780, 0x70280, "fps_pending", "next_pending", "RL2-pending focus: 0x70280(type)",
                    smoke_reachable=False),
               Hook(0x95738, 0x70280, "fps_sensor", "next_sensor", "external-sensor focus: 0x70280(type)",
                    smoke_reachable=False)],
        checks=[Check(0x95754, fw.ram(0x95754, 0x2C), "RL2-pending branch (0x1f4e7b == 2) up to the call"),
                Check(0x95710, fw.ram(0x95710, 0x28), "type 3/4 branch up to the call"),
                Check(0x70994, fw.ram(0x70994, 4), "0x70994: strb type -> 0x1bf028 (what we mirror)")])


def smoke_setup():
    return {BYTE: 1}                       # ON
