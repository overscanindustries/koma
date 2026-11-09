"""tether: the 0x4fe44 redirect leaves 0x4fd1e stock unless USB is attached AND the host sent -CCTL remtin; then
state 9 re-resolves from the (virtual) dial once. v1.2: an unplug with the remtin flag still set clears the remote state
and re-resolves from the physical dial; the 0xcd090 remtout redirect clears injection and posts one dial event (0,4)
when the running state is not 9. Runs the real resolver 0x4fd1e on the 2.40 payload (unit emulation), with the dial
reader 0x324b8 and 0x66b90 stubbed."""
from __future__ import annotations

import struct

from _util import fw
from koma.features import tether
from koma.linker import link, phase_a_lines
from koma.unitemu import RET, STACK, Unit
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                               UC_ARM_REG_SP)

FLAG, INIT, STATE = 0x1BD1B8, 0x1BD1BC, 0x1BD1C0
CCTL_STATE, RUNNING, KEYS = 0x1BD2EC, 0x23A874, 0x2B9DD8
A888, A88C, INJECT = 0x23A888, 0x23A88C, 0x2B9D34
DIAL_STILL = 4                      # 0x324b8 code 4 -> state 2 (0x4fd5a / 0x4fdce)


_R = link([tether.make({})])
APO = 0x290DE


def _latch_addr(u):
    return u.resident[0] + _R.symbols["kf_tether__latch"] - _R.symbols["koma_res"]


def _latch(u, v=None):
    """Read (v None) or set the once-per-session Auto Power Off latch byte in the resident block."""
    if v is None:
        return u.read(_latch_addr(u), 1)[0]
    u.uc.mem_write(_latch_addr(u), bytes([v]))


def _apo(u):
    """0x290de calls made since the last u.calls.clear(), as the mode argument."""
    return [a for n, a in u.calls if n == "apo_290de"]


def _w32(u, a, v):
    u.uc.mem_write(a, struct.pack(">I", v))


def _resolve(u, *, flag, state, usb=1, service=0, dial=DIAL_STILL, inject=1):
    _w32(u, INJECT, inject)
    _w32(u, FLAG, flag)
    _w32(u, INIT, 1)
    _w32(u, STATE, state)
    _w32(u, A888, usb)
    _w32(u, A88C, service)
    u.stub(0x324B8, "dial_324b8", r0=dial)
    u.stub(0x66B90, "f66b90", r0=1)     # non-zero: a state outside the re-derive list is kept as is (0x4fe02)
    u.stub(APO, "apo_290de", r0=0)
    u.watch = {0x4FD1E}
    u.watch_hits = []
    r0 = u.run_site(0x4FD1E, RET)
    return r0, struct.unpack(">I", u.read(STATE, 4))[0], len(u.watch_hits)


def _units():
    stock = Unit(link([tether.make({})]).module)            # not installed: the stock 2.40 code
    mod = Unit(link([tether.make({})]).module)
    assert mod.install() == 0 and mod.callee_saved_ok
    return stock, mod


def test_before_bytes_and_records():
    f = tether.make({})
    assert fw.ram(0x4FE44, 4) == tether.SITE_BEFORE
    for c in f.checks:
        assert fw.ram(c.addr, len(c.before)) == c.before
    assert not f.patches and not f.hooks and not f.settings and not f.rows and len(f.redirects) == 5


def test_site_not_in_phase_a():
    lines = sorted(phase_a_lines())
    assert not [x for x in lines if x < 0x4FE48 and x + 32 > 0x4FE44]


