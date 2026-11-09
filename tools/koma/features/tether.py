"""tether: fire the shutter over USB with `-CCTL` while the USB session stays up (Koma Setup app phase C).

Why stock cannot (handbook/07 §4.3-4.4): the app-state resolver 0x4fd1e ends at 0x4fe44 with
`if (0x23a888 == 1 && 0x23a88c == 0) state = 9`. 0x23a888 follows the USB attach event (8,1) of UsbShutter 0x131b3a
(the "cable attached" meaning is I). State 9 is the USB state: its runner 0x6d670 starts the session (0x5d5d0(0x800))
and its handlers 0x6d6f0 / 0x6d840 ignore RL1/RL2, so `-CCTL keyset` presses answer 200 and do nothing. Ricoh's own
remote branch in main loop 0x282e4 re-resolves the state when the `remtin` flag 0x1bd1b8 is set, but the force above
ignores that flag, so the re-resolve returns 9 again. Never forcing 9 (`0x4fe4a` d104 -> e004) is REJECTED: state 9 is
what opens the USB session.

This feature is the emu-frontier "remote-aware cave" moved from the Dcfs self-test island into Koma's resident block,
and made smaller: one exclusive BL redirect at 0x4fe44 replaces `ldr r0, =0x23a864 ; ldr r1, [r0, #0x24]`. The handler
returns r0 = 0x23a864 and r1 = 0x23a888 exactly as those two instructions would, so the stock test that follows
(0x4fe48..0x4fe63, unchanged) runs as before, except when all of these hold:

    0x23a888 == 1 and 0x23a88c == 0      (the stock force condition: USB attached, not the service boot)
    [0x1bd1b8] != 0                       (the host sent `-CCTL remtin`; only 0xcd07c, the -CCTL handler, sets it)
    [0x2b9d34] != 0                       (key injection on: the sink 0x51740 sets it on every injected key/dial,
                                           `keyset,0,2` clears it at 0x51754; `diaset,N` turns it on)

Then r1 = 0 (skip the force), and if the stored state is 9 the handler stores 2 and calls 0x4fd1e once, so the state
re-resolves from the (virtual) dial set by `-CCTL diaset,N`. The nested resolve reaches the handler again with a state
other than 9 (the dial map never yields 9), so the recursion depth is 1. The injection term makes the documented release
`keyset,0,2` the end of tether: its dial event re-resolves with injection off, so state 9 is forced again before
`remtout` (v1.0 without it left the camera in the physical dial's state, 3 on the spare's Movie dial, until a physical
dial event; emulator). `-CCTL remtout` (0x4fc84) clears the flag; any later
resolve forces 9 as stock does.

v1.2 (2026-11-07) adds the two exits stock never needed, because stock never leaves state 9 while the cable is in:

  remtout   a second exclusive redirect, at the -CCTL remtout call site 0xcd090 (`bl 0x4fc84`), runs `tether_out`:
            the stock flag clear plus a clear of every injected key, the virtual dial and the -CCTL state (what
            `keyset,0,2` would do, minus its event), then, if USB is attached and the running state 0x23a874 is not 9,
            one dial event (0,4) through the stock poster 0x517dc. The resolve that event triggers forces 9 and ends
            the state-2 runner (0x28d68 -> 0x5105 -> exit code 6 -> main loop 0x282e4 -> runner 9). Stock `keyset,0,2`
            emits that event only when the virtual dial differs from the physical one (0x5175a); with the physical
            dial on STILL nothing re-resolved, so the camera stayed in state 2 after remtout. Returns r0 = 0 as
            0x4fc84 does (`cmp r0,#0` -> 200).
  unplug    the resolver handler itself: no USB ([0x23a888] != 1, written 0 by the (8,0) detach event at 0x4ff0e and
            0x28d66) while the remtin flag is still set means the cable left mid-tether. Stock would keep the camera on
            the virtual STILL dial with injected keys held and the physical dial dead (0x4fe6c turns (8,0) into the
            0x720d power-off only when the stored state is 9). The handler clears the same state as `tether_out`
            and re-resolves once from the physical dial, so 0x28d68 switches the runner to the dial's mode and the
            camera behaves as if never tethered.

v1.3 (2026-11-07) holds Auto Power Off while a tether session is live. Stock state 9 holds it (runner 0x6d670 calls
0x290de(2)), but the record side re-applies the setting (0x290de(1) in the mode handlers at 0x73b2c, 0x75742, 0x7a88a,
0x7b84e, ... and 0x290de(2) at the shot path 0x7b14a), so an idle tethered camera could switch itself off. 0x290de(mode)
(push {lr}; r0 = mode; mode 1 = "apply the setting": the halfword [0x1b080 + 0x1e] 0 off / 1 -> 0x3c / 2 -> 0x12c /
else 0x708 ticks, forced 0 while [0x23a884] == 1; any other mode = 0 = off; then 0x4fce8(ticks) -> 0x323d0 -> 0x5207a)
is stock's own hold/release API (pair 0x4fa08 / 0x4fa28 on [0x1bd1b0]: `if !latch: 0x290de(2)` / `if latch: 0x290de(1)`).
Emulator trace: a hold issued inside the resolve that enters state 2 is NOT
overridden by the state-2 runner entry (0x29b28 runs after it, no 0x290de call), but the record side calls it again
later (shot path, mode handlers), so the hold is RENEWED instead of issued once: tether_force only sets the resident
byte `latch` (1 = tether diverged this session); a third exclusive redirect at 0xcd03a (the `keyset` case of 0xcd000,
`bl 0x65cfc` = mg1_keyset) runs `tether_key`: while the latch is set it calls `0x290de(2)` and then mg1_keyset as
before, i.e. one cheap call per injected key press or release. Both clean exits (tether_out, the unplug branch) call
`tether_release` = `0x290de(1)` if the latch is set, and clear it. Never diverged: nothing is issued anywhere. After
remtout the posted dial event re-resolves to 9 and the state-9 runner holds again (stock USB-state behaviour); after an
unplug the physical dial's record runner re-applies the setting on its own mode entry.

v1.4 (2026-11-07, hardware) fixes the two failures the main camera
showed (physical dial on STILL, Auto Power Off 1 min):

  diaset    `-CCTL diaset,4` left the camera in state 9: -LVFR answered 451 for 8 s and only answered 150 about 3 s
            after the first `keyset`. mg1_dialset 0x65d70 sets the virtual dial at once (0x5123c) and queues (0,0) to
            the sink, whose code-0 path posts the dial event (0,4) only when the virtual dial differs from the last one
            it posted, 0x2b9d3a (0x51782..0x517a2). With injection on, a physical dial scan sets that byte to the
            physical dial (0x51214), so on a STILL dial no event was posted and nothing re-resolved until the next key
            event. The half-press then landed in the runner switch and the full press during mode entry. A fourth
            exclusive redirect, at the -CCTL diaset call 0xcd05e (`bl 0x65d70`), runs `tether_dial`: stock
            mg1_dialset, then, on success, with the remtin flag set, USB attached (not the service boot) and the
            running state 0x23a874 still 9, it stores the sink's injection word 1 (the queued (0,0) does the same store
            a moment later; doing it first means the resolve below cannot see injection off) and posts one dial event
            (0,4) through 0x517dc, as `tether_out` does. Its resolve runs `tether_force`, which leaves state 9.
            When the stock code-0 path also posts (virtual dial differs, e.g. physical dial on Movie), the second
            resolve finds the stored state 2 already and changes nothing. Returns mg1_dialset's r0.
  APO       The hold is issued at the divergence too (after the nested resolve that leaves 9; the state-2 runner entry
            does not re-apply the setting, v1.3 emulator trace), renewed by `tether_dial`
            (every diaset after the divergence) as well as `tether_key`, and by liveview's -LVFR handler (v1.2, while
            it serves a frame). The host's keepalive (keyset,0,27 every 20 s of idle) covers the rest.

v1.5 (2026-11-07, hardware) stops the record runner from dying with
end factor 5 (`12-30-05` once the camera leaves state 2). The bridge's connect-time `-VER` runs 0xccb5c -> 0x114824,
which arms a 3 s timer; on its event 0xb it posts message 0xa201 to the main task queue. In state 9 the USB runner
0x6d6f0 consumes it (LCD off 0x3da80(2), lens retract 0x29fbe). Once tether has switched to record state 2, the record
runner's receive/dispatch 0xd2770 gets it instead: it switches on [buf+6]>>12, nibble 0xA (>= 8) falls to the case
that loads 0x123005 and calls 0x6eb70 (stores it to 0x1befac), then returns -1, and the record loop 0x7eeb4 (call at
0x7eed0, `cmn r0,#1`) exits the runner. A fifth exclusive redirect, at that `bl 0x6eb70` (0xd27f2), runs `tether_msg`:
when r0 == 0x123005, the message id [r7+6] (r7 = the buffer, set at 0xd2772) is exactly 0xa201 and the remtin flag is
set, it returns 0 from 0xd2770 through 0xd2770's own epilogue (`pop {r7}; pop {r3}; bx r3`, as 0xd27c6 does, after
dropping its own pushed lr, so sp is 0xd2770's frame). The loop then treats it as "no event": r7 = 0, not 0x71, the
event tables hold no 0 key, so 0x7efc2 receives again. Anything else tail-calls 0x6eb70 with r0 unchanged; lr still
holds 0xd27f7 from the redirect's `bl`, so the stock `return -1` at 0xd27f6 follows. Only that one message while
remtin is set: every other class-A message, and 0xa201 outside a tether session, still ends the runner as stock does.
The message is dropped rather than replayed to the USB runner after remtout: its stock action is a screen-off and lens
retract, which tether never wants.

v1.6 (2026-11-07, hardware: a short session -VER, remtin, diaset,4, one -KSET, remtout within about 2 s showed
`12-30-05`): the -VER timer's 0xa201 arrived about 3 s after connect, after remtout had cleared the remtin flag but while
the record runner was still up during the switch back to state 9, so 1.5 let it through. `tether_msg` now swallows
0xa201 whenever USB is attached ([0x23a888] == 1, the stock attach flag) outside the service boot ([0x23a88c] == 0, as
`tether_force` checks), with the remtin flag kept as an alternative trigger. With USB attached and not the service boot,
stock forces state 9 on every resolve, so the record runner only runs there because tether put it there (or in the
switch window just after attach / remtout), and 0xa201's only consumer is the state-9 USB runner 0x6d6f0 (the only
0xa201 literal, 0x6d7f8; the poster is the USB-session state machine 0x114824). The service boot keeps the 1.5 rule.

v1.7 (2026-11-07, Codex static review scratchpad review/codex-arm*.log): `tether_dial` no longer requires the running
state 0x23a874 == 9. 1.4-1.6 skipped the (0,4) post when the running state had already moved (e.g. run_state 2 while the
stored app state is still 9, or any re-arm while the record runner is up), and with the physical and virtual dials
both on STILL stock `diaset,4` posts nothing either, so nothing re-resolved. Now every successful diaset with the remtin
flag set, USB attached and not the service boot posts (0,4) (and stores injection on first). When the stored state is
already 2, the resolve that event triggers finds 2 and changes nothing (the same case 1.4 documents for the stock
code-0 post). Auto Power Off renewal and the return value (mg1_dialset's r0) are unchanged; the 1.6 0xa201 swallow is
unchanged.

v1.8 (2026-11-08): a resident word `arm_hook` (exported, 0 at install). When it is
non-zero, `tether_force` calls it once right after the divergence (after the nested resolve and the Auto Power Off
hold; r4-r7 kept, its return value ignored). stallfix's init sets it to its fix 4 (panel and backlight on, the
0x3da80(1) / 0x3db8c(1) pair 0x28560 runs for state 9). Without stallfix in the build the word stays 0 and tether is
1.7.

Not touched: no settings byte, no menu row, no flash or store-write routine. Without a USB attach and remtin, every path
is the stock one (the unplug branch needs the remtin flag, which only `-CCTL remtin` over USB sets). No option: leaving
the feature out of the build is the off switch.
"""
from __future__ import annotations

