# Vendored from analysis/module-loader/grmod.py (the GRMOD container SDK). Edits: imports, firmware/data paths, writers removed.
"""GRMOD module SDK for the GR DIGITAL 2.40 card-module loader. Offline: builds bytes, never writes to a card.

File format (all big-endian, like the firmware):
  0x00  u32  magic 'GRMD' (0x47524d44)
  0x04  u16  format version = 1
  0x06  u16  flags = 0 (reserved; the loader rejects anything else)
  0x08  u32  body length L: 4 <= L <= 0x10000, L % 4 == 0
  0x0c  u32  entry offset E into the body: even, E < L (Thumb code; the loader adds 1)
  0x10  u32  checksum: c = 0; for each BE u32 word w of (header[0x00:0x10] + body): c = rol32(c, 1) + w
  0x14  body (L bytes). The file must end exactly here (the loader probes one byte past it).

Entry ABI: Thumb, called in MainCtrl right after the post-update-gate call 0x282cc, before the script gate read.
  r0 = service table (in the image), r1 = body base (pool memory, 4-aligned), r2 = L.
  Preserve r4-r11 and sp; return r0 = 0 to let the loader free the body, non-zero to keep it resident.
  Must not block for long, must not write flash, must not loop forever (pulling the card is the recovery).

Service table (words, Thumb pointers have bit 0 set):
  +0x00 ABI = 1            +0x04 image version word 0x00000240 (qrflect3 header +0x12)
  +0x08 pool_alloc 0x15f025 (pool, &ptr, size, 0) -> 0 ok     +0x0c pool_free 0x15f085 (ptr)
  +0x10 pool handle 0x2bf81c                                  +0x14 file_open 0x55dc1 (path, flags) -> fd / -1
  +0x18 file_read 0x56025 (fd, buf, n) -> n / -1               +0x1c file_close 0x55f0d (fd)
  +0x20 key funnel 0x517dd (stock key-event emitter)          +0x24 LUT upload 0x8ffc9 (selector, 0)
  +0x28 irq_set 0x1c5b4 (ARM; old = irq_set(bits 0xc0 mask))  +0x2c sleep 0x288f7 (ticks, ~ms)

Data-patch modules: build_patch_module() prepends a fixed position-independent Thumb patcher. It checks ABI and the
image version word, verifies EVERY patch's 'before' bytes against RAM, and only then (IRQ/FIQ masked) writes every
'after'. Any mismatch applies nothing. Patch table: records {u32 addr, u32 len, before[pad4], after[pad4]}, ends with 0.
"""
from __future__ import annotations

import struct

from .asm_legacy import assemble, disasm

MAGIC = 0x47524D44
FORMAT = 1
MAXLEN = 0x10000
HDR = 0x14
IMAGE_VERSION = 0x00000240

PATCHER_SRC = """
entry:                          @ r0 = svc, r1 = base, r2 = len
    push {r4, r5, r6, r7, lr}
    sub sp, #8                  @ sp+0 svc, sp+4 old irq mask
    str r0, [sp, #0]
    ldr r1, [r0, #0]
    cmp r1, #1                  @ ABI 1
    bne fail
    ldr r1, [r0, #4]
    ldr r2, want
    cmp r1, r2                  @ image version word
    bne fail
    adr r4, table               @ pass 1: every 'before' must match RAM
v:  ldr r5, [r4, #0]
    cmp r5, #0
    beq apply
    ldr r6, [r4, #4]
    adds r4, #8
    movs r2, #0
vc: cmp r2, r6
    bhs vn
    ldrb r0, [r5, r2]
    ldrb r1, [r4, r2]
    cmp r0, r1
    bne fail
    adds r2, #1
    b vc
vn: adds r0, r6, #3
    lsrs r0, r0, #2
    lsls r0, r0, #3             @ before + after, each padded to 4
    adds r4, r4, r0
    b v
apply:
    ldr r0, [sp, #0]
    ldr r3, [r0, #0x28]         @ irq_set (ARM)
    movs r0, #0xc0
    bl call_r3
    str r0, [sp, #4]
    adr r4, table               @ pass 2: write every 'after'
a:  ldr r5, [r4, #0]
    cmp r5, #0
    beq restore
    ldr r6, [r4, #4]
    adds r4, #8
    adds r0, r6, #3
    lsrs r0, r0, #2
    lsls r0, r0, #2
    adds r7, r4, r0             @ after bytes
    movs r2, #0
ac: cmp r2, r6
    bhs an
    ldrb r1, [r7, r2]
    strb r1, [r5, r2]
    adds r2, #1
    b ac
an: lsls r0, r0, #1
    adds r4, r4, r0
    b a
restore:
    ldr r1, [sp, #0]
    ldr r3, [r1, #0x28]
    ldr r0, [sp, #4]
    bl call_r3
fail:
    movs r0, #0                 @ nothing stays resident
    add sp, #8
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
call_r3:
    bx r3
.p2align 2
want: .word 0x%08x
table:
""" % IMAGE_VERSION


def checksum(header16: bytes, body: bytes) -> int:
    c = 0
    data = header16 + body
    for (w,) in struct.iter_unpack(">I", data):
        c = (((c << 1) | (c >> 31)) + w) & 0xFFFFFFFF
    return c


def pack_module(body: bytes, entry: int = 0, flags: int = 0, version: int = FORMAT) -> bytes:
    body = bytes(body) + bytes(-len(body) % 4)
    assert 4 <= len(body) <= MAXLEN and entry % 2 == 0 and entry < len(body)
    h = struct.pack(">IHHII", MAGIC, version, flags, len(body), entry)
    return h + struct.pack(">I", checksum(h, body)) + body


def parse_module(data: bytes) -> dict:
    """Host-side mirror of the loader's checks. Returns dict(ok=..., reason=...)."""
    if len(data) < HDR:
        return dict(ok=False, reason="short header")
    magic, ver, flags, length, entry, s = struct.unpack(">IHHIII", data[:HDR])
    body = data[HDR:]
    for bad, why in ((magic != MAGIC, "magic"), (ver != FORMAT or flags, "version/flags"),
                     (not (0 < length <= MAXLEN) or length % 4, "length"), (entry % 2 or entry >= length, "entry"),
                     (len(body) != length, "file size != 20 + length"), (checksum(data[:16], body) != s, "checksum")):
        if bad:
            return dict(ok=False, reason=why)
    return dict(ok=True, length=length, entry=entry, checksum=hex(s))


def patch_table(patches) -> bytes:
    """patches: [(addr, before bytes, after bytes)]"""
    out = bytearray()
    for addr, before, after in patches:
        assert len(before) == len(after) > 0 and addr != 0
        out += struct.pack(">II", addr, len(before)) + before + bytes(-len(before) % 4) + after + bytes(-len(after) % 4)
    return bytes(out + bytes(4))


PATCHER, PATCHER_SYMS = assemble(PATCHER_SRC, 0)
assert PATCHER_SYMS["table"] == len(PATCHER), "table must follow the patcher"


def build_patch_module(patches) -> bytes:
    return pack_module(PATCHER + patch_table(patches))


def build_asm_module(src: str) -> bytes:
    """Thumb source assembled at base 0. Must be position-independent: adr / pc-relative only, no absolute bl
    (the body lands at an unknown pool address); reach firmware through the service table with `bx`."""
    code, syms = assemble(src, 0)
    return pack_module(code, syms.get("entry", 0))
