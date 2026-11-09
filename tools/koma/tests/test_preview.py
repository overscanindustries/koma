"""preview (Koma 0.3): live-view recipe re-apply, unit-emulated (Unicorn on the 2.40 payload, firmware callees faked
where noted). Built together with bwrecipes, whose tables and leaf routines it shares.

Covered: install and records (5 Thumb BL hooks; level C: 2 veneer pointers); at each of the four monitor sites the
recipe registers are the last write after the stock write (colour: chroma = tapped stock monitor word x SATURATION,
matrix + TINT; SELECT: chroma only; B&W: filter luma weights, toning word through the TE setter; no WB writes); the
chroma tap runs the real 0x922bc; OFF and IMG-SET-mismatch leave stock values (luma weights put back after a filter
recipe); level A never uploads a LUT; level B streams the recipe curve into CC2 and CC1 at start / resume only, sets
both cache bytes 0x1f4db0 / 0x1f4db3 to 0xff, and reloads the stock LUTs (0x92050) on OFF; level C scales R / B of the
monitor WB words only while the monitor is active, entered from the real ARM caller through the veneers; callee-saved
registers and sp intact and the return lands on the instruction after the site (Thumb) or after the veneer call (ARM).
"""
from __future__ import annotations

import struct

from _util import fw  # noqa: F401  (sets sys.path)
import test_bwrecipes as TB
from koma.features import bwrecipes as B
from koma.features import preview as P
from koma.linker import link
from koma.unitemu import RES_POOL, STACK
from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R4,
                               UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
                               UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP)

CP = TB.CP
STOCK = {"12018104": "00000000", "12018108": "00000000", "1201810c": "00000000", "1201800c": "4008993a",
         "12018038": "00000001"}
SENT = {UC_ARM_REG_R4: 0xA4, UC_ARM_REG_R5: 0xA5, UC_ARM_REG_R6: 0xA6, UC_ARM_REG_R8: 0xA8, UC_ARM_REG_R9: 0xA9,
        UC_ARM_REG_R10: 0xAA, UC_ARM_REG_R11: 0xAB}
# site -> first instruction of the stock lead-in (r0 = cp+0x22 from r7) and the instruction after the BL
SITES = {P.SITE_START: 0x98718, P.SITE_RESUME: 0x95E16, P.SITE_SETTING: 0x8F2DA}


class PU(TB.RU):
    def __init__(self, module, **kw):
        self.cc1, self.lutload = [], 0
        super().__init__(module, **kw)

    def _code(self, uc, a, size, _):
        if a == P.CC1_UP:
            r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            assert r0 <= 0xFFF and 1 <= r1 <= 0x1000 and r0 + r1 <= 0x1000, (r0, r1)
            self.cc1.append((r0, list(struct.unpack(f">{r1}H", bytes(uc.mem_read(r2, 2 * r1))))))
            self._ret(0)
            return
        if a == P.LUT_LOAD:
            self.lutload += 1
            self._ret(0)
            return
        super()._code(uc, a, size, _)

    def psym(self, name):
        return RES_POOL + self.link.symbols[f"kf_preview__{name}"] - self.link.symbols["koma_res"]

    def pv(self):
        return self.read(self.psym("pv"), P.PL_LEN)

    def stream1(self):
        out = []
        for off, vals in self.cc1:
            assert off == len(out)
            out += vals
        return out


def _stock_mono(u):
    """Stand-in for the stock monitor preset that ends in 0x8ff0e: matrix 0, luma weights, mono word (MMIO log)."""
    u.calls.append(("mono_8ff0e", u.uc.reg_read(UC_ARM_REG_R0)))
    u.mmio.extend(STOCK.items())


def unit(name=None, imgset=None, opts=None):
    lr = link([B.make({}), P.make(opts or {})])
    rid = TB.rid(name) if name else 0
    if imgset is None:
        imgset = B.imgset_of(B.RECIPES[rid - 1]["params"], B.RECIPES[rid - 1]["type"]) if rid else 1
    edits = {CP + 0x21: b"\x00", CP + 0x22: b"\x00", CP + 0x268: struct.pack(">3H", 185, 64, 172)}
    u = PU(lr.module, ram_edits=edits, settings_imgset=imgset)
    u.link = lr
    u.uc.mem_map(0x08010000, 0x10000)                 # adjustment store (zeros): stock words / default paths
    u.uc.mem_write(B.ACT, bytes([rid]) + bytes(B.RST - B.ACT))
    u.fakes[P.MONO] = _stock_mono
    assert u.install() == 0 and u.callee_saved_ok
    if rid:
        u.on_change(B.ACT, rid)                       # menu commit: store, published type
        u.w8(0x1B030, imgset)
    return u


