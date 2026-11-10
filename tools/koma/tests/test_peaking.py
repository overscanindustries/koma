"""peaking unit-emu tests: the linked module installed by the real Koma installer under Unicorn on the 2.40 payload, then
the hooked VD-task site 0x95af4 run with the real wrapped call 0x99a28 around it. Stubbed: the ARM flush 0x6bbfc.
A host model (model()) is the reference for every plane byte and bitmap bit."""
from __future__ import annotations

import struct

import numpy as np
from _util import fw  # noqa: F401  (sets sys.path)
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2

from koma import features as F
from koma.features import peaking as P
from koma.linker import link
from koma.unitemu import ALLOC, RES_POOL, Unit

HEAP = 0x3E0000
RING = 0x8470000
SLOTS = (RING, RING + 0x2AA80, RING + 0x55500)
ROW = 0xB4
PLANE_BYTES = ROW * 240
PREFIX = "kf_peaking__"


class U(Unit):
    """The second pool allocation (the feature's bitmap) gets its own address; heap_fail makes only that one fail."""

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


def edge_scene(x_edge=321, lo=40, hi=200):
    """640x244 at stride 672: a sharp vertical edge (x < x_edge dark, x >= x_edge bright) plus a soft ramp that must
    not mark (one level per pixel)."""
    img = np.full((244, 672), lo, np.uint8)
    img[:, x_edge:640] = hi
    ramp = (np.arange(640) // 6 + 60).astype(np.uint8)
    img[200:244, :640] = ramp
    img[:, 640:] = 0
    return img


def texture_scene(seed=1):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, (244, 672), dtype=np.uint8)
    img[:, 640:] = 0
    return img


def icons(pl):
    """Firmware-drawn stand-ins (index 1 white, 6 black, 15 red battery) incl. one across the edge column."""
    for (x0, y0, w, h, v) in ((8, 6, 60, 14, 1), (291, 7, 61, 13, 6), (170, 100, 30, 12, 1), (300, 220, 20, 8, 15),
                              (181, 150, 1, 9, 6)):
        for yy in range(y0, y0 + h):
            for xx in range(x0, x0 + w):
                i = yy * ROW + (xx >> 1)
                pl[i] = (pl[i] & 0x0F) | (v << 4) if not xx & 1 else (pl[i] & 0xF0) | v


def model(plane, bm, img, col, oldcol, thresh=24, step=4, cap=1200):
    """Host reference of one pass: returns (plane, bm, marks)."""
    g = P.geometry(step)
    pl, bm = bytearray(plane), bytearray(bm)
    marks = 0
    for j in range(g["ny"]):
        y = step * j
        for gi in range(g["groups"]):
            old = bm[j * g["groups"] + gi]
            new = 0
            if img is not None:
                for k in range(8):
                    x = step * (8 * gi + k)
                    v = int(img[y, x])
                    d = abs(int(img[y, x + 1]) - v) + abs(int(img[y + 1, x]) - v)
                    if d >= thresh:
                        new |= 1 << k
            out = 0
            for k in range(8):
                a = y * ROW + g["gstride"] * gi + g["boff"][k]
                if old >> k & 1:
                    for q in (a, a + ROW):
                        b = pl[q]
                        if b >> 4 == oldcol:
                            b &= 0x0F
                        if b & 0xF == oldcol:
                            b &= 0xF0
                        pl[q] = b
                if new >> k & 1 and marks >= cap:
                    new &= ~(1 << k)
                if new >> k & 1 and pl[a] == 0 and pl[a + ROW] == 0:
                    pl[a] = pl[a + ROW] = col * 0x11
                    out |= 1 << k
                    marks += 1
            bm[j * g["groups"] + gi] = out
    return bytes(pl), bytes(bm), marks


class Rig:
    def __init__(self, opts=None, setting=1, heap_fail=False, img=None):
        self.opts = opts or {}
        self.step = int(self.opts.get("step", 4))
        self.res = link([P.make(self.opts)])
        self.u = u = U(self.res.module, heap_fail=heap_fail)
        self.r0 = u.install()
        uc = u.uc
        uc.mem_map(0x8400000, 0x100000)
        u.stub(0x6BBFC, "flush")
        self.img = edge_scene() if img is None else img
        for i, base in enumerate(SLOTS):
            uc.mem_write(base, self.img.tobytes() if i == 1 else bytes(672 * 244))
            uc.mem_write(P.LIVEPTR + 4 * i, struct.pack(">I", base))
        uc.mem_write(P.DSLOT, struct.pack(">I", 1))           # display slot 1 holds the scene
        uc.mem_write(P.MONRUN, b"\x01")
        uc.mem_write(P.ZOOM + 5, b"\x00")
        uc.mem_write(0x1F5034, b"\x00")
        uc.mem_write(P.SETB, bytes([setting]))
        self.state(2)
        self.vd = 1000
        pl = bytearray(PLANE_BYTES)
        icons(pl)
        uc.mem_write(P.PLANE1, bytes(pl))
        self.insns = 0

        def count(uc_, a, s, d):
            self.insns += 1
        uc.hook_add(UC_HOOK_CODE, count)

    def sym(self, name):
        s = self.res.symbols
        return RES_POOL + s[PREFIX + name] - s["koma_res"]

    def state(self, s):
        self.u.uc.mem_write(P.APPST, struct.pack(">I", s))

    def set(self, v):
        self.u.uc.mem_write(P.SETB, bytes([v]))

    def plane(self):
        return self.u.read(P.PLANE1, PLANE_BYTES)

    def bm(self):
        return self.u.read(HEAP, P.geometry(self.step)["bm_bytes"])

    def call(self, advance=True, r0=0x00):
        """One VD-task pass through 0x95af4. Returns (r0 after the site, flush calls, instructions)."""
        if advance:
            self.vd += 1
        self.u.uc.mem_write(P.VDCNT, struct.pack(">I", self.vd))
        self.u.uc.mem_write(0x1F5034, bytes([r0]))
        self.u.calls.clear()
        self.insns = 0
        out = self.u.thumb(P.SITE, {}, stop=P.SITE + 4)
        return out, [c for c in self.u.calls if c[0] == "flush"], self.insns

    def rect(self):
        return struct.unpack(">4H", self.u.read(self.sym("rect"), 8))


