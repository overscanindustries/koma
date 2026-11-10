"""colourprobe: unit-emulated checks (Unicorn on the 2.40 payload; the real stock setters run and write the
MMIO log; MMIO reads are faked at the stock getters).

Covered: install; the frame table is the documented round-3 design (<= 8 frames incl. stock and restore, sign-magnitude
and 2's-complement -63 candidates, a whole row, two rows at once, the UV high field, gain x1.3 + matrix); shot counter
increments across shots (pass 1 only); k = 0 captures the seven baselines through the stock getters and writes
nothing; each k = 1..6 restores the data baselines and then writes the right value(s) to the right register(s), in
both develop passes; the chroma-gain scale write is per byte, saturating, and never compounds across passes; the
control-word guard puts bit 6 back only when it differs; k = 7 restores only; k >= 8 writes nothing; no baseline ->
probe write only; r4-r7 kept.
"""
from __future__ import annotations

import struct

from _util import fw  # noqa: F401  (sets sys.path)
from koma.features import colourprobe as P
from koma.linker import link
from koma.unitemu import Unit
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7

CP = 0x700000
# fake power-on/stock register values seen through the getters (bit 7 of the control word set to prove the setter
# clears it; bit 6 clear)
BASE = {P.GET_IQ: 0x11223344, P.GET_MATR: 0x01020304, P.GET_MATG: 0x05060708, P.GET_MATB: 0x090A0B0C,
        P.GET_UV: 0x00000D0E, P.GET_GAIN: 0x8470697C, P.GET_CTRL: 0x00000385}   # gain = stock NORMAL
DATA = [(P.R_IQ, BASE[P.GET_IQ]), (P.R_MATR, BASE[P.GET_MATR]), (P.R_MATG, BASE[P.GET_MATG]),
        (P.R_MATB, BASE[P.GET_MATB]), (P.R_UV, BASE[P.GET_UV]), (P.R_GAIN, BASE[P.GET_GAIN])]
GETS = [P.GET_IQ, P.GET_MATR, P.GET_MATG, P.GET_MATB, P.GET_UV, P.GET_GAIN, P.GET_CTRL]


class PU(Unit):
    def __init__(self, module, **kw):
        self.got = []
        self.fake = dict(BASE)
        super().__init__(module, **kw)

    def _code(self, uc, a, size, _):
        if a in self.fake:                              # MMIO reads are 0 in unit-emu: fake the getters' result
            self.got.append(a)
            uc.mem_write(uc.reg_read(UC_ARM_REG_R0), struct.pack(">I", self.fake[a]))
            self._ret()
            return
        super()._code(uc, a, size, _)


def unit():
    r = link([P.make({})])
    u = PU(r.module, ram_edits={CP + 0x22: b"\x00"})
    assert u.install() == 0
    u.state = u.resident[0] + r.symbols["kf_colourprobe__state"] - r.symbols["koma_res"]
    assert u.read(u.state, 8) == P.MAGIC
    return u


def shot(u, pass_):
    lo = 0x96430 if pass_ == 1 else 0xAFD38
    u.calls.clear()
    u.mmio.clear()
    u.got.clear()
    sent = {UC_ARM_REG_R4: 0x44, UC_ARM_REG_R5: 0x55, UC_ARM_REG_R6: 0x66, UC_ARM_REG_R7: CP}
    u.thumb(lo, sent, stop=lo + 10)
    assert [c for c in u.calls if c[0] == "mono_8ff0e"] == [("mono_8ff0e", 0)], u.calls
    assert not any(c[0] == "ASSERT/HALT" for c in u.calls), u.calls
    assert all(u.uc.reg_read(r) == v for r, v in sent.items())
    return [(int(a, 16), int(v, 16)) for a, v in u.mmio]


def expected(k, based=True, ctrl_now=BASE[P.GET_CTRL], gain_now=BASE[P.GET_GAIN]):
    """MMIO writes for frame k when the control getter returns ctrl_now and the gain getter gain_now."""
    if not 1 <= k <= P.RESTORE_K:
        return []
    out = list(DATA) if based else []
    if based and (ctrl_now ^ BASE[P.GET_CTRL]) & P.CTRL_BITS:
        ctrl_now = ctrl_now ^ ((ctrl_now ^ BASE[P.GET_CTRL]) & P.CTRL_BITS)
        out.append((P.R_CTRL, ctrl_now & ~0x80))
    for reg, val, scale in P.PROBES.get(k, []):
        out.append((reg, P.scaled(gain_now, val) if scale else val))
    return out


def test_install_and_initial_state():
    u = unit()
    s = u.read(u.state, P.STATE_LEN)
    assert s[P.O_COUNT] == 0 and s[P.O_CUR] == 0xFF and s[P.O_BASED] == 0


