#!/usr/bin/env python3
"""Build the Koma 2 firmware image (tag 240K2) from your own stock 2.40 update file. Offline only.

Image = Koma 1 (stock qrflect3 + card-module loader + hook + Rev024M, built in memory by tools/koma/loader.py) + the
SDHC driver (sdhc_patch.py: cave at 0x57274, group-B chunk copies at 0x575b4/0x57740, BL redirects, A4 branch edit,
USB READ CAPACITY), the /IROM loader fallback, marker Rev024M -> 240K2, and the container checksum recomputed. Every
edit's before-bytes are asserted against the Koma 1 payload, and against the stock payload for every site Koma 1 did
not change. The output is written only if its sha256 is the known Koma 2 build 2b image.

Usage: tools/koma/firmware2/build.py <qrflect3 | gr-240.exe | zip> -o QRFLECT3 [--report build.json]
Needs LLVM 20 (KOMA_LLVM, Homebrew llvm@20 or PATH), as tools/koma does.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))     # tools/

from koma import loader as K1                      # noqa: E402
from koma.firmware2 import sdhc_patch as P         # noqa: E402
from koma.vendor.asm_legacy import disasm          # noqa: E402

LOAD, HEADER_SIZE = K1.LOAD, K1.HEADER_SIZE
NAME = "koma-fw2-240"
OUTPUT_SHA256 = "75425a70810f3c7b51554bb2941be30116f3f074e8e30afdbbda778095fa9680"   # Koma 2 build 2b


def changed_ranges(left: bytes, right: bytes) -> list[dict]:
    out, start = [], None
    for i in range(len(left) + 1):
        diff = i < len(left) and left[i] != right[i]
        if diff and start is None:
            start = i
        elif not diff and start is not None:
            out.append(dict(file_offset=start, length=i - start))
            start = None
    return out


def build(stock: bytes):
    """Return (image, report) for verified stock 2.40 qrflect3 bytes."""
    koma1 = K1.build(stock)
    stock_pl, k1_pl = stock[HEADER_SIZE:], koma1[HEADER_SIZE:]
    edits, syms = P.edits(k1_pl)
    payload = bytearray(k1_pl)
    rows = []
    for addr, before, after, why in edits:
        off = addr - LOAD
        assert len(before) == len(after)
        assert bytes(payload[off:off + len(before)]) == before, hex(addr)
        stock_same = bytes(stock_pl[off:off + len(before)]) == before
        if addr != P.MARKER[0] and not (0x57F74 <= addr < 0x58198):   # marker and loader cave: Koma 1 bytes
            assert stock_same, f"{addr:#x}: Koma 1 bytes differ from stock"
        payload[off:off + len(after)] = after
        big = len(after) > 16
        rows.append(dict(runtime=hex(addr), file_offset=hex(off + HEADER_SIZE), length=len(after),
                         before=("sha256:" + hashlib.sha256(before).hexdigest()) if big else before.hex(),
                         after=("sha256:" + hashlib.sha256(after).hexdigest()) if big else after.hex(),
                         reason=why))
    header = bytearray(stock[:HEADER_SIZE])
    struct.pack_into(">I", header, 0x30, K1.checksum(bytes(payload), LOAD))
    image = bytes(header) + bytes(payload)
    K1._header(image)                                  # magic, sizes and checksum verify
    spans = []
    for lo, n in sorted([(int(r["file_offset"], 16), r["length"]) for r in rows] + [(0x30, 4)]):
        if spans and spans[-1][0] + spans[-1][1] >= lo:
            spans[-1] = (spans[-1][0], max(spans[-1][0] + spans[-1][1], lo + n) - spans[-1][0])
        else:
            spans.append((lo, n))
    diff = changed_ranges(koma1, image)
    stray = [d for d in diff if not any(lo <= d["file_offset"] and d["file_offset"] + d["length"] <= lo + n
                                        for lo, n in spans)]
    assert not stray, f"bytes changed outside the declared edits: {stray[:5]}"
    cave_code, _ = P.build_code()
    report = dict(name=NAME, image_sha256=hashlib.sha256(image).hexdigest(), bytes=len(image),
                  base_sha256=K1.OUTPUT_SHA256, stock_sha256=K1.STOCK_SHA256,
                  checksum=hex(struct.unpack_from(">I", image, 0x30)[0]), firmware_tag="240K2",
                  cave=dict(start=hex(P.CAVE), end_exclusive=hex(P.CAVE + len(cave_code)), limit=hex(P.CAVE_END),
                            symbols={k: hex(v) for k, v in sorted(syms.items(), key=lambda kv: str(kv[1]))
                                     if not k.startswith(("CALL_", "_")) and isinstance(v, int)},
                            disassembly=disasm(cave_code, P.CAVE)),
                  edits=rows, diff_vs_koma1_runs=len(diff), diff_vs_koma1_bytes=sum(d["length"] for d in diff))
    return image, report


def main():
    ap = argparse.ArgumentParser(description="Build the Koma 2 firmware image (240K2) from stock 2.40.")
    ap.add_argument("stock", type=Path, help="stock qrflect3, Ricoh's gr-240.exe, or a zip holding qrflect3")
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--report", type=Path, help="also write a JSON report of every edit")
    a = ap.parse_args()
    try:
        image, report = build(K1.read_stock(a.stock))
    except K1.LoaderError as e:
        print(f"refused\n{e}", file=sys.stderr)
        return 2
    sha = report["image_sha256"]
    if sha != OUTPUT_SHA256:
        print(f"built image sha256 {sha} != expected {OUTPUT_SHA256}; not writing it", file=sys.stderr)
        return 1
    if a.output.exists() and a.output.read_bytes() != image:
        print(f"{a.output} exists with different bytes; remove it or choose another name", file=sys.stderr)
        return 2
    a.output.write_bytes(image)
    if a.report:
        a.report.write_text(json.dumps(report, indent=1) + "\n")
    print(f"{a.output}  sha256 {sha}  edits {len(report['edits'])}  "
          f"diff vs Koma 1: {report['diff_vs_koma1_runs']} runs, {report['diff_vs_koma1_bytes']} bytes")
    print("next: copy to the card root as QRFLECT3 and run the keyed power-on (docs/install.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
