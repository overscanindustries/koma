"""longexp: long exposure limit (byte 0x1b8ba) 0 = 180 s (stock), 1 = 240 s, with the shooting-display and EXIF labels.

Setting-driven: table[8] = 7200 gives count 902, ~240.5 s.

Code: M index 0x1b0c5 is clamped to 42 (0x6f42c), Tv 0x17ec3c[42] = -120. The exposure count is
0x17fe86[bucket 0x4699c(Tv)]; bucket 8 is Tv <= -120, i.e. index 42 only. Readers: 0x46ef4 (shot, via 0x47074 at
0x470f4) and 0x469d2 (0x47074 / 0x45354). Above 0x3fd the count becomes round(n/8) and is packed into a 10-bit field
(0x457c8/0x459ac): 8172 is the largest safe value (field 1023, ~273 s); 7200 keeps a 121-unit margin.
Labels: EXIF exposure time = rational table 0x17ec92[42] (u16 num at 0x17ed3a, 180/1), read by 0x412cc / 0x4171a.
Shooting display: 0x7ff6c case 0x2a does `movs r0,#0xb4` (0x80100) and formats it with 0x29232(num, 10, buf) at the
`bl` 0x80112, which every index shares; 180 with x=10 is produced only by index 42.

Koma version:
  - refresh(): table[8] := 5395 / 7200 and EXIF num := 180 / 240 from the setting byte (two aligned u16 data
    writes). No init: install writes nothing (selfcheck rule). Called at use from both hooks and from on_change.
    The label hook runs whenever the M display draws a shutter label, so the table and EXIF are current before
    any M shot; the count hook refreshes again at the shot itself.
  - hook `bl 0x29232` at 0x80112: when the setting is 240 S and the call is (180, 10), pass 240. No .text byte is
    written at run time.
  Out-of-range byte or missing KOMA magic = 180 s (stock values written back).
"""
from __future__ import annotations

from ..feature import Check, Feature, Hook

NAME = "longexp"
SUMMARY = "Long exposure limit 180S/240S (count table, EXIF and display label), hooks 0x470f4 + 0x80112"
OPTIONS = {}

SET_LE = 0x1B8BA
COUNT8, EXIF42 = 0x17FE96, 0x17ED3A
COUNTS = [5395, 7200]
SECONDS = [180, 240]
ROWS = [dict(id="longexp", label="LONG EXP LIMIT", options=["180S", "240S"], byte=SET_LE, default=0, group="exposure")]

SRC = f"""
.equ SET_LE, {SET_LE:#x}
.equ MAGIC, 0x1b8b0
.equ COUNT8, {COUNT8:#x}
.equ EXIF42, {EXIF42:#x}

@ r0 = 1 when the 240 S setting is active (magic present and byte == 1), else 0. Clobbers r0-r1 only.
active:
    ldr r0, =MAGIC
    ldr r0, [r0, #0]
    ldr r1, =0x4b4f4d41               @ 'KOMA'
    cmp r0, r1
    bne act_no
    ldr r0, =SET_LE
    ldrb r0, [r0, #0]
    cmp r0, #1
    bne act_no
    bx lr
act_no:
    movs r0, #0
    bx lr

@ writes the count table entry and the EXIF numerator for the current setting (two aligned u16 stores, each
@ atomic). Clobbers r0-r2.
refresh:
    push {{lr}}
    bl active
    lsls r0, r0, #1
    ldr r1, =counts
    ldrh r1, [r1, r0]
    ldr r2, =COUNT8
    strh r1, [r2, #0]
    ldr r1, =secs
    ldrh r1, [r1, r0]
    ldr r2, =EXIF42
    strh r1, [r2, #0]
    pop {{r0}}
    bx r0

on_change:
    push {{r4, lr}}
    bl refresh
    pop {{r4}}
    pop {{r0}}
    bx r0

@ wraps `bl 0x46ef4` at 0x470f4 (exposure count for the shot)
h_count:
    push {{r0, r1, r2, r3, r4, lr}}
    bl refresh
    pop {{r0, r1, r2, r3}}
    ldr r4, =next_count
    mov ip, r4
    bl call_ip
    pop {{r4}}
    pop {{r3}}
    bx r3

@ wraps `bl 0x29232` at 0x80112 (shutter label formatter: r0 = number, r1 = unit code, r2 = buffer)
h_label:
    push {{r0, r1, r2, r3, r4, lr}}
    bl refresh                        @ the M display draws the label before any M shot: table/EXIF current
    ldr r0, [sp, #0]
    ldr r1, [sp, #4]
    cmp r0, #180
    bne lab_go
    cmp r1, #10
    bne lab_go
    bl active
    cmp r0, #0
    beq lab_go
    movs r0, #240
    str r0, [sp, #0]                  @ replace the saved r0
lab_go:
    pop {{r0, r1, r2, r3}}
    ldr r4, =next_label
    mov ip, r4
    bl call_ip
    pop {{r4}}
    pop {{r3}}
    bx r3
call_ip:
    bx ip
.ltorg
.p2align 2
counts:
    .short {COUNTS[0]}, {COUNTS[1]}
secs:
    .short {SECONDS[0]}, {SECONDS[1]}
"""


def make(opts):
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=opts, source=SRC,
        exports=["on_change"],
        hooks=[Hook(0x470F4, 0x46EF4, "h_count", "next_count", "shot exposure count 0x46ef4: refresh table at use"),
               Hook(0x80112, 0x29232, "h_label", "next_label", "shutter label formatter: 180 -> 240",
                    smoke_reachable=False)],   # display/EXIF label: not on the smoke's shot path
        checks=[Check(COUNT8, COUNTS[0].to_bytes(2, "big"), "0x17fe86[8] = 5395 (180 s count)"),
                Check(EXIF42, bytes.fromhex("00b40001"), "EXIF rational 0x17ec92[42] = 180/1"),
                Check(0x800FE, bytes.fromhex("920420b49005"), "label case 0x2a: movs r0,#180 with unit 10")],
        smoke=smoke_checks)


def smoke_setup():
    return {0x1B8B0: 0x4B, 0x1B8B1: 0x4F, 0x1B8B2: 0x4D, 0x1B8B3: 0x41, SET_LE: 1}


def smoke_checks(obs):
    n = int.from_bytes(obs.ram(COUNT8, 2), "big")
    e = int.from_bytes(obs.ram(EXIF42, 2), "big")
    return [("240 S refreshed at use: count 7200, EXIF 240/1", (n, e) == (7200, 240), f"count={n} exif={e}")]