def test_install_writes_only_the_redirect():
    stock, u = _units()
    assert u.read(0x4FE48, 0x28) == fw.ram(0x4FE48, 0x28)           # stock tail untouched (the whole Check range)
    assert [c for c in tether.make({}).checks if c.addr == 0x4FE48][0].before == u.read(0x4FE48, 0x28)
    assert u.read(0x4FE44, 4) != tether.SITE_BEFORE
    out = [a for a, _ in u.outside_writes() if not 0x58190 <= a < 0x58198]
    assert all(0x4FE44 <= a < 0x4FE48 or 0xCD090 <= a < 0xCD094 or 0xCD03A <= a < 0xCD03E or 0xCD05E <= a < 0xCD062
               or 0xD27F2 <= a < 0xD27F6
               for a in out), [hex(a) for a in out]
    assert u.read(0xCD05E, 4) != tether.DIAL_BEFORE and u.read(0xCD062, 0x10) == fw.ram(0xCD062, 0x10)
    assert u.read(0xCD090, 4) != tether.OUT_BEFORE and u.read(0xCD03A, 4) != tether.KEY_BEFORE and u.read(0xCD094, 6) == fw.ram(0xCD094, 6)


def test_no_remtin_is_stock():
    """USB attached, no remtin: both force state 9 (the hardware result: keyset does nothing)."""
    stock, u = _units()
    for state in (3, 2, 9):
        assert _resolve(stock, flag=0, state=state)[:2] == (9, 9)
        assert _resolve(u, flag=0, state=state)[:2] == (9, 9)


def test_no_usb_is_stock_even_with_flag():
    """No USB: the state is stock's (with the flag set this is the unplug path, which re-resolves to the same dial)."""
    stock, u = _units()
    for flag in (0, 1):
        assert _resolve(stock, flag=flag, state=3, usb=0)[:2] == (2, 2)
        assert _resolve(u, flag=flag, state=3, usb=0)[:2] == (2, 2)
        # service boot (0x23a88c = 1): never forced, stock and tether agree
        assert _resolve(stock, flag=flag, state=3, service=1)[:2] == _resolve(u, flag=flag, state=3, service=1)[:2]


def test_remtin_state9_reresolves_from_dial_once():
    stock, u = _units()
    assert _resolve(stock, flag=1, state=9) == (9, 9, 1)          # stock: Ricoh's remote branch is defeated
    r0, st, entries = _resolve(u, flag=1, state=9)
    assert (r0, st) == (2, 2) and entries == 2                      # one nested 0x4fd1e
    assert ("dial_324b8", 2) in [(n, 2) for n, _ in u.calls]


def test_remtin_other_state_follows_dial_without_force():
    _, u = _units()
    assert _resolve(u, flag=1, state=2, dial=1)[:3] == (3, 3, 1)    # dial code 1 -> 3 (movie), no recursion
    assert _resolve(u, flag=1, state=3)[:3] == (2, 2, 1)


def test_remtout_restores_force():
    """remtout = flag 0: the next resolve forces 9 again."""
    _, u = _units()
    assert _resolve(u, flag=1, state=9)[:2] == (2, 2)
    assert _resolve(u, flag=0, state=2)[:2] == (9, 9)


def test_injection_cleared_is_stock():
    """keyset,0,2 clears injection before remtout: the resolve its dial event triggers forces 9 again."""
    stock, u = _units()
    for state in (2, 3, 9):
        assert _resolve(u, flag=1, state=state, inject=0)[:2] == (9, 9)
        assert _resolve(stock, flag=1, state=state, inject=0)[:2] == (9, 9)


