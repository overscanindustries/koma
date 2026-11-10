"""Installer logic, executed for real (unit-emu: Unicorn on the 2.40 payload, firmware callees stubbed)."""
from __future__ import annotations

import hashlib
import struct

import pytest
from _util import LOG, SITE, TARGET, Feature, fw, hook_feature, patch_feature
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R7

from koma import features as F
from koma.asm import bl_target
from koma.inspector import parse
from koma.linker import KOMA_VERSION, link
from koma.selfcheck import selfcheck
from koma.unitemu import RES_POOL, Unit

CP = 0x300000


def default_module():
    return link([F.make(n) for n in F.DEFAULT]).module


def test_default_install_applies_everything():
    mod = default_module()
    u = Unit(mod)
    assert u.install() == 0
    assert u.callee_saved_ok
    assert u.read(0x3C840, 10).hex() == "20aa0080300346c046c0"
    assert hashlib.sha256(u.read(0x199B30, 0x2000)).hexdigest().startswith("9aad2861")
    assert u.read(fw.PUBLISH, 4) == b"KOMA" and struct.unpack(">I", u.read(fw.PUBLISH + 4, 4))[0] == RES_POOL
    v = f"KOMA V{KOMA_VERSION}".encode()
    assert u.read(RES_POOL + 0x10, len(v)) == v
    assert struct.unpack(">I", u.read(RES_POOL + 12, 4))[0] == 1
    assert bl_target(SITE, u.read(SITE, 4)) >= RES_POOL
    assert [c[0] for c in u.calls] == ["alloc", "irq_set", "irq_set"]


@pytest.mark.parametrize("addr", [0x3C849, 0x199B30 + 0x1FFF, 0x9031A, SITE + 2, fw.PUBLISH + 5])
def test_any_single_byte_mismatch_applies_nothing(addr):
    mod = default_module()
    b = fw.ram(addr, 1)[0] ^ 0x5A
    u = Unit(mod, ram_edits={addr: bytes([b])})
    assert u.install() == 0
    assert u.outside_writes() == []
    assert not any(c[0] in ("alloc", "irq_set") for c in u.calls)


def test_wrong_version_or_abi_applies_nothing():
    for kw in (dict(version=0x231), dict(abi=2)):
        u = Unit(default_module(), **kw)
        assert u.install() == 0 and u.outside_writes() == [] and u.calls == []


def test_alloc_failure_and_out_of_range_free_and_apply_nothing():
    u = Unit(default_module(), alloc_result=None)
    assert u.install() == 0 and u.outside_writes() == []
    assert [c[0] for c in u.calls] == ["alloc"]
    u = Unit(default_module(), alloc_result=0x1000000)          # > 4 MB from 0x96436: BL cannot reach
    assert u.install() == 0 and u.outside_writes() == []
    assert [c[0] for c in u.calls] == ["alloc", "free"] and u.calls[1][1] == 0x1000000


def test_data_only_module_needs_no_allocation():
    mod = link([F.make("snap15"), F.make("tone-r3")], publish=False).module
    u = Unit(mod)
    assert u.install() == 0
    assert [c[0] for c in u.calls] == ["irq_set", "irq_set"]
    assert u.read(0x3C850, 10).hex() == "21aa0089310346c046c0"


def run_site(u, r0=0x55):
    u.uc.mem_write(LOG, bytes(8))
    u.run_site(SITE, SITE + 4, {UC_ARM_REG_R0: r0})
    n = u.u8(LOG)
    return list(u.read(LOG + 1, n))


def test_two_features_chain_on_one_hook_site():
    mod = link([hook_feature("a", 0xA), hook_feature("b", 0xB)]).module
    u = Unit(mod)
    assert u.install() == 0
    assert run_site(u) == [0xA, 0xB]
    assert [c for c in u.calls if c[0] == "mono_8ff0e"] == [("mono_8ff0e", 0x55)]


def test_chain_order_follows_build_order():
    u = Unit(link([hook_feature("b", 0xB), hook_feature("a", 0xA)]).module)
    u.install()
    assert run_site(u) == [0xB, 0xA]


def test_three_way_chain_with_recipe_slots_runs_the_real_recipe_hook():
    mod = link([hook_feature("a", 0xA), F.make("recipe-slots"), hook_feature("z", 0x2)]).module
    assert selfcheck(mod, verbose=False)["ok"]
    u = Unit(mod)
    assert u.install() == 0
    u.uc.mem_write(0x1F4DB3, b"\x22")
    assert run_site(u) == [0xA, 0x2]
    lut = [c for c in u.calls if c[0] == "cc2_lut_16d31c"]
    assert len(lut) == 1 and lut[0][3].startswith("9aad2861")
    assert u.u8(0x1F4DB3) == 0xFF
    assert len([c for c in u.calls if c[0] == "mono_8ff0e"]) == 1


def test_recipe_port_matches_original_behaviour():
    """Same observable result as the standalone recipe-slot unit runs, through Koma."""
    for slot, imgset, want_mmio, curve in (("1", 3, [], "9aad2861"),
                                           ("2", 5, [("12018004", "7a676172"), ("12018104", "098f0400"),
                                                     ("12018108", "03870600"), ("1201810c", "03018300"),
                                                     ("120180d8", "00000002")], None)):
        u = Unit(link([F.make("recipe-slots", {"slot": slot})]).module)
        assert u.install() == 0
        assert u.u8(0x1B030) == imgset and u.u8(0x1B806) == int(slot)
        u.uc.mem_write(CP + 0x22, b"\x01")
        u.uc.mem_write(0x1F4DB3, b"\x22")
        u.run_site(0x96430, 0x9643A, {UC_ARM_REG_R7: CP})
        assert u.uc.reg_read(UC_ARM_REG_R7) == CP
        lut = [c for c in u.calls if c[0] == "cc2_lut_16d31c"]
        assert len(lut) == 1 and u.u8(0x1F4DB3) == 0xFF
        if curve:
            assert lut[0][3].startswith(curve)
        assert [m for m in u.mmio if m[0] != "12018038"] == want_mmio


def test_inits_run_before_patches_with_irq_enabled():
    mod = link([F.make("recipe-slots")]).module
    u = Unit(mod)
    u.install()
    p = parse(mod)
    assert p["inits"] and u.u8(0x1B806) == 1
    tail = [a for a, _ in u.writes_while_unmasked if 0x1B800 <= a < 0x1B8B0]
    site = [a for a, _ in u.writes if SITE <= a < SITE + 4]
    assert tail and site and not [a for a, _ in u.writes_while_unmasked if SITE <= a < SITE + 4]


def test_selfcheck_passes_on_default_and_on_a_chain():
    assert selfcheck(default_module(), verbose=False)["ok"]
    assert selfcheck(link([hook_feature("a", 1), hook_feature("b", 2), patch_feature("c", 0x3C840)]).module,
                     verbose=False)["ok"]


def test_feature_without_hooks_but_with_init_stays_resident():
    f = Feature("i", "t", "s", source=f"go:\n    push {{lr}}\n    ldr r0, =0x{LOG:x}\n    movs r1, #7\n"
                                      "    strb r1, [r0, #0]\n    pop {r3}\n    bx r3\n", init="go")
    u = Unit(link([f]).module)
    assert u.install() == 0 and u.u8(LOG) == 7
    assert TARGET == 0x8FF0E
