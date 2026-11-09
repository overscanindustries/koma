"""Assemble a machine for one boot scenario and report milestones. Offline. Simulated results only."""
from __future__ import annotations

import hashlib
import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_SP

from .core import Machine, Trace
from .periph import FlashFaults, Gpio, Intc, OneNand, SysTimer, Unknown, Usart
from .stubs import Cpu1, FileLayer

ORIGINAL_SHA256 = "de00e03d563fd1bdd0a2a925761b1a029993425049841119e3e83eef990c783b"
HEADER = 0x200
BLOCK_MAIN, BLOCK_MAIN_INFO, BLOCK_MON = 0xd, 0x3c, 0
SECTORS = 128

# Milestones are (name, address, evidence). Reaching one means the emulated CPU executed that address.
MILESTONES = [
    ("reset_code_entered", 0x1c054, "reset handler"),
    ("reset_code_done", 0x1c1bc, "bx to C init 0x1cf58 at end of reset code"),
    ("bss_init_done", 0x1c0e4, "BSS loop 0x1c0d8..0x1c0e0 exited"),
    ("main_task_entered", 0x281e8, "MainCtrl task entry, RTOS running a task"),
    ("startup_entered", 0x1e7ac, "startup function called by MainCtrl"),
    ("opemain_task_entered", 0x8a3b8, "tOpeMain task entry"),
    ("main_info_read", 0x62b46, "main info read from flash"),
    ("update_gate", 0x1eb22, "key combination test that selects the update path"),
    ("update_gate_entered_path", 0x1eb36, "update path taken: key held or main info invalid"),
    ("update_skip_path", 0x1ec74, "skip path after update section"),
    ("quickver_check", 0x1eb74, "open /ATA1/QUICKVER.UP"),
    ("update_function", 0x5d18c, "SD update check and flash driver"),
    ("update_scan", 0x278d4, "scan QRFLECT0..9"),
    ("update_checksum", 0x275cc, "per-file checksum 0x275cc"),
    ("update_flash_all", 0xd6dec, "flash each accepted file"),
    ("flash_one_file", 0xd6c58, "flash one file"),
    ("flash_adjust_file", 0xd6a62, "apply type 5 adjustment text"),
    ("flash_erase_main_info", 0x63c68, "erase main info block"),
    ("flash_program_main", 0x63b88, "program main region"),
    ("flash_program_main_info", 0x63c80, "program main info"),
    ("script_gate_checked", 0x55844, "SCRIPT.MG1 gate setter region (0x39a44 was a file offset)"),
    ("script_entry", 0x65108, "script interpreter entry"),
    ("post_startup", 0x282cc, "first call after startup returns"),
    ("power_off_requested", 0x32b80, "firmware power-off path 0x32b80"),
    ("power_off_requested_b", 0x32b74, "firmware power-off/reboot path 0x32b74"),
    ("abort_handler", 0x174978, "data abort handler"),
    ("undef_handler", 0x174984, "undefined instruction handler"),
    ("pabt_handler", 0x17496c, "prefetch abort handler"),
    ("error_logger", 0x5de6c, "error logger 0x5de6c (fatal only for some codes)"),
]


@dataclass
class Scenario:
    name: str = "stock-no-card-files"
    image: bytes | None = None              # complete vendor-format container (required: the caller supplies it)
    flash_image: bytes | None = None        # what the emulated flash contains; default same as image
    run_image_from_flash: bool = True       # payload loaded to RAM from flash model main region
    sdcard: str | None = None               # host directory that is the fake /ATA1
    card_files: dict | None = None          # /ATA1 files from memory (name -> bytes); nothing written to disk
    card_present: bool = True
    keys: dict = field(default_factory=dict)        # CPU1 HKEY index -> state
    main_info_valid: bool = True
    power_cut_after: int | None = None
    max_insns: int = int(3e8)
    tick_insns: int = 4000
    trace_path: str | None = None
    alias_low_ram: bool = True
    # Pin levels. Shutter (0x36, 0x37), dial and misc inputs are read as active-low by 0x50fc0, so idle high.
    # Pin 8 is the suspected USB sense (0x256ea(8), see script-analysis): low means no USB. All unobserved.
    gpio_inputs: dict = field(default_factory=lambda: {8: 0, 3: 1, 0x30: 1, 0x31: 1, 0x35: 1, 0x36: 1, 0x37: 1,
                                                       0x38: 1, 0x3a: 1, 0x3b: 1, 0x3c: 1})
    extra_hooks: list = field(default_factory=list)
    confirm_update: bool = False            # model a user answering the on-screen update prompt with OK
    # OneNAND state the app inherits from the unseen Monitor, and injected failures. See periph.OneNand.MODEL_CHOICES.
    flash_initial_lock: str = "locked"
    flash_handoff_config: int = 0x40e0
    flash_faults: FlashFaults | None = None


