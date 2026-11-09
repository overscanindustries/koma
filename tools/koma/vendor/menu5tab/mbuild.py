# Vendored from the earlier menu-5tab mbuild.py (menu module builder). Edits: imports, firmware/data paths, writers removed.
"""Resident-module builder for the GR DIGITAL 2.40 card-module loader (offline; builds bytes only).

Extends the module-loader grmod.py (imported, not edited) with what a *resident* code module needs:
  * a position-independent installer (Thumb) that checks ABI 1 + image word 0x240, verifies EVERY patch's 4
    'before' bytes, relocates the body's own absolute pointers, runs an optional init routine, then writes all
    patches with IRQ/FIQ masked (service +0x28) and returns 1 so the loader keeps the body resident;
  * three patch kinds, all 4 bytes at a 2-aligned address:
        0 RAW   write arg
        1 BL    Thumb `bl` from the site to body+arg (computed at run time from the pool address)
        2 PTR   write the word body+arg
  * relocations found by assembling the module twice (base 0 and base 0x10000): every word that differs by
    exactly 0x10000 is a body-absolute pointer; the installer adds the runtime base to it.
Module source rules: no absolute `bl` to firmware (use `ldr r3, =fn|1` + `bl call_r3`); body-absolute addresses
are written `&label` (`ldr rX, =&label`, `.word &label`), which the builder turns into (label - entry + BASE) and
the installer relocates. The user source must define `init`
(may just `bx lr`) and may use `call_r3`, which the installer provides.
"""
from __future__ import annotations

import re
import struct

from ... import firmware as fw
from .. import grmod
from ..asm_legacy import assemble, disasm, thumb_bl

RAW = fw.payload()
LOAD = 0x1C000
RAW_K, BL_K, PTR_K = 0, 1, 2


def img(addr, n):
    return RAW[addr - LOAD:addr - LOAD + n]


INSTALLER = """
entry:                              @ r0 = service table, r1 = body base, r2 = length
    push {r4, r5, r6, r7, lr}
    sub sp, #8                      @ sp+0 svc, sp+4 old irq mask
    str r0, [sp, #0]
    movs r7, r1                     @ body base
    ldr r1, [r0, #0]
    cmp r1, #1
    bne i_fail
    ldr r1, [r0, #4]
    ldr r2, i_ver
    cmp r1, r2
    bne i_fail
    adr r4, i_tables
    ldr r6, [r4, #4]
    adds r6, r6, r7                 @ patch table
    movs r4, r6
i_v:                                @ pass 1: every 'before' must match
    ldr r0, [r4, #0]
    cmp r0, #0
    beq i_vok
    movs r2, #0
i_vc:
    ldrb r1, [r0, r2]
    adds r3, r4, #4
    adds r3, #4
    ldrb r3, [r3, r2]
    cmp r1, r3
    bne i_fail
    adds r2, #1
    cmp r2, #4
    blo i_vc
    adds r4, #16
    b i_v
i_vok:
    adr r4, i_tables
    ldr r5, [r4, #0]
    adds r5, r5, r7                 @ relocation list (body offsets, -1 terminated)
i_r:
    ldr r0, [r5, #0]
    adds r1, r0, #1
    beq i_rok
    ldr r1, [r7, r0]
    adds r1, r1, r7
    str r1, [r7, r0]
    adds r5, #4
    b i_r
i_rok:
    bl init
    ldr r0, [sp, #0]
    ldr r3, [r0, #0x28]             @ irq_set (ARM)
    movs r0, #0xc0
    bl call_r3
    str r0, [sp, #4]
    movs r4, r6
i_a:
    ldr r0, [r4, #0]                @ site
    cmp r0, #0
    beq i_done
    ldr r1, [r4, #4]                @ kind
    ldr r2, [r4, #12]               @ arg
    cmp r1, #0
    beq i_w
    adds r2, r2, r7                 @ body + arg
    cmp r1, #2
    beq i_w
    adds r3, r0, #4                 @ BL: off = target - (site + 4)
    subs r2, r2, r3
    lsls r3, r2, #9
    lsrs r3, r3, #21                @ (off >> 12) & 0x7ff
    lsls r1, r2, #20
    lsrs r1, r1, #21                @ (off >> 1) & 0x7ff
    ldr r2, i_blhi
    orrs r3, r2
    lsls r3, r3, #16
    ldr r2, i_bllo
    orrs r1, r2
    orrs r3, r1
    movs r2, r3
i_w:                                @ big-endian byte stores (site may be 2-aligned)
    lsrs r1, r2, #24
    strb r1, [r0, #0]
    lsrs r1, r2, #16
    strb r1, [r0, #1]
    lsrs r1, r2, #8
    strb r1, [r0, #2]
    strb r2, [r0, #3]
    adds r4, #16
    b i_a
i_done:
    ldr r1, [sp, #0]
    ldr r3, [r1, #0x28]
    ldr r0, [sp, #4]
    bl call_r3
    movs r0, #1                     @ stay resident
    b i_out
i_fail:
    movs r0, #0                     @ nothing applied; loader frees the body
i_out:
    add sp, #8
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
call_r3:
    bx r3
.p2align 2
i_ver: .word 0x00000240
i_blhi: .word 0xf000
i_bllo: .word 0xf800
i_tables: .word 0, 0                @ filled by the builder: reloc list offset, patch table offset
"""


