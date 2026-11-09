"""stallfix: each fix runs the real 2.40 code around its site (unit emulation, callees stubbed) with the flag byte
0x1b8be in every relevant state: on, off, invalid (default), USB attached or not."""
from __future__ import annotations

import struct

from _util import fw
from koma.features import stallfix as SF
from koma.features import tether
from koma.linker import link, phase_a_lines
from koma.unitemu import RET, STACK, Unit
from unicorn.arm_const import UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_SP

A888, A88C = 0x23A888, 0x23A88C
SUB, RETRACT_FLAG = 0x1BEF5C, 0x1BEF60
BUF = 0x2C50D0
MODE, SEM, D54 = 0x1A8D40, 0x1A8D14, 0x1A8D54
H = 0x002C1000                       # a fake semaphore handle
FLAG, STATE, INIT, INJECT = 0x1BD1B8, 0x1BD1C0, 0x1BD1BC, 0x2B9D34

_R = link([tether.make({}), SF.make({})])


def _w32(u, a, v):
    u.uc.mem_write(a, struct.pack(">I", v))


def _r32(u, a):
    return struct.unpack(">I", u.read(a, 4))[0]


def _unit():
    u = Unit(_R.module)
    assert u.install() == 0 and u.callee_saved_ok
    return u


def _flags(u, v):
    u.uc.mem_write(SF.FLAGS, bytes([v]))


def test_before_bytes_checks_and_records():
    f = SF.make({})
    assert fw.ram(0x6D778, 4).hex() == "f7d7fe68"
    assert fw.ram(0x284E6, 4).hex() == "f000fd0f"
    assert fw.ram(0x9765E, 4).hex() == "f78bff8b"
    for c in f.checks:
        assert fw.ram(c.addr, len(c.before)) == c.before
    assert [r.site for r in f.redirects] == [0x6D778, 0x284E6, 0x9765E]
    assert not f.patches and not f.hooks and not f.settings and not f.rows and f.requires == ["tether"]


def test_sites_not_in_phase_a():
    lines = phase_a_lines()
    for site in (0x6D778, 0x284E6, 0x9765E):
        assert not [x for x in lines if x <= site < x + 32 or x <= site + 3 < x + 32], hex(site)


def test_flag_encoding():
    assert SF.flag_value() == 0xAD == 173
    assert SF.flag_value(False, False, False, False) == 0xA0
    assert SF.effective_bits(0xFF) == SF.effective_bits(0x00) == SF.effective_bits(0x0F) == 0b1101
    assert SF.effective_bits(0xA0) == 0 and SF.effective_bits(0xAF) == 0xF and SF.effective_bits(0xA2) == 2
    assert SF.FLAGS_OFF == 0x8BE == 2238


def test_flag_byte_is_free_and_kset_writable():
    from koma.features import ksettings
    from koma import features as KF
    rows = [r for n in KF.SETS["koma04"] for r in (getattr(KF.module(n), "ROWS", None) or [])]
    assert SF.FLAGS not in {r["byte"] for r in rows}
    cnt = ksettings.count_table(rows)
    lo = ksettings.WINDOW[0][0]
    assert lo <= SF.FLAGS_OFF < ksettings.WINDOW[0][1] and cnt[SF.FLAGS_OFF - lo] == 0     # any 0..255
    assert SF.FLAGS_OFF < ksettings.APPLY_FROM                                              # no recipe reconcile


def test_install_writes_only_the_sites():
    u = _unit()
    out = [a for a, _ in u.outside_writes() if not 0x58190 <= a < 0x58198]
    sites = (0x4FE44, 0xCD090, 0xCD03A, 0xCD05E, 0xD27F2, 0x6D778, 0x284E6, 0x9765E)
    assert all(any(s <= a < s + 4 for s in sites) for a in out), [hex(a) for a in out]
    for s in (0x6D778, 0x284E6, 0x9765E):
        assert u.read(s, 4) != fw.ram(s, 4)
    # fix 4 registered with tether
    hook = u.res_base + _R.symbols["kf_tether__arm_hook"] - _R.symbols["koma_res"]
    arm = u.res_base + _R.symbols["kf_stallfix__sf_arm"] - _R.symbols["koma_res"]
    assert _r32(u, hook) == arm | 1


