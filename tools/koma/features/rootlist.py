"""rootlist: list the root of a FAT32 card (fixes the USB `-NLST /EXT0` hang on 64 GB cards).

Cause (STATIC on 2.40): the directory reader 0xb7954 (readdir 0x5644c, used
by `-NLST` 0xcc970) bounds a depth-0 (root) handle by the BPB RootEntCnt `ldrh [drive+0xb2]` at two places and has no
FAT32 branch. On FAT32 RootEntCnt is 0, so the end test at 0xb7992..0xb799c (`if RootEntCnt <= dffcc*16 return 0`)
returns NULL on the first call: the listing is empty, `-NLST` answers 150 with zero data, and the USB state machine
waits in state 6 for a "data sent" event that never comes (the hang). Subdirectories use the cluster bound
(`ldrh [drive+0xc4] >> 5` at 0xb79e0..0xb79e4) and work; the root walker underneath (0xb58d4(1) -> 0xb21ac when
[drive+0xc8] == 2) steps a FAT32 root cluster sector by sector, but from a base nobody sets (second gap below).

Fix: two exclusive BL redirects replace the RootEntCnt load with a FAT-type-aware bound, and on FAT32 seed dffa0 (below):

  0xb7992  32a0 8a52  adds r2,#0xa0 ; ldrh r2,[r2,#0x12]   (r2 = drive base)  -> bl rl_bound
  0xb79ca  30a0 8a42  adds r0,#0xa0 ; ldrh r2,[r0,#0x12]   (r0 = drive base)  -> bl rl_bound_r0

  FAT type [drive+0xc8] == 2 (FAT32): r2 = cluster bytes [drive+0xc4] >> 5 (1,024 entries at 32 KiB), the bound the
  stock code already uses for subdirectories. Otherwise (FAT12/16: /IROM, SDSC cards): r2 = RootEntCnt, the stock
  value, so behaviour there is unchanged.

Second gap, found in the emulator: on a FAT32 root the
walk past the first sector read LBA 1, 2, 3... The opendir root init 0xb58d4(0) sets only stream A's sector
dffb0 := rootStart [drive+0xa4] and dffcc := 1 (decompiled_000.c:162036-162037); the FAT32 stepper 0xb21ac then sets
dffcc += 1, dffa0 += 1 sector (widefs S3) and dffb0 := dffa0 (:159528-159531, :159567). dffa0 (handle+0x74) is
never set for a root handle (0 in the emulator), so the second sector came from LBA 1. On FAT12/16 the stepper is
dffb0 += 1 sector directly and needs no dffa0. So on FAT32 the end-test helper also stores dffa0 := dffb0 whenever
dffcc == 1: the reader is then in the first sector of the root cluster (the bound stops it before the cluster ends,
so it never crosses into another cluster), where dffb0 is the root start. Idempotent; units match because both
words are positions of the same stream (sectors under widefs, bytes on stock).

Register contract (STATIC, disasm 0xb7954..0xb79e8): the helpers return r2 and preserve r0, r1, r3-r7 (r0 and r3 are
saved on the stack with lr; return is `pop {r0, r3, pc}`). At site 1 they read r4 = 0x2e020c (0xb796c) and r7 = 0x2dff2c (0xb7974), both
unchanged up to the site. They clobber the flags, which neither site reads before setting them again (0xb7996 `cmp`
after site 1; site 2 falls to `b 0xb79e6` -> str/ldrb/ldr, then `lsls` at 0xb79ec sets them). At site 2 the stock
code also left r0 = drive+0xa0; r0 is overwritten at 0xb79e8 (`ldrb r0,[r4,#7]`) before any read, so leaving it
as the drive base is equivalent. lr is free inside 0xb7954 (pushed at 0xb7954, returned through the stack).

Known limits (INFERRED from STATIC): on FAT32 the bound is per cluster, and dffcc is the sector within the root
cluster (0xb21ac), so a root listing covers the FIRST root cluster (1,024 slots at 32 KiB incl. long-name slots); it
stops at the end-marker entry or with 0x33 at the bound, so it always terminates. Do not use 0xffff: at the end of a
chain 0xb21ac returns 0x33 without reloading the buffer and the reader would repeat entries. The entry test's `<=`
hides the last sector of the first cluster (16 slots) at a call boundary. A camera root holds about 10 entries.

Not covered: an empty listing (zero records, e.g. a FAT32 root holding only a volume label) still hangs `-NLST`
(state 6, 0xcb9cc); that is a separate fix. Early-site rule: neither site is in the phase-A line set (linker lint) and
both are on the depth-0 path only; the installer writes them with IRQ/FIQ masked like every Koma record.
"""
from __future__ import annotations

from ..feature import Check, Feature, Redirect
from .. import firmware as fw

