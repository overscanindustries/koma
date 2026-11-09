"""CPU, memory map, exception model, virtual time and trace. Peripheral behaviour lives in periph.py.

Every assumption about the machine the firmware does not itself establish is listed in
ASSUMPTIONS and copied into each run report. Emulator output is simulated: it does not
establish camera behaviour.
"""
from __future__ import annotations

import array
import ctypes
import hashlib
import struct
from dataclasses import dataclass, field

from unicorn import (Uc, UcError, UC_ERR_INSN_INVALID, UC_ARCH_ARM, UC_MODE_ARM, UC_MODE_BIG_ENDIAN, UC_PROT_ALL,
                     UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_MEM_READ,
                     UC_HOOK_MEM_WRITE, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_FETCH_UNMAPPED)
from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0,
                               UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4,
                               UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_SP,
                               UC_ARM_REG_SPSR)

LOAD = 0x1c000
RAM_SIZE = 0x4000000           # inference: literals in .text span 0x08000000..0x0bffffff
RAM_ALIAS = 0x08000000
MMIO_BASE = 0x10000000
MMIO_SIZE = 0x09000000         # 0x10000000..0x18ffffff, every literal peripheral page seen in .text
SRAM_BASE = 0x12200000
SRAM_SIZE = 0x10000           # inference: 0x12200000..0x12201xxx literals; size unverified
MONITOR_LR = 0x0000fe10        # assumption: any address below 0xfc00+ standing in for the monitor
RETURN_TRAP = MONITOR_LR

MODE_FIQ, MODE_IRQ, MODE_SVC, MODE_ABT, MODE_UND, MODE_SYS = 0x11, 0x12, 0x13, 0x17, 0x1b, 0x1f
T_BIT, I_BIT, F_BIT = 0x20, 0x80, 0x40
VEC_UND, VEC_SWI, VEC_PABT, VEC_DABT, VEC_IRQ, VEC_FIQ = 1, 2, 3, 4, 6, 7

ASSUMPTIONS = [
    "Entry state: SVC mode, IRQ and FIQ masked, as forced by the first reset-code instructions (0x1c054).",
    "Monitor hand-off: LR=0xfe10 and the 16 bytes before it are zero. The firmware copies those 16 bytes "
    "to 0x1c044..0x1c050 and stores LR in 0x235964. Real values are unknown: the monitor is not in our image.",
    "Vectors 0x00..0x1c: not in our image. Modelled as jumps through the table 0x80..0x9c written by the "
    "reset code (inference from that table's layout; the stub bytes are unobserved).",
    "Low RAM is an alias of 0x08000000 (inference from buffers at 0x08010000 etc. lying beyond .bss; "
    "selectable, unverified on hardware).",
    "CPU core: Unicorn default ARM model, not an ARMv4T core. Unaligned LDR does not rotate; v4T "
    "non-interworking pop/ldr-pc semantics are not enforced.",
    "Virtual time: one timer tick per tick_insns translated-block instructions. Bus and peripheral timing absent.",
]


class EmuStop(Exception):
    """Raised by a model to end the run with an explicit reason."""


