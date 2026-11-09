"""ksettings: -SDIS / -GDIS records become -KSET / -KGET. Unit emulation on the 2.40 payload: the real parser 0xcbd64 and
dispatcher 0xcc304 run the command text, so syntax handling is the stock parser's plus the handlers' digit check; the
-KGET data phase runs the real stock states 0xcb834 (event 0x10) and 0xcb9cc (event 0x20)."""
from __future__ import annotations

import struct

from _util import fw
from koma import features as F
from koma.features import ksettings as K
from koma.features import preview as P
from koma.linker import link
from koma.unitemu import Unit
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3

REPLY, LEN, T, CMD = 0x7F0500, 0x7F0504, 0x7F0508, 0x7F0600
USB = 0x700000                  # stands in for 0x0bf844e0 (the unit map ends at 0x800000)
BLK = K.BLOCK
_CACHE = {}


def _unit(features=("bwrecipes", "preview", "ksettings"), opts=None):
    key = (features, tuple(sorted((opts or {}).items())))
    if key not in _CACHE:
        _CACHE[key] = link([F.make(n, (opts or {}) if n == "ksettings" else {}) for n in features])
    r = _CACHE[key]
    u = Unit(r.module)
    assert u.install() == 0 and u.callee_saved_ok
    u.res = lambda name: u.resident[0] + r.symbols[name] - r.symbols["koma_res"]
    block = bytearray(u.read(BLK, 0x1000))
    for i in range(0x8B5, 0x8D0):
        block[i] = 0
    block[0x8C2:0x8C6] = b"\x04" * 4
    u.uc.mem_write(BLK, bytes(block))
    _w32(u, K.USB_BUF, USB)
    u.uc.mem_write(K.MONI_RUN, b"\0")
    u.stub(0x55F0C, "close", r0=0)
    return u


def _w32(u, a, v):
    u.uc.mem_write(a, struct.pack(">I", v & 0xFFFFFFFF))


def _u32(u, a):
    return struct.unpack(">I", u.read(a, 4))[0]


def _cmd(u, text):
    """Parser + dispatcher as the idle state 0xcb76e runs them. Returns (reply, len, state, block writes)."""
    raw = text.encode()
    u.uc.mem_write(CMD, raw + b"\0" * 16)
    for a in (REPLY, LEN, K.XSTATE, K.TOTAL, K.CHUNK, K.FINAL):
        _w32(u, a, 0)
    _w32(u, K.XSTATE, 1)
    u.writes.clear()
    u.calls.clear()
    u.thumb(0xCBD64, {UC_ARM_REG_R0: CMD, UC_ARM_REG_R1: len(raw), UC_ARM_REG_R2: REPLY, UC_ARM_REG_R3: LEN})
    if _u32(u, 0x1FB3E0) == 1:
        u.thumb(0xCC304, {UC_ARM_REG_R0: REPLY, UC_ARM_REG_R1: LEN})
    p, n = _u32(u, REPLY), _u32(u, LEN)
    reply = u.read(p, n).decode() if p else None
    blk = sorted({a for a, s in u.writes if BLK <= a < BLK + 0x1000})
    return reply, n, _u32(u, K.XSTATE), blk


def _kset(u, off, val):
    return _cmd(u, f"-KSET {off},{val}\r\n")


