"""One UI session: boots the unmodified official firmware under grdemu with the display model and scripted key input."""
from __future__ import annotations

import collections
import itertools
import json
import re
import tempfile
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_SP

from ..grdemu.boot import Emu, Scenario
from ..grdemu.stubs import Cpu1

from . import display as D

RUNS = Path(tempfile.gettempdir()) / "koma-ui-runs"   # callers (koma smoke) set their own

DRAW_ELEMENT = 0x6ba80          # draws one text or image element into a layer buffer
SETTINGS, SETTINGS_SIZE = 0x1b000, 0x1000  # block saved at power-off (0x32b8c, I), settings read by menus
CONSOLE = 0x26baa               # firmware console printf
SINK = 0x51740                  # key event sink: writes injected array 0x2b9dd8, read by 0x50eb0
POLL = 0x50eb0                  # key poll, runs in tOpeMain (0x8a74c)
EMIT = 0x517dc                  # key event emitter
APP_STATE = 0x1bd1c0            # resolved by 0x4fd1e; 3 is the dial position used here (derived from 0x4fd1e and 0x51158)
DISP_STATE = 0x1a8d40           # 2 selects LCD in 0x2328c (inference from callers)
OSD_PAGE = 0x08422800           # table 0x1fba18
OSD_W, OSD_H, OSD_STRIDE = 360, 240, 0xb4

STANDINS = [
    {"name": "CPU1", "address": "0xca524", "what": "All replies fabricated (grdemu.stubs.Cpu1). Battery, temperature, "
     "lens state, power and mode dial are invented values; CPU1 firmware is not in our image."},
    {"name": "BOOT_TRIGGER", "address": "REQ_GET_BOOT_TRIGGER 0xf201", "what": "Reply set to 0x20 (bit 5, 0x51a64), which "
     "starts a record boot with the default dial. Meaning on hardware unconfirmed."},
    {"name": "MODE_DIAL", "address": "key table 0x18108c codes 0-2", "what": "MODE0+MODE2 from grdemu default: position 1, "
     "app state 3 (0x51158, 0x4fd1e). Which printed label this is: unknown."},
    {"name": "VEN_FRAME", "address": "VEN 0x12020018 bit 0x400, INTC line 2", "what": f"Frame interrupt every {D.FRAME_INSNS} "
     "virtual instructions. Real panel timing unknown."},
    {"name": "VIDEO_PLANE", "address": "VEN video layer", "what": "Not rendered. Transparent OSD index 0 shows a flat grey "
     f"{D.STANDIN_VIDEO_PLANE}, because there is no sensor and no live image."},
    {"name": "SDRAM_CAPACITY", "address": "0x11000004", "what": "Reads 0x40, 64 MiB, matching grdemu's RAM size. Without it the "
     "display driver asserts and suspends the task. Real value unknown."},
    {"name": "VEN_RESET_VALUE", "address": "0x12020010", "what": "Resets to 0x000f0f0f so 0x1705d4's range assertion passes. "
     "Real reset value unknown."},
    {"name": "PALETTE_DECODE", "address": "0x161d30", "what": "YCbCr with signed Cb and Cr, per the constants in 0x161d30, and "
     "bits 24-25 ignored. The panel's real color response is unknown, so screenshot colors are approximate."},
    {"name": "KEY_INJECT", "address": "sink 0x51740, array 0x2b9dd8", "what": "The harness calls the real sink 0x51740 in tOpeMain's "
     "context at the top of real poll 0x50eb0. Everything from the sink on is real firmware. Physical keys, the qOPE message "
     "that normally carries the call, and CPU1 transport are bypassed."},
    {"name": "CARD", "address": "0x55dc0 family", "what": "grdemu.stubs.FileLayer: host directory, in-memory writes, no FAT."},
    {"name": "SENSOR_LENS_AF_RELEASE", "address": "none", "what": "Absent. No CCD, AFE, lens, AF, flash or shutter is modelled; "
     "registers at 0x12014000 and others stay unmodelled and logged."},
    {"name": "FLASH_CONTENT", "address": "OneNand model", "what": "Monitor, adjustment and extended-data blocks erased; "
     "real device contents unknown. Any firmware default loaded because of this is listed in the report."},
]


def read_element(kind, data):
    """Text form of an element's data. Kind 1: pairs up to a zero pair (0x6ba80 stops at a zero first byte)."""
    if kind == 1:
        out = bytearray()
        for i in range(0, len(data) - 1, 2):
            if data[i] == 0:
                break
            out += data[i:i + 2]
        return out.hex()
    if kind == 0 or kind == 3:
        return data.split(b"\0", 1)[0].hex()
    return data[:2].hex()


