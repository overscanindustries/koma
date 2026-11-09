"""telemetry feature: link it alone (local registry; not in features/__init__ yet), run the real install under the
unit emulator with the file API and pool stubbed, and decode the log it writes."""
from __future__ import annotations

import struct

import pytest
from _util import fw
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from koma.features import telemetry as T
from koma.linker import link
from koma.unitemu import ALLOC, FREE, RES_POOL, Unit

FD = 0x7007
HEAP = 0x500000


class TlmUnit(Unit):
    """Unit with a size-limited pool, a heap for non-resident allocations, a timer page and a fake card."""

    def __init__(self, module, pool_limit=0x13800, create_ok=True, short_write=False, buf_ok=True, **kw):
        super().__init__(module, **kw)
        self.uc.mem_write(0x12010004, struct.pack(">I", 0x1000))
        self.pool_limit, self.create_ok, self.short_write, self.buf_ok = pool_limit, create_ok, short_write, buf_ok
        self.live, self.next_heap = {}, HEAP
        self.card, self.snapshots, self.file_ops = None, [], []

    def _code(self, uc, a, size, _):
        if a == ALLOC:
            r1, r2 = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)
            self.calls.append(("alloc", 0, r2, 0))
            if self.resident is None:
                p = RES_POOL
                self.resident = (p, r2)
            elif r2 > self.pool_limit or (r2 == T.CODE_BUF and not self.buf_ok):
                return self._ret(1)
            else:
                p, self.next_heap = self.next_heap, self.next_heap + ((r2 + 0xFFF) & ~0xFFF)
                self.live[p] = r2
            uc.mem_write(r1, struct.pack(">I", p))
            return self._ret(0)
        if a == FREE:
            p = uc.reg_read(UC_ARM_REG_R0)
            self.calls.append(("free", p))
            self.live.pop(p, None)
            return self._ret(0)
        if a == T.CREATE:
            self.file_ops.append("create " + bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R0), 32)).split(b"\0")[0].decode())
            if not self.create_ok:
                return self._ret(0xFFFFFFFF)
            self.card = b""
            return self._ret(FD)
        if a == T.WRITE:
            n = uc.reg_read(UC_ARM_REG_R2)
            self.card = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), n))
            self.file_ops.append(f"write {n}")
            return self._ret(n - 1 if self.short_write else n)
        if a == T.CLOSE:
            self.file_ops.append("close")
            self.snapshots.append(self.card)
            return self._ret(0)
        return super()._code(uc, a, size, _)


def build(**opts):
    return link([T.make(opts)])


def fields(data):
    return T.parse(data)["fields"]


def test_install_writes_three_stages_and_decodes():
    r = build()
    u = TlmUnit(r.module)
    assert u.install() == 0 and u.callee_saved_ok
    assert u.file_ops == ["create /ATA1/KOMALOG.TXT", f"write {len(T.template())}", "close"] * 3
    stages = [fields(s)["STAGE"] for s in u.snapshots]
    assert stages == [1, 2, 3]
    s1 = fields(u.snapshots[0])
    assert s1["SMC1"] is None and s1["CP15"] is None          # stage 1 record holds only plain reads
    p = T.parse(u.snapshots[-1])
    assert p["header_ok"] and p["trailer_ok"] and p["fixed_length"]
    f = p["fields"]
    assert f["BUILD"] == int(r.manifest["build_id"], 16)
    assert f["RES"] == RES_POOL                                   # resident base, not the body base (transient init)
    assert f["CPSR"] & 0x1F == 0x13
    assert f["LOOPS"] == T.LOOPS and f["TMA0"] == 0x1000
    assert 0x13800 - 0x13800 // 64 <= f["POOL"] <= 0x13800 and f["PTRY"] <= 6 + 10
    assert f["SMC1"] == 0x5A and f["SMC2"] == 0xA5 and f["FRESH"] == 0x3C and f["CBUF"] >= HEAP
    assert f["CP15"] == T.CP15_READ and f["MIDR"] >> 24 == 0x41
    assert u.live == {}                                          # every pool block it took was freed
    assert struct.unpack(">I", u.read(RES_POOL + 12, 4))[0] == 1  # install still completes
    assert all(a >= HEAP or fw.PUBLISH <= a < fw.PUBLISH + 8 for a, _ in u.outside_writes())   # heap + publish
    d = T.describe(f)
    assert d["STAGE"] == "all stages complete" and d["SMC"]["verdict"].startswith("coherent")


def test_cp15_off_has_no_mrc_and_logs_skip():
    r = build(cp15="off")
    assert "mrc p15" not in r.source.lower()
    u = TlmUnit(r.module)
    u.install()
    f = fields(u.snapshots[-1])
    assert f["STAGE"] == 3 and f["CP15"] == T.CP15_SKIPPED_OPTION and f["MIDR"] is None
    assert "mrc p15" in build().source.lower()


def test_bad_option():
    with pytest.raises(ValueError):
        T.make({"cp15": "maybe"})


def test_create_fail_skips_risky_stages():
    u = TlmUnit(build().module, create_ok=False)
    assert u.install() == 0
    assert u.file_ops == ["create /ATA1/KOMALOG.TXT"] and u.live == {}
    assert not any(c[0] == "alloc" and c[2] == T.CODE_BUF for c in u.calls)


def test_short_write_skips_risky_stages():
    u = TlmUnit(build().module, short_write=True)
    u.install()
    assert len(u.snapshots) == 1 and fields(u.snapshots[0])["STAGE"] == 1


def test_no_code_buffer_still_reaches_stage3():
    u = TlmUnit(build().module, buf_ok=False)
    u.install()
    f = fields(u.snapshots[-1])
    assert f["CBUF"] == 0xFFFFFFFF and f["SMC1"] is None and f["STAGE"] == 3


def test_pool_at_cap_and_none():
    u = TlmUnit(build().module, pool_limit=0x200000)
    u.install()
    assert fields(u.snapshots[-1])["POOL"] == T.POOL_CAP
    u = TlmUnit(build().module, pool_limit=0x100, buf_ok=False)
    u.install()
    f = fields(u.snapshots[-1])
    assert f["POOL"] == 0 and f["PTRY"] == 10


def test_parse_truncated_log():
    t = T.template()
    f = T.parse(t[:40])
    assert not f["fixed_length"] and all(v is None for v in f["fields"].values())


def test_resident_cost_small():
    r = build()
    feat = r.manifest["features"][0]
    assert feat["transient"] and feat["resident_bytes"] == 0 and 0 < feat["transient_code_bytes"] < 1200
    assert not r.manifest["records"][0]["kind"] == "data"
