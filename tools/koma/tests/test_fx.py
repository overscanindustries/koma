"""fx (Koma 0.3): GRAIN / VIGNETTE on the master Y plane, unit-emulated (Unicorn on the 2.40 payload, the module
installed by its own installer).

Two harnesses: the real hooked site (0x117380 `add r0, r4, #0` + the patched bl at 0x117382 -> handler -> pass ->
original 0x1173e8, stubbed) on small planes, and a bare copy of the installed machine (no per-instruction Python hook)
that calls the relocated ARM entry directly for full 8 MP planes and counts instructions with a block hook.

Covered: OFF is a no-op and costs about 20 instructions; every GRAIN x VIGNETTE pair equals the Python model; grain
mean ~0, std per level, bounded, none outside 16..239, no wrap on a full ramp, a new pattern every shot; vignette
centre exactly unchanged, corners darker by the designed ratio, symmetric; every size / aspect; geometry guards;
the store-header gate; build options grain=off / vignette=off; full-frame instruction counts (<= 40 M); the numpy
model equals the reference; fx build options never change the recipe store layout (selfcheck).
"""
from __future__ import annotations

import struct

from _util import fw  # noqa: F401  (sets sys.path)
from koma.features import fx as X
from koma.linker import link
from koma.unitemu import Unit
from unicorn import UC_ARCH_ARM, UC_HOOK_BLOCK, UC_MODE_ARM, UC_MODE_BIG_ENDIAN, Uc
from unicorn.arm_const import UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7

CP = 0x700000
Y = X.Y8
SIZES = [(3264, 2448), (3264, 2176), (2592, 1944), (2048, 1536), (1280, 960), (640, 480)]


def unit(opts=None):
    r = link([X.make(opts or {})])
    u = Unit(r.module)
    u.uc.mem_map(0x08000000, 0x01000000)
    assert u.install() == 0
    u.state = u.resident[0] + r.symbols["kf_fx__state"] - r.symbols["koma_res"]
    assert u.read(u.state, 8) == X.MAGIC
    u.stub(X.TARGET, "orig_1173e8")
    u.link = r
    return u


def header(u, grain, vig, magic=X.STMAGIC):
    h = bytearray(16)
    h[0:4], h[11], h[12] = magic, grain, vig
    u.uc.mem_write(X.STB, bytes(h))


def geom(u, stride, width, lines, y=Y):
    u.uc.mem_write(CP, bytes(0x100))
    u.uc.mem_write(CP + 0x54, struct.pack(">I", y))
    u.uc.mem_write(CP + 0x5C, struct.pack(">HHH", stride, width, lines))


def shot(u, grain, vig, plane, stride, width, lines, magic=X.STMAGIC, y=Y):
    """Through the real site; returns (plane after, state, code bytes executed: Thumb 2 / ARM 4 per instruction)."""
    header(u, grain, vig, magic)
    geom(u, stride, width, lines, y)
    u.uc.mem_write(Y, plane)
    u.calls.clear()
    n = [0]
    h = u.uc.hook_add(UC_HOOK_BLOCK, lambda uc, a, size, _: n.__setitem__(0, n[0] + size))
    sent = {UC_ARM_REG_R4: CP, UC_ARM_REG_R5: 0x55, UC_ARM_REG_R6: 0x66, UC_ARM_REG_R7: 0x77}
    try:
        u.thumb(0x117380, sent, stop=0x117386, count=50_000_000)
    finally:
        u.uc.hook_del(h)
    assert u.calls == [("orig_1173e8", CP)], u.calls                    # original called once, with cp
    assert all(u.uc.reg_read(r) == v for r, v in sent.items())
    return u.read(Y, len(plane)), u.read(u.state, X.STATE_LEN), n[0]


def seed_of(st):
    return struct.unpack(">I", st[X.O_SEED:X.O_SEED + 4])[0]


def bare(u):
    """A hook-free copy of the installed machine (every mapped region), for full-size runs."""
    b = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_BIG_ENDIAN)
    for start, end, perms in u.uc.mem_regions():
        b.mem_map(start, end - start + 1, perms)
        b.mem_write(start, bytes(u.uc.mem_read(start, end - start + 1)))
    return b