def test_records_and_before_bytes():
    assert fw.ram(K.REC_SET, 0x48) == bytes.fromhex(
        "000cb6240000001600000000000000000000000000000000000000000000000100" "0cd399"
        "000cb62c00000017" + "00" * 24 + "000cd39b")
    assert fw.ram(0xCB624, 16) == b"-SDIS\0\0\0-GDIS\0\0\0"
    assert fw.ram(0xCD398, 4) == bytes.fromhex("47704770")          # both handlers: bx lr
    f = K.make({})
    assert {p.addr: p.before for p in f.pointers} == {
        0x1FB6D0: K.SET_NAME_BEFORE, 0x1FB6F0: K.SET_HANDLER_BEFORE, 0x1FB6F4: K.GET_NAME_BEFORE,
        0x1FB714: K.GET_HANDLER_BEFORE, 0x1FB760: K.STA_NAME_BEFORE, 0x1FB780: K.STA_HANDLER_BEFORE,
        0x1FB6AC: K.PEEK_NAME_BEFORE, 0x1FB6CC: K.PEEK_HANDLER_BEFORE}
    assert fw.ram(K.REC_PEEK, 0x24) == bytes.fromhex("000cb61c" + "00" * 28 + "000cd397")
    assert fw.ram(0xCB61C, 6) == b"-PWOF\0" and fw.ram(0xCD396, 2) == bytes.fromhex("4770")
    assert fw.ram(K.REC_STA, 0x24) == bytes.fromhex("000cb64400000019" + "00" * 24 + "000cd39d")
    assert fw.ram(0xCB644, 6) == b"-FUPL\0" and fw.ram(0xCD39C, 2) == bytes.fromhex("4770")
    assert [(p.addr, p.after) for p in f.patches] == [(0x1FB6E0, b"\0\0\0\1"), (0x1FB6EC, b"\0\0\0\0"),
                                                      (0x1FB704, b"\0\0\0\1"), (0x1FB6B0, b"\0\0\0\x25"),
                                                      (0x1FB6BC, b"\0\0\0\1")]
    for c in f.checks:
        assert fw.ram(c.addr, len(c.before)) == c.before, hex(c.addr)
    assert not f.hooks and not f.redirects and not f.settings and not f.rows
    assert f.requires == ["bwrecipes", "preview"] and K.make({"apply": "off"}).requires == []
    assert fw.ram(K.FW_501, len(K.S501)).decode() == K.S501
    assert fw.ram(K.FW_150, len(K.S150)).decode() == K.S150 and fw.ram(K.FW_250, len(K.S250)).decode() == K.S250


def test_install_writes_only_the_records():
    u = _unit(("ksettings",), {"apply": "off"})
    u2 = Unit(link([K.make({"apply": "off"})]).module)
    assert u2.install() == 0
    out = sorted({a & ~3 for a, _ in u2.outside_writes() if not 0x58190 <= a < 0x58198})
    assert out == [0x1FB6AC, 0x1FB6B0, 0x1FB6BC, 0x1FB6CC, 0x1FB6D0, 0x1FB6E0, 0x1FB6EC, 0x1FB6F0, 0x1FB6F4, 0x1FB704,
                   0x1FB714, 0x1FB760, 0x1FB780], \
        [hex(a) for a in out]
    assert u.read(_u32(u, 0x1FB6D0), 6) == b"-KSET\0" and u.read(_u32(u, 0x1FB6F4), 6) == b"-KGET\0"
    assert _u32(u, 0x1FB6F0) & 1 and _u32(u, 0x1FB714) & 1
    assert u.read(_u32(u, 0x1FB760), 6) == b"-KSTA\0" and _u32(u, 0x1FB780) & 1


EDGES = [(0x000, False), (0x8B4, False), (0x8B5, True), (0x8CF, True), (0x8D0, False), (0x8DF, False),
         (0x8E0, False), (0x8FF, False), (0x900, False), (0x90F, False), (0x910, True), (0x9FF, True), (0xA00, True),
         (0xA13, True), (0xA14, False), (0xFFC, False), (0xFFF, False), (0x1000, False), (9999999, False)]


def test_window_edges_accept_and_refuse_with_no_other_block_write():
    for features, opts in ((("ksettings",), {"apply": "off"}), (("bwrecipes", "preview", "ksettings"), {})):
        u = _unit(features, opts)
        for off, ok in EDGES:
            reply, n, state, blk = _kset(u, off, 0)
            assert state == 3 and n == len(reply), (off, reply)
            if ok:
                assert reply == f"200 -KSET {off}=0\r\n" == K.model(off, 0, {}) and blk == [BLK + off], (off, reply)
                assert u.u8(BLK + off) == 0
            else:
                assert reply == K.S451_RANGE == K.model(off, 0, {}) and blk == [], (off, reply, blk)
            assert u.u8(0x1BD2C2) == 1 and u.u8(0x1BD2C0) == 0


