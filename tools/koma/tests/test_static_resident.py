"""Static resident (linker resident="static[:ADDR]"): the resident segment linked at a fixed
low-RAM address, no relocation list, no pool block, a canary after it, and telemetry's CANRY field."""
from __future__ import annotations

import struct

import pytest
from _util import SITE, fw, hook_feature
from test_telemetry import TlmUnit, fields
from unicorn import UC_HOOK_CODE

from koma import cli
from koma import features as F
from koma.asm import bl_target
from koma.features import telemetry as T
from koma.inspector import describe, parse
from koma.linker import (CANARY_LEN, CANARY_MAGIC, STATIC_DEFAULT, STATIC_SPAN, LinkError, link, parse_resident)
from koma.selfcheck import selfcheck
from koma.unitemu import Unit


def feats():
    return [F.make(n) for n in F.DEFAULT] + [hook_feature("hk", 7)]


def canary(u, base, n):
    return struct.unpack(">IIII", u.read(base + n, CANARY_LEN))


def test_parse_resident():
    assert parse_resident(None) is None and parse_resident("pool") is None
    assert parse_resident("static") == STATIC_DEFAULT == 0x3DAE00
    assert parse_resident("static:0x3e0000") == 0x3E0000
    for bad in ("heap", "static:zz", "pool:1"):
        with pytest.raises(LinkError):
            parse_resident(bad)


def test_pool_is_still_the_default():
    r = link(feats())
    assert r.manifest["layout"]["resident_mode"] == "pool" and r.manifest["layout"]["relocs"] > 0
    p = parse(r.module)
    assert not p["header"]["static"] and p["header"]["flags"] == 1


def test_static_words_are_the_pool_relocations_plus_addr():
    pool, st = link(feats()), link(feats(), resident="static")
    pp, ps = parse(pool.module), parse(st.module)
    assert ps["header"]["static"] and ps["header"]["static_addr"] == STATIC_DEFAULT and ps["relocs"] == []
    assert st.manifest["layout"]["words_baked"] == len(pp["relocs"]) > 0
    a = STATIC_DEFAULT
    seg_p = pp["body"][pp["header"]["resident"]:][:pp["header"]["resident_bytes"]]
    seg_s = ps["body"][ps["header"]["resident"]:][:ps["header"]["resident_bytes"]]
    assert len(seg_p) == len(seg_s)
    rel = set(pp["relocs"])
    for o in range(0, len(seg_p), 4):
        if o in (8,) or 0x10 <= o < 0x28:           # build id and version string differ by construction
            continue
        vp, vs = struct.unpack_from(">I", seg_p, o)[0], struct.unpack_from(">I", seg_s, o)[0]
        assert vs == (vp + a if o in rel else vp), hex(o)
    assert "resident: static at 0x3dae00" in describe(st.module)


def test_static_install_no_pool_block_canary_and_records():
    r = link(feats(), resident="static")
    n = r.manifest["layout"]["resident_bytes"]
    u = Unit(r.module)
    assert u.install() == 0 and u.callee_saved_ok
    a = STATIC_DEFAULT
    assert not any(c[0] == "alloc" for c in u.calls)                # nothing from the KernPool for the resident
    assert u.read(a, 4) == b"KOMA" and struct.unpack(">I", u.read(a + 12, 4))[0] == 1
    assert struct.unpack(">I", u.read(fw.PUBLISH + 4, 4))[0] == a
    assert bl_target(SITE, u.read(SITE, 4)) == a + int(next(x for x in r.manifest["records"]
                                                            if x["kind"] == "bl")["target_offset"], 16)
    m, bid, ln, chk = canary(u, a, n)
    assert (m, bid, ln) == (CANARY_MAGIC, int(r.manifest["build_id"], 16), n)
    assert chk == ~(m + bid + ln) & 0xFFFFFFFF
    assert [c[0] for c in u.calls] == ["irq_set", "irq_set"]
    assert not [x for x, _ in u.outside_writes() if STATIC_SPAN[0] <= x < STATIC_SPAN[1]]   # only block + canary


def test_static_refused_install_writes_nothing():
    r = link(feats(), resident="static")
    addr = 0x3C849
    u = Unit(r.module, ram_edits={addr: bytes([fw.ram(addr, 1)[0] ^ 0x5A])})
    assert u.install() == 0
    assert u.calls == [] and not [w for w in u.writes if not u.body_base <= w[0] < u.body_base + len(u.body)]


def test_static_address_rules():
    with pytest.raises(LinkError, match="outside the clean span"):
        link(feats(), resident=f"static:{STATIC_SPAN[1] - 0x100:#x}")
    with pytest.raises(LinkError, match="outside the clean span"):
        link(feats(), resident="static:0x300000")
    with pytest.raises(LinkError, match="not 4-aligned"):
        link(feats(), resident="static:0x3dae02")
    r = link(feats(), resident="static:0x3e0000")
    assert parse(r.module)["header"]["static_addr"] == 0x3E0000


def test_static_selfcheck_passes():
    r = link(feats(), resident="static")
    rep = selfcheck(r.module, r.manifest, verbose=False)
    assert rep["ok"], [c for c in rep["checks"] if not c["ok"]]
    names = [c["check"] for c in rep["checks"]]
    assert any("[static] relocation list empty" in x for x in names)
    assert any("static: canary" in x for x in names)
    assert not any("pool allocation fails" in x for x in names)


def test_telemetry_canary_ok_bad_and_pool():
    st = link([T.make({"cp15": "off"})], resident="static")
    n = st.manifest["layout"]["resident_bytes"]
    u = TlmUnit(st.module)
    assert u.install() == 0
    f = fields(u.snapshots[-1])
    assert f["RES"] == STATIC_DEFAULT and f["CANRY"] == T.CANARY_STATIC | T.CANARY_OK | n
    assert fields(u.snapshots[0])["CANRY"] == f["CANRY"]                  # stage 1 and end of init
    assert T.describe(f)["RES_LINE"] == f"RES =0x003dae00/{n} CANARY=OK"
    # canary overwritten after the installer wrote it (before telemetry's init): BAD
    u = TlmUnit(st.module)
    init = u.body_base + (parse(st.module)["inits"][-1] & 0x7FFFFFFF)
    u.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, d: uc.mem_write(STATIC_DEFAULT + n, bytes(4)), begin=init, end=init)
    u.install()
    f = fields(u.snapshots[-1])
    assert f["CANRY"] == T.CANARY_STATIC | n and T.describe(f)["RES_LINE"].endswith("CANARY=BAD")
    # pool build: 0, no canary read
    u = TlmUnit(link([T.make({"cp15": "off"})]).module)
    u.install()
    f = fields(u.snapshots[-1])
    assert f["CANRY"] == 0 and "pool build" in T.describe(f)["RES_LINE"]


def test_koma04_set_asks_for_static():
    assert cli._set_resident("koma04") == "static"
    assert cli._set_resident("koma03") is None and cli._set_resident("menu5,telemetry") is None
