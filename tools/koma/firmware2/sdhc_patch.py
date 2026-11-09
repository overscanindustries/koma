#!/usr/bin/env python3
"""SDHC/SDXC driver patch for GR DIGITAL 2.40 (Koma 2), on top of the Koma 1 loader image. Source + edit list.

Offline only: this module assembles Thumb code and describes byte edits; build.py applies them.

SD 2.0 / SDHC / SDXC cards initialise (CMD8, ACMD41 HCS, CCS latch, CSD v2), are block-addressed, and are served at
their real capacity to USB mass storage. The stock FS never mounts or formats a CCS card unless a full-capacity FS
module (widefs, tools/koma/sdhc) sets `fs_wide_ok`. SDSC cards run the stock driver bytes.

  H1  0xc9a6c  bl 0xc9aa0 -> bl sd_init     stock SD path first; only if it gives up does sd_init send CMD8 and
                                            ACMD41 with HCS; ready -> FLAG = 1 (CCS=0) or 2 (CCS=1)
  H2  0xc9bd4  bl 0xc9ce8 -> bl csd_hook    CSD v2 capacity for FLAG == 2; CSD/flag mismatch latched
  H3  0xc9a86  bl 0xc9b34 -> bl common_hook mismatch latched -> card unusable
  A3  group B: relocated copies of the chunk functions 0x508f8/0x50afc at 0x575b4/0x57740 carry block numbers;
      rd_guard/wr_guard pick the copy when FLAG == 2. The stock functions are untouched.
  A4  0x5087a and the read/write chunk BLs: I/O at or past the served capacity is refused
  A6  0xb3970  mount_guard   a CCS card does not mount unless fs_wide_ok
  A7  0xb26e8  fmt_guard     a CCS card is not formatted unless fs_wide_ok
  A8  0x50936/0x50b36  swap_guard   card removed -> every later chunk fails until the next init
  /IROM fallback: the loader opens /IROM/GRMOD.BIN when /ATA1/GRMOD.BIN cannot be opened (mounting /IROM if needed)
  M   0x112578 'Rev024M' -> '240K2'          MakerNote tag 0x2 / firmware tag
FLAG and the other ABI words live inside the cave (RAM copy of the image; nothing else references the cave).
"""
from __future__ import annotations

import re

import capstone

from ..vendor.asm_legacy import assemble, disasm, thumb_bl   # noqa: F401

LOAD = 0x1C000
CAVE = 0x57274            # tail of dead FUN_0005721c (Dcfs island), after flash-candidate-2's 88-B gate slot
CAVE_END = 0x57590        # exp4's claim starts here
CAP_SECTORS = 0x3C0000    # default only; the build's single source is build.py CAP_SECTORS (passed in)
C8_TICKS = 100            # CMD8 response poll bound (ticks of 0x50c8a, ~1 ms each: I)

