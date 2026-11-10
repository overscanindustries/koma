"""rootlist: the stock 2.40 reader 0xb7954 runs in unit emulation around both patched sites, with a FAT32 and a FAT16
drive struct, before (stock RAM) and after the install. The FAT32 root assertions fail on stock RAM (the bug)."""
from __future__ import annotations

import struct

from _util import fw
from koma.features import rootlist as RL
from koma.linker import link, phase_a_lines
from koma.unitemu import Unit
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4

_R = link([RL.make({})])
DRV_TABLE, DRV_SIZE = 0x2DFCDC, 0x128
HND_TABLE, HND_SIZE = 0x2DFF2C, 0x170
SEL = 0x2E020C                          # +6 selected drive, +7 selected handle (0x2e0212 / 0x2e0213)
DRIVE, HANDLE = 1, 0                    # the card is drive 1
DBASE = DRV_TABLE + DRIVE * DRV_SIZE
NULL_EXIT, CONTINUE = 0xB799A, 0xB79B6  # end test: return NULL / go on reading
BOUND_AT = 0xB79E6                      # str r2,[sp,#8]: r2 = the entries bound


def _unit(installed=True):
    u = Unit(_R.module)
    if installed:
        assert u.install() == 0 and u.callee_saved_ok
    return u


def _drive(u, *, fat_type, rootent, cbytes, dffcc, depth=0, dffa0=0, dffb0=0x7F7A):
    u.uc.mem_write(SEL + 6, bytes([DRIVE, HANDLE]))
    u.uc.mem_write(DBASE + RL.DRIVE_ROOTENT, struct.pack(">H", rootent))
    u.uc.mem_write(DBASE + RL.DRIVE_CBYTES, struct.pack(">H", cbytes))
    u.uc.mem_write(DBASE + RL.DRIVE_FAT_TYPE, bytes([fat_type]))
    h = HND_TABLE + HANDLE * HND_SIZE
    u.uc.mem_write(h + 0xA, bytes([depth]))
    u.uc.mem_write(h + RL.H_DFFCC, struct.pack(">I", dffcc))   # 0x2dffcc: sector index of the root stream
    u.uc.mem_write(h + RL.H_DFFA0, struct.pack(">I", dffa0))   # 0x2dffa0: base 0xb21ac steps from
    u.uc.mem_write(h + RL.H_DFFB0, struct.pack(">I", dffb0))   # 0x2dffb0: stream A sector


def _run_until(u, start, stops, regs):
    hit = []

    def code(uc, a, size, _):
        if a in stops:
            hit.append(a)
            uc.emu_stop()
    hh = u.uc.hook_add(UC_HOOK_CODE, code)
    try:
        u.thumb(start, regs, stop=0x7FFFFF0, count=200)
    finally:
        u.uc.hook_del(hh)
    return hit[0] if hit else None


def end_test(u, **drv):
    """0xb796c..: depth test, then the root end test. Returns (exit address, r3, r1, r0 at exit)."""
    _drive(u, **drv)
    at = _run_until(u, 0xB796C, {NULL_EXIT, CONTINUE}, {UC_ARM_REG_R0: 0x55, UC_ARM_REG_R3: 0x33})
    return at, u.uc.reg_read(UC_ARM_REG_R3), u.uc.reg_read(UC_ARM_REG_R1), u.uc.reg_read(UC_ARM_REG_R0)


def dffa0(u):
    return struct.unpack(">I", u.read(HND_TABLE + HANDLE * HND_SIZE + RL.H_DFFA0, 4))[0]


def bound(u, **drv):
    """0xb79be..0xb79e6 (after bl 0xb5bc0 on the depth-0 path). Returns r2 (the bound stored at [sp+8]) and r3."""
    _drive(u, **drv)
    at = _run_until(u, 0xB79BE, {BOUND_AT}, {UC_ARM_REG_R4: SEL, UC_ARM_REG_R1: 0x1111})
    assert at == BOUND_AT
    return u.uc.reg_read(UC_ARM_REG_R2), u.uc.reg_read(UC_ARM_REG_R3), u.uc.reg_read(UC_ARM_REG_R1)


FAT32 = dict(fat_type=2, rootent=0, cbytes=0x8000)          # widefs / diskutil 64 GB card: 32 KiB clusters
FAT16 = dict(fat_type=1, rootent=512, cbytes=0x8000)        # 2 GB SDSC card


