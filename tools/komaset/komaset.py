#!/usr/bin/env python3
"""komaset: convert the camera's raw 4 KB settings block to and from named plain text.

  python3 tools/komaset/komaset.py dump PARAM.CUR [-o settings.txt]
  python3 tools/komaset/komaset.py apply settings.txt -o PARAM.DAT [--base PARAM.CUR] [--force]
  python3 tools/komaset/komaset.py diff A.bin B.bin
  python3 tools/komaset/komaset.py merge --target target.cur --source source.cur -o PARAM.DAT
  python3 tools/komaset/komaset.py check PARAM.bin
  python3 tools/komaset/komaset.py info

Any block file is accepted by any command, whatever its name (PARAM.CUR, PARAM.SAV from SAVE TO CARD, PARAM.DAT).
Stdlib only. Reads inputs, writes only the -o output. See tools/komaset/README.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KOMA_OFF = 0x8B0
KOMA_MAGIC = b"KOMA"
CHECKSUM_ALGO = "u32-sum-of-u16be"


class MapError(Exception):
    pass


def _int(v):
    return int(v, 0) if isinstance(v, str) else int(v)


class Field:
    def __init__(self, d):
        self.name = d["name"]
        self.label = d.get("label", self.name)
        self.page = d.get("page", "misc")
        self.offset = _int(d["offset"])
        self.size = int(d.get("size", 1))
        self.type = d.get("type", "u8")
        self.range = d.get("range")
        self.persist = d.get("persist")
        self.body_specific = d.get("body_specific")
        self.confidence = d.get("confidence")
        self.values = {_int(k): v for k, v in (d.get("values") or {}).items()}
        bits = d.get("bits") or {}
        if isinstance(bits, list):
            bits = {str(i): n for i, n in enumerate(bits) if n}
        self.bits = {_int(k): v for k, v in bits.items()}
        if self.type not in ("enum", "u8", "u16", "u32", "i8", "bitfield", "bytes"):
            raise MapError(f"field {self.name}: unknown type {self.type}")
        self.rev_values = {str(v).strip().upper(): k for k, v in self.values.items()}
        self.rev_bits = {str(v).upper(): k for k, v in self.bits.items()}

    @property
    def end(self):
        return self.offset + self.size

    def raw(self, blk):
        return bytes(blk[self.offset:self.end])

    def _uint(self, blk):
        return int.from_bytes(self.raw(blk), "big")

    def render(self, blk):
        """Return (value_text, comment_text)."""
        r = self.raw(blk)
        if self.type == "bytes":
            return r.hex(" "), f"0x{self.offset:03x}"
        v = int.from_bytes(r, "big")
        raw = f"0x{v:0{self.size * 2}x}"
        c = f"0x{self.offset:03x} = {raw}"
        if self.type == "enum":
            return (self.values[v] if v in self.values else raw), c
        if self.type == "bitfield":
            names = [self.bits.get(b, f"BIT{b}") for b in range(self.size * 8) if v >> b & 1]
            return ("|".join(names) if names else "NONE"), c
        if self.type == "i8":
            return str(v - 256 if v > 127 else v), c
        if self.range and not (self.range[0] <= v <= self.range[1]):
            return raw, c  # out-of-range stored value (e.g. erased 0xff): keep it verbatim as hex
        return str(v), c

    def parse(self, text):
        """Return bytes for text, or raise ValueError."""
        t = text.strip()
        if self.type == "bytes":
            b = bytes.fromhex(t.replace(",", " "))
            if len(b) != self.size:
                raise ValueError(f"need {self.size} bytes, got {len(b)}")
            return b
        if self.type == "enum":
            if t.upper() in self.rev_values:
                v = self.rev_values[t.upper()]
            else:
                try:
                    v = int(t, 0)
                except ValueError:
                    raise ValueError(f"unknown enum label {t!r}")
                if not t.lower().startswith("0x"):
                    raise ValueError(f"unknown enum label {t!r} (raw values need 0x prefix)")
                if v >= 1 << (self.size * 8):
                    raise ValueError(f"raw value {t} does not fit in {self.size} byte(s)")
                # unknown raw values are accepted: dump emits them verbatim (e.g. erased 0xff)
        elif self.type == "bitfield":
            v = 0
            if t.upper() not in ("NONE", "0", ""):
                for p in t.split("|"):
                    p = p.strip().upper()
                    if p in self.rev_bits:
                        v |= 1 << self.rev_bits[p]
                    elif p.startswith("BIT") and p[3:].isdigit():
                        v |= 1 << int(p[3:])
                    else:
                        try:
                            v |= int(p, 0)
                        except ValueError:
                            raise ValueError(f"unknown flag {p!r}")
            if v >= 1 << (self.size * 8):
                raise ValueError("flags exceed field width")
        else:
            try:
                v = int(t, 0)
            except ValueError:
                raise ValueError(f"not an integer: {t!r}")
            if self.range and not t.lower().startswith("0x") and not (self.range[0] <= v <= self.range[1]):
                raise ValueError(f"{v} outside range {self.range[0]}..{self.range[1]}")
            if self.type == "i8":
                if not -128 <= v <= 127:
                    raise ValueError("outside i8")
                v &= 0xFF
            elif v < 0 or v >= 1 << (self.size * 8):
                raise ValueError(f"does not fit in {self.size} byte(s)")
        return v.to_bytes(self.size, "big")


class Map:
    def __init__(self, path):
        d = json.loads(Path(path).read_text())
        m = d["meta"]
        self.firmware = m.get("firmware", "?")
        self.base = _int(m.get("block_base", "0x1b000"))
        self.size = int(m.get("block_size", 4096))
        mg = m.get("magic", {"offset": 0, "bytes": "4d473148"})
        self.magic_off = _int(mg["offset"])
        self.magic = bytes.fromhex(mg["bytes"])
        cs = m.get("checksum", {})
        self.cs_algo = cs.get("algo", CHECKSUM_ALGO)
        if self.cs_algo != CHECKSUM_ALGO:
            raise MapError(f"unsupported checksum algo {self.cs_algo!r} (only {CHECKSUM_ALGO})")
        self.cs_range = tuple(cs.get("range", [0, self.size - 4]))
        self.cs_off = _int(cs.get("offset", self.size - 4))
        self.cs_size = int(cs.get("size", 4))
        self.cs_endian = cs.get("endian", "big")
        self.fields = [Field(f) for f in d.get("fields", [])]
        self.pages = []
        for f in self.fields:
            if f.page not in self.pages:
                self.pages.append(f.page)
        self.by_name = {}
        for f in self.fields:
            if f.name in self.by_name:
                raise MapError(f"duplicate field name {f.name}")
            self.by_name[f.name] = f
        self.exclude = [(_int(e["offset"]), int(e.get("size", 1)), e.get("reason", ""))
                        for e in d.get("exclude_on_merge", [])]

    def covered(self):
        """Set-free coverage: list of bool per byte for mapped fields + checksum."""
        cov = [False] * self.size
        for f in self.fields:
            for i in range(f.offset, min(f.end, self.size)):
                cov[i] = True
        for i in range(self.cs_off, min(self.cs_off + self.cs_size, self.size)):
            cov[i] = True
        return cov

    def unmapped_ranges(self, blk=None, other=None):
        """Ranges (start, end_exclusive) of unmapped bytes; if blk and other given, only differing ones."""
        cov = self.covered()
        out, s = [], None
        for i in range(self.size):
            hit = (not cov[i]) and (blk is None or blk[i] != other[i])
            if hit and s is None:
                s = i
            if not hit and s is not None:
                out.append((s, i))
                s = None
        if s is not None:
            out.append((s, self.size))
        return out


# ---- checksum -------------------------------------------------------------

def compute_checksum(blk, m):
    """Sum of big-endian u16 halfwords over meta.range, truncated to the checksum width."""
    lo, hi = m.cs_range
    seg = bytes(blk[lo:hi])
    t = sum(int.from_bytes(seg[i:i + 2], "big") for i in range(0, len(seg) - 1, 2))
    return t & ((1 << (8 * m.cs_size)) - 1)


def stored_checksum(blk, m):
    return int.from_bytes(blk[m.cs_off:m.cs_off + m.cs_size], m.cs_endian)


def set_checksum(blk, m):
    blk[m.cs_off:m.cs_off + m.cs_size] = compute_checksum(blk, m).to_bytes(m.cs_size, m.cs_endian)


def koma_info(blk):
    if bytes(blk[KOMA_OFF:KOMA_OFF + 4]) == KOMA_MAGIC:
        return blk[KOMA_OFF + 4]
    return None


KSIO_OFF = 0x8D0
KSIO_MAGIC = b"KSIO"
KSIO_TAG_OFF = 0x8D5
KSIO_TAG_LEN = 7


def ksio_tag(blk):
    """The 7-byte firmware tag from the KSIO sub-header (text), or None when the block has no KSIO magic."""
    if bytes(blk[KSIO_OFF:KSIO_OFF + 4]) == KSIO_MAGIC:
        return bytes(blk[KSIO_TAG_OFF:KSIO_TAG_OFF + KSIO_TAG_LEN]).decode("latin1")
    return None


def tag_problem(tgt, src):
    """(is_refusal, text) for the firmware tag rule, or None. Never a refusal: cameras can run the
    same Koma module on different firmware tags (Rev024M, 240K2), and the block layout is governed by the
    Koma settings version (0x8b4, checked separately) and the KSIO version, not the tag. Both KSIO and differing: a
    warning naming both tags (source first); the camera imports such a file with KSIO status 3. One KSIO: warn."""
    tt, ts = ksio_tag(tgt), ksio_tag(src)
    if tt is not None and ts is not None:
        if tt != ts:
            a, b = ts.rstrip("\0 "), tt.rstrip("\0 ")
            text = f"These cameras run different firmware tags ({a}, {b})."
            if koma_info(tgt) == koma_info(src):
                text += " Settings still copy; Koma version matches."
            return False, text
        return None
    if tt is not None or ts is not None:
        return False, "only one file has a KSIO header (" + ("target" if tt is not None else "source") + "), firmware tag not compared"
    return None


def load_block(path, m):
    b = Path(path).read_bytes()
    if len(b) != m.size:
        raise SystemExit(f"error: {path}: {len(b)} bytes, expected {m.size}")
    return bytearray(b)


def load_map(args):
    p = args.map or str(HERE / "settings-map.json")
    try:
        return Map(p)
    except FileNotFoundError:
        raise SystemExit(f"error: map not found: {p}")
    except (MapError, KeyError, ValueError) as e:
        raise SystemExit(f"error: bad map {p}: {e}")


# ---- dump -----------------------------------------------------------------

def render_text(blk, m):
    out = []
    comp, st = compute_checksum(blk, m), stored_checksum(blk, m)
    kv = koma_info(blk)
    out += ["[meta]",
            f"firmware = {m.firmware}",
            f"magic = {'ok' if bytes(blk[m.magic_off:m.magic_off + len(m.magic)]) == m.magic else 'BAD'}",
            f"checksum = {'ok' if comp == st else 'BAD'}   ; stored 0x{st:08x} computed 0x{comp:08x} ({m.cs_algo})",
            f"koma = {'absent' if kv is None else kv}",
            "; [meta] is informational and ignored by apply", ""]
    for page in m.pages:
        out.append(f"[{page}]")
        fs = [f for f in m.fields if f.page == page]
        w = max(len(f.name) for f in fs)
        for f in fs:
            v, c = f.render(blk)
            out.append(f"{f.name.ljust(w)} = {v}   ; {c}" if f.type != "bytes" else f"{f.name.ljust(w)} = {v}   ; {c}")
        out.append("")
    out.append("[raw]")
    out.append("; unmapped byte ranges, offsets relative to block base, end inclusive")
    for s, e in m.unmapped_ranges():
        for p in range(s, e, 16):
            q = min(p + 16, e)
            out.append(f"0x{p:03x}-0x{q - 1:03x} = {bytes(blk[p:q]).hex(' ')}")
    out.append("")
    return "\n".join(out)


def cmd_dump(args):
    m = load_map(args)
    blk = load_block(args.input, m)
    text = render_text(blk, m)
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


# ---- apply ----------------------------------------------------------------

def parse_text(text, m):
    """Return (field_values {name:(lineno,text)}, raw_ranges [(lineno,start,bytes)], errors)."""
    fields, raws, errs = {}, [], []
    section = None
    for n, line in enumerate(text.splitlines(), 1):
        s = line.split(";", 1)[0].strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip()
            continue
        if "=" not in s:
            errs.append(f"line {n}: expected 'name = value': {line.strip()}")
            continue
        k, v = (x.strip() for x in s.split("=", 1))
        if section == "meta":
            continue
        if section == "raw":
            try:
                a, _, b = k.partition("-")
                lo = int(a, 0)
                data = bytes.fromhex(v.replace(",", " "))
                if b and int(b, 0) - lo + 1 != len(data):
                    raise ValueError("range length does not match data length")
                if lo < 0 or lo + len(data) > m.size:
                    raise ValueError("outside block")
                raws.append((n, lo, data))
            except ValueError as e:
                errs.append(f"line {n}: bad raw range: {e}")
            continue
        if k not in m.by_name:
            errs.append(f"line {n}: unknown field {k!r}")
            continue
        fields[k] = (n, v)
    return fields, raws, errs


def cmd_apply(args):
    m = load_map(args)
    fields, raws, errs = parse_text(Path(args.input).read_text(), m)
    blk = load_block(args.base, m) if args.base else bytearray(b"\xff" * m.size)
    for _, lo, data in raws:
        blk[lo:lo + len(data)] = data
    for k, (n, v) in fields.items():
        f = m.by_name[k]
        try:
            blk[f.offset:f.end] = f.parse(v)
        except ValueError as e:
            errs.append(f"line {n}: {k} = {v}: {e}")
    if not args.base and bytes(blk[m.magic_off:m.magic_off + len(m.magic)]) != m.magic:
        errs.append("no --base and the text has no [raw] magic bytes; result would lack MG1H")
    if errs:
        print(("warning" if args.force else "error") + ": problems in " + args.input, file=sys.stderr)
        for e in errs:
            print("  " + e, file=sys.stderr)
        if not args.force:
            print("refusing to write (use --force to override; offending values are skipped)", file=sys.stderr)
            return 2
    set_checksum(blk, m)
    Path(args.output).write_bytes(bytes(blk))
    print(f"wrote {args.output} ({len(blk)} B, checksum 0x{stored_checksum(blk, m):08x})")
    return 0


# ---- diff -----------------------------------------------------------------

def diff_blocks(a, b, m):
    rows = []
    for f in m.fields:
        if f.raw(a) != f.raw(b):
            rows.append((f, f.render(a)[0], f.render(b)[0]))
    ranges = m.unmapped_ranges(a, b)
    return rows, ranges


def cmd_diff(args):
    m = load_map(args)
    a, b = load_block(args.a, m), load_block(args.b, m)
    rows, ranges = diff_blocks(a, b, m)
    cs = bytes(a[m.cs_off:m.cs_off + 2]) != bytes(b[m.cs_off:m.cs_off + 2])
    if not rows and not ranges and not cs:
        print("identical")
        return 0
    if rows:
        print(f"{'field':<20} {'page':<10} {'A':<18} {'B':<18} offset")
        for f, va, vb in rows:
            print(f"{f.name:<20} {f.page:<10} {va:<18} {vb:<18} 0x{f.offset:03x}")
    if ranges:
        print("unmapped bytes differing:")
        for s, e in ranges:
            print(f"  0x{s:03x}-0x{e - 1:03x} ({e - s} B)  A={bytes(a[s:min(e, s + 8)]).hex(' ')}"
                  f"{'...' if e - s > 8 else ''}  B={bytes(b[s:min(e, s + 8)]).hex(' ')}{'...' if e - s > 8 else ''}")
    if cs:
        print(f"checksum differs: A=0x{stored_checksum(a, m):08x} B=0x{stored_checksum(b, m):08x}")
    return 1


# ---- merge ----------------------------------------------------------------

def cmd_merge(args):
    m = load_map(args)
    t, s = load_block(args.target, m), load_block(args.source, m)
    problems = []
    for nm, blk in (("target", t), ("source", s)):
        if bytes(blk[m.magic_off:m.magic_off + len(m.magic)]) != m.magic:
            problems.append(f"{nm} magic is not {m.magic.decode('latin1')}")
    kt, ks = koma_info(t), koma_info(s)
    if kt != ks:
        problems.append(f"Koma versions differ (target {kt}, source {ks})")
    tp = tag_problem(t, s)          # a warning only: a differing tag never needs --force (the Koma version above does)
    if tp:
        print("warning: " + tp[1], file=sys.stderr)
    if problems:
        for p in problems:
            print(("warning: " if args.force else "error: ") + p, file=sys.stderr)
        if not args.force:
            print("refusing (use --force)", file=sys.stderr)
            return 2
    skip = [False] * m.size
    for i in range(m.magic_off, m.magic_off + len(m.magic)):
        skip[i] = True
    for i in range(m.cs_off, m.cs_off + 2):
        skip[i] = True
    if not args.include_excluded:
        for off, sz, _ in m.exclude:
            for i in range(off, min(off + sz, m.size)):
                skip[i] = True
    out = bytearray(t)
    copied = 0
    for i in range(m.size):
        if not skip[i] and out[i] != s[i]:
            out[i] = s[i]
            copied += 1
    set_checksum(out, m)
    Path(args.output).write_bytes(bytes(out))
    print(f"wrote {args.output}: {copied} byte(s) changed from target")
    print("skipped: magic, checksum (recomputed)")
    if args.include_excluded:
        print("skipped: (exclusions disabled by --include-excluded)")
    for off, sz, why in ([] if args.include_excluded else m.exclude):
        differs = t[off:off + sz] != s[off:off + sz]
        print(f"skipped: 0x{off:03x} +{sz} {why}{'' if differs else ' (already equal)'}")
    return 0


# ---- check / info ---------------------------------------------------------

def cmd_check(args):
    m = load_map(args)
    b = Path(args.input).read_bytes()
    bad = 0
    size_ok = len(b) == m.size
    print(f"size: {len(b)} {'ok' if size_ok else 'BAD (expected ' + str(m.size) + ')'}")
    if not size_ok:
        return 1
    blk = bytearray(b)
    mg = bytes(blk[m.magic_off:m.magic_off + len(m.magic)])
    mok = mg == m.magic
    print(f"magic: {mg.hex()} {'ok' if mok else 'BAD'}")
    comp, st = compute_checksum(blk, m), stored_checksum(blk, m)
    cok = comp == st
    print(f"checksum: stored 0x{st:08x} computed 0x{comp:08x} {'ok' if cok else 'BAD'}")
    kv = koma_info(blk)
    print(f"koma: {'absent' if kv is None else 'version ' + str(kv)}")
    return 0 if (mok and cok) else 1


def cmd_info(args):
    m = load_map(args)
    cov = m.covered()
    n = sum(cov)
    print(f"firmware {m.firmware}, block base 0x{m.base:x}, {m.size} B")
    print(f"checksum {m.cs_algo} range {m.cs_range} at 0x{m.cs_off:x} size {m.cs_size} {m.cs_endian}")
    print(f"fields: {len(m.fields)} on {len(m.pages)} page(s)")
    for p in m.pages:
        print(f"  {p}: {sum(1 for f in m.fields if f.page == p)}")
    by_type = {}
    for f in m.fields:
        by_type[f.type] = by_type.get(f.type, 0) + 1
    print("types: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    conf = {}
    for f in m.fields:
        conf[f.confidence or "?"] = conf.get(f.confidence or "?", 0) + 1
    print("confidence: " + ", ".join(f"{k}={v}" for k, v in sorted(conf.items())))
    print(f"mapped bytes (incl. checksum): {n}/{m.size} ({100 * n / m.size:.1f}%)")
    print(f"unmapped ranges: {len(m.unmapped_ranges())}")
    print(f"exclude_on_merge: {len(m.exclude)} range(s)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", help="settings map JSON (default: settings-map.json next to this script)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        p = sub.add_parser(name, **kw)
        p.set_defaults(fn=fn)
        # allow the global options after the subcommand too
        p.add_argument("--map", dest="map", default=argparse.SUPPRESS)
        return p

    p = add("dump", cmd_dump, help="binary -> text")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p = add("apply", cmd_apply, help="text -> binary")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--base")
    p.add_argument("--force", action="store_true")
    p = add("diff", cmd_diff, help="compare two blocks")
    p.add_argument("a")
    p.add_argument("b")
    p = add("merge", cmd_merge, help="copy source settings onto target dump")
    p.add_argument("--target", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--include-excluded", action="store_true")
    p.add_argument("--force", action="store_true")
    p = add("check", cmd_check, help="validate a block")
    p.add_argument("input")
    add("info", cmd_info, help="map coverage stats")

    a = ap.parse_args(argv)
    for k in ("map",):
        if not hasattr(a, k):
            setattr(a, k, None)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
