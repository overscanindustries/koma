"""autoiso: GR II-style ISO AUTO high limit (byte 0x1b8b7) and slowest shutter before ISO rises (byte 0x1b8b8).

Setting-driven port of the earlier auto-ISO patch (12 B code-immediate patch, O-emu at function level).

How the stock AE uses the limits (D, emu-frontier-3 README §1; units 1/16 EV, gain 0 = ISO 100, ISO = 100*2^(gain/16)):
  RL1 -> 0x3f7f0 -> `bl 0x42ad4` at 0x3f920 (the whole still AE; no arguments, no stack arguments).
  Inside it every limit is read from the adjustment store in SDRAM, base = *(0x175378) = 0x08010000:
    store+0x080 (u8)  Tv floor = slowest shutter before ISO rises (0x4389c -> 0x1f9ebb); 82 in the default image (~1/35)
    store+0x47e (s16) flash-off AUTO gain ceiling (0x42f14 -> 0x4356c, and 0x432d4's b0c8 branch); 10 (~ISO 154)
    store+0x49a (s16) <1 s clamp in 0x432d4 and the 'Tv < 0x18 -> back to 0x49a' fallback in 0x4356c; 10
Readers of these three values outside 0x42ad4: 0x42460 (via 0x421b8), 0x44a3c (flash decision), 0x44b04/0x44b24,
0xa457c. The frontier-3 patch replaced the reads with immediates (P1, P2, P4) and removed the fallback (P3).

Koma version (no .text change): hook `bl 0x42ad4` at 0x3f920. At use the handler reads the two Koma bytes; when ISO is
AUTO (cp+9 == 0) and flash is off (cp+0x10 & 0xf == 0) it saves the store values, writes the selected ones, calls the
stock AE, and restores the saved values before returning. Raising 0x49a together with 0x47e makes P3 unnecessary (the
fallback only fires when gain > 0x49a). Index 0, an out-of-range byte, or a missing KOMA magic leave AE untouched.
Live view / flash paths outside the call keep the stock values. Fixed ISO and flash auto/on are always stock.
"""
from __future__ import annotations

from ..feature import Check, Feature, Hook

NAME = "autoiso"
SUMMARY = "ISO AUTO high limit + slowest shutter before ISO rises (GR II style), hook 0x3f920 (still AE)"
OPTIONS = {}

SET_ISO, SET_SS = 0x1B8B7, 0x1B8B8
SITE, TARGET = 0x3F920, 0x42AD4
STOREPTR, STORE = 0x175378, 0x08010000
OFF_TVMIN, OFF_CEIL, OFF_CLAMP = 0x80, 0x47E, 0x49A

# Maximum ISO: GR II offers 200..25600; this sensor's fixed-ISO range ends at 1600 (cp+9 code 8 = Sv 0x90 = gain 64),
# so the AUTO ceiling stops there. Index -> gain (1/16 EV over ISO 100).
ISO_OPTIONS = ["STOCK", "200", "400", "800", "1600"]
ISO_GAIN = [None, 16, 32, 48, 64]
# Slowest shutter: GR II offers Auto, 1/2 .. 1/250. Index -> Tv (16*log2(1/t), the nominal points of table 0x17ec3c).
SS_OPTIONS = ["STOCK", "1/250", "1/125", "1/60", "1/30", "1/15", "1/8", "1/4", "1/2"]
SS_TV = [None, 128, 112, 96, 80, 64, 48, 32, 16]

ROWS = [dict(id="isomax", label="AUTO-ISO MAX", options=ISO_OPTIONS, byte=SET_ISO, default=0, group="exposure"),
        dict(id="slowshut", label="SLOW SHUTTER", options=SS_OPTIONS, byte=SET_SS, default=0, group="exposure")]