def test_value_and_option_count_refusal():
    u = _unit()
    counts = K.row_counts(F.make("bwrecipes").rows)
    assert counts[0x8C0] == 21 and counts[0x8C2] == 9
    cases = [(0x8C0, 20, True), (0x8C0, 21, False), (0x8C2, 8, True), (0x8C2, 9, False), (0x8C2, 255, False),
             (0x910, 255, True), (0x910, 256, False), (0x8B5, 255, True), (0x8CF, 0, True)]
    for off, val, ok in cases:
        before = u.u8(BLK + off)
        reply, _, state, blk = _kset(u, off, val)
        assert reply == K.model(off, val, counts) and state == 3, (off, val, reply)
        if ok:
            assert reply.startswith("200 ") and blk == [BLK + off] and u.u8(BLK + off) == val
        else:
            assert reply == K.S451_VALUE and blk == [] and u.u8(BLK + off) == before, (off, val)
    reply, *_ = _kset(u, 0x900, 300)
    assert reply == K.S451_VALUE                    # value is checked before the window


def test_syntax_501():
    u = _unit()
    for text in ("-KSET 2240\r\n", "-KSET x,1\r\n", "-KSET 22a0,1\r\n", "-KSET 2240,1x\r\n", "-KSET 12345678,1\r\n",
                 "-KSET ,1\r\n", "-KSET 2240,\r\n", "-KGET 2240,-1\r\n", "-KGET a,4\r\n", "-kget 1 2\r\n"):
        reply, n, state, blk = _cmd(u, text)
        assert reply == K.S501 and state == 3 and blk == [], (text, reply)
    reply, *_ = _cmd(u, "-kset 0002240,0000003\r\n")        # lower case verb, leading zeros: echoed as sent
    assert reply == "200 -KSET 0002240=0000003\r\n" and u.u8(BLK + 0x8C0) == 3


def test_kget_lengths_and_data_phase():
    u = _unit()
    pattern = bytes((i * 7 + 3) & 0xFF for i in range(0x1000))
    u.uc.mem_write(BLK, pattern)
    for off, n, ok in ((0, 1, True), (0, 4096, True), (4095, 1, True), (2240, 4, True), (0, 0, False),
                       (0, 4097, False), (4095, 2, False), (4096, 1, False), (9999999, 1, False)):
        u.uc.mem_write(USB, b"\xee" * 16)
        _w32(u, K.HANDLE, 0x1234)
        reply, rn, state, blk = _cmd(u, f"-KGET {off},{n}\r\n")
        assert blk == [] and not [a for a, _ in u.writes if BLK <= a < BLK + 0x1000]
        if not ok:
            assert reply == K.S451_GET and state == 3 and _u32(u, K.HANDLE) == 0x1234, (off, n, reply)
            continue
        assert reply == K.S150 and rn == len(K.S150) and state == 4, (off, n)
        assert u.read(USB + 8, n) == pattern[off:off + n]
        assert _u32(u, K.TOTAL) == _u32(u, K.CHUNK) == n and _u32(u, K.HANDLE) == 0x8001
        assert u.read(_u32(u, K.FINAL), len(K.S250)).decode() == K.S250 and _u32(u, K.CMD_ID) == K.DATA_ID
        # stock state 4, event 0x10 ("reply sent"): one data frame of n bytes from [0x1bd308], t = 1, state 6
        _state_call(u, 0xCB834, 0x10)
        assert _u32(u, REPLY) == USB and _u32(u, LEN) == n and _u32(u, T) == 1 and _u32(u, K.XSTATE) == 6
        # stock state 6, event 0x20 ("data sent"): close 0x8001, reply [0x1bd344] = 250, state 3
        _state_call(u, 0xCB9CC, 0x20)
        assert u.read(_u32(u, REPLY), _u32(u, LEN)).decode() == K.S250 and _u32(u, K.XSTATE) == 3
        assert _u32(u, T) == 0 and ("close", 0x8001) in u.calls


def _state_call(u, fn, event):
    """fn(event, buf, len, &reply, &len, &t): args 5 and 6 on the stack."""
    from koma.unitemu import STACK
    for a in (REPLY, LEN, T):
        _w32(u, a, 0)
    u.calls.clear()
    sp = STACK - 0x100
    u.uc.mem_write(sp, struct.pack(">II", LEN, T))
    from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_SP
    from koma.unitemu import RET
    uc = u.uc
    for r, v in {UC_ARM_REG_R0: event, UC_ARM_REG_R1: 0, UC_ARM_REG_R2: 0, UC_ARM_REG_R3: REPLY}.items():
        uc.reg_write(r, v)
    uc.reg_write(UC_ARM_REG_SP, sp)
    uc.reg_write(UC_ARM_REG_LR, RET | 1)
    uc.reg_write(UC_ARM_REG_CPSR, (uc.reg_read(UC_ARM_REG_CPSR) & ~0xFF) | 0x13 | 0x20 | 0xC0)
    uc.emu_start(fn | 1, RET, count=200000)


