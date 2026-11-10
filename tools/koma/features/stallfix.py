"""stallfix: candidate fixes for the second-session tether stall, each one
switchable at runtime from the host through one Koma settings byte, so one session can A/B them.

Status: STATIC only. Every site, before-byte and callee below was read from the 2.40 image; the stall hypothesis
(H1) is plausible, not confirmed.

Flag byte FLAGS = 0x1b8be (settings block offset 0x8be). It is a Koma byte that no feature and no ROWS entry uses
(linker.ROW_BYTES 0x8b5..0x8cf, row bytes 0x8b5..0x8bd, 0x8bf..0x8cd taken), so ksettings' -KSET accepts any value
0..255 there (count table 0: "no Koma row of this build"), writes it in any app state (state 9 included; only menu/ADJ
open answers busy) and never runs the recipe reconcile (that starts at 0x8c0). Read at every use, so a -KSET takes
effect on the next event. It persists with the settings block like every Koma byte. Encoding (self-validating, because
an unset settings block holds 0xff there):

    high nibble 0xA: the low nibble is the fix mask      any other value (0xff, 0x00, ...): DEFAULT_BITS
    bit 0 (1) fix 1   0xa201 no-op in the state-9 USB runner while USB is attached
    bit 1 (2) fix 2   no 60 s lens auto-retract timer armed while USB is attached
    bit 2 (4) fix 3   LCD mutex leak guard on the capture path
    bit 3 (8) fix 4   panel on at tether arm
    DEFAULT_BITS = 0xd (fixes 1, 3, 4 on; fix 2 off). Host values: 160 (0xa0) all off, 173 (0xad) default, 175 all on.

Fix 1, redirect 0x6d778 (`bl 0x4544c`, before f7d7fe68), state-9 sub-state-0 handler 0x6d6f0, message 0xa201 (posted
3 s after `-VER` by 0x114824). Stock 0x6d778..0x6d796 runs, in order: 0x4544c (USB-idle power step: if 0x3e8d4() != 0
it clears bit 0 of 0x12014068, calls 0x254d0(0x59), 0x50018(8), 0x50418(); else a branch with ~0.5 s sleeps),
0x3da80(2) (LCD panel off: [0x1b97dc] 1 -> 2 and 0x2328c(0), which takes the LCD mutex [0x1a8d14] with a blocking
10,001-tick wait), 0xc8328 (0x5bc00(2, 0x1c1c7c, 0) and 0x5bd44(2, 0, 0)), the lens retract 0x29fbe with
[0x1bef60] = 2 when [0x1bef60] == 1, and [0x1bef5c] = 1 (sub-state 1, whose 0x5103 handling retracts at once). With
the fix on, USB attached ([0x23a888] == 1) and not the service boot ([0x23a88c] == 0), the handler skips ALL of these
and leaves through 0x6d6f0's own epilogue 0x6d7dc (the runner 0x6d670 ignores the return value, 0x6d6da -> 0x6d6ca):
the runner stays in sub-state 0 exactly as before the message. User-visible: the LCD stays on in state 9 and the lens
is not retracted by 0xa201 while the cable is in. Otherwise the original call runs (tail call, lr = 0x6d77d).

Fix 2, redirect 0x284e6 (`bl 0x28f08`, before f000fd0f) in the transition 0x28378: record -> {1, 6, 9} with the lens
out arms timer 2 (60,000 ticks -> 0x5103, the auto-retract). With the fix on and USB attached (not the service boot)
the timer is not armed (returns r0 = 0; 0x3eabc, next, takes no argument). It does NOT cancel a timer that is already
armed (e.g. by a record exit before the cable went in; only 0x28fa4(2) on record entry cancels): after such an arm
[0x1bef60] can still become 1. Default OFF (H2, ~25%). With fix 1 on, the 0x5103 in sub-state 0 only sets [0x1bef60]
(0x6d75e); no retract happens while attached either way, so fix 2 matters only with fix 1 off.

Fix 3, redirect 0x9765e (`bl 0x23578`, before f78bff8b): the only caller of 0x23578, on the capture path 0x975f0,
reached when [[0x175378] + 0xc8] == 'Z' and [0x23b084] == 0. Stock 0x23578: 0x5db18([0x1a8d20]); 0x21860();
0x2291c() (try-take of [0x1a8d14], result ignored); if [0x1a8d40] in {2, 3, 4} 0x2302e(mode) (async completion
releases through 0x223c8); [0x1a8d54] = 0. A successful try-take with the panel mode outside 2..4 is never released:
the count-1 mutex stays taken and every later BLOCKING acquire (0x228e8, 10,001 ticks; e.g. 0x237e2 in 0x237d0 at
record entry, 0x2328c in 0x3da80) times out after 10 s. Try-takes (0x2291c, 0x234e8) return at once. The fix re-runs
the same sequence and adds exactly one call: on that path (take succeeded AND mode not 2..4) 0x2293c(h), which releases
only when h == [0x1a8d14]. Modes 2..4 and a failed take are unchanged. It cannot repair a mutex already drained before
the fix was switched on; no "heal" is attempted (a release without a matching take would raise the count above 1).
Fix off: tail call to the stock 0x23578.

Fix 4: tether 1.8 calls the resident word `$tether.arm_hook` (0 = none) right after the divergence that leaves state 9;
this feature's init stores sf_arm there. With the fix on it calls 0x3da80(1) and 0x3db8c(1), the panel and backlight
restore the runner dispatcher 0x28560 performs for state 9 (0x285c4..0x285cc). Both are state-tracked: 0x3da80(1) is a
no-op when [0x1b97dc] == 1 (panel already on), and when the panel is off it runs the stock power-on (0x2328c(2|3|4),
which takes the LCD mutex with the blocking wait). So the record session runs with the panel in mode 2..4, where
0x23578 cannot leak. Runs in the task that resolves the (0,4) dial event (tAplCtrl, inside 0x28bdc).

Early-site rule: none of the three sites is in the phase-A line set (linker.lint) and none can be mid-call when the
module installs: the loader hook 0x281ee runs on tAplCtrl before its main loop, 0x6d778 (state-9 runner) and 0x284e6
(transition 0x28378) run only on tAplCtrl from that loop, and 0x9765e only during a capture. Plain BL records.
"""
from __future__ import annotations

