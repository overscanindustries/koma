"""clockkeep: keep a last-known-good time on the card and restore it when the camera boots with a lost clock.

The original GR DIGITAL forgets the date about a week after the main battery comes out (its backup cell drains;
Ricoh FAQ, Feb 2008). The DB-60 charges outside the camera, so on a body with a weak cell every charge resets the
clock. Static facts this feature relies on (derived from the firmware code):

  * The RTC sits behind CPU1. CPU1 requests (table 0x181674, 0x44-byte entries): 0x12 REQ_GET_RTC_DATETIME (f2e2),
    0x13 REQ_SET_RTC_DATETIME (f2f2), 0x14 REQ_SET_RTC_UPDATE, 0x15 REQ_GET_RTC_XSTP (f2f1), 0x16 REQ_SET_RTC_XSTP.
  * 0x51b54 (CPU1 task init) reads XSTP: bit 0x10 set (oscillator stopped = time lost) -> status byte 0x2b9e30 = 0,
    else 8.
  * MainCtrl 0x281e8 calls 0x1e7ac first; at 0x1ea52..0x1ea6a, unless (status 8 and the persisted "date set by the
    user" byte 0x1b0ca == 1), it sets the power-on prompt flag 0x23a878 = 1, and when status is 0 it posts the
    firmware default 2005-01-01 00:00:00 (0x1775bc) through 0x326ce with flag byte 1, which does NOT clear XSTP. So a
    clock that lost power resets to 2005-01-01 at every power-on until the user commits DATE SETTINGS.
  * 0x326ce posts message 0x7105 (12-byte buffer) to qOPE; the CPU1 task runs 0x51bd4: SET_RTC_DATETIME
    [sec, min, hour, -, day, month|0x80, year] (BCD); buffer flag byte +8 == 0 (the DATE SETTINGS commit, 0xdbfa8)
    also sends SET_RTC_XSTP(0), sets status 8 and 0x1b0ca = 1; then SET_RTC_UPDATE(0xf8).
  * EXIF (0x1156c8) and FAT (0xb9130) stamps both read the RAM copy 0x2b9e34 [sec, min, hour, day, month, year, 0x20]
    through 0x3272a; the CPU1 task refreshes that copy from GET_RTC_DATETIME (0x51c7c) on CPU1's RTC event.
  * The module installs right after 0x1e7ac (loader hook 0x281ee) and before 0x66bb8 (0x28248), which shows the
    power-on date prompt when 0x23a878 == 1.

What it does, once, at install (resident, for the power-off hooks below):
  * Clock lost (status 0) and /ATA1/KOMATIME.TXT holds a valid 2006..2035 time: post that time (+ `offset` minutes
    when the file carries clockkeep's header; a bare date line from a PC is taken literally) through 0x326ce with flag 0 (the firmware's own DATE SETTINGS path: clears XSTP, marks the date as set), write the
    same value into the RAM copy so the first shots are stamped at once, and (prompt=skip) clear the prompt flag.
    The firmware's 2005 default was posted earlier, so ours lands after it in the same queue.
  * Clock running and user-set (status 8, 0x1b0ca == 1) and the RAM copy holds a valid time: rewrite KOMATIME.TXT
    when the clock is `every` minutes past the stored time, or earlier than it (the user set it back). Otherwise no
    card write. A clock that is valid is never changed.
  * Anything else: nothing (stock behaviour).
At power-off: the boot-time save usually finds the RAM copy not yet refreshed (decision NOT READY: CPU1's first RTC event comes after the module installs), so the save also runs at
power-off, where the clock has long been read. Hooks 0x32d3e / 0x32d82 (`bl 0x293f4(0xb)` in the power-off 0x32b8c,
after the lens retract and just before the settings save 0x4949c; the same sites battlog writes KOMABATT.TXT from):
when the clock is running and user-set (status 8, 0x1b0ca == 1) and the RAM copy holds a valid 2006..2035 time,
rewrite KOMATIME.TXT with it. Skipped while USB is attached (0x23a888 == 1) and at battery END below the END threshold
(battlog's floor, the stock image-write floor). Then 0x293f4(0xb) with the caller's r0..r3. The outcome goes to
settings bytes 0x1b8e4..0x1b8e7 ('P', decision, flags, saves so far), written before the firmware's settings save,
so the next boot's PARAM.CUR shows it at 0x8e4.
The decision is left in settings bytes 0x1b8e0..0x1b8e3 ('C', decision, flags, file state) for the telemetry CLOCK
field (telemetry.clock=on) and is decoded by `describe`.

KOMATIME.TXT: "KOMA TIME V1\\nYYYY-MM-DD HH:MM:SS\\n" (33 bytes). A file that is just the date line also works, so
the user can seed the clock from a PC (`seed_text`).
"""
from __future__ import annotations