def test_apply_calls_reconcile_and_preview_only_while_live_and_for_recipe_bytes():
    u = _unit()
    u.stub(u.res("kf_bwrecipes__on_change"), "on_change")
    u.stub(u.res("kf_preview__pv_apply"), "pv_apply")
    u.uc.mem_write(K.MONO_FLAG, b"\x01")
    for live, off, called in ((0, 0x8C0, False), (1, 0x8B5, False), (1, 0x8BF, False), (1, 0x8C0, True),
                              (1, 0x8CD, True), (1, 0x910, True), (1, 0xA13, True)):
        _idle(u, live=live)
        reply, *_ = _kset(u, off, 1)
        names = [c for c in u.calls if c[0] in ("on_change", "pv_apply")]
        assert reply == f"200 -KSET {off}=1\r\n"
        assert names == ([("on_change", 1), ("pv_apply", 1)] if called else []), (live, off, names)
    u.uc.mem_write(K.MONI_RUN, b"\x01")
    _kset(u, 0x900, 1)                                    # refused: no apply
    assert not [c for c in u.calls if c[0] in ("on_change", "pv_apply")]


def test_apply_real_reconcile_moves_imgset_and_reapplies():
    """Unstubbed bwrecipes sync + preview pv_apply: RECIPE row -> IMG SET base, preview counter, chroma register."""
    from koma.features import bwrecipes as B
    u = _unit()
    _idle(u)
    u.uc.mem_write(K.MONO_FLAG, b"\x00")
    rid = next(i + 1 for i, r in enumerate(B.RECIPES) if r["type"] == B.TYPE_COLOUR)
    pv = u.res("kf_preview__pv")
    n0 = u.u8(pv + P.O_N)
    u.mmio.clear()
    reply, _, state, blk = _kset(u, 0x8C0, rid)
    assert reply == f"200 -KSET 2240={rid}\r\n" and state == 3
    assert u.u8(BLK + 0x8C0) == rid and u.u8(BLK + 0x30) == 1          # colour recipe base: IMG SET NORMAL
    assert u.u8(pv + P.O_N) == n0 + 1 and u.u8(pv + P.O_SITE) == K.SITE_SETTING and u.u8(pv + P.O_TYPE) == 1
    assert any(a == "12018004" for a, _ in u.mmio), u.mmio
    assert all(BLK + 0x800 <= a < BLK + 0xC00 or a == BLK + 0x30 for a in blk), [hex(a) for a in blk]


def test_resident_size():
    # v1.4: +-PEEK handler and name, 13 snapshot words, the ksta_ext word (was 990 / 1050)
    for feats, opts, limit in ((("ksettings",), {"apply": "off"}, 1200), (("bwrecipes", "preview", "ksettings"), {}, 1260)):
        r = link([F.make(n, opts if n == "ksettings" else {}) for n in feats])
        s = r.symbols
        start = s["kf_ksettings__kset_handler"]
        end = min(v for k, v in s.items() if v > s["kf_ksettings__kget_name"] and k.startswith(("kf_end", "kf_start")))
        assert end - start <= limit, end - start


# ---------------------------------------------------------------- v1.1: stock whitelist + 0x553d4
def _idle(u, **kw):
    v = dict(app=2, mode=0, overlay=0, cmd=0, live=1, rl1=0, inj=0)
    v.update(kw)
    u.uc.mem_write(K.KEY_SCAN + K.KEY_RL1, bytes([v["rl1"]]))
    u.uc.mem_write(K.KEY_INJ + K.KEY_RL1, bytes([v["inj"]]))
    _w32(u, K.APP_STATE, v["app"])
    u.uc.mem_write(K.UI_MODE, bytes([v["mode"]]))
    u.uc.mem_write(K.UI_OVERLAY, bytes([v["overlay"]]))
    u.uc.mem_write(K.UI_CMD, bytes([v["cmd"]]))
    u.uc.mem_write(K.MONI_RUN, bytes([v["live"]]))