def test_handler_preserves_r4_to_r7_across_a_resolve():
    """The handler itself (called as 0x4fe44's bl would) on the nested-resolve path: r4..r7 and sp come back intact."""
    r = link([tether.make({})])
    u = Unit(r.module)
    assert u.install() == 0 and u.callee_saved_ok
    handler = u.resident[0] + r.symbols["kf_tether__tether_force"] - r.symbols["koma_res"]
    for flag, state, inject, want_r1, want_state, nested in ((1, 9, 1, 0, 2, 1), (1, 3, 1, 0, 2, 0), (0, 9, 1, 1, 9, 0),
                                                            (1, 9, 0, 1, 9, 0)):
        _w32(u, INJECT, inject)
        _w32(u, FLAG, flag)
        _w32(u, INIT, 1)
        _w32(u, STATE, state)
        _w32(u, A888, 1)
        _w32(u, A88C, 0)
        u.stub(0x324B8, "dial_324b8", r0=DIAL_STILL)
        u.stub(0x66B90, "f66b90", r0=1)
        u.stub(APO, "apo_290de", r0=0)
        u.watch, u.watch_hits = {0x4FD1E}, []
        regs = {UC_ARM_REG_R4: FLAG, UC_ARM_REG_R5: 0xA5A5A5A5, UC_ARM_REG_R6: 0xA6A6A6A6, UC_ARM_REG_R7: 0xA7A7A7A7}
        u.thumb(handler, regs)
        got = {k: u.uc.reg_read(k) for k in regs}
        assert got == regs and u.uc.reg_read(UC_ARM_REG_SP) == STACK, (flag, state, inject, got)
        assert u.uc.reg_read(UC_ARM_REG_R0) == 0x23A864 and u.uc.reg_read(UC_ARM_REG_R1) == want_r1
        st = struct.unpack(">I", u.read(STATE, 4))[0]
        assert len(u.watch_hits) == nested and (st == want_state if nested else st == state), (flag, state, inject, st)


def _handler(r, u, name):
    return u.resident[0] + r.symbols[f"kf_tether__{name}"] - r.symbols["koma_res"]


def _dirty(u):
    """The remote state a tethered session leaves: flag, -CCTL state 2, injection on, virtual dial STILL, RL1 held."""
    _w32(u, FLAG, 1)
    _w32(u, CCTL_STATE, 2)
    _w32(u, INJECT, 1)
    u.uc.mem_write(INJECT + 5, bytes([4, 4]))
    u.uc.mem_write(KEYS, bytes(42))
    u.uc.mem_write(KEYS + 26, b"\x01")


def _clean(u):
    return (struct.unpack(">I", u.read(FLAG, 4))[0], struct.unpack(">I", u.read(CCTL_STATE, 4))[0],
            struct.unpack(">I", u.read(INJECT, 4))[0], u.read(INJECT + 5, 2), u.read(KEYS, 42)) == \
        (0, 0, 0, b"\x09\x09", bytes(42))


def test_unplug_mid_tether_clears_and_reresolves_from_the_dial():
    """(8,0) set 0x23a888 = 0 while remtin + injection were on: the resolve clears the remote state and re-derives
    the state from the (physical) dial through one nested 0x4fd1e; stock keeps the virtual dial and the held keys."""
    stock, u = _units()
    _dirty(stock)
    _w32(stock, INIT, 1); _w32(stock, STATE, 2); _w32(stock, A888, 0); _w32(stock, A88C, 0)
    stock.stub(0x324B8, "dial_324b8", r0=1); stock.stub(0x66B90, "f66b90", r0=1); stock.stub(APO, "apo_290de", r0=0)
    stock.run_site(0x4FD1E, RET)
    assert not _clean(stock)                                       # stock leaves it all behind
    _dirty(u)
    _w32(u, INIT, 1); _w32(u, STATE, 2); _w32(u, A888, 0); _w32(u, A88C, 0)
    u.stub(0x324B8, "dial_324b8", r0=1)                            # the physical dial: movie (code 1 -> state 3)
    u.stub(0x66B90, "f66b90", r0=1)
    u.stub(APO, "apo_290de", r0=0)
    u.watch, u.watch_hits = {0x4FD1E}, []
    r0 = u.run_site(0x4FD1E, RET)
    assert _clean(u) and (r0, struct.unpack(">I", u.read(STATE, 4))[0]) == (3, 3) and len(u.watch_hits) == 2