def stock_chroma(u):
    """Run the real 0x922bc(0) (monitor preset) as 0x98db0 does; returns the word it wrote through the tap."""
    u.mmio.clear()
    u.thumb(P.CHROMA_PRESET, {UC_ARM_REG_R0: 0})
    w = [v for a, v in u.mmio if a == "12018004"]
    assert len(w) == 1, u.mmio
    assert u.pv()[P.O_MON:P.O_MON + 4].hex() == w[0]
    return int(w[0], 16)


def run_site(u, site, mono):
    """Stock lead-in + the hooked BL, from a clean stack with sentinels; stops on the instruction after the BL."""
    stock_chroma(u)
    u.w8(CP + 0x22, mono)
    u.calls.clear()
    u.mmio.clear()
    u.cc2.clear()
    u.cc1.clear()
    u.te.clear()
    uc = u.uc
    for r, v in {UC_ARM_REG_R7: CP, **SENT}.items():
        uc.reg_write(r, v)
    uc.reg_write(UC_ARM_REG_SP, STACK)
    uc.reg_write(UC_ARM_REG_CPSR, (uc.reg_read(UC_ARM_REG_CPSR) & ~0xFF) | 0x13 | 0x20 | 0xC0)
    uc.emu_start(SITES[site] | 1, site + 4, count=3_000_000)
    assert uc.reg_read(UC_ARM_REG_PC) == site + 4 and uc.reg_read(UC_ARM_REG_CPSR) & 0x20, hex(site)
    assert uc.reg_read(UC_ARM_REG_SP) == STACK, hex(site)
    assert all(uc.reg_read(r) == v for r, v in SENT.items()) and uc.reg_read(UC_ARM_REG_R7) == CP, hex(site)
    assert [c for c in u.calls if c[0] == "mono_8ff0e"] == [("mono_8ff0e", mono)], u.calls
    return dict(u.mmio), u.mmio


def scaled(word, step):
    q = P.SAT_Q8[step]
    return int.from_bytes(bytes(min(0xFF, (b * q) >> 8) for b in word.to_bytes(4, "big")), "big")


def colour_want(name, word):
    p, kind = B.RECIPES[TB.rid(name) - 1]["params"], B.RECIPES[TB.rid(name) - 1]["type"]
    if kind & 3 == B.TYPE_COLOUR:
        mr, mg, mb = B.LOOK_MATRIX[kind >> 4]
        return {"12018004": "%08x" % scaled(word, p[B.P_SAT]), "12018104": "%08x" % mr,
                "12018108": "%08x" % B.tint_word(mg, p[B.P_TINT] - 3), "1201810c": "%08x" % mb}
    return {"12018004": "%08x" % scaled(word, p[B.P_SSAT])}


# ------------------------------------------------------------------ tests
def test_install_records_and_size():
    u = unit()
    from koma.asm import bl_target
    for h in P.make({}).hooks:
        assert bl_target(h.site, u.read(h.site, 4)) == u.psym(h.handler), hex(h.site)
    assert u.pv()[:4] == P.PL_MAGIC
    f = next(x for x in link([B.make({}), P.make({})]).manifest["features"] if x["name"] == "preview")
    assert f["resident_bytes"] <= 600, f["resident_bytes"]                 # level A budget (incl. its data)
    assert not P.make({"live": "off"}).hooks and not P.make({"live": "off"}).source
    link([B.make({}), P.make({"live": "off"})])


def test_colour_recipe_at_every_site():
    for name in ("POSITIVE", "BLEACH", "SLIDE 50", "WARM NEG"):
        u = unit(name)
        w = stock_chroma(u)
        want = colour_want(name, w)
        for site in SITES:
            last, log = run_site(u, site, mono=0)
            assert {a: last[a] for a in want} == want, (name, hex(site), last, want)
            first_stock = log.index(("12018104", "00000000"))
            assert all(max(i for i, (x, _) in enumerate(log) if x == a) > first_stock for a in want), (name, hex(site))
            assert not {"12018110", "12018114"} & set(last) and not u.te and not u.cc2 and not u.cc1
            assert u.pv()[P.O_TYPE] == 1 and u.pv()[P.O_SITE] == list(SITES).index(site)


def test_select_recipe_chroma_only():
    u = unit("SELECT")
    w = stock_chroma(u)
    want = colour_want("SELECT", w)
    for site in SITES:
        last, _ = run_site(u, site, mono=0)
        assert last["12018004"] == want["12018004"] and last["12018104"] == "00000000", (hex(site), last)


