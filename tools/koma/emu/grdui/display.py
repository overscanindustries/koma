"""Display device model: VEN registers (0x12020000), OSD DMA channel (0x1200c080), frame timer, PNG renderer.

Code basis (runtime addresses; class D unless noted):
  0x161f18   palette write: *(0x12020100 + i*4) = value, i in 1..63, value < 2^26
  0x161d30   RGB to palette value: (Y&0xff)<<16 | (Cb&0xff)<<8 | (Cr&0xff)
  0x161fa8   OSD config: base -> 0x1200c080 via 0x16f3b0; stride/4 -> 0x1200c088 bits 0-7
  0x22296    OSD enable: 0x1200c00c |= 4, 0x12020004 |= 2, latch 0x1202003c, 0x12020000 |= 0x10
  0x21736    VEN interrupt mask: 0x12020014 (1=masked); status 0x12020018 write-0-to-clear (0x21708)
  0x217d8    VEN interrupt handler on INTC line 2 (0x1c280..0x1c298); bit 0x400 runs the frame callback
  0x1fba14.. data table: page 0 0x08422800, page 1 0x0842f280, width 0x168, height 0x120, stride 0xb4
  0xd46a0    fill: nibble repeated into each byte, so 2 pixels per byte, 4 bits per pixel
  0xd6fc4    font: bit 7 of the glyph goes to the top nibble of the first byte, so high nibble is the left pixel

The frame period is a modelling choice, not an observation: the panel's timing is unknown.
"""
from __future__ import annotations

import gzip
import struct

from ..grdemu.periph import Unknown

VEN_BASE = 0x12020000
DMA_BASE = 0x1200c000
PALETTE_OFF = 0x100
OSD_ENABLE_BIT = 2          # 0x12020004 bit 1, set by 0x22296, cleared by 0x222fa
OSD_DMA_ENABLE_BIT = 4      # 0x1200c00c bit 2, set by 0x22296, cleared by 0x222fa
FRAME_BIT = 0x400           # VEN status bit whose handler runs the frame callback (0x217d8)
FRAME_INSNS = 66_667        # about 16.7 firmware ticks at 4000 instructions per tick; modelling choice


class AccessLog:
    """Every access to the display registers, in order. Written gzip-compressed; counted per register."""

    def __init__(self, path=None):
        self.fh = gzip.open(path, "wt") if path else None
        self.counts = {}

    def add(self, vt, pc, rw, addr, size, value):
        key = (rw, addr)
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.fh:
            self.fh.write(f"{vt} {pc:08x} {rw} {addr:08x}/{size} {value:08x}\n")

    def close(self):
        if self.fh:
            self.fh.close()
            self.fh = None


class Ven(Unknown):
    """0x12020000. Registers are last-write storage with a record of every access, except: status +0x18 (set by the
    frame timer, write-0-to-clear per 0x21708) and INTC line 2 (asserted while status & ~mask is non-zero per 0x217d8
    and 0x21736). Registers with no modelled behaviour keep the UNMODELLED marker from grdemu.periph.Unknown."""
    name = "ven"
    BASE = VEN_BASE
    MODELLED = {0x14, 0x18, 0x10, 0x04, 0x00, 0x3c} | {PALETTE_OFF + i * 4 for i in range(64)}

    def __init__(self, machine, intc, log):
        super().__init__(machine)
        self.intc = intc
        self.log = log
        self.status = 0
        self.mask = 0xffffffff
        self.frames = 0
        self.palette_writes = 0
        self.latches = 0
        # Reset value. 0x161fa8 and 0x161830 read-modify-write +0x10 and 0x1705d4 asserts each of its three fields is in
        # 3..0x1d, so hardware must reset to something in range. The value itself is a modelling choice.
        self.regs[self.BASE + 0x10] = 0x000f0f0f
        machine.schedule(FRAME_INSNS, self.frame)

    def note(self, addr):
        off = addr - self.BASE
        if off in self.MODELLED:
            return ""
        return super().note(addr)

    def _line(self):
        self.intc.set_line(2, bool(self.status & ~self.mask & 0xfff))

    def frame(self):
        self.frames += 1
        self.status |= FRAME_BIT
        self._line()
        self.m.schedule(FRAME_INSNS, self.frame)

    def read(self, addr, size):
        off = addr - self.BASE
        if off == 0x18:
            value = self.status
        elif off == 0x14:
            value = self.mask
        elif off in self.MODELLED:
            value = self.regs.get(addr, 0)
        else:
            value = super().read(addr, size)
        self.log.add(self.m.vt, self.m.uc.reg_read(_PC), "R", addr, size, value)
        return value

    def write(self, addr, size, value):
        off = addr - self.BASE
        self.log.add(self.m.vt, self.m.uc.reg_read(_PC), "W", addr, size, value)
        if off == 0x18:
            self.status &= value
            self._line()
            return
        if off == 0x14:
            self.mask = value
            self._line()
            return
        if PALETTE_OFF <= off < PALETTE_OFF + 0x100:
            self.palette_writes += 1
        if off == 0x3c:
            self.latches += 1
        super().write(addr, size, value)

    def palette(self):
        return [self.regs.get(self.BASE + PALETTE_OFF + i * 4) for i in range(64)]


