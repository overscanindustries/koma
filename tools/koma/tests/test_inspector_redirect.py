"""inspect must not crash on a bl record whose stock bytes are not a bl (e.g. tether's 0x4fe44 redirect)."""
from __future__ import annotations

from koma import inspector
from koma.asm import thumb_bl


def test_describe_non_bl_redirect():
    addr = 0x4FE44
    rec = {"kind": "bl", "addr": addr, "len": 4, "before": b"\x46\xc0\x46\xc0", "target_offset": 0x10,
           "after": thumb_bl(addr, addr + 0x100)}
    p = {"container": {"bytes": 0, "sha256": "x", "entry": 0}, "header": {"format": 1, "publish": 0, "resident_bytes": 0,
         "resident": 0}, "manifest": None, "body": b"", "relocs": [], "inits": [], "records": [rec]}
    saved = inspector.parse, inspector.verify_against_image
    inspector.parse, inspector.verify_against_image = (lambda m: p), (lambda p: [])
    try:
        _check(rec, addr)
    finally:
        inspector.parse, inspector.verify_against_image = saved


def _check(rec, addr):
    out = inspector.describe(b"")
    assert "0x004fe44" in out and "not a bl: 46c046c0" in out and "resident+0x10" in out
    # bl case unchanged
    rec["before"] = thumb_bl(addr, 0x8FF0E)
    assert "(stock: bl 0x8ff0e)" in inspector.describe(b"")