def format_printf(m, fmt_addr):
    """Minimal printf for the console log: %s %d %x %X %c %ld %lx with width, read from r1..r3 then the caller's stack."""
    fmt = bytes(m.uc.mem_read(fmt_addr, 200)).split(b"\0", 1)[0].decode("cp932", "replace")
    args = [m.r(1), m.r(2), m.r(3)]
    sp = m.uc.reg_read(UC_ARM_REG_SP)
    out, i, n = [], 0, 0

    def arg():
        nonlocal n
        n += 1
        if n <= 3:
            return args[n - 1]
        return m.u32(sp + (n - 4) * 4)
    for match in re.finditer(r"%([-0 ]*)(\d*)(?:\.\d+)?l*([sdxXuc%])", fmt):
        out.append(fmt[i:match.start()])
        i = match.end()
        flags, width, conv = match.groups()
        if conv == "%":
            out.append("%")
            continue
        value = arg()
        if conv == "s":
            s = m.cstr(value, 120) if 0x1000 <= value < 0x4000000 else f"<{value:#x}>"
        elif conv == "c":
            s = chr(value & 0xff)
        elif conv in "xX":
            s = format(value, conv)
        else:
            s = str(value - (1 << 32) if value >> 31 and conv == "d" else value)
        w = int(width or 0)
        out.append(s.ljust(w) if "-" in flags else s.rjust(w, "0" if "0" in flags else " "))
    out.append(fmt[i:])
    return "".join(out)