SRC_T = """
@ ---------------------------------------------------------------- H1: init wrapper (replaces bl 0xc9aa0 in 0xc9a68)
sd_init:
    push {{r4, r5, r6, r7, lr}}
    adds r7, r0, #0             @ r7 = &card type (0x1bd210)
    adr r4, flag
    movs r0, #0
    str r0, [r4, #0]            @ FLAG = 0 at the start of every init
    str r0, [r4, #4]            @ A8 'card seen absent' latch = 0 at the start of every init
    str r0, [r4, #8]            @ real card sectors = 0
    str r0, [r4, #12]           @ served limit (sectors) = 0 until init succeeds
    str r0, [r4, #16]           @ fs_wide_ok = 0 (only a full-capacity FS module may set it, after init)
    adds r0, r7, #0
CALL_tstock: .hword 0xf000, 0xf800      @ X1 t_stock: stock 0xc9aa0 unchanged + trace + HCS=0-quirk check (2b)
    cmp r0, #0
    bne i_out                   @ CMD0 failed 7x: stock -1
    ldr r0, [r7, #0]
    cmp r0, #2
    bne i_ok                    @ stock found an SD card (type 1): nothing new runs
    movs r1, #0                 @ stock gave up (busy x17 or CMD55 illegal): SD 2.0 probe
    movs r0, #0
CALL_tcmd0: .hword 0xf000, 0xf800       @ CMD0 (via X2 t_cmd0: trace stage 2)
    cmp r0, #0
    bne i_ok                    @ keep type 2: stock MMC path, as today
    movs r6, #1                 @ CMD8 tries left after this one (one retry)
i_c8:
    movs r2, #0
    movs r1, #0
    movs r0, #10
CALL_gap: .hword 0xf000, 0xf800         @ ~10 ms gap before CMD8 (stock delay helper 0x268f8 op 10)
CALL_tcmd8: .hword 0xf000, 0xf800       @ cmd8 (via X3 t_cmd8: trace stage 3/4)
    cmp r0, #1
    beq i_v2
    subs r6, #1
    bpl i_c8                    @ one bounded retry
    b i_ok                      @ no valid R7 echo: keep type 2, stock MMC path
i_v2:
    movs r6, #0                 @ try 0..16 (17 tries, like stock)
    ldr r5, =0x1fb390           @ stock R1/OCR store
i_loop:
    movs r1, #0
    movs r0, #0x37
CALL_cmd55: .hword 0xf000, 0xf800       @ CMD55 arg 0
    cmp r0, #0
    bne i_fail
    adds r1, r5, #0
    movs r0, #0
CALL_resp1: .hword 0xf000, 0xf800       @ 0x2679c(0, 0x1fb390): R1
    ldr r0, [r5, #0]
    lsrs r0, r0, #23            @ C = R1 bit 22 ILLEGAL_COMMAND
    bcs i_fail
    ldr r1, =0x40100000         @ HCS + 3.2-3.3 V (stock window)
    movs r0, #0x69
CALL_ta41: .hword 0xf000, 0xf800        @ ACMD41 (via X4 t_a41: trace stage 5 + try count)
    cmp r0, #0
    bne i_fail
    adds r1, r5, #4
    movs r0, #3
CALL_tr3: .hword 0xf000, 0xf800         @ 0x2679c(3, 0x1fb394): OCR (via X5 t_r3: trace stage 7 on ready)
    ldr r0, [r5, #4]
    cmp r0, #0
    bge i_busy                  @ OCR bit 31 clear: still busy
    movs r1, #1                 @ ready, CCS = 0
    lsls r0, r0, #1
    bpl i_set
    movs r1, #2                 @ ready, CCS = 1 (OCR bit 30)
i_set:
    str r1, [r4, #0]
    movs r0, #1
    str r0, [r7, #0]            @ type 1 = SD
i_ok:
    movs r0, #0
i_out:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
i_busy:
    movs r5, #0                 @ wait (try+1) x 0x268f8(10): the stock delay ramp
i_dly:
    movs r2, #0
    movs r1, #0
    movs r0, #10
CALL_wait10: .hword 0xf000, 0xf800
    adds r5, #1
    cmp r5, r6
    bls i_dly
    ldr r5, =0x1fb390
    adds r6, #1
    cmp r6, #24                 @ 25 tries (2b; was 17): last ACMD41 ~3.1 s after the first
    bls i_loop
i_fail:
    movs r0, #0                 @ SD 2.0 card that never became ready / misbehaved: init fails
    mvns r0, r0
    b i_out

@ ---------------------------------------------------------------- CMD8 (SEND_IF_COND), own bounded poll
cmd8:                           @ returns r0 = 1 on a valid R7 echo (low 12 bits 0x1AA), else 0
    push {{r4, r5, r6, lr}}
    mov r2, sp                  @ (unused by op 0, as stock passes it)
    movs r1, #0
    movs r0, #0
CALL_ready: .hword 0xf000, 0xf800       @ 0x268f8(0): wait 'ready for command' (bit 31), stock helper
    adds r0, #1
    beq c8_no                   @ -1: not ready
    ldr r4, =0x12044000
    ldr r5, =0x12044218
    movs r0, #0
    str r0, [r4, #0x38]         @ clear status lo/hi and error detail lo/hi (as 0x26420 does)
    str r0, [r4, #0x3c]
    str r0, [r4, #0x58]
    str r0, [r4, #0x5c]
    ldr r0, =0x1aa
    str r0, [r4, #0x08]         @ arg low
    movs r0, #0
    str r0, [r4, #0x0c]         @ arg high
    ldr r0, =0x0408             @ CMD8, RSP field 100b (R1/R7)  -- hardware-unproven encoding
    str r0, [r4, #0x00]
CALL_tick0: .hword 0xf000, 0xf800
    adds r6, r0, #0
c8_poll:
    ldr r0, [r5, #0]
    ldr r1, =0x007f0000         @ any error bit 16..22 (cmd timeout, CRC, ...)
    tst r0, r1
    bne c8_no
    lsrs r0, r0, #1             @ C = bit 0 response end
    bcs c8_resp
CALL_tick1: .hword 0xf000, 0xf800
    subs r0, r0, r6
    cmp r0, #{C8_TICKS}
    bls c8_poll
    b c8_no
c8_resp:
    ldr r0, [r4, #0x5c]         @ error detail high: must be clear
    lsls r0, r0, #16
    bne c8_no
    ldr r0, [r4, #0x18]         @ response bits 23:8 -> R7 arg low half
    lsls r0, r0, #20
    lsrs r0, r0, #20
    ldr r1, =0x1aa
    cmp r0, r1
    bne c8_no
    movs r0, #1
    b c8_out
c8_no:
    movs r0, #0
c8_out:
    pop {{r4, r5, r6}}
    pop {{r3}}
    bx r3

@ ---------------------------------------------------------------- H2: CSD (replaces bl 0xc9ce8 in 0xc9b34)
csd_hook:
    push {{r4, lr}}
CALL_csd: .hword 0xf000, 0xf800         @ stock 0xc9ce8, unchanged
    ldr r3, =0x2dea68           @ CSD words (CRC byte dropped): w0=[39:8] w1=[71:40] w2=[103:72] w3=[127:104]
    ldr r0, [r3, #12]
    lsls r0, r0, #8
    lsrs r0, r0, #30            @ CSD_STRUCTURE = CSD[127:126]
    adr r4, flag
    ldr r1, [r4, #0]
    cmp r1, #2
    beq k_hc
    cmp r0, #1
    bne k_out                   @ v1 CSD, not SDHC: stock result stands
    ldr r2, =0x1bd210
    ldr r2, [r2, #0]
    cmp r2, #1
    bne k_out                   @ MMC card: stock behaviour (MMC CSD_STRUCTURE 1 is legal there)
    b k_mism                    @ SD card, CCS=0 (or stock path) but CSD v2: refuse
k_hc:
    cmp r0, #1
    bne k_mism                  @ CCS=1 but not CSD v2: refuse
    ldr r0, [r3, #4]
    lsls r0, r0, #2
    lsrs r0, r0, #10            @ C_SIZE = CSD[69:48] (22 bits)
    adds r0, #1                 @ <= 0x400000
    lsrs r2, r0, #22
    bne k_sat                   @ C_SIZE = 0x3FFFFF: (C_SIZE+1)*1024 = 2^32, saturate
    lsls r0, r0, #10            @ real sectors
    b k_real
k_sat:
    movs r0, #0
    mvns r0, r0
k_real:
    str r0, [r4, #8]            @ real card sectors (from the CSD; the Base-2 sector-count word)
    str r0, [r4, #12]           @ served limit for the A4/A5/A6 guards = real capacity
    ldr r2, =0x7fffff
    cmp r0, r2
    bls k_nocap
    adds r0, r2, #0             @ stock byte field saturates at 0x7fffff sectors (0xfffffe00 bytes): no u32 wrap
k_nocap:
    lsls r0, r0, #9
    str r0, [r3, #0x18]         @ [0x2dea80] capacity bytes (clamped; stock FS never uses it for a CCS card)
    movs r0, #1
    lsls r0, r0, #9
    str r0, [r3, #0x14]         @ [0x2dea7c] block length 512
    b k_out
k_mism:
    movs r2, #0x80
    orrs r1, r2
    str r1, [r4, #0]            @ FLAG |= 0x80 (mismatch latched)
k_out:
    pop {{r4}}
    pop {{r3}}
    bx r3

@ ---------------------------------------------------------------- H3: common init (replaces bl 0xc9b34 in 0xc9a68)
common_hook:
    push {{r4, lr}}
CALL_tcommon: .hword 0xf000, 0xf800     @ X6 t_common: stock 0xc9b34 + trace + one re-init on a CCS=0/CSD v2 mismatch
    cmp r0, #0
    bne m_out                   @ stock failure passes through
    adr r4, flag
    ldr r1, [r4, #0]
    movs r2, #0x80
    tst r1, r2
    bne m_bad
    cmp r1, #2
    beq m_out                   @ SDHC: csd_hook set the served limit
    ldr r2, =0x2dea80
    ldr r2, [r2, #0]
    lsrs r2, r2, #9
    str r2, [r4, #12]           @ SDSC/MMC: served limit = stock capacity (= ops template +0x18)
    b m_out
m_bad:
    ldr r1, =0x2dea80
    movs r2, #0
    str r2, [r1, #0]            @ capacity 0: nothing can address the card
    str r2, [r4, #12]
    mvns r0, r2                 @ -1: init failed (controller stopped, drive status 3, as stock failures)
m_out:
    pop {{r4}}
    pop {{r3}}
    bx r3

@ ---------------------------------------------------------------- A4/A5: chunk guards
rd_guard:                       @ replaces bl 0x508f8: r1 = sector, r2 = count
    push {{r0, r4}}
    adr r0, rd_tgt
    b g_chk
wr_guard:                       @ replaces bl 0x50afc
    push {{r0, r4}}
    adr r0, wr_tgt
g_chk:
    adr r4, flag
    ldr r4, [r4, #0]
    cmp r4, #2
    bne g_sel
    adds r0, #4                 @ SDHC: the block-addressed copy (group B)
g_sel:
    ldr r0, [r0, #0]
    mov r12, r0
    adr r4, flag
    ldr r4, [r4, #12]           @ served limit in sectors (0 until init succeeds)
    cmp r1, r4
    bhs g_fail                  @ start at or beyond capacity
    subs r4, r4, r1
    cmp r2, r4
    bhi g_fail                  @ would run past capacity
    pop {{r0, r4}}
    bx r12                      @ stock chunk function, same r0-r3, same lr
g_fail:
    pop {{r0, r4}}
    movs r0, #0
    mvns r0, r0                 @ -1, no command sent
    bx lr

@ ---------------------------------------------------------------- A8: sticky card-removed latch (replaces bl 0x262f0)
swap_guard:                     @ returns r0 = 0 present / -1 absent, like 0x262f0; clobbers r0 + flags only
    push {{r1}}
    adr r1, flag
    ldr r0, =0x12044218
    ldr r0, [r0, #0]
    lsrs r0, r0, #6             @ C = bit 5 card present
    bcs s_chk
    movs r0, #1
    str r0, [r1, #4]            @ seen absent: latch until the next init (reboot)
s_chk:
    ldr r0, [r1, #4]
    pop {{r1}}
    cmp r0, #0
    beq s_ok
    movs r0, #0
    mvns r0, r0
s_ok:
    bx lr

@ ---------------------------------------------------------------- USB READ CAPACITY (replaces 2 insns at 0x157134)
usb_cap:                        @ r1 = card sectors; returns past 0x157138 'lsr r1,r0,#9'; clobbers r2 (dead there)
    adr r1, flag
    ldr r2, [r1, #0]
    cmp r2, #2
    bne u_stock
    ldr r1, [r1, #8]            @ CCS card: the real sector count
    b u_ret
u_stock:
    ldr r1, =0x2dea80
    ldr r1, [r1, #0]
    lsrs r1, r1, #9             @ stock value, same as 0x157136/0x157138
u_ret:
    mov r2, lr
    adds r2, #2                 @ skip 0x157138 (lr keeps the Thumb bit)
    bx r2

@ ---------------------------------------------------------------- A6: mount (replaces 'beq +2; movs r7,#0x10' at 0xb3970)
mount_guard:                    @ in: r0 = cluster-count high half, r4 = drive, r6 = drive struct, r7 = error
    cmp r0, #0
    beq mg_1
    movs r7, #0x10              @ stock: 65536+ clusters
mg_1:
    cmp r4, #1
    bne mg_out                  @ only the SD card drive
    adr r0, flag
    ldr r1, [r0, #0]
    cmp r1, #2
    bne mg_ext                  @ not a CCS card: extent check only
    ldr r1, [r0, #16]
    cmp r1, #0
    beq mg_bad                  @ LOCKOUT: CCS card and no full-capacity FS (fs_wide_ok == 0): never mount
mg_ext:
    ldr r2, [r0, #16]           @ fs_wide_ok: selects the unit of drive+0x9c
    ldr r0, [r0, #12]           @ served limit in sectors (real capacity)
    adds r1, r6, #0
    adds r1, #0x80
    ldr r3, [r1, #0x1c]         @ drive+0x9c: volume start
    cmp r2, #0
    bne mg_sec                  @ widefs FS (0xb29b8 patched: start = startLBA * 1): already sectors
    lsrs r3, r3, #9             @ stock FS: bytes (startLBA * 0x200, 0xb29b8..0xb29c0) -> sectors
mg_sec:
    ldr r2, [r1, #0x34]         @ drive+0xb4: volume TotSec
    cmp r2, r0
    bhi mg_bad
    subs r0, r0, r2
    cmp r3, r0
    bls mg_out
mg_bad:
    movs r7, #0x10              @ volume reaches past the card: mount error
mg_out:
    bx lr

@ ---------------------------------------------------------------- A7: format (replaces bl 0xd2f54 at 0xb26e8)
fmt_guard:                      @ r0 = drive; returns size index like 0xd2f54 (clobbers r1, r3 only)
    movs r3, #0x6c
    muls r3, r0, r3
    ldr r1, =0x2dfc40           @ slot 0 + 0x3c
    adds r3, r3, r1
    ldrb r3, [r3, #0]
    cmp r0, #1
    blt f_ok                    @ internal memory: stock
    adr r1, flag
    ldr r1, [r1, #0]
    cmp r1, #2
    bne f_idx
    adr r1, flag
    ldr r1, [r1, #16]
    cmp r1, #0
    beq f_refuse                @ LOCKOUT: CCS card and fs_wide_ok == 0: format refused before any write
f_idx:
    ldr r1, =0x19fe0e
    ldrh r1, [r1, #0]           @ card format table entries (11)
    cmp r3, r1
    bhs f_refuse
f_ok:
    adds r0, r3, #0
    bx lr
f_refuse:
    movs r4, #0x10              @ 0xb25a8 returns 0x10 -> 0xb83a4 reports error 0xcb; nothing written
    ldr r0, =0xb29c3            @ 0xb25a8 epilogue (add r0,r4; add sp,#0xc; pop)
    bx r0

.p2align 2
.ltorg
.p2align 2
.p2align 2
rd_tgt:
    .word 0x508f9, {rdcopy:#x}  @ stock read chunk (byte addresses) / copy (block addresses)
wr_tgt:
    .word 0x50afd, {wrcopy:#x}
flag:
    .word 0                     @ +0  0 stock path, 1 SD2.0 CCS=0, 2 SDHC (CCS=1); |0x80 mismatch
gone:
    .word 0                     @ +4  A8 latch
real:
    .word 0                     @ +8  real card sectors from a v2 CSD (u32, saturating)
lim:
    .word 0                     @ +12 served limit in sectors (guards A4/A5/A6)
fs_wide_ok:
    .word 0                     @ +16 Base ABI: set non-zero by a full-capacity FS module to admit a CCS card
trace:
    .word 0                     @ +20 init trace (2b, read-only for modules): see TRACE_DOC
ldiag:
    .word 0                     @ +24 loader + presence-latch diag (2b, read-only for modules): see TRACE_DOC
"""
CALLS = dict(gap=0x268F8, cmd55=0x26420, resp1=0x2679C,
             wait10=0x268F8, ready=0x268F8, tick0=0x50C8A, tick1=0x50C8A, csd=0xC9CE8)
