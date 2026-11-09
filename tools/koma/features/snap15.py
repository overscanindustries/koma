"""snap15: SNAP routine 0x3c840/0x3c850 loads of calibration 0x08010892 -> constant 683 (1.5 m). .text data patch."""
from __future__ import annotations

from ..feature import Feature, Patch
from . import _legacy

NAME = "snap15"
SUMMARY = "SNAP focus distance 2.5 m -> 1.5 m (.text patch, 20 B)"
OPTIONS = {}


def make(opts):
    s = _legacy.samples()
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=opts,
        patches=[Patch(a, b, n, w) for (a, b, n), w in zip(
            s.SNAP_PATCHES, ("ldrh 0x08010892 -> movs/lsls/adds r0 = 683", "ldrh 0x08010892 -> movs/lsls/adds r1 = 683"))])
