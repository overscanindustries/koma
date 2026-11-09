"""Unit emulation of a Koma module (Unicorn, ARM BE): the real 2.40 payload in RAM, the module body at a pool address,
a service table, and stubbed firmware callees. Runs the installer exactly as the loader calls it, records every write,
and can then run the stock instructions around a hook site. Not a camera boot (that is smoke.py)."""
from __future__ import annotations

import hashlib
import struct

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_MODE_ARM, UC_MODE_BIG_ENDIAN, Uc
from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
                               UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
                               UC_ARM_REG_R7, UC_ARM_REG_SP)

from . import firmware as fw

BODY = 0x3C71C8          # pool address the loader proofs observed
RES_POOL = 0x3D0000      # where the stub allocator hands out the resident block
SVC = 0x7F0000
STUB_IRQ = 0x7F0100
RET = 0x7F0200
STACK = 0x7E0000
MMIO = 0x12018000
TIMER_PAGE = 0x12010000
ALLOC, FREE = 0x15F024, 0x15F084
STATIC_HEAP = 0x600000  # static builds: where the stub allocator hands out feature-init blocks
FW_STUBS = {0x8FF0E: "mono_8ff0e", 0x16D31C: "cc2_lut_16d31c"}


class Unit:
    def __init__(self, module: bytes, version: int = fw.IMAGE_VERSION, abi: int = fw.ABI, ram_edits=None,
                 body_base: int = BODY, alloc_result: int | None = RES_POOL, settings_imgset: int = 1):
        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_BIG_ENDIAN)
        uc.mem_map(0, 0x800000)
        uc.mem_write(fw.LOAD, fw.payload())
        for a, b in (ram_edits or {}).items():
            uc.mem_write(a, bytes(b))
        n_body = len(module) - 0x14
        if alloc_result and body_base < alloc_result < body_base + n_body:
            # large module (> 36 KB): the observed body address would run into the stub resident block; move the
            # body below it (the camera's pool never overlaps two live blocks)
            body_base = (alloc_result - n_body - 0x100) & ~0xF
        for base in (body_base, alloc_result or 0):
            if base >= 0x800000:
                uc.mem_map(base & ~0xFFFFF, 0x100000)
        self.body_base = body_base
        self.body = module[0x14:]
        self.entry = struct.unpack(">I", module[0x0C:0x10])[0]
        uc.mem_write(body_base, self.body)
        uc.mem_write(SVC, struct.pack(">12I", abi, version, ALLOC | 1, FREE | 1, 0x2BF81C, 0x55DC1, 0x56025, 0x55F0D,
                                      0x517DD, 0x8FFC9, STUB_IRQ, 0x288F7))
        uc.mem_write(STUB_IRQ, bytes.fromhex("e3a00013e12fff1e"))      # mov r0,#0x13 ; bx lr  (ARM)
        uc.mem_write(RET, bytes.fromhex("46c046c0"))
        blk = bytearray(b"\x03" * 0x1000)
        blk[0:4] = b"MG1H"
        blk[0x30] = settings_imgset
        blk[0x800:0x8B0] = b"\xff" * 0xB0
        uc.mem_write(0x1B000, bytes(blk))
        self.static = None
        flags = struct.unpack_from(">H", module, 0x14 + 6)[0] if module[0x14:0x18] == b"KMOD" else 0
        if flags & 2:                       # static resident (linker FLAG_STATIC): linked at a fixed address
            o_relocs, o_res, res_len = (struct.unpack_from(">I", module, 0x14 + o)[0] for o in (0x0C, 0x14, 0x18))
            nrel = struct.unpack_from(">I", module, 0x14 + o_relocs)[0]
            addr = struct.unpack_from(">I", module, 0x14 + o_relocs + 8 + 4 * nrel)[0]
            self.static = (addr, res_len + 16)          # segment + canary
            if alloc_result == RES_POOL:
                alloc_result = STATIC_HEAP          # feature-init pool blocks: away from the static block
        self.alloc_result = alloc_result
        self.res_base = self.static[0] if self.static else (alloc_result or RES_POOL)
        self.calls, self.mmio, self.writes = [], [], []
        self.irq_masked = False
        self.writes_while_unmasked = []
        uc.mem_map(TIMER_PAGE, 0x1000)     # timer-block candidates read by telemetry (plain RAM, reads 0)
        uc.mmio_map(MMIO, 0x1000, lambda u, o, s, d: 0, None,
                    lambda u, o, s, v, d: self.mmio.append(("%08x" % (MMIO + o), "%08x" % struct.unpack(
                        ">I", struct.pack("<I", v))[0])), None)   # Unicorn BE MMIO hands the value byte-swapped
        uc.hook_add(UC_HOOK_CODE, self._code)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._write)
        self.resident = None
        self.alloc_next = 0
        self.blocks = []
        self.stubs = {}
        self.watch, self.watch_hits = set(), []    # addresses whose first execution is logged (pc, len(writes))

    def stub(self, addr, name, r0=None):
        """Make firmware function `addr` record its call and return immediately (r0 unchanged unless given)."""
        self.stubs[addr & ~1] = (name, r0)

    # ---------------------------------------------------------------- hooks
    def _ret(self, r0=None):
        if r0 is not None:
            self.uc.reg_write(UC_ARM_REG_R0, r0)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def _code(self, uc, a, size, _):
        if a in self.watch:
            self.watch_hits.append((a, len(self.writes)))
        if a == RET:
            uc.emu_stop()
        elif a == STUB_IRQ:
            r0 = uc.reg_read(UC_ARM_REG_R0)
            self.calls.append(("irq_set", r0))
            self.irq_masked = (r0 & 0xC0) == 0xC0
        elif a == ALLOC:
            r0, r1, r2, r3 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
            self.calls.append(("alloc", r0, r2, r3))
            if self.alloc_result is None:
                self._ret(1)
            else:
                # distinct blocks: the first is the resident block; later ones (feature allocations at install)
                # follow it, 16-aligned
                addr = self.alloc_result + self.alloc_next
                self.alloc_next += (r2 + 15) & ~15
                uc.mem_write(r1, struct.pack(">I", addr))
                if self.resident is None and not self.static:
                    self.resident = (addr, r2)
                self.blocks.append((addr, r2))
                self._ret(0)
        elif a == FREE:
            self.calls.append(("free", uc.reg_read(UC_ARM_REG_R0)))
            self._ret(0)
        elif a == 0x8FF0E:
            self.calls.append(("mono_8ff0e", uc.reg_read(UC_ARM_REG_R0)))
            self._ret()
        elif a == 0x16D31C:
            r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            self.calls.append(("cc2_lut_16d31c", r0, r1, hashlib.sha256(bytes(uc.mem_read(r2, 0x2000))).hexdigest()))
            self._ret(0)
        elif a in self.stubs:
            name, r0 = self.stubs[a]
            self.calls.append((name, uc.reg_read(UC_ARM_REG_R0)))
            self._ret(r0)
        elif a in (0x26BAA, 0x289EC):
            self.calls.append(("ASSERT/HALT", a))
            uc.emu_stop()

    def _write(self, uc, access, addr, size, value, _):
        if STACK - 0x10000 <= addr < STACK + 0x100:
            return
        rec = (addr, size)
        self.writes.append(rec)
        if not self.irq_masked:
            self.writes_while_unmasked.append(rec)

    # ---------------------------------------------------------------- runs
    def thumb(self, pc, regs, stop=RET, count=2_000_000):
        uc = self.uc
        for r, v in regs.items():
            uc.reg_write(r, v)
        uc.reg_write(UC_ARM_REG_SP, STACK)
        uc.reg_write(UC_ARM_REG_LR, RET | 1)
        uc.reg_write(UC_ARM_REG_CPSR, (uc.reg_read(UC_ARM_REG_CPSR) & ~0xFF) | 0x13 | 0x20 | 0xC0)
        uc.emu_start(pc | 1, stop, count=count)
        return uc.reg_read(UC_ARM_REG_R0)

    def install(self):
        """Call the module entry like the loader does. Returns r0."""
        self.calls.clear()
        self.writes.clear()
        self.resident, self.alloc_next, self.blocks, self.watch_hits = self.static, 0, [], []
        self.writes_while_unmasked.clear()
        sentinels = {UC_ARM_REG_R4: 0xA4, UC_ARM_REG_R5: 0xA5, UC_ARM_REG_R6: 0xA6, UC_ARM_REG_R7: 0xA7}
        r0 = self.thumb(self.body_base + self.entry, {UC_ARM_REG_R0: SVC, UC_ARM_REG_R1: self.body_base,
                                                      UC_ARM_REG_R2: len(self.body), **sentinels})
        self.callee_saved_ok = all(self.uc.reg_read(r) == v for r, v in sentinels.items()) and \
            self.uc.reg_read(UC_ARM_REG_SP) == STACK
        return r0

    def outside_writes(self):
        """Writes outside the module body, the resident block and the stack (i.e. writes to the camera's RAM)."""
        res = self.resident or (0, 0)
        return [(a, s) for a, s in self.writes
                if not (self.body_base <= a < self.body_base + len(self.body)) and not (res[0] <= a < res[0] + res[1])
                and not any(b <= a < b + n for b, n in self.blocks)]

    def run_site(self, start, end, regs=None):
        self.calls.clear()
        self.mmio.clear()
        return self.thumb(start, regs or {}, stop=end)

    def read(self, a, n):
        return bytes(self.uc.mem_read(a, n))

    def u8(self, a):
        return self.uc.mem_read(a, 1)[0]