from .. import firmware as fw
from ..feature import Check, Feature, Redirect

NAME = "tether"
SUMMARY = "USB tether: after -CCTL remtin the USB state 9 yields to the (virtual) dial, so -CCTL keyset can shoot"
OPTIONS = {}

SITE = 0x4FE44                   # ldr r0, =0x23a864 ; ldr r1, [r0, #0x24]
SITE_BEFORE = bytes.fromhex("48086a41")
OUT_SITE = 0xCD090               # -CCTL remtout case: bl 0x4fc84 (remtout flag clear)
OUT_BEFORE = bytes.fromhex("f782fdf8")
RESOLVE = 0x4FD1E                # app-state resolver (r4 = 0x1bd1b8 block: +0 remtin flag, +4 init, +8 state 0x1bd1c0)
FLAG, STATE = 0x1BD1B8, 0x1BD1C0
CCTL_STATE = 0x1BD2EC            # -CCTL remote state (0 idle, 1 after remtin, 2 after diaset)
INJECT = 0x2B9D34               # key-injection on (sink 0x51740: [0x2b9d30 + 4] = 1, code 2 clears it)
                                 # +5/+6 = virtual dial 0x2b9d39/0x2b9d3a (9 = none), +0xa4 = injected keys 0x2b9dd8[42]
