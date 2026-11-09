"""Early-site hooks (linker.EARLY_SITE_OK): congruent entry veneers and prefix-only (BLHI) installs.

The hazard: these BL sites already run when the module installs, so a task can be preempted between the two
halfwords of the stock BL and resume with the OLD prefix's LR. Writing a new suffix would send it to old-prefix +
new-suffix. The fix keeps the suffix halfword and writes only the prefix. These tests check the encoding math with an
independent model of the ARMv4T/v5 Thumb BL pair, the link-time placement, and the install in the unit emulator,
including a task that executed the old prefix before the install and the suffix after it.
"""
from __future__ import annotations

import struct

import pytest
from _util import fw
from koma import features as F
from koma.asm import bl_target, disasm, thumb_bl
from koma.inspector import parse
from koma.linker import EARLY_SITE_OK, LinkError, _check_early, bl_halves, link, veneer_target
from koma.unitemu import STACK, Unit
from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_SP

FEATS = ("bwrecipes", "preview", "ksettings", "waitdiag")
STOCK = {0x5D992: 0x15F458, 0x5D9BA: 0x15F494, 0x5D7C8: 0x15F244, 0x5D8AA: 0x15F2B0, 0x67178: 0x67020,
         0x672F4: 0x67020}
_C = {}


def _built():
    if "r" not in _C:
        _C["r"] = link([F.make(n) for n in FEATS], resident="static")
    return _C["r"]


# ---------------------------------------------------------------- BL semantics (independent of asm.thumb_bl)

