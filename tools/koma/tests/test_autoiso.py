"""autoiso: the 0x3f920 hook under unit-emu, for every option value (stock AE 0x42ad4 stubbed; it snapshots the store)."""
from __future__ import annotations

import struct

import pytest
from _util import fw
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4,
                               UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_SP)

from koma.asm import bl_target
from koma.features import autoiso as A
from koma.linker import link
from koma.unitemu import RES_POOL, STACK, Unit

STORE = 0x08010000
ARGS = {UC_ARM_REG_R0: 0x10, UC_ARM_REG_R1: 0x11, UC_ARM_REG_R2: 0x12, UC_ARM_REG_R3: 0x13}
SAVED = {UC_ARM_REG_R4: 0xA4, UC_ARM_REG_R5: 0xA5, UC_ARM_REG_R6: 0xA6, UC_ARM_REG_R7: 0xA7}


def unit(iso, ss, magic=b"KOMA", iso_code=0, flash=0):
    u = Unit(link([A.make({})]).module)
    assert u.install() == 0 and u.callee_saved_ok
    u.uc.mem_map(0x08000000, 0x100000)
    u.uc.mem_write(STORE, fw.ram(0x175380, 0x2000))           # compiled default store image (0x27bc0 copies it)
    u.uc.mem_write(0x1B8B0, magic + bytes([1, 3, 0, iso, ss]))
    u.uc.mem_write(0x23B068 + 9, bytes([iso_code]))
    u.uc.mem_write(0x23B068 + 0x10, bytes([flash]))
    seen = []

    def ae(uc, a, size, _):
        st = bytes(uc.mem_read(STORE, 0x4A0))
        seen.append(dict(args=[uc.reg_read(r) for r in ARGS], tv=st[0x80],
                         ceil=struct.unpack(">h", st[0x47E:0x480])[0], clamp=struct.unpack(">h", st[0x49A:0x49C])[0]))
        uc.reg_write(UC_ARM_REG_R0, 0x5A5A)                       # AE's return value
    u.stub(0x42AD4, "ae")
    u.uc.hook_add(UC_HOOK_CODE, ae, begin=0x42AD4, end=0x42AD4)
    r0 = u.run_site(0x3F920, 0x3F924, {**ARGS, **SAVED})
    assert len(seen) == 1, seen
    assert r0 == 0x5A5A and seen[0]["args"] == list(ARGS.values())
    assert all(u.uc.reg_read(r) == v for r, v in SAVED.items()) and u.uc.reg_read(UC_ARM_REG_SP) == STACK
    after = bytes(u.uc.mem_read(STORE, 0x4A0))
    assert (after[0x80], after[0x47E:0x480].hex(), after[0x49A:0x49C].hex()) == (82, "000a", "000a"), "not restored"
    return seen[0]


def test_hook_installed():
    u = Unit(link([A.make({})]).module)
    assert u.install() == 0
    assert bl_target(0x3F920, u.read(0x3F920, 4)) >= RES_POOL


def test_rows_declared():
    assert [r["byte"] for r in A.ROWS] == [0x1B8B7, 0x1B8B8] and all(r["default"] == 0 for r in A.ROWS)
    for r in A.ROWS:
        assert len(r["label"]) <= 14 and all(len(o) <= 9 for o in r["options"])


@pytest.mark.parametrize("iso", list(range(len(A.ISO_OPTIONS))) + [5, 0xFF])
def test_iso_values(iso):
    s = unit(iso, 0)
    want = A.ISO_GAIN[iso] if iso < len(A.ISO_GAIN) and A.ISO_GAIN[iso] else 10
    assert (s["tv"], s["ceil"], s["clamp"]) == (82, want, want)


@pytest.mark.parametrize("ss", list(range(len(A.SS_OPTIONS))) + [9, 0xFF])
def test_slow_shutter_values(ss):
    s = unit(0, ss)
    want = A.SS_TV[ss] if ss < len(A.SS_TV) and A.SS_TV[ss] else 82
    assert (s["tv"], s["ceil"], s["clamp"]) == (want, 10, 10)


def test_both_together():
    s = unit(2, 3)
    assert (s["tv"], s["ceil"], s["clamp"]) == (96, 32, 32)       # the frontier-3 iso400-min60 values


@pytest.mark.parametrize("gate", ["nomagic", "fixed-iso", "flash-auto", "flash-on"])
def test_gates_keep_stock(gate):
    kw = {"nomagic": dict(magic=b"\xff" * 4), "fixed-iso": dict(iso_code=6), "flash-auto": dict(flash=1),
          "flash-on": dict(flash=2)}[gate]
    s = unit(4, 8, **kw)
    assert (s["tv"], s["ceil"], s["clamp"]) == (82, 10, 10)
