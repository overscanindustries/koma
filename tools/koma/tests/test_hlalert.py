"""hlalert unit-emu tests: the linked module installed by the real Koma installer under Unicorn on the 2.40 payload,
then the three hooked sites run with real stock code around them (0x2a090 -> 0x82370 -> 0x82f50 -> hooked
0x82e98 -> real bars; monitor-loop BL 0x95b04 -> real DISP getter 0x6e91c; element-14 BL 0x2a100). Stubbed: the ARM
flush 0x6bbfc, the ARM element-enable 0x6a564 (RTOS timer), and the stock plane-2 bars 0x837f0."""
from __future__ import annotations

import struct

import numpy as np
from _util import fw  # noqa: F401  (sets sys.path)
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from koma import features as F
from koma.features import hlalert as H
from koma.linker import link
from koma.unitemu import ALLOC, RES_POOL, Unit

HEAP = 0x3E0000
LIVE = 0x8470000
PB = 0x600000
ROW = 0xB4
PREFIX = "kf_hlalert__"


class U(Unit):
    """The second pool allocation (the feature's heap) gets its own address; heap_fail makes only that one fail."""

    def __init__(self, *a, heap_fail=False, **kw):
        self.heap_calls, self.heap_fail = [], heap_fail
        super().__init__(*a, **kw)

    def _code(self, uc, a, size, _):
        if a == ALLOC and self.resident is not None:
            self.heap_calls.append(uc.reg_read(UC_ARM_REG_R2))
            if not self.heap_fail:
                uc.mem_write(uc.reg_read(UC_ARM_REG_R1), struct.pack(">I", HEAP))
            self._ret(1 if self.heap_fail else 0)
            return
        super()._code(uc, a, size, _)


def live_scene():
    h, w = 240, 672
    y, x = np.mgrid[0:h, 0:w].astype(float)
    img = 120 + 140 * np.clip(1 - y / 110, 0, 1)
    img = np.where(y > 150, 45 + 20 * np.sin(x / 23.0), img)
    img = np.where(((x - 470) ** 2 + (y - 45) ** 2) < 28 ** 2, 255, img)
    img = np.where((x > 90) & (x < 210) & (y > 120) & (y < 200), 252, img)
    img = np.where((x > 260) & (x < 380) & (y > 120) & (y < 200), 244, img)     # 244: must not mark
    for k in range(12):
        img = np.where(((x - 300 - 25 * k) ** 2 + (y - 215) ** 2) < 9, 255, img)
    img[:, 640:] = 0
    return np.clip(img, 0, 255).astype(np.uint8)


def play_scene():
    y, x = np.mgrid[0:480, 0:704]
    img = np.full((480, 704), 90, np.uint8)
    img[(x > 400) & (y < 120)] = 255           # clipped sky corner
    img[((x - 150) ** 2 + (y - 300) ** 2) < 30 ** 2] = 251   # lamp
    img[(x > 300) & (x < 360) & (y > 300)] = 240             # bright, not clipped
    return img


def setnib(pl, x, y, v):
    i = y * ROW + (x >> 1)
    pl[i] = (pl[i] & 0x0F) | (v << 4) if not x & 1 else (pl[i] & 0xF0) | v


def nib(pl, x, y):
    b = pl[y * ROW + (x >> 1)]
    return b >> 4 if not x & 1 else b & 0xF


def icons(pl):
    for (x0, y0, w, h) in ((8, 6, 60, 14), (291, 7, 61, 13), (60, 140, 30, 12), (8, 222, 90, 12)):
        for yy in range(y0, y0 + h):
            for xx in range(x0, x0 + w):
                setnib(pl, xx, yy, 1)


def expected(before, img, xt, lstep, thresh=250, col=15):
    out, n = bytearray(before), 0
    for k in range(120):
        la, lb = (2 * k * lstep) >> 8, ((2 * k + 1) * lstep) >> 8
        for b in range(180):
            px = [(2 * b + dx, 2 * k + dy) for dy in (0, 1) for dx in (0, 1)]
            if any(nib(before, x, y) for x, y in px):
                continue
            s = (img[la, xt[2 * b]], img[la, xt[2 * b + 1]], img[lb, xt[2 * b]], img[lb, xt[2 * b + 1]])
            if max(s) >= thresh:
                for x, y in px:
                    setnib(out, x, y, col)
                n += 1
    return bytes(out), n


