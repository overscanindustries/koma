"""snapdist + fullpress, unit-emulated on the real 2.40 code around each hook (firmware callees stubbed)."""
from __future__ import annotations

import struct

import pytest
from _util import fw
from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_SP

from koma.features import fullpress, snapdist
from koma.linker import LinkError, link
from koma.unitemu import STACK, Unit

CAL = 0x08010000
ROM_CAL = 0x175380            # ROM defaults of the calibration block
EXPECT = {0: 1025, 1: 683, 2: 512, 3: 410, 4: 205, 5: 0}


def unit(feats, snap=None, full=None, cal_edit=None):
    u = Unit(link(feats).module)
    u.uc.mem_map(CAL, 0x1000)
    u.uc.mem_write(CAL, fw.ram(ROM_CAL, 0x1000))
    for a, v in (cal_edit or {}).items():
        u.uc.mem_write(CAL + a, struct.pack(">H", v))
    assert u.install() == 0 and u.callee_saved_ok
    if snap is not None:
        u.uc.mem_write(snapdist.BYTE, bytes([snap]))
    if full is not None:
        u.uc.mem_write(fullpress.BYTE, bytes([full]))
    return u


def r0_of(u, name):
    return [c[1] for c in u.calls if c[0] == name]


def snap_routine(u):
    """Run SNAP focus-mode routine 0x3c83c whole; return (0x71314 argument, stored word 0x2d24fc)."""
    for a, n in ((0x71314, "pos"), (0x15EC8C, "evt"), (0x37FB0, "move"), (0x3CA7C, "a"), (0x385F8, "b")):
        u.stub(a, n, 0)
    u.run_site(0x3C83C, 0x3C87A)
    return r0_of(u, "pos")[0], struct.unpack(">H", u.read(0x2D24FC, 2))[0]


@pytest.mark.parametrize("b", [0, 1, 2, 3, 4, 5, 6, 0xFF])
def test_snap_routine_each_byte(b):
    want = EXPECT.get(b, 410)
    assert snap_routine(unit([snapdist.make({})], snap=b)) == (want, want)


def test_stock_snap_routine():
    u = Unit(link([fullpress.make({})]).module)
    u.uc.mem_map(CAL, 0x1000)
    u.uc.mem_write(CAL, fw.ram(ROM_CAL, 0x1000))
    assert u.install() == 0
    assert snap_routine(u) == (410, 410)


def test_calibrated_body_line():
    u = unit([snapdist.make({})], snap=1, cal_edit={0x892: 420, 0x894: 6})
    assert snap_routine(u)[0] == 6 + (414 * 109227 >> 16)        # 696
    u = unit([snapdist.make({})], snap=5, cal_edit={0x892: 420, 0x894: 6})
    assert snap_routine(u)[0] == 6
    u = unit([snapdist.make({})], snap=0, cal_edit={0x892: 5, 0x894: 9})      # implausible: stock
    assert snap_routine(u)[0] == 5


@pytest.mark.parametrize("b", [1, 3, 5])
def test_reapply_mode2(b):
    u = unit([snapdist.make({})], snap=b)
    u.stub(0x71314, "pos", 0)
    u.uc.mem_write(0x23B06C, b"\x02")
    u.run_site(0x36FF0, 0x3700E)
    assert r0_of(u, "pos") == [EXPECT[b]]


def preset(u, mode, scene=0):
    """Run the type-1 preset step 0x703fa..0x70404 (real 0x712a4); return the word stored at 0x1bf02a."""
    u.uc.mem_write(0x23B06C, bytes([mode]))
    u.uc.mem_write(0x23B068 + 0x1D, bytes([scene]))
    u.run_site(0x703FA, 0x70402)
    return struct.unpack(">H", u.read(0x1BF02A, 2))[0]