USB_BLOCK = 0x23A864             # +0x10 running state 0x23a874, +0x24 0x23a888 (USB attached), +0x28 0x23a88c (service)
APO = 0x290DE                    # Auto Power Off control 0x290de(mode): 1 = apply the setting, 2 = off (0x6d68e, 0x4fa14)
KEY_SITE = 0xCD03A               # -CCTL keyset case: bl 0x65cfc (mg1_keyset)
KEY_BEFORE = bytes.fromhex("f798fe5f")
KEYSET = 0x65CFC
DIAL_SITE = 0xCD05E              # -CCTL diaset case: bl 0x65d70 (mg1_dialset)
DIAL_BEFORE = bytes.fromhex("f798fe87")
DIALSET = 0x65D70
POST_EVENT = 0x517DC             # stock key/dial event poster (code, action); tDcx already reaches it via the sink
MSG_SITE = 0xD27F2               # record runner receive/dispatch 0xd2770: bl 0x6eb70 (store the error code, then -1)
MSG_BEFORE = bytes.fromhex("f79cf9bd")
SET_ERR = 0x6EB70                # str r0, [0x1befac] ; bx lr
ERR_CLASS_A = 0x123005           # 0xd2770's code for [buf+6]>>12 = 3 or >= 8
MSG_A201 = 0xA201                # posted by the 3 s timer 0x114824 that `-VER` (0xccb5c) arms
DISPATCH = 0xD2770
DEC_SITE = 0xD27C2               # 0xd2770's `bl 0xd23bc` (event decoder); not checked here, waitdiag's phase hook site

