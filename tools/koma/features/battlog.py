"""battlog: battery diagnostic logger. Records what the stock battery poll sees and writes /ATA1/KOMABATT.TXT at power-off.

Stock mechanism (derived from code):
  tOpeMain's periodic body 0x8a790 calls the battery poll 0x5290c once per key-scan period (stock 50 ticks, fastkeys
  20). The poll reads CPU1 REQ_GET_BATT_VOLT (frame 0x1e, reg 0xf21a, 1 byte) through 0x521dc into a u16 on its stack
  and into the cache 0x2b9e66, picks END/NEAR/HALF thresholds (0x2b9e80/82/8a) from the store table indexed by the
  camera state (index in 0x2b9e93; type 0 table 0x08010e10, type 1 0x08010d10; type 2 = no battery check) and sets the
  level 0x2b9e92 (0 END, 1 NEAR END, 2 HALF, 3 full). A single sample below END latches level 0 and starts the shutdown.

What this feature does:
  hook 0x52918 (`bl 0x521dc`, the poll's read): after the read, fold the sample into a segment (min / max / count) and
  the per-index minimum and below-END counters. A segment closes when the threshold index, the level/type, or the
  strobe-charge state 0x2b9e74 changes, or after PERIOD ticks; closed segments go into a ring of RING records.
  hooks 0x32d3e / 0x32d82 (`bl 0x293f4(0xb)` in the power-off 0x32b8c, after the lens retract, just before the settings
  save): write the log, then continue. Skipped while USB is attached (0x23a888 == 1).
  The write is skipped when the level is END and the latest raw reading is still below the stock END threshold:
  stock itself only writes images while the reading is at or above END, so the log never writes below that floor.

Raw reading = the byte CPU1 returns (scale unknown, not volts). Times are (tick - first poll) >> 8 (tick = the RTOS
counter 0x5df20; 1 ms per tick is an inference from 20000/period = 400 polls).

File (binary after a 12-byte ASCII magic; decode with tools/koma_battlog_decode.py or battlog.parse()):
  0   "KOMA BATT V2"          12 B
  12  build id                 4 B
  16  0x2b9e64..0x2b9e97      52 B  stock battery driver block: six ADC channel caches (u16; ch1 = battery raw),
                                    strobe state 0x2b9e74, END/NEAR/HALF thresholds, hold counters, level, index, type
  68  0x1a918c                 4 B  camera-state word
  72  log state               56 B  segment (24), lowest raw per threshold index (16, FF = never), polls below the END
                                    threshold in force per index (16, saturating)
  128 ring                ring*8 B  records t(2) idx lvl|type<<4 min max flags n; head/total in the log state
flags: low nibble = strobe-charge state at segment start; 0x20 = raw below END.
"""
from __future__ import annotations

from .. import firmware as fw
from ..feature import Check, Feature, Hook

NAME = "battlog"
SUMMARY = "battery log: raw reading, level, threshold index, strobe state per segment -> /ATA1/KOMABATT.TXT at power-off"
OPTIONS = {"ring": "records kept (8 B each), 16..256, default 40",
           "period": "segment length in ticks (about ms), 1000..60000, default 10000"}

LOG_PATH = "/ATA1/KOMABATT.TXT"
POLL_SITE, READ = 0x52918, 0x521DC          # bl 0x521dc in the battery poll 0x5290c
# 0x32b8c (common orderly power-off: POWER key, battery END, startup aborts, fatal 0x5205): both branches call
# 0x293f4(0xb) after the lens retract and just before the settings save 0x4949c at 0x32d94.
POWEROFF_SITES, POWEROFF = (0x32D3E, 0x32D82), 0x293F4
USB = 0x23A888                              # word, 1 = cable attached
TICK = 0x5DF20
TICK_WORD = 0x1A8A00               # 0x5df20 -> 0x1e1b0: ldr r0, =0x1a8a00 ; ldr r0, [r0]
CREATE, WRITE, CLOSE = 0x55CF4, 0x560EC, 0x55F0C
RAW, END_T, NEAR_T, HALF_T = 0x2B9E66, 0x2B9E80, 0x2B9E82, 0x2B9E8A
LVL, IDX, TYPE, STROBE = 0x2B9E92, 0x2B9E93, 0x2B9E95, 0x2B9E74
CAMSTATE = 0x1A918F                         # low byte of the camera-state word 0x1a918c (big-endian)
HEADER = b"KOMA BATT V2"                # then build id (4), driver block, camera state, log state (binary)
REC = 8
DEF_RING, DEF_PERIOD = 40, 15000
DRV, DRV_LEN = 0x2B9E64, 0x34              # 0x2b9e64..0x2b9e97
CAMSTATE_W = 0x1A918C
SEG_LEN = 56                                # seg 24 + smin 16 + slow 16
EARLY_OK = ("0x52918 is in phase-A line 0x52900 (the startup polls from 0x8a3ec run it before the loader). Waived: "
            "one 4-byte bl written with IRQs masked; a task suspended inside the call returns to 0x5291c untouched; "
            "stores to executed code are fetched fresh on hardware (KOMALOG-v04-boot SMC1 5A / SMC2 A5, "
            "analysis/koma-0.1/adj-hang.md). Every per-poll site of 0x5290c is in phase A")


