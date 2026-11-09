"""Koma modes: label tables filled at init, MENU LAYOUT directory, ESSENTIALS gate."""
from __future__ import annotations

from _util import LOG, SITE, Feature, Patch, fw

from koma import features as F
from koma.linker import link
from koma.unitemu import RES_POOL, Unit

LAYOUT, FEAT = 0x1B8BB, 0x1B8BC


def res(r, sym):
    return RES_POOL + r.symbols[sym] - r.symbols["koma_res"]


def word(u, a):
    return int.from_bytes(u.read(a, 4), "big")


def boot(r, layout=0, feat=0, row0=0):
    u = Unit(r.module)
    u.uc.mem_write(0x1B8B0, b"KOMA\x01" + bytes([row0]) + bytes(5) + bytes([layout, feat]))
    assert u.install() == 0 and u.callee_saved_ok
    return u


def test_label_tables_filled_at_init_without_relocations():
    r = link([F.make("menu5"), F.make("settingsio-menu")])
    u = boot(r)
    a, end, n = res(r, "kf_menu5__lt_start"), res(r, "kf_menu5__lt_end"), 0
    while a < end:
        s = a + 36
        assert [word(u, a + 4 * k) for k in range(9)] == [s] * 9, hex(a)
        z = s
        while u.read(z, 2) != b"\0\0":
            z += 2
        a = (z + 2 + 3) & ~3
        n += 1
    assert a == end and n > 20
    assert word(u, res(r, "kf_menu5__lab_tab")) == res(r, "kf_menu5__lab_tab_s")   # footer CHANGE TAB (PTR record)


def test_menu_layout_picks_the_directory():
    r = link([F.make("menu5"), F.make("settingsio-menu")])
    v = res(r, "kf_menu5__vars")
    five = (res(r, "kf_menu5__metatab"), res(r, "kf_menu5__stripdata"), 5, 0)
    sk = (res(r, "kf_menu5__metatab_sk"), res(r, "kf_menu5__stripdata_sk"), 3, 1)
    g2 = (res(r, "kf_menu5__metatab_g2"), res(r, "kf_menu5__stripdata_g2"), 3, 1)
    for layout, want in ((0, five), (1, sk), (2, g2), (7, five)):        # 7 is out of range: clamped -> 0
        u = boot(r, layout=layout)
        assert (word(u, v + 4), word(u, v + 8), u.u8(v + 12), u.u8(v + 13)) == want, layout
        assert u.u8(v + 14) == (layout == 2), layout                         # GR II flag only for layout 2
    u = boot(r, layout=1)
    sk_meta = res(r, "kf_menu5__metatab_sk")
    assert (word(u, sk_meta + 24), word(u, sk_meta + 32)) == (0x1C224C, 30)          # stock SETUP table in place
    assert word(u, sk_meta + 12) == res(r, "kf_menu5__tbl1")                          # KOMA tab
    u.uc.mem_write(0x1C23B8, b"\x01")                                                   # movie page
    assert u.thumb(res(r, "kf_menu5__mode_slot"), {}) == 0                              # mode page is always slot 0
    u.thumb(res(r, "kf_menu5__kstripfix"), {})
    assert word(u, res(r, "kf_menu5__stripdata_sk") + 12) == res(r, "kf_menu5__strip_KOMA")
    # header LEFT/RIGHT cycle the 3 tabs; reaching slot 0 (the mode page) takes the stock exit (key -> LEFT)
    from unicorn.arm_const import UC_ARM_REG_R0
    u.stub(0xD3A68, "redraw")
    u.stub(0xE3698, "stock_classify", r0=9)
    u.uc.mem_write(0x1C23B9, b"\x02")
    u.uc.mem_write(0x1C23F0, b"\x00")
    for slot, key, want, leave in ((1, 0x20, 2, False), (2, 0x20, 0, True), (1, 0x10, 0, True), (2, 0x10, 1, False)):
        u.uc.mem_write(v, bytes([slot]))
        u.calls.clear()
        u.thumb(res(r, "kf_menu5__classify"), {UC_ARM_REG_R0: key})
        assert u.u8(v) == want and (("stock_classify", 0x10) in u.calls) == leave, (slot, key)
        if not leave:
            assert word(u, 0x1C23E0) == word(u, sk_meta + 12 * want) and u.u8(0x1C23EC) == word(u, sk_meta + 12 * want + 8)
    u = boot(r, layout=0)
    u.uc.mem_write(0x1C23B8, b"\x01")
    assert u.thumb(res(r, "kf_menu5__mode_slot"), {}) == 1
    # without the MENU LAYOUT row in the build the byte is ignored (no STOCK+KOMA tables at all)
    r0 = link([F.make("menu5")])
    assert "kf_menu5__metatab_sk" not in r0.symbols
    u = boot(r0, layout=1)
    assert word(u, res(r0, "kf_menu5__vars") + 4) == res(r0, "kf_menu5__metatab")