NAME = "rootlist"
VERSION = "1.0"
SUMMARY = "FAT32 card root lists over USB (-NLST /EXT0): 0xb7954 root bound = cluster entries, not RootEntCnt 0"
OPTIONS: dict = {}

READER = 0xB7954
SITE_END_TEST, BEFORE_END_TEST = 0xB7992, bytes.fromhex("32a08a52")
SITE_BOUND, BEFORE_BOUND = 0xB79CA, bytes.fromhex("30a08a42")
# stock instructions around both sites that the helper's register contract relies on (never written)
CONTEXT = [(0xB796C, 0xB7992, "0xb7954 r4 = 0x2e020c, r7 = 0x2dff2c, depth test, r1 = dffcc*16, r2 = drive base"),
           (0xB7BB0, 0xB7BBC, "0xb7954 literals 0x2e020c (r4), 0x2dff2c (r7), 0x2dfcdc (drive table)"),
           (0xB7996, 0xB79CA, "0xb7954 cmp r1,r2 after site 1, depth>0 path, bl 0xb5bc0, r0 = drive base"),
           (0xB79CE, 0xB79EE, "0xb7954 b 0xb79e6 after site 2, str r2,[sp,#8], r0 overwritten at 0xb79e8")]
DRIVE_FAT_TYPE, DRIVE_CBYTES, DRIVE_ROOTENT = 0xC8, 0xC4, 0xB2      # offsets from the drive base 0x2dfcdc + n*0x128
H_DFFA0, H_DFFB0, H_DFFCC = 0x74, 0x84, 0xA0                        # offsets from the handle base 0x2dff2c + n*0x170

SRC = """
@ Both handlers: entered by the BL that replaced the 4 stock bytes; return r2 = root entry bound; r0, r1, r3-r7 kept.
@ rl_bound_r0: site 0xb79ca (stock: adds r0,#0xa0; ldrh r2,[r0,#0x12]); r0 = drive base. Bound only.
rl_bound_r0:
    push {r0, r3, lr}
    movs r2, r0
    adds r2, #0xa0
    b rl_pick
@ rl_bound: site 0xb7992 (stock: adds r2,#0xa0; ldrh r2,[r2,#0x12]), once per readdir call on the depth-0 path.
@ In: r2 = drive base, r4 = 0x2e020c (0xb796c), r7 = 0x2dff2c handle table (0xb7974), r0 = depth 0, r1 = dffcc*16.
@ FAT32 only: seed the root stream's sector position dffa0 := dffb0 while dffcc == 1 (see the module docstring).
rl_bound:
    push {r0, r3, lr}
    adds r2, #0xa0              @ r2 = drive+0xa0, as the stock instruction
    movs r3, #0x28
    ldrb r3, [r2, r3]           @ drive+0xc8: FAT type (2 = FAT32)
    cmp r3, #2
    bne rl_pick
    ldrb r0, [r4, #7]           @ [0x2e0213] handle index
    movs r3, #0xb8
    lsls r3, r3, #1             @ 0x170 handle size
    muls r0, r3, r0
    adds r0, r0, r7             @ handle base
    adds r0, #0x74              @ &dffa0 (+0x10 dffb0, +0x2c dffcc)
    ldr r3, [r0, #0x2c]
    cmp r3, #1
    bne rl_pick                 @ only in the first sector of the root cluster: dffb0 = root start there
    ldr r3, [r0, #0x10]
    str r3, [r0, #0]            @ dffa0 := dffb0, the base 0xb21ac steps from
rl_pick:
    movs r3, #0x28
    ldrb r3, [r2, r3]           @ FAT type
    cmp r3, #2
    bne rl_stock
    ldrh r2, [r2, #0x24]        @ drive+0xc4: cluster bytes (u16)
    lsrs r2, r2, #5             @ entries per cluster, the subdirectory bound (0xb79e2..0xb79e4)
    pop {r0, r3, pc}            @ Thumb to Thumb (as waitdiag); pop leaves the flags alone
rl_stock:
    ldrh r2, [r2, #0x12]        @ drive+0xb2: BPB RootEntCnt, the stock value
    pop {r0, r3, pc}
"""


def make(opts):
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options=opts, source=SRC,
        redirects=[Redirect(SITE_END_TEST, BEFORE_END_TEST, "rl_bound",
                            "0xb7954 root end test: RootEntCnt -> FAT-type-aware bound"),
                   Redirect(SITE_BOUND, BEFORE_BOUND, "rl_bound_r0",
                            "0xb7954 root entries bound [sp+8]: RootEntCnt -> FAT-type-aware bound")],
        checks=[Check(a, fw.ram(a, b - a), w) for a, b, w in CONTEXT])
