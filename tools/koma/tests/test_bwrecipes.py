"""bwrecipes v4 (Koma 0.2: B&W + SELECT + color): unit-emulated checks (Unicorn on the 2.40 payload, firmware callees
faked).

Covered: install; the list (IMG SET / ADJ / KOMA RECIPE order and the label text in the module); defaults of every
look and of CUSTOM 1..3 (= MONO); menu commit / load; ADJ stepping across the new list with live apply (bases 1/3/6);
every curve stream against the host reference; filter / toning / sharpness of every B&W look; chroma gain / matrix /
WB of every color look and SELECT in both passes; every editor sub-setting of each type committed through the exported
on_change (record + shot effect); SAVE TO CUSTOM (kind kept); RESET (base follows); the RCP2 v3 reset, the RCP4 v4 and
RCP5 v5 -> RCP6 v6 migrations (active id cleared, F3); GRAIN / VIGNETTE (FX table: commit, SAVE TO CUSTOM, RESET,
published +11 / +12); kind validation; SELECT's published COLOR / RANGE; the matrices against matrix_model (derivation,
encode round trip, TINT direction); menu5's per-type editor rows.
"""
from __future__ import annotations

import hashlib
import struct

from _util import fw
from koma.features import bwrecipes as B
from koma.linker import link
from koma.unitemu import RES_POOL, Unit
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R7

CP = 0x700000
MAP = {0: 0, 1: 1, 2: 2, 3: 3, 4: 6, 5: 4, 6: 5}         # menu cursor-1 -> value (0x1bd452)
INV = {v: k + 1 for k, v in MAP.items()}
ADJ_APPLY = {1: 0, 2: 1, 3: 2, 4: 3, 5: 6, 6: 4}         # 0x778e0: cursor -> value, else 5
AUTO_ROW = B.IMG_ROWS[0]

# List order and the look definitions (base, con, key, shd, hil, filter, tone, level, sharp)
ORDER = ["MONO", "SHINJUKU", "HICON B&W", "HARD MONO", "SOFT MONO", "FILM 400", "FINE 100", "DENSE", "NOIR", "MATTE",
         "SELECT", "POSITIVE", "NEGATIVE", "BLEACH", "RETRO", "WARM NEG", "SLIDE 50", "CUSTOM 1", "CUSTOM 2", "CUSTOM 3"]
SPEC = {"MONO": ("R3", 0, 0, 0, 0, "OFF", "OFF", 3, 1),
        "SHINJUKU": ("HI-CONT", 2, 0, -2, 1, "RED", "OFF", 3, 2),
        "HICON B&W": ("HI-CONT", 0, 0, 0, 0, "OFF", "OFF", 3, 2),
        "HARD MONO": ("HARD", 2, 0, 0, 0, "OFF", "BLUE", 1, 1),
        "SOFT MONO": ("SOFT MONO", 0, 0, 0, 0, "OFF", "OFF", 3, -1),
        "FILM 400": ("FILM 400", 1, 0, 0, 0, "YELLOW", "OFF", 3, 1),
        "FINE 100": ("FINE 100", 0, 0, 0, 0, "OFF", "OFF", 3, 0),
        "DENSE": ("SOFT MONO", 1, -1, 0, 0, "OFF", "OFF", 3, 1),
        "NOIR": ("HI-CONT", 2, -2, 0, 0, "RED", "OFF", 3, 1),
        "MATTE": ("STANDARD", -3, 0, 3, -2, "OFF", "OFF", 3, 0)}
# color looks: con, key, shd, hil, SAT, WARMTH, TINT, sharp, matrix src. POSITIVE / BLEACH: GRD IV firmware refit
# (v4.1); the other four: PROPOSAL section 2 starting values, checked against B.REFS in v4.2 (NEGATIVE, WARM NEG). Base STANDARD except BLEACH (CBASE).
CSPEC = {"POSITIVE": (2, 2, -3, -3, -1, 0, 0, 0, "grd4:positive-film"),
         "NEGATIVE": (-1, 1, 2, 0, -1, 0, 0, 0, "gr-negative-film"),
         "BLEACH": (0, 0, 0, 0, -3, -3, 0, 1, "grd4:bleach-bypass"),
         "RETRO": (-2, 1, 4, -2, -1, 2, 0, 0, "gr-retro"),
         "WARM NEG": (-2, 2, 2, -3, 0, 3, -1, 0, "portra-400-like"),
         "SLIDE 50": (3, 0, -2, -1, 3, 1, 0, 0, "fuji-velvia")}
CBASE = {"BLEACH": "BLEACH"}
CREGS = ("12018004", "12018104", "12018108", "1201810c", "12018110", "12018114")
INVP1 = {v: k for k, v in B.P1.items()}
HDR = B.MAGIC + bytes([B.STORE_VER])                     # 'RCP6' v6 (Koma 0.3)
# The IMG SET list in GR III order, STANDARD first; ADJ LEVER defaults
DISPLAY = ["STANDARD", "MONO", "SOFT MONO", "HARD MONO", "HICON B&W", "SHINJUKU", "FILM 400", "FINE 100", "DENSE",
           "NOIR", "MATTE", "SELECT", "POSITIVE", "NEGATIVE", "SLIDE 50", "WARM NEG", "BLEACH", "RETRO", "CUSTOM 1",
           "CUSTOM 2", "CUSTOM 3"]
ADJ_ON = ["STANDARD", "MONO", "HARD MONO", "HICON B&W", "SHINJUKU", "POSITIVE", "CUSTOM 1"]
GHOST = B.NDISP + 1


class RU(Unit):
    """Unit with Python fakes for firmware callees (run before the base class's fixed stubs)."""

    def __init__(self, module, **kw):
        self.fakes, self.cc2, self.te, self.trace = {}, [], [], []
        super().__init__(module, **kw)

    def _code(self, uc, a, size, _):
        if a in self.fakes:
            self.fakes[a](self)
            self._ret()
            return
        if a == B.CC2_UP:
            r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            assert r0 <= 0xFFF and 1 <= r1 <= 0x1000 and r0 + r1 <= 0x1000, (r0, r1)
            self.cc2.append((r0, list(struct.unpack(f">{r1}H", bytes(uc.mem_read(r2, 2 * r1))))))
            self._ret(0)
            return
        if a == B.TE_SET:
            self.te.append(struct.unpack(">I", bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R0), 4)))[0])
            self._ret()
            return
        super()._code(uc, a, size, _)

    def w8(self, a, v):
        self.uc.mem_write(a, bytes([v]))

    def stream(self):
        out = []
        for off, vals in self.cc2:
            assert off == len(out)
            out += vals
        return out

    def sym(self, name):
        return RES_POOL + self.link.symbols[f"kf_bwrecipes__{name}"] - self.link.symbols["koma_res"]

    def on_change(self, byte, value):
        """menu5's commit: write the row byte, then call the exported on_change (r0 = value, r1 = byte)."""
        self.w8(byte, value)
        self.thumb(self.sym("on_change"), {UC_ARM_REG_R0: value, UC_ARM_REG_R1: byte})

    def rec(self, rid):
        return self.read(B.STDREC if rid == B.STD else B.REC + (rid - 1) * B.RECLEN, B.RECLEN)


def _commit_fake(u):                     # 0x5b4e4 r0 = 0/1: 0x1b030 = map[cursor]
    c = u.u8(AUTO_ROW + 9)
    u.w8(0x1B030, MAP[c - 1] if 1 <= c <= 7 else 0x28)


def _load_fake(u):                       # 0x5aaf0: cursor from 0x1b030
    v = u.u8(0x1B030)
    u.w8(AUTO_ROW + 9, INV.get(v, 1))


def _adj_apply(u):                       # 0x77450: IMG SET from the ADJ cursor, live
    u.w8(0x1B030, ADJ_APPLY.get(u.u8(B.ADJD + 9), 5))


def _adj_commit(u):                      # 0x59314: clamp 1..7, then map
    c = u.u8(B.ADJD + 9)
    if not 1 <= c <= 7:
        c = 1
        u.w8(B.ADJD + 9, 1)
    u.w8(0x1B030, MAP[c - 1])


def unit(act=0, imgset=1, store=None, adj_type=B.ADJ_IMGSET, opts=None, tail=None):
    lr = link([B.make(opts or {})])
    # 0x1bf090 is a 32-bit big-endian word (0x77040: ldr r0,[r7,#0x4c]; switch on it): IMG SET = 00 00 00 08
    edits = {0x1A91BD: b"\x05", 0x1A91E3: b"\x05", 0x1A91EF: b"\x05", 0x1BF01D: b"\x00",
             B.ADJ_TYPE: struct.pack(">I", adj_type),
             CP + 0x22: b"\x01", CP + 0x268: struct.pack(">3H", 185, 64, 172)}
    u = RU(lr.module, ram_edits=edits, settings_imgset=imgset)
    u.link = lr
    u.uc.mem_map(0x08010000, 0x10000)                             # adjustment store: 0x080113f8 = 0 -> stock word
    u.uc.mem_write(B.ACT, tail if tail is not None else bytes([act]) + bytes(B.RST - B.ACT))
    if store is not None:
        u.uc.mem_write(B.ST, store)
    u.fakes.update({B.COMMIT_AUTO: _commit_fake, B.LOAD_AUTO: _load_fake, B.ADJ_STEP: _adj_apply,
                    B.ADJ_COMMIT: _adj_commit, B.SITE_ADJ_OPEN[1]: lambda u: None,
                    B.SHARP_GET: lambda u: u.uc.reg_write(UC_ARM_REG_R0, 3)})
    assert u.install() == 0
    return u


def call_site(u, site, r0=0):
    u.calls.clear()
    u.mmio.clear()
    u.cc2.clear()
    u.te.clear()
    return u.thumb(site, {UC_ARM_REG_R0: r0, UC_ARM_REG_R7: CP}, stop=site + 4)