import datetime as _dt

from .. import firmware as fw
from ..feature import Check, Feature, Hook, Settings

NAME = "clockkeep"
SUMMARY = ("restore a lost clock from /ATA1/KOMATIME.TXT at power-on; save the time there on healthy boots and at "
           "power-off")
OPTIONS = {"offset": "minutes added on restore to a time clockkeep saved (default 60, 0..1440); not to a PC seed line",
           "every": "minutes the clock must be past the stored time before a healthy boot rewrites it (default 15)",
           "prompt": "skip (default): no power-on date prompt after a restore | keep: leave the firmware's prompt"}

PATH = "/ATA1/KOMATIME.TXT"
HEADER = b"KOMA TIME V1\n"
TEXT_LEN = len(HEADER) + 20
OPEN, READ, CREATE, WRITE, CLOSE = 0x55DC0, 0x56024, 0x55CF4, 0x560EC, 0x55F0C
POST_DATE = 0x326CE              # FUN_000326ce(buf): qOPE message 0x7105, 12 bytes -> 0x51bd4
RTC_STATUS, RAM_CLOCK, USER_SET, PROMPT = 0x2B9E30, 0x2B9E34, 0x1B0CA, 0x23A878
RESULT = 0x1B8E0                 # 4 settings bytes owned by this feature: the per-body KSIO reserve 0x1b8dd..0x1b8ff
                                 # (excluded from PARAM.DAT imports; 0x1b8bc is KOMA FEATURES, 0x1b8bf PEAKING)
OFF_RESULT = 0x1B8E4             # 4 more: the last power-off ('P', decision, flags, saves so far, saturating at 255)
POWEROFF_SITES, POWEROFF = (0x32D3E, 0x32D82), 0x293F4    # as battlog: bl 0x293f4(0xb) in 0x32b8c, before the save
USB = 0x23A888                   # word, 1 = cable attached
LVL, RAW, END_T = 0x2B9E92, 0x2B9E66, 0x2B9E80            # battery level (0 END), raw cache, END threshold in force
YMIN, YMAX = 6, 35               # 2006..2035: below is a reset default, above the firmware's date screen clamps
BUFN = 40

D_RESTORED, D_LOST_NOFILE, D_SAVED, D_SAME, D_NOTREADY, D_UNSET, D_SAVEFAIL, D_SKIPPED = 1, 2, 3, 4, 5, 6, 7, 8
DECISIONS = {D_RESTORED: "RESTORED: clock was lost; set from KOMATIME.TXT (+offset)",
             D_LOST_NOFILE: "LOST: clock was lost; no valid KOMATIME.TXT, firmware default 2005-01-01 kept",
             D_SAVED: "SAVED: clock valid; KOMATIME.TXT rewritten",
             D_SAME: "OK: clock valid; stored time recent enough, no write",
             D_NOTREADY: "NOT READY: clock valid but the RAM copy was not refreshed yet (or invalid), no write "
                         "(at boot: the power-off save covers it)",
             D_UNSET: "UNSET: oscillator running but the date was never committed (at power-off: or the clock is "
                      "lost); nothing done",
             D_SAVEFAIL: "SAVE FAILED: KOMATIME.TXT create/write failed",
             D_SKIPPED: "SKIPPED (power-off): USB attached, or battery END below the END threshold; no write"}