from .. import firmware as fw
from ..feature import Check, Feature, Redirect

NAME = "stallfix"
VERSION = "1.0"
SUMMARY = "tether stall candidates: 0xa201 no-op, no auto-retract arm, LCD-mutex leak guard, panel on at arm (-KSET 0x8be)"
OPTIONS = {}

FLAGS = 0x1B8BE
FLAGS_OFF = FLAGS - 0x1B000      # 0x8be = 2238, the -KSET offset
VALID = 0xA                      # high nibble
DEFAULT_BITS = 0b1101            # fixes 1, 3, 4
FIX1, FIX2, FIX3, FIX4 = 1, 2, 4, 8
USB_BLOCK = 0x23A864             # +0x24 0x23a888 USB attached, +0x28 0x23a88c service boot

A201_SITE, A201_BEFORE, IDLE_4544C = 0x6D778, bytes.fromhex("f7d7fe68"), 0x4544C
SUB0_EPILOGUE = 0x6D7DC          # add sp,#8; pop {r4-r7}; pop {r3}; bx r3 (0x6d6f0's frame)
TMR_SITE, TMR_BEFORE, TIMER_ARM = 0x284E6, bytes.fromhex("f000fd0f"), 0x28F08
CAP_SITE, CAP_BEFORE, LCD_CAP = 0x9765E, bytes.fromhex("f78bff8b"), 0x23578
LCD_SEM = 0x1A8D14               # +0xc [0x1a8d20] timer, +0x2c [0x1a8d40] panel mode, +0x40 [0x1a8d54]
TMR_STOP, LCD_21860, TRY_TAKE, ASYNC_DONE, RELEASE = 0x5DB18, 0x21860, 0x2291C, 0x2302E, 0x2293C
PANEL, BACKLIGHT = 0x3DA80, 0x3DB8C
TAKE_LR = 0x22905                # caller LR 0x5d978 stores at TCB+0xd0 while 0x228e8 blocks (host probe)


def flag_value(fix1=True, fix2=False, fix3=True, fix4=True) -> int:
    """The -KSET value for a fix set."""
    return VALID << 4 | (FIX1 if fix1 else 0) | (FIX2 if fix2 else 0) | (FIX3 if fix3 else 0) | (FIX4 if fix4 else 0)


