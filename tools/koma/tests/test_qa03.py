"""Cross-feature seams no single feature's tests cover.

Covered: menu5 xlate (review F1) for every row of the 13-row 0.3 editor, in koma03 and in a minimal menu5,bwrecipes,fx build;
the EDIT RECIPE display order; both live-view entry paths refresh preview's chroma tap (0x922bc(0)) before the
preview hook site, so a still develop's tap (0x922bc(1) at 0x9642c / 0xafd34) never reaches the screen.
"""
from __future__ import annotations

import struct

from _util import fw
from koma import features as F
from koma.asm import bl_target
from koma.features import bwrecipes as B
from koma.features import menu5 as M5
from koma.linker import link
from koma.unitemu import RES_POOL, Unit
from unicorn.arm_const import UC_ARM_REG_R0



def _xlate_rows(feats):
    r = link([F.make(n) for n in feats])
    res = lambda s: RES_POOL + r.symbols[s] - r.symbols["koma_res"]     # noqa: E731
    u = Unit(r.module)
    assert u.install() == 0
    main, ed, _ = M5.layout([dict(x, byte=int(x["byte"], 16)) for x in r.manifest["rows"]])
    u.uc.mem_write(M5.D_TAB, b"\x02")
    u.uc.mem_write(res("kf_menu5__vars"), b"\x01")                   # slot 1 = KOMA tab, editor shown
    return len(main), [u.thumb(res("kf_menu5__xlate"), {UC_ARM_REG_R0: row}) for row in range(1, len(ed) + 1)]


def test_editor_rows_never_fall_through_to_setup():
    """Review F1 with 0.3's editor (title + 13 rows): every editor row maps to 31 (no stock SETUP row), both in koma03 and in a
    build whose KOMA tab is far shorter than the editor."""
    for feats in (["menu5", "bwrecipes", "fx"], F.SETS["koma03"]):
        kcount, rows = _xlate_rows(feats)
        assert len(rows) >= 13 and set(rows) == {31}, (feats, kcount, rows)     # title row + 13 rows


def test_edit_recipe_order():
    r = link([F.make(n) for n in F.SETS["koma03"] if n != "telemetry"])
    ed = [x["label"] for x in r.manifest["rows"] if x["group"] == "recipe-edit"]
    assert ed[-5:] == ["SHARPNESS", "GRAIN", "VIGNETTE", "SAVE TO CUSTOM", "RESET"], ed


def _bls(start, end):
    out = []
    for a in range(start, end, 2):
        t = bl_target(a, fw.ram(a, 4))
        if t is not None:
            out.append((a, t))
    return out


def test_preview_tap_is_refreshed_before_each_apply():
    """pv_tap also fires in still develop (0x922bc(1) at 0x9642c / 0xafd34). Monitor start and post-shot resume both
    call 0x98db0 -> 0x922bc(0) before their hooked bl 0x8ff0e, so pv_chroma never scales a still's chroma word."""
    assert bl_target(0x9642C, fw.ram(0x9642C, 4)) == 0x922BC and fw.ram(0x9642A, 2) == b"\x20\x01"
    assert any(t == 0x922BC for _, t in _bls(0x98DB0, 0x98DF4))
    assert fw.ram(0x98DC4, 2) == b"\x20\x00"                          # movs r0, #0 (monitor preset)
    for start, site in ((0x985A0, 0x98726), (0x95CD0, 0x95E24)):
        calls = [a for a, t in _bls(start, site) if t == 0x98DB0]
        assert calls and calls[-1] < site, hex(start)


def test_selcolour_fx_chain_on_the_real_site():
    """The 0x117382 chain as koma03 links it (selcolour -> fx -> original 0x1173e8), which `koma smoke` never reaches:
    SELECT RED on the UV plane and GRAIN 2 / VIGNETTE 2 on the Y plane in one shot, each equal to its own model, the
    original called once with cp, r4-r7 and sp as on entry; with both FX OFF and no SELECT the planes are untouched."""
    from koma.features import fx as X
    from koma.features import selcolour as SC
    from unicorn.arm_const import UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_SP
    r = link([SC.make({}), X.make({})])
    assert [f["name"] for f in r.manifest["features"]] == ["selcolour", "fx"]
    u = Unit(r.module)
    u.uc.mem_map(0x08000000, 0x01000000)
    assert u.install() == 0
    sym = lambda s: u.resident[0] + r.symbols[s] - r.symbols["koma_res"]     # noqa: E731
    cp, y, uv, w, s, n = 0x700000, 0x8605F00, 0x8F40E00, 2048, 2080, 6
    yp = bytes(((x * 256 // w) + ln * 7) & 0xFF if x < w else 0x5A for ln in range(n) for x in range(s))
    uvp = bytes((x * 37 + ln * 11) & 0xFF if x < w else 0x5A for ln in range(n) for x in range(s))
    u.stub(SC.TARGET, "orig_1173e8")
    for (col, g, v) in ((1, 2, 2), (0, 0, 0)):
        hdr = bytearray(16)
        hdr[0:4], hdr[9], hdr[10], hdr[11], hdr[12] = B.MAGIC, col, 0, g, v
        u.uc.mem_write(B.ST, bytes(hdr))
        u.uc.mem_write(SC.MODEB, b"\0")
        u.uc.mem_write(cp, bytes(0x100))
        u.uc.mem_write(cp + 0x54, struct.pack(">II", y, uv))
        u.uc.mem_write(cp + 0x5C, struct.pack(">HHH", s, w, n))
        u.uc.mem_write(y, yp)
        u.uc.mem_write(uv, uvp)
        u.calls.clear()
        sent = {UC_ARM_REG_R4: cp, UC_ARM_REG_R5: 0x55, UC_ARM_REG_R6: 0x66, UC_ARM_REG_R7: 0x77}
        u.thumb(0x117380, sent, stop=0x117386, count=50_000_000)
        assert u.calls == [("orig_1173e8", cp)], u.calls
        assert all(u.uc.reg_read(k) == val for k, val in sent.items())
        from koma.unitemu import STACK
        assert u.uc.reg_read(UC_ARM_REG_SP) == STACK
        ya, uva = u.read(y, len(yp)), u.read(uv, len(uvp))
        if col == 0:
            assert ya == yp and uva == uvp
            continue
        st = u.read(sym("kf_fx__state"), X.STATE_LEN)
        seed = struct.unpack(">I", st[X.O_SEED:X.O_SEED + 4])[0]
        assert ya == X.reference_pass(yp, s, w, n, g, v, seed)
        assert uva == SC.reference_pass(uvp, s, w, n, SC.reference_lut(2 * (col - 1)))
