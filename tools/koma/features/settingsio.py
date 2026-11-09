"""settingsio: boot-time import of /ATA1/PARAM.DAT and export of /ATA1/PARAM.CUR.

Camera side of the settings-externalization design. Transient (init only, freed after install: 0 resident bytes). The
init runs once per boot from the installer (installer step 6: after the loader hook 0x281ee, after the stock block
0x1b000..0x1bfff is loaded and after the core Koma settings init). Place it late in the build (just before telemetry)
so the other features' inits have run before the export.

  1. Import. If /ATA1/PARAM.DAT opens (service-table open/read/close, the loader's own proven calls) and is exactly
     4096 bytes (64 reads of 64 bytes, then a 1-byte read must return 0, as the loader checks GRMOD.BIN), it is
     validated in a first pass: magic MG1H at 0, checksum (sum of big-endian u16 halves over 0x000..0xffb == the
     big-endian u32 at 0xffc), Koma header (if 'KOMA' is at 0x8b0, the version byte 0x8b4 must equal the running Koma
     settings version 1; a file without a KOMA header is accepted). The firmware tag is compared but never refuses
     (2026-11-07, SDHC session): if 'KSIO' is at 0x8d0 and its 7-byte tag 0x8d5..0x8db differs from the running body's
     tag at 0x112578, the import proceeds and the status is 3 (OK, tag differed). The two bodies run the same Koma
     module on different firmware tags (main Rev024M, spare 240K2), and the block layout is governed by the Koma
     settings version (0x8b4) and the KSIO version (0x8d4), not the tag. A second pass re-reads the file and copies it over
     0x1b000..0x1bffb, byte by byte, skipping every byte inside the sorted keep table (the map's exclude_on_merge
     plus this feature's per-body bytes, see EXCLUDE_RANGES and range_table()). A file without a KOMA header copies only 0x000..0x7ff: the running Koma area
     (rows, recipe store) is kept, so a stock body's block cannot wipe Koma's settings. After the copy every Koma row
     byte (0x1b8b5..0x1b8cf) is clamped as the core init koma_settings_init does at boot (byte >= option count ->
     default), from a table built at link time from the build's rows (late_source), so an out-of-range row byte never
     sits in RAM for a session. A short read in pass 2 (the card changed between passes) is BAD READ: the bytes copied
     so far stay applied (no 4 KB undo buffer), they are clamped, and the file is consumed with status 2.
     The file is read in 64-byte chunks into a buffer on the init's stack frame (92 B frame): no pool allocation, no
     4 KB buffer, and no body space (the emulated pool has no slack for a bigger transient body; README section 6).
  2. One-shot. No rename or delete routine is proven on hardware, so the one-shot rests on the marker fallback of the
     design doc: PARAM.DAT is re-created with the 4 bytes "DONE" (create/write/close, hardware-proven by telemetry; the
     next boot fails the size check). Then PARAM.DAT is removed with the firmware's delete 0x55d80 (r0 = path; STATIC:
     the CONFIG rotation 0x653c0 calls it that way, and the GX8/R2 PARAM.DAT helpers save with remove + create + write
     + close, never rename). If the remove does nothing on hardware, the DONE file stays
     and the one-shot still holds. No rename is used. Then /ATA1/PARAM.OK ("OK\\r\\n") or /ATA1/PARAM.BAD (one ASCII
     reason line) is created. A wrong-size file is ignored
     silently (that is also how the DONE marker is skipped). A 4096-byte file starting with "DONE" is ignored too, in
     case create does not truncate on hardware (truncation is not hardware-proven: the telemetry log is fixed length).
     Telemetry has no log hook, so nothing is added to KOMALOG.TXT; the KSIO status byte records the last import.
  3. KSIO sub-header at 0x1b8d0 (every boot): 'KSIO', settings-io version 1, 7-byte firmware tag copied from the
     image's MakerNote version format at 0x112578 ('Rev024M' on the flash-1 loader image), last-import status 0x1b8dc
     (0 none, 1 ok, 2 bad, 3 ok with a differing firmware tag; set to 0 the first time the header is written, else
     kept until the next import attempt). Status 3 writes PARAM.OK like status 1.
  4. Export. /ATA1/PARAM.CUR = the 4096 bytes at 0x1b000 as they are in RAM (checksum bytes as currently stored; on a
     boot that imported, 0x1bffc still holds the pre-import checksum until the power-off save recomputes it).

Persistence is the stock power-off save (0x4949c, which stock calls; Koma never does). This feature calls no flash or
store-write routine. Lint exception: the import writes stock settings bytes with whatever values the file holds, which
breaks the rule "a stock settings byte may only receive a value stock also writes". It is declared as
Settings(bulk=True), which linker.BULK_SETTINGS_WRITERS allows for this feature only. The envelope checks above and the
host tool's byte-map validation (tools/komaset) are the guard; stock validators (0x3d4a4) still coerce at the next
load (SETTINGS-EXTERNAL decision 8).

The KOMA-page rows (MENU LAYOUT, KOMA FEATURES, SAVE TO CARD) are in the companion feature settingsio-menu, because a
transient feature cannot carry rows or resident code (linker rule).
"""
from __future__ import annotations

