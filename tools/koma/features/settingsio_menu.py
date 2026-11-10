"""settingsio-menu: KOMA-page rows for settings externalization.

Companion of settingsio (which is transient and so cannot carry rows or resident code). Rows, on the OTHER page:

  MENU LAYOUT    0x1b8bb  5-TAB / STK+KOMA / GR II   mode byte, honoured at the next boot by menu5's init: STK+KOMA =
                                                     mode page | KOMA | stock SETUP; GR II falls back to 5-TAB
  KOMA FEATURES  0x1b8bc  FULL / ESSENTIAL           mode byte, honoured at the next boot by the installer: ESSENTIAL
                                                     skips the inits and records of features outside
                                                     features.ESSENTIAL (linker.essential_split)
  SAVE TO CARD   0x1b8bd  NO / YES / SAVED / FAILED  action row. The menu framework has no action rows, so this is a
                                                     row with an on_change (menu5 calls it on every change of the
                                                     byte): YES writes /ATA1/PARAM.SAV (the current block with its
                                                     checksum recomputed, so another body can import it) and leaves
                                                     SAVED or FAILED in the byte; the menu reloads the cursor, so the
                                                     row shows the result. Choosing YES from any state (NO, SAVED,
                                                     FAILED) is a change, so it saves again. SAVED or FAILED chosen by
                                                     hand is not a request: the byte goes back to NO, nothing written.
                                                     The init puts it back to NO at every boot.

Option texts are at most 9 characters (menu font), hence STK+KOMA and ESSENTIAL for the design's STOCK+KOMA and
ESSENTIALS. The save writes 0x1b000..0x1bffb from RAM, then the 4-byte checksum from the stack: RAM 0x1bffc is never
written. It calls the firmware create/write/close directly (as telemetry and settingsio). No flash or store write.

The checksum sum runs with IRQ/FIQ masked through the firmware's irq_set 0x1c5b4 (ARM; the loader service table's
+0x28, used by the installer), so the sum itself is one snapshot. The file write reads the block from RAM with IRQs on
(the SD driver needs them), so a stock write to the block between the sum and the write is still possible: the file's
checksum then does not match its bytes and the import rejects it as BAD CHECKSUM (fail-safe; save again).
"""
from __future__ import annotations

from .. import firmware as fw
from ..feature import Check, Feature
from .settingsio import BASE, CK_OFF, CLOSE, CREATE, SAVE_BYTE, WRITE

# Not PARAM.DAT: the same body re-imports that at its next boot and would revert later changes.
PATH_SAV = "/ATA1/PARAM.SAV"

IRQ_SET = 0x1C5B4               # ARM: old = irq_set(bits) (I/F mask in bits 6-7); loader service table +0x28

NAME = "settingsio-menu"
SUMMARY = "KOMA rows MENU LAYOUT / KOMA FEATURES (mode bytes) + SAVE TO CARD (writes /ATA1/PARAM.SAV)"
OPTIONS = {}
VERSION = "1.1"

LAYOUT_BYTE, FEATURES_BYTE = 0x1B8BB, 0x1B8BC
SAVE_NO, SAVE_YES, SAVE_SAVED, SAVE_FAILED = 0, 1, 2, 3
ROWS = [dict(id="menulayout", label="MENU LAYOUT", options=["5-TAB", "STK+KOMA", "GR II"], byte=LAYOUT_BYTE,
             default=0, group="settings"),
        dict(id="komafeat", label="KOMA FEATURES", options=["FULL", "ESSENTIAL"], byte=FEATURES_BYTE, default=0,
             group="settings"),
        dict(id="savecard", label="SAVE TO CARD", options=["NO", "YES", "SAVED", "FAILED"], byte=SAVE_BYTE, default=0,
             group="settings")]

SRC = f"""
@ init (resident, once per boot): SAVE TO CARD back to NO
init:
    push {{r4, lr}}
    ldr r0, =0x{SAVE_BYTE:x}
    movs r1, #{SAVE_NO}
    strb r1, [r0]
    pop {{r4}}
    pop {{r3}}
    bx r3

@ on_change (menu5 KOMA tab, called on every change: r0 = value, r1 = byte address). SAVE TO CARD: YES writes
@ PARAM.SAV, byte := SAVED / FAILED; SAVED / FAILED picked by hand -> NO (not a request); NO: nothing.
on_change:
    push {{r4, r5, r6, lr}}
    sub sp, #8
    ldr r2, =0x{SAVE_BYTE:x}
    cmp r1, r2
    bne oc_out
    cmp r0, #{SAVE_NO}
    beq oc_out
    cmp r0, #{SAVE_YES}
    beq 2f
    movs r0, #{SAVE_NO}
    b oc_set
2:  movs r0, #0xc0              @ IRQ/FIQ off for the sum (sp+4 = old mask)
    ldr r3, =0x{IRQ_SET:x}
    bl call_r3
    str r0, [sp, #4]
    ldr r1, =0x{BASE:x}         @ checksum: u32 sum of big-endian u16 halves over 0x000..0xffb (as 0x3d9b8)
    ldr r2, =0x{BASE + CK_OFF:x}
    movs r0, #0
1:  ldrh r3, [r1]
    adds r0, r0, r3
    adds r1, #2
    cmp r1, r2
    blo 1b
    str r0, [sp, #0]
    ldr r0, [sp, #4]            @ restore the mask before any file call
    ldr r3, =0x{IRQ_SET:x}
    bl call_r3
    ldr r0, =path
    movs r1, #2
    ldr r3, =0x{CREATE | 1:x}
    bl call_r3
    movs r4, r0
    adds r0, #1
    beq oc_fail
    movs r0, r4
    ldr r1, =0x{BASE:x}
    ldr r2, =0x{CK_OFF:x}
    ldr r3, =0x{WRITE | 1:x}
    bl call_r3
    movs r5, r0
    movs r0, r4
    mov r1, sp
    movs r2, #4
    ldr r3, =0x{WRITE | 1:x}
    bl call_r3
    movs r6, r0
    movs r0, r4
    ldr r3, =0x{CLOSE | 1:x}
    bl call_r3
    ldr r0, =0x{CK_OFF:x}
    cmp r5, r0
    bne oc_fail
    cmp r6, #4
    bne oc_fail
    movs r0, #{SAVE_SAVED}
    b oc_set
oc_fail:
    movs r0, #{SAVE_FAILED}
oc_set:
    ldr r1, =0x{SAVE_BYTE:x}
    strb r0, [r1]
oc_out:
    add sp, #8
    pop {{r4, r5, r6}}
    pop {{r3}}
    bx r3

call_r3:
    bx r3
.ltorg
.p2align 2
path: .asciz "{PATH_SAV}"
.p2align 2
"""


def make(opts):
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options={}, source=SRC, init="init", exports=["on_change"],
        checks=[Check(CREATE, fw.ram(CREATE, 4), "file create 0x55cf4 prologue"),
                Check(WRITE, fw.ram(WRITE, 4), "file write 0x560ec prologue"),
                Check(CLOSE, fw.ram(CLOSE, 4), "file close 0x55f0c prologue"),
                Check(IRQ_SET, fw.ram(IRQ_SET, 4), "irq_set 0x1c5b4 prologue (mrs r2, cpsr)")])
