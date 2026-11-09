"""menu5 port + KOMA page rows + core settings init + build-time lint (platform lane #14)."""
from __future__ import annotations

import pytest
from _util import Feature, Patch, Settings, fw, hook_feature

from koma import features as F
from koma.linker import LinkError, link
from koma.unitemu import RES_POOL, Unit

ROW = dict(id="snapdist", label="SNAP DIST", options=["1M", "1.5M", "2M", "2.5M", "5M", "\u221e"], byte=0x1B8B5,
           default=3)


def sample(rows=(ROW,), **kw):
    return Feature("sample", "t", "sample rows", rows=[dict(r) for r in rows], **kw)


def build(*extra):
    return link([F.make("menu5"), *extra])


def unit(r, tail=None):
    u = Unit(r.module)
    if tail is not None:                                 # unitemu writes its settings block after ram_edits
        u.uc.mem_write(0x1B8B0, tail)
    return u


def res(r, sym):
    return RES_POOL + r.symbols[sym] - r.symbols["koma_res"]


def test_menu5_ports_all_23_records_with_before_bytes():
    f = F.make("menu5")
    assert (len(f.redirects), len(f.pointers), len(f.patches)) == (8, 12, 3)
    r = build()
    recs = [x for x in r.manifest["records"] if x["owner"] == "menu5"]
    assert len(recs) == 23 and 50 < r.manifest["layout"]["relocs"] < 400   # label tables: no relocs (koma-modes)


def test_settings_init_fresh_then_keep_then_repair():
    r = build(sample())
    assert [x["id"] for x in r.manifest["rows"]] == ["snapdist"]
    u = unit(r)                                         # unitemu tail 0x1b8b0.. = 0x03: no magic
    assert u.install() == 0
    assert u.read(0x1B8B0, 6) == b"KOMA\x01\x03"
    u = unit(r, b"KOMA\x01\x05")
    u.install()
    assert u.read(0x1B8B5, 1) == b"\x05"                 # magic present: user value kept
    u = unit(r, b"KOMA\x01\x09")
    u.install()
    assert u.read(0x1B8B5, 1) == b"\x03"                 # out of range: default


def test_koma_page_load_store_and_version_glyphs():
    r = build(sample())
    from koma.features.menu5 import legacy
    glyphs = legacy()[2]
    u = unit(r, b"KOMA\x01\x01")
    u.install()
    u.thumb(res(r, "kf_menu5__ksync_load"), {})
    e = res(r, "kf_menu5__kr_e_0")                       # SNAP DIST entry (other group -> OTHER page)
    assert u.u8(e + 9) == 2                               # byte 1 -> cursor 2 (1.5M)
    assert u.u8(res(r, "kf_menu5__kv_entry") + 9) == 1
    ver = r.manifest["version_string"]                    # "KOMA V0.2.2 XXXXXXXX"
    n = len(ver.split()[1]) - 1
    want = glyphs.enc(ver[6:6 + n])
    assert u.read(res(r, "kf_menu5__kv_short_s"), len(want)) == want
    assert u.read(res(r, "kf_menu5__kv_id_s"), 18) == glyphs.enc(ver[7 + n:15 + n])
    u.uc.mem_write(e + 9, b"\x06")                        # user picks the 6th option
    u.thumb(res(r, "kf_menu5__ksync_store"), {})
    assert u.u8(0x1B8B5) == 5
    u.uc.mem_write(e + 9, b"\x09")                        # never writes an out-of-range value
    u.thumb(res(r, "kf_menu5__ksync_store"), {})
    assert u.u8(0x1B8B5) == 5 and u.u8(e + 9) == 6        # and the cursor is reloaded from the byte


# ---------------------------------------------------------------- IA v2: KOMA tab layout and navigation
def _row(i, group, n=2, **kw):
    return dict(dict(id=f"r{i}", label=f"ROW {i}", options=[f"O{k}" for k in range(n)], byte=0x1B8B5 + i, default=0,
                     group=group), **kw)