FILE_STATES = {0: "no file", 1: "valid", 2: "unreadable or invalid"}

# Stock bytes the behaviour depends on (verified, never written).
CHECKS = [
    (0x1EA52, 30, "0x1e7ac boot RTC check: prompt flag 0x23a878 and the 2005 default post via 0x326ce(0x1775bc)"),
    (0x1EBB8, 4, "0x1e7ac literal: 0x23a86c state block (prompt flag at +0x14)"),
    (0x1EBC0, 4, "0x1e7ac literal: 0x1775bc default date"),
    (0x1775BC, 12, "firmware default date 2005-01-01 00:00:00, flag 1"),
    (0x51B54, 0x28, "0x51b54 REQ_GET_RTC_XSTP -> status 0x2b9e30 (0 lost, 8 running)"),
    (0x51B7C, 0x30, "0x51b7c REQ_SET_RTC_XSTP(0) + 0x1b0ca on a flag-0 set"),
    (0x51BD4, 0x5C, "0x51bd4 qOPE 0x7105 handler: SET_RTC_DATETIME, flag byte +8, SET_RTC_UPDATE"),
    (0x51C9C, 0x50, "0x51c7c RAM copy layout 0x2b9e34 [sec,min,hour,day,month,year,0x20]"),
    (0x326CE, 0x22, "0x326ce post date (0x8a678 copies 12 bytes)"),
    (0x8A8C6, 8, "qOPE 0x7105 -> 0x51bd4(msg+0xc)"),
    (0x281EA, 4, "MainCtrl: bl 0x1e7ac before the loader hook"),
    (0x28248, 4, "MainCtrl: bl 0x66bb8 (power-on prompt reads 0x23a878) after the loader hook"),
    (OPEN, 4, "file open 0x55dc0 prologue"), (READ, 4, "file read 0x56024 prologue"),
    (CREATE, 4, "file create 0x55cf4 prologue"), (WRITE, 4, "file write 0x560ec prologue"),
    (CLOSE, 4, "file close 0x55f0c prologue"),
]


def template() -> bytes:
    return HEADER + b"2000-00-00 00:00:00\n"


def seed_text(t: _dt.datetime, header: bool = True) -> bytes:
    """KOMATIME.TXT contents for time `t`. header=True: as clockkeep writes it (restore adds the offset);
    header=False: a bare date line, the form to write from a PC to seed the clock (restored exactly)."""
    return (HEADER if header else b"") + t.strftime("%Y-%m-%d %H:%M:%S").encode() + b"\n"


def parse_text(data: bytes):
    """Host mirror of the on-camera parser: datetime or None."""
    data = bytes(data)
    if data[:1] == b"K":
        data = data[len(HEADER):]
    if len(data) < 19 or data[:2] != b"20":
        return None
    try:
        v = [int(data[a:a + 2]) for a in (2, 5, 8, 11, 14, 17)]
        if not all(data[a:a + 2].isdigit() for a in (2, 5, 8, 11, 14, 17)):
            return None
        t = _dt.datetime(2000 + v[0], v[1], v[2], v[3], v[4], v[5])
    except ValueError:
        return None
    return t if YMIN <= v[0] <= YMAX else None


def describe(word: int) -> dict:
    """Decode the 0x1b8e0 word (telemetry CLOCK field), or the 0x1b8e4 power-off word ('P')."""
    b = word.to_bytes(4, "big")
    if b[0] == 0x50:
        return dict(valid=True, poweroff=True, decision=DECISIONS.get(b[1], f"unknown {b[1]}"),
                    rtc_status=b[2] & 0x0F, owner_set_0x1b0ca=bool(b[2] & 0x10), saves=b[3])
    if b[0] != 0x43:
        return dict(valid=False, note="no 'C' marker: clockkeep did not run in this boot")
    return dict(valid=True, decision=DECISIONS.get(b[1], f"unknown {b[1]}"), rtc_status=b[2] & 0x0F,
                owner_set_0x1b0ca=bool(b[2] & 0x10), prompt_flag_at_install=bool(b[2] & 0x20),
                file=FILE_STATES.get(b[3], f"unknown {b[3]}"))


