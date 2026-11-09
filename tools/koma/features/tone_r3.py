"""tone-r3: B&W contrast-5 tone table 0x199b30 (stock c5, 8 KiB) -> pinned curve sib-R3-18c3c4.
Data patch only; the transient body carries before + after and is freed after install."""
from __future__ import annotations

import hashlib

from ..feature import Feature, Patch
from . import _legacy

NAME = "tone-r3"
SUMMARY = "B&W contrast 5 tone table -> pinned R3 curve (data patch, 8 KiB)"
OPTIONS = {}
R3_SHA = "9aad2861"


def make(opts):
    s = _legacy.samples()
    table = s.r3_table()
    assert hashlib.sha256(table).hexdigest().startswith(R3_SHA)
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=opts,
        patches=[Patch(s.C5, s.ram(s.C5, 0x2000), table, "contrast-5 table (selector 0x22) := sib-R3-18c3c4")],
        smoke=smoke_checks)


def smoke_setup():
    return {0x1B030: 3, 0x1B197: 5}       # IMG SET B&W, B&W contrast 5 (the table this feature replaces)


def smoke_checks(obs):
    """The shot's first CC2 upload is the stock capture upload of selector 0x22 (before any hook): it must be R3."""
    ups = obs.cc2_uploads
    ok = bool(ups) and ups[0][0].startswith(R3_SHA)
    return [("stock CC2 capture upload carries R3", ok, f"uploads={[(h[:8], n) for h, n in ups]}")]
