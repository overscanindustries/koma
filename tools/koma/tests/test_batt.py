"""battlog unit-emu tests: the real 2.40 battery poll 0x5290c runs under Unicorn with the module installed
by the real Koma installer. Stubbed: the CPU1 exchange 0xca76a (REQ 0x1e answers the test's reading; REQ 0x33 is the
zoom stop), the message post 0x8a678 (0x7204 = battery level message), 0x68764 (index-8 condition, answers 0) and the
file API. The store at 0x08010000 holds the ROM defaults (0x175380), so the thresholds are the stock tables."""
from __future__ import annotations

import struct

from _util import fw
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP

from koma.features import battlog as L
from koma.linker import link
from koma.unitemu import Unit

CPU1, POST, IDX8 = 0xCA76A, 0x8A678, 0x68764
FD = 0x7007
END0 = {0: 121, 2: 135, 8: 111, 11: 131}          # type 0 END per index (ROM defaults)


class BattUnit(Unit):
    def __init__(self, module, **kw):
        super().__init__(module, **kw)
        self.uc.mem_map(0x08000000, 0x100000)
        self.uc.mem_write(0x08010000, fw.ram(0x175380, 0x1000))
        self.reading, self.tick = 200, 10000
        self.posts, self.zoom_stops, self.reads = [], 0, 0
        self.file_ops, self.card, self.create_ok, self.poweroffs = [], b"", True, 0

    def _code(self, uc, a, size, _):
        if a == CPU1:
            req, buf = uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)
            if req == 0x1E:
                self.reads += 1
                uc.mem_write(buf, bytes([self.reading, 0, 0, 0]))
            elif req == 0x33:
                self.zoom_stops += 1
            return self._ret(0)
        if a == POST:
            sp = uc.reg_read(UC_ARM_REG_SP)
            pay = struct.unpack(">I", uc.mem_read(sp, 4))[0]
            self.posts.append((uc.reg_read(UC_ARM_REG_R3), uc.mem_read(pay, 1)[0]))
            return self._ret(0)
        if a == IDX8:
            return self._ret(0)
        if a == L.CREATE:
            self.file_ops.append("create " + bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R0), 32)).split(b"\0")[0].decode())
            if not self.create_ok:
                return self._ret(0xFFFFFFFF)
            self.card = b""
            return self._ret(FD)
        if a == L.WRITE:
            n = uc.reg_read(UC_ARM_REG_R2)
            assert uc.reg_read(UC_ARM_REG_R0) == FD
            self.card += bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), n))
            self.file_ops.append(f"write {n}")
            return self._ret(n)
        if a == L.CLOSE:
            self.file_ops.append("close")
            return self._ret(0)
        if a == L.POWEROFF:
            self.poweroffs += 1
            self.poweroff_regs = tuple(uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                                                 UC_ARM_REG_R3))
            return self._ret(0)
        super()._code(uc, a, size, _)

    def setup(self, idx=0, btype=0, level=3):
        w = self.uc.mem_write
        w(0x2B9E92, bytes([level, idx, 0, btype, 0]))         # level, index, 94, type, alt flag 96
        w(0x2B9E84, b"\0")
        w(0x2B9E8C, b"\0")
        for a in (0x2B9E86, 0x2B9E88, 0x2B9E8E, 0x2B9E90):
            w(a, struct.pack(">H", 400))
        w(0x1BD238, bytes([idx]))
        w(0x1A918C, struct.pack(">I", idx))
        w(0x2C9E3C, struct.pack(">I", 1))
        w(0x2B9E74, b"\x05")                                  # strobe state machine idle
        self.thumb(0x52824, {UC_ARM_REG_R0: idx})            # thresholds for the index, as the poll would

    def poll(self, reading, dt=50):
        self.reading = reading
        self.tick += dt
        self.uc.mem_write(L.TICK_WORD, struct.pack(">I", self.tick))
        self.thumb(0x5290C, {})

    @property
    def level(self):
        return self.u8(0x2B9E92)

    def u16(self, a):
        return struct.unpack(">H", self.read(a, 2))[0]


def unit(features, **setup):
    u = BattUnit(link(features).module)
    assert u.install() == 0
    u.setup(**setup)
    return u


def stock(**setup):
    """No module records applied: the stock poll."""
    u = BattUnit(link([L.make({})]).module)        # never installed
    u.setup(**setup)
    return u


# ---------------------------------------------------------------- the stock mechanism (EMU evidence for battery.md)

def test_stock_thresholds_from_rom_defaults():
    u = stock(idx=2)
    assert (u.u16(0x2B9E80), u.u16(0x2B9E82), u.u16(0x2B9E8A)) == (135, 144, 152)
    u = stock(idx=0)
    assert (u.u16(0x2B9E80), u.u16(0x2B9E82), u.u16(0x2B9E8A)) == (121, 130, 140)


def test_stock_one_sample_below_end_is_end():
    u = stock(idx=0)
    for _ in range(5):
        u.poll(160)
    assert u.level == 3 and not u.posts
    u.poll(END0[0] - 1)                     # one sample
    assert u.level == 0
    assert u.zoom_stops == 1
    assert (0x7204, 0) in u.posts
    u.poll(200)                             # END is sticky: recovery never un-latches it
    assert u.level == 0