XSRC = f"""
xinit:
    push {{r4, lr}}
    ldr r2, =0x{LOG:x}
    movs r1, #0x5a
    strb r1, [r2, #0]
    pop {{r4}}
    pop {{r3}}
    bx r3
on_change:
    bx lr
.ltorg
"""


def _gated_build():
    x = Feature("xfeat", "t", "non-essential", source=XSRC, init="xinit", exports=["on_change"],
                patches=[Patch(SITE, fw.ram(SITE, 4), b"\0\0\0\0")],
                rows=[dict(id="xrow", label="X ROW", options=["A", "B"], byte=0x1B8B5, default=0)])
    return link([F.make("menu5"), F.make("settingsio-menu"), x])


def test_essentials_skips_gated_inits_and_records():
    r = _gated_build()
    e = r.manifest["essentials"]
    assert (e["gate"], e["gated"]) == (True, ["xfeat"])
    assert e["resident_bytes_essentials"] < e["resident_bytes_full"]
    kt = res(r, "kf_menu5__kt_0")                                   # xrow's ktab entry (+4 on_change)
    u = boot(r, feat=1)
    assert u.u8(LOG) == 0                                            # xfeat init skipped
    assert u.read(SITE, 4) == fw.ram(SITE, 4)                        # its patch not applied
    assert u.read(0xE34B4, 4) != fw.ram(0xE34B4, 4)                  # menu5 (essential) records applied
    assert word(u, kt + 4) == 0 and u.u8(res(r, "koma_ess")) == 1    # row shows but its on_change is cleared
    assert word(u, RES_POOL + 12) == 1                               # manifest status: installed
    allocs = [c for c in u.calls if c[0] == "alloc"]
    assert allocs[0][2] == e["resident_bytes_essentials"]               # the gated tail is never allocated
    xf = next(f for f in r.manifest["features"] if f["name"] == "xfeat")
    assert int(xf["resident_offset"], 16) >= e["resident_bytes_essentials"]  # gated code sits after the prefix


def test_full_installs_everything():
    r = _gated_build()
    kt = res(r, "kf_menu5__kt_0")
    for feat in (0, 9):                                              # FULL, and out of range (clamped to FULL)
        u = boot(r, feat=feat)
        assert u.u8(LOG) == 0x5A and u.read(SITE, 4) == b"\0\0\0\0", feat
        assert word(u, kt + 4) == res(r, "kf_xfeat__on_change") + 1 and u.u8(FEAT) == 0


# ------------------------------------------------------------------ MENU LAYOUT = 2 (GR II)
from koma.features import menu5 as M5  # noqa: E402
from koma.vendor.menu5tab.glyphs import P1  # noqa: E402

_GLYPH = {v: k for k, v in P1.items()}
SETUP_T = 0x1C224C


def text(u, a):
    out = []
    while u.read(a, 2) != b"\0\0":
        out.append(_GLYPH[u.u8(a + 1)])
        a += 2
    return "".join(out)


def _g2_build(extra=()):
    return link([F.make("menu5"), F.make("bwrecipes"), F.make("settingsio-menu"), *extra])


def _slot(u, r, slot):
    meta = word(u, res(r, "kf_menu5__vars") + 4)
    return word(u, meta + 12 * slot), word(u, meta + 12 * slot + 4), word(u, meta + 12 * slot + 8)