def source(offset: int, every: int, skip_prompt: bool) -> str:
    clear_prompt = f"""
    ldr r1, =0x{PROMPT:x}
    movs r0, #0
    str r0, [r1]                @ prompt=skip: no power-on DATE prompt (0x66bb8 reads it right after the loader)""" \
        if skip_prompt else "    @ prompt=keep: the firmware still shows its power-on date prompt"
    return f"""
@ clockkeep init (transient): r0 = service table, r1 = body base. Runs in MainCtrl after 0x1e7ac, IRQs on.
.equ D_RESTORED, {D_RESTORED}
.equ D_LOST_NOFILE, {D_LOST_NOFILE}
.equ D_SAVED, {D_SAVED}
.equ D_SAME, {D_SAME}
.equ D_NOTREADY, {D_NOTREADY}
.equ D_UNSET, {D_UNSET}
.equ D_SAVEFAIL, {D_SAVEFAIL}
.equ D_SKIPPED, {D_SKIPPED}
ck_init:
    push {{r4, r5, r6, r7, lr}}
    ldr r1, =0x{OFF_RESULT:x}   @ power-off word: start clean the first time (no 'P' yet), else keep its save count
    ldrb r0, [r1]
    cmp r0, #0x50
    beq 0f
    movs r0, #0
    str r0, [r1]
0:
    ldr r0, =0x{RTC_STATUS:x}
    ldrb r4, [r0]               @ r4 = RTC status: 0 = XSTP (time lost), 8 = running
    ldr r0, =0x{USER_SET:x}
    ldrb r5, [r0]               @ r5 = 1: date committed through DATE SETTINGS (persisted)
    ldr r0, =0x{PROMPT:x}
    ldr r6, [r0]                @ r6 = power-on prompt flag set by 0x1e7ac
    lsls r0, r4, #28
    lsrs r0, r0, #28
    cmp r5, #1
    bne 1f
    adds r0, #0x10
1:  cmp r6, #0
    beq 2f
    adds r0, #0x20
2:  ldr r1, =res
    strb r0, [r1, #2]
    bl load                     @ r0 = 0 no file, 1 valid (sto), 2 unreadable/invalid
    movs r7, r0
    ldr r1, =res
    strb r0, [r1, #3]
    cmp r4, #0
    bne healthy
    movs r0, #D_LOST_NOFILE
    cmp r7, #1
    bne done
    ldr r0, =hdr
    ldrb r0, [r0]
    cmp r0, #0
    beq 3f                      @ a bare date line (seeded from a PC) is taken literally
    ldr r0, =sto
    bl add_offset
3:  bl restore
    movs r0, #D_RESTORED
    b done
healthy:
    movs r0, #D_UNSET
    cmp r4, #8
    bne done
    cmp r5, #1
    bne done
    bl read_now                 @ r0 = 1: RAM copy is a valid 2006..2035 time (now)
    movs r1, r0
    movs r0, #D_NOTREADY
    cmp r1, #1
    bne done
    cmp r7, #1
    bne do_save
    ldr r0, =now
    bl minutes
    movs r6, r0
    ldr r0, =sto
    bl minutes
    cmp r6, r0
    blo do_save                 @ clock earlier than the file: the owner set it back, the file follows
    subs r6, r6, r0
    ldr r1, ={every}
    cmp r6, r1
    bhs do_save
    movs r0, #D_SAME
    b done
do_save:
    bl save                     @ r0 = 0 written
    movs r1, r0
    movs r0, #D_SAVED
    cmp r1, #0
    beq done
    movs r0, #D_SAVEFAIL
done:
    ldr r1, =res
    strb r0, [r1, #1]
    ldr r0, [r1]                @ 'C', decision, flags, file state
    ldr r1, =0x{RESULT:x}
    str r0, [r1]
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

@ ---- KOMATIME.TXT -> sto. r0 = 0 no file, 1 valid, 2 unreadable or invalid
load:
    push {{r4, r5, lr}}
    ldr r0, =path
    movs r1, #0                 @ read (flag bit 9 would create)
    ldr r3, =0x{OPEN | 1:x}
    bl call_r3
    movs r4, r0
    adds r0, #1
    beq ld_none
    movs r0, r4
    ldr r1, =buf
    movs r2, #{BUFN}
    ldr r3, =0x{READ | 1:x}
    bl call_r3
    movs r5, r0
    movs r0, r4
    ldr r3, =0x{CLOSE | 1:x}
    bl call_r3
    ldr r1, =buf
    ldrb r0, [r1]
    cmp r0, #0x4b               @ 'K': header line present (written by clockkeep: the offset applies)
    bne 1f
    adds r1, #{len(HEADER)}
    subs r5, #{len(HEADER)}
    ldr r2, =hdr
    movs r0, #1
    strb r0, [r2]
1:  movs r0, #2
    cmp r5, #19
    blt ld_out
    movs r0, r1
    ldr r1, =sto
    bl parse
    cmp r0, #1
    beq ld_out
    movs r0, #2
    b ld_out
ld_none:
    movs r0, #0
ld_out:
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

@ r0 = "20YY-MM-DD HH:MM:SS", r1 = fields [yy, mo, dd, hh, mi, ss] -> r0 = 1 valid
parse:
    push {{r4, r5, r6, lr}}
    movs r4, r0
    movs r5, r1
    ldrb r0, [r4]
    cmp r0, #0x32
    bne p_bad
    ldrb r0, [r4, #1]
    cmp r0, #0x30
    bne p_bad
    movs r6, #0
1:  ldr r1, =offs
    ldrb r1, [r1, r6]
    adds r0, r4, r1
    bl d2
    strb r0, [r5, r6]
    adds r6, #1
    cmp r6, #6
    blo 1b
    movs r0, r5
    bl valid
    b p_out
p_bad:
    movs r0, #0
p_out:
    pop {{r4, r5, r6}}
    pop {{r3}}
    bx r3

d2:                             @ r0 = two ASCII digits -> 0..99, or 255. Clobbers r1, r2
    ldrb r1, [r0]
    subs r1, #0x30
    cmp r1, #9
    bhi 1f
    ldrb r2, [r0, #1]
    subs r2, #0x30
    cmp r2, #9
    bhi 1f
    lsls r0, r1, #3
    lsls r1, r1, #1
    adds r0, r0, r1
    adds r0, r0, r2
    bx lr
1:  movs r0, #255
    bx lr

@ r0 = fields -> r0 = 1 when {YMIN}..{YMAX}, month, day of month, hour, minute and second are in range
valid:
    push {{r4, lr}}
    movs r4, r0
    ldrb r0, [r4]
    cmp r0, #{YMIN}
    blo v_bad
    cmp r0, #{YMAX}
    bhi v_bad
    ldrb r0, [r4, #3]
    cmp r0, #23
    bhi v_bad
    ldrb r0, [r4, #4]
    cmp r0, #59
    bhi v_bad
    ldrb r0, [r4, #5]
    cmp r0, #59
    bhi v_bad
    ldrb r0, [r4, #1]
    subs r0, #1
    cmp r0, #11
    bhi v_bad
    ldrb r0, [r4, #2]
    cmp r0, #0
    beq v_bad
    movs r0, r4
    bl dim
    ldrb r1, [r4, #2]
    cmp r1, r0
    bhi v_bad
    movs r0, #1
    b v_out
v_bad:
    movs r0, #0
v_out:
    pop {{r4}}
    pop {{r3}}
    bx r3

dim:                            @ r0 = fields -> days in that month (leap: yy % 4 == 0, right for 2000..2099)
    ldrb r1, [r0, #1]
    ldr r2, =mdays
    subs r3, r1, #1
    ldrb r2, [r2, r3]
    cmp r1, #2
    bne 1f
    ldrb r3, [r0]
    lsls r3, r3, #30
    bne 1f
    adds r2, #1
1:  movs r0, r2
    bx lr

minutes:                        @ r0 = fields -> minutes since 2000-01-01 00:00
    push {{r4, r5, lr}}
    movs r4, r0
    ldrb r5, [r4]
    ldr r1, =365
    movs r0, r5
    muls r0, r1, r0
    adds r1, r5, #3
    lsrs r1, r1, #2             @ leap days before year yy (2000 is one)
    adds r0, r0, r1
    ldrb r1, [r4, #1]
    subs r1, #1
    lsls r1, r1, #1
    ldr r2, =cum
    ldrh r1, [r2, r1]
    adds r0, r0, r1
    ldrb r1, [r4, #1]
    cmp r1, #2
    bls 1f
    lsls r2, r5, #30
    bne 1f
    adds r0, #1
1:  ldrb r1, [r4, #2]
    subs r1, #1
    adds r0, r0, r1
    movs r1, #24
    muls r0, r1, r0
    ldrb r1, [r4, #3]
    adds r0, r0, r1
    movs r1, #60
    muls r0, r1, r0
    ldrb r1, [r4, #4]
    adds r0, r0, r1
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

add_offset:                     @ r0 = fields += {offset} minutes, carrying into hour, day, month, year
    push {{r4, r5, lr}}
    movs r4, r0
    ldrb r5, [r4, #4]
    ldr r0, ={offset}
    adds r5, r5, r0
1:  cmp r5, #60
    blo 9f
    subs r5, #60
    ldrb r0, [r4, #3]
    adds r0, #1
    strb r0, [r4, #3]
    cmp r0, #24
    blo 1b
    movs r0, #0
    strb r0, [r4, #3]
    ldrb r0, [r4, #2]
    adds r0, #1
    strb r0, [r4, #2]
    movs r0, r4
    bl dim
    ldrb r1, [r4, #2]
    cmp r1, r0
    bls 1b
    movs r0, #1
    strb r0, [r4, #2]
    ldrb r0, [r4, #1]
    adds r0, #1
    strb r0, [r4, #1]
    cmp r0, #12
    bls 1b
    movs r0, #1
    strb r0, [r4, #1]
    ldrb r0, [r4]
    adds r0, #1
    strb r0, [r4]
    b 1b
9:  strb r5, [r4, #4]
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

@ RAM copy 0x{RAM_CLOCK:x} [sec, min, hour, day, month, year, 0x20] (BCD) -> now. r0 = 1 valid
read_now:
    push {{r4, r5, lr}}
    ldr r4, =0x{RAM_CLOCK:x}
    ldrb r0, [r4, #6]
    cmp r0, #0x20
    bne rn_bad
    movs r5, #0
1:  movs r0, #5
    subs r0, r0, r5
    ldrb r0, [r4, r0]
    bl bcddec
    ldr r1, =now
    strb r0, [r1, r5]
    adds r5, #1
    cmp r5, #6
    blo 1b
    ldr r0, =now
    bl valid
    b rn_out
rn_bad:
    movs r0, #0
rn_out:
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

bcddec:                         @ r0 = BCD byte -> 0..99, or 255. Clobbers r1, r2
    lsrs r1, r0, #4
    lsls r2, r0, #28
    lsrs r2, r2, #28
    cmp r1, #9
    bhi 1f
    cmp r2, #9
    bhi 1f
    lsls r0, r1, #3
    lsls r1, r1, #1
    adds r0, r0, r1
    adds r0, r0, r2
    bx lr
1:  movs r0, #255
    bx lr

bcdenc:                         @ r0 = 0..99 -> BCD. Clobbers r1
    movs r1, #0
1:  cmp r0, #10
    blo 2f
    subs r0, #10
    adds r1, #1
    b 1b
2:  lsls r1, r1, #4
    orrs r0, r1
    bx lr

@ sto -> msg (BCD, flag 0 = the DATE SETTINGS commit path) -> RAM copy and 0x{POST_DATE:x}
restore:
    push {{r4, r5, lr}}
    ldr r4, =msg
    movs r5, #0
1:  ldr r0, =sto
    ldrb r0, [r0, r5]
    bl bcdenc
    movs r1, #5
    subs r1, r1, r5
    strb r0, [r4, r1]
    adds r5, #1
    cmp r5, #6
    blo 1b
    ldr r1, =0x{RAM_CLOCK:x}
    movs r5, #0
2:  ldrb r0, [r4, r5]
    strb r0, [r1, r5]
    adds r5, #1
    cmp r5, #7
    blo 2b
    movs r0, r4
    ldr r3, =0x{POST_DATE | 1:x}
    bl call_r3                  @ queued behind the firmware's own 2005 default post
{clear_prompt}
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

@ now -> text -> KOMATIME.TXT (create truncates). r0 = 0 written, 1 failed
save:
    push {{r4, r5, lr}}
    ldr r4, =text
    movs r5, #0
1:  ldr r0, =now
    ldrb r0, [r0, r5]
    ldr r1, =offs
    ldrb r1, [r1, r5]
    adds r1, #{len(HEADER)}
    adds r1, r4, r1
    bl put2
    adds r5, #1
    cmp r5, #6
    blo 1b
    ldr r0, =path
    movs r1, #2                 @ as stock callers; 0x55cf4 ignores it
    ldr r3, =0x{CREATE | 1:x}
    bl call_r3
    movs r4, r0
    adds r0, #1
    beq sv_fail
    movs r0, r4
    ldr r1, =text
    movs r2, #{TEXT_LEN}
    ldr r3, =0x{WRITE | 1:x}
    bl call_r3
    movs r5, r0
    movs r0, r4
    ldr r3, =0x{CLOSE | 1:x}
    bl call_r3
    movs r0, #0
    cmp r5, #{TEXT_LEN}
    beq sv_out
sv_fail:
    movs r0, #1
sv_out:
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

put2:                           @ r0 = 0..99 -> two ASCII digits at r1. Clobbers r2
    movs r2, #0x30
1:  cmp r0, #10
    blo 2f
    subs r0, #10
    adds r2, #1
    b 1b
2:  strb r2, [r1]
    adds r0, #0x30
    strb r0, [r1, #1]
    bx lr

@ ---- power-off (1.1): entered as 0x293f4(r0 = 0xb) at 0x32d3e / 0x32d82, before the settings save
ck_off1:
    push {{r0, r1, r2, r3, r4, lr}}
    bl off_save                 @ preserves r4-r7
    ldr r4, =next_off1
    b ck_off_tail
ck_off2:
    push {{r0, r1, r2, r3, r4, lr}}
    bl off_save
    ldr r4, =next_off2
ck_off_tail:
    mov ip, r4
    pop {{r0, r1, r2, r3, r4}}  @ the caller's arguments and r4 back
    bl call_ip
    pop {{r3}}
    bx r3

call_ip:
    bx ip

off_save:                       @ bounded: at most one create, one write, one close
    push {{r4, r5, lr}}
    ldr r0, =0x{RTC_STATUS:x}
    ldrb r4, [r0]
    ldr r0, =0x{USER_SET:x}
    ldrb r5, [r0]
    lsls r0, r4, #28
    lsrs r0, r0, #28
    cmp r5, #1
    bne 1f
    adds r0, #0x10
1:  ldr r1, =0x{OFF_RESULT:x}
    strb r0, [r1, #2]
    movs r0, #D_SKIPPED
    ldr r1, =0x{USB:x}          @ USB attached: the computer may own the card; never write
    ldr r1, [r1]
    cmp r1, #1
    beq os_done
    ldr r1, =0x{LVL:x}          @ battery END: write only at or above the END threshold (battlog's floor)
    ldrb r1, [r1]
    cmp r1, #0
    bne 2f
    ldr r1, =0x{RAW:x}
    ldrh r1, [r1]
    ldr r2, =0x{END_T:x}
    ldrh r2, [r2]
    cmp r1, r2
    blo os_done
2:  movs r0, #D_UNSET
    cmp r4, #8
    bne os_done
    cmp r5, #1
    bne os_done
    bl read_now                 @ r0 = 1: the RAM copy is a valid 2006..2035 time
    movs r1, r0
    movs r0, #D_NOTREADY
    cmp r1, #1
    bne os_done
    bl save
    movs r1, r0
    movs r0, #D_SAVEFAIL
    cmp r1, #0
    bne os_done
    ldr r1, =0x{OFF_RESULT:x}
    ldrb r2, [r1, #3]
    cmp r2, #255
    beq 3f
    adds r2, #1
    strb r2, [r1, #3]
3:  movs r0, #D_SAVED
os_done:
    ldr r1, =0x{OFF_RESULT:x}
    strb r0, [r1, #1]
    movs r0, #0x50              @ 'P'
    strb r0, [r1]
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

call_r3:
    bx r3
.ltorg
.p2align 2
res:
    .byte 0x43, 0, 0, 0
msg:
    .byte 0, 0, 0, 0, 0, 0, 0x20, 0, 0, 0, 0, 0
sto:
    .byte 0, 0, 0, 0, 0, 0, 0, 0
now:
    .byte 0, 0, 0, 0, 0, 0, 0, 0
cum:
    .hword 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334
mdays:
    .byte 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31
offs:
    .byte 2, 5, 8, 11, 14, 17
hdr:
    .byte 0
path:
    .asciz "{PATH}"
.p2align 2
text:
    .ascii "{template().decode().replace(chr(10), '\\n')}"
.p2align 2
buf:
    .space {BUFN}
.p2align 2
"""