# ABI words (widefs.grmod and diag modules read these by address; they must never move)
ABI = dict(flag=0x5753C, gone=0x57540, real=0x57544, lim=0x57548, fs_wide_ok=0x5754C, trace=0x57550, ldiag=0x57554)

# ---------------------------------------------------------------- 2b (image after 514f0a20): trace + HCS=0-quirk fix
# Shims in the free tail of cave 2 (after the group-B copies). Every sd_init/common_hook edit is a 4-byte BL retarget
# (or the same-size cmp), so cave 1's layout and every ABI word stay where they were.
TRACE_DOC = """trace word 0x57550 (written during the one card init per boot; read it after init):
  bits 0-7   stage reached: 1 stock SD path returned; 2 SD 2.0 probe CMD0 sent (stays 2 if CMD0 failed);
             3 CMD8 no valid R7 echo; 4 R7 echo OK; 5 ACMD41(HCS=1) sent, card busy; 7 ACMD41(HCS=1) ready
  bits 8-15  ACMD41(HCS=1) tries sent (1..25; 25 + stage 5 = busy to the end)
  bits 16-23 path: low 2 bits 1 = stock path kept the card (SDSC/SD 1.x, no new command),
             2 = stock gave up -> CMD8 path (spec SDHC/SDXC, MMC), 3 = quirk re-init (card went ready to HCS=0 with
             CCS=1 -> CMD0/CMD8/ACMD41 HCS=1); | 0x04 = mismatch re-init (ready to HCS=0 with CCS=0, CSD v2);
             | 0x40 = stock CMD2/3/9/7/ACMD6 stage failed; | 0x80 = refused (CSD/CCS mismatch latched)
  bits 24-31 OCR[31:24] latched by the stock HCS=0 ACMD41 loop (0x80 SDSC ready, 0xC0 HC card ready = quirk,
             0x00 busy / never answered)
ldiag word 0x57554 (image value 0; nothing zeroes it, RAM is reloaded every boot):
  bits 0-7   loader sleep(40) calls made waiting for tFileTask (0 = ready at the first check; 250 with bit 8
             clear = the ~10 s wait expired)
  bit 8      loader saw tFileTask ready and is opening GRMOD.BIN
  bit 9      a GRMOD.BIN opened (card or /IROM)
  bit 10     module entry called (header/checksum passed)
  bit 16     presence bit 5 read low once but high again within ~16 re-samples (contact glitch absorbed)
  bit 17     card-gone latch set (16 consecutive low re-samples after a low sample, ~5 ms at 48k insns/ms)
  bits 24-31 trace stage byte (0x57550 bits 0-7) when the gone latch was set"""