def test_gr2_three_tabs_and_labels():
    r = _g2_build()
    u = boot(r, layout=2)
    v = res(r, "kf_menu5__vars")
    assert u.u8(v + 12) == 3 and u.u8(v + 13) == 1                       # 3 tabs, mode page always slot 0
    sd = res(r, "kf_menu5__stripdata_g2")
    assert word(u, sd + 4) == 0                                          # slot 0: the page label (mode page)
    assert [text(u, word(u, sd + 8 * k + 4)) for k in (1, 2)] == ["KEY CUST.", "SETUP"]
    assert all(len(n) <= 9 for n in M5.G2_STRIP)
    lab = res(r, "kf_menu5__lab_still")                                   # still pages' tab label -> SHOOTING
    assert {word(u, lab + 4 * k) for k in range(9)} == {res(r, "kf_menu5__g2_shoot_s")}
    assert text(u, res(r, "kf_menu5__g2_shoot_s")) == "SHOOTING"
    u.uc.mem_write(0x1C23B8, b"\x00")
    u.thumb(res(r, "kf_menu5__kstripfix"), {})                            # no KOMA / MOVIE swap on the GR II strip
    assert word(u, sd + 12) == res(r, "kf_menu5__strip_KEYCUST")
    u = boot(r, layout=0)                                                 # 5-TAB keeps STILL
    assert text(u, word(u, res(r, "kf_menu5__lab_still"))) == "STILL"


def test_gr2_setup_order_and_hidden_rows():
    x = Feature("xtail", "t", "a settings-tail row", source="xs:\n    bx lr\n",
                rows=[dict(id="xtail", label="X TAIL", options=["A", "B"], byte=0x1B8B5, default=0, group="settings")])
    r = link([F.make("menu5"), F.make("settingsio-menu"), x])
    u = boot(r, layout=2)
    tbl, mp, n = _slot(u, r, 2)
    maps = list(u.read(mp, n))
    assert maps[:3] == [15, 1, 2]                                         # USB CONNECTION, FORMAT CARD, FORMAT INT.
    for k, src in enumerate(maps[:3]):                                    # the stock label and options words
        assert u.read(tbl + 12 * k, 8) == fw.ram(SETUP_T + 12 * (src - 1), 8)
    key_tbl, key_map, kn = _slot(u, r, 1)
    shown = sorted([x for x in maps + list(u.read(key_map, kn)) if x] + list(M5.G2_SHOOT_SETUP))
    assert shown == list(range(1, 31))                     # every stock SETUP row exactly once (3 of them on SHOOTING)
    tail = [text(u, word(u, word(u, tbl + 12 * k))) for k in range(n) if maps[k] == 0]
    assert tail == ["MENU LAYOUT", "KOMA FEATURES", "SAVE TO CARD", "KOMA VERSION"]
    assert "kf_menu5__kr_e_0" in r.symbols and "kf_menu5__kg_e_0" not in r.symbols   # X TAIL: 5-TAB only
    # the Koma rows' ktab display entries follow the installed layout
    kt = res(r, "kf_menu5__kt_1")                                         # MENU LAYOUT
    assert word(u, kt + 8) == res(r, "kf_menu5__kg_e_1")
    assert word(boot(r, layout=0), kt + 8) == res(r, "kf_menu5__kr_e_1")


def test_gr2_key_tab_holds_the_nine_key_rows():
    r = _g2_build()
    u = boot(r, layout=2)
    tbl, mp, n = _slot(u, r, 1)
    assert list(u.read(mp, n)) == M5.G2_KEY and n == 9
    assert [u.read(tbl + 12 * k, 8) for k in range(n)] == [fw.ram(SETUP_T + 12 * (s - 1), 8) for s in M5.G2_KEY]


def _stock_maps(u):
    for _, _, rmap in M5.STILL_TABLES:
        for o, val in rmap.items():
            u.uc.mem_write(M5.ROWMAP + o, bytes([val]))


# ------------------------------------------------------------------ GR II SHOOTING page: Koma + SETUP-app rows
from unicorn.arm_const import UC_ARM_REG_R0 as _R0  # noqa: E402

SHOOT_AUTO = ["FOCUS", "SNAP DIST", "FULL SNAP", "PHOTOMETRY", "PIC QUALITY/SIZE", 28, 19, "RECIPE", "EDIT RECIPE",
              "IMG SET", "DATE IMPRINT", "CONT. MODE", "AUTO BRACKET", 29, "INTERVAL", "IMAGE WITH SOUND",
              "WHITE BALANCE", "ISO SETTING", "EXPOSURE COMP.", "LONG EXP LIMIT", "FLASH EXPO. COMP.",
              "RESTORE DEFAULTS"]