@dataclass
class Trace:
    """Deterministic event log. Hash covers every event, including those folded out of the text file."""
    path: str | None = None
    sha: "hashlib._Hash" = field(default_factory=hashlib.sha256)
    events: list = field(default_factory=list)      # (seq, vt, kind, text) for non-MMIO events
    mmio: list = field(default_factory=list)        # compact (vt, pc, rw, addr, size, value)
    keep_mmio: int = 3_000_000
    file_mmio_limit: int = 1_500_000     # text log stops listing MMIO past this; hash still covers all of it
    _file_mmio: int = 0
    fh: object = None
    _last: tuple | None = None
    _rep: int = 0
    seq: int = 0

    def __post_init__(self):
        if self.path:
            self.fh = open(self.path, "w")

    def _flush_rep(self):
        if self._rep and self.fh:
            self.fh.write(f"       ... repeated {self._rep} more times\n")
        self._rep = 0

    def event(self, vt: int, kind: str, text: str):
        self.seq += 1
        line = f"{vt:012d} {kind} {text}"
        self.sha.update(line.encode() + b"\n")
        self.events.append((self.seq, vt, kind, text))
        if self.fh:
            self._flush_rep()
            self._last = None
            self.fh.write(line + "\n")

    def access(self, vt, pc, rw, addr, size, value, note=""):
        key = (pc, rw, addr, size, value)
        line = f"{pc:08x} {rw} {addr:08x}/{size} {value:08x}{' ' + note if note else ''}"
        self.sha.update(line.encode() + b"\n")   # time excluded so polling length alone is visible in order
        if len(self.mmio) < self.keep_mmio:
            self.mmio.append((vt, pc, rw, addr, size, value))
        if self.fh:
            if key == self._last:
                self._rep += 1
            elif self._file_mmio < self.file_mmio_limit:
                self._file_mmio += 1
                self._flush_rep()
                self._last = key
                self.fh.write(f"{vt:012d} MMIO {line}\n")

    def close(self):
        if self.fh:
            self._flush_rep()
            self.fh.close()
            self.fh = None


def _swap(value: int, size: int) -> int:
    # Unicorn hands MMIO values byte-swapped within the access size in big-endian mode (measured).
    if size == 4:
        return struct.unpack("<I", struct.pack(">I", value & 0xffffffff))[0]
    if size == 2:
        return struct.unpack("<H", struct.pack(">H", value & 0xffff))[0]
    return value & 0xff


