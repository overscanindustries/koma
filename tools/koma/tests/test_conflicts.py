"""Build-time conflict detection."""
from __future__ import annotations

import pytest
from _util import Check, Feature, Patch, Settings, fw, hook_feature, patch_feature

from koma.linker import LinkError, check_conflicts, link, order_features


def errs(*features, image_check=True):
    return check_conflicts(list(features), image_check=image_check)


def test_disjoint_features_have_no_conflicts():
    e, w = errs(patch_feature("a", 0x3C840), patch_feature("b", 0x3C850))
    assert e == [] and w == []


def test_overlapping_patches_across_features():
    e, _ = errs(patch_feature("a", 0x3C840, 8), patch_feature("b", 0x3C844, 8))
    assert any("a patch 0x3c840+8 overlaps b patch 0x3c844+8" in x for x in e)


def test_overlapping_patches_within_one_feature():
    f = Feature("a", "t", "x", patches=[Patch(0x3C840, fw.ram(0x3C840, 4), bytes(4)),
                                        Patch(0x3C842, fw.ram(0x3C842, 4), bytes(4))])
    e, _ = errs(f)
    assert any("overlaps its own" in x for x in e)


def test_patch_over_another_features_check():
    a = patch_feature("a", 0x9031A, 2)
    b = Feature("b", "t", "x", checks=[Check(0x9031A, fw.ram(0x9031A, 10))])
    e, _ = errs(a, b)
    assert any("b check 0x9031a depends on bytes that a patch" in x for x in e)


def test_own_check_may_cover_own_hook():
    f = hook_feature("a", 1, checks=[Check(0x96430, fw.ram(0x96430, 10))])
    assert errs(f) == ([], [])


def test_checks_that_disagree():
    a = Feature("a", "t", "x", checks=[Check(0x90350, fw.ram(0x90350, 4))])
    b = Feature("b", "t", "x", checks=[Check(0x90352, b"\0\0")])
    e, _ = errs(a, b, image_check=False)
    assert any("expect different bytes" in x for x in e)


def test_two_hooks_on_one_site_chain_not_conflict():
    assert errs(hook_feature("a", 1), hook_feature("b", 2)) == ([], [])


def test_hooks_disagreeing_on_original_target():
    b = hook_feature("b", 2, target=0x8FF10)
    e, _ = errs(hook_feature("a", 1), b, image_check=False)
    assert any("disagree on the original call target" in x for x in e)


def test_hook_vs_patch_on_same_bytes():
    e, _ = errs(hook_feature("a", 1), patch_feature("b", 0x96434, 4))
    assert any("overlaps" in x for x in e)


def test_hook_site_must_hold_the_declared_bl():
    e, _ = errs(hook_feature("a", 1, target=0x8FF10))
    assert any("image has no `bl 0x8ff10`" in x for x in e)


def test_before_bytes_must_match_image():
    f = Feature("a", "t", "x", patches=[Patch(0x3C840, b"\x00\x00", b"\x01\x01")])
    e, _ = errs(f)
    assert any("before-bytes differ from the 2.40 image" in x for x in e)


@pytest.mark.parametrize("addr", [0x57F80, 0x281EE, fw.PUBLISH + 2])
def test_reserved_ranges(addr):
    e, _ = errs(patch_feature("a", addr, 2))
    assert any("overlaps the" in x for x in e)


def test_settings_owned_overlap():
    a = Feature("a", "t", "x", settings=[Settings(0x1B800, 0x1B8B0)])
    b = Feature("b", "t", "x", settings=[Settings(0x1B8A0, 0x1B8C0)])
    e, _ = errs(a, b)
    assert any("both claim the same bytes" in x for x in e)


def test_settings_write_into_anothers_store():
    a = Feature("a", "t", "x", settings=[Settings(0x1B800, 0x1B8B0)])
    b = Feature("b", "t", "x", settings=[Settings(0x1B806, 0x1B807, owned=False)])
    e, _ = errs(a, b)
    assert any("writes 0x1b806..0x1b807 inside a's store" in x for x in e)


def test_settings_shared_stock_field_is_a_warning():
    a = Feature("a", "t", "x", settings=[Settings(0x1B030, 0x1B031, owned=False)])
    b = Feature("b", "t", "x", settings=[Settings(0x1B030, 0x1B031, owned=False)])
    e, w = errs(a, b)
    assert e == [] and any("last writer wins" in x for x in w)


def test_settings_outside_block():
    e, _ = errs(Feature("a", "t", "x", settings=[Settings(0x1BFF0, 0x1C000)]))
    assert any("not inside" in x for x in e)


def test_requires_conflicts_cycles_duplicates():
    with pytest.raises(LinkError, match="requires b"):
        order_features([Feature("a", "t", "x", requires=["b"])])
    with pytest.raises(LinkError, match="declares a conflict with b"):
        order_features([Feature("a", "t", "x", conflicts=["b"]), Feature("b", "t", "x")])
    with pytest.raises(LinkError, match="cycle"):
        order_features([Feature("a", "t", "x", requires=["b"]), Feature("b", "t", "x", requires=["a"])])
    with pytest.raises(LinkError, match="listed twice"):
        order_features([Feature("a", "t", "x"), Feature("a", "t", "x")])


def test_dependencies_are_ordered_first():
    out = order_features([Feature("a", "t", "x", requires=["b"]), Feature("b", "t", "x")])
    assert [f.name for f in out] == ["b", "a"]


def test_link_reports_every_problem_at_once():
    with pytest.raises(LinkError) as ei:
        link([patch_feature("a", 0x3C840, 8), patch_feature("b", 0x3C844, 8), patch_feature("c", 0x281EE, 2)])
    assert len(ei.value.problems) >= 2