# Cave 3: free island segment 0x57a08..0x57cd4 (caves README §1; between exp5 and exp3 claims)
LBASE, LBASE_END = 0x57A08, 0x57CD4
SRC_L = """
@ ---------------------------------------------------------------- L1: loader sleep (replaces bl 0x288f6 at 0x57f92)
ld_sleep:                       @ r0 = 40, r4 = sleeps done so far (0..249)
    ldr r1, ={ldiag:#x}
    ldr r2, [r1, #0]
    lsrs r2, r2, #8
    lsls r2, r2, #8
    adds r3, r4, #1
    orrs r2, r3                 @ ldiag 7:0 = sleeps made
    str r2, [r1, #0]
    ldr r3, =0x288f7
    bx r3                       @ tail call sleep(40)

@ ---------------------------------------------------------------- L2: loader open (replaces bl open_both at 0x57fa4)
ld_open:                        @ r0 = "/ATA1/GRMOD.BIN", r1 = 0, r4 = -1
    push {{r0, lr}}
    ldr r2, ={ldiag:#x}
    ldr r3, [r2, #0]
    movs r1, #1
    lsls r1, r1, #8
    orrs r3, r1
    str r3, [r2, #0]            @ bit 8: wait ended with tFileTask ready
    pop {{r0}}
    movs r1, #0
CALL_ob: .hword 0xf000, 0xf800          @ open_both (0x580cc), unchanged
    cmp r0, r4
    beq 1f
    ldr r2, ={ldiag:#x}
    ldr r3, [r2, #0]
    movs r1, #2
    lsls r1, r1, #8
    orrs r3, r1
    str r3, [r2, #0]            @ bit 9: a GRMOD.BIN opened
1:  pop {{r3}}
    bx r3

@ ---------------------------------------------------------------- L3: module entry (replaces bl 0x5807a at 0x58040)
ld_enter:                       @ r0-r2 module args, r3 = entry; lr = 0x58044
    push {{r0, r1, r2}}
    ldr r0, ={ldiag:#x}
    ldr r1, [r0, #0]
    movs r2, #1
    lsls r2, r2, #10
    orrs r1, r2
    str r1, [r0, #0]            @ bit 10: module entered
    pop {{r0, r1, r2}}
    bx r3                       @ as 0x5807a

@ ---------------------------------------------------------------- A8 v2: debounced card-removed latch
swap_guard2:                    @ replaces swap_guard at 0x50936/0x50b36 and in both copies; r0 = 0 present / -1;
    push {{r1, r2, r3}}         @ clobbers r0 + flags only
    ldr r1, ={flag:#x}
    ldr r0, =0x12044218
    ldr r0, [r0, #0]
    lsrs r0, r0, #6             @ C = bit 5 card present
    bcs sg_chk                  @ present: exactly the old path (no extra read)
    movs r2, #16                @ one low sample: re-sample 16 x after ~0x2000-loop spins (~5 ms total)
sg_rs:
    ldr r3, =0x2000
sg_spin:
    subs r3, #1
    bne sg_spin
    ldr r0, =0x12044218
    ldr r0, [r0, #0]
    lsrs r0, r0, #6
    bcs sg_glitch               @ high again: contact bounce, not a removal
    subs r2, #1
    bne sg_rs
    movs r0, #1
    str r0, [r1, #4]            @ gone: latch until the next init (reboot)
    ldr r0, [r1, #20]           @ trace stage byte -> ldiag 31:24
    lsls r0, r0, #24
    ldr r2, ={ldiag:#x}
    ldr r3, [r2, #0]
    lsls r3, r3, #8
    lsrs r3, r3, #8
    orrs r3, r0
    movs r0, #2
    lsls r0, r0, #16
    orrs r3, r0                 @ bit 17
    str r3, [r2, #0]
    b sg_chk
sg_glitch:
    ldr r2, ={ldiag:#x}
    ldr r3, [r2, #0]
    movs r0, #1
    lsls r0, r0, #16
    orrs r3, r0                 @ bit 16
    str r3, [r2, #0]
sg_chk:
    ldr r0, [r1, #4]
    pop {{r1, r2, r3}}
    cmp r0, #0
    beq sg_ok
    movs r0, #0
    mvns r0, r0
sg_ok:
    bx lr
.p2align 2
.ltorg
"""
OPEN_BOTH = 0x580CC