def test_stock_whitelist_values_setters_and_one_apply():
    u = _unit(("ksettings",), {"apply": "off"})
    u.stub(K.STOCK_APPLY, "apply")
    _idle(u)
    for off, cls, setter, name in K.STOCK:
        for val in list(range(0, 33)) + [255]:
            u.uc.mem_write(BLK + off, b"\xee")
            reply, _, state, blk = _kset(u, off, val)
            ok = val in K.CLASSES[cls]
            assert reply == K.model(off, val, {}) and state == 3, (name, val, reply)
            applies = [c for c in u.calls if c[0] == "apply"]
            if ok:
                assert reply == f"200 -KSET {off}={val}\r\n" and u.u8(BLK + off) == val, (name, val)
                assert blk == [BLK + off] and len(applies) == 1, (name, val, blk, applies)
            else:
                assert reply == K.S451_VALUE and blk == [] and not applies and u.u8(BLK + off) == 0xEE, (name, val)


def test_stock_setter_is_the_writer():
    u = _unit(("ksettings",), {"apply": "off"})
    u.stub(K.STOCK_APPLY, "apply")
    for i, a in enumerate(K.SETTERS):
        u.stub(a, f"setter{i + 1}")
    _idle(u)
    for off, cls, setter, name in K.STOCK:
        val = min(K.CLASSES[cls])
        u.uc.mem_write(BLK + off, b"\xee")
        reply, _, _, blk = _kset(u, off, val)
        names = [c for c in u.calls if c[0] != "close"]
        if setter:
            assert [n for n, _ in names] == [f"setter{setter}", "apply"] and names[0][1] == val, (name, names)
            assert u.u8(BLK + off) == 0xEE and blk == []              # stubbed setter: nothing else wrote it
        else:
            assert [n for n, _ in names] == ["apply"] and blk == [BLK + off], (name, names)


def test_stock_busy_preconditions():
    u = _unit(("ksettings",), {"apply": "off"})
    u.stub(K.STOCK_APPLY, "apply")
    for kw, busy in ((dict(), False), (dict(mode=4), False), (dict(app=9), True), (dict(mode=1), True),
                     (dict(mode=2), True), (dict(overlay=5), True), (dict(overlay=6), True), (dict(overlay=7), True),
                     (dict(cmd=1), True), (dict(live=0), True), (dict(rl1=1), True), (dict(rl1=3), True),
                     (dict(rl1=4), False), (dict(inj=1), True), (dict(inj=3), True)):
        _idle(u, **kw)
        u.uc.mem_write(BLK + 0x53, b"\x0d")
        reply, _, state, blk = _kset(u, 0x53, 15)
        applies = [c for c in u.calls if c[0] == "apply"]
        if busy:
            assert reply == K.S451_BUSY and blk == [] and not applies and u.u8(BLK + 0x53) == 13, kw
        else:
            assert reply == "200 -KSET 83=15\r\n" and u.u8(BLK + 0x53) == 15 and len(applies) == 1, kw
    _idle(u, overlay=5)
    reply, *_ = _kset(u, 0x53, 14)
    assert reply == K.S451_VALUE                        # the value is checked before the preconditions


def test_unlisted_stock_bytes_stay_refused():
    u = _unit(("ksettings",), {"apply": "off"})
    _idle(u)
    for off in (0x058, 0x06F, 0x1A5, 0x068, 0x069, 0x0FC, 0x0D4, 0x052, 0x05C, 0x197, 0x1A7, 0x038, 0x0FF):
        reply, _, _, blk = _kset(u, off, 1)
        assert reply == K.S451_RANGE and blk == [], hex(off)