from .. import firmware as fw
from ..feature import Check, Feature, Settings

NAME = "settingsio"
SUMMARY = "boot import of /ATA1/PARAM.DAT (validated, one-shot) + export of /ATA1/PARAM.CUR every boot"
OPTIONS = {}
VERSION = "1.2"

BASE, SIZE = 0x1B000, 0x1000
CK_OFF = 0xFFC
CHUNK = 64                      # read size; the buffer is on the init's stack frame
BUF = 28                        # sp+24 tag-mismatch flag, sp+28 the read buffer
FRAME = BUF + CHUNK
KOMA_OFF, KOMA_VER_OFF, KOMA_VERSION = 0x8B0, 0x8B4, 1        # linker.SET_MAGIC / SET_VERSION_ADDR / SET_VERSION
NOKOMA_LIMIT = 0x800            # file without a KOMA header: copy only the stock area 0x000..0x7ff
KSIO = 0x1B8D0                  # sub-header: +0 'KSIO', +4 version, +5..+11 firmware tag, +12 last-import status
KSIO_END = 0x1B900              # 0x1b8d0..0x1b8ff reserved for settings-io (bwrecipes store starts at 0x1b900)
KSIO_VERSION = 1
TAG_SRC, TAG_LEN = 0x112578, 7  # MakerNote version format: 'Rev%4.4x' stock, 'Rev024M' on the flash-1 image
ST_NONE, ST_OK, ST_BAD, ST_OK_TAG = 0, 1, 2, 3
STATUS = {ST_NONE: "NONE", ST_OK: "OK", ST_BAD: "BAD", ST_OK_TAG: "OK, tag differed"}   # KSIO byte 0x8dc values
SAVE_BYTE = 0x1B8BD             # settingsio-menu SAVE TO CARD row (per-boot action state: never imported)

PATH_DAT, PATH_CUR, PATH_OK, PATH_BAD = "/ATA1/PARAM.DAT", "/ATA1/PARAM.CUR", "/ATA1/PARAM.OK", "/ATA1/PARAM.BAD"
DONE, OK_TEXT = b"DONE", b"OK\r\n"
REASONS = {1: b"BAD MAGIC\r\n", 2: b"BAD CHECKSUM\r\n", 3: b"BAD KOMA VERSION\r\n", 4: b"BAD REOPEN\r\n",
           6: b"BAD READ\r\n"}    # 5 (BAD TAG) retired 2026-11-07: a tag mismatch imports with status 3
KSIO_OFF, TAG_OFF = KSIO - BASE, KSIO - BASE + 5             # 0x8d0 'KSIO', 0x8d5..0x8db firmware tag (in a file)
SET_BASE = 0x1B8B0                                             # linker.SET_MAGIC: the clamp table's offsets are from here