XBASE = 0x578B8                 # cave 2 after the write-chunk copy (0x57740 + 376)
SRC_X = """
@ ---------------------------------------------------------------- X1: stock SD path wrapper (sd_init's call 0xc9aa0)
t_stock:                        @ r0 = r7 = &type (0x1bd210), r4 = &flag; uses r0-r3 only
    push {{lr}}
    ldr r1, [r4, #20]
    lsrs r1, r1, #19            @ C = trace bit 18 (path |4: t_common asked for one re-init)
    bcs ts_retry
CALL_sdpath: .hword 0xf000, 0xf800      @ stock 0xc9aa0, unchanged (CMD0, CMD55/ACMD41 HCS=0 x17)
    ldr r1, =0x1fb394
    ldr r1, [r1, #0]            @ OCR latched by the stock loop
    lsrs r2, r1, #24
    lsls r2, r2, #24            @ trace 31:24 = OCR 31:24
    ldr r3, =0x00010001         @ path 1, stage 1
    adds r2, r2, r3
    cmp r0, #0
    bne ts_out                  @ stock CMD0 failed 7x: -1 passes through
    ldr r3, [r7, #0]
    cmp r3, #2
    beq ts_v2                   @ stock gave up: sd_init's SD 2.0 probe follows (path 2)
    lsls r1, r1, #1             @ type 1 = ready (OCR bit 31 set); N = OCR bit 30 (CCS)
    bpl ts_out                  @ CCS 0: SDSC card, the stock result stands, nothing new runs
    movs r3, #2                 @ CCS 1 to HCS=0: an HC card that went ready against the spec (hw64 cause #1)
    str r3, [r7, #0]            @ report type 2 -> sd_init re-inits it: CMD0, ~10 ms, CMD8, ACMD41 HCS=1
    lsls r3, r3, #16
    adds r2, r2, r3             @ path 3
    b ts_out
ts_v2:
    movs r3, #1
    lsls r3, r3, #16
    adds r2, r2, r3             @ path 2
ts_out:
    str r2, [r4, #20]
    pop {{r3}}
    bx r3
ts_retry:                       @ re-init (t_common): no HCS=0 commands; type 2 -> SD 2.0 probe
    movs r1, #2
    str r1, [r0, #0]
    movs r0, #0
    pop {{r3}}
    bx r3

@ ---------------------------------------------------------------- X2: CMD0 of the SD 2.0 probe (r0 = r1 = 0)
t_cmd0:
    ldr r2, [r4, #20]
    lsrs r2, r2, #8
    lsls r2, r2, #8
    adds r2, #2                 @ stage 2
    str r2, [r4, #20]
    ldr r3, =0x26421
    bx r3                       @ tail call 0x26420(0, 0)

@ ---------------------------------------------------------------- X3: CMD8
t_cmd8:
    push {{lr}}
CALL_cmd8: .hword 0xf000, 0xf800        @ sd_init's cmd8: r0 = 1 on a valid R7 echo
    ldr r2, [r4, #20]
    lsrs r2, r2, #8
    lsls r2, r2, #8
    adds r2, #3                 @ stage 3: no echo
    cmp r0, #1
    bne 1f
    adds r2, #1                 @ stage 4: echo OK
1:  str r2, [r4, #20]
    pop {{r3}}
    bx r3

@ ---------------------------------------------------------------- X4: ACMD41 HCS=1 (r0 = 0x69, r1 = arg, r6 = try)
t_a41:
    ldr r2, [r4, #20]
    lsrs r2, r2, #16
    lsls r2, r2, #16            @ keep path + OCR byte
    adds r3, r6, #1
    lsls r3, r3, #8
    adds r2, r2, r3             @ tries = try + 1
    adds r2, #5                 @ stage 5
    str r2, [r4, #20]
    ldr r3, =0x26421
    bx r3                       @ tail call 0x26420(0x69, arg)

@ ---------------------------------------------------------------- X5: R3 read (r5 = 0x1fb390)
t_r3:
    push {{lr}}
CALL_resp3: .hword 0xf000, 0xf800       @ 0x2679c(3, 0x1fb394)
    ldr r2, [r5, #4]
    cmp r2, #0
    bge 1f
    ldr r2, [r4, #20]
    adds r2, #2                 @ stage 5 -> 7: ready
    str r2, [r4, #20]
1:  pop {{r3}}
    bx r3

@ ---------------------------------------------------------------- X6: common init wrapper (common_hook's call 0xc9b34)
t_common:
    push {{r4, lr}}
    ldr r4, ={flag:#x}
tc_go:
CALL_common: .hword 0xf000, 0xf800      @ stock 0xc9b34 (CMD2/3/9, csd_hook, clock, CMD7, ACMD6)
    ldr r2, [r4, #20]
    cmp r0, #0
    bne tc_err
    ldr r1, [r4, #0]
    cmp r1, #0x80
    bne tc_ref                  @ only 'stock-path SD card with a v2 CSD' is re-inited
    lsrs r3, r2, #19
    bcs tc_ref                  @ once per init
    movs r3, #4
    lsls r3, r3, #16
    orrs r2, r3
    str r2, [r4, #20]           @ path |= 4: t_stock skips the stock path on the next sd_init
    ldr r0, =400
CALL_clk: .hword 0xf000, 0xf800         @ 0x26868(400): identification clock again (0x12044048 = 0x120)
    ldr r0, =0x1bd210
CALL_sdinit: .hword 0xf000, 0xf800      @ sd_init: zeroes the ABI words, CMD0, CMD8, ACMD41 HCS=1
    cmp r0, #0
    bne tc_fail
    ldr r0, =0x1bd210
    ldr r0, [r0, #0]
    cmp r0, #1
    beq tc_go                   @ ready: CMD2/3/9/7/ACMD6 again (csd_hook now sees flag 1/2)
tc_fail:                        @ CMD0 / CMD8 / ACMD41 failed on the re-init: card unusable
    ldr r1, =0x2dea80
    movs r0, #0
    str r0, [r1, #0]            @ capacity 0 (lim is already 0: sd_init zeroed it)
    mvns r0, r0
    b tc_out
tc_err:
    movs r3, #0x40              @ path |= 0x40: stock 0xc9b34 failed
    b tc_set
tc_ref:
    movs r3, #0x80
    tst r1, r3
    beq tc_out
tc_set:                         @ path |= 0x80: mismatch latched, common_hook refuses the card
    lsls r3, r3, #16
    orrs r2, r3
    str r2, [r4, #20]
tc_out:
    pop {{r4}}
    pop {{r3}}
    bx r3
.p2align 2
.ltorg
"""