def effective_bits(byte: int) -> int:
    """What the camera does with a raw flag byte (mirror of sf_bits)."""
    return byte & 0xF if byte >> 4 == VALID else DEFAULT_BITS


SRC = f"""
@ r0 = effective fix mask (FLAGS byte low nibble when its high nibble is 0xA, else DEFAULT_BITS). Clobbers r1.
sf_bits:
    ldr r1, ={FLAGS:#x}
    ldrb r0, [r1]
    lsrs r1, r0, #4
    cmp r1, #{VALID}
    bne 1f
    movs r1, #0xf
    ands r0, r1
    bx lr
1:  movs r0, #{DEFAULT_BITS}
    bx lr

@ r0 = 1 when USB is attached and this is not the service boot (tether's force condition), else 0. Clobbers r1.
sf_usb:
    ldr r1, ={USB_BLOCK:#x}
    ldr r0, [r1, #0x24]
    cmp r0, #1
    bne 1f
    ldr r0, [r1, #0x28]
    cmp r0, #0
    bne 1f
    movs r0, #1
    bx lr
1:  movs r0, #0
    bx lr

sf_call_r3:
    bx r3

@ Fix 1. Replaces `bl 0x4544c` at 0x6d778 (0x6d6f0, message 0xa201). r4-r7 belong to 0x6d6f0 and are kept.
sf_a201:
    push {{r0, r1, r2, lr}}
    bl sf_bits
    lsrs r0, r0, #1             @ bit 0 -> C
    bcc 8f
    bl sf_usb
    cmp r0, #0
    beq 8f
    add sp, #16                 @ drop r0-r2 and lr: sp is 0x6d6f0's frame again
    ldr r3, ={SUB0_EPILOGUE + 1:#x}
    bx r3                       @ 0x6d6f0 returns; sub-state 0 kept, nothing else done
8:  pop {{r0, r1, r2, r3}}
    mov lr, r3                  @ 0x6d77d
    ldr r3, ={IDLE_4544C + 1:#x}
    bx r3                       @ stock 0x4544c, then 0x6d77c.. as before

@ Fix 2. Replaces `bl 0x28f08` (r0 = 2, r1 = 0, r2 = 60000) at 0x284e6 in 0x28378.
sf_tmr:
    push {{r0, r1, r2, lr}}
    bl sf_bits
    lsrs r0, r0, #2             @ bit 1 -> C
    bcc 8f
    bl sf_usb
    cmp r0, #0
    beq 8f
    pop {{r0, r1, r2, r3}}
    movs r0, #0
    bx r3                       @ not armed; back to 0x284ea
8:  pop {{r0, r1, r2, r3}}
    mov lr, r3
    ldr r3, ={TIMER_ARM + 1:#x}
    bx r3                       @ stock 0x28f08(2, 0, 60000)

@ Fix 3. Replaces `bl 0x23578` at 0x9765e (capture path 0x975f0; no argument, result unused: `b 0x97668` follows).
sf_cap:
    push {{r4, lr}}
    bl sf_bits
    lsrs r0, r0, #3             @ bit 2 -> C
    bcs 1f
    pop {{r4}}
    pop {{r3}}
    mov lr, r3
    ldr r3, ={LCD_CAP + 1:#x}
    bx r3                       @ fix off: stock 0x23578, returns to 0x97663
1:  ldr r4, ={LCD_SEM:#x}
    ldr r0, [r4, #0xc]          @ [0x1a8d20]
    ldr r3, ={TMR_STOP + 1:#x}
    bl sf_call_r3               @ 0x5db18
    ldr r3, ={LCD_21860 + 1:#x}
    bl sf_call_r3               @ 0x21860
    ldr r3, ={TRY_TAKE + 1:#x}
    bl sf_call_r3               @ 0x2291c: r0 = [0x1a8d14] when taken, else 0
    ldr r1, [r4, #0x2c]         @ panel mode [0x1a8d40]
    subs r2, r1, #2
    cmp r2, #2
    bhi 2f                      @ mode not 2..4: stock never releases this take
    movs r0, r1                 @ stock passes r0 = mode
    ldr r3, ={ASYNC_DONE + 1:#x}
    bl sf_call_r3               @ 0x2302e: async completion releases (stock path)
    b 3f
2:  cmp r0, #0
    beq 3f                      @ try-take failed: nothing held, nothing to release
    ldr r3, ={RELEASE + 1:#x}
    bl sf_call_r3               @ 0x2293c(h): releases only when h == [0x1a8d14]
3:  movs r0, #0
    str r0, [r4, #0x40]         @ [0x1a8d54] = 0
    pop {{r4}}
    pop {{r3}}
    bx r3

@ Fix 4. Called by tether 1.8 (`$tether.arm_hook`) after the divergence that leaves state 9. Keeps r4-r7.
sf_arm:
    push {{r4, lr}}
    bl sf_bits
    lsrs r0, r0, #4             @ bit 3 -> C
    bcc 9f
    movs r0, #1
    ldr r3, ={PANEL + 1:#x}
    bl sf_call_r3               @ 0x3da80(1): panel on (no-op when already on)
    movs r0, #1
    ldr r3, ={BACKLIGHT + 1:#x}
    bl sf_call_r3               @ 0x3db8c(1): backlight on
9:  pop {{r4}}
    pop {{r3}}
    bx r3

@ init: register fix 4 with tether.
sf_init:
    push {{lr}}
    ldr r0, =$tether.arm_hook
    ldr r1, =sf_arm
    adds r1, #1                 @ Thumb
    str r1, [r0]
    pop {{r3}}
    bx r3
.ltorg
"""


