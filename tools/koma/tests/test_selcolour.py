"""selcolour: unit-emulated checks (Unicorn on the 2.40 payload, the module installed by its own installer).

The real hooked site runs: 0x117380 `add r0, r4, #0` + the patched `bl` at 0x117382 -> handler -> pass -> original
0x1173e8 (stubbed). A synthetic master UV plane (one vertical band per known hue, padding past the width) sits at the
real 8 MP UV base 0x8f40e00 with a small geometry in a fake cp.

Covered: install (1 KiB table block allocated); for every COLOR x RANGE the Thumb-built table equals the Python
reference and the plane equals the reference pass; which patches survive (NARROW: exactly the chosen hue; WIDE: the
neighbour inside the soft edge is halved); OFF / out-of-range COLOR / out-of-range RANGE / B&W develop / bad geometry
leave the plane untouched and still call the original once with cp; crcb order; r4-r7 kept; table rebuilt only when
the key changes; COLOR / RANGE only from an 'RCP5' recipe store header (Koma 0.2: published by the SELECT recipe).
"""
from __future__ import annotations

import struct

from _util import fw  # noqa: F401  (sets sys.path)
from koma.features import selcolour as S
from koma.linker import link
from koma.unitemu import Unit
from unicorn.arm_const import UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7

CP = 0x700000
UV = S.UV8
STRIDE, WIDTH, LINES = 352, 336, 3          # width: 8 bands of 40 bytes + 16 bytes of grey tail; 16 B padding
SURVIVE = {  # (color, range) -> {patch: "keep" | "half"}; everything else must go neutral
    (1, 0): {"red": "keep"}, (1, 1): {"red": "keep", "orange": "half"},
    (2, 0): {"orange": "keep"}, (2, 1): {"orange": "keep"},
    (3, 0): {"yellow": "keep"}, (3, 1): {"yellow": "keep", "orange": "half"},
    (4, 0): {"green": "keep"}, (4, 1): {"green": "keep"},
    (5, 0): {"blue": "keep"}, (5, 1): {"blue": "keep"},
    (6, 0): {"magenta": "keep"}, (6, 1): {"magenta": "keep"},
}


def unit(opts=None):
    r = link([S.make(opts or {})])
    u = Unit(r.module)
    u.uc.mem_map(UV & ~0xFFFFF, 0x100000)
    assert u.install() == 0
    u.state = u.resident[0] + r.symbols["kf_selcolour__state"] - r.symbols["koma_res"]
    assert u.read(u.state, 8) == S.MAGIC
    u.stub(S.TARGET, "orig_1173e8")
    return u


def lut_of(u):
    return struct.unpack(">I", u.read(u.state + S.O_LUT, 4))[0]


def shot(u, color, rng, mode=0, plane=None, geom=(UV, STRIDE, WIDTH, LINES), magic=S.STMAGIC):
    uv, stride, width, lines = geom
    u.uc.mem_write(S.STB, magic)
    u.uc.mem_write(S.COLB, bytes([color, rng]))
    u.uc.mem_write(S.MODEB, bytes([mode]))
    u.uc.mem_write(CP, bytes(0x100))
    u.uc.mem_write(CP + 0x58, struct.pack(">I", uv))
    u.uc.mem_write(CP + 0x5C, struct.pack(">HHH", stride, width, lines))
    if plane is not None:
        u.uc.mem_write(UV, plane)
    u.calls.clear()
    sent = {UC_ARM_REG_R4: CP, UC_ARM_REG_R5: 0x55, UC_ARM_REG_R6: 0x66, UC_ARM_REG_R7: 0x77}
    u.thumb(0x117380, sent, stop=0x117386, count=20_000_000)
    assert u.calls == [("orig_1173e8", CP)], u.calls                    # original called once, with cp
    assert all(u.uc.reg_read(r) == v for r, v in sent.items())
    return u.read(UV, STRIDE * LINES), u.read(u.state, S.STATE_LEN)