def _int_opt(opts, key, default, lo, hi):
    v = int(str((opts or {}).get(key, default)), 0)
    if not lo <= v <= hi:
        raise ValueError(f"battlog.{key}={v}: must be {lo}..{hi}")
    return v


def source(ring: int, period: int) -> str:
    STATE_LEN = SEG_LEN + ring * REC
    return f"""
@ ---------------------------------------------------------------- poll hook: entered as 0x521dc(r0 = &u16 reading)
bl_poll:
    push {{r4, r5, r6, r7, lr}}
    movs r4, r0
    ldr r3, =next
    bl call_r3                  @ the read (and battguard, if built after this feature)
    ldr r0, =0x{TICK_WORD:x}     @ RTOS tick (what 0x5df20 -> 0x1e1b0 returns)
    ldr r6, [r0]                @ r6 = now
    ldr r5, =seg
    ldrb r0, [r5, #20]          @ first call: remember the boot tick
    cmp r0, #0
    bne 1f
    str r6, [r5, #12]
    movs r0, #1
    strb r0, [r5, #20]
1:  ldr r0, =0x{RAW:x}
    ldrh r7, [r0]
    lsls r7, r7, #24
    lsrs r7, r7, #24            @ r7 = raw byte
@ ---- does the open segment end here?
    ldrb r0, [r5, #4]
    cmp r0, #0
    beq 3f
    bl cur_ix
    ldrb r1, [r5, #8]
    cmp r0, r1
    bne 2f
    bl cur_lv
    ldrb r1, [r5, #9]
    cmp r0, r1
    bne 2f
    bl cur_sb
    ldrb r1, [r5, #10]
    lsls r1, r1, #28
    lsrs r1, r1, #28
    cmp r0, r1
    bne 2f
    ldr r0, [r5, #0]
    subs r0, r6, r0
    ldr r1, ={period}
    cmp r0, r1
    blo 4f
2:  bl emit
@ ---- open a segment with this sample
3:  str r6, [r5, #0]
    strb r7, [r5, #5]
    strb r7, [r5, #6]
    bl cur_ix
    strb r0, [r5, #8]
    bl cur_lv
    strb r0, [r5, #9]
    bl cur_sb
    strb r0, [r5, #10]
    movs r0, #0
    strb r0, [r5, #4]
@ ---- fold the sample in
4:  ldrb r0, [r5, #4]
    cmp r0, #255
    beq 5f
    adds r0, #1
    strb r0, [r5, #4]
5:  ldrb r0, [r5, #5]
    cmp r7, r0
    bhs 6f
    strb r7, [r5, #5]
6:  ldrb r0, [r5, #6]
    cmp r7, r0
    bls 7f
    strb r7, [r5, #6]
7:  ldrh r0, [r4]               @ value the poll will judge (battguard may have replaced it)
    cmp r0, r7
    beq 8f
    ldrb r0, [r5, #10]
    movs r1, #0x10
    orrs r0, r1
    strb r0, [r5, #10]
8:  bl cur_ix
    movs r1, #15
    ands r0, r1
    ldr r1, =smin
    adds r1, r1, r0
    ldrb r2, [r1]
    cmp r7, r2
    bhs 9f
    strb r7, [r1]
9:  ldr r1, =0x{END_T:x}
    ldrh r1, [r1]
    cmp r7, r1
    bhs 10f
    ldr r1, =slow
    adds r1, r1, r0
    ldrb r2, [r1]
    cmp r2, #255
    beq 11f
    adds r2, #1
    strb r2, [r1]
11: ldrb r0, [r5, #10]
    movs r1, #0x20
    orrs r0, r1
    strb r0, [r5, #10]
10: pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

cur_ix:
    ldr r0, =0x{IDX:x}
    ldrb r0, [r0]
    bx lr
cur_lv:                         @ level | type << 4
    ldr r0, =0x{TYPE:x}
    ldrb r0, [r0]
    lsls r0, r0, #4
    ldr r1, =0x{LVL:x}
    ldrb r1, [r1]
    orrs r0, r1
    lsls r0, r0, #24
    lsrs r0, r0, #24
    bx lr
cur_sb:                         @ strobe-charge state, 4 bits
    ldr r0, =0x{STROBE:x}
    ldrb r0, [r0]
    lsls r0, r0, #28
    lsrs r0, r0, #28
    bx lr

@ ---- emit: close the open segment into the ring (r5 = seg). Clobbers r0-r3
emit:
    ldrb r0, [r5, #4]
    cmp r0, #0
    beq 1f
    ldrh r1, [r5, #16]          @ head
    lsls r2, r1, #3
    ldr r3, =ring
    adds r3, r3, r2             @ r3 = record
    strb r0, [r3, #7]           @ n
    ldr r0, [r5, #0]
    ldr r2, [r5, #12]
    subs r0, r0, r2
    lsrs r0, r0, #8             @ t = (t0 - boot) >> 8
    strb r0, [r3, #1]
    lsrs r0, r0, #8
    strb r0, [r3, #0]
    ldrb r0, [r5, #8]
    strb r0, [r3, #2]
    ldrb r0, [r5, #9]
    strb r0, [r3, #3]
    ldrb r0, [r5, #5]
    strb r0, [r3, #4]
    ldrb r0, [r5, #6]
    strb r0, [r3, #5]
    ldrb r0, [r5, #10]
    strb r0, [r3, #6]
    adds r1, #1
    ldr r2, ={ring}
    cmp r1, r2
    blo 2f
    movs r1, #0
2:  strh r1, [r5, #16]
    ldrh r1, [r5, #18]          @ total, saturating
    adds r1, #1
    lsrs r2, r1, #16
    bne 3f
    strh r1, [r5, #18]
3:  movs r0, #0
    strb r0, [r5, #4]
1:  bx lr

@ ---------------------------------------------------------------- power-off hook: entered as 0x4fcaa(r0..r3)
bl_off1:                        @ entered as 0x293f4(r0 = 0xb) at 0x32d3e
    push {{r0, r1, r2, r3, r4, lr}}
    bl flush                    @ preserves r4-r7
    ldr r4, =next_off1
    b off_tail
bl_off2:                        @ entered as 0x293f4(r0 = 0xb) at 0x32d82
    push {{r0, r1, r2, r3, r4, lr}}
    bl flush
    ldr r4, =next_off2
off_tail:
    mov ip, r4
    pop {{r0, r1, r2, r3, r4}}  @ the caller's arguments and r4 back
    bl call_ip
    pop {{r3}}
    bx r3

call_ip:
    bx ip

flush:                          @ bounded: one create, five writes, one close; no loops, no waits of its own
    push {{r4, r5, lr}}
    ldr r5, =seg
    ldr r0, =0x{USB:x}          @ USB cable attached (0x23a888 == 1): the PC may own the card; never write
    ldr r0, [r0]
    cmp r0, #1
    beq fl_out
    ldr r0, =0x{LVL:x}          @ floor: at END, write only if the latest raw is back at or above the END threshold
    ldrb r0, [r0]
    cmp r0, #0
    bne 1f
    ldr r0, =0x{RAW:x}
    ldrh r0, [r0]
    ldr r1, =0x{END_T:x}
    ldrh r1, [r1]
    cmp r0, r1
    blo fl_out
1:  bl emit
    ldr r0, =path
    movs r1, #2
    ldr r3, =0x{CREATE | 1:x}
    bl call_r3
    movs r4, r0
    adds r0, #1
    beq fl_out
    ldr r1, =header
    movs r2, #{len(HEADER)}
    bl wr
    ldr r1, =$koma.manifest
    adds r1, #8                 @ build id
    movs r2, #4
    bl wr
    ldr r1, =0x{DRV:x}          @ battery driver block: ADC cache, strobe state, thresholds, counters, level, type
    movs r2, #{DRV_LEN}
    bl wr
    ldr r1, =0x{CAMSTATE_W:x}   @ camera-state word
    movs r2, #4
    bl wr
    ldr r1, =seg                @ log state + ring
    ldr r2, ={STATE_LEN}
    bl wr
    movs r0, r4
    ldr r3, =0x{CLOSE | 1:x}
    bl call_r3
fl_out:
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

wr:                             @ write(fd r4, r1, r2); a failed write is not retried
    push {{lr}}
    movs r0, r4
    ldr r3, =0x{WRITE | 1:x}
    bl call_r3
    pop {{r3}}
    bx r3

call_r3:
    bx r3
.ltorg
.p2align 2
path: .asciz "{LOG_PATH}"
.p2align 2
header: .ascii "{HEADER.decode()}"
.p2align 2
@ seg: +0 t0, +4 n, +5 min, +6 max, +8 idx, +9 lvl|type<<4, +10 flags, +12 boot tick, +16 head, +18 total, +20 started
seg:
    .space 24
smin:
    .byte {", ".join(["0xff"] * 16)}
slow:
    .space 16
ring:
    .space {ring * REC}
"""