def test_setting_change_chroma_site_runs_real_preset():
    """0x8f1f0: the real 0x922bc(0) writes the stock word (through the tap), then the hook writes the recipe word."""
    u = unit("POSITIVE")
    p = B.RECIPES[TB.rid("POSITIVE") - 1]["params"]
    u.mmio.clear()
    for r, v in {UC_ARM_REG_R5: 0x55, UC_ARM_REG_R4: 0x44, UC_ARM_REG_R7: CP}.items():
        u.uc.reg_write(r, v)
    u.thumb(0x8F1EE, {UC_ARM_REG_R5: 0x55, UC_ARM_REG_R4: 0x44, UC_ARM_REG_R7: CP}, stop=0x8F1F4)
    w = [int(v, 16) for a, v in u.mmio if a == "12018004"]
    assert len(w) == 2 and w[1] == scaled(w[0], p[B.P_SAT]), u.mmio
    assert u.uc.reg_read(UC_ARM_REG_R4) == 0x44 and u.uc.reg_read(UC_ARM_REG_R5) == 0x55
    assert not [a for a, _ in u.mmio if a != "12018004"]                   # chroma only at this site
    assert u.pv()[P.O_SITE] == 3


def test_saturation_steps_scale_the_monitor_word():
    u = unit("POSITIVE")
    rid = TB.rid("POSITIVE")
    for step in range(7):
        u.on_change(B.EB + B.P_SAT, step)
        last, _ = run_site(u, P.SITE_RESUME, mono=0)
        assert last["12018004"] == "%08x" % scaled(stock_chroma(u), step), step
    assert step == 6 and u.u8(B.ACT) == rid


def test_bw_filter_and_toning_at_every_site():
    for name in ("SHINJUKU", "FILM 400", "HARD MONO", "MONO"):
        u = unit(name)
        p = B.RECIPES[TB.rid(name) - 1]["params"]
        toned = p[B.P_TONE] != 0
        for site in SITES:
            last, log = run_site(u, site, mono=2 if toned else 1)
            f = p[B.P_FILT]
            if f:
                assert last["1201800c"] == "%08x" % B.FTAB[f - 1][0], (name, last)
                assert log[-1 if not toned else -1][0] == "1201800c" or toned
            else:
                assert last["1201800c"] == "4008993a"
            if toned:
                cb, cr = B.tone_table()[p[B.P_TONE] - 1][p[B.P_DENS]]
                assert u.te == [(1 << 24) | ((cr & 0x3FF) << 12) | (cb & 0x3FF)], (name, u.te)
            else:
                assert not u.te
            assert not {"12018110", "12018114"} & set(last)              # no red-filter WB tilt on the monitor
            assert {a: v for a, v in log if a in ("12018004", "12018104")} == {"12018104": "00000000"}


def test_bw_recipe_with_monitor_in_colour_is_stock():
    u = unit("SHINJUKU")
    last, log = run_site(u, P.SITE_START, mono=0)
    assert log == list(STOCK.items()) and not u.te


def test_off_and_mismatch_leave_stock():
    u = unit()
    for site in SITES:
        last, log = run_site(u, site, mono=0)
        assert log == list(STOCK.items()) and u.pv()[P.O_TYPE] == 0xFF
    u = unit("POSITIVE", imgset=3)                # recipe id set but IMG SET holds another base (card swap)
    last, log = run_site(u, P.SITE_RESUME, mono=1)
    assert log == list(STOCK.items())
    u = unit("SHINJUKU")                          # filter moved the luma weights, then OFF: the 0x92050 word again
    run_site(u, P.SITE_RESUME, mono=1)
    u.on_change(B.ACT, 0)
    last, log = run_site(u, P.SITE_RESUME, mono=1)
    assert log[-1] == ("1201800c", "4008993a") and len(log) == len(STOCK) + 1
    last, log = run_site(u, P.SITE_RESUME, mono=1)
    assert log == list(STOCK.items())             # once restored, nothing more


def test_level_a_never_uploads():
    u = unit("POSITIVE")
    for site in SITES:
        run_site(u, site, mono=0)
        assert not u.cc2 and not u.cc1 and u.lutload == 0