class Rig:
    def __init__(self, opts=None, setting=1, heap_fail=False):
        self.res = link([H.make(opts or {})])
        self.u = u = U(self.res.module, heap_fail=heap_fail)
        self.r0 = u.install()
        u.uc.mem_map(0x8400000, 0x100000)
        self.flushes = []
        u.stub(0x6BBFC, "flush")
        u.stub(0x6A564, "osd_enable")
        u.stub(0x837F0, "plane2_bars")
        uc = u.uc
        self.live = live_scene()
        uc.mem_write(LIVE, self.live.tobytes())
        uc.mem_write(H.LIVEPTR, struct.pack(">I", LIVE))
        self.play = play_scene()
        uc.mem_write(PB, self.play.tobytes())
        uc.mem_write(H.PBTAB, struct.pack(">III", PB, 0x6E0000, 0))
        uc.mem_write(H.ZOOM + 5, b"\x00")
        uc.mem_write(0x1BEFE3, b"\x01")
        uc.mem_write(H.SETB, bytes([setting]))
        self.state(2)
        self.disp(5)
        self.tick = 100
        for plane in (H.PLANE1, H.PLANE2):
            pl = bytearray(0xA8C0)
            icons(pl)
            uc.mem_write(plane, bytes(pl))

    def sym(self, name):
        s = self.res.symbols
        return RES_POOL + s[PREFIX + name] - s["koma_res"]

    def state(self, s):
        self.u.uc.mem_write(H.APPST, struct.pack(">I", s))

    def disp(self, d):
        self.u.uc.mem_write(0x1BEF92, bytes([d]))

    def plane(self, p=1):
        return self.u.read(H.PLANE1 if p == 1 else H.PLANE2, 0xA8C0)

    def _tick(self, d):
        self.tick += d
        self.u.uc.mem_write(H.TICK, struct.pack(">I", self.tick))

    def hist(self, dtick=1):
        self._tick(dtick)
        self.u.calls.clear()
        self.u.thumb(0x2A090, {})
        return [c for c in self.u.calls if c[0] == "flush"]

    def mon(self):
        self.u.calls.clear()
        r0 = self.u.thumb(H.SITE_MON, {UC_ARM_REG_R0: 0x77}, stop=H.SITE_MON + 4)
        return r0, [c for c in self.u.calls if c[0] == "flush"]

    def play_call(self, dtick=1):
        self._tick(dtick)
        self.u.calls.clear()
        self.u.thumb(H.SITE_PLAY, {}, stop=H.SITE_PLAY + 4)
        return [c for c in self.u.calls if c[0] == "flush"]


def test_install_heap_and_tables():
    r = Rig()
    assert r.r0 == 0 and r.u.callee_saved_ok
    assert r.u.heap_calls == [H.HEAP_BYTES]
    assert struct.unpack(">I", r.u.read(r.sym("heap_ptr"), 4))[0] == HEAP
    xt = struct.unpack(">360H", r.u.read(HEAP, 720))
    xp = struct.unpack(">360H", r.u.read(HEAP + 720, 720))
    assert list(xt) == H.xtab_live() and list(xp) == H.xtab_play()
    assert r.u.read(HEAP + H.MAP_OFF, H.MAP_BYTES) == bytes(H.MAP_BYTES)
    for site, h in ((H.SITE_HIST, "hook_hist"), (H.SITE_MON, "hook_mon"), (H.SITE_PLAY, "hook_play")):
        from koma.asm import bl_target
        assert bl_target(site, r.u.read(site, 4)) == r.sym(h)


def test_footprint_and_fits_with_default_build():
    res = link([H.make({})])
    assert res.symbols["K_RES_LEN"] < 1100          # resident: code + state, no shipped map
    assert len(res.module) < 2240          # 2200 before the installer grew 28 B (BLHI prefix-only records)
    full = link([F.make(n) for n in F.DEFAULT] + [H.make({})])
    assert len(full.module) - 0x14 <= fw.MAXLEN