SRC = f"""
.equ SET_ISO, {SET_ISO:#x}
.equ SET_SS, {SET_SS:#x}
.equ MAGIC, 0x1b8b0
.equ CP, 0x23b068
.equ STOREPTR, {STOREPTR:#x}
.equ NISO, {len(ISO_OPTIONS) - 1}
.equ NSS, {len(SS_OPTIONS) - 1}

@ wraps 0x42ad4 (still AE). r7 bit0: Tv floor overridden, bit1: gain ceiling + clamp overridden.
handler:
    push {{r4, r5, r6, r7, lr}}
    push {{r0, r1, r2, r3}}
    movs r7, #0
    ldr r4, =MAGIC
    ldr r5, [r4, #0]
    ldr r6, =0x4b4f4d41               @ 'KOMA'
    cmp r5, r6
    bne call_ae
    ldr r4, =CP
    ldrb r5, [r4, #9]                 @ ISO code: 0 = AUTO
    cmp r5, #0
    bne call_ae
    ldrb r5, [r4, #0x10]              @ flash mode, low nibble 0 = off
    lsls r5, r5, #28
    bne call_ae
    ldr r4, =STOREPTR
    ldr r4, [r4, #0]                  @ adjustment store base (0x08010000)
    ldr r6, =saved
    ldr r5, =SET_SS
    ldrb r5, [r5, #0]
    subs r5, #1
    cmp r5, #NSS
    bhs iso                           @ 0 (stock) or out of range: unsigned >= NSS
    ldr r0, =tvtab
    ldrb r0, [r0, r5]
    movs r3, #0x80
    ldrb r1, [r4, r3]
    strb r1, [r6, #0]
    strb r0, [r4, r3]
    movs r7, #1
iso:
    ldr r5, =SET_ISO
    ldrb r5, [r5, #0]
    subs r5, #1
    cmp r5, #NISO
    bhs call_ae
    adds r5, #1
    lsls r5, r5, #4                   @ gain = 16 * index
    ldr r3, =0x47e
    ldrh r1, [r4, r3]
    strh r1, [r6, #2]
    strh r5, [r4, r3]
    ldr r3, =0x49a
    ldrh r1, [r4, r3]
    strh r1, [r6, #4]
    strh r5, [r4, r3]
    adds r7, #2
call_ae:
    ldr r3, =next
    mov ip, r3
    pop {{r0, r1, r2, r3}}
    bl call_ip                        @ stock 0x42ad4 (or the next feature's handler)
    lsrs r1, r7, #1
    bcc no_tv
    ldrb r1, [r6, #0]
    movs r3, #0x80
    strb r1, [r4, r3]
no_tv:
    lsrs r1, r7, #2
    bcc done
    ldrh r1, [r6, #2]
    ldr r3, =0x47e
    strh r1, [r4, r3]
    ldrh r1, [r6, #4]
    ldr r3, =0x49a
    strh r1, [r4, r3]
done:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
call_ip:
    bx ip
.ltorg
.p2align 2
saved:
    .word 0, 0
tvtab:
    .byte {", ".join(str(v) for v in SS_TV[1:])}
.p2align 2
"""


def make(opts):
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=opts, source=SRC,
        hooks=[Hook(SITE, TARGET, "handler", "next", "RL1 still AE 0x42ad4: AUTO-ISO limits read at use")],
        checks=[Check(STOREPTR, STORE.to_bytes(4, "big"), "adjustment-store pointer the AE reads its limits through")],
        smoke=smoke_checks)


def smoke_setup():
    # ISO max 400, slowest shutter 1/60, flash off, plus the KOMA magic (no core lane writes it in a test build)
    return {0x1B8B0: 0x4B, 0x1B8B1: 0x4F, 0x1B8B2: 0x4D, 0x1B8B3: 0x41, SET_ISO: 2, SET_SS: 3, 0x1B06F: 0}


def smoke_checks(obs):
    st = obs.ram(STORE, 0x500)
    tvmin, ceil, clamp = st[OFF_TVMIN], int.from_bytes(st[OFF_CEIL:OFF_CEIL + 2], "big", signed=True), \
        int.from_bytes(st[OFF_CLAMP:OFF_CLAMP + 2], "big", signed=True)
    sh = obs.ram(0x08011DDE, 6)
    tv, gain = int.from_bytes(sh[0:2], "big", signed=True), int.from_bytes(sh[4:6], "big", signed=True)
    return [("store limits restored after AE", (tvmin, ceil, clamp) == (82, 10, 10),
             f"store+0x80={tvmin} +0x47e={ceil} +0x49a={clamp}"),
            ("shot exposure within the new limits (gain<=32 and gain>10 or Tv>=96)",
             gain <= 32 and (gain > 10 or tv >= 96), f"shot Tv={tv} gain={gain} (flash byte {obs.ram8(0x1B06F)})")]