def test_install_bitmap_and_hook():
    r = Rig()
    assert r.r0 == 0 and r.u.callee_saved_ok
    g = P.geometry(4)
    assert r.u.heap_calls == [g["bm_bytes"]] == [1200]
    assert struct.unpack(">I", r.u.read(r.sym("heap_ptr"), 4))[0] == HEAP
    assert r.bm() == bytes(1200)
    from koma.asm import bl_target
    assert bl_target(P.SITE, r.u.read(P.SITE, 4)) == r.sym("hook_vd")


def test_footprint():
    for step in (4, 8):
        res = link([P.make({"step": step})])
        assert res.symbols["K_RES_LEN"] < 1120         # 76 B of it is the manifest; the feature itself ~1 KB
        assert len(res.module) < 2300
    full = link([F.make(n) for n in F.DEFAULT] + [P.make({})])
    assert len(full.module) - 0x14 <= fw.MAXLEN


def test_edge_marks_exact_and_wrapped_r0_kept():
    r = Rig()
    before = r.plane()
    r0, fl, n = r.call(r0=0x01)
    want, bm, marks = model(before, bytes(1200), r.img, 15, 0)
    assert r0 == 0x01
    assert r.plane() == want and r.bm() == bm
    # the edge x=320|321 is sampled at x=320 (sample i=80): plane byte 90 (OSD x 180,181), rows 4j, 4j+1, j=0..49
    # (rows 200+ hold the soft ramp), minus the blocks holding a firmware nibble (the icon at x 170..199, y 100..111:
    # j 25..27; the black line at x 181, y 150..158: j 38, 39)
    assert marks == 45
    cols = {i % ROW for i in range(PLANE_BYTES) if before[i] != want[i]}
    assert cols == {90}
    assert fl and fl[-1][1] == 1
    assert r.rect() == (0, 0, 360, 4 * 49 + 2)          # rows 0..197: the last changed sample row is j 49


def test_never_overwrites_firmware_nibbles():
    r = Rig(opts={"cap": 9600}, img=texture_scene())
    before = r.plane()
    r.call()
    after = r.plane()
    changed = 0
    for i in range(PLANE_BYTES):
        for sh in (4, 0):
            b, a = before[i] >> sh & 0xF, after[i] >> sh & 0xF
            if b != a:
                assert b == 0 and a == 15
                changed += 1
    assert changed > 1000


def test_clear_then_draw_leaves_no_stale_marks():
    r = Rig(opts={"cap": 9600}, img=texture_scene(1))
    base = r.plane()
    r.call()
    # move the scene: new texture in the display slot, also a different slot
    img2 = texture_scene(2)
    r.u.uc.mem_write(SLOTS[2], img2.tobytes())
    r.u.uc.mem_write(P.DSLOT, struct.pack(">I", 2))
    p1, b1 = r.plane(), r.bm()
    r.call()
    want, bm, _ = model(p1, b1, img2, 15, 15, cap=9600)
    assert r.plane() == want and r.bm() == bm
    fresh, _, _ = model(base, bytes(1200), img2, 15, 0, cap=9600)
    assert r.plane() == fresh                                  # identical to drawing img2 on a clean plane
    # a flat frame: every mark goes, the plane is the firmware's again
    r.u.uc.mem_write(SLOTS[2], bytes(672 * 244))
    _, fl, _ = r.call()
    assert r.plane() == base and r.bm() == bytes(1200) and fl


def test_steady_scene_does_not_flush_again():
    r = Rig()
    r.call()
    p = r.plane()
    _, fl, _ = r.call()
    assert r.plane() == p and not fl
    _, fl, _ = r.call(advance=False)                          # same VD count: no pass at all
    assert not fl


def test_firmware_redraw_is_repainted_and_flushed():
    r = Rig()
    before = r.plane()
    r.call()
    want = r.plane()
    r.u.uc.mem_write(P.PLANE1, before)                         # the firmware redrew the plane: our marks are gone
    _, fl, _ = r.call()
    assert r.plane() == want and fl