def shot(u, mono=1, pass_=1):
    u.w8(CP + 0x22, mono)
    lo = 0x96430 if pass_ == 1 else 0xAFD38
    u.calls.clear()
    u.mmio.clear()
    u.cc2.clear()
    u.te.clear()
    u.thumb(lo, {UC_ARM_REG_R7: CP}, stop=lo + 10)
    return {a: v for a, v in u.mmio}


def rid(name):
    return [r["name"] for r in B.ALL].index(name) + 1


def pos(name):
    return DISPLAY.index(name) + 1


def select(u, name):
    """KOMA page RECIPE row commit."""
    u.on_change(B.ACT, rid(name))
    return rid(name)


def kind_of(name):
    return B.RECIPES[rid(name) - 1]["type"]


def expect_shot(u, p, name="", kind=0):
    """One pass-1 shot (+ pass 2) with params p active: curve and sharpness; B&W: filter word, red tilt, toning word;
    color / SELECT: chroma gain (+ matrix, WB) in both passes, nothing of the B&W path."""
    if kind & 3:
        assert u.u8(0x1B030) == 1, name
        want = {"%08x" % a: "%08x" % v for a, v in B.color_regs(p, kind).items()}
        for pass_ in (1, 2):
            m = shot(u, mono=0, pass_=pass_)
            assert {a: v for a, v in m.items() if a in CREGS} == want, (name, pass_, m, want)
            assert m.get("1201800c") in (None, "4008993a") and not u.te, name
            assert len(u.cc2) == (16 if pass_ == 1 else 0), name
            if pass_ == 1:
                assert u.stream() == B.curve(p), name
        assert call_site(u, B.SITES_SHARP[0]) == p[B.P_SHARP] + 1, name
        return
    toned = p[B.P_TONE] != 0
    assert u.u8(0x1B030) == (6 if toned else 3), name
    m = shot(u, mono=2 if toned else 1)
    assert u.stream() == B.curve(p) and len(u.cc2) == 16, name
    assert not set(m) & set(CREGS[:4]), name                       # no chroma / matrix writes on B&W
    f = p[B.P_FILT]
    want_yw = ("%08x" % B.FTAB[f - 1][0],) if f else (None, "4008993a")    # OFF: untouched, or the stock word
    assert m.get("1201800c") in want_yw, (name, m)                         # put back after a filtered shot
    assert ("12018110" in m) == (B.FILTERS[f] == "RED"), name
    if toned:
        cb, cr = B.tone_table()[p[B.P_TONE] - 1][p[B.P_DENS]]
        assert u.te == [(1 << 24) | ((cr & 0x3FF) << 12) | (cb & 0x3FF)], name
    else:
        assert not u.te, name
    m2 = shot(u, mono=2 if toned else 1, pass_=2)                 # pass 2: filter + toning again, no LUT
    assert m2.get("1201800c") in want_yw and not u.cc2, name
    assert call_site(u, B.SITES_SHARP[0]) == p[B.P_SHARP] + 1, name


def glyph_text(u, table):
    s = struct.unpack(">I", u.read(table, 4))[0]                   # language slot 0 -> glyph string
    out = ""
    while True:
        page, g = u.read(s, 2)
        if page == 0:
            return out
        assert page == 1
        out += INVP1[g]
        s += 2


def xor_ok(rec):
    x = 0x5A
    for b in rec[:10]:
        x ^= b
    return rec[11] == x


def v4_store(records):
    """An RCP4 v4 store (Koma 0.2 B&W, 13 records) with {id: params} written as valid B&W records."""
    st = bytearray(b"RCP4\x04\xff\xff" + bytes([B.V4_N]) + bytes(8))
    for k in range(1, B.V4_N + 1):
        r = bytearray(12)
        if k in records:
            r[:9] = bytes(records[k])
            r[10] = B.VALID
            x = 0x5A
            for b in r[:10]:
                x ^= b
            r[11] = x
        st += r
    return bytes(st)


# ------------------------------------------------------------------ tests
def test_install_and_records():
    u = unit()
    for r in B.IMG_ROWS:
        assert u.u8(r + 8) == B.NDISP == 21
        assert struct.unpack(">I", u.read(r + 4, 4))[0] == struct.unpack(">I", u.read(B.ADJ_ITEM, 4))[0]
    assert u.u8(B.ADJ_ITEM + 4) == B.NDISP
    assert u.read(B.ST, 5) == HDR                    # stale tail (0x03 fill) -> fresh store
    assert u.read(B.ST + 8, 5) == bytes(5)                   # published bytes: no recipe (+13 = FX row owner)
    assert u.callee_saved_ok
    assert (B.N, B.NCUR, B.NCUSTOM, B.NB, B.NBW, B.NCOL, B.STD) == (20, 17, 3, 8, 10, 6, 21)
    m = u.read(B.AJM, 4)                                          # fresh store: the default ADJ LEVER mask
    assert int.from_bytes(m[:3], "little") == B.DEFMASK and m[3] == m[0] ^ m[1] ^ m[2] ^ 0x5A
    assert u.u8(B.STDREC + 10) != B.VALID and fxt(u, B.STD) == 0


def test_list_order_and_labels():
    """IMG SET list = the 21 looks in GR III order (STANDARD first; no stock HARD / SOFT / B&W / B&W(TE) / SETTING1/2),
    plus the ghost slot; the look-id row (no page; editor title) = OFF + the looks by id + STANDARD; the editor rows
    (declared labels) and every per-type variant meet the naming rules."""
    assert [r["name"] for r in B.RECIPES] == ORDER and B.DISPLAY == DISPLAY
    assert B.ROWS[0]["options"] == ["OFF"] + ORDER + ["STANDARD"] and B.ROWS[0]["group"] == "recipe-title"
    u = unit()
    opts = struct.unpack(">I", u.read(AUTO_ROW + 4, 4))[0]
    ptrs = struct.unpack(f">{B.NDISP + 1}I", u.read(opts, 4 * (B.NDISP + 1)))
    assert [glyph_text(u, p) for p in ptrs[:B.NDISP]] == DISPLAY
    assert [glyph_text(u, struct.unpack(">I", u.read(u.sym("rnames") + 4 * k, 4))[0]) for k in range(B.NALL + 1)] == \
        B.ROWS[0]["options"]                                      # menu5's editor title list (shared, not copied)
    assert set(B.DISPLAY) == {r["name"] for r in B.ALL}
    for r in B.ROWS:                                              # naming rules
        alts = [(r["label"], r["options"])] + list(r.get("variants", {}).get("alts", []))
        for lab, o in alts:
            assert len(lab) <= 14 and all(len(x) <= 9 for x in o), r["id"]
            assert all(c in B.P1 for x in o + [lab] for c in x), r["id"]
            assert len(o) <= len(r["options"]), r["id"]            # declared list is the longest (boot repair)
    assert [r["label"] for r in sorted(B.ROWS[1:], key=lambda r: r["byte"])] == [
        "CURVE", "CONTRAST", "KEY", "SHADOW", "HIGHLIGHT", "SATURATION", "WARMTH", "TINT", "SHARPNESS",
        "SAVE TO CUSTOM", "RESET", "GRAIN", "VIGNETTE", "ADJ LEVER"]
    per_type = [[r["variants"]["alts"][t][0] for r in B.ROWS if "variants" in r] for t in range(3)]
    assert per_type == [["FILTER EFF.", "TONING", "TONE LEVEL"], ["SATURATION", "WARMTH", "TINT"],
                        ["COLOR", "RANGE", "SATURATION"]]
    assert all(r["variants"]["sel"] == B.SELB for r in B.ROWS if "variants" in r)


def test_defaults_of_every_look():
    for name, (base, con, key, shd, hil, filt, tone, dens, sharp) in SPEC.items():
        p = [B.B[base], con + 4, key + 4, shd + 4, hil + 4, B.F[filt], B.T[tone], dens - 1, sharp + 2]
        assert B.RECIPES[rid(name) - 1]["params"] == p and kind_of(name) == B.TYPE_BW, name
    for look, (name, (con, key, shd, hil, sat, warm, tint, sharp, src)) in enumerate(CSPEC.items()):
        r = B.RECIPES[rid(name) - 1]
        assert r["params"] == [B.B[CBASE.get(name, "STANDARD")], con + 4, key + 4, shd + 4, hil + 4, sat + 3, warm + 3, tint + 3,
                               sharp + 2], name
        assert r["type"] == B.TYPE_COLOR | (look << 4) and r["src"] == src, name
    sel = B.RECIPES[rid("SELECT") - 1]
    assert sel["type"] == B.TYPE_SEL and sel["params"] == [B.B["R3"], 4, 4, 4, 4, 0, 0, 3, 3]   # RED NARROW x1.0 +1
    for i in range(1, 4):
        assert B.RECIPES[rid(f"CUSTOM {i}") - 1]["params"] == B.RECIPES[rid("MONO") - 1]["params"]
        assert kind_of(f"CUSTOM {i}") == B.TYPE_BW
    u = unit()
    for i, r in enumerate(B.RECIPES, 1):                          # selected on the KOMA page: rows = defaults
        u.on_change(B.ACT, i)
        assert list(u.read(B.EB, B.NPARAM)) == r["params"], r["name"]
        assert u.u8(0x1B030) == B.imgset_of(r["params"], r["type"]), r["name"]
        assert u.u8(B.SELB) == r["type"] & 3, r["name"]            # menu5 selector = type
        assert u.rec(i)[10] != B.VALID                            # selecting writes no record
    tab = u.sym("deftab")                                          # default table: 17 looks x (9 params + kind)
    assert u.read(tab, B.NCUR * B.DEFLEN) == bytes(x for r in B.CURATED for x in r["params"] + [r["type"]])


