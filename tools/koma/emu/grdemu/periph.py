"""Peripheral models. Each model cites the firmware code that justifies its behaviour.

Rule: a register with no model is served by Unknown, which logs every read with a marker and
keeps a per-register counter. Read value: last written value, else 0, and the record says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class Unknown:
    name = "unknown"

    def __init__(self, machine):
        self.m = machine
        self.regs = {}
        self.read_counts = {}
        self.write_counts = {}

    def note(self, addr):
        return "UNMODELLED" if addr not in self.regs else "UNMODELLED-lastwrite"

    def read(self, addr, size):
        self.read_counts[addr] = self.read_counts.get(addr, 0) + 1
        return self.regs.get(addr, 0)

    def write(self, addr, size, value):
        self.write_counts[addr] = self.write_counts.get(addr, 0) + 1
        self.regs[addr] = value


class Intc:
    """0x12000000. Justification: IRQ entry 0x1c214 reads pending at +0x10, mask at +0x08, and dispatches
    bits 1..17 then 0 of pending&~mask (14+4 bits masked by shifts at 0x1c254/0x1c258). Reset code writes
    mask +0x08=0x3fffe and +0x0c=0x3ffff. 0x1c208 writes +0x04=1 then jumps to 0 (soft reset path).
    Model: a line is pending while a source holds it; +0x08 is a plain mask (1=masked)."""
    name = "intc"
    BASE = 0x12000000

    def __init__(self, machine):
        self.m = machine
        self.regs = {}
        self.lines = 0
        self.mask = 0xffffffff

    def note(self, addr):
        return "" if (addr - self.BASE) in (0x08, 0x0c, 0x10) else "intc-other"

    def pending(self):
        return self.lines & ~self.mask & 0x3ffff

    def read(self, addr, size):
        off = addr - self.BASE
        if off == 0x10:
            return self.lines & 0x3ffff
        if off == 0x08:
            return self.mask
        return self.regs.get(off, 0)

    def write(self, addr, size, value):
        off = addr - self.BASE
        if off == 0x08:
            self.mask = value
        elif off == 0x04 and value == 1:
            self.m.stop("soft-reset-request write 0x12000004=1 (0x1c208 path); boot stage not modelled")
        else:
            self.regs[off] = value

    def set_line(self, bit, on=True):
        self.lines = (self.lines | (1 << bit)) if on else (self.lines & ~(1 << bit))


class SysTimer:
    """0x12010000. Justification: IRQ line 0 handler 0x1f36c reads +0x14 pending and +0x10 mask via accessors at
    0x16f074/0x16f05c, writes ~pending to +0x14 (0x16f080) i.e. write-0-to-clear, and on bit 0 calls Nucleus
    timer 0x1e238 and tick counter 0x1613ec. Reset code writes +0x00=0xfd20 (reload) and +0x10=0xe (only bit 0
    unmasked). Model: bit 0 fires every tick_insns virtual instructions; bits 1..3 never fire until armed.
    Rate is virtual, not the 0xfd20 divisor."""
    name = "systimer"
    BASE = 0x12010000

    def __init__(self, machine, intc):
        self.m = machine
        self.intc = intc
        self.regs = {0x10: 0xffffffff}
        self.pend = 0
        self.ticks = 0
        machine.schedule(machine.tick_insns, self.tick)

    def note(self, addr):
        return "" if (addr - self.BASE) in (0x00, 0x10, 0x14) else "systimer-other"

    def tick(self):
        self.ticks += 1
        self.pend |= 1
        self._line()
        self.m.schedule(self.m.tick_insns, self.tick)

    def _line(self):
        self.intc.set_line(0, bool(self.pend & ~self.regs.get(0x10, 0xffffffff) & 0xf))

    def read(self, addr, size):
        off = addr - self.BASE
        if off == 0x14:
            return self.pend
        return self.regs.get(off, 0)

    def write(self, addr, size, value):
        off = addr - self.BASE
        if off == 0x14:
            self.pend &= value
        else:
            self.regs[off] = value
        self._line()


class Gpio:
    """0x12030000. Justification: pin accessors 0x256c2/0x256d6 read data at +0x20 (pins 0..31) and +0x24
    (pins 32..63) via 0x16ed40/0x16ed58; 0x2547c shows read-modify-write of output levels on +0x20.
    OneNAND driver 0x64524 polls +0x24 bit 25 for completion, so pin 57 is modelled as the OneNAND INT line
    (inference). OneNand sets onenand_int from its INT bit, IOBE and INTpol.
    External inputs are configuration, listed in the report: not observed on a camera."""
    name = "gpio"
    BASE = 0x12030000

    def __init__(self, machine, intc, inputs=None):
        self.m = machine
        self.intc = intc
        self.regs = {}
        self.inputs = dict(inputs or {})   # pin -> level; pins named here ignore the latch
        self.onenand_int = False

    def note(self, addr):
        return "" if (addr - self.BASE) in (0x20, 0x24) else "gpio-config"

    def level(self, pin):
        word = self.read(self.BASE + (0x20 if pin < 32 else 0x24), 4)
        return (word >> (pin & 31)) & 1

    def read(self, addr, size):
        off = addr - self.BASE
        value = self.regs.get(off, 0)
        if off in (0x20, 0x24):
            first = 0 if off == 0x20 else 32
            for pin, lvl in self.inputs.items():
                if first <= pin < first + 32:
                    bit = 1 << (pin - first)
                    value = (value | bit) if lvl else (value & ~bit)
            if off == 0x24:
                value = (value | (1 << 25)) if self.onenand_int else (value & ~(1 << 25))
        return value

    def write(self, addr, size, value):
        self.regs[addr - self.BASE] = value


@dataclass
class FlashFaults:
    """Injected flash failures, all off by default. Pass the same object to later boots: failed cells stay failed.
    ecc maps linear sector -> (main, spare), each 0 none, 1 correctable, 2 uncorrectable (p.49). A fault is a weak
    cell, so it survives program and erase (model choice ECC_FAULT_PERSISTS)."""
    factory_bad: dict = field(default_factory=dict)       # block -> marked page, 0 or 1 (p.85)
    bad_ops: str = "fail"                                 # model choice BAD_BLOCK_OPS: "fail" or "erases_marker"
    erase_fail: set = field(default_factory=set)          # blocks
    program_fail: set = field(default_factory=set)        # (block, sector)
    fail_after_ops: int | None = None                     # every program or erase once this many ops are recorded
    ecc: dict = field(default_factory=dict)
    lock_tight: set = field(default_factory=set)          # blocks handed over locked-tight: the Monitor is unseen


class OneNand:
    """0x10800000. Samsung OneNAND256 KFG5616x1A, per datasheet Ver 1.1 (2005-08-12, SHA-256 9c4e024f...3213); page
    numbers below are that document's. The part itself is derived from the driver and no marking was read. M-die was
    not read. Geometry from firmware table 0x177978: device 0x14 or 0x15, 0x200 blocks, 0x40 pages, 2 sectors per page.
    Registers per driver 0x64524..0x64f04 and pp.30-31: id at 0x1e000..4, block 0x1e200, page/sector 0x1e20e, buffer
    0x1e400, cmd 0x1e440, config 0x1e442, status 0x1e480, int 0x1e482, start block 0x1e498, reserved 0x1e49a (p.49),
    write protection status 0x1e49c, ECC status 0x1fe00; DataRAM 0x10800400, spare RAM 0x10810020. Completion: INT pin
    via GPIO, polled at 0x64524. Status values per the output-mode table p.46. Commands p.39: 0x00 load, 0x13 load
    spare, 0x80 program, 0x1a program spare, 0x94 erase, 0x23 unlock, 0x2a lock, 0x2c lock-tight, 0xf0 core reset,
    0xf3 hot reset. Other datasheet commands are not issued by the driver and stop the run as unmodelled.
    Modelled: cold reset to all locked with BootRAM loaded from block 0 (pp.56-57, 101), per-block lock states (pp.57-59),
    F24Eh (pp.49, 57), interrupt bits (pp.47-48), IOBE and INTpol on the INT pin (pp.41-42), injected invalid blocks,
    program, erase and ECC failures (pp.46, 49, 85-86), busy time per op (p.93, reported only: virtual time ignores it).
    Not modelled: wear, read disturb, torn programs at a power cut, ECC code bytes in spare (pp.28, 84), the ECC
    position registers FF01h..FF04h, OTP, copy-back, multi-block erase, synchronous reads.
    Anything the datasheet does not state is listed in MODEL_CHOICES and copied into each report."""
    name = "onenand"
    BASE = 0x10800000
    SECTOR, SPARE = 512, 16
    BLOCKS, SECTORS_PER_BLOCK = 0x200, 128
    UNLOCKED, LOCKED, TIGHT = 0, 1, 2
    WP_BITS = {UNLOCKED: 4, LOCKED: 2, TIGHT: 1}    # F24Eh US, LS, LTS (p.49)
    DEFAULT_CONFIG = 0x40c0                         # F221h default (p.40)
    ECC_BYPASS, IOBE, INTPOL = 0x100, 0x20, 0x40    # F221h bits (pp.41-42)
    # Controller status output modes (p.46)
    LOAD_FAIL, LOAD_LOCK, PROGRAM_FAIL, PROGRAM_LOCK = 0x2400, 0x6400, 0x1400, 0x5400
    ERASE_FAIL, ERASE_LOCK, INVALID_COMMAND = 0x0c00, 0x4c00, 0x0400
    # Interrupt status on completion: INT plus RI, WI, EI or RSTI (pp.47-48). Lock commands set INT only.
    INT_LOAD, INT_PROGRAM, INT_ERASE, INT_LOCK, INT_RESET = 0x8080, 0x8040, 0x8020, 0x8000, 0x8010
    # (typ, max) in microseconds (p.93): one or two sectors for load and program.
    TIMES_US = {"load1": (23, 35), "load2": (25, 40), "program1": (205, 720), "program2": (220, 750),
                "erase": (2000, 3000), "lock": (0.5, 0.7)}
    MODEL_CHOICES = {
        "HANDOFF_LOCK_STATE": "Lock state when the app starts is a parameter. Cold reset locks every block (p.57), but "
                              "the Monitor runs first and its lock actions are unseen.",
        "HANDOFF_CONFIG_IOBE": "F221h at app start is 40E0h: default 40C0h (p.40) with IOBE set. The app's only F221h "
                               "writes change bit 8 and it polls the INT pin, so an earlier stage probably set IOBE "
                               "(inference). A parameter.",
        "HANDOFF_INT": "F241h at app start is 8080h, the cold reset default (p.47). The Monitor's last state is unseen.",
        "INT_HIZ_READS_LOW": "With IOBE clear the INT pin is High-Z (p.42). The GPIO reads it as 0; the board is unseen.",
        "WP_COMMAND_STATUS_ZERO": "F240h reads 0 after a lock command; p.46 lists no mode for these, and the app does "
                                  "not check them (table 0x1779cc). Commands on a locked-tight block have no effect "
                                  "(p.59) and raise no error, which p.59 does not state either way.",
        "LOCK_TIGHT_NEEDS_LOCKED": "Lock-tight on an unlocked block is ignored, per p.59. pp.57-58 suggest otherwise, so "
                                   "the datasheet is inconsistent. The driver never issues the command.",
        "BOOT_BUFFER_PROGRAM_INVALID": "Program from a BootRAM buffer (BSA[3]=0) gives Invalid Command. Not stated.",
        "BOOT_PARTITION_COMMANDS": "Writes to BootRAM never change it (p.57). 00F0h resets (p.53). 00E0h and 0090h "
                                   "(pp.52-53) are logged and ignored, not run. Other values are ignored (p.52).",
        "BAD_BLOCK_OPS": "Program or erase of a factory-marked block. The datasheet prohibits it (pp.85, 90) and says the "
                         "marker is erasable 'in most cases' (p.85), with no outcome stated. Option: 'fail' gives the "
                         "fail status; 'erases_marker' lets the erase clear the marker and the block then works.",
        "ERASE_FAIL_KEEPS_CELLS": "A failed erase leaves the block's contents as they were. Not stated.",
        "PROGRAM_FAIL_TEARS_HALF": "A failed program leaves the first half of each main sector programmed and spare "
                                   "untouched. Not stated; p.86 says other pages are unaffected.",
        "ECC_FAULT_PERSISTS": "An injected ECC fault stays on its sector through program and erase. Not stated.",
        "ECC_BYPASS_REPORTS_ZERO": "With ECC bypassed, FF00h is 'invalid' (p.84). The model reads 0 and raises no load "
                                   "failure.",
        "ECC_UNCORRECTABLE_DATA_RAW": "An uncorrectable load still fills the buffer, with the first byte inverted. Not "
                                      "stated, beyond Load Fail (p.46).",
        "NOP_COUNTED_NOT_ENFORCED": "NOP 2 per sector (p.93) has no stated failure, so excess programs are counted, and "
                                    "cells still clear bits only. The same goes for programming without an erase.",
    }

    def __init__(self, machine, gpio, device_id=0x14, initial_lock="locked", handoff_config=0x40e0, faults=None):
        if initial_lock not in ("locked", "unlocked"):
            raise ValueError(f"initial_lock must be locked or unlocked, not {initial_lock!r}")
        self.m = machine
        self.gpio = gpio
        self.device_id = device_id
        self.initial_lock = initial_lock
        self.handoff_config = handoff_config
        self.faults = faults if faults is not None else FlashFaults()
        total = self.BLOCKS * self.SECTORS_PER_BLOCK
        self.data = bytearray(b"\xff" * (total * self.SECTOR))
        self.spare = bytearray(b"\xff" * (total * self.SPARE))
        self.ram = bytearray(b"\xff" * 0xc00)       # boot + DataRAM0 + DataRAM1
        self.sram = bytearray(b"\xff" * 0x60)       # boot + buffer spare
        self.regs = {}
        self.status = 0
        self.int_reg = 0
        self.ecc_status = 0
        self.wp_status = 0
        self.lock_state = bytearray(self.BLOCKS)
        self.ops = []           # (kind, block, sector, count, sha16)
        self.op_status = []     # F240h value for each entry of ops
        self.op_busy_us = []    # cumulative (typ, max) busy time at the end of each entry of ops
        self.lock_ops = []      # (command, block, state after)
        self.busy_us = [0.0, 0.0]
        self.nop = {}           # linear sector -> program cycles since its erase
        self.nop_violations = 0
        self.program_on_unerased = 0
        self.commands_without_int_clear = 0
        self.boot_partition_writes = 0
        self.unmodelled_boot_commands = 0
        self.power_cut_after = None
        self.cold_reset()
        self.apply_handoff()

    def note(self, addr):
        return "" if addr < 0x1081e000 or (addr & 0xfffff) in (
            0x1e000, 0x1e002, 0x1e004, 0x1e200, 0x1e20e, 0x1e400, 0x1e440, 0x1e442,
            0x1e480, 0x1e482, 0x1e498, 0x1e49a, 0x1e49c, 0x1fe00) else "onenand-other"

    def load_image(self, sector, data):
        self.data[sector * self.SECTOR:sector * self.SECTOR + len(data)] = data

    def cold_reset(self):
        """Power-up: registers at their defaults, every block locked (p.57), and 1 KB of block 0 page 0 copied to
        BootRAM with its spare (pp.56, 101). ECC status updates during that load (p.49)."""
        self.regs = {0x1e442: self.DEFAULT_CONFIG}
        self.status = 0
        self.int_reg = self.INT_LOAD
        self.lock_state[:] = bytes([self.LOCKED]) * self.BLOCKS
        self.ram[0:0x400] = self.data[0:0x400]
        self.sram[0:0x20] = self.spare[0:0x20]
        self.ecc_status = 0
        for i in range(2):
            self.ecc_status |= self._ecc_field(i, i)
        self._latch_wp(0)
        self._int_pin()

    def warm_reset(self):
        """RP pin: registers to defaults, array locked, tight blocks locked, BufferRAM kept (pp.56-59). IOBE only falls
        at a cold reset (p.42). The firmware cannot be seen driving RP, so only tests call this."""
        iobe = self.regs.get(0x1e442, 0) & self.IOBE
        self.regs = {0x1e442: self.DEFAULT_CONFIG | iobe}
        self.status = 0
        self.int_reg = self.INT_RESET
        self.lock_state[:] = bytes([self.LOCKED]) * self.BLOCKS
        self._latch_wp(0)
        self._int_pin()

    def apply_handoff(self):
        """State the app inherits from the unseen Monitor. See MODEL_CHOICES HANDOFF_*."""
        if self.initial_lock == "unlocked":
            self.lock_state[:] = bytes([self.UNLOCKED]) * self.BLOCKS
        for block in self.faults.lock_tight:
            self.lock_state[block] = self.TIGHT
        self.regs[0x1e442] = self.handoff_config
        self._latch_wp(self.regs.get(0x1e200, 0) & 0x1ff)
        self._int_pin()

    def apply_factory_marks(self):
        """Marker: non-FFFFh at the first spare word of sector 0, in the first or second page (p.85)."""
        for block, page in self.faults.factory_bad.items():
            at = (block * self.SECTORS_PER_BLOCK + page * 2) * self.SPARE
            self.spare[at:at + 2] = b"\x00\x00"

    def describe(self):
        return {
            "part": "OneNAND256 KFG5616x1A, datasheet Ver 1.1 (derived from the driver; no marking read)",
            "initial_lock": self.initial_lock, "handoff_config": hex(self.handoff_config),
            "choices": sorted(self.MODEL_CHOICES), "bad_ops": self.faults.bad_ops,
            "busy_us": {"typ": round(self.busy_us[0], 1), "max": round(self.busy_us[1], 1)},
            "failed_ops": [[i, *self.ops[i][:3], hex(s)] for i, s in enumerate(self.op_status) if s][:200],
            "lock_ops": len(self.lock_ops), "lock_ops_below_0xd": sorted({b for _, b, _ in self.lock_ops if b < 0xd}),
            "still_unlocked": [b for b in range(self.BLOCKS) if self.lock_state[b] == self.UNLOCKED][:16],
            "nop_violations": self.nop_violations, "program_on_unerased": self.program_on_unerased,
            "commands_without_int_clear": self.commands_without_int_clear,
            "boot_partition_writes": self.boot_partition_writes,
            "unmodelled_boot_commands": self.unmodelled_boot_commands,
        }

    def _sector_addr(self):
        block = self.regs.get(0x1e200, 0) & 0x1ff
        fsa = self.regs.get(0x1e20e, 0)
        # 0x64838: fpa_fsa = (sector&0x7e)<<1 | (sector&1)
        return block, ((fsa >> 1) & 0x7e) | (fsa & 1)

    def _buffer(self):
        b = self.regs.get(0x1e400, 0)
        # 0x64838: ((buf&1)<<2 | sector&1 | 8) << 8 | count&1
        bsa = (b >> 8) & 0xf
        bsc = b & 1
        unit = ((bsa >> 2) & 1) * 2 + (bsa & 1)  # data at 0x10800400+unit*0x200 per 0x64930/0x64a74
        count = bsc if bsc else 2
        return unit, count

    def _boot_buffer_selected(self):
        return not (self.regs.get(0x1e400, 0) >> 8) & 8     # BSA[3] (p.38)

    def _latch_wp(self, block):
        # F24Eh updates when FBA is set and when a write protection command is entered (p.57)
        self.wp_status = self.WP_BITS[self.lock_state[block]]

    def _int_pin(self):
        config = self.regs.get(0x1e442, 0)
        bit = bool(self.int_reg & 0x8000)
        self.gpio.onenand_int = bool(config & self.IOBE) and bit == bool(config & self.INTPOL)

    def read(self, addr, size):
        off = addr - self.BASE
        if off < len(self.ram):
            return self._mem_read(self.ram, off, size)
        if 0x10000 <= off < 0x10000 + len(self.sram):
            return self._mem_read(self.sram, off - 0x10000, size)
        if off == 0x1e000:
            return 0xec
        if off == 0x1e002:
            return self.device_id
        if off == 0x1e004:
            return 0
        if off == 0x1e480:
            return self.status
        if off == 0x1e482:
            return self.int_reg
        if off == 0x1e49c:
            return self.wp_status
        if off == 0x1fe00:
            return self.ecc_status
        return self.regs.get(off, 0)

    @staticmethod
    def _mem_read(buf, off, size):
        return int.from_bytes(buf[off:off + size], "big")

    def write(self, addr, size, value):
        off = addr - self.BASE
        if off < 0x400:
            self._boot_partition_write(off, value)
            return
        if off < len(self.ram):
            self.ram[off:off + size] = value.to_bytes(size, "big")
            return
        if 0x10000 <= off < 0x10020:
            self._boot_partition_write(off, value)
            return
        if 0x10020 <= off < 0x10000 + len(self.sram):
            self.sram[off - 0x10000:off - 0x10000 + size] = value.to_bytes(size, "big")
            return
        if off == 0x1e482:
            self.int_reg &= value      # writing 0 clears a bit (pp.47-48)
            self._int_pin()
            return
        if off == 0x1e440:
            self._command(value)
            return
        if off in (0x1e480, 0x1e49c, 0x1fe00):      # read-only (pp.43, 49). 0x64838 writes FF00h anyway
            return
        if off == 0x1e442:
            value = value & ~0xf                    # reserved and BWPS bits read as fixed zero (pp.40, 42)
        self.regs[off] = value
        if off == 0x1e200:
            self._latch_wp(value & 0x1ff)
        if off == 0x1e442:
            self._int_pin()

    def _boot_partition_write(self, off, value):
        self.boot_partition_writes += 1
        if value == 0xf0:
            self._hot_reset()
        elif value in (0xe0, 0x90):
            self.unmodelled_boot_commands += 1
            self.m.event("FLASH", f"UNMODELLED-BOOT-COMMAND {value:#x} at {off:#x}, ignored")

    def _hot_reset(self):
        """Registers to defaults, lock states kept (pp.39, 57). IOBE only falls at a cold reset (p.42)."""
        iobe = self.regs.get(0x1e442, 0) & self.IOBE
        self.regs = {0x1e442: self.DEFAULT_CONFIG | iobe}
        self._done(0, self.INT_RESET)

    def _done(self, err=0, interrupt=INT_LOCK):
        self.status = err
        self.int_reg = interrupt
        self._int_pin()

    def _spend(self, key):
        typ, top = self.TIMES_US[key]
        self.busy_us[0] += typ
        self.busy_us[1] += top

    def _record(self, kind, block, sector, count, payload=b""):
        import hashlib
        pc = self.m.uc.reg_read(15 + 0) if False else 0
        digest = hashlib.sha256(payload).hexdigest()[:16] if payload else "-"
        self.ops.append((kind, block, sector, count, digest))
        self.op_status.append(0)
        self.op_busy_us.append((self.busy_us[0], self.busy_us[1]))
        self.m.event("FLASH", f"{kind} block={block:#x} sector={sector:#x} count={count} sha={digest}")

    def _ecc_field(self, lin, i):
        main, spare = self.faults.ecc.get(lin, (0, 0))
        return ((main << 2) | spare) << (4 * i)     # ERm and ERs fields of FF00h (p.49)

    def _wear_failed(self):
        limit = self.faults.fail_after_ops
        return limit is not None and len(self.ops) >= limit

    def _block_is_marked_bad(self, block):
        return block in self.faults.factory_bad and self.faults.bad_ops == "fail"

    def _program_fails(self, block, sector, count):
        return (self._block_is_marked_bad(block) or self._wear_failed()
                or any((block, sector + i) in self.faults.program_fail for i in range(count)))

    def _erase_fails(self, block):
        return self._block_is_marked_bad(block) or self._wear_failed() or block in self.faults.erase_fail

    def _load(self, cmd, lin, unit, count):
        if self._boot_buffer_selected():
            self._done(self.LOAD_LOCK, self.INT_LOAD)
            return
        self._spend("load1" if count == 1 else "load2")
        bypass = bool(self.regs.get(0x1e442, 0) & self.ECC_BYPASS)
        ecc = 0
        for i in range(count):
            if cmd == 0x00:
                chunk = bytearray(self.data[(lin + i) * self.SECTOR:(lin + i + 1) * self.SECTOR])
                if not bypass and self.faults.ecc.get(lin + i, (0, 0))[0] == 2:
                    chunk[0] ^= 0xff
                self.ram[0x400 + (unit + i) * 0x200:0x400 + (unit + i + 1) * 0x200] = chunk
            self.sram[0x20 + (unit + i) * 0x10:0x20 + (unit + i + 1) * 0x10] = self.spare[
                (lin + i) * self.SPARE:(lin + i + 1) * self.SPARE]
            if not bypass:
                ecc |= self._ecc_field(lin + i, i)
        self.ecc_status = ecc
        self._done(self.LOAD_FAIL if ecc & 0xaa else 0, self.INT_LOAD)   # Load Fail: an ER field is 10 (p.46, note 1)

    def _program(self, cmd, block, sector, lin, unit, count):
        kind = "PROGRAM" if cmd == 0x80 else "PROGRAM_SPARE"
        if self._boot_buffer_selected():
            self._done(self.INVALID_COMMAND, self.INT_PROGRAM)
            return
        payload = b""
        err = 0
        if self.lock_state[block] != self.UNLOCKED:
            err = self.PROGRAM_LOCK
        else:
            self._spend("program1" if count == 1 else "program2")
            fails = self._program_fails(block, sector, count)
            for i in range(count):
                n = lin + i
                self.nop[n] = self.nop.get(n, 0) + 1
                if self.nop[n] > 2:
                    self.nop_violations += 1
                if cmd == 0x80:
                    chunk = bytes(self.ram[0x400 + (unit + i) * 0x200:0x400 + (unit + i + 1) * 0x200])
                    cur = self.data[n * self.SECTOR:(n + 1) * self.SECTOR]
                    if cur != b"\xff" * self.SECTOR:
                        self.program_on_unerased += 1
                    keep = self.SECTOR // 2 if fails else self.SECTOR
                    self.data[n * self.SECTOR:n * self.SECTOR + keep] = bytes(
                        a & b for a, b in zip(cur[:keep], chunk))   # NAND: program clears bits only
                    payload += chunk
                if not fails:
                    sp = bytes(self.sram[0x20 + (unit + i) * 0x10:0x20 + (unit + i + 1) * 0x10])
                    cur = self.spare[n * self.SPARE:(n + 1) * self.SPARE]
                    self.spare[n * self.SPARE:(n + 1) * self.SPARE] = bytes(a & b for a, b in zip(cur, sp))
            if fails:
                err = self.PROGRAM_FAIL
        self._record(kind, block, sector, count, payload)
        self.op_status[-1] = err
        self._done(err, self.INT_PROGRAM)

    def _erase(self):
        block = self.regs.get(0x1e200, 0) & 0x1ff
        err = 0
        if self.lock_state[block] != self.UNLOCKED:
            err = self.ERASE_LOCK
        else:
            self._spend("erase")
            if self._erase_fails(block):
                err = self.ERASE_FAIL
            else:
                start = block * self.SECTORS_PER_BLOCK
                n = self.SECTORS_PER_BLOCK
                self.data[start * self.SECTOR:(start + n) * self.SECTOR] = b"\xff" * (n * self.SECTOR)
                self.spare[start * self.SPARE:(start + n) * self.SPARE] = b"\xff" * (n * self.SPARE)
                for s in range(start, start + n):
                    self.nop.pop(s, None)
                self.faults.factory_bad.pop(block, None)
        self._record("ERASE", block, 0, self.SECTORS_PER_BLOCK)
        self.op_status[-1] = err
        self._done(err, self.INT_ERASE)

    def _write_protect(self, cmd):
        block = self.regs.get(0x1e498, 0) & 0x1ff      # SBA[8:0] (p.48)
        state = self.lock_state[block]
        if state != self.TIGHT:     # only a cold or warm reset leaves locked-tight (p.59)
            if cmd == 0x23:
                state = self.UNLOCKED
            elif cmd == 0x2a:
                state = self.LOCKED
            elif state == self.LOCKED:
                state = self.TIGHT
        self.lock_state[block] = state
        self.lock_ops.append((cmd, block, state))
        self._latch_wp(block)
        self._spend("lock")
        self._done(0, self.INT_LOCK)

    def _command(self, cmd):
        block, sector = self._sector_addr()
        unit, count = self._buffer()
        lin = block * self.SECTORS_PER_BLOCK + sector
        if self.int_reg & 0x8000:      # p.39: commands are issued after writing 0 to INT
            self.commands_without_int_clear += 1
        if cmd in (0x00, 0x13):
            self._load(cmd, lin, unit, count)
        elif cmd in (0x80, 0x1a):
            self._program(cmd, block, sector, lin, unit, count)
        elif cmd == 0x94:
            self._erase()
        elif cmd in (0x23, 0x2a, 0x2c):
            self._write_protect(cmd)
        elif cmd == 0xf0:
            self._done(0, self.INT_RESET)
        elif cmd == 0xf3:
            self._hot_reset()
        else:
            self.m.event("FLASH", f"UNMODELLED-CMD {cmd:#x}")
            self.m.stop(f"onenand-unmodelled-command {cmd:#x}")
            return
        if self.power_cut_after is not None and len(self.ops) >= self.power_cut_after:
            self.m.stop(f"injected-power-cut after flash op {len(self.ops)}")


class Usart:
    """0x12024000 (ch0) / 0x12024200 (ch1), IRQ line 8. Justification: IRQ handler 0x23ef4 tests +0x8c bit0 and
    +0x00 bit0 per channel, reads RX at +0x40 (0x17284c), writes next TX at +0x60 (0x172858) and signals a HISR
    when the buffer ends; send path 0x240fc writes first byte then waits 1000 ticks. Model: TX completes after a
    fixed virtual delay, setting +0x00 bit0; RX read clears it. RX data returns 0xff with a marker because the
    attached LCD controller and any other serial peer are not modelled. Bytes sent are logged."""
    name = "usart"
    BASE = 0x12024000
    DELAY = 200

    def __init__(self, machine, intc):
        self.m = machine
        self.intc = intc
        self.regs = {}

    def note(self, addr):
        off = addr - self.BASE
        return "RX-NO-PEER" if off & 0x1ff == 0x40 else ""

    def _line(self):
        on = any(self.regs.get(ch, 0) & 1 and self.regs.get(ch + 0x8c, 0) & 1 for ch in (0, 0x200))
        self.intc.set_line(8, on)

    def read(self, addr, size):
        off = addr - self.BASE
        ch, reg = off & 0x200, off & 0x1ff
        if reg == 0x40:
            self.regs[ch] = self.regs.get(ch, 0) & ~1
            self._line()
            return 0xff
        return self.regs.get(off, 0)

    def write(self, addr, size, value):
        off = addr - self.BASE
        ch, reg = off & 0x200, off & 0x1ff
        if reg == 0x60:
            self.m.event("USART", f"ch{ch >> 9} tx={value & 0xff:#04x}")
            def done():
                self.regs[ch] = self.regs.get(ch, 0) | 1
                self._line()
            self.m.schedule(self.DELAY, done)
            return
        if reg == 0x00:
            # Software-writable bits kept; bit0 is the completion flag owned by this model.
            self.regs[off] = (value & ~1) | (self.regs.get(off, 0) & 1)
            return
        self.regs[off] = value
        self._line()