def full(u, grain, vig, plane, stride, width, lines, count=False):
    """Run the relocated ARM entry on a bare copy; returns (plane after, state, ARM instructions or None)."""
    b = bare(u)
    hdr = bytearray(16)
    hdr[0:4], hdr[11], hdr[12] = X.STMAGIC, grain, vig
    b.mem_write(X.STB, bytes(hdr))
    b.mem_write(CP, bytes(0x100))
    b.mem_write(CP + 0x54, struct.pack(">I", Y))
    b.mem_write(CP + 0x5C, struct.pack(">HHH", stride, width, lines))
    b.mem_write(Y, plane)
    st = bytes(b.mem_read(u.state, X.STATE_LEN))
    main, ret = (struct.unpack(">I", st[o:o + 4])[0] for o in (X.O_MAIN, X.O_RET))
    n = [0]
    if count:
        b.hook_add(UC_HOOK_BLOCK, lambda uc, a, size, _: n.__setitem__(0, n[0] + size))
    X.run_main(b, main, ret, CP, 0x7E0000)
    return bytes(b.mem_read(Y, len(plane))), bytes(b.mem_read(u.state, X.STATE_LEN)), n[0] // 4 if count else None


def flat(stride, width, lines, v):
    return (bytes([v]) * width + b"\x5a" * (stride - width)) * lines


# ------------------------------------------------------------------ small planes, real site
W, S, L = 2048, 2080, 6              # width^2 >= 2^22: the vignette is on; 32 bytes of padding per line


def test_rows_store_and_install():
    from koma.features import bwrecipes as B
    assert (X.STB, X.STMAGIC, X.GRB, X.VIGB) == (B.ST, B.MAGIC, B.FXGRAIN, B.FXVIG)
    assert X.ROWS == [] and B.GRAINS == B.VIGS == ["OFF", "1", "2", "3"]
    f = link([X.make({})]).manifest["features"][0]
    assert f["resident_bytes"] <= 1100, f["resident_bytes"]
    assert X.LOOP_COST == {k: len([ln for ln in X.loop_src(*k) if not ln.endswith(":")]) for k in X.LOOP_COST}


def test_off_is_a_noop_and_cheap():
    u = unit()
    plane = X.synthetic_plane(S, W, L)
    for g, v in ((0, 0), (4, 0), (0, 9), (0xFF, 0xFF)):              # OFF, and out of range = OFF
        after, st, n = shot(u, g, v, plane, S, W, L)
        assert after == plane and st[X.O_RES] == X.RES_OFF and n <= 160, (g, v, n)    # bytes: ~40 instructions
    for magic in (b"RCP5", b"\xff\xff\xff\xff"):                       # no RCP6 store: OFF whatever +11/+12 say
        after, st, _ = shot(u, 3, 3, plane, S, W, L, magic=magic)
        assert after == plane and st[X.O_RES] == X.RES_OFF


def test_every_level_pair_equals_the_model():
    u = unit()
    plane = X.synthetic_plane(S, W, L)
    for g in range(4):
        for v in range(4):
            if not g and not v:
                continue
            after, st, _ = shot(u, g, v, plane, S, W, L)
            assert st[X.O_RES] == X.RES_RAN, (g, v)
            assert after == X.reference_pass(plane, S, W, L, g, v, seed_of(st)), (g, v)
            for ln in range(L):                                         # stride padding never touched
                assert after[ln * S + W:(ln + 1) * S] == b"\x5a" * (S - W)
            assert struct.unpack(">I", st[X.O_END:X.O_END + 4])[0] == Y + (L - 1) * S + W


def test_new_pattern_every_shot():
    u = unit()
    plane = flat(S, W, L, 128)
    a, sa, _ = shot(u, 2, 0, plane, S, W, L)
    b, sb, _ = shot(u, 2, 0, plane, S, W, L)
    assert seed_of(sa) != seed_of(sb) and a != b and a != plane


def test_geometry_guards_leave_plane_untouched():
    u = unit()
    plane = X.synthetic_plane(S, W, L)
    for stride, width, lines, y in ((S, 8, L, Y),                       # width < 16
                                    (S, S + 16, L, Y),                  # width > stride
                                    (S, W, L, Y + 2),                   # misaligned
                                    (S + 2, W, L, Y),                   # stride misaligned
                                    (8192, W, L, Y),                    # stride > 4096
                                    (S, W, 0, Y), (S, W, 4097, Y),      # lines
                                    (S, W, L, 0x700000),                # below SDRAM
                                    (S, W, 4000, 0x0BF00000)):          # past SDRAM end
        after, st, _ = shot(u, 3, 3, plane, stride, width, lines, y=y)
        assert after == plane and st[X.O_RES] == X.RES_GEOM, (stride, width, lines, hex(y))