def test_layout_one_topic_per_page_titles_and_padding():
    from koma.features.menu5 import layout, skips
    rows = [_row(0, "focus"), _row(1, "recipe"), _row(2, "display"), _row(3, "weird"), _row(4, "recipe-edit"),
            _row(5, "exposure"), _row(6, None)]
    main, editor, rr = layout(rows)
    kinds = [e[0] if e[0] != "title" else e[1] for e in main]
    assert kinds == ["KOMA: RECIPE", "row", "edit", "blank", "blank",
                     "KOMA: FOCUS", "row", "blank", "blank", "blank",
                     "KOMA: EXPOSURE", "row", "blank", "blank", "blank",
                     "KOMA: DISPLAY", "row", "version", "blank", "blank",
                     "KOMA: OTHER", "row", "row"]                      # unknown and missing groups: OTHER, last
    assert rr["id"] == "r1" and [e[0] for e in editor] == ["etitle", "row"] and editor[1][1]["id"] == "r4"
    assert skips(main)[:3] == [1, 0, 0] and skips(editor) == [1, 0]
    main, editor, rr = layout([_row(i, "exposure") for i in range(6)])    # 6 rows: continues on a second page
    assert [e[1] for e in main if e[0] == "title"] == ["KOMA: EXPOSURE", "KOMA: EXPOSURE", "KOMA: DISPLAY"]
    assert editor == [] and rr is None and all(len(t) <= 14 for t in (e[1] for e in main if e[0] == "title"))


def test_release_layout_matches_the_owner_ia():
    from koma.features import menu5 as M5
    from koma.features.menu5 import layout
    from koma.linker import collect_rows
    rows, errors, _ = collect_rows([F.make(n) for n in F.KOMA03])
    assert not errors
    main, editor, _ = layout(rows)
    lab = lambda e: e[1] if e[0] == "title" else e[1]["label"] if e[0] == "row" else e[0]   # noqa: E731
    assert [lab(e) for e in main if e[0] != "blank"] == [
        "KOMA: RECIPE", "RECIPE", "edit", "KOMA: FOCUS", "SNAP DIST", "FULL SNAP",
        "KOMA: EXPOSURE", "AUTO-ISO MAX", "SLOW SHUTTER", "LONG EXP LIMIT", "KOMA: DISPLAY", "HIGHLIGHT ALRT",
        "version"]
    # Koma 0.2: rows p5..p7 are declared with their colour labels; menu5 shows the active type's variant (B&W FILTER
    # EFF. / TONING / TONE LEVEL, SELECT COLOUR / RANGE / SATURATION; test_bwrecipes covers the switch)
    assert [lab(e) for e in editor] == ["etitle", "CURVE", "CONTRAST", "KEY", "SHADOW", "HIGHLIGHT", "SATURATION",
                                       "WARMTH", "TINT", "SHARPNESS", "GRAIN", "VIGNETTE", "SAVE TO CUSTOM",
                                       "RESET"]
    assert [M5.row_variants(e[1])["alts"][0][0] for e in editor if e[0] == "row" and M5.row_variants(e[1])] == [
        "FILTER EFF.", "TONING", "TONE LEVEL"]
    assert all(len(r["label"]) <= 14 and all(len(o) <= 9 for o in r["options"]) for r in rows)


def test_koma02_rows_and_shared_label_tables():
    """Koma 0.2 set: selcolour adds no rows (SELECT's rows live in the recipe editor); identical strings share one label
    table, which keeps menu5 at or below the 0.2 B&W bundle's 11,492 B despite 7 more looks and the variant rows."""
    from koma.linker import collect_rows
    r01, e01, _ = collect_rows([F.make(n) for n in F.KOMA01])
    r02, e02, _ = collect_rows([F.make(n) for n in F.KOMA02])
    assert not e01 and not e02 and [x["id"] for x in r01] == [x["id"] for x in r02]
    r = link([F.make("menu5"), F.make("bwrecipes")])
    m = {f["name"]: f["resident_bytes"] for f in r.manifest["features"]}
    assert m["menu5"] <= 11492, m
    assert r.symbols["kf_menu5__kr_opts_2"] == r.symbols["kf_menu5__kr_opts_3"]   # CONTRAST / KEY: one option list


ONCH = """
on_change:                              @ a change of the recipe row moves the next row (like a recipe load)
    ldr r2, =0x1b8b6
    movs r3, #1
    strb r3, [r2]
    bx lr
.ltorg
"""