def test_before_bytes_and_records():
    assert fw.ram(RL.SITE_END_TEST, 4) == RL.BEFORE_END_TEST            # adds r2,#0xa0 ; ldrh r2,[r2,#0x12]
    assert fw.ram(RL.SITE_BOUND, 4) == RL.BEFORE_BOUND                  # adds r0,#0xa0 ; ldrh r2,[r0,#0x12]
    f = RL.make({})
    assert [r.site for r in f.redirects] == [0xB7992, 0xB79CA]
    for c in f.checks:
        assert fw.ram(c.addr, len(c.before)) == c.before
    assert not f.patches and not f.hooks and not f.rows and f.init is None


def test_sites_not_in_phase_a():
    lines = phase_a_lines()
    for site in (RL.SITE_END_TEST, RL.SITE_BOUND):
        assert not [x for x in lines if x <= site < x + 32 or x <= site + 3 < x + 32], hex(site)


def test_install_writes_only_the_two_sites():
    u = _unit()
    out = {a & ~3 for a, _ in u.outside_writes() if not 0x58190 <= a < 0x58198}
    assert out <= {0xB7990, 0xB7994, 0xB79C8, 0xB79CC}, [hex(a) for a in out]
    assert u.read(0xB7992, 4) != RL.BEFORE_END_TEST and u.read(0xB79CA, 4) != RL.BEFORE_BOUND


def test_bug_on_stock_fat32_root_reads_nothing():
    """Documents the bug: stock RAM returns NULL at once for a FAT32 root (RootEntCnt 0), bound 0."""
    u = _unit(installed=False)
    assert end_test(u, dffcc=1, **FAT32)[0] == NULL_EXIT
    assert bound(u, dffcc=1, **FAT32)[0] == 0


def test_fat32_root_is_read_after_fix():
    """Fails before the fix (stock gives NULL_EXIT and bound 0)."""
    u = _unit()
    at, r3, r1, r0 = end_test(u, dffcc=1, **FAT32)
    assert at == CONTINUE and r3 == DRV_TABLE and r1 == 16 and r0 == 0   # r0 (depth), r1 = dffcc*16, r3 kept
    r2, r3, r1 = bound(u, dffcc=1, **FAT32)
    assert r2 == 1024 and r3 == DRV_TABLE and r1 == 0x1111
    # the bound still ends the first cluster: dffcc = 64 (last sector) -> 1024 <= 1024 -> NULL (terminates)
    assert end_test(u, dffcc=64, **FAT32)[0] == NULL_EXIT
    assert end_test(u, dffcc=63, **FAT32)[0] == CONTINUE
    # 16 KiB clusters: 512 entries
    assert bound(u, dffcc=1, fat_type=2, rootent=0, cbytes=0x4000)[0] == 512


def test_fat16_unchanged():
    for installed in (False, True):
        u = _unit(installed)
        assert end_test(u, dffcc=1, **FAT16)[0] == CONTINUE
        assert end_test(u, dffcc=32, **FAT16)[0] == NULL_EXIT          # 512 <= 32*16: the stock end
        assert bound(u, dffcc=1, **FAT16)[0] == 512
        assert bound(u, dffcc=1, fat_type=0, rootent=224, cbytes=0x2000)[0] == 224   # FAT12 (/IROM style)


def test_subdirectory_path_never_reaches_the_sites():
    u = _unit()
    _drive(u, dffcc=1, depth=1, **FAT32)
    at = _run_until(u, 0xB796C, {0xB799E, RL.SITE_END_TEST}, {UC_ARM_REG_R0: 0})
    assert at == 0xB799E


def test_fat32_root_seeds_the_stepper_base_in_the_first_sector_only():
    """Fails before the fix: dffa0 stays 0, so 0xb21ac's second sector is LBA 1 (EMU nlst-fix-many without the seed)."""
    u = _unit()
    end_test(u, dffcc=1, dffa0=0, dffb0=0x7F7A, **FAT32)
    assert dffa0(u) == 0x7F7A
    end_test(u, dffcc=5, dffa0=0x7F7E, dffb0=0x7F7E, **FAT32)              # later sectors: left to 0xb21ac
    assert dffa0(u) == 0x7F7E
    end_test(u, dffcc=1, dffa0=0, dffb0=0x7F7A, **FAT16)                  # FAT12/16: never touched
    assert dffa0(u) == 0
    stock = _unit(installed=False)
    end_test(stock, dffcc=1, dffa0=0, dffb0=0x7F7A, **FAT32)
    assert dffa0(stock) == 0
