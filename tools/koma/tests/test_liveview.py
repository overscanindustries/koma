"""liveview: the -FDWL record becomes -LVFR; the resident handler answers 451 outside a tether-held live view and
otherwise builds the 16-byte header + frame in the USB payload buffer with the -PRET state. Unit emulation on the 2.40
payload; the VD wait, resize and JPEG engines are stubbed (their register programs are not modelled)."""
from __future__ import annotations

import struct

from _util import fw
from koma.features import liveview as L
from koma.linker import link
from koma.unitemu import Unit
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

REPLY, LEN = 0x7F0500, 0x7F0504
USB = 0x700000                  # stands in for 0x0bf844e0 (the unit map ends at 0x800000)
RING = (0x8470000, 0x849AA80, 0x84C5500)


def _unit(opts=None):
    r = link([L.make(opts or {})])
    u = Unit(r.module)
    assert u.install() == 0 and u.callee_saved_ok
    u.uc.mem_map(0x8400000, 0x200000)
    handler = u.resident[0] + r.symbols["kf_liveview__lvfr_handler"] - r.symbols["koma_res"]
    return u, handler


def _w32(u, a, v):
    u.uc.mem_write(a, struct.pack(">I", v & 0xFFFFFFFF))


def _u32(u, a):
    return struct.unpack(">I", u.read(a, 4))[0]


def _call(u, handler, mode, seq, *, running=1, state=2, slot=1, jpeg_len=0x3E82, sync_r0=0, eoi=True):
    _w32(u, L.ARG_A, mode)
    _w32(u, L.ARG_A + 4, seq)
    u.uc.mem_write(L.MONI_RUN, bytes([running]))
    _w32(u, L.APP_STATE, state)
    _w32(u, L.SLOT, slot)
    for i, y in enumerate(RING):
        _w32(u, L.RING_Y + 4 * i, y)
        _w32(u, L.RING_C + 4 * i, y + 0x7FF80)
    _w32(u, L.USB_BUF, USB)
    _w32(u, L.XSTATE, 1)
    for a in (L.TOTAL, L.CHUNK, L.FINAL, REPLY, LEN):
        _w32(u, a, 0)
    u.uc.mem_write(USB, b"\xee" * 0x40)
    u.uc.mem_write(L.SCR_Y, bytes(range(251)) * 100)           # the "resized" Y plane
    u.uc.mem_write(L.SCR_OUT, b"\xab" * 0x40)
    if eoi and 2 <= jpeg_len < 0x10000 * 2:
        u.uc.mem_write(L.SCR_OUT + jpeg_len - 2, b"\xff\xd9")
    u.stub(L.VD_WAIT, "vd_wait", r0=sync_r0)
    u.stub(L.RESIZE, "resize", r0=0)
    u.stub(L.JPEG, "jpeg", r0=jpeg_len)
    u.stub(L.APO, "apo_290de", r0=0)
    u.calls.clear()
    u.thumb(handler, {UC_ARM_REG_R0: REPLY, UC_ARM_REG_R1: LEN})
    reply = u.read(_u32(u, REPLY), 64).split(b"\0")[0].decode() if _u32(u, REPLY) else None
    u.last_args = {n: a for n, a in u.calls}
    u.apo = [a for n, a in u.calls if n == "apo_290de"]             # v1.2 Auto Power Off hold, checked on its own
    return reply, _u32(u, LEN), _u32(u, L.XSTATE), [n for n, _ in u.calls if n != "apo_290de"]