# (site, stock bytes, target symbol or literal bytes, reason)
HOOKS = [
    (0xC9A6C, "f000f818", "sd_init", "H1 init: 0xc9a68 calls sd_init instead of the stock SD path 0xc9aa0 (sd_init calls it first)"),
    (0xC9A86, "f000f855", "common_hook", "H3: 0xc9a68 calls common_hook instead of 0xc9b34 (mismatch -> init -1)"),
    (0xC9BD4, "f000f888", "csd_hook", "H2 CSD: 0xc9b34 calls csd_hook instead of 0xc9ce8 (v2 capacity + cap + mismatch)"),
    (0x508B6, "f000f81f", "rd_guard", "A4 read chunk guard (full chunks)"),
    (0x508DE, "f000f80b", "rd_guard", "A4 read chunk guard (remainder)"),
    (0x50ABE, "f000f81d", "wr_guard", "A5 write chunk guard (full chunks)"),
    (0x50AE6, "f000f809", "wr_guard", "A5 write chunk guard (remainder)"),
    (0x50936, "f7d5fcdb", "swap_guard2", "A8 read chunk presence check: sticky 'card removed' latch (2b: debounced)"),
    (0x50B36, "f7d5fbdb", "swap_guard2", "A8 write chunk presence check: sticky 'card removed' latch (2b: debounced)"),
    (0x57F92, "f7d0fcb0", "ld_sleep", "2b loader wait sleep -> ld_sleep (ldiag sleep count; tail-calls 0x288f6)"),
    (0x58040, "f000f81b", "ld_enter", "2b loader module call -> ld_enter (ldiag bit 10; then bx r3 as 0x5807a)"),
    (0xB3970, "d0002710", "mount_guard", "A6 mount: card volume start+TotSec must fit the card (replaces 'beq; movs r7,#0x10', kept inside)"),
    (0x157134, None, "usb_cap", "USB READ CAPACITY: CCS card reports the real sector count (SDSC: stock value)"),
    (0xB26E8, "f020fc34", "fmt_guard", "A7 format: refuse a card size index >= table count"),
]
RAW_EDITS = [
    (0x5087A, "d201", "e001", "A4 read op: 'bcs 0x50880' -> 'b 0x50880' (lba > cap no longer returns 0 unread; the chunk guard returns -1)"),
    (0x57F90, "200a", "2028", "2b loader wait for tFileTask: 250 x sleep(10) -> 250 x sleep(40) (~2.8 s -> ~10 s; hw64 P2; flash-1 loader byte)"),
]
MARKER = (0x112578, b"Rev024M\0\0", b"240K2\0\0\0\0",
          "firmware tag: MakerNote tag 0x2 format slot 'Rev024M' (flash-1) -> '240K2' (Koma 2 firmware), NUL-padded; "
          "header +0x12 stays 0240")


CAVE2 = 0x575B4                # free island run 0x575b4..0x579dc (between exp4 and exp5 claims)
CAVE2_END = 0x579DC
COPIES = [  # (stock start, end exclusive, copy address): same address mod 4 so pc-relative pools stay valid
    (0x508F8, 0x50A84, 0x575B4),   # read chunk
    (0x50AFC, 0x50C74, 0x57740),   # write chunk
]
# group B (block addresses carried from the start) inside the copies; stock bytes verified before editing
GROUP_B = [
    (0x50954, "0264", "0024", "lsl r4,r4,#9 -> lsl #0 (movs): read address = sector"),
    (0x50984, "34ff", "3401", "+0x200 -> +1 (single-read loop, mode 1)"), (0x5098A, "34ff", "46c0", "nop"),
    (0x5098E, "3402", "46c0", "nop"),
    (0x509C2, "231f025b18e018f1", "231f18e0025b18f1", "r0 = sector + 31 (block 32 after CMD18 x31); r1 = buf + 0x3e00"),
    (0x50A0C, "34ff", "3401", "+0x200 -> +1 (single-read loop, mode 0)"), (0x50A12, "34ff", "46c0", "nop"),
    (0x50A16, "3402", "46c0", "nop"),
    (0x50B62, "024d", "000d", "lsl r5,r1,#9 -> lsl #0 (movs): write address = sector"),
    (0x50B8C, "35ff", "3501", "+0x200 -> +1 (single-write loop, mode 1)"), (0x50B92, "35ff", "46c0", "nop"),
    (0x50B96, "3502", "46c0", "nop"),
    (0x50BC4, "231f025b18e818f1", "231f18e8025b18f1", "r0 = sector + 31; r1 = buf + 0x3e00"),
    (0x50C06, "35ff", "3501", "+0x200 -> +1 (single-write loop, mode 0)"), (0x50C0C, "35ff", "46c0", "nop"),
    (0x50C10, "3502", "46c0", "nop"),
]