def test_store_applies_changes_found_before_any_on_change():
    f = sample([_row(0, "recipe", n=3), _row(1, "recipe-edit", n=3)], source=ONCH, exports=["on_change"])
    r = build(f)
    u = unit(r, b"KOMA\x01\x00\x00")
    u.install()
    u.thumb(res(r, "kf_menu5__ksync_load"), {})
    ea, eb = res(r, "kf_menu5__kr_e_0"), res(r, "kf_menu5__kr_e_1")
    assert (u.u8(ea + 9), u.u8(eb + 9), u.u8(res(r, "kf_menu5__ket_entry") + 9)) == (1, 1, 1)
    u.uc.mem_write(ea + 9, b"\x03")                       # recipe row -> option 3; edit row cursor still stale (1)
    u.thumb(res(r, "kf_menu5__ksync_store"), {})
    assert (u.u8(0x1B8B5), u.u8(0x1B8B6)) == (2, 1)       # on_change's write survives (old code wrote 0 back)
    assert (u.u8(eb + 9), u.u8(res(r, "kf_menu5__ket_entry") + 9)) == (2, 3)   # cursors reloaded; title mirrors


def _nav(r, recipe=0):
    from koma.features import menu5 as M5
    u = unit(r, b"KOMA\x01" + bytes([recipe]) + b"\x00")
    u.install()
    u.stub(0xD3A68, "redraw")
    u.stub(0xE3698, "stock_classify", r0=8)
    u.thumb(res(r, "kf_menu5__ksync_load"), {})

    def key(code, state=None, row=None, page=None, tbl=None, cnt=None):
        for a, v in ((M5.D_STATE, state), (M5.D_ROW, row), (M5.D_PAGE, page), (M5.D_CNT, cnt)):
            if v is not None:
                u.uc.mem_write(a, bytes([v]))
        if tbl is not None:
            u.uc.mem_write(M5.D_TBL, tbl.to_bytes(4, "big"))
        u.calls.clear()
        from unicorn.arm_const import UC_ARM_REG_R0
        ret = u.thumb(res(r, "kf_menu5__classify"), {UC_ARM_REG_R0: code})
        return ret, u.u8(M5.D_STATE), u.u8(M5.D_ROW), u.u8(M5.D_PAGE), int.from_bytes(u.read(M5.D_TBL, 4), "big")
    u.uc.mem_write(M5.D_TAB, b"\x02")
    return u, key


def test_navigation_skips_titles_and_blanks_and_opens_the_editor():
    f = sample([_row(0, "recipe", n=3), _row(1, "recipe-edit", n=3), _row(2, "focus")], source=ONCH,
               exports=["on_change"])
    r = build(f)
    t1, te = res(r, "kf_menu5__tbl1"), res(r, "kf_menu5__tblE")
    DOWN, UP, LEFT, RIGHT, OK = 0x40, 0x30, 0x10, 0x20, 0x50
    u, key = _nav(r)
    assert key(DOWN, state=0, row=1, page=1, tbl=t1, cnt=12)[:4] == (0xFF, 2, 2, 1)   # header -> RECIPE (row 2)
    assert key(DOWN)[:4] == (0xFF, 2, 3, 1)                                           # EDIT RECIPE
    assert key(DOWN)[:4] == (0xFF, 2, 7, 2)                                           # skips blanks + FOCUS title
    assert key(UP)[:4] == (0xFF, 2, 3, 1)
    assert key(DOWN, row=12)[:4] == (0xFF, 0, 1, 1)                                   # past the end -> header
    assert key(UP, state=0)[:4] == (0xFF, 2, 12, 3)                                   # header UP -> last row
    assert key(RIGHT, state=1, page=2)[:4] == (0xFF, 2, 7, 2)                         # page column -> 1st row
    assert [c[0] for c in u.calls] == ["redraw"]
    assert key(RIGHT, state=2, row=3)[4] == t1                                        # recipe OFF: no editor
    u.uc.mem_write(0x1B8B5, b"\x02")
    ret, st, row, page, tbl = key(OK, state=2, row=3)
    assert (ret, st, row, tbl) == (0xFF, 2, 2, te) and u.u8(0x1C23EC) == 2           # editor, on its 1st row
    assert u.u8(res(r, "kf_menu5__ket_entry") + 9) == 3                               # title shows the recipe
    assert key(LEFT)[:5] == (0xFF, 2, 3, 1, t1) and u.u8(0x1C23EC) == 12              # back on EDIT RECIPE
    ret, *_ = key(RIGHT, state=2, row=7)                                              # other keys: stock path
    assert ret == 8 and ("stock_classify", RIGHT) in u.calls