def test_menu_commit_look_standard_and_ghost():
    """IMG SET commit: a look's display position -> its id + base; STANDARD -> plain NORMAL, no id (at its defaults);
    the ghost commits the hidden stock value unchanged, no id."""
    u = unit()
    for name, base in (("MONO", 3), ("HARD MONO", 6), ("POSITIVE", 1), ("SHINJUKU", 3), ("SELECT", 1),
                       ("CUSTOM 3", 3), ("SLIDE 50", 1)):
        i = rid(name)
        u.w8(AUTO_ROW + 9, pos(name))
        call_site(u, 0x58882, r0=1)
        assert (u.u8(0x1B030), u.u8(B.ACT)) == (base, i), name
        assert u.u8(AUTO_ROW + 9) == pos(name)                    # display cursor kept: row shows the name
        assert list(u.read(B.EB, B.NPARAM)) == B.ALL[i - 1]["params"]
    u.w8(AUTO_ROW + 9, pos("STANDARD"))
    call_site(u, 0x58882, r0=1)
    assert (u.u8(0x1B030), u.u8(B.ACT), u.u8(AUTO_ROW + 9)) == (1, 0, 1)
    u.w8(0x1B030, 4)                                              # SETTING1 recalled elsewhere (MY SETTINGS)
    call_site(u, 0x5B394)                                         # menu load: ghost entry
    assert (u.u8(AUTO_ROW + 8), u.u8(AUTO_ROW + 9)) == (GHOST, GHOST)
    call_site(u, 0x58882, r0=1)                                   # left on the ghost: SETTING1 stays
    assert (u.u8(0x1B030), u.u8(B.ACT)) == (4, 0)


def test_menu_load_shows_look_standard_or_ghost():
    u = unit()
    for name, base in (("FILM 400", 3), ("NEGATIVE", 1)):
        i = rid(name)
        u.w8(B.ACT, i)
        call_site(u, 0x5B394)                                     # load: sync sets the base, cursor = its position
        assert u.u8(0x1B030) == base and u.u8(AUTO_ROW + 9) == pos(name) and u.u8(AUTO_ROW + 8) == B.NDISP
        u.w8(0x1B030, 0 if base == 1 else 1)                      # IMG SET changed elsewhere (e.g. MY SETTINGS)
        call_site(u, 0x5B446)
        assert u.u8(B.ACT) == 0
        if base == 3:                                             # NORMAL: shown as STANDARD, no ghost
            assert (u.u8(AUTO_ROW + 8), u.u8(AUTO_ROW + 9)) == (B.NDISP, 1)
        else:                                                     # HARD: hidden, ghost entry with its stock label
            assert (u.u8(AUTO_ROW + 8), u.u8(AUTO_ROW + 9)) == (GHOST, GHOST)
            opts = struct.unpack(">I", u.read(AUTO_ROW + 4, 4))[0]
            assert u.read(opts + 4 * B.NDISP, 4) == fw.ram(B.IMG_OPTS, 4)          # HARD's stock label word
    for v, cur in ((2, 3), (3, 4), (6, 5), (5, 7)):                # SOFT, B&W, B&W(TE), SETTING2: their stock labels
        u.w8(0x1B030, v)
        call_site(u, 0x5B394)
        opts = struct.unpack(">I", u.read(AUTO_ROW + 4, 4))[0]
        assert u.u8(AUTO_ROW + 9) == GHOST and u.read(opts + 4 * B.NDISP, 4) == fw.ram(B.IMG_OPTS + 4 * (cur - 1), 4)


def adj_open(u):
    """ADJ lever opened on IMG SET: the hook builds the list; returns [(label, look id)] and the cursor."""
    call_site(u, B.SITE_ADJ_OPEN[0])
    n = u.u8(B.ADJ_ITEM + 4)
    opts = struct.unpack(">I", u.read(B.ADJ_ITEM, 4))[0]
    amap = u.sym("adjmap")
    out = []
    for k in range(n):                                            # the ghost (id 0): its stock label word
        w, i = struct.unpack(">I", u.read(opts + 4 * k, 4))[0], u.u8(amap + k)
        out.append((glyph_text(u, w) if i else w, i))
    return out, u.u8(B.ADJD + 9)


def adj_walk(u, n, start):
    seen, c = [], start
    for _ in range(n + 1):
        c = c + 1 if c < n else 1                                 # stock key handler 0x13cf10: wrap at count
        u.w8(B.ADJD + 9, c)
        call_site(u, B.SITES_ADJ_STEP[0])
        assert u.u8(B.ADJD + 9) == c
        seen.append((u.u8(0x1B030), u.u8(B.ACT)))
    return seen


def test_adj_lever_walks_the_on_looks_in_display_order():
    """The ADJ lever lists the looks with ADJ LEVER = ON (defaults: 7 looks) in display order, wrapping; the
    current look stays in the list even when OFF (one step leaves it); a hidden stock value is the ghost (last);
    no look ON: only the current entry. Commit keeps a look (no clamp to NORMAL)."""
    u = unit()
    lst, cur = adj_open(u)                                        # IMG SET NORMAL, no look: STANDARD current
    assert [n for n, _ in lst] == [n for n in DISPLAY if n in ADJ_ON] and cur == 1
    assert [k for _, k in lst] == [rid(n) for n in DISPLAY if n in ADJ_ON]
    seen = adj_walk(u, len(lst), cur)
    want = [(B.imgset_of(B.ALL[rid(n) - 1]["params"], B.ALL[rid(n) - 1]["type"]), rid(n))
            for n in DISPLAY if n in ADJ_ON]
    assert seen == want[1:] + [(1, 0), want[1]]                  # back on STANDARD: plain NORMAL, id 0
    i = rid("NOIR")                                               # current look OFF: listed at its place
    u.w8(B.ADJD + 9, 1)
    call_site(u, B.SITES_ADJ_COMMIT[0])
    u.on_change(B.ACT, i)
    lst, cur = adj_open(u)
    names = [n for n, _ in lst]
    assert names == [n for n in DISPLAY if n in ADJ_ON or n == "NOIR"] and names[cur - 1] == "NOIR"
    u.w8(B.ADJD + 9, cur + 1 if cur < len(lst) else 1)            # one step: the next ON look (POSITIVE)
    call_site(u, B.SITES_ADJ_STEP[1])
    assert u.u8(B.ACT) == rid("POSITIVE")
    for name in ("HARD MONO", "CUSTOM 1", "STANDARD"):            # commit on a look
        k = [n for n, _ in adj_open(u)[0]].index(name) + 1
        u.w8(B.ADJD + 9, k)
        call_site(u, B.SITES_ADJ_COMMIT[0])
        r = B.ALL[rid(name) - 1]
        assert (u.u8(0x1B030), u.u8(B.ADJD + 9)) == (B.imgset_of(r["params"], r["type"]), k), name
        assert u.u8(B.ACT) == (0 if name == "STANDARD" else rid(name)), name
    u.w8(0x1B030, 5)                                              # SETTING2 (hidden): the ghost, last
    lst, cur = adj_open(u)
    setting2 = struct.unpack(">I", fw.ram(B.IMG_OPTS + 4 * 6, 4))[0]                 # stock cursor 7
    assert lst[-1] == (setting2, 0) and cur == len(lst) and len(lst) == len(ADJ_ON) + 1
    seen = adj_walk(u, len(lst), cur)
    assert seen[-2] == (5, 0)                                     # back on the ghost: SETTING2 again, no id


def test_adj_lever_row_per_look():
    """Editor row ADJ LEVER (0x1b8ce, 0 ON / 1 OFF) follows the active look's bit; a change goes into the mask (with its
    check byte) and survives a menu reload; a bad check byte reads as the default mask; no look ON -> one entry."""
    u = unit()
    for name in DISPLAY:
        u.on_change(B.ACT, rid(name))
        assert u.u8(B.AJROW) == (0 if name in ADJ_ON else 1), name
    u.on_change(B.ACT, rid("NOIR"))
    u.on_change(B.AJROW, 0)                                       # NOIR -> ON
    u.on_change(B.ACT, rid("MONO"))
    u.on_change(B.AJROW, 1)                                       # MONO -> OFF
    m = u.read(B.AJM, 4)
    mask = int.from_bytes(m[:3], "little")
    assert mask == (B.DEFMASK | 1 << (rid("NOIR") - 1)) & ~(1 << (rid("MONO") - 1))
    assert m[3] == m[0] ^ m[1] ^ m[2] ^ 0x5A
    call_site(u, 0x5B394)
    u.on_change(B.ACT, rid("NOIR"))
    assert u.u8(B.AJROW) == 0
    u.on_change(B.ACT, rid("MONO"))
    assert u.u8(B.AJROW) == 1
    u.w8(B.AJROW, 7)                                              # out of range row: reloaded, mask untouched
    u.on_change(B.ACT, rid("MONO"))
    assert u.u8(B.AJROW) == 1 and u.read(B.AJM, 4) == m
    u.on_change(B.RST, 1)                                         # RESET keeps the lever choice
    assert u.read(B.AJM, 4) == m
    u.w8(B.AJM + 3, m[3] ^ 1)                                     # bad check: default mask
    u.on_change(B.ACT, rid("HICON B&W"))
    assert u.u8(B.AJROW) == 0
    u.on_change(B.ACT, rid("NOIR"))
    assert u.u8(B.AJROW) == 1
    u.uc.mem_write(B.AJM, bytes([0, 0, 0, 0x5A]))                 # no look ON: only the current entry
    u.on_change(B.ACT, rid("DENSE"))
    lst, cur = adj_open(u)
    assert lst == [("DENSE", rid("DENSE"))] and cur == 1
    assert adj_walk(u, 1, 1) == [(3, rid("DENSE"))] * 2


