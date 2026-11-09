"""widefs: full-capacity FAT32 for SDHC cards (phase 1, 4-32 GB) as Koma features. Offline build input; no hardware.

Two features (tools/koma Feature objects, linked by build_koma04w.py through koma's own linker):
  widefs       resident. Thumb helpers the patched FS calls (BL targets in sites.BL) + the FAT32 formatter, and a
               Check for every patched byte (Koma verifies all of them before anything is written, all-or-nothing).
  widefs_init  transient (freed after install). Runs once in MainCtrl from the Koma installer (step 6, after every
               check passed). Does nothing unless the image is Base 2 (signature) AND an SDHC card is in (Base 2
               FLAG == 2; option always=on skips this test, for proofs). Then: FS semaphore -> IRQ/FIQ off -> write
               every sites.DATA/BL edit -> IRQ on -> remount /IROM (its drive struct still holds byte positions) ->
               fs_wide_ok := 1 (Base 2 lockout lifted) -> mount the card -> release -> drive status bytes and the
               DCF rescan exactly as the stock FORMAT path 0x138b76 does after its own remount.
Why the init writes the FS edits itself instead of Koma DATA records: the edits must be live BEFORE the remount, and
Koma applies its records after every init has run. Koma's verify pass still covers every byte (Checks).
"""
from __future__ import annotations

from ..feature import Check, Feature
from . import sites as S
from .base_abi import ABI

VERSION = "0.1"
HANDLERS = sorted({h for _, _, h, _, _ in S.BL})

