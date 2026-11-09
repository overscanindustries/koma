"""liveview 1.3 mode 3: the display slot itself, 640x240 at stride 672, JPEG-encoded straight from the ring (no resize),
one retry at twice the scale, never more than one 65,536-byte chunk. Unit emulation on the 2.40 payload with the VD
wait and the JPEG engine stubbed (the engine's real register program is EMU-only)."""
from __future__ import annotations

import struct

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_SP

from _util import fw
from koma.features import liveview as L
from test_liveview import RING, USB, _call, _u32, _unit, _w32


def _m3(u, h, a, seq, rets, eoi=True, bump=0, **kw):
    """Run -LVFR a,seq with the JPEG stub returning rets[i] on call i (FF D9 placed at out+L-2 when eoi)."""
    seen = []

    def hook(uc, addr, size, _):
        i = len(seen)
        # the unit's stub hook runs first and already set r0 to the return value: the descriptor is the handler's
        # frame +0x40 (call_r3 is a bare `bx r3`, so sp is the handler's), the scale is r1 (untouched by the stub)
        r1, sp = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_SP)
        seen.append((r1, struct.unpack(">9I", bytes(uc.mem_read(sp + 0x40, 36)))))
        cur = rets[min(i, len(rets) - 1)]
        if eoi and 2 <= cur <= 0x30000:
            uc.mem_write(L.SCR_OUT3 + cur - 2, b"\xff\xd9")
        u.stubs[L.JPEG & ~1] = ("jpeg", rets[min(i + 1, len(rets) - 1)])
        _w32(u, L.VD_COUNT, _u32(u, L.VD_COUNT) + bump)
    hk = u.uc.hook_add(UC_HOOK_CODE, hook, begin=L.JPEG & ~1, end=(L.JPEG & ~1) + 1)
    u.uc.mem_write(L.SCR_OUT3, bytes(range(256)) * 0x110)      # scan stand-in (overrun slack included)
    try:
        out = _call(u, h, a, seq, jpeg_len=rets[0], eoi=False, **kw)
    finally:
        u.uc.hook_del(hk)
    return out, seen


def test_geometry_constants_from_firmware_tables():
    assert struct.unpack(">H", fw.ram(0x193838, 2))[0] == L.SRC_W == 640
    assert struct.unpack(">H", fw.ram(0x193876, 2))[0] == L.SRC_H == 244
    assert struct.unpack(">H", fw.ram(0x1935C4, 2))[0] == L.SRC_STRIDE == 672
    assert L.M3_W % 16 == 0 and L.M3_W <= L.SRC_STRIDE and L.M3_H == (L.SRC_H >> 3) << 3 == 240
    assert (L.M3_H >> 3) * (L.M3_W >> 4) == 1200 <= 0xFFFF                     # mcuNum assert 0x16325c
    assert L.GAP[0] <= L.SCR_OUT3 and L.M3_LIMIT + 8 <= L.GAP[1] and L.M3_LIMIT % 4 == 0
    assert L.M3_LIMIT - L.SCR_OUT3 + 5 <= L.MAX_PAYLOAD                        # 0x5e2ec's longest success fits


def test_mode3_encodes_the_ring_slot_directly():
    u, h = _unit()
    (reply, n, state, calls), seen = _m3(u, h, 3, 0x4242, [40000], slot=2)
    assert reply == L.S150 and n == len(L.S150) and state == 4
    assert calls == ["vd_wait", "jpeg"]                                       # no resize pass
    assert u.apo == [2]
    scale, desc = seen[0]
    assert scale == 448
    assert desc == (RING[2], RING[2] + 0x7FF80, 672, 0, 0, 640, 240, L.SCR_OUT3, L.M3_LIMIT)
    hdr = u.read(USB + 8, 16)
    assert hdr == b"LVFR" + bytes([3, 0]) + struct.pack(">HHHHH", 0x4242, 640, 240, 40000, 448)
    assert u.read(USB + 24, 40000) == u.read(L.SCR_OUT3, 40000)
    assert _u32(u, L.TOTAL) == _u32(u, L.CHUNK) == 40016 and _u32(u, L.HANDLE) == 0x8001
    assert u.read(_u32(u, L.FINAL), 16).split(b"\0")[0].decode() == L.S250


