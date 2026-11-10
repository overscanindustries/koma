#!/usr/bin/env python3
"""verify_session: read a copy of a camera's card root after a hardware session and say what happened.

  python3 tools/komaset/verify_session.py <card-root-dir> [--expect-tag Rev024M] [--baseline PARAM.CUR]

Reads only the directory it is given (never writes). Stdlib only. Exit code: 0 when no section needs
ATTENTION and KOMALOG.TXT / PARAM.CUR are present; 1 when any section is ATTENTION; 2 when nothing is
ATTENTION but KOMALOG.TXT or PARAM.CUR is MISSING.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import komaset as K  # noqa: E402

POOL_EMU, POOL_HW_01, POOL_HW_V04, POOL_MIN_SHIP = 1888, 6656, 3904, 1500
LOG_HEADER, LOG_TRAILER = "KOMA TLM V1", "END"
STAGES = {1: "stage 1 only (generated-code stage did not finish)", 2: "stage 2 only (CP15 stage did not finish)",
          3: "all stages complete"}
STATUS = {0: "NONE (no import this boot)", 1: "OK (import applied)", 2: "BAD (import refused, see PARAM.BAD)",
          3: "OK (import applied; the settings file came from a camera with a different firmware tag)"}
OK, ATT, MISS = "OK", "ATTENTION", "MISSING"


def find(root, name):
    """Case-insensitive lookup of a file in the card root (FAT upper-cases names)."""
    for p in sorted(root.iterdir()):
        if p.name.upper() == name.upper() and p.is_file():
            return p
    return None


def parse_log(text):
    f = {}
    for line in text.splitlines():
        if len(line) >= 14 and line[5:6] == "=" and line[:5].strip().isalnum():
            v = line[6:14]
            try:
                f[line[:5].strip()] = None if v == "--------" else int(v, 16)
            except ValueError:
                f[line[:5].strip()] = None
    return f


def section_log(root, out):
    p = find(root, "KOMALOG.TXT")
    if not p:
        out.append("KOMALOG.TXT: not on the card. The loader never ran the telemetry init (no GRMOD.BIN,")
        out.append("  wrong card, or the camera hung before it), or the card was write-protected.")
        return MISS
    data = p.read_bytes()
    text = data.decode("ascii", "replace")
    f = parse_log(text)
    header = text.startswith(LOG_HEADER)
    out.append(f"KOMALOG.TXT: present, {len(data)} B (full record is 346 B), header {'ok' if header else 'MISSING'}, "
               f"trailer {'ok' if text.rstrip().endswith(LOG_TRAILER) else 'missing'}")
    verdict = OK
    if not header:
        verdict = ATT
    b = f.get("BUILD")
    out.append(f"  build id: {'--' if b is None else f'{b:08X}'}")
    st = f.get("STAGE")
    out.append(f"  status: STAGE={'--' if st is None else st}: {STAGES.get(st, 'no stage completed (log partly written?)')}")
    if st != 3:
        verdict = ATT
    pool = f.get("POOL")
    if pool is None:
        out.append("  POOL: not recorded")
        verdict = ATT
    else:
        if pool == POOL_EMU:
            cmp_ = "equals the emulator figure for koma04 (1,888 B)"
        elif pool == POOL_HW_01:
            cmp_ = "equals the 0.1-bundle hardware figure (6,656 B)"
        elif pool == POOL_HW_V04:
            cmp_ = "equals the v04 hardware figure (3,904 B)"
        else:
            cmp_ = (f"{pool - POOL_EMU:+,} B vs the emulator figure 1,888 B, "
                    f"{pool - POOL_HW_01:+,} B vs the 0.1-bundle hardware figure 6,656 B")
        ship = pool > POOL_MIN_SHIP
        out.append(f"  POOL: {pool:,} B ({cmp_}); "
                   + ("above 1,500 B: the mode rows can ship" if ship else "NOT above 1,500 B: the mode rows cannot ship"))
        if not ship:
            verdict = ATT
    return verdict


def section_cur(root, out, expect_tag, baseline):
    p = find(root, "PARAM.CUR")
    if not p:
        out.append("PARAM.CUR: not on the card (the settings-io feature did not run, or the card was not writable).")
        return MISS, None
    m = K.Map(HERE / "settings-map.json")
    b = p.read_bytes()
    verdict = OK
    out.append(f"PARAM.CUR: present, {len(b)} B (must be {m.size})")
    if len(b) != m.size:
        out.append("  wrong size")
        return ATT, None
    blk = bytearray(b)
    mg = bytes(blk[m.magic_off:m.magic_off + len(m.magic)])
    mok = mg == m.magic
    comp, sto = K.compute_checksum(blk, m), K.stored_checksum(blk, m)
    out.append(f"  magic: {mg.hex()} {'ok' if mok else 'BAD'}")
    out.append(f"  checksum: stored 0x{sto:08x}, computed 0x{comp:08x} {'ok' if comp == sto else 'BAD'}"
               + ("" if comp == sto else " (a checksum mismatch right after an import boot is expected until a normal power-off save)"))
    if not mok or comp != sto:
        verdict = ATT
    if bytes(blk[K.KSIO_OFF:K.KSIO_OFF + 4]) == K.KSIO_MAGIC:
        tag = K.ksio_tag(blk)
        ver = blk[K.KSIO_OFF + 4]
        sb = blk[0x8DC]
        out.append(f"  KSIO: magic ok, version {ver}, firmware tag {tag!r}, import status {sb} = {STATUS.get(sb, 'unexpected')}")
        if expect_tag and tag != expect_tag:
            out.append(f"  tag mismatch: expected {expect_tag!r}")
            verdict = ATT
        if sb == 2 or sb not in STATUS:
            verdict = ATT
    else:
        out.append("  KSIO: magic absent (the block was not written by the settings-io feature)")
        verdict = ATT
    kv = K.koma_info(blk)
    out.append(f"  koma header: {'absent' if kv is None else 'version ' + str(kv)}")
    if baseline:
        bp = Path(baseline)
        try:
            base = K.load_block(bp, m)
        except (SystemExit, OSError) as e:
            out.append(f"  baseline {baseline}: unreadable ({e})")
            return ATT, bytes(blk)
        rows, ranges = K.diff_blocks(blk, base, m)
        cs = K.stored_checksum(blk, m) != K.stored_checksum(base, m)
        out.append(f"  diff vs baseline {bp.name}: {len(rows)} named field(s), {len(ranges)} unmapped range(s) differ"
                   + (", checksum differs" if cs else ""))
        for f, va, vb in rows[:40]:
            out.append(f"    {f.name} (0x{f.offset:03x}): card={va} baseline={vb}")
        if len(rows) > 40:
            out.append(f"    ... {len(rows) - 40} more")
        for s, e in ranges[:20]:
            out.append(f"    unmapped 0x{s:03x}-0x{e - 1:03x} ({e - s} B)")
    return verdict, bytes(blk)


def section_import(root, out):
    dat, ok, bad = find(root, "PARAM.DAT"), find(root, "PARAM.OK"), find(root, "PARAM.BAD")
    verdict = OK
    if dat is None:
        out.append("PARAM.DAT: absent (deleted: the one-shot remove worked, or none was staged)")
    else:
        d = dat.read_bytes()
        if len(d) == 4 and d == b"DONE":
            out.append("PARAM.DAT: present as the 4-byte DONE marker (remove did nothing, create truncated: consumed)")
        elif len(d) == 4096 and d[:4] == b"DONE":
            out.append("PARAM.DAT: 4096 B starting DONE (create did not truncate; the guard ignores it) - a finding")
            verdict = ATT
        elif len(d) == 4096:
            out.append("PARAM.DAT: a full 4096 B block, NOT consumed. Either no import ran, or the card is write-protected "
                       "and it will re-import every boot: take it off the card.")
            verdict = ATT
        else:
            out.append(f"PARAM.DAT: unexpected {len(d)} B - a finding")
            verdict = ATT
    if ok:
        out.append(f"PARAM.OK: present ({ok.stat().st_size} B)")
    else:
        out.append("PARAM.OK: absent")
    if bad:
        reason = bad.read_bytes().decode("ascii", "replace").strip()
        out.append(f"PARAM.BAD: present ({bad.stat().st_size} B), reason: {reason or '(empty)'}")
        verdict = ATT
    else:
        out.append("PARAM.BAD: absent")
    if not ok and not bad and dat is None:
        out.append("  no import marker and no staged file: no import was attempted.")
        verdict = MISS
    elif not ok and not bad:
        if verdict == OK:
            verdict = ATT
        out.append("  PARAM.DAT was staged but neither PARAM.OK nor PARAM.BAD exists: the import did not complete.")
    return verdict


def _metadata():
    try:
        spec = importlib.util.spec_from_file_location("_vs_meta", HERE.parent / "spare_test" / "metadata.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def section_jpeg(root, out, expect_tag):
    dcim = find_dir(root, "DCIM")
    if not dcim:
        out.append("DCIM: not on the card")
        return MISS
    files = [p for p in dcim.rglob("*") if p.is_file() and p.suffix.upper() in (".JPG", ".JPEG")]
    if not files:
        out.append("DCIM: no JPEGs")
        return MISS
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    meta = _metadata()
    out.append(f"DCIM: {len(files)} JPEG(s); newest 3:")
    verdict = OK
    for p in files[:3]:
        rel = p.relative_to(root)
        if meta is None:
            out.append(f"  {rel}: MakerNote reader tools/spare_test/metadata.py could not be loaded, firmware tag skipped")
            continue
        try:
            rev = meta.read(p).get("rev")
        except Exception as e:  # noqa: BLE001
            out.append(f"  {rel}: no readable MakerNote ({e})")
            continue
        line = f"  {rel}: MakerNote firmware tag {rev!r}"
        if expect_tag and rev != expect_tag:
            line += f" (expected {expect_tag!r})"
            verdict = ATT
        out.append(line)
    return verdict


def find_dir(root, name):
    for p in root.iterdir():
        if p.name.upper() == name.upper() and p.is_dir():
            return p
    return None


def run(root, expect_tag=None, baseline=None):
    root = Path(root)
    out, verdicts = [], []
    if not root.is_dir():
        return [f"error: {root} is not a directory"], [("input", ATT)]
    for title, fn in (("KOMALOG", lambda: section_log(root, out)),
                      ("PARAM.CUR", lambda: section_cur(root, out, expect_tag, baseline)[0]),
                      ("Import files", lambda: section_import(root, out)),
                      ("JPEGs", lambda: section_jpeg(root, out, expect_tag))):
        out.append(f"== {title} ==")
        v = fn()
        out.append(f"-> {title}: {v}")
        out.append("")
        verdicts.append((title, v))
    return out, verdicts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("card_root")
    ap.add_argument("--expect-tag", help="firmware tag the KSIO header / JPEG MakerNote should carry (e.g. Rev024M)")
    ap.add_argument("--baseline", help="a PARAM.CUR to diff the card's PARAM.CUR against")
    a = ap.parse_args(argv)
    out, verdicts = run(a.card_root, a.expect_tag, a.baseline)
    print("\n".join(out))
    summary = "; ".join(f"{t} {v}" for t, v in verdicts)
    print(f"SUMMARY: {summary}")
    vs = {v for _, v in verdicts}
    if ATT in vs:
        return 1
    if any(v == MISS and t in ("KOMALOG", "PARAM.CUR") for t, v in verdicts):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