def test_imgset_right_opens_the_editor_and_standard_normalises():
    """menu5's RIGHT on an IMG SET entry (imgright): the entry becomes the row value and the active look (r0 = 1); the
    ghost and other rows: r0 = 0. STANDARD while edited keeps id 21 (editor session); imgdone ends the session: at its
    defaults (or after RESET) STANDARD goes back to plain NORMAL, id 0, record dropped; edited it stays a look."""
    from unicorn.arm_const import UC_ARM_REG_R0 as R0, UC_ARM_REG_R1 as R1
    u = unit()
    call_site(u, 0x5B394)
    right = lambda c, row=AUTO_ROW: u.thumb(u.sym("imgright"), {R0: c, R1: row})     # noqa: E731
    done = lambda: u.thumb(u.sym("imgdone"), {})                                       # noqa: E731
    assert right(pos("HARD MONO")) == 1
    assert (u.u8(B.ACT), u.u8(AUTO_ROW + 9), u.u8(0x1B030)) == (rid("HARD MONO"), pos("HARD MONO"), 6)
    done()
    assert right(GHOST) == 0 and right(0) == 0 and right(3, row=B.IMG_ROWS[0] - 12) == 0
    assert u.u8(B.ACT) == rid("HARD MONO")
    assert right(pos("STANDARD")) == 1                            # STANDARD: an editable look while editing
    assert (u.u8(B.ACT), u.u8(0x1B030), u.u8(B.SELB)) == (B.STD, 1, B.TYPE_COLOR)
    assert list(u.read(B.EB, B.NPARAM)) == B.STANDARD["params"]
    u.on_change(B.EB + B.P_CON, 5)                                # CONTRAST +1
    u.on_change(B.EB + B.P_CON, 4)                                # and back: still in the session, id kept
    assert u.u8(B.ACT) == B.STD
    done()
    assert (u.u8(B.ACT), u.u8(0x1B030), u.u8(B.STDREC + 10)) == (0, 1, 0)    # plain NORMAL, no look id
    assert shot(u) == {} and not u.cc2
    right(pos("STANDARD"))
    u.on_change(B.EB + B.P_CON, 6)                                # CONTRAST +2: STANDARD becomes a real look
    done()
    p = list(B.STANDARD["params"])
    p[B.P_CON] = 6
    assert u.u8(B.ACT) == B.STD and list(u.rec(B.STD)[:9]) == p and xor_ok(u.rec(B.STD))
    expect_shot(u, p, "STANDARD +2", B.STANDARD["type"])
    call_site(u, 0x5B394)                                         # menu load: STANDARD shows at position 1
    assert u.u8(AUTO_ROW + 9) == 1 and u.u8(AUTO_ROW + 8) == B.NDISP
    right(pos("STANDARD"))
    u.on_change(B.RST, 1)                                         # RESET: back to plain NORMAL when the editor closes
    done()
    assert (u.u8(B.ACT), u.u8(0x1B030)) == (0, 1)
    right(pos("STANDARD"))                                        # GRAIN on STANDARD also keeps the look
    u.on_change(B.FXG, 2)
    done()
    assert u.u8(B.ACT) == B.STD and fxt(u, B.STD) == 0x52
    u.on_change(B.FXG, 0)
    assert u.u8(B.ACT) == 0                                       # outside an editor session: normalised at once


def test_store_from_koma04_keeps_records_and_gets_the_look_ia_bytes():
    """A valid 0.4 'RCP6' store (records + FX table, nothing after it) stays as it is; the ADJ LEVER mask reads as the
    defaults until a row writes it; STANDARD has no record (defaults). Downgrade-safe: header, records 1..20 and the
    FX table bytes 1..20 keep their 0.4 layout."""
    mono, c2 = rid("MONO"), rid("CUSTOM 2")
    st = bytearray(v5_store({mono: ([5, 8, 4, 4, 4, 3, 0, 2, 4], 0), c2: ([1, 6, 6, 1, 1, 2, 3, 3, 2], 0)}))
    st[0:5] = HDR
    fx = bytes([0x55 if k == mono else 0 for k in range(1, B.N + 1)])
    st += fx + bytes(0x40)
    u = unit(store=bytes(st))
    assert u.read(B.ST, 5) == HDR and u.read(B.REC, len(st) - 0x50) == bytes(st[16:-0x40])   # records + FX untouched
    u.on_change(B.ACT, mono)
    assert list(u.read(B.EB, B.NPARAM)) == [5, 8, 4, 4, 4, 3, 0, 2, 4] and (u.u8(B.FXG), u.u8(B.FXV)) == (1, 1)
    assert u.u8(B.AJROW) == 0                                     # default mask (check byte 0 is bad): MONO ON
    u.on_change(B.ACT, rid("NOIR"))
    assert u.u8(B.AJROW) == 1


def test_every_look_shot():
    u = unit()
    for i, r in enumerate(B.RECIPES, 1):
        u.on_change(B.ACT, i)
        expect_shot(u, r["params"], r["name"], r["type"])         # emulated stream == host curve()
        s = B.curve(r["params"])
        assert s[:258] == list(B.base_table(r["params"][0]))[:258] and s[256] == 64
        assert all(a <= b for a, b in zip(s, s[1:])) and max(s) <= 1023
        assert u.u8(B.CC2_CACHE) == 0xFF
    r3 = B.curve(B.RECIPES[rid("MONO") - 1]["params"][:1] + [4, 4, 4, 4])
    assert hashlib.sha256(struct.pack(">4096H", *r3)).hexdigest().startswith(B.R3_SHA)
    u.on_change(B.ACT, rid("SHINJUKU"))
    shot(u)
    m = shot(u, mono=0)                                           # develop says color: weights restored, no tilt
    assert m == {"1201800c": "4008993a"} and len(u.cc2) == 16
    u.on_change(B.ACT, 0)
    assert shot(u) == {} and not u.cc2                            # no recipe: stock shot


def test_color_regs_values():
    """POSITIVE (GRD IV refit): chroma x0.85 of stock 8470697c (each byte rounded half to even), its matrix rows;
    BLEACH: chroma x0.55, stock-zero matrix rows, WARMTH COOL 3; RETRO (WARM 2) also the WB words from the AWB values
    185/64/172: R 185*16/25*1.07, G 64, B 172*16/25*0.93 (unit-emu == host model)."""
    assert B.chroma_word(3) == B.STOCK_CHROMA and B.chroma_word(5) == 0xAC9288A1 and B.chroma_word(2) == 0x705F5969
    u = unit()
    select(u, "POSITIVE")
    m = shot(u, mono=0)
    assert (m["12018004"], m["12018104"], m["12018108"], m["1201810c"]) == ("705f5969", "01818101", "02828202",
                                                                            "84030383")
    assert "12018110" not in m                                    # WARMTH 0: stock WB stays
    select(u, "BLEACH")
    m = shot(u, mono=0)
    r, b = (185 * (B.WB0 - 3 * B.WK)) >> 16, (172 * (B.WB0 + 3 * B.WK)) >> 16
    assert (m["12018004"], m["12018104"], m["12018108"], m["1201810c"]) == ("493e3a44", "00000000", "00000000",
                                                                            "00000000")
    assert (m["12018110"], m["12018114"]) == ("%08x" % (r << 16 | 64), "%08x" % b)
    select(u, "RETRO")
    m = shot(u, mono=0)
    r, b = (185 * (B.WB0 + 2 * B.WK)) >> 16, (172 * (B.WB0 - 2 * B.WK)) >> 16
    assert (r, b) == (126, 102)
    assert (m["12018110"], m["12018114"]) == ("%08x" % (r << 16 | 64), "%08x" % b)
    wl = u.sym("wlog")
    assert u.read(wl, 8) == B.WL_MAGIC and u.u8(wl + B.WL_PASS) == 1 and u.u8(wl + B.WL_KIND) == kind_of("RETRO")
    assert struct.unpack(">I", u.read(wl + B.WL_WB, 4))[0] == r << 16 | 64


def test_red_tilt_values():
    u = unit()
    select(u, "NOIR")
    for p in (1, 2):
        m = shot(u, pass_=p)
        assert (m["1201800c"], m["12018110"], m["12018114"]) == ("4008ff00", "009c002d", "0000004e")


def _commit_steps(u, i, steps, kind):
    p = list(B.RECIPES[i - 1]["params"])
    for j, v in steps:
        u.on_change(B.EB + j, v)
        p[j] = v
        rec = u.rec(i)
        assert list(rec[:9]) == p and rec[9] == kind and rec[10] == B.VALID and xor_ok(rec), (j, v)
        assert list(u.read(B.EB, B.NPARAM)) == p
        expect_shot(u, p, f"p{j}={v}", kind)
    return p


def test_every_subsetting_commits_and_applies():
    """Each B&W editor row, changed on the KOMA page (byte write + on_change): the look's record holds it (kind 0, valid,
    xor) and the next shot uses it. Toning on/off moves IMG SET between 3 and 6."""
    u = unit()
    i = select(u, "MONO")
    steps = [(B.P_CURVE, B.B["FINE 100"]), (B.P_CON, 8), (B.P_KEY, 1), (B.P_SHD, 6), (B.P_HIL, 2),
             (B.P_FILT, B.F["ORANGE"]), (B.P_FILT, B.F["GREEN"]), (B.P_TONE, B.T["SELENIUM"]), (B.P_DENS, 4),
             (B.P_TONE, B.T["GREEN"]), (B.P_SHARP, 0), (B.P_TONE, B.T["OFF"])]
    p = _commit_steps(u, i, steps, B.TYPE_BW)
    u.on_change(B.ACT, rid("SOFT MONO"))                          # edits stick to MONO across a recipe change
    u.on_change(B.ACT, i)
    assert list(u.read(B.EB, B.NPARAM)) == p
    for j, bad in ((B.P_CURVE, B.NB), (B.P_TONE, len(B.TONES)), (B.P_SHARP, 5)):    # out of range: repaired
        u.on_change(B.EB + j, bad)
        assert list(u.read(B.EB, B.NPARAM)) == p


