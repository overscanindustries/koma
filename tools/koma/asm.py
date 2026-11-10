"""Thumb (ARMv4T, big-endian) assembly for Koma: llvm-mc -> (bytes, symbols, relocations).

Unlike tools/emu_frontier2_asm.assemble, R_ARM_ABS32 relocations are allowed and returned: they are the words whose
value is a body offset (`ldr rX, =label`, `.word label`). The installer adds the runtime body base to each of them.
Any other relocation type is a build error.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import capstone


def _llvm_bin() -> Path:
    """LLVM 20 tools directory: $KOMA_LLVM, else Homebrew's llvm@20, else wherever llvm-mc is on PATH."""
    env = os.environ.get("KOMA_LLVM")
    if env:
        return Path(env).expanduser()
    for d in (Path("/opt/homebrew/opt/llvm@20/bin"), Path("/usr/local/opt/llvm@20/bin"), Path("/usr/lib/llvm-20/bin")):
        if (d / "llvm-mc").exists():
            return d
    found = shutil.which("llvm-mc")
    return Path(found).parent if found else Path("/opt/homebrew/opt/llvm@20/bin")


LLVM = _llvm_bin()


class AsmError(Exception):
    pass


def assemble(src: str, files: dict[str, bytes] | None = None):
    """Assemble at base 0. `files`: name -> bytes made available to `.incbin "name"`.
    Returns (code, {symbol: offset}, [reloc offsets])."""
    with tempfile.TemporaryDirectory(prefix="koma-asm-") as t:
        t = Path(t)
        for name, data in (files or {}).items():
            (t / name).write_bytes(data)
        s, o, b = t / "k.s", t / "k.o", t / "k.bin"
        s.write_text(".syntax unified\n.thumb\n.text\n.p2align 2\n" + src + "\n")
        r = subprocess.run([str(LLVM / "llvm-mc"), "-triple=thumbeb-none-eabi", "-mcpu=arm7tdmi", "-filetype=obj",
                            str(s), "-o", str(o)], capture_output=True, text=True, cwd=t)
        if r.returncode:
            raise AsmError(_annotate(r.stderr, s.read_text()))
        rel = subprocess.run([str(LLVM / "llvm-readobj"), "--relocations", str(o)], check=True, capture_output=True,
                             text=True).stdout
        nm = subprocess.run([str(LLVM / "llvm-nm"), "--defined-only", str(o)], check=True, capture_output=True,
                            text=True).stdout
        subprocess.run([str(LLVM / "llvm-objcopy"), "-O", "binary", "--only-section=.text", str(o), str(b)],
                       check=True, capture_output=True)
        code = b.read_bytes() if b.exists() else b""
    relocs = []
    for line in rel.splitlines():
        m = re.match(r"\s*0x([0-9A-Fa-f]+)\s+(R_ARM_\w+)\s+(\S+)", line)
        if not m:
            continue
        off, kind, sym = int(m.group(1), 16), m.group(2), m.group(3)
        if sym != ".text":
            raise AsmError(f"undefined symbol {sym!r} referenced at {off:#x} ({kind}); misspelt label, or a "
                           f"cross-feature reference without $feature.symbol")
        if kind != "R_ARM_ABS32":
            raise AsmError(f"unsupported relocation {kind} against {sym} at {off:#x} (only body-internal words)")
        relocs.append(off)
    syms = {}
    for line in nm.splitlines():
        f = line.split()
        if len(f) == 3 and not f[2].startswith("$") and f[1] in "tTdDaAbB":
            syms[f[2]] = int(f[0], 16)
    return code, syms, sorted(relocs)


def _annotate(err: str, src: str) -> str:
    lines = src.splitlines()
    out = []
    for e in err.splitlines():
        out.append(e)
        m = re.match(r".*k\.s:(\d+):", e)
        if m and 0 < int(m.group(1)) <= len(lines):
            out.append("    > " + lines[int(m.group(1)) - 1].strip())
    return "\n".join(out[:40])


def disasm(code: bytes, base: int = 0):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_BIG_ENDIAN)
    return list(md.disasm(code, base))


def thumb_bl(site: int, target: int) -> bytes:
    off = target - (site + 4)
    if off & 1 or not -(1 << 22) <= off < (1 << 22):
        raise ValueError(f"BL out of range {site:#x}->{target:#x}")
    return ((0xF000 | ((off >> 12) & 0x7FF)).to_bytes(2, "big") + (0xF800 | ((off >> 1) & 0x7FF)).to_bytes(2, "big"))


def bl_target(site: int, b: bytes) -> int | None:
    """Target of a Thumb BL pair at `site`, or None if the 4 bytes are not a BL pair."""
    hi, lo = int.from_bytes(b[:2], "big"), int.from_bytes(b[2:4], "big")
    if hi & 0xF800 != 0xF000 or lo & 0xF800 != 0xF800:
        return None
    off = ((hi & 0x7FF) << 12) | ((lo & 0x7FF) << 1)
    if off & 0x400000:
        off -= 0x800000
    return site + 4 + off