def test_firmware_pixel_drawn_over_a_mark_is_kept():
    r = Rig()
    r.call()
    pl = bytearray(r.plane())
    a = 40 * ROW + 90                                          # a marked block (row 40 = j 10)
    assert pl[a] == 0xFF
    pl[a] = 0x61                                               # firmware drew black + white into it
    r.u.uc.mem_write(P.PLANE1, bytes(pl))
    r.set(0)
    r.call()
    after = r.plane()
    assert after[a] == 0x61 and after[a + ROW] == 0x00          # only our nibbles cleared


def test_gates_draw_nothing():
    for setup in ("off", "range", "monitor", "menu-state", "zoom", "noslot", "badslot"):
        r = Rig(setting=0 if setup == "off" else (3 if setup == "range" else 1))
        if setup == "monitor":
            r.u.uc.mem_write(P.MONRUN, b"\x00")
        if setup == "menu-state":
            r.state(7)
        if setup == "zoom":
            r.u.uc.mem_write(P.ZOOM + 5, b"\x02")
        if setup == "noslot":
            r.u.uc.mem_write(P.LIVEPTR + 4, bytes(4))
        if setup == "badslot":
            r.u.uc.mem_write(P.DSLOT, struct.pack(">I", 3))
        before = r.plane()
        r0, fl, n = r.call(r0=0x01)
        assert r0 == 0x01 and r.plane() == before and not fl, setup
        assert n < 120, (setup, n)


def test_turning_off_erases_and_leaving_the_screen_forgets():
    r = Rig()
    before = r.plane()
    r.call()
    marked = r.plane()
    r.set(0)
    _, fl, _ = r.call()
    assert r.plane() == before and r.bm() == bytes(1200) and fl
    r.set(1)
    r.call()
    assert r.plane() == marked
    r.u.uc.mem_write(P.MONRUN, b"\x00")                        # menu: the monitor stops, the screen is the menu's
    _, fl, _ = r.call()
    assert r.plane() == marked and r.bm() == bytes(1200) and not fl
    r.u.uc.mem_write(P.MONRUN, b"\x01")


def test_yellow_and_color_change():
    r = Rig(setting=2)
    before = r.plane()
    r.call()
    want, bm, _ = model(before, bytes(1200), r.img, 10, 0)
    assert r.plane() == want
    r.set(1)                                                   # RED: the amber marks go, red ones come
    r.call()
    want2, _, _ = model(want, bm, r.img, 15, 10)
    assert r.plane() == want2 and want2 == model(before, bytes(1200), r.img, 15, 0)[0]


def test_step8_and_threshold_option():
    img = texture_scene(3)
    r = Rig(opts={"step": 8, "threshold": 60}, img=img)
    assert r.u.heap_calls == [300]
    before = r.plane()
    r.call()
    want, bm, marks = model(before, bytes(300), img, 15, 0, thresh=60, step=8, cap=1200)
    assert marks > 100 and r.plane() == want and r.bm() == bm


def test_heap_alloc_failure_is_inert():
    r = Rig(heap_fail=True)
    assert r.r0 == 0 and struct.unpack(">I", r.u.read(r.sym("heap_ptr"), 4))[0] == 0
    before = r.plane()
    r0, fl, _ = r.call(r0=0x01)
    assert r0 == 0x01 and r.plane() == before and not fl


def test_mark_cap_bounds_dense_detail():
    img = texture_scene(4)
    r = Rig(img=img)
    before = r.plane()
    r.call()
    want, bm, marks = model(before, bytes(1200), img, 15, 0)
    assert marks == 1200 and r.plane() == want and r.bm() == bm
    img2 = texture_scene(5)                                    # a new dense frame: old marks go, new capped ones come
    r.u.uc.mem_write(SLOTS[1], img2.tobytes())
    p1, b1 = r.plane(), r.bm()
    r.call()
    want2, bm2, marks2 = model(p1, b1, img2, 15, 15)
    assert marks2 == 1200 and r.plane() == want2 and r.bm() == bm2


def test_cost_per_frame():
    """Instructions per pass (flush stubbed): edge scene, dense texture, and the 8x option. Printed for the README."""
    out = {}
    r = Rig()
    _, _, out["edge_first"] = r.call()
    _, _, out["edge_steady"] = r.call()
    r = Rig(img=texture_scene())
    _, _, out["dense_first"] = r.call()
    _, _, out["dense_steady"] = r.call()
    r.u.uc.mem_write(SLOTS[1], texture_scene(9).tobytes())
    _, _, out["dense_change"] = r.call()
    r = Rig(opts={"cap": 9600}, img=texture_scene())
    _, _, out["dense_nocap_first"] = r.call()
    _, _, out["dense_nocap_steady"] = r.call()
    r = Rig(img=np.zeros((244, 672), np.uint8))
    _, _, out["flat"] = r.call()
    r = Rig(opts={"step": 8})
    _, _, out["edge_step8"] = r.call()
    print("peaking cost", out)
    assert out["edge_steady"] < 150_000 and out["flat"] < 150_000 and out["dense_steady"] < 200_000