def make(opts):
    for site, before in ((A201_SITE, A201_BEFORE), (TMR_SITE, TMR_BEFORE), (CAP_SITE, CAP_BEFORE)):
        if fw.ram(site, 4) != before:
            raise ValueError(f"stallfix: before-bytes at {site:#x} are {fw.ram(site, 4).hex()}, not {before.hex()}")
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options=opts, source=SRC, init="sf_init", requires=["tether"],
        redirects=[Redirect(A201_SITE, A201_BEFORE, "sf_a201",
                            "fix 1: state-9 0xa201 (screen off, idle power step, lens retract) is a no-op while USB is attached"),
                   Redirect(TMR_SITE, TMR_BEFORE, "sf_tmr",
                            "fix 2: no 60 s lens auto-retract timer armed while USB is attached (default off)"),
                   Redirect(CAP_SITE, CAP_BEFORE, "sf_cap",
                            "fix 3: release the capture-path LCD mutex try-take when the panel mode is not 2..4")],
        checks=[Check(0x6D77C, fw.ram(0x6D77C, SUB0_EPILOGUE + 8 - 0x6D77C),
                      "0x6d6f0: rest of the 0xa201 case (0x3da80(2), 0xc8328, retract, sub-state 1) and its epilogue"),
                Check(0x6D6C6, fw.ram(0x6D6C6, 0x1A), "runner 0x6d670: return value of 0x6d6f0 ignored (0x6d6da -> 0x6d6ca)"),
                Check(0x284D8, fw.ram(0x284D8, TMR_SITE - 0x284D8), "0x28378: lens-out test and r0/r1/r2 = 2/0/60000"),
                Check(TMR_SITE + 4, fw.ram(TMR_SITE + 4, 4), "0x28378: 0x3eabc (no argument) after the arm"),
                Check(0x97650, fw.ram(0x97650, CAP_SITE - 0x97650), "0x975f0: 'Z' and [0x23b084] gates before the call"),
                Check(CAP_SITE + 4, fw.ram(CAP_SITE + 4, 2), "0x975f0: `b 0x97668` after the call (result unused)"),
                Check(LCD_CAP, fw.ram(LCD_CAP, 0x38), "0x23578 (re-implemented by fix 3) and its literal 0x1a8d14"),
                Check(TRY_TAKE, fw.ram(TRY_TAKE, 0x20), "0x2291c try-take: r0 = [0x1a8d14] or 0"),
                Check(RELEASE, fw.ram(RELEASE, 0x18), "0x2293c(h): release only when h == [0x1a8d14]"),
                Check(0x285C4, fw.ram(0x285C4, 0x0C), "0x28560: 0x3da80(1), 0x3db8c(1) for state 9 (what fix 4 repeats)")])