def test_every_color_subsetting_commits_and_applies():
    """Color editor rows: CURVE (any of the 8 bases), CONTRAST..HIGHLIGHT, SATURATION (chroma gain), WARMTH (WB), TINT
    (G row), SHARPNESS: record kind = the look's (type 1 + look index), the shot writes the new values in both passes;
    IMG SET stays 1. Out-of-range values for the color limits are repaired."""
    u = unit()
    i = select(u, "BLEACH")
    kind = kind_of("BLEACH")
    steps = [(B.P_CURVE, B.B["HI-CONT"]), (B.P_CON, 0), (B.P_KEY, 7), (B.P_SHD, 8), (B.P_HIL, 1),
             (B.P_SAT, 6), (B.P_SAT, 0), (B.P_WARM, 0), (B.P_WARM, 6), (B.P_WARM, 3), (B.P_TINT, 0), (B.P_TINT, 6),
             (B.P_TINT, 4), (B.P_SHARP, 4)]
    p = _commit_steps(u, i, steps, kind)
    assert u.u8(0x1B030) == 1 and u.u8(B.SELB) == B.TYPE_COLOR
    for j, bad in ((B.P_SAT, 7), (B.P_WARM, 9), (B.P_TINT, 0xFF), (B.P_CURVE, B.NB)):
        u.on_change(B.EB + j, bad)
        assert list(u.read(B.EB, B.NPARAM)) == p
    u.on_change(B.ACT, rid("MONO"))                               # color -> B&W -> color: base follows, edits stay
    assert u.u8(0x1B030) == 3 and u.u8(B.SELB) == B.TYPE_BW
    u.on_change(B.ACT, i)
    assert u.u8(0x1B030) == 1 and list(u.read(B.EB, B.NPARAM)) == p


def test_color_base_and_off_restores_stock():
    """A color recipe holds 0x1b030 = 1 (NORMAL) and writes the color registers; OFF leaves plain NORMAL and the next
    shot is stock (no module writes, no LUT); IMG SET moved away (e.g. VIVID) -> the recipe does not apply."""
    u = unit(imgset=3)
    select(u, "NEGATIVE")
    assert u.u8(0x1B030) == 1
    assert set(shot(u, mono=0)) >= {"12018004", "12018104", "12018108", "1201810c"}
    u.on_change(B.ACT, 0)
    assert u.u8(0x1B030) == 1 and shot(u, mono=0) == {} and not u.cc2
    select(u, "NEGATIVE")
    u.w8(0x1B030, 0)
    assert shot(u, mono=0) == {} and not u.cc2
    assert call_site(u, B.SITES_SHARP[1]) == 3                    # stock sharpness passes through


def test_select_params_stored_and_published():
    """SELECT: COLOR (p5) / RANGE (p6) / SATURATION (p7) are editor rows stored in its record (no settings byte of
    their own: 0x1b8cb is RESET); sync publishes COLOR+1 / RANGE at 0x1b909/0x1b90a only while SELECT applies; the
    shot writes the chroma gain only (no matrix, no WB)."""
    from koma.features import selcolour as SC
    assert (SC.COLB, SC.RNGB) == (B.SELCOL, B.SELRNG) and B.RST == 0x1B8CB
    u = unit()
    i = select(u, "SELECT")
    assert u.u8(0x1B030) == 1 and (u.u8(B.SELB), u.u8(B.SELCOL), u.u8(B.SELRNG)) == (2, 1, 0)   # RED NARROW
    p = _commit_steps(u, i, [(B.P_SCOL, 4), (B.P_SRNG, 1), (B.P_SSAT, 5), (B.P_CURVE, B.B["HI-CONT"])], B.TYPE_SEL)
    assert (u.u8(B.SELCOL), u.u8(B.SELRNG)) == (5, 1)             # BLUE WIDE
    m = shot(u, mono=0)
    assert set(m) == {"12018004"} and m["12018004"] == "%08x" % B.chroma_word(5)
    for j, bad in ((B.P_SCOL, 6), (B.P_SRNG, 2), (B.P_SSAT, 7)):  # SELECT limits
        u.on_change(B.EB + j, bad)
        assert list(u.read(B.EB, B.NPARAM)) == p
    u.w8(0x1B030, 3)                                              # base left: no SELECT shot published
    call_site(u, 0x96430 + 6, r0=0)
    assert (u.u8(B.SELCOL), u.u8(B.SELRNG)) == (0, 0)
    u.on_change(B.ACT, rid("POSITIVE"))
    assert (u.u8(B.SELB), u.u8(B.SELCOL)) == (1, 0)
    u.on_change(B.ACT, i)                                         # back: edits kept, published again
    assert list(u.read(B.EB, B.NPARAM)) == p and (u.u8(B.SELCOL), u.u8(B.SELRNG)) == (5, 1)


def test_reset_restores_defaults():
    u = unit()
    i = select(u, "FILM 400")
    u.on_change(B.EB + B.P_TONE, B.T["SEPIA"])                    # toned: IMG SET follows to 6
    u.on_change(B.EB + B.P_CON, 0)
    assert u.u8(0x1B030) == 6 and u.rec(i)[10] == B.VALID
    u.on_change(B.RST, 1)
    assert u.u8(B.RST) == 0 and u.rec(i)[10] != B.VALID
    assert list(u.read(B.EB, B.NPARAM)) == B.RECIPES[i - 1]["params"] and u.u8(0x1B030) == 3
    c = select(u, "CUSTOM 1")                                     # a custom resets to MONO's defaults
    u.on_change(B.EB + B.P_KEY, 8)
    u.on_change(B.RST, 1)
    assert list(u.read(B.EB, B.NPARAM)) == B.RECIPES[rid("MONO") - 1]["params"] and u.rec(c)[10] != B.VALID
    select(u, "WARM NEG")                                         # color saved to CUSTOM 2, then RESET there:
    u.on_change(B.SAVE, 2)                                        # back to MONO (B&W): IMG SET follows 1 -> 3
    c2 = select(u, "CUSTOM 2")
    assert u.u8(0x1B030) == 1 and u.u8(B.SELB) == B.TYPE_COLOR
    u.on_change(B.RST, 1)
    assert u.u8(0x1B030) == 3 and u.u8(B.SELB) == B.TYPE_BW and u.rec(c2)[10] != B.VALID
    expect_shot(u, B.RECIPES[rid("MONO") - 1]["params"], "CUSTOM 2 reset", B.TYPE_BW)


def test_save_to_custom_copies_and_returns_to_off():
    u = unit()
    i = select(u, "NOIR")
    u.on_change(B.EB + B.P_KEY, 1)                                # NOIR key -3
    u.on_change(B.EB + B.P_TONE, B.T["BLUE"])                     # + blue toning (IMG SET 6)
    p = list(u.read(B.EB, B.NPARAM))
    u.on_change(B.SAVE, 2)                                        # SAVE TO CUSTOM 2
    assert u.u8(B.SAVE) == 0 and u.u8(B.ACT) == i                 # row back to OFF; NOIR stays active
    c2 = rid("CUSTOM 2")
    rec = u.rec(c2)
    assert list(rec[:9]) == p and rec[9] == B.TYPE_BW and rec[10] == B.VALID
    assert u.rec(rid("CUSTOM 1"))[10] != B.VALID and u.rec(rid("CUSTOM 3"))[10] != B.VALID
    assert list(u.rec(i)[:9]) == p                                # NOIR keeps its own edits
    u.on_change(B.ACT, c2)                                        # choose CUSTOM 2: the copy, on B&W(TE)
    assert list(u.read(B.EB, B.NPARAM)) == p and u.u8(0x1B030) == 6
    expect_shot(u, p, "CUSTOM 2")
    u.on_change(B.EB + B.P_CON, 0)                                # editing the copy leaves NOIR alone
    assert list(u.rec(i)[:9]) == p
    u.on_change(B.SAVE, 7)                                        # out of range: cleared, nothing written
    assert u.u8(B.SAVE) == 0 and u.rec(rid("CUSTOM 3"))[10] != B.VALID
    u.on_change(B.ACT, 0)                                         # no active recipe: SAVE does nothing
    u.on_change(B.SAVE, 3)
    assert u.u8(B.SAVE) == 0 and u.rec(rid("CUSTOM 3"))[10] != B.VALID


def test_save_color_to_custom_keeps_kind_and_matrix():
    u = unit()
    s = select(u, "SLIDE 50")
    u.on_change(B.EB + B.P_TINT, 5)                               # GREEN 2
    p = list(u.read(B.EB, B.NPARAM))
    u.on_change(B.SAVE, 3)
    c3 = rid("CUSTOM 3")
    rec = u.rec(c3)
    assert list(rec[:9]) == p and rec[9] == kind_of("SLIDE 50") and xor_ok(rec)
    u.on_change(B.ACT, c3)
    assert u.u8(0x1B030) == 1 and u.u8(B.SELB) == B.TYPE_COLOR
    expect_shot(u, p, "CUSTOM 3 = SLIDE 50", kind_of("SLIDE 50"))
    assert shot(u, mono=0)["12018108"] == "%08x" % B.tint_word(B.LOOK_MATRIX[5][1], 2)
    assert list(u.rec(s)[:9]) == p                                # the source keeps its edit


def test_menu_load_and_on_change_both_sync():
    """The load hook path (shot / menu) syncs edit rows too, as v2 did."""
    u = unit()
    i = rid("HARD MONO")
    u.w8(B.ACT, i)
    call_site(u, 0x5B394)
    assert u.u8(0x1B030) == 6
    u.w8(B.EB + B.P_TONE, 0)
    u.w8(B.EB + B.P_CON, 8)
    call_site(u, 0x5B394)
    rec = u.rec(i)
    assert rec[10] == B.VALID and rec[B.P_TONE] == 0 and rec[B.P_CON] == 8 and u.u8(0x1B030) == 3