# ---- fix 1: run 0x6d6f0 (sub-state 0 handler) with message 0xa201
def _a201(u, *, flags, usb=1, service=0, retract=1):
    _flags(u, flags)
    _w32(u, A888, usb)
    _w32(u, A88C, service)
    _w32(u, SUB, 0)
    _w32(u, RETRACT_FLAG, retract)
    u.uc.mem_write(BUF + 6, b"\xa2\x01")
    for a, n in ((0x4544C, "idle_4544c"), (0x3DA80, "panel_3da80"), (0xC8328, "c8328"), (0x29FBE, "retract_29fbe")):
        u.stub(a, n, r0=0)
    regs = {UC_ARM_REG_R4: 0xA4, UC_ARM_REG_R5: 0xA5, UC_ARM_REG_R6: 0xA6, UC_ARM_REG_R7: 0xA7}
    u.calls.clear()
    u.thumb(0x6D6F0, regs)
    assert {k: u.uc.reg_read(k) for k in regs} == regs and u.uc.reg_read(UC_ARM_REG_SP) == STACK
    return [c for c in u.calls if c[0] in ("idle_4544c", "panel_3da80", "c8328", "retract_29fbe")], _r32(u, SUB), \
        _r32(u, RETRACT_FLAG)


STOCK_A201 = [("idle_4544c",), ("panel_3da80", 2), ("c8328",), ("retract_29fbe",)]


def _names(calls):
    return [(n,) if n != "panel_3da80" else (n, a) for n, a in calls]


def test_fix1_a201_noop_while_usb_attached():
    u = _unit()
    for flags in (0xAD, 0xFF, 0xA1):                         # default (explicit and invalid byte), fix 1 alone
        calls, sub, rf = _a201(u, flags=flags)
        assert calls == [] and sub == 0 and rf == 1, flags


def test_fix1_stock_when_off_or_no_usb_or_service():
    u = _unit()
    for kw in (dict(flags=0xA0), dict(flags=0xAC), dict(flags=0xAD, usb=0), dict(flags=0xAD, service=1)):
        calls, sub, rf = _a201(u, **kw)
        assert _names(calls) == STOCK_A201 and sub == 1 and rf == 2, kw
    stock = Unit(_R.module)                                  # not installed: same calls
    calls, sub, rf = _a201(stock, flags=0xAD)
    assert _names(calls) == STOCK_A201 and sub == 1 and rf == 2


# ---- fix 2: 0x284d8..0x284ee in 0x28378 (lens out -> arm timer 2)
def _timer(u, *, flags, usb=1, service=0):
    _flags(u, flags)
    _w32(u, A888, usb)
    _w32(u, A88C, service)
    u.stub(0x685F0, "lens_out", r0=1)
    u.stub(0x28F08, "timer_28f08", r0=0x55)
    u.stub(0x3EABC, "f3eabc", r0=0)
    u.run_site(0x284D8, 0x284EE)
    return [c for c in u.calls if c[0] == "timer_28f08"]


def test_fix2_skips_the_retract_timer_only_when_on_and_attached():
    u = _unit()
    assert _timer(u, flags=0xAF) == []
    assert _timer(u, flags=0xA2) == []
    for kw in (dict(flags=0xAD), dict(flags=0xFF), dict(flags=0xAF, usb=0), dict(flags=0xAF, service=1)):
        assert _timer(u, **kw) == [("timer_28f08", 2)], kw