def make(opts):
    ring = _int_opt(opts, "ring", DEF_RING, 16, 256)
    period = _int_opt(opts, "period", DEF_PERIOD, 1000, 60000)
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=dict(ring=ring, period=period),
        source=source(ring, period),
        hooks=[Hook(POLL_SITE, READ, "bl_poll", "next",
                    "battery poll 0x5290c: the CPU1 battery read; sample folded into the log after it returns",
                    early_ok=EARLY_OK, smoke_reachable=False),
               Hook(POWEROFF_SITES[0], POWEROFF, "bl_off1", "next_off1",
                    "0x32b8c power-off (branch A): write /ATA1/KOMABATT.TXT, then 0x293f4(0xb) and the settings save",
                    smoke_reachable=False),
               Hook(POWEROFF_SITES[1], POWEROFF, "bl_off2", "next_off2",
                    "0x32b8c power-off (branch B): write /ATA1/KOMABATT.TXT, then 0x293f4(0xb) and the settings save",
                    smoke_reachable=False)],
        checks=[Check(CREATE, fw.ram(CREATE, 4), "file create 0x55cf4 prologue"),
                Check(WRITE, fw.ram(WRITE, 4), "file write 0x560ec prologue"),
                Check(CLOSE, fw.ram(CLOSE, 4), "file close 0x55f0c prologue"),
                Check(0x1E1B0, fw.ram(0x1E1B0, 12), "tick getter 0x1e1b0 (via 0x5df20): reads the word at 0x1a8a00"),
                Check(0x1E180, fw.ram(0x1E180, 4), "0x1e1b0 literal = 0x1a8a00"),
                Check(0x5290C, fw.ram(0x5290C, 0x20), "battery poll head: read into [sp] at 0x52918"),
                Check(0x521DC, fw.ram(0x521DC, 0x1C), "0x521dc: REQ 0x1e read, raw cached at 0x2b9e66")],
        smoke=smoke_checks)


