"""Parse a Koma GRMOD back into its parts (container, boot header, records, resident manifest, relocations), and
check every before-byte against the 2.40 image. Pure host code: needs no manifest JSON."""
from __future__ import annotations

import hashlib
import struct

from . import firmware as fw
from .asm import bl_target, disasm, thumb_bl
from .linker import (BODY_MAGIC, BL_KINDS, CANARY_LEN, CANARY_MAGIC, FEAT_ENTRY, FLAG_STATIC, K_BL, K_BLHI, K_CHECK,
                     K_DATA, K_PTR,
                     KIND_NAMES, MANIFEST_HDR, MANIFEST_MAGIC, VERSION_LEN, grmod)


class InspectError(Exception):
    pass


def _cstr(b):
    return b.split(b"\0", 1)[0].decode("ascii", "replace")


def parse(module: bytes) -> dict:
    c = grmod.parse_module(module)
    if not c["ok"]:
        raise InspectError(f"not a valid GRMOD container: {c['reason']}")
    body = module[0x14:]
    if body[:4] != BODY_MAGIC:
        raise InspectError("valid GRMOD, but not a Koma module (no KMOD header)")
    fmt, flags, o_recs, o_relocs, o_inits, o_res, res_len, _ = struct.unpack_from(">HHIIIIII", body, 4)
    recs, off = [], o_recs
    while True:
        addr, = struct.unpack_from(">I", body, off)
        if addr == 0:
            break
        kind, n, size = struct.unpack_from(">HHI", body, off + 4)
        before = body[off + 12:off + 12 + n]
        pay = off + 12 + n + (-n % 4)
        r = dict(kind=KIND_NAMES.get(kind, f"?{kind}"), addr=addr, len=n, before=before, record_offset=off)
        if kind == K_DATA:
            r["after"] = body[pay:pay + n]
        elif kind in (K_BL, K_BLHI, K_PTR):
            r["target_offset"] = struct.unpack_from(">I", body, pay)[0]
        recs.append(r)
        if size <= 0:
            raise InspectError(f"record at {off:#x}: bad size")
        off += size
    ninit, = struct.unpack_from(">I", body, o_inits)
    inits = list(struct.unpack_from(f">{ninit}I", body, o_inits + 4))
    nrel, = struct.unpack_from(">I", body, o_relocs)
    relocs = list(struct.unpack_from(f">{nrel}I", body, o_relocs + 4))
    static_addr = None
    if flags & FLAG_STATIC:
        magic, static_addr = struct.unpack_from(">II", body, o_relocs + 4 + 4 * nrel)
        if magic != CANARY_MAGIC:
            raise InspectError("static flag set but no 'KSTA' address after the relocation table")
    res = body[o_res:o_res + res_len]
    manifest = None
    if res_len and res[:4] == MANIFEST_MAGIC:
        mfmt, nfeat, build_id, status = struct.unpack_from(">HHII", res, 4)
        feats = []
        for i in range(nfeat):
            e = res[MANIFEST_HDR + i * FEAT_ENTRY:MANIFEST_HDR + (i + 1) * FEAT_ENTRY]
            h32, size = struct.unpack_from(">II", e, 24)
            feats.append(dict(name=_cstr(e[:16]), version=_cstr(e[16:24]), hash32=f"{h32:08x}", resident_bytes=size))
        manifest = dict(format=mfmt, build_id=f"{build_id:08X}", status=status,
                        version=_cstr(res[0x10:0x10 + VERSION_LEN]), features=feats)
    return dict(container=dict(bytes=len(module), sha256=hashlib.sha256(module).hexdigest(), **c),
                header=dict(format=fmt, flags=flags, publish=bool(flags & 1), records=o_recs, relocs=o_relocs,
                            inits=o_inits, resident=o_res, resident_bytes=res_len,
                            static=bool(flags & FLAG_STATIC), static_addr=static_addr),
                records=recs, inits=inits, relocs=relocs, manifest=manifest, body=body)