def test_level_b_curve_and_caches():
    for name in ("POSITIVE", "HARD MONO"):
        u = unit(name, opts={"curve": "on"})
        p = B.RECIPES[TB.rid(name) - 1]["params"]
        mono = 0 if B.RECIPES[TB.rid(name) - 1]["type"] else 2
        for site in (P.SITE_START, P.SITE_RESUME):
            u.w8(P.CC1_CACHE, 0x05)
            u.w8(B.CC2_CACHE, 0x05)
            run_site(u, site, mono=mono)
            assert len(u.cc2) == 16 and len(u.cc1) == 16, (name, hex(site))
            assert u.stream() == B.curve(p) and u.stream1() == B.curve(p), name
            assert u.u8(P.CC1_CACHE) == 0xFF and u.u8(B.CC2_CACHE) == 0xFF, name
        run_site(u, P.SITE_SETTING, mono=mono)          # live stream: registers only
        assert not u.cc2 and not u.cc1
        u.on_change(B.ACT, 0)
        u.w8(P.CC1_CACHE, 0x05)
        u.w8(B.CC2_CACHE, 0x05)
        run_site(u, P.SITE_RESUME, mono=mono)           # OFF after a preview curve: stock LUT load once, caches 0xff
        assert u.lutload == 1 and u.u8(P.CC1_CACHE) == 0xFF and u.u8(B.CC2_CACHE) == 0xFF
        run_site(u, P.SITE_RESUME, mono=mono)
        assert u.lutload == 1


def _arm_awb(u, rg, b, active):
    """The monitor AWB tail 0x9a358..0x9a368 (ARM): bl 0x174e94(&[sp+0x14]), bl 0x174e88(&[sp+0x10])."""
    uc = u.uc
    u.mmio.clear()
    u.w8(P.MON_ACTIVE, active)
    sp = STACK - 0x40
    uc.mem_write(sp + 0x10, struct.pack(">II", b, rg))
    for r, v in SENT.items():
        uc.reg_write(r, v)
    uc.reg_write(UC_ARM_REG_SP, sp)
    uc.reg_write(UC_ARM_REG_CPSR, (uc.reg_read(UC_ARM_REG_CPSR) & ~0xFF & ~0x20) | 0x13 | 0xC0)
    uc.emu_start(0x9A358, 0x9A368, count=100_000)
    assert uc.reg_read(UC_ARM_REG_PC) == 0x9A368 and not uc.reg_read(UC_ARM_REG_CPSR) & 0x20
    assert uc.reg_read(UC_ARM_REG_SP) == sp and all(uc.reg_read(r) == v for r, v in SENT.items())
    return dict(u.mmio)


def test_level_c_warmth_veneers():
    u = unit("RETRO", opts={"warmth": "on"})          # RETRO: WARMTH +2
    p = B.RECIPES[TB.rid("RETRO") - 1]["params"]
    s = p[B.P_WARM] - 3
    assert s == 2
    for q in P.make({"warmth": "on"}).pointers:
        assert struct.unpack(">I", u.read(q.addr, 4))[0] == u.psym(q.label) + 1
    assert _arm_awb(u, 0x00400040, 0x40, active=1) == {"12018110": "00400040", "12018114": "00000040"}   # not latched
    run_site(u, P.SITE_START, mono=0)
    m = _arm_awb(u, 0x00400040, 0x40, active=1)
    r = min(0x1FF, (0x40 * (65536 + P.WARM_Q16 * s)) >> 16)
    bb = min(0x1FF, (0x40 * (65536 - P.WARM_Q16 * s)) >> 16)
    assert m == {"12018110": "%08x" % ((r << 16) | 0x40), "12018114": "%08x" % bb}, m
    assert _arm_awb(u, 0x00400040, 0x40, active=0) == {"12018110": "00400040", "12018114": "00000040"}   # develop
    u.on_change(B.ACT, TB.rid("POSITIVE"))              # WARMTH 0: stock
    run_site(u, P.SITE_START, mono=0)
    assert _arm_awb(u, 0x00400040, 0x40, active=1) == {"12018110": "00400040", "12018114": "00000040"}
    u.on_change(B.ACT, TB.rid("SHINJUKU"))              # B&W: nothing latched
    u.w8(0x1B030, 3)
    run_site(u, P.SITE_START, mono=1)
    assert _arm_awb(u, 0x00400040, 0x40, active=1) == {"12018110": "00400040", "12018114": "00000040"}


def test_full_build_links_with_the_release_set():
    from koma import features as F
    feats = [F.make(n, {"cp15": "off"} if n == "telemetry" else
                    ({"curve": "on", "warmth": "on"} if n == "preview" else None)) for n in F.KOMA03]
    lr = link(feats)
    assert [f["name"] for f in lr.manifest["features"]][-2:] == ["preview", "telemetry"]