SHOOT = {0: SHOOT_AUTO, 1: [x for x in SHOOT_AUTO if x != "RESTORE DEFAULTS"],
         2: [x for x in SHOOT_AUTO if x not in ("RESTORE DEFAULTS", "EXPOSURE COMP.")]}
SNAP, FULL, LONGEXP, ACT, MDIAL = 0x1B8B5, 0x1B8B6, 0x1B8BA, 0x1B8C0, 0x1BF01D
DZ, RAWJ, CLBKT = 0x1B0A4, 0x1B193, 0x1B194
LOAD_SITE = {0: 0x5B394, 1: 0x5B3D4, 2: 0x5B3D4}
STILL_LABELS = {int.from_bytes(fw.ram(0x1C1EE8 + 12 * k, 4), "big"): n for k, n in enumerate(M5.STOCK_STILL)}
SETUP_LABELS = {int.from_bytes(fw.ram(SETUP_T + 12 * (n - 1), 4), "big"): n for n in M5.G2_SHOOT_SETUP}


def _shoot_build():
    return link([F.make(x) for x in ("menu5", "snapdist", "fullpress", "longexp", "bwrecipes", "settingsio-menu")])


def _shoot(layout=2):
    r = _shoot_build()
    u = boot(r, layout=layout)
    u.stub(0x29E14, "rowmap_init", r0=0)
    _stock_maps(u)
    assert u.thumb(res(r, "kf_menu5__krowmap"), {}) == 0
    for a, name in ((0x5AAF0, "load_auto"), (0x5AEB4, "load_pam"), (0x5B4E4, "commit_auto"), (0x5A260, "commit_pam"),
                    (0x79118, "stock_right"), (0xD3A68, "redraw"), (0xE3698, "stock_classify"), (0x6BDEA, "setup_load")):
        u.stub(a, name, r0=9 if name == "stock_classify" else 0)
    return r, u


def _names(u, buf, n):
    out = []
    for k in range(n):
        lw = word(u, buf + 12 * k)
        out.append(STILL_LABELS.get(lw) or SETUP_LABELS.get(lw) or text(u, word(u, lw)))
    return out


def _load(u, tid):
    u.uc.mem_write(MDIAL, bytes([2 if tid == 2 else 0]))
    u.calls.clear()
    site = LOAD_SITE[tid]
    u.thumb(site, {}, stop=site + 4)


def test_gr2_shooting_rows_in_place_for_auto_pa_and_m():
    r, u = _shoot()
    buf = res(r, "kf_menu5__kmbuf")
    for tid, slot, cnt in zip((0, 1, 2), M5.DESC_SLOTS, M5.DESC_COUNTS):
        assert word(u, slot) == buf and u.u8(cnt) == len(SHOOT[tid])        # descriptor page 0 / 6 / 7 -> kmbuf
        _load(u, tid)
        assert [c[0] for c in u.calls] == ["load_auto" if tid == 0 else "load_pam"]
        assert _names(u, buf, len(SHOOT[tid])) == SHOOT[tid], tid
    # pages of five: SHOOTING 1 FOCUS..PIC QUALITY, 2 RAW/JPEG..IMG SET, 3 DATE..INTERVAL
    assert SHOOT_AUTO[5:10] == [28, 19, "RECIPE", "EDIT RECIPE", "IMG SET"]
    # the row-index map holds the SHOOTING positions (the mode app's row checks compare it with the cursor row)
    for (t, n, rmap), tid in zip(M5.STILL_TABLES, (0, 1, 2)):
        names = M5.still_names(t, n)
        assert all(u.u8(M5.ROWMAP + o) == SHOOT[tid].index(names[v - 1]) + 1 for o, v in rmap.items())
    # the stock tables keep their stock order (bwrecipes' IMG SET rows, the load / commit literals)
    assert all(u.read(t, 8) == fw.ram(t, 8) and u.read(t + 12 * (n - 1), 8) == fw.ram(t + 12 * (n - 1), 8)
               for t, n, _ in M5.STILL_TABLES)