def test_store_v3_resets_to_v5_defaults():
    """A v0.1 RCP2 v3 store (with valid-looking records) and stale SAVE/RESET bytes: the store is rebuilt as RCP6 v6,
    every look is at its default, the two rows are 0 (no surprise save or reset), and the stale active id is cleared
    (review F3: an old id would select whatever look has that number now)."""
    old = bytearray(b"RCP2\x03\x01\x01\x1c" + bytes(8))
    for k in range(28):
        r = bytearray([1, 8, 8, 8, 8, 1, 1, 1, 1, 0, B.VALID, 0])
        x = 0x5A
        for b in r[:10]:
            x ^= b
        r[11] = x
        old += r
    i = rid("DENSE")
    tail = bytes([i]) + bytes(B.NPARAM) + bytes([1, 1])           # 0x1b8ca/cb: stale 1s
    u = unit(store=bytes(old), tail=tail)
    assert u.read(B.ST, 11) == HDR + bytes([0, 0, B.N, 0, 0, 0])  # active id cleared (F3)
    assert all(u.rec(k)[10] != B.VALID for k in range(1, B.N + 1))
    assert (u.u8(B.SAVE), u.u8(B.RST), u.u8(B.ACT)) == (0, 0, 0)
    u.on_change(B.ACT, i)
    assert list(u.read(B.EB, B.NPARAM)) == B.RECIPES[i - 1]["params"]
    u.w8(B.ACT, 200)                                              # out-of-range id -> off
    call_site(u, 0x5B394)
    assert u.u8(B.ACT) == 0
    u = unit()                                                    # trashed v5 header also rebuilds
    u.uc.mem_write(B.ST, B.MAGIC + bytes([B.STORE_VER + 1]))
    call_site(u, 0x5B394)
    assert u.read(B.ST, 5) == HDR


def test_store_v4_migrates_to_v5():
    """Koma 0.2 B&W store (RCP4 v4, 13 records): B&W edits stay on ids 1..10, CUSTOM 1..3 move to ids 18..20; SELECT
    and the color looks start at their defaults; the active id is cleared (every migration does, F3)."""
    mono = rid("MONO")
    mono_p = [5, 8, 4, 4, 4, 3, 0, 2, 4]                          # FINE 100 curve, contrast +4, RED, sharp +2
    cust_p = [3, 2, 4, 6, 4, 0, 1, 4, 0]                          # a user CUSTOM 2 (sepia 5)
    store = v4_store({mono: mono_p, 12: cust_p, 11: [9, 9, 9, 9, 9, 9, 9, 9, 9]})   # (11's params out of range)
    u = unit(store=store, act=12)                                 # CUSTOM 2 active in v4
    c2 = rid("CUSTOM 2")
    assert c2 == 19 and u.u8(B.ACT) == 0
    assert u.read(B.ST, 8) == HDR + bytes([0, 0, B.N])
    u.on_change(B.ACT, c2)
    assert list(u.rec(mono)[:9]) == mono_p and u.rec(mono)[10] == B.VALID
    assert list(u.rec(c2)[:9]) == cust_p and xor_ok(u.rec(c2))
    assert list(u.read(B.EB, B.NPARAM)) == cust_p and u.u8(0x1B030) == 6
    for k in range(11, 18):                                       # SELECT + color: empty -> defaults
        assert u.rec(k)[10] != B.VALID
    assert u.rec(rid("CUSTOM 1"))[:9] == bytes([9] * 9)         # moved as is; out of range -> read as MONO
    u.on_change(B.ACT, rid("CUSTOM 1"))
    assert list(u.read(B.EB, B.NPARAM)) == B.RECIPES[mono - 1]["params"]
    u.on_change(B.ACT, mono)
    assert list(u.read(B.EB, B.NPARAM)) == mono_p
    u = unit(store=v4_store({}), act=7)                           # a B&W id stays where it was
    assert u.u8(B.ACT) == 0 and u.read(B.ST, 5) == HDR


def test_kind_validation():
    """Record byte 9 is the kind: type (low 2 bits) <= 2 and color look < 6, else the look's default is used; a valid
    color kind on a CUSTOM slot applies that look's matrix."""
    u = unit()
    i = select(u, "MATTE")
    u.on_change(B.EB + B.P_CON, 8)
    a = B.REC + (i - 1) * B.RECLEN
    for bad in (3, 0x61, 0x81, 0x0C, 0xFF):
        rec = bytearray(u.rec(i))
        rec[9] = bad
        x = 0x5A
        for b in rec[:10]:
            x ^= b
        rec[11] = x
        u.uc.mem_write(a, bytes(rec))
        u.on_change(B.ACT, 0)
        u.on_change(B.ACT, i)
        assert list(u.read(B.EB, B.NPARAM)) == B.RECIPES[i - 1]["params"], hex(bad)


def test_matrices_against_matrix_model():
    """Against the vendored matrix model v2 (probe 3): every byte of the frozen ROM rows is sign-magnitude with bit 6
    clear; the words decode through the model; encode(registers_to_delta(words)) gives the words back; TINT moves
    mid-grey chroma toward green (-Cb, -Cr) for + steps and magenta for - steps, monotonically, luma untouched. (The
    words' derivation from the tier-B fits is re-checked in test_provenance, research checkout only.)"""
    import numpy as np
    MM = B.matrix_model()
    for look, r in enumerate(B.CLOOKS):
        words = B.LOOK_MATRIX[look]
        regs = dict(zip(MM.ROWS, words))
        D, _ = MM.registers_to_delta(regs)
        again = MM.encode(D)
        assert all(again[a] == regs[a] for a in MM.ROWS), r["name"]
        for t in range(-3, 4):
            w = B.tint_word(words[1], t)
            assert all(((w >> (8 * k)) & 0x40) == 0 for k in range(4))
            dec, base = MM.decode_word(w), MM.decode_word(words[1])
            assert sum(dec[k] - base[k] for k in range(4)) == t or any(abs(base[k]) == 63 for k in B.TINT_TAPS)
        grey = np.array([[128.0, 0.0, 0.0]])
        cs = [MM.predict_ycc(grey, {MM.REG_MATR: words[0], MM.REG_MATG: B.tint_word(words[1], t),
                                    MM.REG_MATB: words[2]})[0] for t in range(-3, 4)]
        assert all(c[0] == 128 for c in cs)
        assert all(a[1] > b[1] and a[2] > b[2] for a, b in zip(cs, cs[1:])), (r["name"], cs)
    assert B._sm(0xBF) == -63 and B._smb(-63) == 0xBF and B._smb(70) == 0x3F and B._sm(0x7F) == 63


def test_bases_lossless_and_budget():
    assert [b[0] for b in B.BASES] == ["R3", "STANDARD", "HARD", "HI-CONT", "SOFT MONO", "FILM 400", "FINE 100",
                                       "BLEACH"]
    for i in range(B.NB):
        assert B.decode(B.encode(B.base_table(i))) == list(B.base_table(i))
    assert len(B.bases_blob()) == 1024 * B.NB <= 8 * 1024
    f = link([B.make({})]).manifest["features"][0]
    # Resident size budget: STANDARD, ADJ LEVER list / mask, imgright, the shared editor-title list (`rnames`, which
    # menu5 does not copy), ghost NORMAL (stdclean) and the host-mask shadow (amsh) fit in 14,680 B.
    assert f["resident_bytes"] <= 14700, f["resident_bytes"]


def test_sharpness():
    u = unit()
    select(u, "HICON B&W")
    assert call_site(u, B.SITES_SHARP[0]) == 5                    # +2 -> stock 5
    select(u, "SOFT MONO")
    assert call_site(u, B.SITES_SHARP[1]) == 2                    # -1 -> stock 2 (EXIF site)
    select(u, "BLEACH")
    assert call_site(u, B.SITES_SHARP[1]) == 4                    # color: +1 -> 4
    u.on_change(B.ACT, 0)
    assert call_site(u, B.SITES_SHARP[1]) == 3                    # stock value passes through


def test_toning_gate_when_imgset_leaves_base():
    u = unit()
    select(u, "HARD MONO")
    assert u.u8(0x1B030) == 6
    u.w8(0x1B030, 3)                                              # base left: recipe does not apply
    shot(u, mono=1)
    assert not u.te and not u.cc2


def test_adj_step_reads_the_type_word():
    """adj-hang triage: 0x1bf090 is a big-endian word."""
    u = unit()
    i = rid("HICON B&W")
    lst, _ = adj_open(u)
    k = [n for n, _ in lst].index("HICON B&W") + 1
    u.w8(B.ADJD + 9, k)
    call_site(u, B.SITES_ADJ_STEP[4])
    assert (u.u8(0x1B030), u.u8(B.ACT), u.u8(B.ADJD + 9)) == (3, i, k)
    v = unit(adj_type=3)                                          # another ADJ item (ISO) steps: plain, id untouched
    v.w8(B.ACT, i)                                                # (after install: a fresh store clears the id, F3)
    v.w8(B.ADJD + 9, 2)
    call_site(v, B.SITES_ADJ_STEP[4])
    assert v.u8(B.ACT) == i and v.u8(B.ADJD + 9) == 2


def test_adj_hooks_return_in_every_state():
    """Every ADJ hook returns (bounded instruction count) for: IMG SET or another item, recipe on/off, stale ids, cursors
    0..0xff, invalid store. Callee-saved r4-r7 and sp are preserved."""
    from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_SP
    from koma.unitemu import STACK
    sites = [B.SITE_ADJ_OPEN[0]] + B.SITES_ADJ_STEP + B.SITES_ADJ_COMMIT
    for adj_type in (B.ADJ_IMGSET, 3):
        for act in (0, rid("HARD MONO"), rid("POSITIVE"), B.N, B.STD, B.STD + 1, 0xFF):
            u = unit(adj_type=adj_type)
            for cur in (0, 1, 7, 8, B.NDISP, B.NDISP + 1, 0xFF):
                for site in sites:
                    u.w8(B.ACT, act)
                    u.w8(B.ADJD + 9, cur)
                    sent = {UC_ARM_REG_R4: 0x44, UC_ARM_REG_R5: 0x55, UC_ARM_REG_R6: 0x66, UC_ARM_REG_R7: CP}
                    u.calls.clear()
                    u.thumb(site, sent, stop=site + 4, count=200_000)
                    assert u.uc.reg_read(UC_ARM_REG_PC) == site + 4, (hex(site), adj_type, act, cur)
                    assert all(u.uc.reg_read(r) == v for r, v in sent.items()), hex(site)
                    assert u.uc.reg_read(UC_ARM_REG_SP) == STACK
                    assert u.u8(0x1B030) <= 6                             # only stock values in IMG SET
    u = unit()
    u.uc.mem_write(B.ST, b"\xff" * 16)                            # trashed store header: sync rebuilds, still returns
    u.w8(B.ADJD + 9, 2)
    call_site(u, B.SITES_ADJ_STEP[0])
    assert u.read(B.ST, 5) == HDR