def test_mode3_host_scale_and_build_default():
    u, h = _unit({"scale3": "300"})
    (reply, *_), seen = _m3(u, h, 3, 1, [20000])
    assert reply == L.S150 and seen[0][0] == 300
    (reply, *_), seen = _m3(u, h, (700 << 4) | 3, 2, [20000])
    assert reply == L.S150 and seen[0][0] == 700
    hdr = u.read(USB + 8, 16)
    assert hdr[4] == 3 and struct.unpack(">H", hdr[14:16])[0] == 700


def test_mode3_retry_at_twice_the_scale_then_451():
    u, h = _unit()
    for first in (0xFFFFFFFF, 70000, 0):                       # error, overrun past one chunk, zero length
        (reply, _, state, calls), seen = _m3(u, h, 3, 5, [first, 30000])
        assert reply == L.S150 and state == 4 and calls == ["vd_wait", "jpeg", "jpeg"], first
        assert [s for s, _ in seen] == [448, 896]
        hdr = u.read(USB + 8, 16)
        assert hdr[5] == L.FLAG_REENC and struct.unpack(">HH", hdr[12:16]) == (30000, 896)
    (reply, _, state, calls), seen = _m3(u, h, 3, 6, [0xFFFFFFFF, 0xFFFFFFFF])
    assert reply == L.S451 and state == 3 and calls == ["vd_wait", "jpeg", "jpeg"]
    assert _u32(u, L.TOTAL) == 0
    (reply, _, state, _), seen = _m3(u, h, (0xFFFF << 4) | 3, 7, [0xFFFFFFFF, 1000])   # doubling clamps at 65535
    assert reply == L.S150 and [s for s, _ in seen] == [0xFFFF, 0xFFFF]


def test_mode3_without_eoi_retries():
    u, h = _unit()
    (reply, _, state, calls), _ = _m3(u, h, 3, 8, [30000], eoi=False)
    assert reply == L.S451 and state == 3 and calls == ["vd_wait", "jpeg", "jpeg"]


def test_mode3_largest_frame_fits_one_chunk():
    u, h = _unit()
    (reply, _, _, _), _ = _m3(u, h, 3, 9, [L.MAX_PAYLOAD])
    assert reply == L.S150 and _u32(u, L.TOTAL) == 0x10000
    assert u.read(USB + 8, 16)[5] == 0                                         # never the truncated flag
    (reply, _, _, calls), _ = _m3(u, h, 3, 9, [L.MAX_PAYLOAD + 1, 100])
    assert reply == L.S150 and calls[-2:] == ["jpeg", "jpeg"]


def test_mode3_gate_torn_and_nosync():
    u, h = _unit()
    for kw in (dict(running=0), dict(state=9)):
        (reply, _, state, calls), _ = _m3(u, h, 3, 1, [100], **kw)
        assert reply == L.S451 and state == 3 and calls == [] and u.apo == [], kw
    _w32(u, L.VD_COUNT, 0xFFFFFFFF)
    (reply, _, _, _), _ = _m3(u, h, 3, 1, [100], bump=2, sync_r0=0xFFFFFFFF)
    assert reply == L.S150 and u.read(USB + 8, 16)[5] == L.FLAG_TORN | L.FLAG_NOSYNC
    (reply, _, _, _), _ = _m3(u, h, 3, 1, [100], bump=1)
    assert u.read(USB + 8, 16)[5] == 0


def test_modes_0_1_2_unchanged_by_mode3_option():
    """scale3 only reaches mode 3: modes 0/1 descriptors and headers are as in 1.2."""
    u, h = _unit({"scale3": "999"})
    reply, _, _, calls = _call(u, h, 1, 2, slot=2)
    assert reply == L.S150 and calls == ["vd_wait", "resize", "jpeg"]
    assert struct.unpack(">H", u.read(USB + 8, 16)[14:16])[0] == 448
    enc = struct.unpack(">9I", u.read(u.last_args["jpeg"], 36))
    assert enc == (L.SCR_Y, L.SCR_C, 352, 0, 0, 320, 240, L.SCR_OUT, L.SCR_END)


def test_scale3_option_bounds():
    for bad in ("0", "63", "65536", "x"):
        try:
            L.make({"scale3": bad})
        except ValueError:
            continue
        raise AssertionError(bad)
    assert L.make({"scale3": "64"}) and L.make({"scale3": "0xffff"})