def test_build_options_drop_each_half():
    plane = X.synthetic_plane(S, W, L)
    for opts, build in (({"grain": "off"}, (False, True)), ({"vignette": "off"}, (True, False)),
                        ({"grain": "off", "vignette": "off"}, (False, False))):
        u = unit(opts)
        after, st, _ = shot(u, 3, 3, plane, S, W, L)
        assert st[X.O_BUILD] == int(build[0]) | int(build[1]) << 1
        assert after == X.reference_pass(plane, S, W, L, 3, 3, seed_of(st), build=build), opts
        if build == (False, False):
            assert after == plane and st[X.O_RES] == X.RES_OFF
    assert len(link([X.make({"grain": "off"})]).module) < len(link([X.make({})]).module)


# ------------------------------------------------------------------ full frames, bare machine
def test_full_frame_cost_and_model():
    """3264 x 2448, both effects at 3: <= 40 M instructions (the budget), lines 0 / mid / last == model. Also the
    grain-only and vignette-only costs. Prints the counts."""
    u = unit()
    w, h = 3264, 2448
    plane = X.synthetic_plane(X.STRIDE8, w, h)
    out = {}
    for g, v in ((3, 3), (2, 0), (0, 3), (1, 1)):
        after, st, n = full(u, g, v, plane, X.STRIDE8, w, h, count=True)
        chk = {0, h // 2, h - 1}
        exp = X.reference_pass(plane, X.STRIDE8, w, h, g, v, seed_of(st), only=chk)
        for ln in chk:
            assert after[ln * X.STRIDE8:(ln + 1) * X.STRIDE8] == exp[ln * X.STRIDE8:(ln + 1) * X.STRIDE8], (g, v, ln)
        out[(g, v)] = n
        assert n <= 40_000_000, (g, v, n)
    blocks = w // 16 * h
    assert abs(out[(3, 3)] - 75 * blocks) < 150_000, out                # the loop dominates; per-line work < 0.15 M
    print("fx 8 MP instructions:", {f"g{g}v{v}": n for (g, v), n in out.items()})


def test_grain_statistics_and_bounds():
    """Flat grey 128: mean ~0, std 4.6 / 9.2 / 9.2 (uniform odd values, +-7 / +-14); flat 8 and 248: untouched (outside
    16..239); a full 0..255 ramp never wraps (|out - in| <= 14 everywhere)."""
    import numpy as np
    u = unit()
    w, h, s = 1024, 64, 1024
    for g, (sd, mx) in ((1, (4.58, 7)), (2, (9.17, 14)), (3, (9.17, 14))):
        after, st, _ = full(u, g, 0, flat(s, w, h, 128), s, w, h)
        d = np.frombuffer(after, np.uint8).astype(int) - 128
        assert abs(d.mean()) < 0.1 and abs(d.std() - sd) < 0.15 and np.abs(d).max() == mx, (g, d.mean(), d.std())
        assert set(np.unique(d)) <= set(range(-mx, mx + 1, 2 if g >= 2 else 1)), g
        for v in (8, 248, 0, 255):
            after, _, _ = full(u, g, 0, flat(s, w, h, v), s, w, h)
            assert after == flat(s, w, h, v), (g, v)
        ramp = bytes(range(256)) * (w // 256) * h
        after, _, _ = full(u, g, 0, ramp, s, w, h)
        d = np.frombuffer(after, np.uint8).astype(int) - np.frombuffer(ramp, np.uint8).astype(int)
        assert np.abs(d).max() <= mx, g
    a, _, _ = full(u, 3, 0, flat(s, w, h, 128), s, w, h)                 # GRAIN 3: lines repeat in pairs (2 x 1 grain)
    rows = [a[i * s:(i + 1) * s] for i in range(h)]
    assert all(rows[i] == rows[i + 1] for i in range(0, h, 2)) and rows[0] != rows[2]


def test_vignette_ratio_centre_and_symmetry():
    """Flat 200 at 8 MP: the centre pixel is unchanged; corner / centre = the designed (1 - k tx^2)(1 - k ty^2) =
    0.76 / 0.57 / 0.44 within 2 levels; left-right and top-bottom symmetric within 1 level; monotone from the centre."""
    u = unit()
    w, h = 3264, 2448
    for v, corner in ((1, 0.76), (2, 0.57), (3, 0.44)):
        after, _, _ = full(u, 0, v, flat(X.STRIDE8, w, h, 200), X.STRIDE8, w, h)
        px = lambda x, y: after[y * X.STRIDE8 + x]                       # noqa: E731
        assert px(w // 2, h // 2) in (199, 200), v
        for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            assert abs(px(x, y) - 200 * corner) <= 2.5, (v, x, y, px(x, y))
        assert abs(px(0, h // 2) - px(w - 1, h // 2)) <= 1 and abs(px(w // 2, 0) - px(w // 2, h - 1)) <= 1
        row = [px(x, h // 2) for x in range(0, w // 2, 16)]
        assert all(a <= b for a, b in zip(row, row[1:])), v


def test_every_size_and_aspect():
    """Each master size the camera makes (4:3 and 3:2): the kernel walks the whole plane (end of last line) and three
    lines equal the model, with both effects at 2."""
    u = unit()
    for w, h in SIZES:
        stride = w + 64 if w < 3264 else X.STRIDE8
        plane = X.synthetic_plane(stride, w, h)
        after, st, _ = full(u, 2, 2, plane, stride, w, h)
        assert st[X.O_RES] == X.RES_RAN and struct.unpack(">I", st[X.O_END:X.O_END + 4])[0] == Y + (h - 1) * stride + w
        chk = {0, h // 3, h - 1}
        exp = X.reference_pass(plane, stride, w, h, 2, 2, seed_of(st), only=chk)
        for ln in chk:
            assert after[ln * stride:(ln + 1) * stride] == exp[ln * stride:(ln + 1) * stride], (w, h, ln)
        vig_on = w * w + h * h >= X.MINDIAG
        assert vig_on == (w >= 2048), (w, h)                            # 1280 x 960 and 640 x 480: no vignette


def test_numpy_model_equals_reference():
    import numpy as np
    w, h = 2048, 12
    plane = X.synthetic_plane(w, w, h)
    y = np.frombuffer(plane, np.uint8).reshape(h, w)
    for g, v in ((1, 0), (2, 3), (3, 1), (0, 2)):
        assert X.reference_np(y, g, v, 0x1234567).tobytes() == X.reference_pass(plane, w, w, h, g, v, 0x1234567)


def test_fx_off_builds():
    """fx.grain=off / fx.vignette=off / no fx at all: the recipe store layout (bwrecipes' settings ranges, header magic,
    record length, FX table, rows) is the same as the full build (without fx the GRAIN / VIGNETTE rows are hidden), and every variant passes selfcheck."""
    from koma import features as F
    from koma.features import bwrecipes as B
    from koma.selfcheck import selfcheck

    def build(fx_opts, with_fx=True):
        names = [n for n in F.KOMA03 if with_fx or n != "fx"]
        fs = [F.make(n, {"cp15": "off"} if n == "telemetry" else fx_opts if n == "fx" else {}) for n in names]
        r = link(fs)
        b = next(f for f in r.manifest["features"] if f["name"] == "bwrecipes")
        return r, (b["settings"], [(x["id"], x["byte"]) for x in r.manifest["rows"]])
    base, layout = build({})
    assert selfcheck(base.module, base.manifest, verbose=False)["ok"]
    for opts, with_fx in (({"grain": "off"}, True), ({"vignette": "off"}, True),
                          ({"grain": "off", "vignette": "off"}, True), ({}, False)):
        r, lay = build(opts, with_fx)
        want = layout if with_fx else (layout[0], [x for x in layout[1] if x[0] not in ("rp_grain", "rp_vig")])
        assert lay == want, opts                # without fx: same store bytes, GRAIN / VIGNETTE rows hidden
        assert selfcheck(r.module, r.manifest, verbose=False)["ok"], opts
    assert (B.MAGIC, B.STORE_VER, B.RECLEN, B.REC + B.N * B.RECLEN) == (b"RCP6", 6, 12, 0x1BA00)
