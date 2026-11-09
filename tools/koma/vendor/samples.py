"""The two oldest sample modules' data (module-loader samples): the tone-r3 contrast-5 table and the snap15 patches.

Trimmed copy: only what features/tone_r3.py and features/snap15.py use. The stock-c5 column of the original CSV is not
shipped (it is Ricoh's table); the R3 table is pinned by sha256 instead.
"""
from __future__ import annotations

import csv
import hashlib
import struct

from .. import firmware as fw

LOAD = fw.LOAD
C5 = 0x199B30
CSV = fw.DATA / "tone-pinned.csv"
R3_SHA256_PREFIX = "9aad2861"          # = tone-r3 / candidate-2 table (recipe-slots build asserts the same)


def ram(addr, n):
    return fw.ram(addr, n)


def r3_table():
    rows = list(csv.DictReader(CSV.open()))
    vals = [int(r["sib-R3-18c3c4"]) for r in rows]
    assert len(vals) == 4096 and all(a <= b for a, b in zip(vals, vals[1:]))
    t = struct.pack(">4096H", *vals)
    assert hashlib.sha256(t).hexdigest().startswith(R3_SHA256_PREFIX)
    return t


SNAP_PATCHES = [(0x3C840, bytes.fromhex("2311682001db18c08a40"), bytes.fromhex("20aa0080300346c046c0")),
                (0x3C850, bytes.fromhex("2311682001db18c08a41"), bytes.fromhex("21aa0089310346c046c0"))]