RES_SRC = r"""
@ ======================================================================== widefs resident helpers
@ A1 M1: replaces 'ldrh r2,[r0,#0xc]; cmp r2,#0' at 0xb38c6 (r0 = drive+0xa0). Z=1 only for BytsPerSec == 512.
h_bps:
    ldrh r2, [r0, #0xc]
    subs r2, #0xff
    subs r2, #0xff
    cmp r2, #2
    bx lr

@ A1 W3: replaces 'ldr r2,[sp]; add r3,#0x71' at 0xb4782 (b4744). r1 = fatStart (sectors) + FAT byte offset >> 9.
h_b4744:
    ldr r2, [sp, #0]
    lsrs r2, r2, #9
    adds r1, r1, r2
    bx lr

@ A3: replaces bl 0xd2ecc(2, buf+0x1ec, &[0x2dfdfc]) in b4aa0 (FSInfo write-back). Writes the LIVE next-free hint
@ [0x2dfdb4] (clamped to 2..clusters+1) instead of the stale mount-time copy.
fsinfo_hint:
    push {r4, r5, lr}
    movs r4, r2                 @ &dfdfc (drive base + 0x120)
    movs r5, r2
    subs r5, #0x60              @ &dfd9c cluster count
    ldr r5, [r5, #0]
    adds r5, #1
    movs r3, r2
    subs r3, #0x48              @ &dfdb4 live hint
    ldr r3, [r3, #0]
    cmp r3, #2
    blo fh_2
    cmp r3, r5
    bls fh_ok
fh_2:
    movs r3, #2
fh_ok:
    str r3, [r4, #0]
    ldr r3, =0xd2ecd
    bl w_call
    pop {r4, r5}
    pop {r3}
    bx r3

@ A4: replaces bl 0xb1a10 in b8090 (mount). FAT32: refuse FATSz32 > 0xffff (phase 2 limits), then trust FSInfo when
@ 0 < free <= clusters and 2 <= hint <= clusters+1, else a full scan that never takes the stock shortcut.
@ FAT12/16 (SDSC cards, /IROM): the stock scan, unchanged.
mount_count:
    push {r4, r5, r6, r7, lr}
    ldr r4, =0x2e020c
    ldrb r4, [r4, #6]           @ selected drive
    movs r0, #0x94
    lsls r0, r0, #1             @ 0x128
    muls r4, r0, r4
    ldr r0, =0x2dfcdc
    adds r4, r4, r0             @ drive base
    movs r5, r4
    adds r5, #0xc0              @ +0 clusters (dfd9c), +8 FAT type (dfda4), +0x18 live hint (dfdb4)
    ldrh r0, [r5, #4]           @ cluster bytes (u16 0x2dfda0): 0 for 64 KiB clusters (65536 truncated)
    cmp r0, #0
    beq mc_big                  @ refuse the mount cleanly, before any write
    ldrb r0, [r5, #8]
    cmp r0, #2
    bne mc_scan
    movs r6, r4
    adds r6, #0xff
    adds r6, #0x0d              @ base+0x10c: +0 FATSz32 (dfde8), +0x10 FSInfo free, +0x14 FSInfo next-free
    ldr r0, [r6, #0]
    ldr r1, =0xffff
    cmp r0, r1
    bhi mc_big
    ldr r7, [r5, #0]            @ clusters
    ldr r0, [r6, #0x10]
    cmp r0, #0
    beq mc_full
    cmp r0, r7
    bhi mc_full
    ldr r0, [r6, #0x14]
    cmp r0, #2
    blo mc_full
    adds r1, r7, #1
    cmp r0, r1
    bhi mc_full
    ldr r3, =0xb3a7d            @ as b1a10 starts: drive-busy check, then the FAT window at fatStart (b1e2c(0))
    bl w_call
    cmp r0, #0
    bne mc_out
    movs r0, #0
    ldr r3, =0xb1e2d
    bl w_call
    cmp r0, #0
    bne mc_out
    ldr r3, =0xb4bad            @ stock FSInfo adoption: free, used, bad=0, live hint = max(2, hint), free bytes
    bl w_call
    b mc_out
mc_full:
    movs r0, #0
    mvns r0, r0
    str r0, [r6, #0x14]         @ hint := -1: the stock 'scan to the hint, then adopt' shortcut can never fire
    ldr r3, =0xb1a11
    bl w_call
    ldr r1, [r6, #0x14]         @ b1a10 leaves the first free cluster here on FAT32
    cmp r1, #2
    blo mc_h2
    adds r2, r7, #1
    cmp r1, r2
    bls mc_h
mc_h2:
    movs r1, #2
mc_h:
    str r1, [r5, #0x18]         @ live allocation hint
    b mc_out
mc_scan:
    ldr r3, =0xb1a11
    bl w_call
mc_out:
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
mc_big:
    movs r0, #0x10              @ mount error: 64 KiB clusters, or FAT > 65535 sectors (phase 2, A7)
    b mc_out
.ltorg

@ A5: replaces bl 0xb1b78(start) in b45b8 (free-cluster cache fill). Stock never wraps, so free clusters below the
@ hint read as 'full'. On 0x11 this rescans from cluster 2 up to the original start and SKIPS every cluster that is
@ already reserved but not yet linked in the FAT: the valid entries of the drive's free-cluster ring (count/head at
@ drive+0xe0/+0xe2) and the per-handle lists (count at handle+0x120, entries handle+0x128..+0x13c, both handles).
@ Without that check a wrapped scan can hand out a cached cluster twice (cross-link).
alloc_wrap:
    push {r4, r5, r6, r7, lr}
    movs r4, r0                 @ original start
    ldr r3, =0xb1b79
    bl w_call
    cmp r0, #0x11
    bne aw_out
    cmp r4, #2
    bls aw_out
    movs r5, #2                 @ wrapped scan position
aw_next:
    movs r0, r5
    ldr r3, =0xb1b79
    bl w_call
    cmp r0, #0
    bne aw_out                  @ 0x11 (nothing free / gave up) or an I/O error
    ldr r1, =0x2e020c
    ldrb r1, [r1, #7]
    movs r2, #0xb8
    lsls r2, r2, #1
    muls r1, r2, r1
    ldr r2, =0x2dff2c
    adds r7, r1, r2             @ current handle
    adds r7, #0x80
    ldr r6, [r7, #0x18]         @ found cluster (handle+0x98, as b45b8 reads it)
    cmp r6, r4
    bhs aw_full                 @ reached the original start: everything from there on was scanned already
    movs r0, r6
    bl aw_reserved
    cmp r0, #0
    beq aw_out                  @ r0 = 0: a genuinely free, unreserved cluster
    adds r5, r6, #1
    b aw_next
aw_full:
    movs r0, #0x11
aw_out:
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3

@ r0 = cluster -> r0 = 1 if it sits in the drive's free-cluster ring or in a handle's reserved list, else 0.
aw_reserved:
    push {r4, r5, r6, lr}
    ldr r1, =0x2e020c
    ldrb r1, [r1, #6]
    ldr r2, =0x1044
    muls r2, r1, r2
    ldr r3, =0x2e0a70           @ 0x2e022c + 0x844: ring entry 0 of drive 0
    adds r4, r2, r3             @ ring base for this drive
    movs r2, #0x94
    lsls r2, r2, #1
    muls r1, r2, r1
    ldr r2, =0x2dfdbc
    adds r1, r1, r2
    ldrh r5, [r1, #0]           @ valid count
    ldrh r6, [r1, #2]           @ head index
ar_ring:
    cmp r5, #0
    beq ar_handles
    lsls r2, r6, #2
    ldr r2, [r4, r2]
    cmp r2, r0
    beq ar_yes
    adds r6, #1
    lsls r6, r6, #24
    lsrs r6, r6, #24            @ 256-entry ring
    subs r5, #1
    b ar_ring
ar_handles:
    ldr r4, =0x2dff2c + 0x120
    movs r5, #2                 @ both file handles
ar_h:
    ldrh r1, [r4, #0]
    cmp r1, #0
    beq ar_hn
    movs r2, #8
ar_he:
    ldr r3, [r4, r2]            @ handle+0x128 .. +0x13c
    cmp r3, r0
    beq ar_yes
    adds r2, #4
    cmp r2, #0x1c
    bls ar_he
ar_hn:
    adds r4, #0xb8
    adds r4, #0xb8              @ next handle (+0x170)
    subs r5, #1
    bne ar_h
    movs r0, #0
    b ar_out
ar_yes:
    movs r0, #1
ar_out:
    pop {r4, r5, r6}
    pop {r3}
    bx r3
.ltorg

@ A6: replaces bl 0xb7780(drive, out) in 0x56650 (19 callers). out[2] := min(free, 0x7fffffff / clusterBytes),
@ out[4] (free bytes) := min(true free bytes, 0x7fffffff); out[3] stays the exact free count (remaining shots).
vol_info:
    push {r0, r4, lr}
    movs r4, r1
    ldr r3, =0xb7781
    bl w_call
    str r0, [sp, #0]
    ldr r0, [r4, #0x14]         @ cluster bytes (-1 on the no-volume path)
    adds r1, r0, #1
    beq vi_out
    cmp r0, #0
    beq vi_out
    ldr r1, =0x7fffffff
    ldr r3, =0x157c85           @ r0 = r1 / r0
    bl w_call
    ldr r1, [r4, #8]
    cmp r1, r0
    bls vi_1
    str r0, [r4, #8]
vi_1:
    ldr r1, [r4, #0xc]
    cmp r1, r0
    bls vi_out
    ldr r1, =0x7fffffff
    str r1, [r4, #0x10]
vi_out:
    pop {r0, r4}
    pop {r3}
    bx r3

@ A6: 0xe40bc stills. In: r0 = clusterBytes, r1 = free clusters (n), r6 = per-shot bytes (a cluster multiple).
@ Out: r0 = n / (r6 / clusterBytes), exact in 32 bits (stock: n * clusterBytes / r6 wraps above 4 GiB free).
shots_div:
    push {r1, lr}
    movs r1, r6
    ldr r3, =0x157c85
    bl w_call                   @ clusters per shot
    pop {r1}
    cmp r0, #0
    bne sd_1
    movs r0, #1
sd_1:
    ldr r3, =0x157c85
    bl w_call
    pop {r3}
    bx r3

@ A10: replaces bl 0xb25a8(2) in b83a4 (format), after b83a4 selected the drive and set the format flag.
@ Card drive with an SDHC card (Base 2 FLAG == 2): the widefs formatter, then b83a4's own success tail 0xb848e
@ (or its error tail 0xb844a with r0 = code). Everything else: the stock chain, untouched.
fmt_hook:
    ldr r1, =0x2e020c
    ldrb r1, [r1, #6]
    cmp r1, #1
    bne f_stock
    ldr r1, =FLAG
    ldr r1, [r1, #0]
    cmp r1, #2
    beq f_wide
f_stock:
    ldr r3, =0xb25a9
    bx r3
f_wide:
    push {r4, r5, r6, r7, lr}
    bl wide_format
    pop {r4, r5, r6, r7}
    pop {r3}
    cmp r0, #0
    bne f_err
    ldr r3, =0xb848f
    bx r3
f_err:
    ldr r3, =0xb844b
    bx r3

@ ------------------------------------------------------------------------ FAT32 formatter (fmtref.py = reference)
@ P = BU = 8192 (<= 32 GiB) or 32768 (above), S = TS - P, F = (S/64 + 2 + 127) >> 7,
@ R = 32 + ((-(32 + 2F)) & (BU-1)), C = (S - R - 2F) / 64.
@ Writes: zero P .. P+R+2F+64 (reserved, both FATs, root cluster) and sectors 1..32; FAT heads; root label;
@ FSInfo (1, 7) + sector 2 (2, 8); boot sector (0, 6) at P; MBR last. Every write is b2560 op 1 (count in the
@ handle word +0x100, honoured while b83a4's format flag is set; LBA 0 is always 1 sector).
wide_format:
    push {r4, r5, r6, r7, lr}
    ldr r0, =REAL
    ldr r0, [r0, #0]            @ TS: real card sectors (Base 2 'real'), never above the served limit
    ldr r1, =LIMIT
    ldr r1, [r1, #0]
    cmp r0, r1
    bls wf_ts
    movs r0, r1
wf_ts:
    ldr r1, =0x10000000
    cmp r0, r1
    bhi wf_refuse               @ > 128 GiB: phase 2 (A7 limits, untested)
    ldr r7, =wf_v
    ldr r2, =8192               @ P = BU = 4 MiB up to 32 GiB (SDHC)
    ldr r1, =0x4000000
    cmp r0, r1
    bls wf_p
    lsls r2, r2, #2             @ 16 MiB above (SDXC sizes as FAT32)
wf_p:
    str r2, [r7, #28]           @ P (= boundary unit)
    cmp r0, r2
    bls wf_refuse
    subs r0, r0, r2
    str r0, [r7, #0]            @ S
    lsrs r1, r0, #6
    adds r1, #129
    lsrs r1, r1, #7
    str r1, [r7, #4]            @ F
    lsls r2, r1, #1
    adds r2, #32
    rsbs r2, r2, #0
    ldr r3, [r7, #28]
    subs r3, #1
    ands r2, r3
    adds r2, #32
    str r2, [r7, #8]            @ R
    subs r0, r0, r2
    lsls r3, r1, #1
    subs r0, r0, r3
    lsrs r0, r0, #6
    str r0, [r7, #12]           @ C
    ldr r1, =65525
    cmp r0, r1
    blo wf_refuse
    movs r0, #0
    str r0, [r7, #24]           @ sticky write error: after the first failure nothing more is written
    b wf_go
wf_refuse:
    movs r0, #0x10
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
wf_go:
    ldr r0, =0x2e020c
    ldrb r0, [r0, #7]
    ldr r3, =0xd31cd            @ handle's 64 KiB transfer buffer
    bl w_call
    str r0, [r7, #16]           @ buf
    bl wf_clear32k
    @ ---- zero the reserved area, both FATs and the root cluster
    ldr r4, [r7, #28]
    ldr r5, [r7, #8]
    ldr r1, [r7, #4]
    lsls r1, r1, #1
    adds r5, r5, r1
    adds r5, r5, r4
    str r5, [r7, #20]           @ data start = cluster 2
    adds r5, #64
wf_z:
    subs r6, r5, r4
    cmp r6, #64
    bls wf_z1
    movs r6, #64
wf_z1:
    movs r0, r4
    movs r1, r6
    bl wf_wr
    adds r4, r4, r6
    cmp r4, r5
    blo wf_z
    movs r0, #1                 @ sectors 1..32: an old GPT header/entries must not survive next to our MBR
    movs r1, #32
    bl wf_wr
    @ ---- FAT heads (both copies): F8FFFF0F FFFFFF0F FFFFFF0F
    ldr r4, [r7, #16]
    ldr r0, =0x0ffffff8
    movs r1, r4
    bl st32
    ldr r0, =0x0fffffff
    adds r1, r4, #4
    bl st32
    ldr r0, =0x0fffffff
    movs r1, r4
    adds r1, #8
    bl st32
    ldr r0, [r7, #28]
    ldr r1, [r7, #8]
    adds r0, r0, r1
    movs r1, #1
    bl wf_wr
    ldr r0, [r7, #28]
    ldr r1, [r7, #8]
    adds r0, r0, r1
    ldr r1, [r7, #4]
    adds r0, r0, r1
    movs r1, #1
    bl wf_wr
    @ ---- root directory: volume label entry
    bl wf_clear512
    movs r0, r4
    bl wf_label
    movs r0, #8
    strb r0, [r4, #11]
    ldr r0, [r7, #20]
    movs r1, #1
    bl wf_wr
    @ ---- FSInfo at P+1 and P+7
    bl wf_clear512
    ldr r0, =0x41615252
    movs r1, r4
    bl st32
    ldr r0, =0x61417272
    movs r1, r4
    adds r1, #0xff
    adds r1, #0xe5              @ +484
    bl st32
    ldr r0, [r7, #12]
    subs r0, #1                 @ free = C - 1 (root uses cluster 2)
    adds r1, #4
    bl st32
    movs r0, #3                 @ next free
    adds r1, #4
    bl st32
    ldr r0, =0xaa550000
    adds r1, #16                @ +508: 00 00 55 AA
    bl st32
    ldr r0, [r7, #28]
    adds r0, #1
    movs r1, #1
    bl wf_wr
    ldr r0, [r7, #28]
    adds r0, #7
    movs r1, #1
    bl wf_wr
    @ ---- boot sector 2 (P+2, P+8): 55 AA only
    bl wf_clear512
    bl wf_sig
    ldr r0, [r7, #28]
    adds r0, #2
    movs r1, #1
    bl wf_wr
    ldr r0, [r7, #28]
    adds r0, #8
    movs r1, #1
    bl wf_wr
    @ ---- boot sector (P, P+6)
    ldr r1, =wf_bpb             @ 90-byte template
    movs r2, #0
wf_t:
    ldrb r0, [r1, r2]
    strb r0, [r4, r2]
    adds r2, #1
    cmp r2, #90
    blo wf_t
    ldr r0, [r7, #8]            @ R
    movs r1, r4
    adds r1, #14
    bl st16
    ldr r0, [r7, #28]
    movs r1, r4
    adds r1, #28
    bl st32
    ldr r0, [r7, #0]            @ S
    adds r1, #4
    bl st32
    ldr r0, [r7, #4]            @ F
    adds r1, #4
    bl st32
    ldr r0, =0x2358dc           @ volume id: tick counter
    ldr r0, [r0, #0]
    adds r1, #31                @ +67
    bl st32
    adds r0, r4, #7
    adds r0, #64                @ +71: label (adds rd,rn,#imm3 then #imm8)
    bl wf_label
    bl wf_sig
    ldr r0, [r7, #28]
    movs r1, #1
    bl wf_wr
    ldr r0, [r7, #28]
    adds r0, #6
    movs r1, #1
    bl wf_wr
    @ ---- MBR (last): one entry, type 0C, LBA-only CHS
    bl wf_clear512
    movs r1, r4
    adds r1, #0xff
    adds r1, #0xc0              @ +446 + 1 = CHS start
    ldr r0, =0x00feffff
    bl st32be3
    movs r0, #0x0c
    strb r0, [r1, #3]           @ +450 type
    adds r1, #4
    ldr r0, =0x00feffff
    bl st32be3                  @ +451 CHS end
    adds r1, #3                 @ +454
    ldr r0, [r7, #28]
    bl st32
    ldr r0, [r7, #0]
    adds r1, #4                 @ +458
    bl st32
    bl wf_sig
    movs r0, #0
    movs r1, #1
    bl wf_wr
    ldr r0, [r7, #24]           @ 0, or the first failing write's status
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3

@ write r1 sectors from the buffer to LBA r0 (b2560 op 1, rw 2); returns b2560's status
wf_wr:
    push {r4, r5, lr}
    sub sp, #4
    movs r4, r0
    ldr r2, =wf_v
    ldr r2, [r2, #24]
    cmp r2, #0
    bne wf_w_out
    ldr r2, =0x2e020c
    ldrb r2, [r2, #7]
    movs r3, #0xb8
    lsls r3, r3, #1             @ 0x170 per handle
    muls r2, r3, r2
    ldr r3, =0x2e002c           @ handle 0 + 0x100: op 1 sector count
    adds r2, r2, r3
    str r1, [r2, #0]
    ldr r2, =wf_v
    ldr r2, [r2, #16]
    str r2, [sp, #0]
    movs r3, r4
    movs r0, #1
    movs r1, #2
    movs r2, #0xa5
    ldr r5, =0xb2561
    bl w_call_r5
    ldr r5, =wf_v
    str r0, [r5, #24]
wf_w_out:
    add sp, #4
    pop {r4, r5}
    pop {r3}
    bx r3

wf_clear32k:
    movs r1, #1
    lsls r1, r1, #15
    b wf_clr
wf_clear512:
    movs r1, #1
    lsls r1, r1, #9
wf_clr:
    push {lr}
    ldr r2, =wf_v
    ldr r2, [r2, #16]
    movs r0, #0
    ldr r3, =0xd2e49            @ memset(value, length, dest)
    bl w_call
    pop {r3}
    bx r3

wf_sig:                         @ 55 AA at +510 of the buffer (r4)
    movs r0, #0x55
    movs r1, r4
    adds r1, #0xff
    adds r1, #0xff
    strb r0, [r1, #0]
    movs r0, #0xaa
    strb r0, [r1, #1]
    bx lr

wf_label:                       @ r0 = destination: the 11-byte card label b83a4 put at drive 1 base + 0xe
    ldr r1, =0x2dfe12
    movs r2, #0
wf_l:
    ldrb r3, [r1, r2]
    strb r3, [r0, r2]
    adds r2, #1
    cmp r2, #11
    blo wf_l
    bx lr

st32:                           @ little-endian u32 r0 at [r1] (byte stores; r1 kept)
    strb r0, [r1, #0]
    lsrs r2, r0, #8
    strb r2, [r1, #1]
    lsrs r2, r0, #16
    strb r2, [r1, #2]
    lsrs r2, r0, #24
    strb r2, [r1, #3]
    bx lr
st16:
    strb r0, [r1, #0]
    lsrs r2, r0, #8
    strb r2, [r1, #1]
    bx lr
st32be3:                        @ 3 bytes of r0 (bits 23..0) big-endian order at [r1]: CHS FE FF FF
    lsrs r2, r0, #16
    strb r2, [r1, #0]
    lsrs r2, r0, #8
    strb r2, [r1, #1]
    strb r0, [r1, #2]
    bx lr

w_call:
    bx r3
w_call_r5:
    bx r5
.ltorg
.p2align 2
wf_bpb:                         @ jmp, OEM, BPB fixed fields (variable ones patched), EBPB, 'FAT32   '
    .byte 0xeb, 0x58, 0x90
    .ascii "MSWIN4.1"
    .byte 0x00, 0x02, 0x40, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0xf8, 0x00, 0x00
    .byte 0x3f, 0x00, 0xff, 0x00
    .byte 0, 0, 0, 0,  0, 0, 0, 0,  0, 0, 0, 0
    .byte 0x00, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x00, 0x06, 0x00
    .byte 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
    .byte 0x80, 0x00, 0x29
    .byte 0, 0, 0, 0
    .ascii "NO NAME    "
    .ascii "FAT32   "
.p2align 2
wf_v:
    .word 0, 0, 0, 0, 0, 0, 0, 0   @ S, F, R, C, buf, data start, sticky write error, P
"""