def survivors(after, band, order="cbcr"):
    got = {}
    for i, (name, rgb) in enumerate(S.PATCHES):
        cb, cr = S.rgb_uv(*rgb)
        orig = bytes([cb, cr]) if order == "cbcr" else bytes([cr, cb])
        now = after[i * band:i * band + 2]
        if orig == b"\x80\x80":
            assert now == orig
            continue
        if now == orig:
            got[name] = "keep"
        elif now == bytes([(orig[0] + 128) >> 1, (orig[1] + 128) >> 1]):
            got[name] = "half"
        else:
            assert now == b"\x80\x80", (name, now.hex())
    return got


def test_rows_and_install():
    from koma.features import bwrecipes as B
    assert S.ROWS == []                                                    # the SELECT recipe carries COLOR / RANGE
    assert (S.STB, S.STMAGIC, S.COLB, S.RNGB) == (B.ST, B.MAGIC, B.SELCOL, B.SELRNG)
    assert S.COLORS[1:] == B.SEL_COLORS and S.RANGES == B.SEL_RANGES
    assert S.LUTN == 1024
    u = unit()
    lut = lut_of(u)
    assert lut and lut & 3 == 0
    assert (lut, S.LUTN) in [(a, n) for a, n in u.blocks]                 # second pool block = the table
    assert u.read(u.state + S.O_KEY, 1) == b"\xff"


def test_every_color_and_range():
    u = unit()
    plane, band = S.synthetic_plane(STRIDE, WIDTH, LINES)
    for (c, rg), want in SURVIVE.items():
        after, st = shot(u, c, rg, plane=plane)
        key = 2 * (c - 1) + rg
        assert st[S.O_RES] == S.RES_RAN and st[S.O_KEY] == key, (c, rg, st.hex())
        assert u.read(lut_of(u), S.LUTN) == S.reference_lut(key), (c, rg)
        assert after == S.reference_pass(plane, STRIDE, WIDTH, LINES, S.reference_lut(key)), (c, rg)
        assert survivors(after, band) == want, (S.COLORS[c], S.RANGES[rg], survivors(after, band))
        assert struct.unpack(">I", st[S.O_END:S.O_END + 4])[0] == UV + (LINES - 1) * STRIDE + WIDTH
        for ln in range(LINES):                                            # stride padding never touched
            assert after[ln * STRIDE + WIDTH:(ln + 1) * STRIDE] == b"\x5a" * (STRIDE - WIDTH)


def test_off_and_guards_leave_plane_untouched():
    u = unit()
    plane, _ = S.synthetic_plane(STRIDE, WIDTH, LINES)
    cases = [((0, 0), {}, S.RES_OFF), ((7, 0), {}, S.RES_OFF), ((0xFF, 1), {}, S.RES_OFF), ((1, 2), {}, S.RES_OFF),
             ((1, 0), dict(mode=1), S.RES_MODE), ((1, 0), dict(mode=2), S.RES_MODE),
             ((1, 0), dict(geom=(0x700000, STRIDE, WIDTH, LINES)), S.RES_GEOM),         # below SDRAM
             ((1, 0), dict(geom=(UV + 2, STRIDE, WIDTH, LINES)), S.RES_GEOM),           # misaligned
             ((1, 0), dict(geom=(UV, STRIDE, STRIDE + 4, LINES)), S.RES_GEOM),          # width > stride
             ((1, 0), dict(geom=(UV, STRIDE, 2, LINES)), S.RES_GEOM),                   # width < 4
             ((1, 0), dict(geom=(UV, STRIDE, WIDTH, 0)), S.RES_GEOM),                   # no lines
             ((1, 0), dict(geom=(UV, 8192, WIDTH, LINES)), S.RES_GEOM),                 # stride > 4096
             ((1, 0), dict(geom=(0x0BFFF000, STRIDE, WIDTH, 100)), S.RES_GEOM)]         # past SDRAM end
    for (c, rg), kw, res in cases:
        after, st = shot(u, c, rg, plane=plane, **kw)
        assert after == plane, (c, rg, kw)
        assert st[S.O_RES] == res, (c, rg, kw, st[S.O_RES])