SRC = f"""
@ Entered by `bl` from 0x4fe44 inside 0x4fd1e; r4 = 0x1bd1b8 (set at 0x4fd20). Returns r0 = 0x23a864 and r1 = the value
@ the stock test compares with 1 (0x23a888, or 0 to skip the state-9 force). Clobbers r2, r3 (dead in the stock tail:
@ 0x4fe48..0x4fe60 reads only r0, r1, r4 and pops r3). Preserves r4-r7.
tether_force:
    push {{r5, lr}}
    ldr r0, ={USB_BLOCK:#x}
    ldr r1, [r0, #0x24]         @ 0x23a888
    cmp r1, #1
    bne 5f                      @ no USB: stock (no force), unless the cable left mid-tether
    ldr r2, [r0, #0x28]         @ 0x23a88c
    cmp r2, #0
    bne 9f                      @ service boot: stock (no force)
    ldr r2, [r4, #0]            @ remtin flag 0x1bd1b8
    cmp r2, #0
    beq 9f                      @ no remtin: stock (force 9)
    ldr r2, ={INJECT:#x}
    ldr r2, [r2]
    cmp r2, #0
    beq 9f                      @ injection cleared (keyset,0,2) or never on: stock (force 9)
    ldr r2, =latch
    movs r3, #1
    strb r3, [r2]               @ diverged: Auto Power Off is held (below, tether_key, tether_dial), tether_release undoes it
    ldr r2, [r4, #8]            @ stored state 0x1bd1c0
    cmp r2, #9
    bne 1f
    movs r2, #2                 @ a re-derivable state: 0x4fd1e re-reads the dial for 2/3/4/5/10/12/13
    str r2, [r4, #8]
    bl nest                     @ nested resolve from the virtual dial; comes back through here with state != 9
    movs r0, #2
    bl apo                      @ v1.4: hold Auto Power Off from the divergence on (renewed by keyset/diaset/-LVFR)
    ldr r3, =arm_hook
    ldr r3, [r3]
    cmp r3, #0
    beq 1f
    bl call_r3                  @ v1.8: arm hook (stallfix fix 4: panel on), keeps r4-r7
1:  ldr r0, ={USB_BLOCK:#x}
    movs r1, #0                 @ stock `cmp r1, #1 ; bne` skips the force
9:  pop {{r5}}
    pop {{r2}}
    bx r2
5:  ldr r2, [r4, #0]
    cmp r2, #0
    beq 9b                      @ r0, r1 intact: stock
    bl tether_clear             @ unplugged mid-tether: drop flag, -CCTL state, injected keys, virtual dial
    bl tether_release           @ Auto Power Off back to the setting if the hold was issued
    movs r2, #2
    str r2, [r4, #8]
    bl nest                     @ re-resolve once from the physical dial (flag now 0: the nested pass is stock)
    ldr r0, ={USB_BLOCK:#x}
    ldr r1, [r0, #0x24]         @ still not 1: no force
    b 9b
nest:
    ldr r3, ={RESOLVE + 1:#x}
    bx r3
apo:
    ldr r3, ={APO + 1:#x}
    bx r3

@ Replaces `bl 0x65cfc` at 0xcd03a (-CCTL keyset, tDcx; mg1_keyset takes no argument and returns r0 = 0 ok / -1).
@ While the session has diverged it renews the Auto Power Off hold first (0x290de(2)), then runs mg1_keyset.
tether_key:
    push {{r4, lr}}
    ldr r0, =latch
    ldrb r1, [r0]
    cmp r1, #0
    beq 1f
    movs r0, #2
    bl apo
1:  ldr r3, ={KEYSET + 1:#x}
    bl call_r3
    pop {{r4}}
    pop {{r3}}
    bx r3

@ Replaces `bl 0x65d70` at 0xcd05e (-CCTL diaset, tDcx; mg1_dialset reads its argument from 0x2c39a0, returns r0 = 0 ok
@ / -1). r4-r7 are live in 0xcd000 after the call (r6 = 0x1bd2ec, which the caller sets to 2 on r0 = 0); r1-r3 are not.
tether_dial:
    push {{r4, lr}}
    ldr r3, ={DIALSET + 1:#x}
    bl call_r3                  @ stock mg1_dialset: virtual dial set now, (0,0) queued to the sink
    adds r4, r0, #0
    bne 9f                      @ refused: nothing else
    ldr r0, =latch
    ldrb r1, [r0]
    cmp r1, #0
    beq 1f
    movs r0, #2
    bl apo                      @ diverged session: renew the Auto Power Off hold
1:  ldr r0, ={FLAG:#x}
    ldr r0, [r0]
    cmp r0, #0
    beq 9f                      @ no remtin (cannot happen: diaset needs the -CCTL state): stock
    ldr r0, ={USB_BLOCK:#x}
    ldr r1, [r0, #0x24]
    cmp r1, #1
    bne 9f                      @ no USB: stock
    ldr r1, [r0, #0x28]
    cmp r1, #0
    bne 9f                      @ service boot: stock
    ldr r0, ={INJECT:#x}        @ v1.7: no running-state test (posted in any state; a resolve in 2 changes nothing)
    movs r1, #1
    str r1, [r0]                @ injection on now (the sink's own first store for the queued (0,0))
    movs r0, #0
    movs r1, #4
    ldr r3, ={POST_EVENT + 1:#x}
    bl call_r3                  @ dial event (0,4): its resolve runs tether_force, which leaves state 9
9:  adds r0, r4, #0
    pop {{r4}}
    pop {{r3}}
    bx r3

@ If the Auto Power Off hold is on, re-apply the setting (0x290de(1), what the record runners call) and drop the latch.
@ Clobbers r0-r3 (the callers are past their last use of them), keeps r4-r7.
tether_release:
    push {{lr}}
    ldr r0, =latch
    ldrb r1, [r0]
    cmp r1, #0
    beq 1f
    movs r1, #0
    strb r1, [r0]
    movs r0, #1
    bl apo
1:  pop {{r3}}
    bx r3

@ Clears what `-CCTL remtin/diaset/keyset` left behind, with plain stores (no event, no call; safe in any task).
@ Mirrors the sink's code-2 path 0x51754..0x51776 plus the remtout flag clear. Clobbers r0-r2.
tether_clear:
    movs r0, #0
    ldr r1, ={FLAG:#x}
    str r0, [r1]                @ remtin flag
    ldr r1, ={CCTL_STATE:#x}
    str r0, [r1]                @ -CCTL state: a new session needs remtin again
    ldr r1, ={INJECT:#x}
    str r0, [r1]                @ injection off (the scan overlay 0x50ec0 stops)
    movs r2, #9
    strb r2, [r1, #5]           @ virtual dial 0x2b9d39 = none
    strb r2, [r1, #6]           @ 0x2b9d3a
    adds r1, #0xa4              @ injected keys 0x2b9dd8[0..41]
    movs r2, #42
2:  subs r2, #1
    strb r0, [r1, r2]
    bne 2b
    bx lr

@ Replaces `bl 0x4fc84` at 0xcd090 (-CCTL remtout, tDcx). Must return r0 = 0 and keep r4-r7 (r4, r5, r6, r7 are live in
@ 0xcd000 after the call; r1-r3 are not).
tether_out:
    push {{r4, lr}}
    bl tether_clear             @ includes the stock 0x4fc84 store (flag = 0)
    bl tether_release           @ before the event: the resolve it triggers lands in 9, whose runner holds again
    ldr r0, ={USB_BLOCK:#x}
    ldr r1, [r0, #0x24]
    cmp r1, #1
    bne 1f                      @ no USB (cannot happen over -CCTL): nothing to re-resolve
    ldr r1, [r0, #0x10]         @ running state 0x23a874
    cmp r1, #9
    beq 1f                      @ already back in the USB state
    movs r0, #0
    movs r1, #4
    ldr r3, ={POST_EVENT + 1:#x}
    bl call_r3                  @ dial event (0,4): its resolve forces 9 and ends the state-2 runner
1:  movs r0, #0
    pop {{r4}}
    pop {{r3}}
    bx r3
call_r3:
    bx r3

@ Replaces `bl 0x6eb70` at 0xd27f2 inside 0xd2770 (record runner receive/dispatch, Thumb; frame `push {{r7, lr}}`,
@ r7 = message buffer). Its own `push {{lr}}` (selfcheck: every handler entry pushes lr) is dropped with `add sp, #4`
@ before either exit, so sp is 0xd2770's frame again and lr still holds 0xd27f7 (nothing here calls): the swallow path
@ is 0xd2770's own epilogue (0xd27c6) with r0 = 0, the other a tail call into 0x6eb70 that returns to 0xd27f6.
@ Clobbers r1-r3 (dead there: 0x6eb70 reads r0 only, 0xd27f6.. sets r0, the epilogue pops r3).
tether_msg:
    push {{lr}}
    ldr r1, ={ERR_CLASS_A:#x}
    cmp r0, r1
    bne 8f
    ldrh r1, [r7, #6]           @ message id
    ldr r2, ={MSG_A201:#x}
    cmp r1, r2
    bne 8f
    ldr r1, ={USB_BLOCK:#x}
    ldr r2, [r1, #0x24]         @ 0x23a888
    cmp r2, #1
    bne 3f                      @ no USB: only a live remtin swallows
    ldr r2, [r1, #0x28]         @ 0x23a88c
    cmp r2, #0
    beq 4f                      @ v1.6: USB attached, not the service boot: swallow
3:  ldr r1, ={FLAG:#x}
    ldr r1, [r1]
    cmp r1, #0
    beq 8f                      @ no USB (or service boot) and no tether session: stock
4:  movs r0, #0                 @ 0x7eed4: not -1, not 0x71 -> no event, receive again
    add sp, #4
    pop {{r7}}                   @ 0xd27c6..0xd27ca: 0xd2770 returns 0 to 0x7eed4
    pop {{r3}}
    bx r3
8:  add sp, #4
    ldr r3, ={SET_ERR + 1:#x}
    bx r3                       @ stock: 0x6eb70(r0) returns to 0xd27f6 (r0 = -1)
.ltorg
.p2align 2
latch:
    .byte 0, 0, 0, 0            @ byte 0: 1 = this session issued the 0x290de(2) hold
arm_hook:
    .word 0                     @ v1.8: Thumb routine called once at each divergence (0 = none; stallfix's init sets it)
"""