INIT_SRC = r"""
@ ======================================================================== widefs_init (transient)
@ r0 = service table, r1 = body base. Inert unless Base 2 + SDHC card (or always=on). Returns nothing useful.
wi_init:
    push {r4, r5, r6, r7, lr}
    movs r7, r0
    ldr r4, =wi_sig             @ Base 2 signature: {u32 addr, u32 len, bytes[pad4]}..., u32 0
wi_s:
    ldr r5, [r4, #0]
    cmp r5, #0
    beq wi_s_ok
    ldr r6, [r4, #4]
    adds r4, #8
    movs r2, #0
wi_sc:
    ldrb r0, [r5, r2]
    ldrb r1, [r4, r2]
    cmp r0, r1
    bne wi_out0
    adds r2, #1
    cmp r2, r6
    blo wi_sc
    adds r6, #3
    lsrs r6, r6, #2
    lsls r6, r6, #2
    adds r4, r4, r6
    b wi_s
wi_out0:
    b wi_out
wi_s_ok:
    ldr r0, =FS_WIDE_OK
    ldr r0, [r0, #0]
    cmp r0, #0
    bne wi_out0                 @ already live (a second install): nothing to do
    ldr r0, =FLAG
    ldr r0, [r0, #0]
    cmp r0, #2
    beq wi_go
    ldr r0, =ALWAYS
    cmp r0, #0
    beq wi_out0                 @ no SDHC card: stock FS untouched
wi_go:
    ldr r0, =0x2bce14
    ldr r0, [r0, #0]            @ open files (max 2): an open handle would keep byte positions -> stay inert
    cmp r0, #0
    bne wi_out0
    @ ---- range pass: every BL handler within +-4 MB of its site (pool address is only known now)
    ldr r4, =wi_tab
wi_r:
    ldr r5, [r4, #0]
    cmp r5, #0
    beq wi_r_ok
    ldrh r6, [r4, #4]
    ldrh r0, [r4, #6]
    adds r4, #8
    cmp r0, #1
    bne wi_r_d
    ldr r0, [r4, #0]
    adds r1, r5, #4
    subs r0, r0, r1
    ldr r1, =0x400000
    adds r0, r0, r1
    lsrs r0, r0, #23
    bne wi_out0
    adds r4, #4
    b wi_r
wi_r_d:
    adds r6, #3
    lsrs r6, r6, #2
    lsls r6, r6, #2
    adds r4, r4, r6
    b wi_r
wi_r_ok:
    @ ---- FS semaphore (0x55950's): no task is inside the FAT layer while positions change unit
    ldr r0, =0x2bce14
    ldr r0, [r0, #4]
    movs r1, #0
    mvns r1, r1
    ldr r3, =0x5d979
    bl wi_call
    ldr r3, [r7, #0x28]         @ irq_set (ARM)
    movs r0, #0xc0
    bl wi_call
    str r0, [sp, #-4]!          @ placeholder, replaced below
"""