def _sx(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def _exec_prefix(site, hw):
    """First half (H=10): LR := PC + (SignExtend(offset_11) << 12), PC = site + 4 (Thumb pipeline)."""
    assert hw & 0xF800 == 0xF000
    return (site + 4 + (_sx(hw & 0x7FF, 11) << 12)) & 0xFFFFFFFF


def _exec_suffix(lr, hw):
    """Second half (H=11): PC := LR + (offset_11 << 1) (unsigned), LR := next instruction | 1."""
    assert hw & 0xF800 == 0xF800
    return (lr + ((hw & 0x7FF) << 1)) & 0xFFFFFFFF


@pytest.mark.parametrize("site", sorted(STOCK))
def test_stock_bytes_and_model_agree(site):
    b = fw.ram(site, 4)
    hi, lo = struct.unpack(">HH", b)
    assert _exec_suffix(_exec_prefix(site, hi), lo) == STOCK[site] == bl_target(site, b)
    ins = disasm(b, site)
    assert ins and ins[0].mnemonic == "bl" and int(ins[0].op_str.lstrip("#"), 0) == STOCK[site]


def test_congruent_target_keeps_the_suffix_halfword():
    """T = O (mod 4096) <=> same suffix: the suffix holds offset bits [11:1] and T - O = off_T - off_O."""
    for site in sorted(STOCK):
        o = STOCK[site]
        for t in (0x3DF000 + (o & 0xFFF), 0x3E3000 + (o & 0xFFF), 0x400000 + (o & 0xFFF)):
            ph_o, sf_o = bl_halves(site, o)
            ph_t, sf_t = bl_halves(site, t)
            assert sf_t == sf_o and ph_t != ph_o
            assert _exec_suffix(_exec_prefix(site, ph_t), sf_t) == t                  # new pair -> new entry
            assert _exec_suffix(_exec_prefix(site, ph_o), sf_t) == o                  # old prefix + kept suffix -> stock
        for t in (0x3DF000 + ((o + 2) & 0xFFF), 0x3E3000 + ((o + 0x800) & 0xFFF)):   # not congruent: suffix changes
            assert bl_halves(site, t)[1] != bl_halves(site, o)[1]
    # the reviewed failure: 0x5d992 -> 0x3e3a0c (not congruent) with the old prefix lands on 0x15ea0c
    assert _exec_suffix(_exec_prefix(0x5D992, 0xF101), bl_halves(0x5D992, 0x3E3A0C)[1]) == 0x15EA0C


def test_waitdiag_sites_are_exactly_the_early_list():
    assert set(EARLY_SITE_OK["waitdiag"]) == set(STOCK)


# ---------------------------------------------------------------- link

def test_early_records_are_prefix_only_with_congruent_entries():
    r = _built()
    m = r.manifest
    static = int(m["layout"]["static_addr"], 16)
    recs = {int(x["addr"], 16): x for x in m["records"]}
    early = {int(e["site"], 16): e for e in m["early_sites"]}
    assert set(early) == set(STOCK)
    pages = {}
    for site, o in STOCK.items():
        assert recs[site]["kind"] == "blhi"
        e = early[site]
        entry = static + int(recs[site]["target_offset"], 16)
        assert int(e["entry"], 16) == entry and entry % 4096 == o % 4096 and e["suffix_unchanged"]
        assert thumb_bl(site, entry)[2:] == fw.ram(site, 4)[2:]
        assert int(e["suspended_old_prefix_target"], 16) == o
        pages.setdefault(o, set()).add(entry >> 12)
        # the veneer reaches the chain's first handler
        hook = next(h for f in m["features"] if f["name"] == "waitdiag" for h in f["hooks"] if int(h["site"], 16) == site)
        body = r.body
        o_res = int(m["layout"]["resident"][0], 16)
        v = veneer_target(body, o_res + entry - static, entry)
        assert v is not None and v[1] == static + int(hook["handler_offset"], 16)
    assert len(pages[0x67020]) == 2           # 0x67178 and 0x672f4: two entries = 0x020, different pages
    others = [x for x in m["records"] if x["kind"] == "bl"]
    assert others and not any(int(x["addr"], 16) in STOCK for x in others)


def test_long_veneer_touches_no_argument_register_lr_or_stack():
    r = _built()
    m = r.manifest
    static = int(m["layout"]["static_addr"], 16)
    o_res = int(m["layout"]["resident"][0], 16)
    for e in m["early_sites"]:
        entry = int(e["entry"], 16)
        off = o_res + entry - static
        if e["veneer"] == "b":
            assert r.body[off] & 0xF8 == 0xE0
            continue
        assert entry % 4 == 0 and r.body[off:off + 12].hex() == "477846c0e59fc000e12fff1c"   # bx pc; nop; ldr ip; bx ip


def test_link_refuses_a_changed_suffix():
    r = _built()
    m = r.manifest
    static = int(m["layout"]["static_addr"], 16)
    site = 0x5D992
    lbl = "koma_ven_5d992"
    bad = dict(site=site, stock=STOCK[site], label=lbl, handler="kf_waitdiag__h_semw", before=fw.ram(site, 4))
    syms = dict(r.symbols)
    syms[lbl] += 4                                   # entry one word off the congruent address
    with pytest.raises(LinkError, match="suffix would change"):
        _check_early([bad], r.body, syms, r.symbols["koma_res"], static)


def test_pool_resident_is_refused():
    with pytest.raises(LinkError, match="need a static resident"):
        link([F.make(n) for n in FEATS])


# ---------------------------------------------------------------- unit emulator

def test_install_writes_only_the_prefix_halfword():
    r = _built()
    u = Unit(r.module)
    assert u.install() == 0
    for site in STOCK:
        w = [(a, n) for a, n in u.writes if a < site + 4 and site < a + n]
        assert w == [(site, 2)], (hex(site), w)
        assert u.read(site + 2, 2) == fw.ram(site + 2, 2)


@pytest.mark.parametrize("site", sorted(STOCK))
def test_task_suspended_inside_the_bl_resumes_into_the_stock_target(site):
    """A task ran the stock prefix (LR from the BL model: unicorn executes an adjacent BL pair as one instruction, so
    the split is set up by hand), then the module installs, then the task resumes at the suffix halfword: it must reach
    the stock target and no handler (unicorn executes a lone BL suffix with Thumb-1 semantics)."""
    r = _built()
    u = Unit(r.module)
    uc = u.uc
    lr = _exec_prefix(site, struct.unpack(">H", fw.ram(site, 2))[0])     # old prefix's LR
    assert u.install() == 0
    assert bl_target(site, u.read(site, 4)) != STOCK[site]          # installed: new pair -> veneer
    u.stub(STOCK[site], "orig")
    u.calls.clear()
    uc.reg_write(UC_ARM_REG_SP, STACK)
    uc.reg_write(UC_ARM_REG_LR, lr)
    uc.reg_write(UC_ARM_REG_CPSR, (uc.reg_read(UC_ARM_REG_CPSR) & ~0xFF) | 0x13 | 0x20 | 0xC0)
    uc.emu_start((site + 2) | 1, site + 4, count=10_000)
    assert uc.reg_read(UC_ARM_REG_PC) == site + 4
    assert [c[0] for c in u.calls] == ["orig"], u.calls


def test_installer_refuses_when_the_ram_suffix_would_change():
    """Tamper a BLHI record's target offset by one word: before-bytes still match, the range pass must refuse before
    any write (static block included)."""
    r = _built()
    p = parse(r.module)
    rec = next(x for x in p["records"] if x["kind"] == "blhi")
    off = 0x14 + rec["record_offset"] + 12 + rec["len"] + (-rec["len"] % 4)
    mod = bytearray(r.module)
    struct.pack_into(">I", mod, off, rec["target_offset"] + 4)
    u = Unit(bytes(mod))
    assert u.install() == 0
    assert not u.outside_writes()
    assert not [(a, n) for a, n in u.writes if u.static[0] <= a < u.static[0] + u.static[1]]
    assert u.read(rec["addr"], 4) == rec["before"]