def build(user_src: str, patches):
    """patches: [(site, kind, arg_or_symbol, expected_before_bytes|None)]; arg may be a body symbol name.
    Returns dict(module=bytes, body=bytes, syms, relocs, patches, disasm)."""
    src = INSTALLER + "\n.p2align 2\n" + user_src + "\n.p2align 2\n.ltorg\n"
    src = re.sub(r"&([A-Za-z_]\w*)", r"(\1 - entry + BASE)", src)
    c0, s0 = assemble(".set BASE, 0\n" + src, 0)
    c1, s1 = assemble(".set BASE, 0x10000\n" + src, 0x10000)
    assert len(c0) == len(c1) and len(c0) % 4 == 0 or True
    code = bytearray(c0 + bytes(-len(c0) % 4))
    c1 = c1 + bytes(-len(c1) % 4)
    relocs = []
    for off in range(0, len(code), 4):
        if off + 4 > len(code):
            break
        a = int.from_bytes(code[off:off + 4], "big")
        b = int.from_bytes(c1[off:off + 4], "big")
        if a != b:
            assert b - a == 0x10000 and off % 4 == 0, f"non-relocatable difference at {off:#x}: {a:#x} {b:#x}"
            relocs.append(off)
    rel_off = len(code)
    table = b"".join(struct.pack(">I", r) for r in relocs) + struct.pack(">I", 0xFFFFFFFF)
    pat_off = rel_off + len(table)
    recs = b""
    plist = []
    for site, kind, arg, before in patches:
        a = s0[arg] if isinstance(arg, str) else arg
        cur = img(site, 4)
        if before is not None:
            assert cur == before, (hex(site), cur.hex(), before.hex())
        recs += struct.pack(">II", site, kind) + cur + struct.pack(">I", a)
        plist.append(dict(site=hex(site), kind=["raw", "bl", "ptr"][kind], arg=hex(a),
                          sym=arg if isinstance(arg, str) else None, before=cur.hex()))
    recs += bytes(16)
    t = s0["i_tables"]
    code[t:t + 8] = struct.pack(">II", rel_off, pat_off)
    body = bytes(code) + table + recs
    body += bytes(-len(body) % 4)
    return dict(module=grmod.pack_module(body, s0["entry"]), body=body, syms=s0, relocs=relocs, patches=plist,
                code_len=len(c0), disasm=disasm(bytes(code[:len(c0)]), 0))


def simulate_install(body: bytes, base: int, syms, ram_read):
    """Host mirror of the installer (for checking only): returns {site: after-bytes} and the relocated body."""
    b = bytearray(body)
    rel_off, pat_off = struct.unpack(">II", b[syms["i_tables"]:syms["i_tables"] + 8])
    i = rel_off
    while True:
        (o,) = struct.unpack(">I", b[i:i + 4])
        if o == 0xFFFFFFFF:
            break
        v = struct.unpack(">I", b[o:o + 4])[0] + base
        b[o:o + 4] = struct.pack(">I", v & 0xFFFFFFFF)
        i += 4
    out = {}
    i = pat_off
    while True:
        site, kind = struct.unpack(">II", b[i:i + 8])
        if site == 0:
            break
        before, (arg,) = b[i + 8:i + 12], struct.unpack(">I", b[i + 12:i + 16])
        assert bytes(ram_read(site, 4)) == before, hex(site)
        if kind == RAW_K:
            out[site] = struct.pack(">I", arg)
        elif kind == PTR_K:
            out[site] = struct.pack(">I", base + arg)
        else:
            out[site] = thumb_bl(site, base + arg)
        i += 16
    return out, bytes(b)