# Thumb-1 has no pre-indexed store; keep the old mask in a transient word instead (assembled below).
INIT_SRC = INIT_SRC.replace("    str r0, [sp, #-4]!          @ placeholder, replaced below\n",
                            "    ldr r1, =wi_mask\n    str r0, [r1, #0]\n")

INIT_SRC += r"""
    ldr r4, =wi_tab
wi_a:
    ldr r5, [r4, #0]
    cmp r5, #0
    beq wi_a_done
    ldrh r6, [r4, #4]
    ldrh r0, [r4, #6]
    adds r4, #8
    cmp r0, #1
    beq wi_a_bl
    movs r2, #0
wi_ad:
    ldrb r0, [r4, r2]
    strb r0, [r5, r2]
    adds r2, #1
    cmp r2, r6
    blo wi_ad
    adds r6, #3
    lsrs r6, r6, #2
    lsls r6, r6, #2
    adds r4, r4, r6
    b wi_a
wi_a_bl:
    ldr r0, [r4, #0]
    adds r1, r5, #4
    subs r0, r0, r1             @ displacement
    asrs r1, r0, #12
    ldr r2, =0x7ff
    ands r1, r2
    ldr r3, =0xf000
    orrs r1, r3
    strh r1, [r5, #0]
    asrs r1, r0, #1
    ands r1, r2
    ldr r3, =0xf800
    orrs r1, r3
    strh r1, [r5, #2]
    adds r4, #4
    b wi_a
wi_a_done:
    ldr r1, =wi_mask
    ldr r0, [r1, #0]
    ldr r3, [r7, #0x28]
    bl wi_call                  @ IRQ/FIQ mask restored
    @ ---- remount /IROM under the semaphore (b8090 = 0x55950's body): every field recomputed in sectors
    ldr r0, =s_irom
    movs r1, #5
    ldr r3, =0xb8091
    bl wi_call
    movs r5, r0
    @ ---- lift the Base 2 lockout, mount the card
    ldr r0, =FS_WIDE_OK
    movs r1, #1
    str r1, [r0, #0]
    ldr r0, =s_ata1
    movs r1, #5
    ldr r3, =0xb8091
    bl wi_call
    movs r4, r0
    ldr r0, =0x2bce14
    ldr r0, [r0, #4]
    ldr r3, =0x5d9a5
    bl wi_call                  @ semaphore released
    @ ---- status bytes, as 0x55950 / 0x49384 set them
    cmp r5, #0
    beq wi_i_ok
    ldr r0, =0x1fb200
    movs r1, #0
    strb r1, [r0, #0]
    movs r0, #0
    movs r1, #3
    movs r2, #2
    ldr r3, =0x491dd
    bl wi_call
wi_i_ok:
    cmp r4, #0
    bne wi_c_fail
    ldr r0, =0x2bce20
    movs r1, #1
    str r1, [r0, #0]
    ldr r0, =0x1b9915           @ drive 1 status := 0 directly: 0x491dc refuses any change while it is 2 (0x49278),
    movs r1, #0                 @ and 2 is what the boot's refused mount left. 0 = what a stock mount sets.
    strb r1, [r0, #0]
    movs r0, #1
    movs r1, #1
    movs r2, #2
    ldr r3, =0x491dd
    bl wi_call
    @ ---- DCF state, as 0x138b76 after its remount
    ldr r3, =0x49637
    bl wi_call
    cmp r0, #1
    bne wi_d_other
    ldr r3, =0x4ab1d
    bl wi_call
    cmp r0, #0
    bne wi_log
    movs r0, #1
    movs r1, #0
    ldr r3, =0x4765d
    bl wi_call
    cmp r0, #0
    bne wi_log
    movs r0, #0
    ldr r3, =0x47b4f
    bl wi_call
    b wi_log
wi_d_other:
    movs r0, #1
    movs r1, #1
    ldr r3, =0x4765d
    bl wi_call
    cmp r0, #0
    bne wi_log
    movs r0, #1
    ldr r3, =0x47b4f
    bl wi_call
    b wi_log
wi_log:
    movs r0, r5
    bl wi_writelog
    b wi_out
wi_c_fail:
    movs r0, #1
    movs r1, #3
    movs r2, #2
    ldr r3, =0x491dd
    bl wi_call
wi_out:
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
wi_call:
    bx r3

@ /ATA1/WIDEFS.TXT (blind check on a camera without a screen): written once per boot, only after the card mounted.
@ r0 = /IROM remount result. Text: header, then 'NAME  =XXXXXXXX' lines (hex), 'END'.
wi_writelog:
    push {r4, r5, r6, lr}
    ldr r1, =wi_iromr
    str r0, [r1, #0]
    ldr r4, =wl_addr
    ldr r5, =wl_text + 16 + 7
    movs r6, #13
wl_f:
    ldr r0, [r4, #0]
    ldr r0, [r0, #0]
    movs r1, r5
    bl wl_hex8
    adds r4, #4
    adds r5, #16
    subs r6, #1
    bne wl_f
    ldr r0, =wl_path
    ldr r3, =0x55d81            @ remove the previous record (result ignored)
    bl wi_call
    ldr r0, =wl_path
    movs r1, #0x80
    lsls r1, r1, #2             @ 0x200: create
    ldr r3, =0x55dc1
    bl wi_call
    movs r4, r0
    lsrs r0, r0, #31
    bne wl_out
    movs r0, r4
    ldr r1, =wl_text
    ldr r2, =228
    ldr r3, =0x560ed
    bl wi_call
    movs r0, r4
    ldr r3, =0x55f0d
    bl wi_call
wl_out:
    pop {r4, r5, r6}
    pop {r3}
    bx r3
wl_hex8:                        @ r0 value -> 8 hex digits at r1 (clobbers r0-r3)
    movs r2, #28
wl_h1:
    movs r3, r0
    lsrs r3, r2
    lsls r3, r3, #28
    lsrs r3, r3, #28
    cmp r3, #10
    blo wl_h2
    adds r3, #7
wl_h2:
    adds r3, #48
    strb r3, [r1, #0]
    adds r1, #1
    subs r2, #4
    bpl wl_h1
    bx lr
.ltorg
.p2align 2
wi_iromr:
    .word 0
wl_addr:
    .word wi_iromr
    .word FLAGW
    .word REALW
    .word 0x2dfec4
    .word 0x2dfef0
    .word 0x2dfec8
    .word 0x2dff10
    .word 0x2dfea0
    .word 0x2dfeac
    .word 0x2dfedc
    .word 0x2dff20
    .word 0x2dff24
    .word 0x2358dc
wl_path:
    .asciz "/ATA1/WIDEFS.TXT"
.p2align 2
wl_text:
    .ascii "KOMA WIDEFS V02\nIROMR =--------\nFLAG  =--------\nREAL  =--------\nCLUS  =--------\nFREE  =--------\nCBYTKB=--------\nFATSZ =--------\nPART  =--------\nDATA  =--------\nHINT  =--------\nFSFREE=--------\nFSNEXT=--------\nTICK  =--------\nEND\n"
.p2align 2
s_irom:
    .asciz "/IROM"
.p2align 2
s_ata1:
    .asciz "/ATA1"
.p2align 2
wi_mask:
    .word 0
"""