def test_table_rebuilt_only_on_key_change():
    u = unit()
    plane, _ = S.synthetic_plane(STRIDE, WIDTH, LINES)
    shot(u, 1, 0, plane=plane)
    lut = lut_of(u)
    u.uc.mem_write(lut, b"\x02" * S.LUTN)                                 # poison: a rebuild would overwrite it
    after, _ = shot(u, 1, 0, plane=plane)
    assert after == plane                                                  # all "keep" -> untouched: no rebuild
    after, st = shot(u, 1, 1, plane=plane)
    assert u.read(lut, S.LUTN) == S.reference_lut(1) and st[S.O_KEY] == 1
    assert st[S.O_RUNS] == 3


def test_crcb_order():
    u = unit({"order": "crcb"})
    plane, band = S.synthetic_plane(STRIDE, WIDTH, LINES, order="crcb")
    for c in (1, 5):
        after, _ = shot(u, c, 0, plane=plane)
        assert u.read(lut_of(u), S.LUTN) == S.reference_lut(2 * (c - 1), order="crcb")
        assert survivors(after, band, order="crcb") == SURVIVE[(c, 0)]


class FailTableAlloc(Unit):
    """The resident block allocation succeeds, the table allocation (second call) fails."""

    def _code(self, uc, a, size, _):
        if a == 0x15F024 and self.resident is not None:
            self.calls.append(("alloc-fail",))
            self._ret(1)
            return
        super()._code(uc, a, size, _)


def test_alloc_failure_keeps_feature_off():
    r = link([S.make({})])
    u = FailTableAlloc(r.module)
    u.uc.mem_map(UV & ~0xFFFFF, 0x100000)
    assert u.install() == 0
    u.state = u.resident[0] + r.symbols["kf_selcolour__state"] - r.symbols["koma_res"]
    assert lut_of(u) == 0
    u.stub(S.TARGET, "orig_1173e8")
    plane, _ = S.synthetic_plane(STRIDE, WIDTH, LINES)
    after, st = shot(u, 1, 0, plane=plane)
    assert after == plane and st[S.O_RES] == S.RES_NOLUT


def test_needs_the_recipe_store_header():
    """COLOR / RANGE count only under an 'RCP5' header: a v4 store (or none) is OFF, whatever bytes 9/10 hold."""
    u = unit()
    plane, _ = S.synthetic_plane(STRIDE, WIDTH, LINES)
    for magic in (b"RCP4", b"\xff\xff\xff\xff", b"\x00\x00\x00\x00"):
        after, st = shot(u, 1, 0, plane=plane, magic=magic)
        assert after == plane and st[S.O_RES] == S.RES_OFF, magic


def test_force_option():
    u = unit({"force": "red"})
    plane, band = S.synthetic_plane(STRIDE, WIDTH, LINES)
    after, st = shot(u, 0, 0, plane=plane)                    # byte says OFF; the build forces RED
    assert st[S.O_RES] == S.RES_RAN and survivors(after, band) == {"red": "keep"}
    after, st = shot(u, 0, 5, plane=plane)                    # RANGE still read at use: out of range = OFF
    assert after == plane and st[S.O_RES] == S.RES_OFF
    after, st = shot(u, 0, 5, plane=plane, magic=b"RCP4")     # no recipe store: forced RED, NARROW
    assert st[S.O_RES] == S.RES_RAN and survivors(after, band) == {"red": "keep"}


def test_rcp6_header_from_koma03_counts_like_rcp5():
    """A store left by Koma 0.3 ('RCP6', same +9 COLOR / +10 RANGE) drives SELECT exactly as 'RCP5' does."""
    u = unit()
    plane, _ = S.synthetic_plane(STRIDE, WIDTH, LINES)
    a5, st5 = shot(u, 1, 0, plane=plane, magic=b"RCP5")
    a6, st6 = shot(u, 1, 0, plane=plane, magic=b"RCP6")
    assert st5[S.O_RES] == S.RES_RAN and st6[S.O_RES] == st5[S.O_RES] and a6 == a5