def test_remtout_redirect_clears_and_posts_one_dial_event():
    """tether_out (the 0xcd090 call) returns 0 like 0x4fc84, clears the remote state, and posts (0,4) through 0x517dc
    only while USB is attached and the running state is not 9. r4..r7 and sp survive."""
    r = link([tether.make({})])
    u = Unit(r.module)
    assert u.install() == 0 and u.callee_saved_ok
    h = _handler(r, u, "tether_out")
    for usb, running, posted in ((1, 2, True), (1, 9, False), (0, 2, False)):
        _dirty(u)
        _w32(u, A888, usb)
        _w32(u, RUNNING, running)
        u.stub(0x517DC, "post_517dc", r0=0)
        u.stub(APO, "apo_290de", r0=0)
        _latch(u, 0)
        u.calls.clear()
        regs = {UC_ARM_REG_R4: 0xA4A4A4A4, UC_ARM_REG_R5: 0xA5A5A5A5, UC_ARM_REG_R6: 0x1BD2EC, UC_ARM_REG_R7: 0xA7A7A7A7}
        assert u.thumb(h, regs) == 0
        assert {k: u.uc.reg_read(k) for k in regs} == regs and u.uc.reg_read(UC_ARM_REG_SP) == STACK
        assert _clean(u), (usb, running)
        assert [c for c in u.calls if c[0] == "post_517dc"] == ([("post_517dc", 0)] if posted else []), u.calls


def _key(u, mg1=0):
    """Run tether_key as the 0xcd03a call does (mg1_keyset 0x65cfc stubbed: returns `mg1`); return (r0, calls)."""
    h = _handler(_R, u, "tether_key")
    u.stub(APO, "apo_290de", r0=0)
    u.stub(0x65CFC, "mg1_keyset", r0=mg1)
    u.calls.clear()
    regs = {UC_ARM_REG_R4: 0xA4A4A4A4, UC_ARM_REG_R5: 0xA5A5A5A5, UC_ARM_REG_R6: 0xA6A6A6A6, UC_ARM_REG_R7: 0xA7A7A7A7}
    r0 = u.thumb(h, regs)
    assert {k: u.uc.reg_read(k) for k in regs} == regs and u.uc.reg_read(UC_ARM_REG_SP) == STACK
    return r0, [(n, a if n == "apo_290de" else 0) for n, a in u.calls if n in ("apo_290de", "mg1_keyset")]


def test_tether_apo_held_at_divergence_and_renewed_on_keyset():
    """v1.4: the divergence (9 -> 2) issues 0x290de(2) once, after the nested resolve; each keyset after it renews the
    hold before mg1_keyset runs, whose result is returned unchanged. Later resolves (state already 2) issue nothing."""
    _, u = _units()
    _latch(u, 0)
    assert _resolve(u, flag=1, state=9)[:2] == (2, 2)               # diverges (nested resolve too)
    assert _apo(u) == [2] and _latch(u) == 1, u.calls               # issued once at divergence
    for mg1 in (0, 0xFFFFFFFF):
        r0, calls = _key(u, mg1)
        assert r0 == mg1 and calls == [("apo_290de", 2), ("mg1_keyset", 0)], calls   # first keyset and each renewal
    for state in (2, 3):                                             # later resolves: no call, latch kept
        _resolve(u, flag=1, state=state)
        assert _apo(u) == [] and _latch(u) == 1


def _dial(u, *, mg1=0, flag=1, usb=1, service=0, running=9, latch=0, inject=0):
    """Run tether_dial as the 0xcd05e call does (mg1_dialset 0x65d70 and the poster 0x517dc stubbed)."""
    h = _handler(_R, u, "tether_dial")
    _latch(u, latch)
    _w32(u, FLAG, flag); _w32(u, A888, usb); _w32(u, A88C, service); _w32(u, RUNNING, running); _w32(u, INJECT, inject)
    u.stub(APO, "apo_290de", r0=0)
    u.stub(0x65D70, "mg1_dialset", r0=mg1)
    u.stub(0x517DC, "post_517dc", r0=0)
    u.calls.clear()
    regs = {UC_ARM_REG_R4: 0xA4A4A4A4, UC_ARM_REG_R5: 0xA5A5A5A5, UC_ARM_REG_R6: 0x1BD2EC, UC_ARM_REG_R7: 0xA7A7A7A7}
    r0 = u.thumb(h, regs)
    assert {k: u.uc.reg_read(k) for k in regs} == regs and u.uc.reg_read(UC_ARM_REG_SP) == STACK
    return r0, [(n, a if n == "apo_290de" else 0) for n, a in u.calls if n in ("apo_290de", "mg1_dialset", "post_517dc")]


