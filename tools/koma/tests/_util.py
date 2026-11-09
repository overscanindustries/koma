"""Shared helpers for the Koma tests (synthetic features over real 2.40 bytes)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # tools/

from koma import firmware as fw   # noqa: E402
from koma.feature import Check, Feature, Hook, Patch, Settings   # noqa: E402

LOG = 0x7F0400          # unit-emu scratch: handlers append their id here (byte 0 = count)
SITE, TARGET = 0x96436, 0x8FF0E


def patch_feature(name, addr, n=4, after=None, **kw):
    before = fw.ram(addr, n)
    return Feature(name=name, version="t", summary="test", patches=[Patch(addr, before, after or bytes(n))], **kw)


def hook_feature(name, ident, site=SITE, target=TARGET, extra_src="", **kw):
    """A hook handler that logs `ident` at LOG, then calls next with the original r0, then returns."""
    src = f"""
handler:
    push {{r4, lr}}
    movs r4, r0
    ldr r2, =0x{LOG:x}
    ldrb r3, [r2, #0]
    adds r3, #1
    strb r3, [r2, #0]
    movs r1, #{ident}
    strb r1, [r2, r3]
    movs r0, r4
    ldr r3, =next
    bl call
    pop {{r4}}
    pop {{r3}}
    bx r3
call:
    bx r3
{extra_src}
"""
    return Feature(name=name, version="t", summary="test", source=src,
                   hooks=[Hook(site, target, "handler", "next")], **kw)


__all__ = ["Check", "Feature", "Hook", "Patch", "Settings", "fw", "patch_feature", "hook_feature", "LOG", "SITE",
           "TARGET"]