def test_hist_blinks_exact_and_stock_untouched():
    ref = Rig(setting=0)
    ref.hist()
    stock = ref.plane()
    r = Rig()
    f1 = r.hist()
    want, n = expected(stock, r.live, H.xtab_live(), 256)
    assert n > 300 and r.plane() == want
    assert f1 and f1[-1][1] == 1
    # the 244 wall (live x 260..380, y 120..200 -> OSD x ~147..213) carries no marks
    pl = r.plane()
    assert all(nib(pl, x, y) != 15 for y in range(124, 198) for x in range(150, 210))
    r.hist()
    assert r.plane() == stock                        # erase restores exactly
    r.hist()
    assert r.plane() == want                         # blink
    r.u.uc.mem_write(H.PLANE1, stock)                # OSD redrawn + refresh chain broken: map dropped, fresh paint
    r.hist(dtick=5)
    assert r.plane() == want
    r.u.uc.mem_write(H.SETB, b"\x00")                # switched off with marks showing: erased
    r.hist()
    assert r.plane() == stock


def test_setting_out_of_range_is_off():
    ref = Rig(setting=0)
    ref.hist()
    for v in (0, 2, 3, 0xFF):
        r = Rig(setting=v)
        r.hist()
        r.hist()
        assert r.plane() == ref.plane()


def test_live_view_path_any_disp():
    r = Rig()
    r.disp(0)
    before = r.plane()
    r0, fl = r.mon()
    assert r0 == 0 and not fl and r.plane() == before         # divider: first call only counts
    r0, fl = r.mon()
    want, n = expected(before, r.live, H.xtab_live(), 256)
    assert r0 == 0 and n > 300 and r.plane() == want and fl[-1][1] == 1
    r.mon()
    assert r.plane() == want
    r.mon()
    assert r.plane() == before                                 # erased on the next active call
    r.mon()
    r.mon()
    assert r.plane() == want
    r.state(7)                                                 # not a shooting screen: drop, plane untouched
    r0, fl = r.mon()
    assert r0 == 0 and not fl and r.plane() == want
    r.disp(5)
    r.state(2)
    r0, fl = r.mon()
    assert r0 == 5 and not fl                                  # DISP 5 belongs to the histogram path


def test_live_view_zoom_or_no_frame_is_off():
    r = Rig()
    r.disp(1)
    before = r.plane()
    r.u.uc.mem_write(H.ZOOM + 5, b"\x02")
    for _ in range(4):
        r.mon()
    assert r.plane() == before
    r.u.uc.mem_write(H.ZOOM + 5, b"\x00")
    r.u.uc.mem_write(H.LIVEPTR, bytes(4))
    for _ in range(4):
        r.mon()
    assert r.plane() == before


def test_playback_path_plane2():
    r = Rig()
    r.state(0)
    before = r.plane(2)
    p1 = r.plane(1)
    fl = r.play_call()
    want, n = expected(before, r.play, H.xtab_play(), 512)
    assert n > 300 and r.plane(2) == want and fl[-1][1] == 2
    assert r.plane(1) == p1
    pl = r.plane(2)                                  # the 240 bar (x 300..360, y > 300) is not marked
    assert all(nib(pl, x, y) != 15 for y in range(155, 240) for x in range(156, 182))
    r.play_call()
    assert r.plane(2) == before
    r.play_call()
    r.state(2)                                       # back to a shooting state with marks showing: erased
    r.play_call()
    assert r.plane(2) == before


def test_heap_alloc_failure_leaves_hooks_inert():
    r = Rig(heap_fail=True)
    assert r.r0 == 0 and r.u.heap_calls == [H.HEAP_BYTES]
    assert struct.unpack(">I", r.u.read(r.sym("heap_ptr"), 4))[0] == 0
    ref = Rig(setting=0)
    ref.hist()
    r.hist()
    r.disp(0)
    r.mon()
    r.mon()
    assert r.plane() == ref.plane()