def test_gr2_shooting_load_shows_the_bytes():
    r, u = _shoot()
    buf = res(r, "kf_menu5__kmbuf")
    u.uc.mem_write(0x1C1EE8 + 12 * 1 + 9, b"\x04")                        # FOCUS cursor as the stock load left it
    for a, v in ((SNAP, 2), (FULL, 1), (LONGEXP, 9), (DZ, 1), (RAWJ, 2), (CLBKT, 1), (ACT, 3)):
        u.uc.mem_write(a, bytes([v]))
    _load(u, 0)
    row = {n: buf + 12 * k for k, n in enumerate(SHOOT_AUTO)}
    cur = {n: u.u8(a + 9) for n, a in row.items()}
    assert cur["FOCUS"] == 4                                               # stock row copied from the stock table
    assert (cur["SNAP DIST"], cur["FULL SNAP"], cur["RECIPE"]) == (3, 2, 4)
    assert cur["LONG EXP LIMIT"] == 1                                      # out of range -> default (180S)
    assert (cur[28], cur[29]) == (3, 2)                                    # FINE NORMAL N640 / ON OFF: byte order
    assert cur[19] == 2                                                    # DIGITAL ZOOM byte 1 = ON, listed OFF, ON
    opts = word(u, row[19] + 4)
    stock = fw.ram(0x1FFF64, 8)
    assert u.read(opts, 8) == stock[4:] + stock[:4] and u.u8(row[19] + 8) == 2
    assert u.read(row[28] + 4, 4) == fw.ram(SETUP_T + 12 * 27 + 4, 4)     # stock options pointer (already byte order)
    assert text(u, word(u, word(u, row["EDIT RECIPE"]))) == "EDIT RECIPE" and u.u8(row["EDIT RECIPE"] + 10) == 1


def test_gr2_shooting_commit_writes_the_bytes():
    r, u = _shoot()
    buf = res(r, "kf_menu5__kmbuf")
    for a in (SNAP, FULL, DZ, RAWJ, ACT):
        u.uc.mem_write(a, b"\x00")
    _load(u, 0)
    row = {n: buf + 12 * k for k, n in enumerate(SHOOT_AUTO)}
    for n, c in (("SNAP DIST", 5), ("FULL SNAP", 2), (19, 2), (28, 9), ("FOCUS", 3), ("RECIPE", 2)):
        u.uc.mem_write(row[n] + 9, bytes([c]))
    u.calls.clear()
    u.thumb(0x58882, {_R0: 0}, stop=0x58886)                              # AUTO commit from the cursors
    assert ("commit_auto", 0) in u.calls
    assert (u.u8(SNAP), u.u8(FULL), u.u8(DZ), u.u8(RAWJ), u.u8(ACT)) == (4, 1, 1, 0, 1)   # RAW/JPEG 9: out of range
    assert u.u8(0x1C1EE8 + 12 * 1 + 9) == 3                                # FOCUS cursor back in the stock table
    assert u.u8(row["SNAP DIST"] + 9) == 5                                 # kmbuf refilled from the bytes
    # a commit from a saved record (r0 > 1) and a commit of a page kmbuf does not show write no row byte
    u.uc.mem_write(row["SNAP DIST"] + 9, b"\x02")
    u.thumb(0x58882, {_R0: 0x1234}, stop=0x58886)
    u.uc.mem_write(MDIAL, b"\x00")
    u.thumb(0x5A0A8, {_R0: 1}, stop=0x5A0AC)                               # P/A commit, kmbuf shows AUTO
    assert u.u8(SNAP) == 4
    # P/A and M carry the same rows and commit through 0x5a260
    for tid, dial in ((1, 0), (2, 2)):
        _load(u, tid)
        k = SHOOT[tid].index("LONG EXP LIMIT")
        u.uc.mem_write(buf + 12 * k + 9, bytes([1 + tid % 2]))
        u.uc.mem_write(MDIAL, bytes([dial]))
        u.calls.clear()
        u.thumb(0x5A0A8, {_R0: 1}, stop=0x5A0AC)
        assert ("commit_pam", 1) in u.calls and u.u8(LONGEXP) == tid % 2, tid