def test_tether_dial_posts_dial_event_while_still_in_state9():
    """v1.4 (hardware: physical dial on STILL, diaset,4 left state 9): after a successful mg1_dialset with remtin, USB
    and the running state 9, tether_dial turns injection on and posts (0,4) once; r0 is mg1_dialset's."""
    _, u = _units()
    r0, calls = _dial(u)
    assert r0 == 0 and calls == [("mg1_dialset", 0), ("post_517dc", 0)], calls
    assert struct.unpack(">I", u.read(INJECT, 4))[0] == 1


def test_tether_dial_posts_whatever_the_running_state():
    """v1.7: the running-state-9 guard is gone. remtin + USB + not the service boot + diaset ok posts (0,4) with the
    running state 2 (the run_state 2 / app_state 9 window) or anything else; r0 stays mg1_dialset's."""
    _, u = _units()
    for running in (2, 0, 3, 9):
        _w32(u, INJECT, 0)
        r0, calls = _dial(u, running=running)
        assert r0 == 0 and calls == [("mg1_dialset", 0), ("post_517dc", 0)], (running, calls)
        assert struct.unpack(">I", u.read(INJECT, 4))[0] == 1, running


def test_tether_dial_is_stock_otherwise():
    """Refused diaset, no remtin, no USB or the service boot: mg1_dialset only, result kept."""
    _, u = _units()
    for kw, want in ((dict(mg1=0xFFFFFFFF), 0xFFFFFFFF), (dict(flag=0), 0), (dict(usb=0), 0), (dict(service=1), 0),
                     (dict(usb=0, running=2), 0)):
        r0, calls = _dial(u, **kw)
        assert r0 == want and calls == [("mg1_dialset", 0)], (kw, calls)
        assert struct.unpack(">I", u.read(INJECT, 4))[0] == 0, kw


def test_tether_dial_renews_apo_when_diverged():
    """A diaset after the divergence (latch set, running state 2) renews the hold, then (v1.7) posts (0,4) too."""
    _, u = _units()
    r0, calls = _dial(u, latch=1, running=2)
    assert r0 == 0 and calls == [("mg1_dialset", 0), ("apo_290de", 2), ("post_517dc", 0)], calls
    r0, calls = _dial(u, mg1=0xFFFFFFFF, latch=1, running=2)     # refused: no renewal
    assert r0 == 0xFFFFFFFF and calls == [("mg1_dialset", 0)], calls


def test_tether_dial_then_resolve_leaves_state9_without_a_key():
    """The resolve the posted event triggers (stored state 9, injection now on) leaves 9 for the virtual dial's state."""
    _, u = _units()
    _latch(u, 0)
    _dial(u)
    assert _resolve(u, flag=1, state=9, inject=struct.unpack(">I", u.read(INJECT, 4))[0])[:2] == (2, 2)


def test_tether_key_before_divergence_is_plain_keyset():
    _, u = _units()
    _latch(u, 0)
    r0, calls = _key(u, 0)
    assert r0 == 0 and calls == [("mg1_keyset", 0)], calls


def test_tether_apo_nothing_when_never_diverged():
    """No remtin, no injection, no USB, service boot, and an exit without a hold: 0x290de is never called."""
    _, u = _units()
    _latch(u, 0)
    u.calls.clear()
    for kw in (dict(flag=0, state=9), dict(flag=1, state=9, inject=0), dict(flag=1, state=3, usb=0),
               dict(flag=0, state=3, usb=0), dict(flag=1, state=9, service=1)):
        _resolve(u, **kw)
        assert _apo(u) == [] and _latch(u) == 0, kw
    r = _R
    h = _handler(r, u, "tether_out")                                 # remtout without a hold: no restore
    _dirty(u); _w32(u, A888, 1); _w32(u, RUNNING, 2)
    u.stub(0x517DC, "post_517dc", r0=0)
    u.thumb(h, {UC_ARM_REG_R4: 0, UC_ARM_REG_R5: 0, UC_ARM_REG_R6: 0, UC_ARM_REG_R7: 0})
    assert _apo(u) == [] and _latch(u) == 0


