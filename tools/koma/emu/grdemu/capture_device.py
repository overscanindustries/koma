"""Opt-in CC1 sensor-timing stand-in for the shutter path. Nothing imports this by default.

Replaces only the logged last-write storage of page 0x12014000 when a caller maps it. Native evidence:

  +0x1c  read-only to firmware. The only accessor is 0x170a30, called only by 0x947e4 and 0x94858; writer accessor
         0x170a3c has no callers. Both pollers wait for bit 31, then test bits 23:16 against a window: 0x14..0xbe,
         or 0x0f..0x38 for two capture states.
  +0x24  low 12 bits are "vcycle" (0x166598 asserts vcycle <= MAX_COUNT_V, stores through 0x170a6c).
  +0x40  status. Line 3 ISR 0x23950 writes it with bit 0 clear through 0x170b14, its only caller, then runs callbacks.
  +0x44  bit 0 is the peripheral enable pair of INTC mask bit 3: 0x9e058/0x9733a clear both, 0x9e0ec sets both.

Supplied, not measured: that bits 23:16 are the low byte of a vertical count running 0..vcycle; that bit 31 means the
count is valid; that bit 0 of +0x40 latches when the count wraps; the frame length in virtual instructions; reset value
1 for +0x44 bit 0. No sensor, pixel, DMA, exposure or shutter behaviour is modelled here. A native pass through these
waits is not evidence that a picture was taken.
"""
from __future__ import annotations

from .periph import Unknown

MODES = ("passive", "running", "invalid", "out-of-window", "irq-suppressed")
FRAME_INSNS = 66_667       # the same modelling choice as grdui's VEN_FRAME. Physical frame timing is unknown
OUT_OF_WINDOW_COUNT = 0xff  # outside both native windows, 0x14..0xbe and 0x0f..0x38


class Cc1Timing(Unknown):
    name = "cc1-timing-optional"
    BASE = 0x12014000
    SIZE = 0x1000
    LINE = 3
    COUNTER, VCYCLE, STATUS, MASK = 0x1c, 0x24, 0x40, 0x44
    VD = 1
    MAX_EVENTS = 2000

    def __init__(self, machine, intc, mode="running", frame_insns=FRAME_INSNS):
        super().__init__(machine)
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
        if type(frame_insns) is not int or frame_insns <= 0:
            raise ValueError("frame_insns must be a positive integer")
        self.intc = intc
        self.mode = mode
        self.frame_insns = frame_insns
        if mode != "passive":
            self.regs[self.BASE + self.MASK] = self.VD
        self.pending = 0
        self.frames = 0
        self.acks = 0
        self.lines_raised = 0
        self.counter_reads = 0
        self.events = []
        self.events_truncated = 0
        machine.schedule(frame_insns, self._frame)

    def _record(self, kind, **fields):
        if len(self.events) < self.MAX_EVENTS:
            self.events.append(dict(vt=self.m.vt, event=kind, **fields))
        else:
            self.events_truncated += 1

    def note(self, addr):
        off = addr - self.BASE
        if self.mode != "passive" and off in (self.COUNTER, self.STATUS, self.MASK):
            return "OPTIONAL cc1 timing stand-in; supplied, not measured"
        return super().note(addr)

    def vcycle(self):
        return self.regs.get(self.BASE + self.VCYCLE, 0) & 0xfff

    def count(self):
        """Position in the current frame, scaled to 0..vcycle. Only meaningful once firmware has programmed vcycle."""
        cycle = self.vcycle()
        return (self.m.vt % self.frame_insns) * (cycle + 1) // self.frame_insns

    def _frame(self):
        self.frames += 1
        if self.vcycle() and self.mode not in ("passive", "irq-suppressed"):
            self.pending |= self.VD
            self._line()
        self.m.schedule(self.frame_insns, self._frame)

    def _line(self):
        on = bool(self.pending & ~self.regs.get(self.BASE + self.MASK, 0) & self.VD)
        if on and not self.intc.lines >> self.LINE & 1:
            self.lines_raised += 1
        self.intc.set_line(self.LINE, on)

    def read(self, addr, size):
        off = addr - self.BASE
        if self.mode == "passive":      # identical to Unknown: last-write storage, so a run can select a mode later
            return super().read(addr, size)
        if off == self.COUNTER:
            self.counter_reads += 1
            self.read_counts[addr] = self.read_counts.get(addr, 0) + 1
            if self.mode == "invalid":
                return 0
            if self.mode == "out-of-window":
                return 0x80000000 | OUT_OF_WINDOW_COUNT << 16
            return 0x80000000 | (self.count() & 0xfff) << 16
        value = super().read(addr, size)
        if off == self.STATUS:
            value = (value & ~self.VD) | (self.pending & self.VD)
        return value

    def write(self, addr, size, value):
        off = addr - self.BASE
        if self.mode == "passive":
            super().write(addr, size, value)
            return
        if off == self.COUNTER:
            self.write_counts[addr] = self.write_counts.get(addr, 0) + 1
            self._record("counter-write-ignored", value=hex(value))
            return
        if off == self.STATUS:
            if self.pending & self.VD and not value & self.VD:
                self.acks += 1
            self.pending &= value | ~self.VD
            super().write(addr, size, value & ~self.VD)
            self._line()
            return
        super().write(addr, size, value)
        if off == self.MASK:
            self._line()

    def description(self):
        return {
            "name": self.name, "mode": self.mode, "frame_insns": self.frame_insns, "vcycle": self.vcycle(),
            "frames": self.frames, "line3_assertions": self.lines_raised, "native_acks": self.acks,
            "counter_reads": self.counter_reads, "events": self.events, "events_truncated": self.events_truncated,
            "limits": ["optional model; not a sensor, DMA, exposure or shutter model",
                       "vertical-count meaning, bit 31 meaning, wrap-position interrupt and frame length are supplied",
                       "no callback is called directly; firmware's IRQ entry, ISR and semaphores run unmodified",
                       "no physical camera comparison exists"],
        }


def install(emu, mode="running", frame_insns=FRAME_INSNS):
    """Map the model over page 0x12014000 on one emulator. Call from a Scenario extra_hook, before the first instruction."""
    model = Cc1Timing(emu.m, emu.intc, mode=mode, frame_insns=frame_insns)
    emu.m.map_model(Cc1Timing.BASE, Cc1Timing.SIZE, model)
    emu.cc1_timing = model
    return model