def _opts(opts):
    opts = dict(opts or {})
    offset = int(opts.get("offset", 60))
    every = int(opts.get("every", 15))
    prompt = str(opts.get("prompt", "skip")).lower()
    if not 0 <= offset <= 1440:
        raise ValueError("clockkeep.offset: 0..1440 minutes")
    if not 1 <= every <= 1440:
        raise ValueError("clockkeep.every: 1..1440 minutes")
    if prompt not in ("skip", "keep"):
        raise ValueError("clockkeep.prompt: skip or keep")
    return dict(offset=offset, every=every, prompt=prompt)


def make(opts):
    o = _opts(opts)
    return Feature(
        name=NAME, version="1.1", summary=SUMMARY, options=o,
        source=source(o["offset"], o["every"], o["prompt"] == "skip"), init="ck_init",
        hooks=[Hook(POWEROFF_SITES[0], POWEROFF, "ck_off1", "next_off1",
                    "0x32b8c power-off (branch A): save the clock to /ATA1/KOMATIME.TXT, then 0x293f4(0xb) and the "
                    "settings save", smoke_reachable=False),
               Hook(POWEROFF_SITES[1], POWEROFF, "ck_off2", "next_off2",
                    "0x32b8c power-off (branch B): save the clock to /ATA1/KOMATIME.TXT, then 0x293f4(0xb) and the "
                    "settings save", smoke_reachable=False)],
        checks=[Check(a, fw.ram(a, n), why) for a, n, why in CHECKS],
        settings=[Settings(RESULT, RESULT + 4, True, "clockkeep decision word ('C', decision, flags, file state)"),
                  Settings(OFF_RESULT, OFF_RESULT + 4, True,
                           "clockkeep power-off word ('P', decision, flags, saves so far)")],
        smoke=smoke_checks)


# ---------------------------------------------------------------- koma smoke

def smoke_setup():
    return {}


def smoke_checks(obs):
    """The model's CPU1 answers XSTP with bit 0x10 (time lost) and the smoke card has no KOMATIME.TXT: the expected
    decision is LOST (stock 2005 default kept), with no card write. Restore and save paths are covered by
    the unit tests."""
    w = int.from_bytes(obs.ram(RESULT, 4), "big")
    d = describe(w)
    out = [("decision word written", d["valid"], f"0x1b8e0={w:08x}")]
    if d["valid"]:
        out.append(("model: lost clock, no file -> LOST", (w >> 16) & 0xFF == D_LOST_NOFILE and w & 0xFF == 0,
                    d["decision"]))
    out.append(("no KOMATIME.TXT written", obs.card_file(PATH) is None, ""))
    return out