class Machine:
    def __init__(self, payload: bytes, trace: Trace, tick_insns: int = 4000, alias_low_ram: bool = True,
                 pc_log_limit: int = 30_000_000, monitor_block: bytes = bytes(16), *, cpu_model=None):
        from .cpu_profile import validate_cpu_model, apply_cpu_model
        validate_cpu_model(cpu_model)
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_BIG_ENDIAN)
        self.cpu_profile = apply_cpu_model(uc, cpu_model)
        self.trace = trace
        self.payload = payload
        self.tick_insns = tick_insns
        self.vt = 0                 # virtual instruction count
        self.stop_reason = None
        self.next_event = None      # (vt) earliest asynchronous work
        self.timers = []            # periph callbacks: (due_vt, fn)
        self.pc_log = array.array("I")
        self.pc_log_limit = pc_log_limit
        self.irq_lines = 0          # asserted at the controller, see periph.Intc
        self.intc = None
        self.pending_pc_redirect = None
        self.models = []            # (base, end, model)
        self.watch = {}
        self.page_model = {}
        self._irq_delivered = 0
        self.want_stop = False
        self.deliver_undef = False  # off by default: an undefined instruction ends the run, as before
        self.und_sp = None          # assumed SP_und. No code in the image sets one; the Monitor might (unknown)
        self.ram = ctypes.create_string_buffer(RAM_SIZE)
        uc.mem_map_ptr(0, RAM_SIZE, UC_PROT_ALL, self.ram)
        if alias_low_ram:
            uc.mem_map_ptr(RAM_ALIAS, RAM_SIZE, UC_PROT_ALL, self.ram)
        else:
            self._ram2 = ctypes.create_string_buffer(RAM_SIZE)
            uc.mem_map_ptr(RAM_ALIAS, RAM_SIZE, UC_PROT_ALL, self._ram2)
        uc.mem_write(LOAD, payload)
        uc.mem_write(MONITOR_LR - 16, monitor_block)
        # 0x12200000 is executed from (code copied there by 0x2b6a0 and the reboot stub 0x1c1e8), so it is RAM.
        uc.mem_map(SRAM_BASE, SRAM_SIZE, UC_PROT_ALL)
        uc.mmio_map(MMIO_BASE, SRAM_BASE - MMIO_BASE, self._mmio_read, None, self._mmio_write, None)
        low = SRAM_BASE + SRAM_SIZE
        uc.mmio_map(low, MMIO_BASE + MMIO_SIZE - low,
                    lambda u, o, sz, d: self._mmio_read(u, o + low - MMIO_BASE, sz, d), None,
                    lambda u, o, sz, v, d: self._mmio_write(u, o + low - MMIO_BASE, sz, v, d), None)
        uc.hook_add(UC_HOOK_BLOCK, self._on_block)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)
        uc.hook_add(UC_HOOK_INTR, self._on_intr)
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        uc.reg_write(UC_ARM_REG_CPSR, (cpsr & ~0xff) | MODE_SVC | I_BIT | F_BIT)
        uc.reg_write(UC_ARM_REG_LR, MONITOR_LR)
        self.pc = LOAD
        assumptions = list(ASSUMPTIONS)
        if self.cpu_profile['selection_mode'] == 'explicit':
            assumptions = [a if not a.startswith('CPU core:') else
                           f"CPU core: explicit Unicorn {cpu_model} model (identifier "
                           f"{self.cpu_profile['selected_model_id']}); physical CPU identity, caches and "
                           "instruction-level hardware equivalence remain unverified."
                           for a in assumptions]
        for name, text in (("assumption", a) for a in assumptions):
            trace.event(0, "ASSUME", text)

    # ---- helpers
    def r(self, n):
        return self.uc.reg_read(UC_ARM_REG_R0 + n) if n < 8 else None

    def u32(self, addr):
        return struct.unpack(">I", self.uc.mem_read(addr, 4))[0]

    def u16(self, addr):
        return struct.unpack(">H", self.uc.mem_read(addr, 2))[0]

    def u8(self, addr):
        return self.uc.mem_read(addr, 1)[0]

    def cstr(self, addr, limit=128):
        b = bytes(self.uc.mem_read(addr, limit))
        return b.split(b"\0", 1)[0].decode("latin1")

    def w32(self, addr, value):
        self.uc.mem_write(addr, struct.pack(">I", value & 0xffffffff))

    def event(self, kind, text):
        self.trace.event(self.vt, kind, text)

    def stop(self, reason: str):
        if self.stop_reason is None:
            self.stop_reason = reason
            self.event("STOP", reason)
        self.uc.emu_stop()

    def at(self, addr: int, fn):
        """Run fn(machine) when execution reaches addr (bounded code hook)."""
        self.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: fn(self), begin=addr, end=addr)

    def return_from_stub(self, r0=0):
        """Boundary stub: return to caller honoring LR's Thumb bit. Only used for recorded stubs."""
        self.uc.reg_write(UC_ARM_REG_R0, r0 & 0xffffffff)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def map_model(self, base, size, model):
        self.models.append((base, base + size, model))
        for page in range(base >> 12, (base + size + 0xfff) >> 12):
            self.page_model[page] = model

    # ---- MMIO dispatch. Unknown accesses are served by periph.Unknown, which records every one.
    def _mmio_read(self, uc, offset, size, _):
        addr = MMIO_BASE + offset
        model = self.page_model.get(addr >> 12, self.default_model)
        value = model.read(addr, size)
        self.trace.access(self.vt, uc.reg_read(UC_ARM_REG_PC), "R", addr, size, value, model.note(addr))
        return _swap(value, size)

    def _mmio_write(self, uc, offset, size, value, _):
        addr = MMIO_BASE + offset
        value = _swap(value, size)
        self.trace.access(self.vt, uc.reg_read(UC_ARM_REG_PC), "W", addr, size, value)
        self.page_model.get(addr >> 12, self.default_model).write(addr, size, value)

    # ---- time
    def _on_block(self, uc, addr, size, _):
        self.vt += (size >> 1) if size else 1
        if len(self.pc_log) < self.pc_log_limit:
            self.pc_log.append(addr)
        if self.want_stop or self.vt >= self.next_due:
            uc.emu_stop()

    def schedule(self, delay, fn):
        due = self.vt + delay
        self.timers.append((due, len(self.timers), fn))
        self.timers.sort()
        self.next_due = self.timers[0][0]

    # ---- faults: all fatal to the run, recorded, never swallowed
    def _on_unmapped(self, uc, access, addr, size, value, _):
        pc = uc.reg_read(UC_ARM_REG_PC)
        self.stop(f"unmapped-access kind={access} addr={addr:#x} size={size} value={value:#x} pc={pc:#x}")
        return False

    def _on_intr(self, uc, intno, _):
        pc = uc.reg_read(UC_ARM_REG_PC)
        self.event("EXC", f"intno={intno} pc={pc:#x} cpsr={uc.reg_read(UC_ARM_REG_CPSR):#x}")
        if intno == 2:      # SVC. Handler table entry written by reset code at 0x88.
            self._enter(VEC_SWI, MODE_SVC, pc)      # PC already past the instruction per Unicorn
        elif intno == 1 and self.deliver_undef:
            self._deliver_undefined(pc)
        else:
            self.stop(f"cpu-exception intno={intno} pc={pc:#x}")

    def _enter(self, vector, mode, lr):
        uc = self.uc
        old = uc.reg_read(UC_ARM_REG_CPSR)
        uc.reg_write(UC_ARM_REG_CPSR, (old & ~(0x1f | T_BIT)) | mode | I_BIT)
        uc.reg_write(UC_ARM_REG_SPSR, old)
        uc.reg_write(UC_ARM_REG_LR, lr)
        target = self.u32(0x80 + vector * 4)
        uc.reg_write(UC_ARM_REG_PC, target)
        return target

    def _deliver_undefined(self, pc):
        """Optional model (recovery tests): vector through the table entry at 0x84 written by reset, so the
        firmware's own handler 0x174984 runs. LR = next instruction, per the ARM exception rules (assumption for
        this core: Unicorn reports PC at the faulting instruction)."""
        cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
        step = 2 if cpsr & T_BIT else 4
        self.event("EXC", f"undefined pc={pc:#x} cpsr={cpsr:#x} delivered to the firmware handler")
        self._enter(VEC_UND, MODE_UND, pc + step)
        if self.und_sp is not None:
            self.uc.reg_write(UC_ARM_REG_SP, self.und_sp)

    def _maybe_irq(self):
        uc = self.uc
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        if cpsr & I_BIT or self.intc is None or not self.intc.pending():
            return False
        pc = uc.reg_read(UC_ARM_REG_PC)
        self._irq_delivered += 1
        self.pc = self._enter(VEC_IRQ, MODE_IRQ, pc + 4)
        return True

    # ---- main loop
    def run(self, max_insns: int):
        uc = self.uc
        self.next_due = self.timers[0][0] if self.timers else 1 << 62
        pc = self.pc
        thumb_entry = False
        while self.stop_reason is None:
            if self.vt >= max_insns:
                self.stop(f"horizon-reached insns={self.vt}")
                break
            self.want_stop = False
            while self.timers and self.timers[0][0] <= self.vt:
                _, _, fn = self.timers.pop(0)
                fn()
            self.next_due = min(self.timers[0][0] if self.timers else 1 << 62, max_insns)
            if self.stop_reason:
                break
            if self._maybe_irq():
                pc = self.pc
            else:
                pc = uc.reg_read(UC_ARM_REG_PC) if self.vt else self.pc
                if self.vt and uc.reg_read(UC_ARM_REG_CPSR) & T_BIT:
                    pc |= 1
            if (pc & ~1) == RETURN_TRAP:
                self.stop(f"returned-to-monitor-stub pc={pc:#x}")
                break
            try:
                uc.emu_start(pc, 0xffffffff)
            except UcError as err:
                fault_pc = uc.reg_read(UC_ARM_REG_PC)
                if self.deliver_undef and err.errno == UC_ERR_INSN_INVALID:
                    self._deliver_undefined(fault_pc)
                    continue
                self.stop(f"cpu-fault {err} pc={fault_pc:#x} cpsr={uc.reg_read(UC_ARM_REG_CPSR):#x}")
                break
        return self.stop_reason