def test_probe_table_matches_design():
    assert P.PROBES == {
        1: [(0x12018104, 0xBF000000, False)],                       # b3 = -63, sign-magnitude
        2: [(0x12018104, 0x81000000, False)],                       # b3 = -63, 7-bit 2's c. over b7,b5..b0
        3: [(0x12018104, 0x20909000, False)],                       # (+32, -16, -16, 0)
        4: [(0x12018104, 0x0000003F, False), (0x1201810C, 0x0000003F, False)],
        5: [(0x120180D8, 0x00001F00, False)],                       # UV high field = 31
        6: [(0x12018004, 333, True), (0x1201810C, 0x10000000, False)]}
    assert P.RESTORE_K == 7 and P.RESTORE_K + 1 <= 8            # <= 8 frames incl. stock and restore check
    touched = {r for ws in P.PROBES.values() for r, _, _ in ws}
    assert touched <= set(P.BASE_REGS)                            # every touched register has a restore
    assert P.STATE_LEN == 8 + 4 + 4 * 6 + 4 + 8


def test_scaled_gain():
    assert P.scaled(0x8470697C) == 0xAB9188A1                      # stock NORMAL x1.30


def test_sequence_both_passes():
    u = unit()
    for k in range(10):
        w1 = shot(u, 1)
        s = u.read(u.state, P.STATE_LEN)
        assert s[P.O_CUR] == k and s[P.O_COUNT] == k + 1, (k, s.hex())
        if k == 0:
            assert w1 == [] and s[P.O_BASED] == 1
            assert u.got == GETS
            assert struct.unpack(">7I", s[P.O_BASE:P.O_BASE + 28]) == tuple(BASE[g] for g in GETS)
        assert w1 == expected(k), (k, [(hex(a), hex(v)) for a, v in w1])
        w2 = shot(u, 2)                                 # pass 2: same k, no increment
        s = u.read(u.state, P.STATE_LEN)
        assert s[P.O_CUR] == k and s[P.O_COUNT] == k + 1
        assert w2 == expected(k), (k, w2)
        if 1 <= k <= P.NPROBE:
            reg, val, _ = P.PROBES[k][-1]
            assert struct.unpack(">II", s[P.O_LAST:P.O_LAST + 8]) == (reg, val)


def test_gain_scale_per_byte_saturating_no_compounding():
    """k = 6: gain = getter value x 333/256 per byte, saturating at 0xff, then MatB. The getter is read after the
    restore, so pass 2 gives the same value as pass 1 (x1.3, not x1.69)."""
    u = unit()
    u.fake[P.GET_GAIN] = 0xFF10C080
    shot(u, 1)                                          # k = 0: baselines
    u.uc.mem_write(u.state + P.O_COUNT, bytes([6]))
    w1 = shot(u, 1)
    assert P.scaled(0xFF10C080) == 0xFF14F9A6
    assert w1[-2:] == [(P.R_GAIN, 0xFF14F9A6), (P.R_MATB, 0x10000000)], w1
    assert w1[:len(DATA)] == DATA[:-1] + [(P.R_GAIN, 0xFF10C080)]  # restore writes the captured baseline first
    assert P.GET_GAIN in u.got
    assert shot(u, 2)[-2:] == w1[-2:]


def test_ctrl_guard_restores_bit6_only_when_changed():
    u = unit()
    shot(u, 1)                                          # k = 0: baselines (ctrl 0x385)
    u.fake[P.GET_CTRL] = 0x342                          # something set bit 6 meanwhile (and stock changed bits 0-5)
    w = shot(u, 1)                                      # k = 1
    assert w == DATA + [(P.R_CTRL, 0x302), (P.R_MATR, 0xBF000000)], w
    u.fake[P.GET_CTRL] = 0x302
    assert shot(u, 1) == DATA + [(P.R_MATR, 0x81000000)]


def test_k_ge_8_writes_nothing():
    u = unit()
    shot(u, 1)
    u.uc.mem_write(u.state + P.O_COUNT, bytes([7]))
    assert shot(u, 1) == DATA and shot(u, 2) == DATA     # k = 7: restore only
    for k in (8, 9):
        assert shot(u, 1) == [] and shot(u, 2) == []
    u.uc.mem_write(u.state + P.O_COUNT, b"\xff")       # saturates, still stock
    assert shot(u, 1) == [] and u.u8(u.state + P.O_COUNT) == 0xFF


def test_no_baseline_probe_only():
    """The smoke path: count poked to 1 before the first shot, so no baseline was captured; only the probe write."""
    u = unit()
    u.uc.mem_write(u.state + P.O_COUNT, b"\x01")
    assert shot(u, 1) == [(P.R_MATR, 0xBF000000)]
    assert shot(u, 2) == [(P.R_MATR, 0xBF000000)]
    assert u.got == []


def test_pass2_before_any_pass1_is_stock():
    u = unit()
    assert shot(u, 2) == []