def make(opts):
    return Feature(
        name=NAME, version="1.8", summary=SUMMARY, options=opts, source=SRC, exports=["arm_hook"],
        redirects=[Redirect(SITE, SITE_BEFORE, "tether_force",
                            "0x4fd1e state-9 force: skip it once the host sent -CCTL remtin; clean up on unplug"),
                   Redirect(OUT_SITE, OUT_BEFORE, "tether_out",
                            "-CCTL remtout: also clear injection and return the runner to state 9"),
                   Redirect(KEY_SITE, KEY_BEFORE, "tether_key",
                            "-CCTL keyset: renew the Auto Power Off hold while a tether session is live"),
                   Redirect(DIAL_SITE, DIAL_BEFORE, "tether_dial",
                            "-CCTL diaset: leave state 9 at once (post (0,4) when the stock sink would not)"),
                   Redirect(MSG_SITE, MSG_BEFORE, "tether_msg",
                            "record dispatch 0xd2770: swallow the -VER timer message 0xa201 while USB is attached or remtin is set")],
        checks=[Check(SITE + 4, fw.ram(SITE + 4, 0x28),
                      "stock tail 0x4fe48..0x4fe6b: cmp r1,#1 / force 9 / epilogue, literals 0x1bd1b8 and 0x23a864"),
                Check(0x4FD1E, fw.ram(0x4FD1E, 4), "0x4fd1e prologue + `ldr r4, =0x1bd1b8` (called nested)"),
                Check(0x51740, fw.ram(0x51740, 0x38),
                      "sink 0x51740: injection flag [0x2b9d30+4] set / code 2 clears it, the dial bytes and keys[42]"),
                Check(0x4FC78, fw.ram(0x4FC78, 0x18), "remtin 0x4fc78 sets / remtout 0x4fc84 clears 0x1bd1b8"),
                Check(0x4FEF6, fw.ram(0x4FEF6, 0x2A), "code-8 (USB) event: detach stores 0 to 0x23a888; 0x720d only in 9"),
                Check(0xCD08A, fw.ram(0xCD08A, 6), "-CCTL remtout case: state 0x1bd2ec check before the call"),
                Check(0xCD094, fw.ram(0xCD094, 6), "-CCTL remtout case: `cmp r0,#0` / state = 0 after the call"),
                Check(APO, fw.ram(APO, 0x3A),
                      "Auto Power Off control 0x290de(mode): mode 1 = setting ([0x1b080+0x1e] -> 0/0x3c/0x12c/0x708) else 0, 0x4fce8"),
                Check(KEYSET, fw.ram(KEYSET, 0x20), "mg1_keyset 0x65cfc: no argument, r0 = 0 / -1 (0xcd03a case)"),
                Check(KEY_SITE - 0x14, fw.ram(KEY_SITE - 0x14, 0x14), "-CCTL keyset case: state check and argument copy before the call"),
                Check(POST_EVENT, fw.ram(POST_EVENT, 0x10), "event poster 0x517dc(code, action) prologue"),
                Check(DIALSET, fw.ram(DIALSET, 0x50),
                      "mg1_dialset 0x65d70: arg from 0x2c39a0, N < 9, 0x32524 (virtual dial) + 0x324e6 queue, r0 = 0 / -1"),
                Check(DIAL_SITE - 0x10, fw.ram(DIAL_SITE - 0x10, 0x10),
                      "-CCTL diaset case: state check and argument copy before the call"),
                Check(DIAL_SITE + 4, fw.ram(DIAL_SITE + 4, 0x10),
                      "-CCTL diaset case: `cmp r0,#0` / state = 2 / replies after the call"),
                Check(0x5123C, fw.ram(0x5123C, 0x0C), "0x5123c: virtual dial byte 0x2b9d39 store (synchronous)"),
                Check(0x51780, fw.ram(0x51780, 0x28),
                      "sink code 0/1: (0,4) only when the virtual dial differs from 0x2b9d3a"),
                Check(DISPATCH, fw.ram(DISPATCH, DEC_SITE - DISPATCH),
                      "0xd2770: push {r7,lr} / r7 = buf / receive / [buf+6]>>12 switch"),
                Check(DEC_SITE + 4, fw.ram(DEC_SITE + 4, MSG_SITE - DEC_SITE - 4),
                      "0xd2770: epilogue 0xd27c6 / 0x123005 case (the bl 0xd23bc at 0xd27c2 between them is left out: "
                      "waitdiag may hook it)"),
                Check(MSG_SITE + 4, fw.ram(MSG_SITE + 4, 0x1A),
                      "0xd2770 after the call: r0 = -1, b 0xd27c6; literals 0x123001..0x123005"),
                Check(0x7EED0, fw.ram(0x7EED0, 0x10), "record loop 0x7eeb4: bl 0xd2770, -1 exits, else r7 = event"),
                Check(SET_ERR, fw.ram(SET_ERR, 0x0C), "0x6eb70: str r0, [0x1befac]; bx lr (r0 only)")])


# Unit-level smoke: `koma smoke` never attaches USB, so neither redirect is on its path (no Hook to report). The
# handlers' contract is in tests/test_tether.py (emulator scenario, USB modelled).