def _bytes_dir(b: bytes) -> str:
    b = b + bytes((-len(b)) % 4)
    return "    .byte " + ", ".join(f"0x{x:02x}" for x in b)


def tables():
    """Signature + patch table (transient data)."""
    sig = ["wi_sig:"]
    for addr, bts in ABI["signature"]:
        sig.append(f"    .word {addr:#x}, {len(bts)}")
        sig.append(_bytes_dir(bts))
    sig.append("    .word 0")
    tab = ["wi_tab:"]
    for addr, before, after, _, why in S.data_rows():
        tab.append(f"    .word {addr:#x}            @ {why[:70]}")
        tab.append(f"    .hword {len(after)}, 0")
        tab.append(_bytes_dir(after))
    for site, before, handler, _, why in S.bl_rows():
        tab.append(f"    .word {site:#x}            @ BL -> {handler}: {why[:60]}")
        tab.append("    .hword 4, 1")
        tab.append(f"    .word $widefs.{handler}")
    tab.append("    .word 0")
    return "\n".join([".p2align 2"] + sig + [".p2align 2"] + tab) + "\n"


def make(opts=None):
    opts = dict(opts or {})
    always = str(opts.get("always", "off")).lower() in ("on", "1", "yes", "true")
    log = str(opts.get("log", "on")).lower() in ("on", "1", "yes", "true")
    res = RES_SRC.replace("=REAL", f"={ABI['flag'] + 8:#x}").replace("=FLAG", f"={ABI['flag']:#x}").replace("=LIMIT", f"={ABI['limit']:#x}")
    checks = [Check(a, b, f"widefs {g}: {w}") for a, b, _, g, w in S.data_rows()]
    checks += [Check(a, b, f"widefs {g} BL site: {w}") for a, b, _, g, w in S.bl_rows()]
    resident = Feature(name="widefs", version=VERSION,
                       summary="FAT32 at full SDHC capacity: sector FS, mount/free/alloc fixes, SD-spec formatter",
                       source=res, exports=HANDLERS, checks=checks, options=opts)
    init_src = (INIT_SRC.replace("=FS_WIDE_OK", f"={ABI['fs_wide_ok']:#x}").replace("=FLAG", f"={ABI['flag']:#x}")
                .replace("FLAGW", f"{ABI['flag']:#x}").replace("REALW", f"{ABI['flag'] + 8:#x}")
                .replace("=ALWAYS", f"={1 if always else 0}")) + tables()
    if not log:                                   # proof variant: no WIDEFS.TXT, so card writes can be compared
        init_src = init_src.replace("    bl wi_writelog\n", "")
    init = Feature(name="widefs_init", version=VERSION,
                   summary="widefs install-time apply + remount (transient)", source=init_src, init="wi_init",
                   transient=True, requires=["widefs"], options=opts)
    return [resident, init]