def test_records_and_before_bytes():
    f = L.make({})
    assert fw.ram(0x1FB784, 0x24) == bytes.fromhex("000cb64c0000001a" + "00" * 24 + "000cd39f")
    assert fw.ram(0xCB64C, 6) == b"-FDWL\0"
    assert {p.addr: p.before for p in f.pointers} == {0x1FB784: bytes.fromhex("000cb64c"),
                                                     0x1FB7A4: bytes.fromhex("000cd39f")}
    assert [(p.addr, p.before, p.after) for p in f.patches] == [(0x1FB794, bytes(4), bytes.fromhex("00000001"))]
    for c in f.checks:
        assert fw.ram(c.addr, len(c.before)) == c.before
    assert not f.hooks and not f.redirects and not f.settings and not f.rows
    assert L.GAP[0] <= L.SCR_Y < L.SCR_C < L.SCR_OUT < L.SCR_END <= L.GAP[1]
    assert L.SCR_C - L.SCR_Y >= 352 * 240 and L.SCR_OUT - L.SCR_C >= 352 * 240


def test_install_writes_only_the_record():
    u, _ = _unit()
    out = sorted({a & ~3 for a, _ in u.outside_writes() if not 0x58190 <= a < 0x58198})
    assert out == [0x1FB784, 0x1FB794, 0x1FB7A4], [hex(a) for a in out]
    assert u.read(0x1FB794, 4) == b"\0\0\0\1"
    name = _u32(u, 0x1FB784)
    assert u.read(name, 6) == b"-LVFR\0" and _u32(u, 0x1FB7A4) & 1


def test_mode0_header_and_payload():
    u, h = _unit()
    reply, n, state, calls = _call(u, h, 0, 0x1234)
    assert reply == L.S150 and n == len(L.S150) and state == 4
    assert calls == ["vd_wait", "resize"]
    rsz = struct.unpack(">14I", u.read(u.last_args["resize"], 56))
    assert rsz[5:8] == (L.SCR_Y, L.SCR_C, 192) and rsz[10:] == (640, 244, 160, 120)    # 0x5e8f0 wants stride w + 32
    hdr = u.read(USB + 8, 16)
    assert hdr == b"LVFR" + bytes([0, 0]) + struct.pack(">HHHHH", 0x1234, 160, 120, 19200, 0)
    plane = u.read(L.SCR_Y, 192 * 120)
    assert u.read(USB + 24, 19200) == b"".join(plane[r * 192:r * 192 + 160] for r in range(120))
    assert _u32(u, L.TOTAL) == _u32(u, L.CHUNK) == 19216 and _u32(u, L.HANDLE) == 0x8001
    assert u.read(_u32(u, L.FINAL), 16).split(b"\0")[0].decode() == L.S250


def test_mode1_resize_descriptor_and_scale():
    u, h = _unit({"scale": "0x80"})
    reply, _, state, calls = _call(u, h, 1, 2, slot=2)
    assert reply == L.S150 and state == 4 and calls == ["vd_wait", "resize", "jpeg"]
    hdr = u.read(USB + 8, 16)
    assert hdr == b"LVFR" + bytes([1, 0]) + struct.pack(">HHHHH", 2, 320, 240, 0x3E82, 0x80)
    assert u.read(USB + 24, 0x40) == b"\xab" * 0x40 and _u32(u, L.TOTAL) == 0x3E82 + 16
    rsz = struct.unpack(">14I", u.read(u.last_args["resize"], 56))
    assert rsz == (RING[2], RING[2] + 0x7FF80, 672, 0, 0, L.SCR_Y, L.SCR_C, 352, 0, 0, 640, 244, 320, 240)
    enc = struct.unpack(">9I", u.read(u.last_args["jpeg"], 36))
    assert enc == (L.SCR_Y, L.SCR_C, 352, 0, 0, 320, 240, L.SCR_OUT, L.SCR_END)