class Emu:
    def __init__(self, sc: Scenario):
        self.sc = sc
        if sc.image is None:
            raise ValueError("Scenario.image is required (koma: firmware.loader_image())")
        image = sc.image
        self.image = image
        flash_image = sc.flash_image if sc.flash_image is not None else image
        payload = flash_image[HEADER:]
        self.trace = Trace(path=sc.trace_path)
        self.m = m = Machine(payload, self.trace, tick_insns=sc.tick_insns, alias_low_ram=sc.alias_low_ram)
        m.default_model = self.unknown = Unknown(m)
        self.intc = Intc(m)
        m.intc = self.intc
        m.map_model(Intc.BASE, 0x1000, self.intc)
        self.timer = SysTimer(m, self.intc)
        m.map_model(SysTimer.BASE, 0x1000, self.timer)
        self.gpio = Gpio(m, self.intc, sc.gpio_inputs)
        m.map_model(Gpio.BASE, 0x1000, self.gpio)
        self.usart = Usart(m, self.intc)
        m.map_model(Usart.BASE, 0x1000, self.usart)
        self.flash = OneNand(m, self.gpio, initial_lock=sc.flash_initial_lock, handoff_config=sc.flash_handoff_config,
                             faults=sc.flash_faults)
        m.map_model(OneNand.BASE, 0x20000, self.flash)
        self.flash.power_cut_after = sc.power_cut_after
        self._seed_flash(flash_image)
        self.reached = {}
        self.reached_mmio = {}      # milestone -> number of MMIO accesses seen when it fired
        for name, addr, why in MILESTONES:
            m.at(addr, lambda mm, n=name, a=addr: self._hit(n, a))
        m.at(0x28800, lambda mm: mm.event("TASK", f"create name={mm.cstr(mm.r(0))!r} entry={mm.r(1):#x}"))
        m.at(0x5de6c, self._fatal)
        self.cpu1 = Cpu1(m, sc.keys)
        self.files = FileLayer(m, sc.sdcard, sc.card_present, preload=sc.card_files)
        if sc.confirm_update:
            m.at(0x5d330, self._dialog)
            m.at(0x5d3b8, self._dialog_wait)
        for fn in sc.extra_hooks:
            fn(self)

    def _seed_flash(self, image):
        """Flash contents laid out as firmware programs them: main at block 0xd (0x63b88) and the 512-byte
        header at block 0x3c sector 0x7e (0x63c80). Replacement table in block 1, as 0x6325c/0x631a4 would build
        for a part with no bad blocks. Blocks 0 (monitor), 0x3d (adjustment), 0x3e.. (extended data) and /IROM
        are erased: their real contents are device-specific and unknown."""
        fl = self.flash
        fl.load_image(BLOCK_MAIN * SECTORS, image[HEADER:])
        if self.sc.main_info_valid:
            fl.load_image(BLOCK_MAIN_INFO * SECTORS + 0x7e, image[:HEADER])
        rep = bytes([0xff, 0xff, ord("E"), ord("R"), 0xff, ord("P")]) + b"\xff" * 10
        fl.spare[1 * SECTORS * fl.SPARE:1 * SECTORS * fl.SPARE + 16] = rep
        fl.apply_factory_marks()
        fl.cold_reset()         # BootRAM takes block 0 as seeded: erased, because the Monitor is not in our image
        fl.apply_handoff()
        self.m.event("SEED", "flash: main@0xd, info@0x3c+0x7e, REP table@block1; "
                             "monitor, adjustment, extended data, IROM erased (contents unknown)")
        self.m.event("ASSUME", f"OneNAND hand-off: blocks {fl.initial_lock}, F221h={fl.handoff_config:#x}. "
                               "Monitor lock actions are unseen, so both are parameters")

    def _dialog(self, m):
        """User input for the prompt in 0x5d330: 0x5d434 returns 1 when ENTER is released after RIGHT (key 0xd)."""
        if m.r(0) != 0x18:
            return
        self._dialog_armed = True

    def _dialog_wait(self, m):
        """Queue wait at 0x5d3b8: firmware is now listening for keys."""
        if not getattr(self, "_dialog_armed", False):
            return
        self._dialog_armed = False
        m.event("DIALOG", "update prompt id=0x18 waiting; modelled user presses RIGHT then ENTER")
        k = self.cpu1.set_key
        d = 12000000
        for key in ("UP", "TELE"):       # 0x513c0 tracks one key at a time, so a held combo masks new presses
            k(key, 0)
        m.schedule(d, lambda: k("RIGHT", 1))
        m.schedule(2 * d, lambda: k("RIGHT", 0))
        m.schedule(3 * d, lambda: k("ENTER", 1))
        m.schedule(4 * d, lambda: k("ENTER", 0))

    def _fatal(self, m):
        m.event("ERRLOG", f"code={m.r(0):#x} arg={m.r(2):#x} lr={m.uc.reg_read(UC_ARM_REG_LR):#x}")

    def _hit(self, name, addr):
        if name not in self.reached:
            self.reached[name] = self.m.vt
            self.reached_mmio[name] = len(self.m.trace.mmio)
            self.m.event("MILESTONE", f"{name} addr={addr:#x}")

    def run(self):
        started = time.time()
        reason = self.m.run(self.sc.max_insns)
        self.trace.close()
        return self.report(reason, time.time() - started)

    def report(self, reason, seconds):
        unknown_reads = {f"{a:#x}": n for a, n in sorted(self.unknown.read_counts.items())}
        return {
            "scenario": self.sc.name,
            "input_sha256": hashlib.sha256(self.image).hexdigest(),
            "stop_reason": reason,
            "virtual_insns": self.m.vt,
            "seconds": round(seconds, 1),
            "timer_ticks": self.timer.ticks,
            "trace_sha256": self.trace.sha.hexdigest(),
            "milestones": self.reached,
            "milestones_mmio_index": self.reached_mmio,
            "tasks": [t for _, _, k, t in self.trace.events if k == "TASK"],
            "flash_ops": self.flash.ops,
            "flash_model": self.flash.describe(),
            "file_ops": self.files.ops,
            "cpu1_requests": len(self.cpu1.log),
            "cpu1_unmodelled": sorted(self.cpu1.unmodelled),
            "unknown_mmio_read_registers": unknown_reads,
            "stubs": self.cpu1.stub_notes + self.files.stub_notes,
            "simulated": True,
        }