@pytest.mark.parametrize("mode", [0, 2, 6])
def test_preset_snap_and_af_modes(mode):
    for b in range(6):
        assert preset(unit([snapdist.make({})], snap=b), mode) == EXPECT[b]


def test_preset_other_branches_stock():
    u = unit([snapdist.make({})], snap=1, cal_edit={0x894: 7, 0x896: 11, 0x898: 13})
    assert preset(u, 3) == 7                       # inf mode: calibration inf word
    assert preset(u, 0, scene=3) == 11
    assert preset(u, 0, scene=4) == 13
    u.uc.mem_write(CAL + 0x40, bytes([0xAA, 0, 0x01, 0x23]))
    assert preset(u, 0) == 0x123                   # calibration override


def fp_pending(u, mode, rl=2, typ=2, macro=0):
    """RL2-pending branch 0x9576c..0x95784 with stub 0x70280; returns the type passed."""
    u.stub(0x70280, "f70280", 0)
    u.uc.mem_write(0x1F4E68 + 0x12, bytes([typ, rl]))
    u.uc.mem_write(0x23B068 + 3, bytes([macro, mode]))
    u.uc.mem_write(0x1BF028, b"\x00")
    u.run_site(0x9576C, 0x95784, {UC_ARM_REG_R5: 0x1F4E68})
    return r0_of(u, "f70280")[0], u.u8(0x1BF028)


def fp_sensor(u, mode, rl=2, typ=3):
    u.stub(0x70280, "f70280", 0)
    u.uc.mem_write(0x1F4E68 + 0x12, bytes([typ, rl]))
    u.uc.mem_write(0x23B068 + 3, bytes([0, mode]))
    u.uc.mem_write(0x1BF028, bytes([typ]))
    u.run_site(0x95732, 0x9573C, {UC_ARM_REG_R6: 0x1F4E68, UC_ARM_REG_R7: 0x1F4E68, UC_ARM_REG_SP: STACK})
    return r0_of(u, "f70280")[0], u.u8(0x1BF028)


@pytest.mark.parametrize("mode", [0, 6])
def test_fullpress_on_af(mode):
    u = unit([snapdist.make({}), fullpress.make({})], full=1)
    assert fp_pending(u, mode) == (1, 1)
    assert fp_pending(u, mode, typ=5) == (1, 1)    # type 5: 0x70994(2) then ours
    assert fp_sensor(u, mode) == (1, 1)


@pytest.mark.parametrize("full", [0, 2, 3, 0xFF])
def test_fullpress_off_is_stock(full):
    u = unit([fullpress.make({})], full=full)
    assert fp_pending(u, 0) == (2, 0)
    assert fp_sensor(u, 0) == (3, 3)


def test_fullpress_on_but_not_applicable():
    u = unit([fullpress.make({})], full=1)
    for mode in (1, 2, 3):                        # MF, SNAP, inf
        assert fp_pending(u, mode) == (2, 0)
    assert fp_pending(u, 0, macro=1) == (2, 0)
    assert fp_sensor(u, 0, rl=1) == (3, 3)        # half press (RL2 not pending): AF untouched


def test_fullpress_then_preset_uses_snapdist_or_stock():
    u = unit([snapdist.make({}), fullpress.make({})], snap=1, full=1)
    assert preset(u, 0) == 683
    u = unit([fullpress.make({})], full=1)
    assert preset(u, 0) == 410


def test_conflicts_with_snap15():
    from koma.features import snap15
    with pytest.raises(LinkError):
        link([snap15.make({}), snapdist.make({})])


def test_rows():
    for mod in (snapdist, fullpress):
        for r in mod.ROWS:
            assert len(r["label"]) <= 14 and all(len(o) <= 9 for o in r["options"])
    assert snapdist.ROWS[0]["byte"] == 0x1B8B5 and snapdist.ROWS[0]["default"] == 3
    assert fullpress.ROWS[0]["byte"] == 0x1B8B6 and fullpress.ROWS[0]["default"] == 0