def test_451_when_monitor_stopped_or_not_tethered():
    u, h = _unit()
    for kw in (dict(running=0), dict(state=9), dict(running=0, state=9)):
        reply, n, state, calls = _call(u, h, 1, 7, **kw)
        assert reply == L.S451 and n == len(L.S451) and state == 3 and calls == [], kw
        assert u.read(USB, 0x40) == b"\xee" * 0x40 and _u32(u, L.TOTAL) == 0
        assert u.u8(L.REPLY_FLAG) == 1 and u.u8(L.REPLY_FLAG2) == 0
    for bad in (4, 0x14, 0x102):                                 # v1.3: a & 0xf == 3 is mode 3; others stay 501
        reply, _, state, calls = _call(u, h, bad, 7)
        assert reply == L.S501 and state == 3 and calls == [], bad


def test_apo_hold_only_when_serving_a_frame():
    """v1.2: modes 0/1 past the gate call 0x290de(2) once, before the VD wait; 451 and 501 issue nothing."""
    u, h = _unit()
    for mode in (0, 1):
        reply, _, _, _ = _call(u, h, mode, 1)
        assert reply == L.S150 and u.apo == [2], (mode, u.calls)
        assert [n for n, _ in u.calls][:2] == ["apo_290de", "vd_wait"]
    for mode, kw in ((1, dict(running=0)), (0, dict(state=9)), (4, {}), (3, dict(running=0))):
        _call(u, h, mode, 1, **kw)
        assert u.apo == [], (mode, kw)


def test_truncation_and_nosync_flags():
    u, h = _unit()
    reply, _, state, _ = _call(u, h, 1, 9, jpeg_len=70000, sync_r0=0xFFFFFFFF)
    assert reply == L.S150 and state == 4
    hdr = u.read(USB + 8, 16)
    assert hdr[4:6] == bytes([1, L.FLAG_TRUNC | L.FLAG_NOSYNC])
    assert struct.unpack(">H", hdr[12:14])[0] == L.MAX_PAYLOAD and _u32(u, L.TOTAL) == 0x10000
    reply, _, state, _ = _call(u, h, 1, 9, jpeg_len=0xFFFFFFFF)            # encoder error -> 451
    assert reply == L.S451 and state == 3


def test_sync_off_skips_the_wait():
    u, h = _unit({"sync": "off"})
    reply, _, _, calls = _call(u, h, 0, 1)
    assert reply == L.S150 and calls == ["resize"]


def test_mode1_without_eoi_is_451():
    u, h = _unit()
    reply, _, state, calls = _call(u, h, 1, 3, eoi=False)       # positive length but no FF D9 at out+L-2
    assert reply == L.S451 and state == 3 and calls == ["vd_wait", "resize", "jpeg"]
    assert u.read(USB, 0x40) == b"\xee" * 0x40 and _u32(u, L.TOTAL) == 0
    reply, _, state, _ = _call(u, h, 1, 4)
    assert reply == L.S150 and state == 4


def test_default_scale_is_448():
    assert "448" in L.OPTIONS["scale"]
    u, h = _unit()
    _call(u, h, 1, 5)
    assert struct.unpack(">H", u.read(USB + 8, 16)[14:16])[0] == 448


def _osd_unit():
    u, h = _unit()
    u.uc.mem_map(0x1200C000, 0x1000)
    u.uc.mem_map(0x12020000, 0x1000)
    return u, h


def _osd_fill(u, base_reg):
    page_a = bytes((i * 7) & 0xFF for i in range(L.OSD_BYTES))
    page_b = bytes((i * 13 + 1) & 0xFF for i in range(L.OSD_BYTES))
    u.uc.mem_write(L.OSD_PAGE_A, page_a)
    u.uc.mem_write(L.OSD_PAGE_B, page_b)
    _w32(u, L.OSD_BASE_REG, base_reg)
    pal = [0] + [0x00DA0000 + i * 0x0101 for i in range(1, 64)]
    for i, v in enumerate(pal):
        _w32(u, L.VEN_PALETTE + 4 * i, v)
    return page_a, page_b, pal


