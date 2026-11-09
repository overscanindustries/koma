"""longexp: init, on_change, the count hook 0x470f4 and the label hook 0x80112 under unit-emu, for every value."""
from __future__ import annotations

import pytest
from _util import fw
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R4, UC_ARM_REG_SP

from koma.features import longexp as L
from koma.linker import link
from koma.unitemu import RES_POOL, STACK
from koma.unitemu import Unit as _Unit


def build():
    r = link([L.make({})])
    o_res = int(r.manifest["layout"]["resident"][0], 16)
    return r.module, r.symbols["kf_longexp__on_change"] - o_res


def state(u):
    return int.from_bytes(u.read(L.COUNT8, 2), "big"), int.from_bytes(u.read(L.EXIF42, 2), "big")


def setting(v, magic=b"KOMA"):
    return {0x1B8B0: magic, L.SET_LE: bytes([v])}


def Unit(mod, ram_edits=None):
    """unitemu.Unit seeds 0x1b000 after ram_edits, so settings bytes are written afterwards (before install)."""
    u = _Unit(mod)
    for a, b in (ram_edits or {}).items():
        u.uc.mem_write(a, bytes(b))
    return u


@pytest.mark.parametrize("v", [0, 1, 2, 0xFF])
def test_install_writes_nothing_then_on_change_applies(v):
    mod, oc = build()
    u = Unit(mod, ram_edits=setting(v))
    assert u.install() == 0 and u.callee_saved_ok
    assert state(u) == (5395, 180)
    u.thumb(RES_POOL + oc, {})
    assert state(u) == ((7200, 240) if v == 1 else (5395, 180))


def test_no_magic_is_stock():
    mod, oc = build()
    u = Unit(mod, ram_edits=setting(1, magic=b"\xff" * 4))
    assert u.install() == 0
    u.thumb(RES_POOL + oc, {})
    assert state(u) == (5395, 180)


def test_on_change_both_ways():
    mod, oc = build()
    u = Unit(mod, ram_edits=setting(0))
    assert u.install() == 0
    for v, want in ((1, (7200, 240)), (0, (5395, 180)), (1, (7200, 240)), (7, (5395, 180))):
        u.uc.mem_write(L.SET_LE, bytes([v]))
        u.thumb(RES_POOL + oc, {UC_ARM_REG_R4: 0xA4})
        assert state(u) == want and u.uc.reg_read(UC_ARM_REG_R4) == 0xA4


@pytest.mark.parametrize("v", [0, 1])
def test_count_hook_refreshes_at_use(v):
    mod, _ = build()
    u = Unit(mod, ram_edits=setting(1 - v))                       # stale table from the other value
    assert u.install() == 0
    u.thumb(RES_POOL + build()[1], {})
    u.uc.mem_write(L.SET_LE, bytes([v]))
    seen = []
    u.stub(0x46EF4, "count")
    u.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, _: seen.append((uc.reg_read(UC_ARM_REG_R0), state(u))),
                  begin=0x46EF4, end=0x46EF4)
    u.run_site(0x470F4, 0x470F8, {UC_ARM_REG_R0: 0x77, UC_ARM_REG_R4: 0xA4})
    assert seen == [(0x77, (7200, 240) if v else (5395, 180))]
    assert u.uc.reg_read(UC_ARM_REG_R4) == 0xA4 and u.uc.reg_read(UC_ARM_REG_SP) == STACK


@pytest.mark.parametrize("case", [(1, 180, 10, 240), (0, 180, 10, 180), (1, 120, 10, 120), (1, 180, 3, 180),
                                  (0xFF, 180, 10, 180)])
def test_label_hook(case):
    v, num, unit_code, want = case
    mod, _ = build()
    u = Unit(mod, ram_edits=setting(v))
    assert u.install() == 0
    u.uc.mem_write(L.COUNT8, (5395 if v == 1 else 7200).to_bytes(2, "big"))   # stale: the label hook refreshes
    seen = []
    u.stub(0x29232, "fmt")
    u.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, _: seen.append(
        (uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2))), begin=0x29232, end=0x29232)
    u.run_site(0x80112, 0x80116, {UC_ARM_REG_R0: num, UC_ARM_REG_R1: unit_code, UC_ARM_REG_R2: 0x7E1000,
                                  UC_ARM_REG_R4: 0xA4})
    assert seen == [(want, unit_code, 0x7E1000)]
    assert state(u)[0] == (7200 if v == 1 else 5395)
    assert u.uc.reg_read(UC_ARM_REG_R4) == 0xA4 and u.uc.reg_read(UC_ARM_REG_SP) == STACK


def test_rows_and_checks():
    assert L.ROWS[0]["byte"] == 0x1B8BA and L.ROWS[0]["default"] == 0 and len(L.ROWS[0]["label"]) <= 16   # naming.md label, 15 chars (stock column 16)
    assert fw.ram(0x80100, 2).hex() == "20b4"
