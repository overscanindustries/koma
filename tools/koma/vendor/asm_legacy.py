# Vendored from tools/emu_frontier2_asm.py (the emu-frontier-2 Thumb assembler wrapper). Edits: imports, firmware/data paths, writers removed.
"""Tiny Thumb (ARMv4T, big-endian) assembler wrapper for emu-frontier-2 patches. Offline; writes no image.

assemble(src, base, calls) runs llvm-mc (thumbeb, arm7tdmi) on `src`, which may contain absolute-call
placeholders written as   CALL_<name>: .hword 0xf000, 0xf800   ; each is patched to `bl calls[<name>]`.
Local `bl label` is resolved by llvm-mc. Returns (code bytes, {symbol: runtime address}).
disasm(code, base) gives capstone Thumb-BE text for review.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import capstone

from ..asm import LLVM   # one LLVM location for all of Koma (KOMA_LLVM)
LOAD = 0x1c000
HEADER = 0x200


def thumb_bl(source, target):
    off = target - (source + 4)
    if off & 1 or not -(1 << 22) <= off < (1 << 22):
        raise ValueError(f"BL out of range {source:#x}->{target:#x}")
    return ((0xF000 | ((off >> 12) & 0x7FF)).to_bytes(2, "big") + (0xF800 | ((off >> 1) & 0x7FF)).to_bytes(2, "big"))


def assemble(src, base, calls=None):
    calls = calls or {}
    if base % 4:
        raise ValueError(f"base {base:#x} must be 4-aligned: PC-relative literal loads assume object offset == runtime mod 4")
    with tempfile.TemporaryDirectory() as t:
        s, o, b = (Path(t) / n for n in ("a.s", "a.o", "a.bin"))
        s.write_text(".syntax unified\n.thumb\n.text\n.p2align 1\n" + src + "\n")
        subprocess.run([str(LLVM / "llvm-mc"), "-triple=thumbeb-none-eabi", "-mcpu=arm7tdmi", "-filetype=obj",
                        str(s), "-o", str(o)], check=True, capture_output=True, text=True)
        rel = subprocess.run([str(LLVM / "llvm-readobj"), "--relocations", str(o)], check=True, capture_output=True,
                             text=True).stdout
        if "R_ARM" in rel:
            raise ValueError("unresolved relocations:\n" + rel)
        nm = subprocess.run([str(LLVM / "llvm-nm"), str(o)], check=True, capture_output=True, text=True).stdout
        subprocess.run([str(LLVM / "llvm-objcopy"), "-O", "binary", "--only-section=.text", str(o), str(b)],
                       check=True, capture_output=True)
        code = bytearray(b.read_bytes())
    syms = {}
    for line in nm.splitlines():
        f = line.split()
        if len(f) == 3 and not f[2].startswith("$"):
            syms[f[2]] = base + int(f[0], 16)
    for name, addr in syms.items():
        if name.startswith("CALL_"):
            tgt = calls[name[5:]]
            off = addr - base
            assert code[off:off + 4] == bytes.fromhex("f000f800"), name
            code[off:off + 4] = thumb_bl(addr, tgt)
    missing = [n for n in calls if f"CALL_{n}" not in syms]
    if missing:
        raise ValueError(f"call placeholders not found: {missing}")
    return bytes(code), syms


def disasm(code, base):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_BIG_ENDIAN)
    return [f"{i.address:#07x}: {i.bytes.hex():<9} {i.mnemonic} {i.op_str}" for i in md.disasm(code, base)]


def payload_offset(addr):
    return addr - LOAD


def file_offset(addr):
    return addr - LOAD + HEADER