class Session:
    def __init__(self, name, boot_trigger=0x20, files=None, keys=None, max_insns=int(6e9), log_access=True,
                 card_present=True, gpio_inputs=None, image=None):
        self.name = name
        self.out = RUNS / name
        self.out.mkdir(parents=True, exist_ok=True)
        Cpu1.REPLIES = dict(Cpu1.REPLIES)
        Cpu1.REPLIES[0xf201] = bytes([boot_trigger])
        self.card = None                     # card files stay in memory (Scenario.card_files)
        self.log = D.AccessLog(self.out / "display-access.log.gz" if log_access else None)
        self.key_events = []
        self.ui_events = collections.Counter()
        self.pending_inject = []
        self.inputs = []
        self.console = []
        self.elements = []
        self._settings_last = None
        self.asserts = []
        self._saved_lr = None
        self.polls = 0
        self.shots = []
        kw = {}
        if gpio_inputs is not None:
            kw["gpio_inputs"] = gpio_inputs
        self.sc = Scenario(name=name, image=image, card_files=dict(files or {}), card_present=card_present,
                           keys=keys or {},
                           max_insns=max_insns, extra_hooks=[self._install], **kw)
        self.emu = Emu(self.sc)
        self.m = self.emu.m
        # Bound the run's memory. The trace hash still covers every event and MMIO access.
        self.m.pc_log_limit = 1_000_000
        self.emu.trace.keep_mmio = 200_000

    def _install(self, e):
        m = e.m
        # grdemu.core.Machine.schedule sorts (due, len, fn) and raises TypeError when due and len both tie, because
        # functions don't compare. grdemu isn't ours to edit, so this instance gets a tie-free sequence number.
        seq = itertools.count(1 << 32)

        def schedule(delay, fn):
            m.timers.append((m.vt + delay, next(seq), fn))
            m.timers.sort()
            m.next_due = m.timers[0][0]
        m.schedule = schedule
        e.ven = self.ven = D.Ven(m, e.intc, self.log)
        m.map_model(D.VEN_BASE, 0x1000, e.ven)
        e.dma = self.dma = D.DmaPage(m, self.log)
        m.map_model(D.DMA_BASE, 0x1000, e.dma)
        e.sdramctl = D.SdramCtl(m)
        m.map_model(D.SdramCtl.BASE, 0x1000, e.sdramctl)
        m.at(CONSOLE, self._on_console)
        m.at(DRAW_ELEMENT, self._on_element)
        m.at(POLL, self._on_poll)
        m.at(EMIT, self._on_emit)

    def _on_console(self, m):
        """Firmware's own console printf 0x26baa. Formatted here from r0..r3 and the stack; output is only recorded."""
        text = format_printf(m, m.r(0))
        self.console.append((m.vt, text))
        m.event("CONSOLE", text)
        if "Assertion failed" in text:
            self.asserts.append((m.vt, text))

    def _on_element(self, m):
        """0x6ba80(layer, element). Element: +0 x, +2 y, +4 kind, +8 data pointer. Kind 1 is a string of (font page, glyph)
        byte pairs, kind 0 a single-byte string, kind 2 and 7 image indices (read from the element, see report)."""
        p = m.r(1)
        if not p:
            return
        x, y, kind, ptr = m.u16(p), m.u16(p + 2), m.u8(p + 4), m.u32(p + 8)
        data = bytes(m.uc.mem_read(ptr, 64)) if 0x1000 < ptr < 0x4000000 else b""
        self.elements.append(dict(vt=m.vt, layer=m.r(0), x=x, y=y, kind=kind, ptr=ptr, data=read_element(kind, data)))

    def _on_poll(self, m):
        """Top of the real poll 0x50eb0, in tOpeMain's context (0x8a8b8 calls the sink from the same task). When a key is
        queued, make a real call to sink 0x51740 and come back here. r0-r3 are free at this void function's entry."""
        if self._saved_lr is not None:
            m.uc.reg_write(UC_ARM_REG_LR, self._saved_lr)
            self._saved_lr = None
        else:
            self.polls += 1
        if self.pending_inject:
            code, action = self.pending_inject.pop(0)
            m.event("INPUT", f"sink 0x51740 call code={code} action={action}")
            self.inputs.append((m.vt, code, action))
            self._saved_lr = m.uc.reg_read(UC_ARM_REG_LR)
            m.uc.reg_write(UC_ARM_REG_R0, code)
            m.uc.reg_write(UC_ARM_REG_R1, action)
            m.uc.reg_write(UC_ARM_REG_LR, POLL | 1)
            m.uc.reg_write(UC_ARM_REG_PC, SINK | 1)

    def _on_emit(self, m):
        self.key_events.append((m.vt, m.r(0), m.r(1)))
        m.event("KEYEVENT", f"code={m.r(0)} action={m.r(1)}")

    # ---- script steps
    def tap(self, code):
        self.pending_inject.append((code, 3))

    def hold(self, code):
        self.pending_inject.append((code, 1))

    def release(self, code):
        self.pending_inject.append((code, 0))

    def sync_dial(self):
        """Same as a script's `keyset 0 1`: sink code 1, virtual dial = real dial, so injection doesn't resolve the app
        state to 6."""
        self.pending_inject.append((1, 0))

    def run_until(self, vt):
        """Advance to virtual instruction vt. Uses grdemu's run loop unmodified; clears its horizon stop for the next step."""
        m = self.m
        if m.stop_reason and not m.stop_reason.startswith("horizon"):
            return m.stop_reason
        m.stop_reason = None
        return m.run(vt)

    def run_for(self, n):
        return self.run_until(self.m.vt + n)

    def state(self):
        m = self.m
        info = D.enabled_state(self.ven, self.dma)
        info.update(vt=m.vt, app_state=m.u32(APP_STATE), disp_state=m.u32(DISP_STATE), frames=self.ven.frames,
                    polls=self.polls)
        return info

    def page(self):
        """Current scan-out page from the OSD DMA base, or the first page when none is latched."""
        base = self.dma.regs.get(D.DMA_BASE + 0x80)
        return base | 0x08000000 if base else OSD_PAGE

    def settings_diff(self):
        """Bytes of the settings block changed since the last call: [(addr, old, new)]."""
        cur = bytes(self.m.uc.mem_read(SETTINGS, SETTINGS_SIZE))
        last, self._settings_last = self._settings_last, cur
        if last is None:
            return []
        return [(hex(SETTINGS + i), last[i], cur[i]) for i in range(SETTINGS_SIZE) if last[i] != cur[i]]

    def shot(self, label):
        m = self.m
        idx = len(self.shots)
        path = self.out / f"{idx:02d}-{label}.png"
        img = D.render_page(m.uc.mem_read, self.page(), OSD_STRIDE, OSD_W, OSD_H, self.ven.palette())
        scanning = self.state()["ven_osd_enable"] and self.state()["disp_state"] == 2
        if not scanning:
            # 0x222fa cleared the OSD enable, or 0x2328c left the LCD state: the page stays in RAM but isn't shown.
            img = img.point(lambda _: 0)
        img.save(path)
        hist = D.blank_check(m.uc.mem_read, self.page(), OSD_STRIDE, OSD_H)
        rec = dict(self.state(), label=label, path=str(path), nibble_histogram=hist,
                   drawn_pixels=sum(hist[1:]), scanout_enabled=scanning, settings_diff=self.settings_diff(), elements=self.elements,
                   simulated=True)
        self.elements = []
        self.shots.append(rec)
        return rec

    def report(self, extra=None):
        rep = self.emu.report(self.m.stop_reason, 0)
        self.log.close()
        rep.update(shots=self.shots, standins=STANDINS, key_events=self.key_events[-400:], inputs=self.inputs, console=self.console,
                   assertions=self.asserts,
                   display_register_counts={f"{rw} {a:#x}": n for (rw, a), n in sorted(self.log.counts.items())},
                   ven_unmodelled_reads={f"{a:#x}": n for a, n in sorted(self.ven.read_counts.items())},
                   dma_unmodelled_reads={f"{a:#x}": n for a, n in sorted(self.dma.read_counts.items())},
                   ven_frames=self.ven.frames, polls=self.polls, simulated=True,
                   claim="Simulated. Does not establish camera behaviour. See standins.")
        if extra:
            rep.update(extra)
        (self.out / "report.json").write_text(json.dumps(rep, indent=1, default=str) + "\n")
        return rep