def test_adj_option_off_drops_every_adj_record():
    """bwrecipes.adj=off (bisect build): no ADJ hook, no ADJ item pointer/count, no hint pointer; menu rows stay."""
    f = B.make({"adj": "off"})
    adj_sites = {B.SITE_ADJ_OPEN[0], *B.SITES_ADJ_STEP, *B.SITES_ADJ_COMMIT}
    assert not adj_sites & {h.site for h in f.hooks}
    assert not {B.ADJ_ITEM, B.ADJ_HINT_LIT} & {p.addr for p in f.pointers}
    assert B.ADJ_ITEM + 4 not in {p.addr for p in f.patches}
    u = unit(opts={"adj": "off"})
    assert u.u8(B.ADJ_ITEM + 4) == 7 and u.u8(B.IMG_ROWS[0] + 8) == B.NDISP


def test_menu5_editor_rows_follow_the_type():
    """menu5 + bwrecipes: after ksync_load the editor rows p5..p7 show the active type's label, options and count
    (display entry and ktab), from the published selector 0x1b908; the cursor shows the row byte."""
    from koma import features as F
    from koma.features import menu5 as M5
    from koma.linker import collect_rows
    from koma.unitemu import RES_POOL as RP
    fs = [F.make("menu5"), F.make("bwrecipes")]
    r = link(fs)
    rows, _, _ = collect_rows(fs)
    idx = {x["id"]: i for i, x in enumerate(rows)}
    u = RU(r.module)
    u.uc.mem_map(0x08010000, 0x10000)
    assert u.install() == 0
    sym = lambda s: RP + r.symbols[s] - r.symbols["koma_res"]     # noqa: E731
    for t, name in ((0, "MONO"), (1, "WARM NEG"), (2, "SELECT")):
        u.uc.mem_write(B.ACT, bytes([rid(name)]))
        u.thumb(sym("kf_bwrecipes__on_change"), {UC_ARM_REG_R0: rid(name), UC_ARM_REG_R1: B.ACT})
        assert u.u8(B.SELB) == t
        u.thumb(sym("kf_menu5__ksync_load"), {})
        for rid_, row in (("rp_p5", B.ROWS[6]), ("rp_p6", B.ROWS[7]), ("rp_p7", B.ROWS[8])):
            i = idx[rid_]
            e, kt = sym(f"kf_menu5__kr_e_{i}"), sym(f"kf_menu5__kt_{i}")
            lab, opts = row["variants"]["alts"][t]
            assert glyph_text(u, struct.unpack(">I", u.read(e, 4))[0]) == lab, (name, rid_)
            optab = struct.unpack(">I", u.read(e + 4, 4))[0]
            assert [glyph_text(u, struct.unpack(">I", u.read(optab + 4 * k, 4))[0]) for k in range(len(opts))] == opts
            assert u.u8(e + 8) == len(opts) == u.u8(kt + 12)
            assert u.u8(e + 9) == u.u8(row["byte"]) + 1           # cursor = the row byte (in this type's range)
    assert M5.row_variants(rows[idx["rp_p5"]])["sel"] == B.SELB


def _rms_max(a, b, lo=258):
    e = [(x - y) / 4 for x, y in zip(a[lo:], b[lo:])]                # 8-bit levels past Ricoh's pinned toe
    return (sum(x * x for x in e) / len(e)) ** 0.5, max(abs(x) for x in e)


def test_grd4_refit_curves_match_the_fitted_tables():
    """GRD IV refit (v4.1): each refit look's generated curve against its target table (GRD IV curve relative to A5,
    on STANDARD, from the vendored data/grd4 rows). POSITIVE = C5 fitted by STANDARD + con/key/shd/hil (2.1 levels
    rms); BLEACH = its own base (B5), equal to the target up to the 2-bit coding. Method check: A6 (GRD IV contrast +1)
    = STANDARD CONTRAST +1. B5 is a lift (mid 120 -> 172 over A5, monotone, deeper toe), not an inverted curve."""
    a5, b5, c5 = B.grd4_curve33("A", 5), B.grd4_curve33("B", 5), B.grd4_curve33("C", 5)
    assert a5[16] == 120 and b5[16] == 172 and c5[16] == 134                      # x = 128
    assert all(x <= y for t in (a5, b5, c5) for x, y in zip(t, t[1:])) and b5[8] < a5[8] and b5[24] > a5[24]
    std = B.base_table(B.B["STANDARD"])
    pos = B.RECIPES[rid("POSITIVE") - 1]["params"]
    rms, mx = _rms_max(B.curve(pos), B.grd4_target("C", 5))
    assert rms < 2.2 and mx < 8.5, (rms, mx)
    assert _rms_max(B.curve(pos), std)[0] > 5                                      # and it is a real change
    ble = B.RECIPES[rid("BLEACH") - 1]["params"]
    tgt = B.grd4_target("B", 5)
    rms, mx = _rms_max(B.curve(ble), tgt)
    assert ble[B.P_CURVE] == B.B["BLEACH"] and ble[1:5] == [4, 4, 4, 4] and rms < 0.05 and mx <= 0.25, (rms, mx)
    assert list(B.base_table(B.B["BLEACH"]))[:258] == list(std)[:258]              # Ricoh's toe kept
    assert B.decode(B.encode(B.base_table(B.B["BLEACH"]))) == list(B.base_table(B.B["BLEACH"]))
    assert _rms_max(B.curve([B.B["STANDARD"], 5, 4, 4, 4]), B.grd4_target("A", 6))[0] < 0.7
    sha = hashlib.sha256(struct.pack(">4096H", *B.base_table(B.B["BLEACH"]))).hexdigest()
    assert sha.startswith(BLEACH_SHA), sha


BLEACH_SHA = "44acb3be"


def test_grd4_color_defaults_and_matrix():
    """POSITIVE's matrix = grd4_matrix(group C) with its SATURATION (re-derived from data/grd4 through model v2) and
    beats SATURATION alone; encode/decode round trip; grey stays near neutral. SATURATION defaults = the step nearest
    the GRD IV mean sector gain (C 0.82 -> x0.85; B 0.43 -> floor x0.55); BLEACH: no matrix, WARMTH COOL 3 (the
    Normal WB triple R x0.817 G x1.083 B x1.083 is a teal shift past COOL 3); every default in its row's range."""
    import numpy as np
    MM = B.matrix_model()
    near = lambda g: min(range(7), key=lambda i: abs(B.SAT_STEPS[i] - g))      # noqa: E731
    pos, ble = (B.RECIPES[rid(n) - 1]["params"] for n in ("POSITIVE", "BLEACH"))
    gc, gb = B.grd4_mean_gain(2), B.grd4_mean_gain(1)
    assert 0.80 < gc < 0.83 and 0.42 < gb < 0.44
    assert pos[B.P_SAT] == near(gc) == 2 and ble[B.P_SAT] == near(gb) == 0
    words, rep = B.grd4_matrix(2, pos[B.P_SAT])
    assert words == B.LOOK_MATRIX[0], [hex(w) for w in words]
    assert rep["rms_residual"] < rep["rms_gain_only"] < rep["rms_change"] and rep["grey_cast"] < 2.0, rep
    regs = dict(zip(MM.ROWS, words))
    again = MM.encode(MM.registers_to_delta(regs)[0])
    assert all(again[a] == regs[a] for a in MM.ROWS)
    assert all(all(((w >> (8 * k)) & 0x40) == 0 for k in range(4)) for w in words)
    assert B.LOOK_MATRIX[2] == (0, 0, 0)
    assert ble[B.P_WARM] == 0 and ble[B.P_TINT] == 3                              # COOL 3, TINT 0
    grey = np.array([[128.0, 0.0, 0.0]])
    assert np.allclose(MM.predict_ycc(grey, {}, chroma_gain=B.SAT_STEPS[ble[B.P_SAT]]), grey)
    for name in ("POSITIVE", "BLEACH"):
        r = B.RECIPES[rid(name) - 1]
        assert all(0 <= x < lim for x, lim in zip(r["params"], B.LIMITS[B.TYPE_COLOR])), name
        assert r["src"].startswith("grd4:"), name


# ------------------------------------------------------------------ Koma 0.3: RCP6, GRAIN / VIGNETTE (FX table)
def v5_store(records):
    """An RCP5 v5 store (Koma 0.2: 20 records of 12 B, the same layout as v6) with {id: (params, kind)} valid."""
    st = bytearray(b"RCP5\x05\xff\xff" + bytes([B.N]) + bytes(8))
    for k in range(1, B.N + 1):
        r = bytearray(12)
        if k in records:
            p, kind = records[k]
            r[:9], r[9], r[10] = bytes(p), kind, B.VALID
            x = 0x5A
            for b in r[:10]:
                x ^= b
            r[11] = x
        st += r
    return bytes(st)


def fxt(u, i):
    return u.u8(B.REC + B.N * B.RECLEN + i - 1)