# Firmware file API. open/read/close come from the loader service table (+0x14/+0x18/+0x1c); create/write/close are
# called by absolute address exactly as the hardware-proven telemetry feature does.
CREATE, WRITE, CLOSE = 0x55CF4, 0x560EC, 0x55F0C
# File remove (delete): r0 = path -> 0 / -1. STATIC only: 18 callers incl. the CONFIG rotation 0x653c0
# (`mov r0, sp; bl 0x55d80` at 0x6541e) and the GR match of the GX8/R2 PARAM.DAT helpers' remove
#. Never run on hardware by Koma, so it is only a cleanup after the
# proven DONE marker.
REMOVE = 0x55D80

# exclude_on_merge of tools/komaset/settings-map.json (33 entries, 2026-11-07), as (offset, size); the import keeps the
# running body's bytes there. tests/test_settingsio.py checks this table against the map file.
MAP_EXCLUDE = [(0x000, 4), (0x010, 14), (0x028, 8), (0x038, 1), (0x056, 1), (0x057, 1), (0x05E, 1), (0x063, 1),
               (0x064, 1), (0x06B, 1), (0x06F, 1), (0x070, 4), (0x074, 4), (0x07F, 1), (0x080, 26), (0x09E, 2),
               (0x0A2, 2), (0x0CE, 1), (0x0D0, 4), (0x0DA, 1), (0x0F4, 8), (0x14E, 4), (0x16C, 8), (0x18F, 1),
               (0x190, 1), (0x1A1, 1), (0x1A2, 1), (0x1A3, 1), (0x2F8, 48), (0x328, 16), (0x8B0, 5), (0x900, 16),
               (0xFFC, 4)]
# This feature's per-body additions: the KSIO sub-header (firmware tag and
# import status describe this body) and the SAVE TO CARD action byte.
SIO_EXCLUDE = [(SAVE_BYTE - BASE, 1), (KSIO - BASE, KSIO_END - KSIO)]
EXCLUDE_RANGES = sorted(MAP_EXCLUDE + SIO_EXCLUDE)


def exclude_mask(ranges=EXCLUDE_RANGES) -> bytes:
    """Host-side 4096-bit mask (bit o set = keep the running byte at offset o); tests and merge() use it."""
    m = bytearray(SIZE // 8)
    for off, n in ranges:
        for o in range(off, off + n):
            m[o >> 3] |= 1 << (o & 7)
    return bytes(m)


def be32(b: bytes) -> int:
    return int.from_bytes(b, "big")


def checksum(block: bytes) -> int:
    """Stock block checksum (0x3d9b8 / 0x4949c): u32 sum of big-endian u16 halves over 0x000..0xffb."""
    return sum(be32(block[i:i + 2]) for i in range(0, CK_OFF, 2)) & 0xFFFFFFFF


def with_checksum(block: bytes) -> bytes:
    b = bytearray(block)
    b[CK_OFF:] = checksum(b).to_bytes(4, "big")
    return bytes(b)


def merge(running: bytes, file: bytes) -> bytes:
    """Host model of the import copy (what the camera leaves in 0x1b000..0x1bfff after a valid import)."""
    out = bytearray(running)
    limit = CK_OFF if file[KOMA_OFF:KOMA_OFF + 4] == b"KOMA" else NOKOMA_LIMIT
    mask = exclude_mask()
    for o in range(limit):
        if not mask[o >> 3] >> (o & 7) & 1:
            out[o] = file[o]
    return bytes(out)


def clamp_rows(block: bytes, rows) -> bytes:
    """Host model of the post-import row repair: rows = linker row dicts (byte, options, default)."""
    b = bytearray(block)
    for r in rows:
        o = r["byte"] - BASE
        if b[o] >= len(r["options"]):
            b[o] = r["default"]
    return bytes(b)


def row_table(rows) -> bytes:
    """Link-time clamp table: (offset from 0x1b8b0, option count, default) per row, then 0xff."""
    out = bytearray()
    for r in sorted(rows, key=lambda r: r["byte"]):
        off, n = r["byte"] - SET_BASE, len(r["options"])
        assert 0 <= off < 0xFF and 0 < n < 256 and 0 <= r["default"] < n, r
        out += bytes([off, n, r["default"]])
    return bytes(out) + b"\xff"


def verdict(file: bytes, version: int = KOMA_VERSION, tag: bytes | None = None):
    """Host model of the envelope check: 'ignore' | 'ok' | 'ok:tag' | 'bad:<reason>'. tag = the running body's 7-byte
    firmware tag (0x112578); None skips the tag compare. 'ok:tag' = imported, KSIO status 3 (tag differed)."""
    if len(file) != SIZE or file[:4] == DONE:
        return "ignore"
    if file[:4] != b"MG1H":
        return "bad:" + REASONS[1].decode().strip()
    if checksum(file) != be32(file[CK_OFF:]):
        return "bad:" + REASONS[2].decode().strip()
    if file[KOMA_OFF:KOMA_OFF + 4] == b"KOMA" and file[KOMA_VER_OFF] != version:
        return "bad:" + REASONS[3].decode().strip()
    if tag is not None and file[KSIO_OFF:KSIO_OFF + 4] == b"KSIO" and file[TAG_OFF:TAG_OFF + TAG_LEN] != tag:
        return "ok:tag"
    return "ok"


def _asc(b: bytes) -> str:
    return ", ".join(f"0x{x:02x}" for x in b)


def range_table(ranges=EXCLUDE_RANGES) -> bytes:
    """Sorted, merged (start, end) u16 BE pairs + a 0xffff sentinel: the import's keep-the-running-byte list."""
    merged = []
    for off, n in sorted(ranges):
        if merged and off <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], off + n)
        else:
            merged.append([off, off + n])
    return b"".join(a.to_bytes(2, "big") + e.to_bytes(2, "big") for a, e in merged) + b"\xff\xff\xff\xff"