def build_id_ok(p) -> bool:
    """Recompute the build id: sha256 of the body with the build id and version fields zeroed."""
    if not p["manifest"]:
        return True
    body = bytearray(p["body"])
    o = p["header"]["resident"]
    body[o + 8:o + 12] = bytes(4)
    body[o + 0x10:o + 0x10 + VERSION_LEN] = bytes(VERSION_LEN)
    return f"{int(hashlib.sha256(bytes(body)).hexdigest()[:8], 16):08X}" == p["manifest"]["build_id"]


def verify_against_image(p) -> list[str]:
    """Problems that would make the installer apply nothing on a stock 2.40 camera (empty list = installs)."""
    out = []
    for r in p["records"]:
        if not fw.in_payload(r["addr"], r["len"]):
            out.append(f"{r['kind']} {r['addr']:#x}: outside the payload (checked only on the camera)")
        elif fw.ram(r["addr"], r["len"]) != r["before"]:
            out.append(f"{r['kind']} {r['addr']:#x}: before-bytes differ from the 2.40 image")
    return out


def describe(module: bytes) -> str:
    p = parse(module)
    h, m = p["header"], p["manifest"]
    lines = [f"GRMOD {p['container']['bytes']} B  sha256 {p['container']['sha256']}",
             f"Koma format {h['format']}  publish={'yes' if h['publish'] else 'no'}  entry {p['container']['entry']:#x}"]
    if m:
        lines.append(f"{m['version']}  build {m['build_id']} ({'ok' if build_id_ok(p) else 'MISMATCH'})")
        for f in m["features"]:
            lines.append(f"  feature {f['name']:<15} {f['version']:<7} hash {f['hash32']}  resident {f['resident_bytes']} B")
    if h.get("static"):
        a = h["static_addr"]
        lines.append(f"resident: static at {a:#x}..{a + h['resident_bytes']:#x}, canary {a + h['resident_bytes']:#x}.."
                     f"{a + h['resident_bytes'] + CANARY_LEN:#x} (no pool block; words pre-relocated)")
    else:
        lines.append("resident: pool block (relocated at install)")
    lines.append(f"resident segment {h['resident_bytes']} B at body {h['resident']:#x}; transient "
                 f"{len(p['body']) - h['resident_bytes']} B; {len(p['relocs'])} relocations; {len(p['inits'])} init(s)")
    lines.append(f"records ({len(p['records'])}):")
    for r in p["records"]:
        extra = ""
        if r["kind"] in BL_KINDS:
            stock = bl_target(r["addr"], r["before"])
            if stock is not None:
                stock_txt = f"bl {stock:#x}"
            else:   # redirect of a non-bl instruction: show the raw bytes and what they disassemble to
                ins = disasm(r["before"], r["addr"])
                stock_txt = f"not a bl: {r['before'].hex()}" + (
                    " = " + "; ".join(f"{i.mnemonic} {i.op_str}".strip() for i in ins) if ins else "")
            extra = f" -> resident+{r['target_offset']:#x}  (stock: {stock_txt})"
            if r["kind"] == "blhi" and h.get("static"):
                e = h["static_addr"] + r["target_offset"]
                new = thumb_bl(r["addr"], e)
                extra += (f"  prefix-only: entry {e:#x} (= stock mod 4096: {stock is not None and e % 4096 == stock % 4096}),"
                          f" {r['before'][:2].hex()} -> {new[:2].hex()}, suffix {new[2:].hex()}"
                          f" {'kept' if new[2:] == r['before'][2:] else 'CHANGED'}")
        elif r["kind"] == "ptr":
            extra = f" -> resident+{r['target_offset']:#x}"
        elif r["kind"] == "data":
            extra = f" after sha {hashlib.sha256(r['after']).hexdigest()[:12]}" if r["len"] > 16 else f" -> {r['after'].hex()}"
        before = r["before"].hex() if r["len"] <= 16 else f"sha {hashlib.sha256(r['before']).hexdigest()[:12]}"
        lines.append(f"  {r['kind']:<5} {r['addr']:#09x} +{r['len']:<5} before {before}{extra}")
    probs = verify_against_image(p)
    lines.append("before-bytes vs 2.40 image: " + ("all match" if not probs else "; ".join(probs)))
    return "\n".join(lines)