class SdramCtl(Unknown):
    """0x11000000. 0x16ea8c reads +4 through 0x172c3c: bits 4-7 select capacity (4 = 64 MiB), bit 8 doubles it. Value 0 means
    2 MiB, and the display driver's own assertion then fires and suspends the task (sdl_ven_api.c line 563, handler 0x289ec).
    Value here matches grdemu's RAM_SIZE, which is itself an inference. Real register value unknown."""
    name = "sdramctl"
    BASE = 0x11000000
    CAPACITY = 0x40

    def note(self, addr):
        return "" if addr == self.BASE + 4 else super().note(addr)

    def read(self, addr, size):
        if addr == self.BASE + 4:
            return self.CAPACITY
        return super().read(addr, size)


class DmaPage(Unknown):
    """0x1200c000. Last-write storage with a record of every access. Only the OSD channel's meaning is used
    (+0x80 base, +0x88 stride, +0x0c enable). The other channels are not modelled: no transfer happens and no
    completion interrupt fires, and they keep the UNMODELLED marker."""
    name = "dma"
    BASE = DMA_BASE
    MODELLED = {0x0c, 0x80, 0x88, 0x8c}
    # Reset values, added only where the firmware's own driver assertion demanded one on read-modify-write. Each value
    # is the widest the assertion allows. Real reset values are unknown.
    RESET = {0x16c: 0x70f}      # 0x16fcfc: lnir 7 or 0xf (bits 0-3), pxir9 3 or 7 (bits 8-10)

    def __init__(self, machine, log):
        super().__init__(machine)
        self.log = log
        for off, value in self.RESET.items():
            self.regs[self.BASE + off] = value

    def note(self, addr):
        return "" if addr - self.BASE in self.MODELLED else super().note(addr)

    def read(self, addr, size):
        value = super().read(addr, size)
        self.log.add(self.m.vt, self.m.uc.reg_read(_PC), "R", addr, size, value)
        return value

    def write(self, addr, size, value):
        self.log.add(self.m.vt, self.m.uc.reg_read(_PC), "W", addr, size, value)
        super().write(addr, size, value)


from unicorn.arm_const import UC_ARM_REG_PC as _PC  # noqa: E402


def ycbcr_to_rgb(y, cb, cr):
    """Screenshot conversion. Cb and Cr are signed: 0x161d30 builds them from R,G,B with no offset before masking, and
    the firmware's own table 0x1fba30 holds neutral colours as Cb=Cr=0 (0xda0000, 0x6d0000). Y scale comes from the same
    function (0.859 = 219/255). The panel's real response is unknown, so colours are approximate."""
    cb = cb - 256 if cb > 127 else cb
    cr = cr - 256 if cr > 127 else cr
    y = y / 0.859
    r = y + cr / 0.6263 * 0.701 * 0 + cr / 0.6263
    g = y - 0.1942 * cb / 0.4957 - 0.5094 * cr / 0.6263
    b = y + cb / 0.4957
    return tuple(max(0, min(255, int(round(v)))) for v in (r, g, b))


def decode_palette(raw):
    out = []
    for value in raw:
        if value is None:
            out.append(None)
        else:
            out.append(ycbcr_to_rgb((value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff))
    return out


STANDIN_VIDEO_PLANE = (64, 64, 72)   # what shows through transparent OSD pixels: no sensor, no live video


def render_page(mem, base, stride, width, height, raw_palette):
    """Render a 4-bit OSD page to an RGB PIL image. Index 0 has no palette register (0x161f18 rejects it), treated as
    transparent over STANDIN_VIDEO_PLANE (inference). Unset palette entries render magenta so they can't be mistaken for
    firmware colour."""
    from PIL import Image
    pal = decode_palette(raw_palette)
    lut = []
    for i in range(16):
        if i == 0:
            lut.append(STANDIN_VIDEO_PLANE)
        else:
            lut.append(pal[i] if pal[i] is not None else (255, 0, 255))
    data = bytes(mem(base, stride * height))
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        row = data[y * stride:(y + 1) * stride]
        for x in range(width):
            b = row[x >> 1] if (x >> 1) < len(row) else 0
            px[x, y] = lut[(b >> 4) if not x & 1 else (b & 0xf)]
    return img


def enabled_state(ven: Ven, dma: DmaPage):
    ctl1 = ven.regs.get(VEN_BASE + 4, 0)
    en = dma.regs.get(DMA_BASE + 0x0c, 0)
    return {
        "ven_osd_enable": bool(ctl1 & OSD_ENABLE_BIT),
        "dma_osd_enable": bool(en & OSD_DMA_ENABLE_BIT),
        "osd_base": dma.regs.get(DMA_BASE + 0x80),
        "osd_stride_words": (dma.regs.get(DMA_BASE + 0x88, 0) & 0xff),
    }


def blank_check(mem, base, stride, height):
    """Histogram of nibble values on a page: used to report honestly whether anything was drawn."""
    data = bytes(mem(base, stride * height))
    hist = [0] * 16
    for b in data:
        hist[b >> 4] += 1
        hist[b & 0xf] += 1
    return hist


_ = struct  # kept for callers that decode register dumps