def source(rows=()) -> str:
    """rows: the build's Koma rows (linker dicts), passed at link time through late_source; the clamp table."""
    reasons = "\n".join(f"r_{k}: .byte {_asc(v)}\n.p2align 2" for k, v in REASONS.items())
    sel = "\n".join(f"""    cmp r4, #{k}
    bne 1f
    ldr r1, =r_{k}
    movs r2, #{len(v)}
    b bad_put
1:""" for k, v in REASONS.items())
    kch, cch = KOMA_OFF & ~(CHUNK - 1), CK_OFF & ~(CHUNK - 1)
    sch = KSIO_OFF & ~(CHUNK - 1)
    assert len({kch, cch, sch}) == 3 and TAG_OFF + TAG_LEN <= sch + CHUNK
    rowtab = row_table(rows)
    return f"""
.equ CHUNK, {CHUNK}
.equ FRAME, {FRAME}
.equ BUF, {BUF}
@ settingsio init (transient): r0 = service table, r1 = resident base. Runs once per boot from the installer.
@ frame: sp+0 fd, +4 word 0 of the file, +8 word at 0x8b0, +12 byte at 0x8b4, +16 stored checksum, +20 copy limit,
@ +24 tag mismatch (non-zero: the file's KSIO tag differs from 0x112578), +28 CHUNK-byte read buffer (on the stack:
@ no pool, no body space). r7 = import status this boot (0 none, 1 ok,
@ 2 bad; header turns 1 into 3 when sp+24 is set), r4 = bad reason (pass 1) / exclusion-table cursor (pass 2)
sio_init:
    push {{r4, r5, r6, r7, lr}}
    sub sp, #FRAME
    ldr r1, =svc
    str r0, [r1]
    movs r7, #{ST_NONE}
@ ---- pass 1: size, checksum and header words
    ldr r0, =p_dat
    bl s_open
    adds r1, r0, #1
    bne 1f
    b header                    @ no PARAM.DAT
1:  str r0, [sp, #0]
    movs r5, #0                 @ r5 = file offset of the chunk
    movs r6, #0                 @ r6 = u16-BE sum
    str r5, [sp, #24]           @ tag mismatch := 0
p1:
    ldr r0, [sp, #0]
    add r1, sp, #BUF
    bl s_chunk
    cmp r0, #CHUNK
    beq p1_full
    b p1_size                   @ short or failed read (-1): not a 4096-byte file
p1_full:
    add r4, sp, #BUF
    cmp r5, #0
    bne 2f
    ldr r0, [r4]
    str r0, [sp, #4]
2:  ldr r0, =0x{kch:x}
    cmp r5, r0
    bne 3f
    ldr r0, [r4, #{KOMA_OFF - kch}]
    str r0, [sp, #8]
    ldr r0, [r4, #{KOMA_VER_OFF - kch}]
    lsrs r0, r0, #24            @ byte 0x8b4 (big-endian word 0x8b4..0x8b7)
    str r0, [sp, #12]
3:  ldr r0, =0x{sch:x}
    cmp r5, r0
    bne 7f
    ldr r0, [r4, #{KSIO_OFF - sch}]
    ldr r1, =0x{be32(b"KSIO"):08x}
    cmp r0, r1
    bne 7f                      @ no KSIO sub-header (stock body or older Koma): no tag check
    ldr r1, =0x{TAG_SRC:x}
    movs r3, #0
p1_tag:
    adds r0, r4, r3
    ldrb r0, [r0, #{TAG_OFF - sch}]
    ldrb r2, [r1, r3]
    cmp r0, r2
    beq p1_tnext
    str r1, [sp, #24]           @ mismatch (r1 = 0x112578, non-zero): import anyway, status 3
p1_tnext:
    adds r3, #1
    cmp r3, #{TAG_LEN}
    blo p1_tag
7:  movs r2, r4
    adds r2, #CHUNK
    ldr r0, =0x{cch:x}
    cmp r5, r0
    bne 4f
    ldr r0, [r4, #{CK_OFF - cch}]  @ last chunk: sum up to 0xffb, keep the stored checksum
    str r0, [sp, #16]
    subs r2, #{CHUNK - (CK_OFF - cch)}
4:  movs r1, r4
5:  ldrh r0, [r1]
    adds r6, r6, r0
    adds r1, #2
    cmp r1, r2
    blo 5b
    adds r5, #CHUNK
    lsrs r0, r5, #12
    beq p1
    ldr r0, [sp, #0]            @ exactly 4096 bytes: one more byte must not exist
    add r1, sp, #BUF
    movs r2, #1
    bl s_read
    cmp r0, #0
    beq p1_eof
    b p1_size
p1_eof:
    ldr r0, [sp, #0]
    bl s_close
@ ---- envelope
    ldr r0, [sp, #4]
    ldr r1, =0x{be32(DONE):08x}           @ "DONE": already consumed (create did not truncate)
    cmp r0, r1
    beq header_far
    movs r4, #1
    ldr r1, =0x{be32(b"MG1H"):08x}
    cmp r0, r1
    bne bad
    movs r4, #2
    ldr r0, [sp, #16]
    cmp r0, r6
    bne bad
    movs r4, #3
    ldr r0, [sp, #8]
    ldr r1, =0x{be32(b"KOMA"):08x}
    ldr r2, =0x{NOKOMA_LIMIT:x}
    cmp r0, r1
    bne 6f
    ldr r0, [sp, #12]
    cmp r0, #{KOMA_VERSION}
    bne bad
    ldr r2, =0x{CK_OFF:x}
6:  str r2, [sp, #20]
@ ---- pass 2: copy, keeping the excluded (per-body) bytes
    movs r4, #4
    ldr r0, =p_dat
    bl s_open
    adds r1, r0, #1
    beq bad
    str r0, [sp, #0]
    ldr r4, =keep               @ r4 = exclusion-table cursor (sorted start/end pairs, offsets only increase)
    movs r5, #0
p2:
    ldr r0, [sp, #0]
    add r1, sp, #BUF
    bl s_chunk
    cmp r0, #CHUNK
    bne p2_short                @ the file changed between passes: BAD READ (the bytes so far stay applied)
    movs r6, #0                 @ r6 = index in the chunk
p2b:
    adds r3, r5, r6             @ r3 = block offset
    ldr r0, [sp, #20]
    cmp r3, r0
    bhs p2_end
7:  ldrh r0, [r4, #2]           @ skip ranges that end at or before this offset
    cmp r3, r0
    blo 8f
    adds r4, #4
    b 7b
8:  ldrh r0, [r4]
    cmp r3, r0
    bhs p2_skip                 @ inside a kept range: keep the running byte
    add r1, sp, #BUF
    ldrb r0, [r1, r6]
    ldr r1, =0x{BASE:x}
    strb r0, [r1, r3]
p2_skip:
    adds r6, #1
    cmp r6, #CHUNK
    blo p2b
    adds r5, #CHUNK
    lsrs r0, r5, #12
    beq p2
p2_end:
    movs r7, #{ST_OK}
p2_close:
    ldr r0, [sp, #0]
    bl s_close
    bl clamp                    @ row bytes back in range, as koma_settings_init at boot
    cmp r7, #{ST_OK}
    beq consume
    b bad
p2_short:
    movs r4, #6                 @ r7 stays ST_NONE: p2_close goes to bad
    b p2_close
header_far:
    b header
p1_size:                        @ not 4096 bytes: ignore silently (also how the DONE marker is skipped)
    ldr r0, [sp, #0]
    bl s_close
    b header
bad:
    movs r7, #{ST_BAD}
@ ---- one-shot: PARAM.DAT := "DONE", remove it, then PARAM.OK or PARAM.BAD
consume:
    ldr r0, =p_dat
    ldr r1, =done_s
    movs r2, #4
    bl put                      @ proven calls first: the one-shot never depends on the remove below
    ldr r0, =p_dat
    ldr r3, =0x{REMOVE | 1:x}
    bl call_r3                  @ remove(path) (result ignored, as the GX8/R2 PARAM.DAT helpers do)
    cmp r7, #{ST_OK}
    bne 7f
    ldr r0, =p_ok
    ldr r1, =ok_s
    movs r2, #4
    bl put
    b header
7:
{sel}
    ldr r1, =r_4
    movs r2, #{len(REASONS[4])}
bad_put:
    ldr r0, =p_bad
    bl put
@ ---- KSIO sub-header: magic, version, firmware tag, last-import status
header:
    ldr r0, =0x{KSIO:x}
    ldr r1, =0x{be32(b"KSIO"):08x}
    ldr r2, [r0]
    cmp r2, r1
    beq 8f
    str r1, [r0]                @ first time: status none
    movs r2, #{ST_NONE}
    strb r2, [r0, #12]
8:  movs r2, #{KSIO_VERSION}
    strb r2, [r0, #4]
    ldr r1, =0x{TAG_SRC:x}
    movs r3, #0
9:  ldrb r2, [r1, r3]
    adds r0, #1
    strb r2, [r0, #4]           @ 0x1b8d5 + i
    adds r3, #1
    cmp r3, #{TAG_LEN}
    blo 9b
    cmp r7, #{ST_NONE}
    beq export
    cmp r7, #{ST_OK}
    bne 7f
    ldr r0, [sp, #24]           @ r7 = ok implies pass 1 ran, so sp+24 was initialised
    cmp r0, #0
    beq 7f
    movs r7, #{ST_OK_TAG}       @ imported, but the file's KSIO tag differs from this body's
7:  ldr r0, =0x{KSIO + 12:x}
    strb r7, [r0]
@ ---- export: PARAM.CUR = the block as it is in RAM
export:
    ldr r0, =p_cur
    ldr r1, =0x{BASE:x}
    ldr r2, =0x{SIZE:x}
    bl put
    add sp, #FRAME
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

s_open:                         @ r0 = path -> r0 = fd or -1
    push {{r4, lr}}
    movs r4, #0
    mvns r4, r4                 @ 0x55dc0 returns the caller's r4 when its path check fails (module-loader README)
    movs r1, #0
    ldr r3, =svc
    ldr r3, [r3]
    ldr r3, [r3, #0x14]
    bl call_r3
    pop {{r4}}
    pop {{r3}}
    bx r3

s_chunk:                        @ r0 = fd, r1 = buffer -> r0 = bytes read (CHUNK wanted)
    movs r2, #CHUNK
s_read:                         @ r0 = fd, r1 = buffer, r2 = count -> r0 = bytes read
    ldr r3, =svc
    ldr r3, [r3]
    ldr r3, [r3, #0x18]
    bx r3

s_close:                        @ r0 = fd
    ldr r3, =svc
    ldr r3, [r3]
    ldr r3, [r3, #0x1c]
    bx r3

put:                            @ create (truncates) / write / close: r0 = path, r1 = data, r2 = length
    push {{r4, r5, r6, lr}}
    movs r5, r1
    movs r6, r2
    movs r1, #2                 @ as stock callers and telemetry
    ldr r3, =0x{CREATE | 1:x}
    bl call_r3
    movs r4, r0
    adds r0, #1
    beq 1f
    movs r0, r4
    movs r1, r5
    movs r2, r6
    ldr r3, =0x{WRITE | 1:x}
    bl call_r3
    movs r0, r4
    ldr r3, =0x{CLOSE | 1:x}
    bl call_r3
1:  pop {{r4, r5, r6}}
    pop {{r3}}
    bx r3

clamp:                          @ Koma row bytes: >= option count -> default (koma_settings_init's boot repair)
    push {{r4, lr}}
    ldr r1, =rowtab             @ (offset from 0x1b8b0, option count, default) per row, 0xff end (link time)
    ldr r2, =0x{SET_BASE:x}
1:  ldrb r0, [r1]
    cmp r0, #0xff
    beq 2f
    adds r0, r2, r0
    ldrb r3, [r0]
    ldrb r4, [r1, #1]
    cmp r3, r4
    blo 3f
    ldrb r4, [r1, #2]
    strb r4, [r0]
3:  adds r1, #3
    b 1b
2:  pop {{r4}}
    pop {{r3}}
    bx r3

call_r3:
    bx r3
.ltorg
.p2align 2
svc: .word 0
p_dat: .asciz "{PATH_DAT}"
.p2align 2
p_cur: .asciz "{PATH_CUR}"
.p2align 2
p_ok: .asciz "{PATH_OK}"
.p2align 2
p_bad: .asciz "{PATH_BAD}"
.p2align 2
done_s: .ascii "DONE"
ok_s: .byte {_asc(OK_TEXT)}
{reasons}
rowtab: .byte {_asc(rowtab)}
.p2align 2
"""