def test_recipe_base_change_calls_stock_apply_once():
    from koma.features import bwrecipes as B
    u = _unit()
    u.stub(K.STOCK_APPLY, "apply")
    _idle(u)
    u.uc.mem_write(K.MONO_FLAG, b"\x00")
    rid_c = next(i + 1 for i, r in enumerate(B.RECIPES) if r["type"] == B.TYPE_COLOUR)
    rid_c2 = [i + 1 for i, r in enumerate(B.RECIPES) if r["type"] & 3 == B.TYPE_COLOUR][1]
    rid_b = next(i + 1 for i, r in enumerate(B.RECIPES) if r["type"] == B.TYPE_BW and B.imgset_of(r["params"]) == 3)
    u.uc.mem_write(BLK + 0x30, b"\x01")
    for rid, base, n in ((rid_c, 1, 0), (rid_c2, 1, 0), (rid_b, 3, 1), (rid_c, 1, 1)):
        reply, *_ = _kset(u, 0x8C0, rid)
        assert reply.startswith("200 ") and u.u8(BLK + 0x30) == base
        assert len([c for c in u.calls if c[0] == "apply"]) == n, (rid, u.calls)
    for kw in (dict(rl1=1), dict(inj=1), dict(cmd=1)):    # not idle (row 0x8c0 is open outside menu / ADJ): byte
        _idle(u, **kw)                                    # written, 200, no reconcile, no stock apply
        reply, *_ = _kset(u, 0x8C0, rid_b)
        assert reply == f"200 -KSET 2240={rid_b}\r\n" and u.u8(BLK + 0x8C0) == rid_b, kw
        assert u.u8(BLK + 0x30) == 1 and not [c for c in u.calls if c[0] == "apply"], kw
        u.uc.mem_write(BLK + 0x8C0, bytes([rid_c]))


def test_adj_open_record_write_no_live_apply():
    """v1.2 fix 1: with ADJ open (overlay 6) a recipe-record write lands, replies 200 and runs no apply at all."""
    u = _unit()
    for n in ("on_change", "pv_apply"):
        u.stub(u.res(f"kf_{'bwrecipes' if n == 'on_change' else 'preview'}__{n}"), n)
    u.stub(K.STOCK_APPLY, "apply")
    for overlay in (5, 6, 7):
        _idle(u, overlay=overlay)
        for off in (0x910, 0xA00):
            reply, _, state, blk = _kset(u, off, 7)
            assert reply == f"200 -KSET {off}=7\r\n" and state == 3 and blk == [BLK + off], (overlay, off)
            assert u.u8(BLK + off) == 7
            assert not [c for c in u.calls if c[0] in ("on_change", "pv_apply", "apply")], (overlay, off, u.calls)
    _idle(u)                                                # idle again: the apply runs
    _kset(u, 0x910, 8)
    assert [c[0] for c in u.calls if c[0] in ("on_change", "pv_apply")] == ["on_change", "pv_apply"]


def test_koma_rows_busy_while_menu_or_adj_open():
    """v1.2 fix 3: rows 0x8b5..0x8cf -> 451 busy with overlay 5 / 6 (the close commit would revert them); records and
    the FX table stay writable; the option count is still checked first."""
    u = _unit()
    u.stub(u.res("kf_bwrecipes__on_change"), "on_change")
    u.stub(u.res("kf_preview__pv_apply"), "pv_apply")
    counts = {0x8C0: 21}
    for overlay in (0, 5, 6, 7):
        _idle(u, overlay=overlay)
        for off in (0x8B5, 0x8C0, 0x8CF):
            u.uc.mem_write(BLK + off, b"\x00")
            reply, _, _, blk = _kset(u, off, 2)
            busy = overlay in (5, 6)
            assert reply == K.model(off, 2, counts, overlay=overlay), (overlay, off, reply)
            if busy:
                assert reply == K.S451_BUSY and blk == [] and u.u8(BLK + off) == 0, (overlay, off)
                assert not [c for c in u.calls if c[0] in ("on_change", "pv_apply")]
            else:
                assert reply == f"200 -KSET {off}=2\r\n" and u.u8(BLK + off) == 2, (overlay, off)
        for off in (0x910, 0x9FF, 0xA00, 0xA13):
            reply, _, _, blk = _kset(u, off, 2)
            assert reply == f"200 -KSET {off}=2\r\n" and blk == [BLK + off], (overlay, off)
        reply, *_ = _kset(u, 0x8C0, 21)
        assert reply == K.S451_VALUE                          # value before busy


