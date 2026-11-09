#!/usr/bin/env python3
"""Build koma04w.grmod: the koma04 set (features.SETS["koma04"], static resident, telemetry.cp15=off) plus widefs and
widefs_init, for Koma 2 cameras with SDHC/SDXC cards. widefs is not registered in features/__init__.py, so its
Feature objects come from widefs_feature.make().

Init order: koma04 up to peaking, then [widefs, widefs_init], then settingsio-menu, settingsio, telemetry. widefs_init
must init before settingsio: on an SDHC card the card is only mounted by widefs_init, so settingsio's PARAM.CUR export
and PARAM.DAT import need it first. telemetry stays last.

Usage: KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/koma/sdhc/build_koma04w.py [-o OUT.grmod]
Writes OUT (default koma04w.grmod) and OUT.json (the build manifest).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))     # tools/

from koma.linker import link, STATIC_SPAN          # noqa: E402
from koma import features as KF                    # noqa: E402
from koma.sdhc import widefs_feature as W          # noqa: E402

RESIDENT = KF.SET_RESIDENT["koma04"]               # "static" -> 0x3dae00


def koma04_feats():
    return [KF.make(n, {"cp15": "off"} if n == "telemetry" else None) for n in KF.SETS["koma04"]]


def check(r, name):
    lay = r.manifest["layout"]
    assert lay["resident_mode"] == "static", lay
    end, (c0, c1) = int(lay["static_end"], 16), (int(x, 16) for x in lay["canary"])
    assert c0 == end and c1 == end + 16, ("canary", lay)
    assert c1 <= STATIC_SPAN[1] == 0x422800, ("span", lay)
    return lay


def overlaps(manifest):
    """Writing records (bl/ptr/data) must not share a byte; a widefs write must not land on bytes another feature
    only checks (and vice versa), since the check's before-bytes would then be stale for one of them."""
    recs = [(int(x["addr"], 16), int(x["addr"], 16) + x["len"], x["owner"], x["kind"]) for x in manifest["records"]]
    w = sorted(r for r in recs if r[3] != "check")
    bad = [(x, y) for x, y in zip(w, w[1:]) if y[0] < x[1]]
    wf = [r for r in recs if r[2].startswith("widefs")]
    other = [r for r in recs if not r[2].startswith("widefs")]
    bad += [(a, b) for a in wf for b in other if a[0] < b[1] and b[0] < a[1] and "check" not in (a[3], b[3]) or
            (a[0] < b[1] and b[0] < a[1] and (a[3] == "check") != (b[3] == "check"))]
    return len(recs), bad


def main():
    ap = argparse.ArgumentParser(description="Build koma04w.grmod (koma04 + widefs).")
    ap.add_argument("-o", "--output", type=Path, default=Path("koma04w.grmod"))
    out = ap.parse_args().output
    f = koma04_feats()
    i = [x.name for x in f].index("settingsio-menu")
    feats = f[:i] + W.make({}) + f[i:]
    r = link(feats, publish=True, resident=RESIDENT)
    for w in r.warnings:
        print("warning:", w, file=sys.stderr)
    lay = check(r, "koma04w")
    n, bad = overlaps(r.manifest)
    if bad:
        raise SystemExit(f"record overlaps: {bad[:5]}")
    out.write_bytes(r.module)
    Path(str(out) + ".json").write_text(json.dumps(r.manifest, indent=1) + "\n")
    m = r.manifest
    print(f"{out.name}  {m['module']['bytes']} B  sha256 {m['module']['sha256']}  {m['version_string']}")
    print(f"  resident {m['layout']['resident_bytes']} B at {lay['static_addr']}..{lay['static_end']}, canary "
          f"{lay['canary'][0]}..{lay['canary'][1]} (span ends {STATIC_SPAN[1]:#x}, headroom "
          f"{STATIC_SPAN[1] - int(lay['canary'][1], 16)} B); transient {m['layout']['transient_bytes']} B; "
          f"{len(m['records'])} records ({n} checked for overlap, 0 overlaps); pool peak {lay['pool_peak_bytes']} B")
    for ft in m["features"]:
        print(f"  {ft['name']:<15} {ft['version']:<5} resident {ft['resident_bytes']:>6} B  {ft['sha256'][:12]}")


if __name__ == "__main__":
    main()