BRANCHES = {"b", "bl"} | {"b" + c for c in "eq ne hs lo cs cc mi pl vs vc hi ls ge lt gt le".split()}


def _disasm_lines(base_payload, lo, hi):
    """Thumb instructions of base_payload in [lo, hi) as (addr, 'T', hex, text), absolute targets as 0x%08x."""
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_BIG_ENDIAN)
    out = []
    for i in md.disasm(bytes(base_payload[lo - LOAD:hi - LOAD]), lo):
        ops = i.op_str
        if i.mnemonic in BRANCHES:
            ops = re.sub(r"#0x([0-9a-f]+)", lambda m: f"0x{int(m.group(1), 16):08x}", ops)
        else:
            lit = re.fullmatch(r"(r\d+), \[pc, #(0x[0-9a-f]+|\d+)\]", ops)
            if i.mnemonic == "ldr" and lit:
                ops = f"{lit.group(1)}, 0x{((i.address + 4) & ~3) + int(lit.group(2), 0):08x}"
        out.append((i.address, "T", i.bytes.hex(), f"{i.mnemonic} {ops}"))
    return out


def make_copies(base_payload, swap_guard):
    """Relocated copies of the two chunk functions with group B applied. Returns [(addr, bytes)], notes."""
    out, notes = [], []
    for lo, hi, dst in COPIES:
        assert lo % 4 == dst % 4
        code = bytearray(base_payload[lo - LOAD:hi - LOAD])
        for a, kind, hexb, text in _disasm_lines(base_payload, lo, hi):
            if kind != "T":
                continue
            m = re.search(r"\b(bl|b|b[a-z]{2}|ldr|adr) .*?0x([0-9a-f]{8})", text)
            if text.startswith("bl "):
                tgt = int(re.search(r"0x([0-9a-f]{8})", text).group(1), 16)
                cur = bytes(base_payload[a - LOAD:a - LOAD + 4])
                if a in (0x50936, 0x50B36):
                    assert tgt == 0x262F0
                    tgt, why = swap_guard, "presence check -> swap_guard (A8)"
                else:
                    why = "relocated"
                code[a - lo:a - lo + 4] = thumb_bl(dst + (a - lo), tgt)
                notes.append(dict(site=hex(a), copy=hex(dst + a - lo), target=hex(tgt), why=why))
            elif m and not text.startswith("bl "):
                t = int(m.group(2), 16)
                assert lo <= t < hi, (hex(a), text)      # every other branch / literal / adr stays inside
        for site, before, after, why in GROUP_B:
            if lo <= site < hi:
                b, n = bytes.fromhex(before), bytes.fromhex(after)
                assert bytes(code[site - lo:site - lo + len(b)]) == b, hex(site)
                code[site - lo:site - lo + len(b)] = n
        out.append((dst, bytes(code)))
    assert out[-1][0] + len(out[-1][1]) <= CAVE2_END
    return out, notes


def build_code(cap=None):
    """Cave 1 code + symbols. The cave-2 shims (SRC_X) are in syms['_x'] = (XBASE, code, xsyms); their names are
    also merged into syms. Three passes: cave 1 with placeholder shim targets (layout does not depend on them),
    the shims against cave 1's symbols, cave 1 again with the real shim addresses."""
    src = SRC_T.format(C8_TICKS=C8_TICKS, rdcopy=COPIES[0][2] | 1, wrcopy=COPIES[1][2] | 1)
    shims = ("tstock", "tcmd0", "tcmd8", "ta41", "tr3", "tcommon")
    _, s1 = assemble(src, CAVE, dict(CALLS, **{k: XBASE for k in shims}))
    xcode, xs = assemble(SRC_X.format(flag=s1["flag"]), XBASE,
                         dict(sdpath=0xC9AA0, cmd8=s1["cmd8"], resp3=0x2679C, common=0xC9B34, clk=0x26868,
                              sdinit=s1["sd_init"]))
    assert XBASE + len(xcode) <= CAVE2_END, hex(XBASE + len(xcode))
    code, syms = assemble(src, CAVE, dict(CALLS, tstock=xs["t_stock"], tcmd0=xs["t_cmd0"], tcmd8=xs["t_cmd8"],
                                          ta41=xs["t_a41"], tr3=xs["t_r3"], tcommon=xs["t_common"]))
    assert {k: v for k, v in syms.items() if not k.startswith("CALL_")} == \
        {k: v for k, v in s1.items() if not k.startswith("CALL_")}
    assert CAVE + len(code) <= CAVE_END, hex(CAVE + len(code))
    for k, a in ABI.items():
        assert syms[k] == a, (k, hex(syms[k]))     # ABI words never move (widefs.grmod reads them by address)
    syms.update({k: v for k, v in xs.items() if not k.startswith("CALL_")})
    syms["_x"] = (XBASE, xcode, xs)
    lcode, ls = assemble(SRC_L.format(flag=syms["flag"], ldiag=syms["ldiag"]), LBASE, dict(ob=OPEN_BOTH))
    assert LBASE + len(lcode) <= LBASE_END
    syms.update({k: v for k, v in ls.items() if not k.startswith("CALL_")})
    syms["_l"] = (LBASE, lcode, ls)
    return code, syms