def test_gr2_shooting_hooks_are_stock_in_the_other_layouts_and_under_essentials():
    for layout in (0, 1):
        r, u = _shoot(layout)
        assert all(word(u, a) == int.from_bytes(fw.ram(a, 4), "big") for a in M5.DESC_SLOTS)
        assert all(u.u8(a) == fw.ram(a, 1)[0] for a in M5.DESC_COUNTS)
        assert all(u.u8(M5.ROWMAP + o) == v for _, _, rm in M5.STILL_TABLES for o, v in rm.items())
        u.uc.mem_write(SNAP, b"\x02")
        area = (res(r, "kf_menu5__ovl"), res(r, "kf_menu5__ovl_end") - res(r, "kf_menu5__ovl"))
        before = u.read(*area)                     # kmbuf aliases this layout's tables (overlay area): untouched
        _load(u, 0)
        u.thumb(0x58882, {_R0: 0}, stop=0x58886)
        assert u.u8(SNAP) == 2 and u.read(*area) == before, layout
        assert all(u.u8(M5.ROWMAP + o) == v for _, _, rm in M5.STILL_TABLES for o, v in rm.items())
    r, u = _shoot()                                                        # ESSENTIALS: bwrecipes (gated) bypassed
    u.uc.mem_write(res(r, "koma_ess"), b"\x01")
    nxt = res(r, "kf_bwrecipes__hook_ml0")
    u.watch.add(nxt)
    _load(u, 0)
    assert [c[0] for c in u.calls] == ["load_auto"] and not u.watch_hits
    assert _names(u, res(r, "kf_menu5__kmbuf"), 22) == SHOOT_AUTO
    u.uc.mem_write(res(r, "koma_ess"), b"\x00")
    _load(u, 0)
    assert u.watch_hits                                                    # FULL: the chain runs bwrecipes' hook


def test_gr2_edit_recipe_from_shooting():
    r, u = _shoot()
    kmv, v = res(r, "kf_menu5__kmv"), res(r, "kf_menu5__vars")
    erow = SHOOT_AUTO.index("EDIT RECIPE") + 1
    _load(u, 0)
    u.uc.mem_write(0x1C23B8, b"\x00")                                    # page 0 (AUTO), mode app tab, row state
    u.uc.mem_write(0x1C23B9, b"\x01")

    rrow = res(r, "kf_menu5__kmbuf") + 12 * SHOOT_AUTO.index("RECIPE")

    def right(row, recipe=1):
        u.uc.mem_write(rrow + 9, bytes([recipe + 1]))                      # RECIPE as chosen on this page
        u.uc.mem_write(0x1C23EE, bytes([row]))
        u.uc.mem_write(0x1C23F0, b"\x02")
        u.calls.clear()
        return u.thumb(res(r, "kf_menu5__kright"), {_R0: 0x17})
    right(erow - 1)                                                         # another row: stock RIGHT, untouched
    assert u.calls == [("stock_right", 0x17)] and u.u8(0x1C23F0) == 2 and u.u8(kmv + 1) == 0
    assert right(erow, recipe=0) == 0 and u.calls == []                    # recipe OFF: nothing to edit
    right(erow)                                                             # EDIT RECIPE: header RIGHT -> SETUP app
    assert u.calls[-1] == ("stock_right", 0x17) and u.u8(0x1C23F0) == 0 and u.u8(v + 1) == 2
    u.uc.mem_write(kmv + 1, b"\x01")                                       # (what the SETUP app saw, cleared on return)
    u.uc.mem_write(0x1C23B9, b"\x02")
    ked, rdw = res(r, "kf_menu5__kedpend"), res(r, "kf_menu5__redraw")
    u.watch |= {ked, rdw}
    u.watch_hits.clear()
    u.thumb(res(r, "kf_menu5__setup_enter"), {_R0: 2})                     # setup_enter: the editor is shown
    assert word(u, 0x1C23E0) == res(r, "kf_menu5__tblE") and u.u8(kmv + 1) == 0
    assert [a for a, _ in u.watch_hits][:1] == [ked]                       # swapped in before the first redraw
    u.calls.clear()
    assert u.thumb(res(r, "kf_menu5__classify"), {_R0: 0x10}) == 9         # LEFT in the editor: stock exit (case 9)
    assert u.calls == [("stock_classify", 0x10)] and u.u8(0x1C23F0) == 0 and u.u8(kmv + 2) == 1 and u.u8(v) == 0
    u.uc.mem_write(0x1C23B9, b"\x01")
    u.uc.mem_write(0x1C23B8, b"\x00")
    u.uc.mem_write(0x1C23EE, bytes([erow]))
    u.uc.mem_write(0x1C23F0, b"\x02")
    u.calls.clear()
    u.thumb(res(r, "kf_menu5__kright"), {_R0: 0x17})                       # the SETUP app returns after that LEFT
    assert (u.u8(0x1C23EE), u.u8(0x1C23ED), u.u8(0x1C23F0)) == (erow, 2, 2)          # cursor back on EDIT RECIPE
    assert "redraw" in [c[0] for c in u.calls]
    assert u.u8(kmv + 2) == 0
    r0 = link([F.make("menu5")])                                            # no MENU LAYOUT row: no GR II code at all
    assert not {"kf_menu5__krowmap", "kf_menu5__kdir2", "kf_menu5__kmbuf", "kf_menu5__kright"} & set(r0.symbols)


