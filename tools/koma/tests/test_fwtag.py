"""fwtag: the real installer + transient init under the unit emulator, over every firmware tag slot a Koma package can
boot on (stock format, flash-1, build 2b, the 1.0 image) and slots it must leave alone."""
from __future__ import annotations

import pytest
from _util import fw

from koma.features import fwtag as T
from koma.linker import link, photo_tag
from koma.unitemu import Unit

_cache = {}


def build(**opts):
    key = tuple(sorted(opts.items()))
    if key not in _cache:
        _cache[key] = link([T.make(opts)])
    return _cache[key]


def run(slot: bytes, **opts):
    u = Unit(build(**opts).module, ram_edits={T.SLOT: slot})
    assert u.install() == 0 and u.callee_saved_ok
    return u


STOCK = b"Rev%4.4x\0"
SLOTS = {"stock": STOCK, "flash-1": b"Rev024M\0\0", "2b": b"240K2\0\0\0\0", "1.0": b"240K1.0\0\0"}


def test_stock_slot_bytes_are_the_firmware_format():
    assert fw.ram(T.SLOT, T.SLOT_LEN) == STOCK and fw.ram(0x112581, 3) == b"\0\0\0"


@pytest.mark.parametrize("name", sorted(SLOTS))
def test_known_slots_get_the_package_tag(name):
    u = run(SLOTS[name])
    assert photo_tag() == "240K1.0"
    assert u.read(T.SLOT, T.SLOT_LEN) == b"240K1.0\0\0"
    assert u.read(T.RESULT, 2) == b"T\x01"
    for a, n in u.outside_writes():
        assert T.SLOT <= a < T.SLOT + T.SLOT_LEN or T.RESULT <= a < T.RESULT + 2 or \
            fw.PUBLISH <= a < fw.PUBLISH + 8, hex(a)


def test_a_later_package_tags_its_own_version():
    u = run(SLOTS["1.0"], tag="240K1.1")
    assert u.read(T.SLOT, T.SLOT_LEN) == b"240K1.1\0\0"


@pytest.mark.parametrize("slot", [b"Koma-B2\0\0", b"240K1.0\0X", b"Rex%4.4x\0", b"240X1.0\0\0", b"\0" * 9])
def test_unknown_slots_are_left_alone(slot):
    u = run(slot)
    assert u.read(T.SLOT, T.SLOT_LEN) == slot and u.read(T.RESULT, 2) == b"T\x02"


@pytest.mark.parametrize("bad", ["", "240K1.0x", "24%K1.0", "240K\n.0", "240K1é"])
def test_tag_option_is_checked(bad):
    with pytest.raises((ValueError, UnicodeEncodeError)):
        T.make({"tag": bad})


def test_transient_and_default_build_records_no_option():
    r = build()
    f = r.manifest["features"][0]
    assert f["transient"] and f["resident_bytes"] == 0 and T.make({}).options == {}