# ---- fix 3: the capture-path call at 0x9765e
def _cap(u, *, flags, mode, take_ok=True):
    _flags(u, flags)
    _w32(u, SEM, H)
    _w32(u, MODE, mode)
    _w32(u, D54, 0x77)
    for a, n, r in ((0x5DB18, "tmr_stop", 0), (0x21860, "f21860", 0), (0x2291C, "try_take", H if take_ok else 0),
                    (0x2302E, "async_2302e", 0), (0x2293C, "release_2293c", 0), (0x23578, "stock_23578", 0)):
        u.stub(a, n, r0=r)
    u.run_site(0x9765E, 0x97662)
    return [c for c in u.calls], _r32(u, D54)


def test_fix3_releases_on_the_leak_path_only():
    u = _unit()
    for mode in (0, 1, 5):
        calls, d54 = _cap(u, flags=0xAD, mode=mode)
        assert [n for n, _ in calls] == ["tmr_stop", "f21860", "try_take", "release_2293c"], (mode, calls)
        assert calls[0][1] == _r32(u, 0x1A8D20) and calls[3][1] == H and d54 == 0
    for mode in (2, 3, 4):                                   # async completion releases: stock behaviour kept
        calls, d54 = _cap(u, flags=0xAD, mode=mode)
        assert [n for n, _ in calls] == ["tmr_stop", "f21860", "try_take", "async_2302e"] and calls[3][1] == mode
        assert d54 == 0
    calls, d54 = _cap(u, flags=0xAD, mode=0, take_ok=False)  # failed take: nothing to release
    assert [n for n, _ in calls] == ["tmr_stop", "f21860", "try_take"] and d54 == 0


def test_fix3_off_runs_stock_23578():
    u = _unit()
    calls, d54 = _cap(u, flags=0xA0, mode=0)
    assert [n for n, _ in calls] == ["stock_23578"] and d54 == 0x77


def test_fix3_matches_stock_sequence_when_the_panel_is_on():
    """Unstubbed stock 0x23578 vs the fix with the same callees stubbed: same calls, same order, same args (mode 3)."""
    stock = Unit(_R.module)
    _w32(stock, SEM, H)
    _w32(stock, MODE, 3)
    for a, n, r in ((0x5DB18, "tmr_stop", 0), (0x21860, "f21860", 0), (0x2291C, "try_take", H),
                    (0x2302E, "async_2302e", 0), (0x2293C, "release_2293c", 0)):
        stock.stub(a, n, r0=r)
    stock.run_site(0x9765E, 0x97662)
    u = _unit()
    calls, _ = _cap(u, flags=0xAD, mode=3)
    assert stock.calls == calls


# ---- fix 4: tether_force divergence calls the arm hook
def _diverge(u, *, flags):
    _flags(u, flags)
    _w32(u, INJECT, 1)
    _w32(u, FLAG, 1)
    _w32(u, INIT, 1)
    _w32(u, STATE, 9)
    _w32(u, A888, 1)
    _w32(u, A88C, 0)
    u.stub(0x324B8, "dial_324b8", r0=4)
    u.stub(0x66B90, "f66b90", r0=1)
    u.stub(0x290DE, "apo_290de", r0=0)
    u.stub(0x3DA80, "panel_3da80", r0=0)
    u.stub(0x3DB8C, "backlight_3db8c", r0=0)
    u.run_site(0x4FD1E, RET)
    return [c for c in u.calls if c[0] in ("panel_3da80", "backlight_3db8c")], _r32(u, STATE)


def test_fix4_panel_on_at_divergence():
    u = _unit()
    assert _diverge(u, flags=0xAD) == ([("panel_3da80", 1), ("backlight_3db8c", 1)], 2)
    assert _diverge(u, flags=0xA5) == ([], 2)


def test_tether_without_stallfix_has_no_arm_call():
    r = link([tether.make({})])
    u = Unit(r.module)
    assert u.install() == 0
    assert _diverge(u, flags=0xAD) == ([], 2)
