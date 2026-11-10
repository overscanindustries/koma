"""waitdiag (temporary diagnostic): the expected before-bytes, the wait ring and phase words under unit emulation on the 2.40
payload (the original callees stubbed; a code hook advances the tick 0x1a8a00 while the "wait" runs), and the -KSTA
extension through the real parser / dispatcher."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from _util import fw
from koma import features as F
from koma.features import waitdiag as W
from koma.linker import link
from koma.unitemu import STACK, Unit
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4,
                               UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_SP)

import test_ksettings as TK

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "komaset" / "bridge"))
import komabridge as KB  # noqa: E402

FEATS = ("bwrecipes", "preview", "ksettings", "waitdiag")
_C = {}


def _unit():
    if "r" not in _C:
        _C["r"] = link([F.make(n) for n in FEATS], resident="static")   # early-site veneers need a fixed address
    r = _C["r"]
    u = Unit(r.module)
    assert u.install() == 0 and u.callee_saved_ok
    u.sym = lambda name: u.resident[0] + r.symbols[f"kf_waitdiag__{name}"] - r.symbols["koma_res"]
    u.ksym = lambda name: u.resident[0] + r.symbols[f"kf_ksettings__{name}"] - r.symbols["koma_res"]
    u.adv = {}

    def code(uc, a, size, _):
        if a in u.adv:
            t = struct.unpack(">I", bytes(uc.mem_read(W.TICK, 4)))[0]
            uc.mem_write(W.TICK, struct.pack(">I", (t + u.adv[a]) & 0xFFFFFFFF))
            u.seen.append((a, uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1),
                           struct.unpack(">I", bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_SP), 4)))[0]))
    u.uc.hook_add(UC_HOOK_CODE, code)
    u.seen = []
    return u


def _w(u, a, v):
    u.uc.mem_write(a, struct.pack(">I", v & 0xFFFFFFFF))


def _r(u, a):
    return struct.unpack(">I", u.read(a, 4))[0]


def _block(u):
    return list(struct.unpack(f">{W.BLOCK_WORDS}I", u.read(u.sym("wd_block"), 4 * W.BLOCK_WORDS)))


def _site(u, site, target, regs, ret=0, ticks=0, stack0=0):
    """Run the hooked bl at `site` (callee `target` stubbed, returning `ret` after `ticks`). Returns r0."""
    u.stub(target, "orig", r0=ret)
    u.adv = {target: ticks}
    u.seen.clear()
    u.calls.clear()
    u.uc.mem_write(STACK, struct.pack(">I", stack0))
    sent = {UC_ARM_REG_R4: 0xB4, UC_ARM_REG_R5: 0xB5, UC_ARM_REG_R6: 0xB6, UC_ARM_REG_R7: 0xB7}
    sent.update({k: v for k, v in regs.items() if k in sent})
    r0 = u.thumb(site, {**regs, **sent}, stop=site + 4)
    assert {k: u.uc.reg_read(k) for k in sent} == sent and u.uc.reg_read(UC_ARM_REG_SP) == STACK
    assert [c[0] for c in u.calls].count("orig") == 1, u.calls
    return r0


def test_expected_before_bytes_match_the_image():
    for site, b in W.before_bytes():
        assert fw.ram(site, 4) == b, hex(site)
    f = W.make({})
    assert len(f.hooks) == 15 and f.requires == ["ksettings"] and f.init == "wd_init"


def test_layout_matches_the_bridge():
    assert (KB.WD_HEADER, KB.WD_PHASES, KB.WD_HANDLES, KB.WD_RECORD) == (W.HEADER, W.PHASES, W.HANDLES, W.RECORD)
    assert (KB.WD_HDR_WORDS, KB.WD_NREC, KB.WD_RECW) == (W.HDR_WORDS, W.NREC, W.RECW)
    assert KB.WD_KINDS == {k: v[0] for k, v in W.KINDS.items()} and KB.WD_MAGIC_HI == W.MAGIC >> 16


def test_init_registers_the_ksta_extension():
    u = _unit()
    assert _r(u, u.ksym("ksta_ext")) == u.sym("wd_export")
    assert _block(u)[0] == W.MAGIC


def test_long_sem_wait_is_pinned_with_provenance():
    u = _unit()
    _w(u, W.TICK, 5000)
    _w(u, 0x1A8D18, 0x40)                              # the LCD semaphore handle variable holds 0x40
    _w(u, 0x600010, 0x22957)                           # [r5 + 0x10] = 0x5d978's caller
    _w(u, W.DISPATCH, 0)
    r0 = _site(u, 0x5D992, 0x15F458, {UC_ARM_REG_R0: 0x40, UC_ARM_REG_R1: 10001, UC_ARM_REG_R5: 0x600000},
               ret=0, ticks=10000)
    assert r0 == 0 and ("orig", 0x40) in u.calls and u.seen[0][2] == 10001     # the callee got r0 / r1 unchanged
    d = KB.waitdiag_decode(_block(u))
    assert d["seq"] == 1 and d["overflow"] == d["lost"] == 0 and len(d["records"]) == 1
    rec = d["records"][0]
    assert (rec["state"], rec["kind_name"], rec["caller"], rec["object"], rec["timeout"], rec["entry"], rec["exit"],
            rec["ret"], rec["dispatch"]) == ("done", "sem_wait", 0x22957, 0x40, 10001, 5000, 15000, 0, 0)
    assert d["prov"]["0x1a8d18"]["acq_lr"] == 0x22957 and d["prov"]["0x1a8d18"]["acq_tick"] == 15000


def test_fast_and_short_waits_leave_no_record():
    u = _unit()
    _w(u, W.TICK, 100)
    assert _site(u, 0x5D992, 0x15F458, {UC_ARM_REG_R0: 0x41, UC_ARM_REG_R1: 999, UC_ARM_REG_R5: 0x600000},
                 ret=0xFFFFFFF6, ticks=50) == 0xFFFFFFF6       # timeout < 1000: tail call, nothing recorded
    assert _block(u)[1] == 0
    _site(u, 0x5D992, 0x15F458, {UC_ARM_REG_R0: 0x41, UC_ARM_REG_R1: 0xFFFFFFFF, UC_ARM_REG_R5: 0x600000},
          ret=0, ticks=99)                                       # infinite but done in 99 ticks: recycled
    d = KB.waitdiag_decode(_block(u))
    assert d["seq"] == 1 and d["records"] == []


def test_queue_receive_passes_the_stack_argument_and_records_the_message():
    u = _unit()
    _w(u, W.TICK, 0)
    _w(u, 0x600010, 0x28B05)
    u.uc.mem_write(0x610000, bytes(6) + b"\x72\x05")         # received message id 0x7205 at buf + 6
    r0 = _site(u, 0x5D8AA, 0x15F2B0, {UC_ARM_REG_R0: 0x77, UC_ARM_REG_R1: 0x610000, UC_ARM_REG_R2: 8,
                                      UC_ARM_REG_R3: 0x620000, UC_ARM_REG_R4: 0x600000},
               ret=0, ticks=3000, stack0=0xFFFFFFFF)
    assert r0 == 0 and u.seen[0][3] == 0xFFFFFFFF            # the callee saw the 5th argument at [sp]
    rec = KB.waitdiag_decode(_block(u))["records"][0]
    assert (rec["kind_name"], rec["caller"], rec["object"], rec["timeout"], rec["detail"], rec["exit"]) == \
        ("queue_recv", 0x28B05, 0x77, 0xFFFFFFFF, 0x7205, 3000)


def test_flag_wait_and_handshake():
    u = _unit()
    _w(u, W.TICK, 0)
    _w(u, STACK + 36, 0x3769F)                               # the API's caller lr at [sp+36]
    _w(u, 0x630000, 0)
    r0 = _site(u, 0x15ED1C, 0x15FCB0, {UC_ARM_REG_R0: 0x99, UC_ARM_REG_R1: 0x10, UC_ARM_REG_R2: 1,
                                       UC_ARM_REG_R3: 0x630000}, ret=0xFFFFFFCE, ticks=10000, stack0=10000)
    assert r0 == 0xFFFFFFCE and u.seen[0][3] == 10000
    _w(u, W.HS, 1); _w(u, W.HS + 4, 0); _w(u, W.HS + 8, 0)
    _w(u, STACK + 8, 0x7F0D5)
    _site(u, 0x670D8, 0x67020, {}, ticks=9999)
    _w(u, W.HS, 0)
    _site(u, 0x670D8, 0x67020, {}, ticks=5000)               # [0x1bec44] != 1: no wait, no record
    recs = KB.waitdiag_decode(_block(u))["records"]
    assert [(r["kind_name"], r["caller"], r["parent"], r["timeout"], r["exit"] - r["entry"]) for r in recs] == \
        [("flag_wait", 0x3769F, 0x10, 10000, 10000), ("hs_670d8", 0x7F0D5, 0, 10000, 9999)]
    assert recs[1]["detail"] == 0x100 << 12 | 0x100          # nibbles 44/48/4c: entry 1,0,0 / exit 1,0,0


def test_release_provenance_and_ring_eviction():
    u = _unit()
    _w(u, 0x1A923C, 0x55)
    _w(u, 0x600010, 0x2AC91)
    _w(u, W.TICK, 777)
    _site(u, 0x5D9BA, 0x15F494, {UC_ARM_REG_R0: 0x55, UC_ARM_REG_R4: 0x600000})
    assert KB.waitdiag_decode(_block(u))["prov"]["0x1a923c"] == dict(acq_lr=0, acq_tick=0, rel_lr=0x2AC91,
                                                                      rel_tick=777)
    for i in range(17):                                      # 16 pinned records, then one more evicts the shortest
        _site(u, 0x5D992, 0x15F458, {UC_ARM_REG_R0: i, UC_ARM_REG_R1: 10000, UC_ARM_REG_R5: 0x600000},
              ticks=150 if i == 3 else 200 + 10 * i)
    d = KB.waitdiag_decode(_block(u))
    assert d["seq"] == 17 and d["overflow"] == 1 and len(d["records"]) == 16
    assert 3 not in [r["object"] for r in d["records"]] and 16 in [r["object"] for r in d["records"]]


def test_phase_hooks():
    u = _unit()
    _w(u, W.TICK, 1000)
    u.uc.mem_write(0x640000, bytes(6) + b"\x51\x05" + bytes(4) + b"\x00\x04")
    _site(u, 0x28B10, 0x28BDC, {UC_ARM_REG_R0: 0x640000, UC_ARM_REG_R1: 1}, ret=0xFFFFFFFF, ticks=5600)
    _site(u, 0x28D68, 0x4FC6E, {UC_ARM_REG_R7: 0x640000}, ret=2, ticks=3)
    _site(u, 0x7EF96, 0x157ACC, {UC_ARM_REG_R0: 1, UC_ARM_REG_R2: 0x7347D}, ret=0, ticks=10000)
    _site(u, 0x7EFA6, 0x157AC8, {UC_ARM_REG_R0: 0x73771}, ret=2, ticks=10)
    u.uc.mem_write(0x650000, bytes(6) + b"\x72\x05")
    _site(u, 0xD27C2, 0xD23BC, {UC_ARM_REG_R0: 0x650000}, ret=0x4B)
    p = KB.waitdiag_decode(_block(u))["phases"]
    assert (p["msg_idcode"], p["msg_t0"], p["msg_t1"]) == (0x51050004, 1000, 6600)
    assert (p["msgslow_idcode"], p["msgslow_t0"], p["msgslow_t1"]) == (0x51050004, 1000, 6600)
    assert (p["res_idcode"], p["res_msg_t0"], p["res_t0"], p["res_t1"], p["res_ret"]) == (0x51050004, 1000, 6600,
                                                                                           6603, 2)
    assert (p["h7347c_t0"], p["h7347c_t1"], p["slow_target"], p["slow_t0"], p["slow_t1"]) == \
        (6603, 16603, 0x7347D, 6603, 16603)
    assert (p["t73770_t0"], p["t73770_t1"], p["t73770_ret"]) == (16603, 16613, 2)
    assert (p["dec_idret"], p["dec_tick"], p["dec4b_tick"]) == (0x7205004B, 16613, 16613)


def test_ksta_frame_carries_the_block():
    u = _unit()
    TK._w32(u, TK.K.USB_BUF, TK.USB)
    _w(u, W.TICK, 0)
    _site(u, 0x5D992, 0x15F458, {UC_ARM_REG_R0: 9, UC_ARM_REG_R1: 10000, UC_ARM_REG_R5: 0x600000}, ticks=10000)
    n = TK.K.KSTA_BYTES + 4 * W.BLOCK_WORDS
    u.uc.mem_write(TK.USB, b"\xee" * (n + 16))
    reply, rn, state, blk = TK._cmd(u, "-KSTA\r\n")
    assert reply == TK.K.S150 and state == 4
    assert TK._u32(u, TK.K.TOTAL) == TK._u32(u, TK.K.CHUNK) == n and u.read(TK.USB + 8 + n, 4) == b"\xee" * 4
    d = KB.ksta_decode(u.read(TK.USB + 8, n))
    assert d["version"] == KB.KSTA_VERSION and d["waitdiag"]["records"][0]["exit"] == 10000
    assert TK.K.ksta_decode(u.read(TK.USB + 8, n))["ext"][0] == W.MAGIC