def test_tether_apo_restored_on_remtout_before_the_event():
    """After a hold, tether_out calls 0x290de(1) once (before the dial event, so state 9's runner holds again) and
    drops the latch; a second remtout issues nothing."""
    _, u = _units()
    _latch(u, 0)
    assert _resolve(u, flag=1, state=9)[:2] == (2, 2) and _latch(u) == 1
    _key(u)
    u.calls.clear()
    h = _handler(_R, u, "tether_out")
    _dirty(u); _w32(u, A888, 1); _w32(u, RUNNING, 2)
    u.stub(0x517DC, "post_517dc", r0=0)
    regs = {UC_ARM_REG_R4: 0xA4A4A4A4, UC_ARM_REG_R5: 0xA5A5A5A5, UC_ARM_REG_R6: 0x1BD2EC, UC_ARM_REG_R7: 0xA7A7A7A7}
    assert u.thumb(h, regs) == 0
    assert {k: u.uc.reg_read(k) for k in regs} == regs and u.uc.reg_read(UC_ARM_REG_SP) == STACK
    assert [n for n, _ in u.calls if n in ("apo_290de", "post_517dc")] == ["apo_290de", "post_517dc"]
    assert _apo(u) == [1] and _latch(u) == 0
    u.calls.clear()
    _dirty(u)
    u.thumb(h, regs)
    assert _apo(u) == [] and _latch(u) == 0


def test_tether_apo_restored_on_unplug():
    """Cable leaves mid-tether after a hold: the resolver restores with 0x290de(1) once and drops the latch."""
    _, u = _units()
    _latch(u, 0)
    assert _resolve(u, flag=1, state=9)[:2] == (2, 2) and _latch(u) == 1
    _key(u)
    u.calls.clear()
    _dirty(u)
    _w32(u, INIT, 1); _w32(u, STATE, 2); _w32(u, A888, 0); _w32(u, A88C, 0)
    u.stub(0x324B8, "dial_324b8", r0=1); u.stub(0x66B90, "f66b90", r0=1); u.stub(APO, "apo_290de", r0=0)
    u.run_site(0x4FD1E, RET)
    assert _clean(u) and _apo(u) == [1] and _latch(u) == 0
    # unplug with remtin set but no hold ever issued: nothing
    u.calls.clear()
    _dirty(u)
    _w32(u, STATE, 2)
    u.run_site(0x4FD1E, RET)
    assert _apo(u) == []


def test_apo_routine_prologue_and_modes():
    """0x290de(mode): the bytes the feature checks; mode 2 takes the `ticks = 0` path (0x29104)."""
    f = tether.make({})
    chk = [c for c in f.checks if c.addr == tether.APO]
    assert chk and chk[0].before == fw.ram(0x290DE, 0x3A)
    assert fw.ram(0x290DE, 6).hex() == "b5002801d10f"                # push {lr}; cmp r0,#1; bne 0x29104
    assert fw.ram(0x29104, 2).hex() == "2000"                        # movs r0,#0 (every mode but 1)


BUF, ERRCODE = 0x7D0000, 0x1BEFAC


def _dispatch(u, *, msg, flag=1, usb=0, service=0):
    """Run the real record dispatch 0xd2770 on a received message `msg` (receive 0x28af0 stubbed: returns 0 with the
    buffer already filled; 0x6eb64 stubbed: not 2). Returns (r0, [0x1befac]); checks r4..r7 and sp."""
    buf = bytearray(16)
    struct.pack_into(">H", buf, 6, msg)
    u.uc.mem_write(BUF, bytes(buf))
    _w32(u, FLAG, flag)
    _w32(u, A888, usb)
    _w32(u, A88C, service)
    _w32(u, ERRCODE, 0xEEEEEEEE)
    u.stub(0x6EB64, "f6eb64", r0=0)
    u.stub(0x28AF0, "recv_28af0", r0=0)
    regs = {UC_ARM_REG_R4: 0xA4A4A4A4, UC_ARM_REG_R5: 0xA5A5A5A5, UC_ARM_REG_R6: 0xA6A6A6A6, UC_ARM_REG_R7: 0xA7A7A7A7}
    r0 = u.run_site(0xD2770, RET, {UC_ARM_REG_R0: BUF, **{k: v for k, v in regs.items() if k != UC_ARM_REG_R0}})
    assert {k: u.uc.reg_read(k) for k in regs} == regs and u.uc.reg_read(UC_ARM_REG_SP) == STACK
    return r0, struct.unpack(">I", u.read(ERRCODE, 4))[0]