def test_strip_labels_and_setup_has_stock_rows_only():
    from koma.features.menu5 import legacy
    M = legacy()[0]
    r = build(sample())
    u = unit(r)
    u.install()
    sd = res(r, "kf_menu5__stripdata")
    words = [int.from_bytes(u.read(sd + 8 * i + 4, 4), "big") for i in range(5)]
    assert words == [res(r, f"kf_menu5__strip_{n}") for n in ("STILL", "KOMA", "PLAY", "CUSTOM", "SETUP")]
    meta = res(r, "kf_menu5__metatab")
    assert int.from_bytes(u.read(meta + 12 * 4 + 8, 4), "big") == len(M.TABS[4]) - 1 == 10
    u.uc.mem_write(0x1C23B8, b"\x01")                     # movie page: slot 1 reads MOVIE
    u.thumb(res(r, "kf_menu5__kstripfix"), {})
    assert int.from_bytes(u.read(sd + 12, 4), "big") == res(r, "kf_menu5__strip_MOVIE")
    assert int.from_bytes(u.read(sd + 10, 2), "big") == 55


@pytest.mark.parametrize("case", [
    (dict(byte=0x1B8B4), "outside"), (dict(label="SNAP DIST LONG NAME"), "longer"), (dict(options=["1,5M"]), "font"),
    (dict(default=9), "default"), (dict(options=["TOOLONGOPTION"]), "chars")])
def test_rows_are_validated(case):
    bad, msg = case
    with pytest.raises(LinkError, match=msg):
        build(sample([dict(ROW, **bad)]))


def test_two_rows_on_one_byte_conflict():
    with pytest.raises(LinkError, match="also used"):
        build(sample([ROW, dict(ROW, id="other")]))


def test_lint_rejects_flash_and_store_calls():
    src = "f:\n    push {lr}\n    ldr r3, =0x4949c+1\n    pop {pc}\n"
    with pytest.raises(LinkError, match="0x4949d.*settings save"):
        link([Feature("bad", "t", "x", source=src, init="f")])
    with pytest.raises(LinkError, match="OneNAND program"):
        link([Feature("bad", "t", "x", source="f:\n    bx lr\n.p2align 2\n    .word 0x64f05\n", init="f")])


def test_lint_rejects_sites_executed_before_the_loader_hook():
    a = 0x8FF0E                                          # in phase A (executes during boot)
    with pytest.raises(LinkError, match="before the loader hook"):
        link([Feature("early", "t", "x", patches=[Patch(a, fw.ram(a, 2), b"\x46\xc0")])])


def test_lint_stock_settings_need_allowed_values():
    with pytest.raises(LinkError, match="allowed values"):
        link([hook_feature("w", 1, settings=[Settings(0x1B030, 0x1B031, owned=False)])])
    link([hook_feature("w", 1, settings=[Settings(0x1B030, 0x1B031, owned=False, values=range(7))])])


def test_transient_init_runs_from_the_body_and_keeps_nothing_resident():
    from _util import LOG
    src = f"""
t_init:
    push {{r4, lr}}
    ldr r2, =0x{LOG:x}
    ldr r0, =$koma.manifest
    str r0, [r2, #0]
    ldr r0, =msg
    ldr r0, [r0]
    str r0, [r2, #4]
    pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg
.p2align 2
msg: .word 0x54524e53
"""
    t = Feature("tinit", "t", "x", source=src, init="t_init", transient=True)
    r = link([t])
    assert r.manifest["features"][0]["resident_bytes"] == 0 and r.manifest["features"][0]["transient_code_bytes"] > 0
    u = Unit(r.module)
    assert u.install() == 0 and u.callee_saved_ok
    assert u.read(LOG, 8) == RES_POOL.to_bytes(4, "big") + b"TRNS"
    from koma.selfcheck import selfcheck
    rep = selfcheck(r.module, r.manifest, verbose=False)          # (its LOG write is flagged as stray: expected)
    assert all(c["ok"] for c in rep["checks"] if "RAM writes outside" not in c["check"])