def make(opts):
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options={}, source=source(), late_source=source, init="sio_init",
        transient=True,
        blobs={"keep": range_table()},
        settings=[Settings(KSIO, KSIO_END, True, "settings-io sub-header: KSIO, version, firmware tag, import status"),
                  Settings(BASE, BASE + CK_OFF, False, "boot import of a validated PARAM.DAT (bulk copy, per-body "
                           "bytes kept)", bulk=True)],
        checks=[Check(CREATE, fw.ram(CREATE, 4), "file create 0x55cf4 prologue"),
                Check(WRITE, fw.ram(WRITE, 4), "file write 0x560ec prologue"),
                Check(CLOSE, fw.ram(CLOSE, 4), "file close 0x55f0c prologue"),
                Check(REMOVE, fw.ram(REMOVE, 4), "file remove 0x55d80 prologue")],
        smoke=smoke_checks)


# ---------------------------------------------------------------- koma smoke (no PARAM.DAT on the smoke card)

def smoke_checks(obs):
    cur = obs.card_file(PATH_CUR) if hasattr(obs, "card_file") else None
    if cur is None:
        return [("PARAM.CUR written", False, f"{PATH_CUR} not on the emulated card")]
    hdr = cur[KSIO - BASE:KSIO - BASE + 13]
    return [("PARAM.CUR is 4096 B with MG1H", len(cur) == SIZE and cur[:4] == b"MG1H", f"{len(cur)} B {cur[:4]!r}"),
            ("KSIO header v1 in PARAM.CUR", hdr[:5] == b"KSIO\x01", hdr.hex()),
            ("no import without PARAM.DAT", obs.card_file(PATH_OK) is None and obs.card_file(PATH_BAD) is None, "")]
