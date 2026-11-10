"""Linker: layout, namespacing, inter-feature symbols, relocations, manifest, CLI."""
from __future__ import annotations

import hashlib
import json
import struct

import pytest
from _util import Feature, hook_feature, patch_feature

from koma import features as F
from koma.inspector import build_id_ok, describe, parse
from koma.linker import KOMA_VERSION, MIN_FIRMWARE_GEN, CORE_EXPORTS, LinkError, link, mangle_source, ns


def default():
    return link([F.make(n) for n in F.DEFAULT])


def test_default_build_layout_and_manifest():
    r = default()
    m = r.manifest
    assert len(r.module) == m["module"]["bytes"] <= 0x10000 + 0x14
    assert [f["name"] for f in m["features"]] == ["tone-r3", "snap15", "recipe-slots"]
    assert m["version_string"] == f"KOMA V{KOMA_VERSION} {m['build_id']} G{MIN_FIRMWARE_GEN}"
    assert m["menu_version"] == "2.40K" + ".".join(KOMA_VERSION.split(".")[:2]) and len(m["menu_version"]) <= 9
    assert m["photo_tag"] == "240K" + ".".join(KOMA_VERSION.split(".")[:2]) and len(m["photo_tag"]) <= 7
    assert m["min_firmware_gen"] == MIN_FIRMWARE_GEN
    p = parse(r.module)
    assert p["manifest"]["version"] == m["version_string"] and p["manifest"]["status"] == 0
    assert build_id_ok(p)
    assert [f["name"] for f in p["manifest"]["features"]] == ["tone-r3", "snap15", "recipe-slots"]
    kinds = [x["kind"] for x in m["records"]]
    assert kinds[:kinds.count("check")] == ["check"] * kinds.count("check"), "checks are verified first"
    # data-only features keep nothing resident; recipe-slots keeps its code + 2 curves
    res = {f["name"]: f["resident_bytes"] for f in m["features"]}
    assert res["tone-r3"] == res["snap15"] == 0 and res["recipe-slots"] > 2 * 8192


def test_build_is_deterministic():
    assert default().module == default().module


def test_options_change_the_build_and_are_recorded():
    a = link([F.make("recipe-slots")])
    b = link([F.make("recipe-slots", {"slot": "keep"})])
    assert a.module != b.module
    assert b.manifest["features"][0]["options"] == {"slot": "keep", "reseed": 0}
    with pytest.raises(ValueError):
        F.make("recipe-slots", {"slot": "9"})
    with pytest.raises(KeyError):
        F.make("tone-r3", {"bogus": "1"})


def test_same_label_names_in_two_features_do_not_collide():
    r = link([hook_feature("a", 1), hook_feature("b", 2)])
    assert "kf_a__handler" in r.symbols and "kf_b__handler" in r.symbols


def test_mangle_keeps_mnemonics_registers_strings():
    f = Feature("x", "t", "s", source='b:\n    b b\n    ldr r0, =b\n    .ascii "b"\n    mov sl, r0 @ b comment\n')
    out = mangle_source(f, lambda *_: "ERR")
    assert "kf_x__b:" in out and "    b kf_x__b" in out and "=kf_x__b" in out
    assert '.ascii "b"' in out and "mov sl, r0" in out and "@ b comment" in out


def test_cross_feature_symbol_needs_requires_and_export():
    lib = Feature("lib", "t", "s", source="func:\n    push {lr}\n    pop {r3}\n    bx r3\n", exports=["func"])
    user_src = "go:\n    push {lr}\n    bl $lib.func\n    pop {r3}\n    bx r3\n"
    user = Feature("user", "t", "s", source=user_src, init="go", requires=["lib"])
    r = link([user, lib])
    assert [f["name"] for f in r.manifest["features"]] == ["lib", "user"]
    with pytest.raises(LinkError, match="without `requires"):
        link([Feature("user", "t", "s", source=user_src, init="go"), lib])
    with pytest.raises(LinkError, match="not exported"):
        link([user, Feature("lib", "t", "s", source=lib.source)])


def test_core_symbols_resolve():
    f = Feature("v", "t", "s", source="get:\n    push {lr}\n    ldr r0, =$koma.version\n    pop {r3}\n    bx r3\n",
                init="get")
    r = link([f])
    assert len(parse(r.module)["relocs"]) == 1
    with pytest.raises(LinkError, match="unknown core symbol"):
        link([Feature("v", "t", "s", source="ldr r0, =$koma.nope\n", init=None)])
    assert set(CORE_EXPORTS) >= {"version", "manifest", "status"}


def test_relocations_are_resident_relative():
    f = Feature("r", "t", "s", source="get:\n    push {lr}\n    ldr r0, =tbl\n    pop {r3}\n    bx r3\n.ltorg\n"
                                       ".p2align 2\ntbl: .word tbl + 1, 0x1234\n", init="get")
    r = link([f])
    p = parse(r.module)
    o = p["header"]["resident"]
    tbl = r.symbols[ns("r") + "tbl"] - o
    vals = [struct.unpack_from(">I", p["body"], o + x)[0] for x in p["relocs"]]
    assert sorted(vals) == [tbl, tbl + 1]


def test_undefined_symbol_is_a_clear_error():
    with pytest.raises(LinkError, match="undefined symbol"):
        link([Feature("u", "t", "s", source="go:\n    push {lr}\n    bl nowhere\n    pop {r3}\n    bx r3\n", init="go")])


def test_size_limit():
    big = Feature("big", "t", "s", source="go:\n    bx lr\n", init="go", blobs={"blob": bytes(0x10000)})
    with pytest.raises(LinkError, match="over the loader"):
        link([big])


def test_data_only_without_publish_keeps_nothing_resident():
    r = link([patch_feature("a", 0x3C840)], publish=False)
    p = parse(r.module)
    assert p["header"]["resident_bytes"] == 0 and p["manifest"] is None
    assert "resident segment 0 B" in describe(r.module)


def test_chain_order_in_manifest():
    r = link([hook_feature("a", 1), hook_feature("b", 2)])
    hooks = {f["name"]: f["hooks"][0] for f in r.manifest["features"]}
    assert hooks["a"]["chain_next"] == "kf_b__handler + 1" and hooks["b"]["chain_next"] == "0x8ff0f"
    bl = [x for x in r.manifest["records"] if x["kind"] == "bl"]
    assert len(bl) == 1 and bl[0]["target"] == "kf_a__handler"


def test_cli_build_inspect_and_refusals(tmp_path, capsys):
    from koma.cli import main
    out = tmp_path / "k.grmod"
    assert main(["build", "--features", "snap15,recipe-slots", "--opt", "recipe-slots.slot=2", "-o", str(out)]) == 0
    side = json.loads((tmp_path / "k.grmod.json").read_text())
    assert side["module"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert side["features"][1]["options"]["slot"] == "2"
    assert main(["inspect", str(out)]) == 0
    assert "all match" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["build", "--features", "snap15", "-o", str(tmp_path / "QUICKVER.UP")])
    with pytest.raises(SystemExit):
        main(["build", "--features", "nope", "-o", str(tmp_path / "x")])
    with pytest.raises(SystemExit):
        main(["build", "--features", "snap15", "--opt", "tone-r3.x=1", "-o", str(tmp_path / "x")])
