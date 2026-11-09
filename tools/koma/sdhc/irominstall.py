#!/usr/bin/env python3
"""Build the /IROM installer modules for Koma 2. Offline: writes only to the output directory.

  irominstall.grmod  /ATA1/KOMAPKG.BIN -> /IROM/GRMOD.BIN (only when /IROM is mounted), read back and checksum-verified;
                     /ATA1/KOMARM.TXT present -> remove /IROM/GRMOD.BIN instead; /ATA1/KOMALS.TXT present -> list only.
                     Log /ATA1/IROMINST.TXT (key=value + /IROM root listing). Idempotent. Returns 0 (body freed).
  irodump.grmod      irominstall plus a dump step: /IROM/KOMADIAG.TXT and /IROM/KOMATRC.TXT are copied to the card
                     root before the install/remove/list modes. Log /ATA1/IRODUMP.TXT.
  usbmass.grmod      RAM-only: USB CONNECTION behaves as MASS STR for this boot. Patches the setting test in 0xc4acc
                     (beq 0xc4afc -> nop, before-checked) and sets the runtime mode byte 0x1bd241 = 2. The persisted
                     setting byte 0x1b0d7 is only read. Log /ATA1/USBMASS.TXT. Returns 0.

All are position-independent Thumb (ARMv4T, BE), no CP15, run in MainCtrl through the card loader (ABI 1, image
version word 0x240). Firmware entry points are 2.40 addresses; the ABI/version check gates them. Registers in module
code: r4 = -1 (0x55dc0 returns the caller's r4 on a path-class failure), r5 = body base, r6 = work area (pool, WSIZE
bytes), r7 free.
Usage: tools/koma/sdhc/irominstall.py [-o OUTDIR]   (needs LLVM 20, as tools/koma does)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))                  # tools/
from koma.vendor import grmod   # noqa: E402
from koma.vendor.asm_legacy import assemble, disasm   # noqa: E402

# Firmware 2.40 entry points (static analysis)
OPEN, CREATE_FLAG, READ, WRITE, CLOSE, REMOVE = 0x55DC0, 0x200, 0x56024, 0x560EC, 0x55F0C, 0x55D80
OPENDIR, READDIR, CLOSEDIR = 0x563C0, 0x5644C, 0x56528          # as used by the vendor -NLST handler 0xcc970
IROM_MOUNTED, IROM_STATUS = 0x1FB200, 0x1B9911
# v2: /IROM is NOT mounted at loader time on hardware with a card in (O-hw 2026-11-07, IROMINST.TXT mounted=0).
# Boot mode byte 0x1b9866 == 1 (pin 8 level, 0x3c270) -> tFileTask 0x55734 mounts /IROM (0x49384(0) at 0x5581a) only
# when the SD init 0x506b0 fails (no card). The playback-menu handler 0xb60bc (5th entry of the table at 0x2001f8,
# INFERRED = COPY TO CARD) does `if (*0x1fb200 == 0) 0x49384(0)` before copying; the installer does the same.
IROM_MOUNT, IROM_D1, BOOT_MODE = 0x49384, 0x1B990F, 0x1B9866
USB_MODE, USB_SETTING = 0x1BD241, 0x1B0D7
SITE, SITE_BEFORE, SITE_AFTER = 0xC4ADC, 0xD00E, 0x46C0           # beq 0xc4afc -> nop in 0xc4acc

WSIZE = 0x680
V = dict(LOGPOS=0x00, SVC=0x04, SIZE=0x08, STORED=0x0C, SUM=0x10, VALID=0x14, BAD=0x18, MOUNTED=0x1C, SRCSIZE=0x20,
         SRCSUM=0x24, PRESENT=0x28, DP=0x2C, DI=0x30, DN=0x34, DE=0x38, TMP=0x3C, TMP2=0x40, HDR=0x50)
IO, LOG, LOGMAX = 0x80, 0x280, 0x3FC

ENTRY = """
body0:
entry:                              @ r0 = service table, r1 = body base, r2 = L
    push {r4, r5, r6, r7, lr}
    sub sp, #8
    adds r5, r1, #0
    adds r7, r0, #0
    ldr r0, [r7, #0]
    cmp r0, #1                      @ ABI 1
    bne out
    ldr r0, [r7, #4]
    ldr r1, =0x240                  @ image version word
    cmp r0, r1
    bne out
    ldr r0, [r7, #0x10]             @ pool_alloc(pool, &p, WSIZE, 0)
    mov r1, sp
    ldr r2, =WSIZE
    ldr r3, [r7, #8]
    mov ip, r3
    movs r3, #0
    bl via12
    cmp r0, #0
    bne out
    ldr r6, [sp, #0]
    lsls r0, r6, #30
    bne free                        @ not 4-aligned: give it back
    str r7, [r6, #V_SVC]
    movs r4, #0
    mvns r4, r4
    movs r0, #0
    str r0, [r6, #V_LOGPOS]
    bl main
free:
    ldr r0, [sp, #0]
    ldr r3, [r7, #0x0c]             @ pool_free(p)
    bl via
out:
    movs r0, #0                     @ nothing stays resident
    add sp, #8
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
via:
    bx r3
via12:
    bx ip
.ltorg
"""

LIB = """
putc:                               @ r0 = byte; uses r1, r2 only
    ldr r1, [r6, #V_LOGPOS]
    ldr r2, =LOGMAX
    cmp r1, r2
    bhs 1f
    ldr r2, =LOG
    adds r2, r2, r6
    strb r0, [r2, r1]
    adds r1, #1
    str r1, [r6, #V_LOGPOS]
1:  bx lr
.ltorg

puts:                               @ r0 = string; returns r0 = byte after its NUL
    push {r7, lr}
    adds r7, r0, #0
2:  ldrb r0, [r7]
    adds r7, #1
    cmp r0, #0
    beq 3f
    bl putc
    b 2b
3:  adds r0, r7, #0
    pop {r7}
    pop {r3}
    bx r3

putdec:                             @ r0 = unsigned value, decimal
    push {r4, r7, lr}
    adds r7, r0, #0
    ldr r4, =(pow10 - body0)
    adds r4, r4, r5
    movs r3, #0
4:  ldr r1, [r4]
    cmp r1, #0
    beq 8f
    movs r0, #0
5:  cmp r7, r1
    blo 6f
    subs r7, r7, r1
    adds r0, #1
    b 5b
6:  adds r4, #4
    cmp r0, #0
    bne 7f
    cmp r3, #0
    bne 7f
    ldr r1, [r4]
    cmp r1, #0
    bne 4b
7:  movs r3, #1
    adds r0, #48
    bl putc
    b 4b
8:  pop {r4, r7}
    pop {r3}
    bx r3
.ltorg

puthex:                             @ r0 = value, 0x%08x
    push {r7, lr}
    adds r7, r0, #0
    movs r0, #48
    bl putc
    movs r0, #120
    bl putc
    movs r3, #8
1:  lsrs r0, r7, #28
    lsls r7, r7, #4
    cmp r0, #10
    blo 2f
    adds r0, #39
2:  adds r0, #48
    bl putc
    subs r3, #1
    bne 1b
    pop {r7}
    pop {r3}
    bx r3

kvd:                                @ r0 = key ("k="), r1 = value -> "k=<dec>\\n"
    push {r7, lr}
    adds r7, r1, #0
    bl puts
    adds r0, r7, #0
    bl putdec
    b kvnl
kvh:
    push {r7, lr}
    adds r7, r1, #0
    bl puts
    adds r0, r7, #0
    bl puthex
    b kvnl
kvs:                                @ r1 = string value
    push {r7, lr}
    adds r7, r1, #0
    bl puts
    adds r0, r7, #0
    bl puts
kvnl:
    movs r0, #10
    bl putc
    pop {r7}
    pop {r3}
    bx r3

exists:                             @ r0 = path -> r0 = 1 if open(path, 0) succeeds (then closed)
    push {lr}
    movs r1, #0
    FCALL OPEN
    cmp r0, r4
    beq 1f
    FCALL CLOSE
    movs r0, #1
    b 2f
1:  movs r0, #0
2:  pop {r3}
    bx r3
.ltorg

write_log:                          @ r0 = card path; replaces the file with the log buffer (skipped if no card)
    push {r7, lr}
    str r0, [r6, #V_TMP]
    bl exists
    cmp r0, #0
    beq 1f
    ldr r0, [r6, #V_TMP]
    FCALL REMOVE
1:  ldr r0, [r6, #V_TMP]
    ldr r1, =CREATE_FLAG
    FCALL OPEN
    cmp r0, r4
    beq 9f
    adds r7, r0, #0
    ldr r1, =LOG
    adds r1, r1, r6
    ldr r2, [r6, #V_LOGPOS]
    FCALL WRITE
    adds r0, r7, #0
    FCALL CLOSE
9:  pop {r7}
    pop {r3}
    bx r3
.ltorg
"""

INSTALL = """
sum_file:                           @ r0 = path -> r0 = 1 present / 0 missing; V_SIZE, V_STORED, V_SUM, V_VALID
    push {r5, r7, lr}
    movs r1, #0
    str r1, [r6, #V_SIZE]
    str r1, [r6, #V_STORED]
    str r1, [r6, #V_SUM]
    str r1, [r6, #V_VALID]
    str r1, [r6, #V_BAD]
    str r1, [r6, #V_HDR]
    str r1, [r6, #V_HDR+4]
    str r1, [r6, #V_HDR+8]
    str r1, [r6, #V_HDR+12]
    FCALL OPEN
    cmp r0, r4
    bne 1f
    movs r0, #0
    b 9f
1:  adds r7, r0, #0
2:  adds r0, r7, #0
    ldr r1, =IO
    adds r1, r1, r6
    ldr r2, =0x200
    FCALL READ
    cmp r0, #0
    beq 6f                          @ end of file
    blt 5f                          @ read error
    lsls r1, r0, #30
    bne 5f                          @ not a whole number of words
    ldr r1, [r6, #V_SIZE]           @ r1 = file offset of this chunk
    adds r3, r1, r0
    str r3, [r6, #V_SIZE]
    ldr r2, =0x10014
    cmp r3, r2
    bhi 5f                          @ larger than any module: stop reading
    ldr r2, =IO
    adds r2, r2, r6
    adds r3, r2, r0
3:  cmp r2, r3
    bhs 2b
    ldr r0, [r2]                    @ BE word
    cmp r1, #16
    beq 4f                          @ stored checksum: not summed
    bhi 7f
    adds r5, r6, r1
    str r0, [r5, #V_HDR]
7:  ldr r5, [r6, #V_SUM]            @ c = rol32(c, 1) + w
    adds r5, r5, r5
    adcs r5, r0
    str r5, [r6, #V_SUM]
    b 8f
4:  str r0, [r6, #V_STORED]
8:  adds r1, #4
    adds r2, #4
    b 3b
5:  movs r0, #1
    str r0, [r6, #V_BAD]
6:  adds r0, r7, #0
    FCALL CLOSE
    bl validate
    movs r0, #1
9:  pop {r5, r7}
    pop {r3}
    bx r3
.ltorg

validate:                           @ the loader's rules (module-loader README section 1 step 4 and 7)
    ldr r0, [r6, #V_BAD]
    cmp r0, #0
    bne 1f
    ldr r0, [r6, #V_HDR]
    ldr r1, =0x47524d44
    cmp r0, r1
    bne 1f
    ldr r0, [r6, #V_HDR+4]
    ldr r1, =0x00010000             @ format 1, flags 0
    cmp r0, r1
    bne 1f
    ldr r0, [r6, #V_HDR+8]          @ L
    cmp r0, #0
    beq 1f
    lsls r1, r0, #30
    bne 1f
    ldr r1, =0x10000
    cmp r0, r1
    bhi 1f
    ldr r1, [r6, #V_HDR+12]         @ entry: even, < L
    lsls r2, r1, #31
    bne 1f
    cmp r1, r0
    bhs 1f
    adds r0, #20
    ldr r1, [r6, #V_SIZE]
    cmp r0, r1
    bne 1f
    ldr r0, [r6, #V_STORED]
    ldr r1, [r6, #V_SUM]
    cmp r0, r1
    bne 1f
    movs r0, #1
    str r0, [r6, #V_VALID]
1:  bx lr
.ltorg

matches:                            @ -> r0 = 1 if the last sum_file is valid and equals the source
    ldr r0, [r6, #V_VALID]
    cmp r0, #0
    beq 1f
    ldr r0, [r6, #V_SIZE]
    ldr r1, [r6, #V_SRCSIZE]
    cmp r0, r1
    bne 1f
    ldr r0, [r6, #V_SUM]
    ldr r1, [r6, #V_SRCSUM]
    cmp r0, r1
    bne 1f
    movs r0, #1
    bx lr
1:  movs r0, #0
    bx lr

logsum:                             @ r0 = three consecutive keys: size, sum, valid
    push {r7, lr}
    bl puts
    adds r7, r0, #0
    ldr r0, [r6, #V_SIZE]
    bl putdec
    movs r0, #10
    bl putc
    adds r0, r7, #0
    bl puts
    adds r7, r0, #0
    ldr r0, [r6, #V_SUM]
    bl puthex
    movs r0, #10
    bl putc
    adds r0, r7, #0
    bl puts
    ldr r0, [r6, #V_VALID]
    bl putdec
    movs r0, #10
    bl putc
    pop {r7}
    pop {r3}
    bx r3

copyf:                              @ r0 = src, r1 = dst -> r0 = 0 ok, 1 no src, 2 no dst, 3 read err, 4 short write
    push {r5, r7, lr}
    str r1, [r6, #V_TMP]
    movs r1, #0
    FCALL OPEN
    cmp r0, r4
    beq 8f
    adds r7, r0, #0
    ldr r0, [r6, #V_TMP]
    ldr r1, =CREATE_FLAG
    FCALL OPEN
    cmp r0, r4
    bne 1f
    adds r0, r7, #0
    FCALL CLOSE
    movs r0, #2
    b 9f
1:  adds r5, r0, #0
    movs r0, #0
    str r0, [r6, #V_TMP]
2:  adds r0, r7, #0
    ldr r1, =IO
    adds r1, r1, r6
    ldr r2, =0x200
    FCALL READ
    cmp r0, #0
    beq 4f
    blt 3f
    str r0, [r6, #V_TMP2]
    adds r2, r0, #0
    adds r0, r5, #0
    ldr r1, =IO
    adds r1, r1, r6
    FCALL WRITE
    ldr r1, [r6, #V_TMP2]
    cmp r0, r1
    beq 2b
    movs r0, #4
    b 5f
3:  movs r0, #3
5:  str r0, [r6, #V_TMP]
4:  adds r0, r7, #0
    FCALL CLOSE
    adds r0, r5, #0
    FCALL CLOSE
    ldr r0, [r6, #V_TMP]
    b 9f
8:  movs r0, #1
9:  pop {r5, r7}
    pop {r3}
    bx r3
.ltorg

putname:                            @ r0 = 32-byte directory record (NUL-padded 8.3, as 0x5644c leaves it)
    push {r7, lr}
    adds r7, r0, #0
    movs r3, #0
1:  ldrb r0, [r7, r3]
    cmp r0, #0
    beq 2f
    bl putc
    adds r3, #1
    cmp r3, #8
    blo 1b
2:  ldrb r0, [r7, #8]
    cmp r0, #0
    beq 4f
    movs r0, #46
    bl putc
    movs r3, #8
3:  ldrb r0, [r7, r3]
    cmp r0, #0
    beq 4f
    bl putc
    adds r3, #1
    cmp r3, #11
    blo 3b
4:  pop {r7}
    pop {r3}
    bx r3

list_irom:                          @ "irom=NAME size=N[ dir=1]" per root entry, filters as -NLST 0xcc970 does
    push {r7, lr}
    STR r0, p_irom
    FCALL OPENDIR
    cmp r0, #0
    beq 9f
    cmp r0, r4
    beq 9f
    adds r7, r0, #0
    movs r0, #0
    str r0, [r6, #V_DN]
1:  ldr r0, [r6, #V_DN]
    adds r0, #1
    str r0, [r6, #V_DN]
    cmp r0, #64                     @ at most 64 x 16 records
    bhi 8f
    adds r0, r7, #0
    FCALL READDIR
    cmp r0, #0
    beq 8f
    str r0, [r6, #V_DP]
    movs r0, #0
    str r0, [r6, #V_DI]
2:  ldr r0, [r6, #V_DI]
    cmp r0, #16
    bhs 1b
    adds r1, r0, #1
    str r1, [r6, #V_DI]
    lsls r0, r0, #5
    ldr r1, [r6, #V_DP]
    adds r0, r0, r1
    str r0, [r6, #V_DE]
    ldrb r1, [r0]
    cmp r1, #0
    beq 2b
    cmp r1, #0xe5                   @ deleted
    beq 2b
    ldrb r1, [r0, #11]
    lsls r2, r1, #27
    bmi 3f                          @ directory: listed
    lsls r2, r1, #28
    bmi 2b                          @ volume label / long-name record
3:  STR r0, k_irom
    bl puts
    ldr r0, [r6, #V_DE]
    bl putname
    STR r0, k_size
    bl puts
    ldr r0, [r6, #V_DE]
    ldr r0, [r0, #0x1c]             @ size, byte-swapped to BE by 0x5644c
    bl putdec
    ldr r0, [r6, #V_DE]
    ldrb r1, [r0, #11]
    lsls r2, r1, #27
    bpl 4f
    STR r0, s_dir
    bl puts
4:  movs r0, #10
    bl putc
    b 2b
8:  adds r0, r7, #0
    FCALL CLOSEDIR
9:  pop {r7}
    pop {r3}
    bx r3
.ltorg

main:
    push {r7, lr}
    STR r0, s_head
    bl puts
    ldr r1, =IROM_MOUNTED
    ldrb r1, [r1]
    str r1, [r6, #V_MOUNTED]
    STR r0, k_mounted
    bl kvd
    ldr r1, =IROM_STATUS
    ldrb r1, [r1]
    STR r0, k_iromst
    bl kvd
    ldr r1, =BOOT_MODE
    ldrb r1, [r1]
    STR r0, k_bootmode
    bl kvd
    ldr r1, =IROM_D1
    ldrb r1, [r1]
    STR r0, k_d1
    bl kvd
    ldr r0, [r6, #V_MOUNTED]
    cmp r0, #0
    bne 1f
    movs r0, #0                     @ as 0xb60bc (COPY TO CARD): /IROM not mounted -> 0x49384(0)
    FCALL IROM_MOUNT
    adds r1, r0, #0
    STR r0, k_mountrc
    bl kvh
    ldr r1, =IROM_MOUNTED
    ldrb r1, [r1]
    str r1, [r6, #V_MOUNTED]
    STR r0, k_mounted2
    bl kvd
    ldr r1, =IROM_STATUS
    ldrb r1, [r1]
    STR r0, k_iromst2
    bl kvd
    ldr r1, =IROM_D1
    ldrb r1, [r1]
    STR r0, k_d1b
    bl kvd
1:  STR r0, p_ls
    bl exists
    cmp r0, #0
    beq 1f
    b m_list
1:  STR r0, p_rm
    bl exists
    cmp r0, #0
    beq m_inst
    b m_rm
m_inst:
    STR r0, k_mode
    STR r1, v_install
    bl kvs
    ldr r0, [r6, #V_MOUNTED]
    cmp r0, #1
    beq 1f
    b m_unmounted
1:  STR r0, p_src
    bl sum_file
    str r0, [r6, #V_PRESENT]
    STR r0, ks_src
    bl logsum
    ldr r0, [r6, #V_PRESENT]
    cmp r0, #0
    bne 2f
    STR r0, r_nosrc
    b m_res
2:  ldr r0, [r6, #V_VALID]
    cmp r0, #0
    bne 3f
    STR r0, r_badsrc
    b m_res
3:  ldr r0, [r6, #V_SIZE]
    str r0, [r6, #V_SRCSIZE]
    ldr r0, [r6, #V_SUM]
    str r0, [r6, #V_SRCSUM]
    STR r0, p_dst
    bl sum_file
    STR r0, ks_dstb
    bl logsum
    bl matches
    cmp r0, #0
    beq 4f
    STR r0, r_same
    b m_res
4:  STR r0, p_dst
    bl exists
    cmp r0, #0
    beq 5f
    STR r0, p_dst
    FCALL REMOVE
5:  STR r0, p_src
    STR r1, p_dst
    bl copyf
    adds r1, r0, #0
    STR r0, k_copy
    bl kvd
    STR r0, p_dst
    bl sum_file
    STR r0, ks_dst
    bl logsum
    bl matches
    cmp r0, #0
    beq 6f
    STR r0, r_installed
    b m_res
6:  STR r0, p_dst
    bl exists
    cmp r0, #0
    beq 7f
    STR r0, p_dst
    FCALL REMOVE
7:  STR r0, r_verifyfail
    b m_res
.ltorg
m_rm:
    STR r0, k_mode
    STR r1, v_remove
    bl kvs
    ldr r0, [r6, #V_MOUNTED]
    cmp r0, #1
    bne m_unmounted
    STR r0, p_dst
    bl exists
    cmp r0, #0
    bne 1f
    STR r0, r_absent
    b m_res
1:  STR r0, p_dst
    FCALL REMOVE
    adds r1, r0, #0
    STR r0, k_rmrc
    bl kvh
    STR r0, p_dst
    bl exists
    cmp r0, #0
    bne 2f
    STR r0, r_removed
    b m_res
2:  STR r0, r_rmfail
    b m_res
m_list:
    STR r0, k_mode
    STR r1, v_list
    bl kvs
    ldr r0, [r6, #V_MOUNTED]
    cmp r0, #1
    bne m_unmounted
    STR r0, r_listed
    b m_res
m_unmounted:
    STR r0, r_unmounted
m_res:
    adds r1, r0, #0
    STR r0, k_result
    bl kvs
    ldr r0, [r6, #V_MOUNTED]
    cmp r0, #1
    bne 1f
    bl list_irom
1:  STR r0, s_end
    bl puts
    STR r0, p_log
    bl write_log
    pop {r7}
    pop {r3}
    bx r3
.ltorg
"""

INSTALL_DATA = {
    "s_head": "koma=irominstall\nversion=2\n", "s_end": "end=1\n", "s_dir": " dir=1",
    "k_mounted": "mounted=", "k_iromst": "irom_status=",
    "k_bootmode": "boot_mode_1b9866=", "k_d1": "irom_d1=", "k_mountrc": "mount_rc=", "k_mounted2": "mounted_after=",
    "k_iromst2": "irom_status_after=", "k_d1b": "irom_d1_after=", "k_mode": "mode=", "k_copy": "copy_rc=",
    "k_rmrc": "remove_rc=", "k_result": "result=", "k_irom": "irom=", "k_size": " size=",
    "ks_src": ["src_size=", "src_sum=", "src_valid="],
    "ks_dstb": ["dst_before_size=", "dst_before_sum=", "dst_before_valid="],
    "ks_dst": ["dst_size=", "dst_sum=", "dst_valid="],
    "v_install": "install", "v_remove": "remove", "v_list": "list",
    "r_nosrc": "nosrc", "r_badsrc": "badsrc", "r_same": "same", "r_installed": "installed",
    "r_verifyfail": "verify_fail", "r_absent": "absent", "r_removed": "removed", "r_rmfail": "remove_fail",
    "r_listed": "listed", "r_unmounted": "unmounted",
    "p_src": "/ATA1/KOMAPKG.BIN", "p_dst": "/IROM/GRMOD.BIN", "p_rm": "/ATA1/KOMARM.TXT", "p_ls": "/ATA1/KOMALS.TXT",
    "p_log": "/ATA1/IROMINST.TXT", "p_irom": "/IROM",
}

USB = """
check_site:                         @ -> r0 = 1 if every context word around the patch site is stock 2.40
    ldr r2, =(sitetab - body0)
    adds r2, r2, r5
1:  ldr r0, [r2]
    cmp r0, #0
    beq 2f
    ldr r1, [r2, #4]
    ldr r0, [r0]
    cmp r0, r1
    bne 3f
    adds r2, #8
    b 1b
2:  movs r0, #1
    bx lr
3:  movs r0, #0
    bx lr
.ltorg

main:
    push {r7, lr}
    STR r0, s_head
    bl puts
    ldr r1, =USB_MODE
    ldrb r1, [r1]
    STR r0, k_modeb
    bl kvd
    ldr r1, =USB_SETTING
    ldrb r1, [r1]
    STR r0, k_setting
    bl kvd
    ldr r1, =SITE
    ldrh r1, [r1]
    STR r0, k_codeb
    bl kvh
    bl check_site
    cmp r0, #0
    beq 3f
    ldr r0, =SITE
    ldrh r0, [r0]
    ldr r1, =SITE_BEFORE
    cmp r0, r1
    bne 2f
    ldr r0, [r6, #V_SVC]            @ old = irq_set(0xc0) (ARM, service table +0x28)
    ldr r3, [r0, #0x28]
    movs r0, #0xc0
    bl via
    str r0, [r6, #V_TMP]
    ldr r0, =SITE
    ldr r1, =SITE_AFTER
    strh r1, [r0]                   @ 0xc4adc beq -> nop: 0xc4acc always takes its MASS STR arm
    ldr r0, =USB_MODE
    movs r1, #2
    strb r1, [r0]                   @ runtime mode byte, as 0x3d4a4 sets it for MASS STR
    ldr r0, [r6, #V_SVC]
    ldr r3, [r0, #0x28]
    ldr r0, [r6, #V_TMP]
    bl via
    STR r0, r_set
    b 4f
2:  ldr r1, =SITE_AFTER
    cmp r0, r1
    bne 3f
    STR r0, r_already
    b 4f
3:  STR r0, r_mismatch
4:  adds r7, r0, #0
    ldr r1, =USB_MODE
    ldrb r1, [r1]
    STR r0, k_modea
    bl kvd
    ldr r1, =USB_SETTING
    ldrb r1, [r1]
    STR r0, k_settinga
    bl kvd
    ldr r1, =SITE
    ldrh r1, [r1]
    STR r0, k_codea
    bl kvh
    adds r1, r7, #0
    STR r0, k_result
    bl kvs
    STR r0, s_end
    bl puts
    STR r0, p_log
    bl write_log
    pop {r7}
    pop {r3}
    bx r3
.ltorg
.p2align 2
sitetab:                            @ (address, expected stock word); 0xc4adc itself is checked separately
    .word 0x000c4ad0, 0x481f2400    @ ldr r0,=0x1b0d0; movs r4,#0
    .word 0x000c4ad4, 0x79c0491f    @ ldrb r0,[r0,#7] (0x1b0d7); ldr r1,=0x1bd244
    .word 0x000c4ad8, 0x4a1f2800    @ ldr r2,=0x1bd241; cmp r0,#0
    .word 0x000c4ae0, 0x70106008    @ strb r0,[r2] (mode=2); str r0,[r1]
    .word 0x000c4afc, 0x7014600c    @ strb r4,[r2] (mode=0); str r4,[r1]
    .word 0x000c4b50, 0x0001b0d0
    .word 0x000c4b54, 0x001bd244
    .word 0x000c4b58, 0x001bd241
    .word 0
"""

USB_DATA = {
    "s_head": "koma=usbmass\nversion=1\n", "s_end": "end=1\n",
    "k_modeb": "mode_1bd241_before=", "k_setting": "setting_1b0d7_before=", "k_codeb": "code_c4adc_before=",
    "k_modea": "mode_1bd241_after=", "k_settinga": "setting_1b0d7_after=", "k_codea": "code_c4adc_after=",
    "k_result": "result=", "r_set": "set", "r_already": "already", "r_mismatch": "mismatch",
    "p_log": "/ATA1/USBMASS.TXT",
}

CONSTS = dict(IROM_MOUNT=IROM_MOUNT, IROM_D1=IROM_D1, BOOT_MODE=BOOT_MODE, OPEN=OPEN, CREATE_FLAG=CREATE_FLAG, READ=READ, WRITE=WRITE, CLOSE=CLOSE, REMOVE=REMOVE, OPENDIR=OPENDIR,
              READDIR=READDIR, CLOSEDIR=CLOSEDIR, IROM_MOUNTED=IROM_MOUNTED, IROM_STATUS=IROM_STATUS,
              USB_MODE=USB_MODE, USB_SETTING=USB_SETTING, SITE=SITE, SITE_BEFORE=SITE_BEFORE, SITE_AFTER=SITE_AFTER,
              WSIZE=WSIZE, IO=IO, LOG=LOG, LOGMAX=LOGMAX, **{f"V_{k}": v for k, v in V.items()})


def data_block(d):
    out = [".p2align 2", "pow10:"] + [f"    .word {10 ** i}" for i in range(9, -1, -1)] + ["    .word 0"]
    for k, v in d.items():
        out.append(f"{k}:")
        for s in (v if isinstance(v, list) else [v]):
            out.append("    .asciz " + json.dumps(s))
    return "\n".join(out) + "\n"


def expand(src):
    """FCALL NAME -> ldr r3, =addr|1; bl via.  STR rX, label -> PC-independent address of a body label."""
    def fcall(m):
        return f"{m.group(1)}    ldr r3, ={CONSTS[m.group(2)] | 1:#x}\n    bl via"

    def straddr(m):
        r, lab = m.group(2), m.group(3)
        return f"{m.group(1)}    ldr {r}, =({lab} - body0)\n    adds {r}, {r}, r5"
    src = re.sub(r"^([ \t]*(?:\w+:)?)[ \t]*FCALL (\w+)[ \t]*$", fcall, src, flags=re.M)
    src = re.sub(r"^([ \t]*(?:\w+:)?)[ \t]*STR (r\d), (\w+)[ \t]*$", straddr, src, flags=re.M)
    for k in sorted(CONSTS, key=len, reverse=True):
        src = re.sub(rf"(?<![\w.]){k}(?!\w)", f"{CONSTS[k]:#x}", src)
    return src


def build(name, body_src, data):
    src = expand(ENTRY + LIB + body_src) + data_block(data)
    code, syms = assemble(src, 0)
    assert syms["entry"] == 0 and syms["body0"] == 0
    mod = grmod.pack_module(code, 0)
    chk = grmod.parse_module(mod)
    assert chk["ok"], chk
    txt = disasm(code[:syms["pow10"]], 0)
    assert not any(" mrc " in t or " mcr " in t or "p15" in t for t in txt), "no CP15"
    return mod, syms, txt


IRODUMP_STEP = r"""
@ ---- dump step (irodump): /IROM/KOMADIAG.TXT and /IROM/KOMATRC.TXT -> card root, only while /IROM is mounted
dump_all:
    push {r7, lr}
    ldr r0, [r6, #V_MOUNTED]
    cmp r0, #1
    bne 9f
    STR r0, p_d1s
    str r0, [r6, #V_DP]
    STR r0, p_d1d
    str r0, [r6, #V_DI]
    STR r0, k_dg1
    str r0, [r6, #V_DN]
    bl dump1
    STR r0, p_d2s
    str r0, [r6, #V_DP]
    STR r0, p_d2d
    str r0, [r6, #V_DI]
    STR r0, k_dg2
    str r0, [r6, #V_DN]
    bl dump1
9:  pop {r7}
    pop {r3}
    bx r3

dump1:                              @ V_DP src, V_DI dst, V_DN keys "<x>_copy=", "<x>_size=" (consecutive)
    push {r7, lr}
    ldr r0, [r6, #V_DP]
    bl exists
    cmp r0, #0
    bne 1f
    ldr r0, [r6, #V_DN]
    STR r1, v_absent
    bl kvs
    b 9f
1:  ldr r0, [r6, #V_DI]
    bl exists
    cmp r0, #0
    beq 2f
    ldr r0, [r6, #V_DI]
    FCALL REMOVE
2:  ldr r0, [r6, #V_DP]
    ldr r1, [r6, #V_DI]
    bl copyf
    adds r1, r0, #0
    ldr r0, [r6, #V_DN]
    bl kvd
    ldr r0, [r6, #V_DN]
1:  ldrb r1, [r0]                   @ skip the first key (no output)
    adds r0, #1
    cmp r1, #0
    bne 1b
    adds r7, r0, #0                 @ second key
    ldr r0, [r6, #V_DI]
    bl fsize
    adds r1, r0, #0
    adds r0, r7, #0
    bl kvd
9:  pop {r7}
    pop {r3}
    bx r3
.ltorg

fsize:                              @ r0 = path -> r0 = bytes read until EOF, or -1
    push {r7, lr}
    movs r1, #0
    FCALL OPEN
    cmp r0, r4
    beq 8f
    adds r7, r0, #0
    movs r0, #0
    str r0, [r6, #V_TMP2]
1:  adds r0, r7, #0
    ldr r1, =IO
    adds r1, r1, r6
    ldr r2, =0x200
    FCALL READ
    cmp r0, #0
    ble 2f
    ldr r1, [r6, #V_TMP2]
    adds r1, r1, r0
    str r1, [r6, #V_TMP2]
    b 1b
2:  adds r0, r7, #0
    FCALL CLOSE
    ldr r0, [r6, #V_TMP2]
    b 9f
8:  adds r0, r4, #0
9:  pop {r7}
    pop {r3}
    bx r3
.ltorg
"""


def irodump_parts():
    anchor = "1:  STR r0, p_ls\n"
    assert INSTALL.count(anchor) == 1
    inst = INSTALL.replace(anchor, "1:  bl dump_all\n    STR r0, p_ls\n") + IRODUMP_STEP
    data = dict(INSTALL_DATA)
    data["s_head"] = "koma=irodump\nversion=1\n"
    data["p_log"] = "/ATA1/IRODUMP.TXT"
    data.update({"p_d1s": "/IROM/KOMADIAG.TXT", "p_d1d": "/ATA1/KOMADIAG.TXT", "k_dg1": ["diag_copy=", "diag_size="],
                 "p_d2s": "/IROM/KOMATRC.TXT", "p_d2d": "/ATA1/KOMATRC.TXT", "k_dg2": ["trace_copy=", "trace_size="],
                 "v_absent": "absent"})
    return inst, data


def main():
    ap = argparse.ArgumentParser(description="Build irominstall.grmod, irodump.grmod and usbmass.grmod.")
    ap.add_argument("-o", "--outdir", type=Path, default=Path("."), help="output directory (default: current)")
    a = ap.parse_args()
    a.outdir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, body, data in (("irominstall", INSTALL, INSTALL_DATA), ("irodump", *irodump_parts()),
                             ("usbmass", USB, USB_DATA)):
        mod, syms, txt = build(name, body, data)
        p = a.outdir / f"{name}.grmod"
        p.write_bytes(mod)
        out[name] = dict(file=p.name, bytes=len(mod), sha256=hashlib.sha256(mod).hexdigest(),
                         checksum=grmod.parse_module(mod)["checksum"])
        print(name, len(mod), out[name]["sha256"])
    (a.outdir / "irominstall.json").write_text(json.dumps(dict(loader_abi=1, image_version_word="0x240", modules=out),
                                                          indent=1) + "\n")


if __name__ == "__main__":
    main()