def test_gr2_state_is_resident_outside_the_layout_overlays():
    # kmv aliased into the overlay area read 5-TAB's label tables (filled by kinit) as "editor
    # pending", so kedpend opened the recipe editor on the next tab select and koma smoke never reached the format;
    # ksync_load's version-cursor write to kv_entry_g2 (also GR II overlay) hit 5-TAB's metatab CUSTOM entry.
    r = _shoot_build()
    kmv, ked = res(r, "kf_menu5__kmv"), res(r, "kf_menu5__kedpend")
    assert not res(r, "kf_menu5__ovl") <= kmv < res(r, "kf_menu5__ovl_end")
    for layout in (0, 1, 2):
        u = boot(r, layout=layout)
        assert u.read(kmv, 4) == bytes(4), layout
        u.uc.mem_write(0x1C23E0, bytes(4))
        u.thumb(ked, {})                                                   # setup_enter's pre-redraw step
        assert word(u, 0x1C23E0) == 0, layout                             # no editor without kright's request
        area = (res(r, "kf_menu5__ovl"), res(r, "kf_menu5__ovl_end") - res(r, "kf_menu5__ovl"))
        before = u.read(*area)
        u.thumb(res(r, "kf_menu5__ksync_load"), {})                        # version cursor: GR II copy only in GR II
        assert layout == 2 or u.read(*area) == before, layout              # 5-TAB / STK+KOMA tables untouched


def test_full_allocates_the_whole_resident_block():
    r = _gated_build()
    u = boot(r, feat=0)
    assert [c for c in u.calls if c[0] == "alloc"][0][2] == r.manifest["layout"]["resident_bytes"]
    assert u.u8(res(r, "koma_ess")) == 0


def test_layout_overlays_shrink_the_resident_block_and_copy_at_install():
    r = link([F.make("menu5"), F.make("bwrecipes"), F.make("settingsio-menu")])
    m5 = next(f for f in r.manifest["features"] if f["name"] == "menu5")
    ov = m5["overlays"]
    assert ov["select"] == "0x1b8bb" and len(ov["bytes"]) == 3 and ov["area_bytes"] >= max(ov["bytes"])
    for name in ("kf_menu5__metatab_sk", "kf_menu5__metatab_g2", "kf_menu5__kmbuf", "kf_menu5__metatab"):
        assert res(r, "kf_menu5__ovl") <= res(r, name) < res(r, "kf_menu5__ovl_end"), name   # all in the one area
    body = r.symbols["kf_menu5__ovk2"]                       # GR II copy in the transient body
    assert body < r.symbols["koma_res"]
    for layout in (1, 2):
        u = boot(r, layout=layout)
        a = res(r, "kf_menu5__ovl")
        k = r.symbols[f"kf_menu5__ovk{layout}"]
        n = r.symbols[f"kf_menu5__ovk{layout}_end"] - k
        meta = word(u, a)                                    # the directory's metatab is the overlay's, relocated
        assert meta == res(r, "kf_menu5__metatab_sk" if layout == 1 else "kf_menu5__metatab_g2"), layout
        assert len(u.read(a, n)) == n


def test_gr2_hooks_must_head_their_chains():
    import pytest
    from koma.linker import LinkError
    with pytest.raises(LinkError, match="menu5 must be first"):
        link([F.make("bwrecipes"), F.make("menu5"), F.make("settingsio-menu")])