# ---------------------------------------------------------------- v1.3: -KSTA read-only state snapshot
def _ksta_seed(u, app, run):
    """Distinct values at every KSTA address (bytes / halfwords / words), app state and running state as given."""
    vals = {}
    for i, (name, a, w) in enumerate(K.KSTA_WORDS):
        v = {4: 0x11220000 + i * 0x101, 2: 0x3300 + i, 1: 0x40 + i}[w]
        u.uc.mem_write(a, v.to_bytes(w, "big"))
        vals[name] = v
    for name, a, v in (("app_state", K.APP_STATE, app), ("run_state", 0x23A874, run)):
        _w32(u, a, v)
        vals[name] = v
    return vals


def test_ksta_layout():
    assert K.KSTA_N == len(K.KSTA_WORDS) + 1 and K.KSTA_BYTES == 4 * K.KSTA_N <= 255
    assert K.KSTA_VERSION >> 16 == 0x4B53 and K.KSTA_VERSION & 0xFF == K.KSTA_N
    names = [n for n, _, _ in K.KSTA_WORDS]
    assert len(set(names)) == len(names)
    for _, a, w in K.KSTA_WORDS:
        assert w in (1, 2, 4) and a % w == 0 and a < 1 << 28
    assert [a for _, a, _ in K.KSTA_WORDS[:11]] == [0x23A86C, 0x23A870, 0x23A874, 0x23A884, 0x23A888, 0x23A88C,
                                                     0x23A890, 0x23A89C, 0x1BD1B8, 0x1BD1C0, 0x1FB9BC]
    by = {a: w for _, a, w in K.KSTA_WORDS}
    assert by[0x1F502F] == 1 and by[0x2B9E50] == by[0x2B9E54] == by[0x2B9E58] == 4
    # the end path's sources (0x29b28 entry flag; 0x29c1c copies 0x6eb7c / 0x6eb94 / 0x6ebac)
    assert fw.ram(0x29B2C, 6) == bytes.fromhex("487f2601 6006")
    assert fw.ram(0x29C1C, 0x10) == bytes.fromhex("f044ffae62f8f044ffb77220f044ffc0")
    for getter, lit in ((0x6EB7C, 0x1BEF70), (0x6EB94, 0x1BEFB0), (0x6EBAC, 0x1BEFB0)):
        assert fw.ram(getter + 8, 4) == lit.to_bytes(4, "big"), hex(getter)


def test_ksta_any_state_plain_loads_and_data_phase():
    u = _unit(("ksettings",), {"apply": "off"})
    for app, run in ((9, 9), (2, 2), (2, 9), (0, 0), (6, 9)):
        vals = _ksta_seed(u, app, run)
        u.uc.mem_write(USB, b"\xee" * (K.KSTA_BYTES + 16))
        _w32(u, K.HANDLE, 0x1234)
        reply, rn, state, blk = _cmd(u, "-KSTA\r\n")
        assert reply == K.S150 and rn == len(K.S150) and state == 4 and blk == [], (app, run, reply)
        assert not u.calls, u.calls                                   # no call at all: nothing can block
        assert not [a for a, _ in u.writes if any(x <= a < x + w for _, x, w in K.KSTA_WORDS)]   # read-only
        data = u.read(USB + 8, K.KSTA_BYTES)
        assert u.read(USB + 8 + K.KSTA_BYTES, 4) == b"\xee" * 4
        d = K.ksta_decode(data)
        assert d.pop("version") == K.KSTA_VERSION and d == vals, (app, run)
        assert _u32(u, K.TOTAL) == _u32(u, K.CHUNK) == K.KSTA_BYTES and _u32(u, K.HANDLE) == 0x8001
        assert u.read(_u32(u, K.FINAL), len(K.S250)).decode() == K.S250 and _u32(u, K.CMD_ID) == K.DATA_ID
        _state_call(u, 0xCB834, 0x10)
        assert _u32(u, REPLY) == USB and _u32(u, LEN) == K.KSTA_BYTES and _u32(u, K.XSTATE) == 6
        _state_call(u, 0xCB9CC, 0x20)
        assert u.read(_u32(u, REPLY), _u32(u, LEN)).decode() == K.S250 and _u32(u, K.XSTATE) == 3
    reply, *_ = _cmd(u, "-ksta\r\n")                                 # the parser upper-cases verbs
    assert reply == K.S150