# ---------------------------------------------------------------- decoding (tools/koma_battlog_decode.py)

def parse(data: bytes) -> dict:
    """Decode a KOMABATT.TXT (binary after the 12-byte text magic). A short file decodes as far as it goes."""
    import struct
    d = bytes(data)
    out = dict(header_ok=d.startswith(HEADER), build=None, snapshot=None, min_by_index=None, low_by_index=None,
               records=[], complete=False, bytes=len(d))
    if len(d) < 72 or not out["header_ok"]:
        return out
    out["build"] = d[12:16].hex().upper()
    v = d[16:16 + DRV_LEN]
    u16 = lambda o: struct.unpack(">H", v[o - 0x64:o - 0x62])[0]   # noqa: E731  (o = low byte of 0x2b9eXX)
    out["snapshot"] = dict(adc=[u16(0x64 + 2 * i) for i in range(6)], raw=u16(0x66), strobe=v[0x74 - 0x64],
                           end=u16(0x80), near=u16(0x82), half=u16(0x8A), level=v[0x92 - 0x64],
                           index=v[0x93 - 0x64], type=v[0x95 - 0x64],
                           camstate=struct.unpack(">I", d[68:72])[0])
    st = d[72:]
    if len(st) < SEG_LEN:
        return out
    head, total = struct.unpack(">HH", st[16:20])
    out["snapshot"]["records_total"] = total
    out["min_by_index"] = {i: x for i, x in enumerate(st[24:40]) if x != 0xFF}
    out["low_by_index"] = {i: x for i, x in enumerate(st[40:56]) if x}
    ring = st[SEG_LEN:]
    n = len(ring) // REC
    out["complete"] = n > 0 and len(ring) == n * REC
    order = range(total) if total < n else [(head + i) % n for i in range(n)]
    for i in order:
        b = ring[i * REC:(i + 1) * REC]
        out["records"].append(dict(t=int.from_bytes(b[0:2], "big"), index=b[2], level=b[3] & 0xF, type=b[3] >> 4,
                                   min=b[4], max=b[5], strobe=b[6] & 0xF, replaced=bool(b[6] & 0x10),
                                   below_end=bool(b[6] & 0x20), n=b[7]))
    return out


def smoke_checks(obs):
    """The poll hook was not hit inside the smoke shot window so it is SKIP there.
    Check that the stock poll ran at all in the model: its raw cache holds the stub CPU1 reply (grdemu 0xc0)."""
    raw = int.from_bytes(obs.ram(RAW, 2), "big")
    return [("stock battery poll ran in the model (raw cache = stub reply 0xc0)", raw == 0xC0, f"raw={raw:#x}")]