def test_stock_near_end_is_held_400_polls():
    u = stock(idx=0)
    u.poll(160)
    u.poll(125)                             # below NEAR (130), above END (121)
    assert u.level == 1 and (0x7204, 1) in u.posts
    for _ in range(10):
        u.poll(160)
    assert u.level == 1                     # NEAR recovery: count-down from 400 polls (+50 hysteresis on NEAR)


def test_stock_type2_skips_the_check():
    u = stock(idx=0, btype=2)
    u.poll(10)
    assert u.level == 3 and u.reads == 0


# ---------------------------------------------------------------- battlog

def logunit(**setup):
    u = unit([L.make({"ring": 32, "period": 1000})], **setup)
    u.uc.mem_write(L.USB, struct.pack(">I", 0))
    return u


def off(u, site=L.POWEROFF_SITES[1]):
    u.calls.clear()
    u.thumb(site, {UC_ARM_REG_R0: 0xB, UC_ARM_REG_R1: 0x11, UC_ARM_REG_R2: 0x22, UC_ARM_REG_R3: 0x33}, stop=site + 4)


def test_battlog_segments_and_flush():
    u = logunit(idx=0)
    for _ in range(10):
        u.poll(160)
    u.poll(125)                     # dip above END (121) but below NEAR: stock drops the level to NEAR END, closing the segment
    u.poll(160)
    u.uc.mem_write(0x1A918C, struct.pack(">I", 2))   # camera state 2: the poll moves to index 2, closing the segment
    for _ in range(3):
        u.poll(150)
    for site in L.POWEROFF_SITES:
        u.card, u.poweroffs = b"", 0
        off(u, site)
        assert u.poweroffs == 1 and u.poweroff_regs == (0xB, 0x11, 0x22, 0x33)
        assert u.file_ops[0] == "create " + L.LOG_PATH and u.file_ops[-1] == "close"
    d = L.parse(u.card)
    assert d["header_ok"] and d["complete"]
    assert d["min_by_index"][0] == 125 and d["low_by_index"].get(0, 0) == 0
    r0, r1, r2 = d["records"][:3]
    assert (r0["index"], r0["level"], r0["min"], r0["max"], r0["n"], r0["replaced"], r0["below_end"]) == \
        (0, 3, 125, 160, 11, False, False)
    assert (r1["index"], r1["level"], r1["min"], r1["max"], r1["n"]) == (0, 1, 150, 160, 2)   # index lags by one poll
    assert r2["index"] == 2 and r2["min"] == 150
    assert d["snapshot"]["type"] == 0 and d["snapshot"]["end"] == 135 and d["snapshot"]["level"] == 1
    assert d["snapshot"]["raw"] == 150 and d["snapshot"]["index"] == 2 and d["snapshot"]["records_total"] == 3
    assert u.file_ops[-6:-1] == ["write 12", "write 4", f"write {L.DRV_LEN}", "write 4", f"write {L.SEG_LEN + 32 * 8}"]
    assert u.level == 1             # battlog only observes: the stock level logic is untouched


def test_battlog_period_and_ring_wrap():
    u = logunit(idx=0)
    for i in range(80):             # 80 polls x 50 ticks, period 1000 -> a record every 20 polls; ring 32
        u.poll(150 + (i % 3), dt=500)
    off(u)
    d = L.parse(u.card)
    assert len(d["records"]) == 32
    ts = [r["t"] for r in d["records"]]
    assert ts == sorted(ts)         # oldest first after the wrap


def test_battlog_no_write_at_end_below_floor_or_usb_or_no_card():
    u = logunit(idx=0)
    u.poll(160)
    u.poll(105)                     # below the floor: END
    assert u.level == 0
    off(u)
    assert not u.file_ops and u.poweroffs == 1          # raw still below END: no card write, power-off continues
    u.poll(150)                                          # recovered under no load
    off(u)
    assert u.file_ops and u.file_ops[0].startswith("create")
    u = logunit(idx=0)
    u.poll(160)
    u.uc.mem_write(L.USB, struct.pack(">I", 1))
    off(u)
    assert not u.file_ops and u.poweroffs == 1
    u = logunit(idx=0)
    u.create_ok = False
    u.poll(160)
    off(u)
    assert u.file_ops == ["create " + L.LOG_PATH] and u.poweroffs == 1


def test_battlog_options():
    import pytest
    with pytest.raises(ValueError):
        L.make({"ring": 8})
    with pytest.raises(ValueError):
        L.make({"period": 10})


def test_phase_a_waiver_is_explicit_and_recorded():
    import dataclasses
    from koma.linker import lint
    f = L.make({})
    bare = dataclasses.replace(f, hooks=[dataclasses.replace(h, early_ok="") for h in f.hooks])
    assert any("phase A" in e for e in lint([bare]))
    assert not lint([f])
    assert f.hooks[0].early_ok.startswith("0x52918 is in phase-A line")