def test_ksta_decode_refuses_foreign_frames():
    import pytest
    good = struct.pack(f">{K.KSTA_N}I", K.KSTA_VERSION, *range(K.KSTA_N - 1))
    assert K.ksta_decode(good)["tick"] == [n for n, _, _ in K.KSTA_WORDS].index("tick")
    for bad in (good[:-4], good[:-1], b"", struct.pack(">I", K.KSTA_VERSION ^ 0x100) + good[4:]):
        with pytest.raises(ValueError):
            K.ksta_decode(bad)


def test_ksta_bridge_table_matches():
    import sys
    from pathlib import Path
    bridge = Path(__file__).resolve().parents[2] / "komaset" / "bridge"
    sys.path.insert(0, str(bridge))
    import komabridge as KB
    assert KB.KSTA_WORDS == [(n, a, w) for n, a, w in K.KSTA_WORDS] and KB.KSTA_LAYOUT == K.KSTA_LAYOUT
    assert KB.KSTA_WORDS_V1 == K.KSTA_WORDS_V1 and KB.KSTA_V1_VERSION == 0x4B530100 | len(K.KSTA_WORDS_V1) + 1
    assert (KB.PEEK_MAX, KB.PEEK_LO, KB.PEEK_END) == (K.PEEK_MAX, K.PEEK_LO, K.PEEK_END) == (256, 0x1A8000, 0x3E5000)


def test_ksta_decode_layout1_still_decodes():
    v1 = struct.pack(f">{len(K.KSTA_WORDS_V1) + 1}I", 0x4B530100 | len(K.KSTA_WORDS_V1) + 1,
                     *range(len(K.KSTA_WORDS_V1)))
    d = K.ksta_decode(v1)
    assert d["tick"] == len(K.KSTA_WORDS_V1) - 1 and "hs_1bec44" not in d and "ext" not in d
    assert K.ksta_decode(struct.pack(f">{K.KSTA_N}I", K.KSTA_VERSION, *range(K.KSTA_N - 1)) + bytes(8))["ext"] == [0, 0]


# ---------------------------------------------------------------- v1.4: -PEEK read-only RAM peek
def test_kpeek_reads_ram_and_refuses_the_rest():
    u = _unit(("ksettings",), {"apply": "off"})
    pat = bytes(range(256))
    for addr in (0x1A8000, 0x1A8A00, 0x3E4F00):         # first and last 256 B of [0x1a8000, 0x3e5000)
        u.uc.mem_write(addr, pat)
        for text, n in ((f"-PEEK {addr:x},256\r\n", 256), (f"-PEEK {addr:X},1\r\n", 1),
                        (f"-peek {addr + 5:x},17\r\n", 17)):
            u.uc.mem_write(USB, b"\xee" * 300)
            _w32(u, K.HANDLE, 0x1234)
            reply, rn, state, blk = _cmd(u, text)
            assert reply == K.S150 and state == 4 and blk == [], (text, reply)
            off = 5 if text.startswith("-peek") else 0
            assert u.read(USB + 8, n) == pat[off:off + n] and u.read(USB + 8 + n, 4) == b"\xee" * 4, text
            assert _u32(u, K.TOTAL) == _u32(u, K.CHUNK) == n and _u32(u, K.CMD_ID) == K.DATA_ID
            assert not [a for a, _ in u.writes if addr <= a < addr + 256], text           # read-only
            _state_call(u, 0xCB834, 0x10)
            assert _u32(u, LEN) == n and _u32(u, K.XSTATE) == 6
    for arg in ("3e4fff,2", "3e5000,1", "3fff00,1", "400000,1", "1a7fff,1", "1a7f01,256", "1b000,4", "0,1",
                "3e4f00,257", "0,0", "1a8000,999999"):
        reply, rn, state, blk = _cmd(u, f"-PEEK {arg}\r\n")
        assert reply == K.S501_PEEK and state == 3, (arg, reply)
    for arg in ("1g000,4", "1b000,4x", "0x10,4", "1b000,-1", ",4"):
        reply, *_ = _cmd(u, f"-PEEK {arg}\r\n")
        assert reply is not None and reply.startswith("501"), (arg, reply)
    reply, *_ = _cmd(u, "-PEEK 1a8000,16\r\n")
    assert reply == K.S150