def test_store_v5_migrates_to_v6():
    """Koma 0.2 store (RCP5 v5): all 20 records stay byte for byte (CUSTOM 1..3 included); the FX table starts OFF (stale
    bytes there are zeroed); the active id and SAVE / RESET are cleared; stale GRAIN / VIGNETTE rows (0x1b8cc/cd)
    are replaced from the FX table when a recipe is selected; the header is written last as RCP6 v6."""
    mono, c3 = rid("MONO"), rid("CUSTOM 3")
    store = v5_store({mono: ([5, 8, 4, 4, 4, 3, 0, 2, 4], 0), c3: ([1, 6, 6, 1, 1, 2, 3, 3, 2], kind_of("SLIDE 50"))})
    tail = bytes([c3]) + bytes(B.NPARAM) + bytes([1, 1, 3, 3])       # SAVE, RESET, GRAIN, VIGNETTE stale
    edits_fx = b"\x5f" * B.N                                         # stale "valid" FX bytes from another life
    lr = link([B.make({})])
    u = RU(lr.module, ram_edits={0x1A91BD: b"\x05", 0x1A91E3: b"\x05", 0x1A91EF: b"\x05", 0x1BF01D: b"\x00",
                                 B.ADJ_TYPE: struct.pack(">I", B.ADJ_IMGSET), CP + 0x22: b"\x01",
                                 CP + 0x268: struct.pack(">3H", 185, 64, 172)})
    u.link = lr
    u.uc.mem_map(0x08010000, 0x10000)
    u.uc.mem_write(B.ACT, tail)
    u.uc.mem_write(B.ST, store)
    u.uc.mem_write(B.REC + B.N * B.RECLEN, edits_fx)
    u.fakes.update({B.COMMIT_AUTO: _commit_fake, B.LOAD_AUTO: _load_fake})
    assert u.install() == 0
    assert u.read(B.ST, 8) == HDR + bytes([0, 0, B.N])
    assert u.read(B.REC, B.N * 12) == store[16:]                       # every record unchanged
    assert all(fxt(u, k) == 0 for k in range(1, B.N + 1))
    assert (u.u8(B.ACT), u.u8(B.SAVE), u.u8(B.RST)) == (0, 0, 0)       # GRAIN / VIGNETTE rows reload on selection
    u.on_change(B.ACT, c3)                                             # the user's CUSTOM 3 is still a SLIDE 50 copy
    assert list(u.read(B.EB, B.NPARAM)) == [1, 6, 6, 1, 1, 2, 3, 3, 2] and u.u8(0x1B030) == 1
    assert (u.u8(B.FXG), u.u8(B.FXV)) == (0, 0)


def test_store_rcp6_needs_exactly_n():
    """0.3 keeps RCP6 only with exactly N records (its FX table sits right after record N); 0.2.2's 'N or more' rule is
    a 0.2.2-only forward-compat read. A longer or shorter RCP6 is rebuilt as RCP6 with N records."""
    for n in (B.N + 2, B.N - 1):
        st = bytearray(HDR + bytes([0xFF, 0xFF, n]) + bytes(8)) + bytes(12 * n)
        u = unit(store=bytes(st))
        assert u.read(B.ST, 8) == HDR + bytes([0, 0, B.N]), n
    u = unit(store=v5_store({}))
    assert u.read(B.ST, 8) == HDR + bytes([0, 0, B.N])


def test_grain_vignette_commit_save_reset_publish():
    """GRAIN (0x1b8cc) / VIGNETTE (0x1b8cd) on a B&W, a color and the SELECT look: the recipe's FX byte holds them
    (0x50 | g | v << 2), the record is untouched, sync publishes them at store +11 / +12 only while the recipe applies;
    each look keeps its own; SAVE TO CUSTOM copies them; RESET puts them back to OFF; out of range is repaired."""
    u = unit()
    want = {}
    for k, name in enumerate(("FILM 400", "NEGATIVE", "SELECT")):
        i = select(u, name)
        rec = u.rec(i)
        assert u.read(B.FXGRAIN, 2) == b"\0\0" and (u.u8(B.FXG), u.u8(B.FXV)) == (0, 0), name
        g, v = 1 + k, 3 - k
        u.on_change(B.FXG, g)
        u.on_change(B.FXV, v)
        assert fxt(u, i) == 0x50 | g | v << 2 and u.rec(i) == rec, name
        assert (u.u8(B.FXGRAIN), u.u8(B.FXVIG)) == (g, v), name
        for byte, bad in ((B.FXG, 4), (B.FXV, 0xFF)):
            u.on_change(byte, bad)
            assert (u.u8(B.FXG), u.u8(B.FXV)) == (g, v) and fxt(u, i) == 0x50 | g | v << 2, name
        u.w8(0x1B030, 0 if kind_of(name) & 3 else 1)                  # base left: the recipe does not apply
        call_site(u, 0x96430 + 6, r0=0)
        assert u.read(B.FXGRAIN, 2) == b"\0\0", name
        want[i] = (g, v)
    for i, (g, v) in want.items():                                    # each look kept its own
        u.on_change(B.ACT, i)
        assert (u.u8(B.FXG), u.u8(B.FXV), u.u8(B.FXGRAIN), u.u8(B.FXVIG)) == (g, v, g, v)
    u.on_change(B.SAVE, 1)                                            # active = SELECT (3, 1)
    c1 = rid("CUSTOM 1")
    assert fxt(u, c1) == 0x50 | 3 | 1 << 2 and u.rec(c1)[9] == B.TYPE_SEL
    u.on_change(B.ACT, c1)
    assert (u.u8(B.FXG), u.u8(B.FXV), u.u8(B.SELCOL)) == (3, 1, 1)
    u.on_change(B.RST, 1)                                             # RESET: FX OFF too
    assert (u.u8(B.FXG), u.u8(B.FXV), u.u8(B.FXGRAIN), fxt(u, c1)) == (0, 0, 0, 0)
    u.on_change(B.ACT, 0)
    assert u.read(B.FXGRAIN, 2) == b"\0\0"


def _unity_fake(u):                      # 0x174ca4 -> 0x9b7e0: cp+0x268.. = 100, CC2 WB unity (warmth/NOTES.md)
    u.uc.mem_write(CP + 0x268, struct.pack(">3H", 100, 100, 100))
    u.mmio += [("12018110", "00400040"), ("12018114", "00000040")]


def test_unity_wb_wrap_reapplies_warmth_and_red_tilt():
    """Continuous / AE-BKT frames 2.. (cp+0x40 != 3) call 0x9b7e0 after the develop hook (0x964b8 pass 1, 0xafddc
    pass 2): unity WB. The wrap re-applies the pass's recipe WB multipliers on top of unity (R = B = 100, G = 64)."""
    g9 = lambda v, m: min(0x1FF, (v * m) >> 16)     # noqa: E731
    for name, site, pass_ in (("RETRO", 0x964B8, 1), ("WARM NEG", 0xAFDDC, 2), ("SHINJUKU", 0x964B8, 1),
                              ("POSITIVE", 0x964B8, 1), (None, 0xAFDDC, 2)):
        u = unit(imgset=1)
        u.fakes[B.WB_UNITY] = _unity_fake
        if name:
            select(u, name)
        shot(u, mono=0 if (name is None or kind_of(name) & 3) else 1, pass_=pass_)
        wl = u.sym("wlog")
        n0 = u.u8(wl + B.WL_UNITY)
        call_site(u, site, r0=CP)
        last = {a: v for a, v in u.mmio}
        p = B.RECIPES[rid(name) - 1]["params"] if name else None
        if name and kind_of(name) & 3:
            want = B.wb_words((100, 64, 100), p[B.P_WARM])
        elif name and p[B.P_FILT] == B.F["RED"]:
            _, mr, mg, mb = B.FTAB[B.F["RED"] - 1]
            want = (g9(100, mr) << 16 | g9(64, mg), g9(100, mb))
        else:
            want = None
        if want is None:                     # WARMTH 0 / stock shot: stock unity stands, nothing re-applied
            assert last == {"12018110": "00400040", "12018114": "00000040"}, (name, last)
            assert u.u8(wl + B.WL_UNITY) == n0, name
        else:
            assert last == {"12018110": "%08x" % want[0], "12018114": "%08x" % want[1]}, (name, last, want)
            assert u.u8(wl + B.WL_UNITY) == (n0 + 1) & 0xFF, name
        assert u.u8(CP + 0x269) == 100, name      # cp+0x268.. left as stock set them


def test_estimate_looks_have_published_sources():
    """v4.2: every color look without a GRD IV firmware source cites published sources (bwrecipes.md, Koma 0.3)."""
    for r in B.CLOOKS:
        if not r["src"].startswith("grd4:"):
            assert B.REFS.get(r["name"]) and all(u.startswith("https://") for u in B.REFS[r["name"]]), r["name"]


def test_grd4_extras_recorded():
    d = B._grd4()
    cp = d["cross_process"]["sub_options"]
    assert [cp[k]["cs209"] for k in ("basic", "magenta", "yellow")] == [1, 2, 3]
    assert [cp[k]["rlut_index"] for k in ("basic", "magenta", "yellow")] == [2, 3, 4]
    for k in cp:
        assert len(cp[k]["knots_x"]) == 33 and all(len(y) == 33 for y in cp[k]["y"])
        assert all(a <= b for y in cp[k]["y"] for a, b in zip(y, y[1:]))          # monotone channels
    assert cp["yellow"]["white"][2] < cp["basic"]["white"][2] < cp["magenta"]["white"][2] < 1
    assert d["vividness"]["chroma_gain_per_level"] is None and "Retro" in d["retro"]["_note"]


def test_fx_rows_order_and_absent_without_fx():
    """EDIT RECIPE lists GRAIN / VIGNETTE after SHARPNESS and before SAVE TO CUSTOM; without fx in the build they are
    not shown (the store layout and the FX table stay as they are)."""
    from koma import features as F
    from koma.linker import collect_rows
    ids = lambda names: [r["id"] for r in collect_rows([F.make(n) for n in names])[0] if r["group"] == "recipe-edit"]  # noqa: E731
    with_fx = ids(F.KOMA03)
    assert with_fx[-6:] == ["rp_sharp", "rp_grain", "rp_vig", "rp_adjlever", "rp_save", "rp_reset"]
    without = ids([n for n in F.KOMA03 if n != "fx"])
    assert "rp_grain" not in without and "rp_vig" not in without and without[-4:] == [
        "rp_sharp", "rp_adjlever", "rp_save", "rp_reset"]