def test_mode2_osd_page_header_payload_and_no_gate():
    u, h = _osd_unit()
    page_a, page_b, pal = _osd_fill(u, 0x08422800)
    for kw in (dict(running=0, state=2), dict(running=0, state=9), dict(running=1, state=2)):  # menu open, stock USB
        reply, n, state, calls = _call(u, h, 2, 0x55, **kw)
        assert reply == L.S150 and n == len(L.S150) and state == 4 and calls == [], kw
        hdr = u.read(USB + 8, 16)
        assert hdr == b"LVFR" + bytes([2, 0]) + struct.pack(">HHHHH", 0x55, 360, 240, 43264, 16)
        assert L.OSD_LEN == 43264 == 360 * 240 // 2 + 64 and 16 + L.OSD_LEN < 0x10000
        assert u.read(USB + 24, L.OSD_BYTES) == page_a
        got = struct.unpack(">16I", u.read(USB + 24 + L.OSD_BYTES, 64))
        assert list(got) == [0] + pal[1:16]
        assert _u32(u, L.TOTAL) == _u32(u, L.CHUNK) == 16 + 43264 and _u32(u, L.HANDLE) == 0x8001
        assert u.read(_u32(u, L.FINAL), 16).split(b"\0")[0].decode() == L.S250


def test_mode2_page_b_and_unknown_base():
    u, h = _osd_unit()
    _, page_b, _ = _osd_fill(u, 0x0042F280)            # register value without the 0x08000000 alias bit
    reply, _, _, _ = _call(u, h, 2, 1, running=0)
    assert reply == L.S150 and u.read(USB + 8, 16)[5] == L.FLAG_PAGE_B
    assert u.read(USB + 24, L.OSD_BYTES) == page_b
    page_a, _, _ = _osd_fill(u, 0x08500000)
    reply, _, _, _ = _call(u, h, 2, 2, running=0)
    assert reply == L.S150 and u.read(USB + 8, 16)[5] == L.FLAG_PAGE_GUESS  # bit 4
    assert u.read(USB + 24, L.OSD_BYTES) == page_a


def test_mode2_payload_decodes_with_grdui_render_page():
    """The host decoder: the OSD page renderer (render_page) over the payload bytes (base 0, stride 180)."""
    from koma.emu.grdui import display as D
    u, h = _osd_unit()
    page_a, _, pal = _osd_fill(u, 0x08422800)
    _call(u, h, 2, 3, running=0)
    body = u.read(USB + 24, L.OSD_LEN)
    raw = list(struct.unpack(">16I", body[L.OSD_BYTES:])) + [None] * 48
    raw[0] = None
    img = D.render_page(lambda a, n: body[a:a + n], 0, L.OSD_STRIDE, L.OSD_W, L.OSD_H, raw)
    ref = D.render_page(lambda a, n: page_a[a:a + n], 0, L.OSD_STRIDE, L.OSD_W, L.OSD_H, [None] + pal[1:])
    assert img.size == (360, 240) and img.tobytes() == ref.tobytes()


def test_torn_flag_when_vd_count_advances_two_across_the_resize():
    from unicorn import UC_HOOK_CODE
    u, h = _unit()
    bump = {"n": 0}
    u.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, _: _w32(u, L.VD_COUNT, _u32(u, L.VD_COUNT) + bump["n"]),
                  begin=L.RESIZE & ~1, end=(L.RESIZE & ~1) + 1)
    for mode, n, torn in ((0, 0, 0), (0, 1, 0), (0, 2, L.FLAG_TORN), (1, 5, L.FLAG_TORN), (1, 1, 0)):
        bump["n"] = n
        _w32(u, L.VD_COUNT, 0xFFFFFFFF)                    # wraps: unsigned difference
        reply, _, state, calls = _call(u, h, mode, 1)
        assert reply == L.S150 and state == 4 and calls[:2] == ["vd_wait", "resize"], (mode, n)
        assert u.read(USB + 8, 16)[5] == torn, (mode, n)
    assert L.FLAG_TORN == 8 and L.FLAG_PAGE_GUESS == 16