def test_record_dispatch_swallows_a201_while_remtin():
    """v1.5 (hardware 12-30-05): stock 0xd2770 turns 0xa201 into 0x123005 and returns -1 (the record loop exits); with
    the remtin flag set the redirect returns 0 through 0xd2770's own epilogue and stores nothing."""
    stock, u = _units()
    assert _dispatch(stock, msg=0xA201) == (0xFFFFFFFF, 0x123005)
    assert _dispatch(u, msg=0xA201) == (0, 0xEEEEEEEE)


def test_record_dispatch_swallows_a201_while_usb_attached():
    """v1.6 (hardware 12-30-05 after remtout): USB attached outside the service boot swallows 0xa201 with remtin 0;
    USB detached with remtin 0 is stock; the service boot is stock unless remtin is set."""
    stock, u = _units()
    assert _dispatch(stock, msg=0xA201, flag=0, usb=1) == (0xFFFFFFFF, 0x123005)
    assert _dispatch(u, msg=0xA201, flag=0, usb=1) == (0, 0xEEEEEEEE)
    assert _dispatch(u, msg=0xA201, flag=1, usb=1) == (0, 0xEEEEEEEE)
    assert _dispatch(u, msg=0xA201, flag=0, usb=0) == _dispatch(stock, msg=0xA201, flag=0, usb=0) == (0xFFFFFFFF, 0x123005)
    assert _dispatch(u, msg=0xA201, flag=0, usb=1, service=1) == (0xFFFFFFFF, 0x123005)
    assert _dispatch(u, msg=0xA201, flag=1, usb=1, service=1) == (0, 0xEEEEEEEE)
    for kw in (dict(msg=0xA202), dict(msg=0xA001), dict(msg=0x3201), dict(msg=0x9201)):
        kw.update(flag=0, usb=1)
        assert _dispatch(u, **kw) == _dispatch(stock, **kw), kw
        assert _dispatch(u, **kw)[0] == 0xFFFFFFFF, kw


def test_record_dispatch_stock_otherwise():
    """No remtin, another class-A id, or another unhandled class (0x3xxx -> 0x123005, 0x9xxx too): stock -1 + code."""
    stock, u = _units()
    for kw in (dict(msg=0xA201, flag=0), dict(msg=0xA202), dict(msg=0xA001), dict(msg=0x3201), dict(msg=0x9201),
               dict(msg=0xA201 - 0x1000)):
        assert _dispatch(u, **kw) == _dispatch(stock, **kw), kw
        assert _dispatch(u, **kw)[0] == 0xFFFFFFFF, kw
    assert _dispatch(u, msg=0x3201)[1] == 0x123005
    assert _dispatch(u, msg=0x6201) == _dispatch(stock, msg=0x6201) == (0xFFFFFFFF, 0x123002)


def test_record_loop_treats_zero_as_no_event():
    """0x7eed0..: -1 exits through 0x7eedc; any other r0 becomes the event r7 (0 is matched by no table entry)."""
    assert fw.ram(0x7EED0, 0x10).hex() == "f053fc4e23011c0742d8d109f053fc32"
    assert fw.ram(0x7EFC2, 8).hex() == "27002d00d000e77e"             # r7 = 0; r5 != 0 -> receive again (0x7eec8)
    assert fw.ram(0xD27C6, 6).hex() == "bc80bc084718"                # 0xd2770 epilogue the swallow path replays
    assert fw.ram(0x6EB70, 6).hex() == "490163c84770"