IROM_MOUNTED, IROM_MOUNT = 0x1FB200, 0x49384
# The flash-1 card-module loader (tools/koma/loader.py) occupies 0x57f74..0x580cc; its one `bl 0x55dc0` (open
# "/ATA1/GRMOD.BIN") is at LOADER_OPEN_SITE. The /IROM tail goes in the unused cave after it, up to LOADER_CAVE_END.
LOADER_OPEN_SITE, IROM_TAIL, LOADER_CAVE_END = 0x57FA4, 0x580CC, 0x58198
# /IROM fallback tail v2 (2026-11-07). v1 opened /IROM/GRMOD.BIN
# directly. O-hw: boot mode byte 0x1b9866 = 1 on the spare, and in mode 1 tFileTask 0x55734 mounts /IROM (0x49384(0)
# at 0x5581a) only if SD init fails, so with a working card /IROM is unmounted at the loader and the open fails.
# v2 adds, on the fallback path only (card open returned -1): if byte 0x1fb200 == 0, call 0x49384(0) (the firmware's
# own mount, as playback handler 0xb60bc and irominstall v2, O-hw mount_rc=0 from the loader context). The mount's
# return value is ignored: if it fails, 0x1fb200 stays 0 and the open below returns -1 (gated in 0x55950) -> stock.
# The card-first path (card open succeeded) executes the same instructions as v1: push, bl open, cmp, bne, pop, bx.
# /IROM is left mounted afterwards, as 0xb60bc and irominstall leave it.
IROM_TAIL_SRC = """
open_both:                      @ replaces the loader's single `bl open`; r0 = "/ATA1/GRMOD.BIN", r1 = 0, r4 = -1
    push {lr}
CALL_open: .hword 0xf000, 0xf800
    cmp r0, r4
    bne 1f                      @ card file opened: card wins (no /IROM access at all)
    ldr r0, =0x1fb200           @ /IROM mounted flag (byte)
    ldrb r0, [r0]
    cmp r0, #0
    bne 2f
    movs r0, #0                 @ /IROM not mounted (mode 1 with a working card): firmware mount 0x49384(0)
CALL_mount: .hword 0xf000, 0xf800
2:  adr r0, path_irom           @ try internal memory once
    movs r1, #0
CALL_open2: .hword 0xf000, 0xf800
1:  pop {r3}
    bx r3                       @ r0 = fd or -1; the loader's own `cmp r0, r4; beq done` follows unchanged
.p2align 2
.ltorg
path_irom:
    .asciz "/IROM/GRMOD.BIN"
"""


def edits(base_payload: bytes, cap=None, irom_fallback=True):
    """[(addr, before, after, reason)] against `base_payload` (flash-1 payload). Asserts before-bytes."""
    code, syms = build_code(cap)
    out = [(CAVE, bytes(base_payload[CAVE - LOAD:CAVE - LOAD + len(code)]), code,
            f"SDHC driver cave ({len(code)} B) in dead FUN_0005721c tail; FLAG word at {syms['flag']:#x}")]
    for site, before, sym, why in HOOKS:
        b = bytes.fromhex(before or "48096880")
        assert base_payload[site - LOAD:site - LOAD + 4] == b, hex(site)
        out.append((site, b, thumb_bl(site, syms[sym]), why))
    for site, before, after, why in RAW_EDITS:
        b = bytes.fromhex(before)
        assert base_payload[site - LOAD:site - LOAD + len(b)] == b, hex(site)
        out.append((site, b, bytes.fromhex(after), why))
    copies, notes = make_copies(base_payload, syms["swap_guard2"])
    for (dst, blob), (lo, hi, _) in zip(copies, COPIES):
        out.append((dst, bytes(base_payload[dst - LOAD:dst - LOAD + len(blob)]), blob,
                    f"group B copy of {lo:#x}..{hi:#x} (block addresses; BLs relocated; presence check -> swap_guard)"))
    lb, lcode, _ = syms["_l"]
    out.append((lb, bytes(base_payload[lb - LOAD:lb - LOAD + len(lcode)]), lcode,
                f"2b cave 3 ({len(lcode)} B): loader diag shims (ldiag {ABI['ldiag']:#x}) + debounced swap_guard2"))
    xb, xcode, _ = syms["_x"]
    assert copies[-1][0] + len(copies[-1][1]) == xb
    out.append((xb, bytes(base_payload[xb - LOAD:xb - LOAD + len(xcode)]), xcode,
                f"2b init shims ({len(xcode)} B, cave 2 tail): trace word {ABI['trace']:#x}, HCS=0-quirk re-init, "
                "CCS=0/CSD v2 mismatch re-init"))
    syms["_copy_notes"] = notes
    if irom_fallback:                       # the loader tries /IROM/GRMOD.BIN after /ATA1/GRMOD.BIN
        tail, tsyms = assemble(IROM_TAIL_SRC, IROM_TAIL, dict(open=0x55DC0, mount=IROM_MOUNT, open2=0x55DC0))
        assert IROM_TAIL + len(tail) <= LOADER_CAVE_END
        assert tsyms["open_both"] == IROM_TAIL       # open_both at 0x580cc
        assert tsyms["open_both"] == OPEN_BOTH
        site_after = thumb_bl(LOADER_OPEN_SITE, syms["ld_open"])   # 2b: loader -> ld_open (ldiag bits 8/9) -> open_both
        syms["_irom_tail"] = (tail, tsyms)
        for a, b, n in ((LOADER_OPEN_SITE, thumb_bl(LOADER_OPEN_SITE, 0x55DC0), site_after),
                        (IROM_TAIL, bytes(base_payload[IROM_TAIL - LOAD:IROM_TAIL - LOAD + len(tail)]), tail)):
            assert bytes(base_payload[a - LOAD:a - LOAD + len(b)]) == b, hex(a)
            out.append((a, b, n, "IROM fallback (irom-fallback lane's site edit; tail v2 = sdhc_patch.IROM_TAIL_SRC): "
                                  + ("loader's open -> ld_open (2b diag) -> open_both" if len(n) == 4 else
                                     "open_both v2 (mounts /IROM via 0x49384(0) if 0x1fb200==0, card open failed only)"
                                     " + '/IROM/GRMOD.BIN'")))
    a, b, c, why = MARKER
    assert base_payload[a - LOAD:a - LOAD + len(b)] == b, "marker"
    out.append((a, b, c, why))
    return sorted(out), syms


if __name__ == "__main__":
    code, syms = build_code()
    print(len(code), "bytes", {k: hex(v) for k, v in syms.items() if not k.startswith(("CALL_", "_"))})
    print("\n".join(disasm(code, CAVE)))
    xb, xcode, _ = syms["_x"]
    print(f"cave 2 shims {xb:#x}..{xb + len(xcode):#x} ({len(xcode)} B)")
    print("\n".join(disasm(xcode, xb)))
