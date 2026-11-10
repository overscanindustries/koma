"""koma selfcheck: static (capstone) and unit-emulated checks of a built Koma GRMOD. Seconds, no grdemu boot.

Static: container + build id; every before-byte matches the 2.40 image; the installer decodes contiguously and every
branch stays inside it, no absolute BL, bx only through r3/ip; every relocation is an aligned word inside the resident
segment pointing into it; every BL handler and init lands on a `push {..., lr}` in the resident segment.
Unit-emu (unitemu.Unit, real payload, firmware callees stubbed): install on stock RAM -> r0 = 0, status 1, every
record applied, r4-r7/sp preserved, every write outside the module made while IRQ/FIQ were masked except the init
writes inside the features' declared settings ranges, mask restored; each hook site's BL runs the chain and calls the
original target exactly once with r0 and callee-saved registers intact. Negative installs (one per record, version
word 0x231, ABI 2, allocation failure, resident block out of BL range) write nothing outside the module and leak no
allocation.
Static-resident builds (header flag 2): the static checks add the fixed-address rules (empty relocation list, address
aligned and segment + canary inside linker.STATIC_SPAN, every BL record and the published manifest pointer resolve
there, BL in range), and the install checks expect no resident pool allocation, the copy at that address and a valid
canary after it. The allocation-failure and out-of-range negatives do not apply (no allocation; range is a link error).
Early-site (BLHI, prefix-only) records: the entry veneer sits at an address = the stock target (mod 4096) and reaches
the handler; the new BL keeps the stock suffix halfword, so old prefix + suffix still = the stock target (a task
suspended inside the BL); the unit install writes exactly one halfword (the prefix) at each such site.
"""
from __future__ import annotations

import struct

from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_SP

from . import firmware as fw
from .asm import bl_target, disasm
from .inspector import build_id_ok, parse, verify_against_image
from .linker import BL_KINDS, CANARY_LEN, CANARY_MAGIC, STATIC_SPAN, thumb_bl, veneer_target
from .unitemu import RES_POOL, STACK, Unit


class Report:
    def __init__(self):
        self.items = []

    def check(self, name, ok, detail=""):
        self.items.append(dict(check=name, ok=bool(ok), detail=detail))
        return ok


def _static(p, rep, side):
    body = p["body"]
    h = p["header"]
    rep.check("container valid + Koma header", True, f"{p['container']['bytes']} B")
    rep.check("body within loader limit", len(body) <= fw.MAXLEN, f"{len(body)} <= {fw.MAXLEN}")
    rep.check("build id matches body", build_id_ok(p), p["manifest"] and p["manifest"]["build_id"])
    probs = verify_against_image(p)
    rep.check("every before-byte matches the 2.40 image", not probs, "; ".join(probs))
    entry = p["container"]["entry"]
    code = body[entry:h["records"]]
    ins = disasm(code, entry)
    covered = sum(i.size for i in ins)
    # the installer ends with its literal pool: decode up to the first non-instruction word after the last bx
    last_bx = max((i.address + i.size for i in ins if i.mnemonic == "bx"), default=entry)
    rep.check("installer decodes contiguously", covered >= last_bx - entry, f"{covered} B decoded, code to {last_bx:#x}")
    bad = []
    for i in ins:
        if i.address >= last_bx:
            break
        if i.mnemonic in ("b", "bl") or (i.mnemonic.startswith("b") and i.op_str.startswith("#")):
            t = int(i.op_str.lstrip("#"), 16)
            if not entry <= t < last_bx:
                bad.append(f"{i.address:#x} {i.mnemonic} {i.op_str}")
        if i.mnemonic == "bx" and i.op_str not in ("r3", "ip", "lr", "r12"):
            bad.append(f"{i.address:#x} bx {i.op_str}")
    rep.check("installer branches stay inside it; bx only r3/ip/lr", not bad, "; ".join(bad))
    o_res, n_res = h["resident"], h["resident_bytes"]
    rbad = []
    for r in p["relocs"]:
        if r % 4 or not 0 <= r < n_res:
            rbad.append(f"{r:#x} misplaced")
            continue
        v = struct.unpack_from(">I", body, o_res + r)[0]
        if not 0 <= (v & ~1) <= n_res:
            rbad.append(f"{r:#x} -> {v:#x}")
    rep.check(f"{len(p['relocs'])} relocation(s) inside the resident segment", not rbad, "; ".join(rbad))
    tbad = []
    targets = [(f"bl {r['addr']:#x}", r["target_offset"]) for r in p["records"] if r["kind"] == "bl"]
    if h["static"]:         # prefix-only records point at a veneer: check the handler it reaches
        a = h["static_addr"]
        for r in p["records"]:
            if r["kind"] == "blhi":
                v = veneer_target(body, o_res + r["target_offset"], a + r["target_offset"])
                targets.append((f"blhi {r['addr']:#x} via {v[0] if v else '?'} veneer",
                                (v[1] - a) if v else r["target_offset"]))
    targets += [(f"init {o:#x}", o) for o in p["inits"] if not o & 0x80000000]
    targets += [(f"transient init {o & 0x7FFFFFFF:#x}", (o & 0x7FFFFFFF) - o_res) for o in p["inits"] if o & 0x80000000]
    for f in (side or {}).get("features", []):
        for hk in f["hooks"]:
            targets.append((f"{f['name']}.{hk['handler']}", int(hk["handler_offset"], 16)))
    for what, off in targets:
        first = disasm(body[o_res + off:o_res + off + 2], off)
        if not first or first[0].mnemonic != "push" or "lr" not in first[0].op_str:
            tbad.append(f"{what} -> +{off:#x}: {first[0].mnemonic + ' ' + first[0].op_str if first else 'undecodable'}")
    rep.check(f"{len(targets)} handler/init entry point(s) start with push {{..., lr}}", not tbad, "; ".join(tbad))
    for r in p["records"]:
        if r["kind"] in BL_KINDS and r["addr"] % 2 or r["kind"] == "ptr" and r["addr"] % 4:
            rep.check(f"record {r['addr']:#x} aligned", False)
    if h["static"]:
        _static_resident(p, rep)
    if any(r["kind"] == "blhi" for r in p["records"]):
        _early(p, rep)


def _early(p, rep):
    """BLHI (early-site) records, from the bytes alone: congruent entry, suffix unchanged, veneer reaches a handler."""
    h = p["header"]
    if not rep.check("[early] prefix-only records need a static resident", h["static"]):
        return
    a, o_res = h["static_addr"], h["resident"]
    for r in [r for r in p["records"] if r["kind"] == "blhi"]:
        stock = bl_target(r["addr"], r["before"])
        e = a + r["target_offset"]
        new = thumb_bl(r["addr"], e)
        mixed = bl_target(r["addr"], r["before"][:2] + new[2:])
        v = veneer_target(p["body"], o_res + r["target_offset"], e)
        rep.check(f"[early] {r['addr']:#x}: stock bl {stock:#x}, entry {e:#x} (residue {e & 0xFFF:#05x} = "
                  f"{(stock or 0) & 0xFFF:#05x}), prefix {r['before'][:2].hex()} -> {new[:2].hex()}, suffix "
                  f"{new[2:].hex()} unchanged; old prefix + suffix -> {mixed and hex(mixed)}; veneer "
                  f"{v and v[0]} -> {v and hex(v[1])}",
                  stock is not None and e % 4096 == stock % 4096 and new[2:] == r["before"][2:] and mixed == stock
                  and v is not None)


def _static_resident(p, rep):
    """Fixed-address build: what the linker promised, re-derived from the bytes alone."""
    h = p["header"]
    a, n = h["static_addr"], h["resident_bytes"]
    rep.check("[static] relocation list empty (words pre-relocated)", not p["relocs"], f"{len(p['relocs'])} entries")
    rep.check(f"[static] address {a:#x} 4-aligned, segment + canary {a:#x}..{a + n + CANARY_LEN:#x} inside the clean "
              f"span {STATIC_SPAN[0]:#x}..{STATIC_SPAN[1]:#x}",
              a % 4 == 0 and STATIC_SPAN[0] <= a and a + n + CANARY_LEN <= STATIC_SPAN[1])
    far = []
    for r in p["records"]:
        if r["kind"] in BL_KINDS:
            d = a + r["target_offset"] - (r["addr"] + 4)
            if not -0x400000 <= d < 0x400000:
                far.append(f"{r['addr']:#x}")
    rep.check("[static] every BL record reaches its handler at the fixed address (+-4 MB)", not far, ", ".join(far))
    pub = [r for r in p["records"] if r["kind"] == "ptr" and r["addr"] == fw.PUBLISH + 4]
    if pub:
        rep.check("[static] published manifest pointer = the fixed address", pub[0]["target_offset"] == 0,
                  f"offset {pub[0]['target_offset']:#x}")


def _settings_ranges(side):
    out = []
    core = side.get("settings_core") or {}
    if core.get("init"):
        out.append((int(core["range"][0], 16), int(core["range"][1], 16)))
    for f in (side or {}).get("features", []):
        for s in f["settings"] + f.get("ram_writes", []):     # ram_writes: init-written image RAM (Feature.ram_writes)
            out.append((int(s["range"][0], 16), int(s["range"][1], 16)))
    return out


def _camera_env(u):
    """What the hook/init bodies read beyond the payload: the adjustment/calibration store at 0x08010000 (ROM
    defaults from 0x175380, as test_snapdist), and init-time card writes (telemetry's /ATA1/KOMALOG.TXT):
    create -> fd, write -> full length, close -> 0."""
    from .features import telemetry as T
    u.uc.mem_map(0x08000000, 0x100000)
    u.uc.mem_write(0x08010000, fw.ram(0x175380, 0x1000))
    u.stub(T.CREATE, "file_create", 0x7007)
    u.stub(T.WRITE, "file_write", len(T.template()))
    u.stub(T.CLOSE, "file_close", 0)


def _install_ok(module, p, rep, side, label="stock"):
    u = Unit(module)
    _camera_env(u)
    u.watch = {(u.body_base + (o & 0x7FFFFFFF)) if o & 0x80000000 else u.res_base + o for o in p["inits"]}
    r0 = u.install()
    res = u.resident
    rep.check(f"[{label}] installer returns 0 (loader frees the transient body)", r0 == 0, f"r0={r0}")
    rep.check(f"[{label}] r4-r7 and sp preserved", u.callee_saved_ok)
    base = u.res_base
    if p["header"]["resident_bytes"] and p["header"]["static"]:
        n = p["header"]["resident_bytes"]
        status = struct.unpack(">I", u.read(base + 12, 4))[0]
        big = [c for c in u.calls if c[0] == "alloc" and c[2] in (n, n + CANARY_LEN)]
        rep.check(f"[{label}] static: no resident pool allocation, copied to {base:#x} ({n} B), status = 1",
                  not big and status == 1, f"allocs={[c for c in u.calls if c[0] == 'alloc']} status={status}")
        magic, bid, ln, chk = struct.unpack(">IIII", u.read(base + n, CANARY_LEN))
        want_bid = int(p["manifest"]["build_id"], 16)
        rep.check(f"[{label}] static: canary at {base + n:#x} = 'KSTA', build id, length, checksum",
                  magic == CANARY_MAGIC and bid == want_bid and ln == n and chk == (~(magic + bid + ln)) & 0xFFFFFFFF,
                  f"{magic:#x} {bid:#x} {ln} {chk:#x}")
    elif p["header"]["resident_bytes"]:
        status = struct.unpack(">I", u.read(base + 12, 4))[0]
        rep.check(f"[{label}] resident block allocated ({p['header']['resident_bytes']} B), status = 1",
                  res and res[1] == p["header"]["resident_bytes"] and status == 1, f"alloc={res} status={status}")
        rep.check(f"[{label}] resident copy equals the module's resident segment after relocation",
                  _resident_equal(u, p), "")
    bad = []
    written = {r["addr"] + i for r in p["records"] if r["kind"] != "check" for i in range(r["len"])}
    for r in p["records"]:
        now = u.read(r["addr"], r["len"])
        if r["kind"] == "data" and now != r["after"]:
            bad.append(f"data {r['addr']:#x}")
        elif r["kind"] in BL_KINDS and bl_target(r["addr"], now) != base + r["target_offset"]:
            bad.append(f"{r['kind']} {r['addr']:#x} -> {bl_target(r['addr'], now)}")
        elif r["kind"] == "ptr" and struct.unpack(">I", now)[0] != base + r["target_offset"]:
            bad.append(f"ptr {r['addr']:#x}")
        elif r["kind"] == "check" and any(now[i] != r["before"][i] for i in range(r["len"])
                                          if r["addr"] + i not in written):
            bad.append(f"check {r['addr']:#x} changed")
    rep.check(f"[{label}] all {len(p['records'])} records applied", not bad, "; ".join(bad))
    hi = [r for r in p["records"] if r["kind"] == "blhi"]
    if hi:
        odd = []
        for r in hi:
            w = [(x, n) for x, n in u.writes if x < r["addr"] + 4 and r["addr"] < x + n]
            if w != [(r["addr"], 2)]:
                odd.append(f"{r['addr']:#x}: {w}")
        rep.check(f"[{label}] {len(hi)} prefix-only site(s): exactly one halfword store (the prefix), suffix untouched",
                  not odd, "; ".join(odd))
    allowed = _settings_ranges(side) if side else [(0x1B000, 0x1C000)]
    stray = sorted({hex(a) for a, s in u.writes_while_unmasked
                    if not (u.body_base <= a < u.body_base + len(u.body))
                    and not (res and res[0] <= a < res[0] + res[1])
                    and not any(b <= a < b + n for b, n in u.blocks)
                    and not any(lo <= a < hi for lo, hi in allowed)})
    rep.check(f"[{label}] RAM writes outside " + ("declared settings" if side else "the settings block (no manifest)")
              + " happen with IRQ/FIQ masked", not stray,
              ", ".join(stray[:12]))
    irq = [c for c in u.calls if c[0] == "irq_set"]
    rep.check(f"[{label}] irq_set(0xc0) then restore", len(irq) == 2 and irq[0][1] == 0xC0 and irq[1][1] == 0x13,
              str(irq))
    return u


def _resident_equal(u, p):
    h = p["header"]
    base = u.res_base
    seg = bytearray(p["body"][h["resident"]:h["resident"] + h["resident_bytes"]])
    for r in p["relocs"]:
        v = struct.unpack_from(">I", seg, r)[0]
        struct.pack_into(">I", seg, r, v + base)
    live = bytearray(u.read(base, len(seg)))
    live[12:16] = seg[12:16]          # status word
    # bytes a feature init wrote into its own resident state (e.g. a pointer it allocated) are not copy errors
    first_init = min((i for _, i in u.watch_hits), default=len(u.writes))
    for a, n in u.writes[first_init:]:
        for x in range(a, a + n):
            if base <= x < base + len(seg):
                live[x - base] = seg[x - base]
    return bytes(live) == bytes(seg)


def _hooks(u, p, rep, side=None):
    """Wrap-convention check for Hook sites only (Redirect records replace code and are not wraps)."""
    rewrites = set()
    if side:
        sites = {int(h["site"], 16) for f in side["features"] for h in f["hooks"]}
        rewrites = {int(h["site"], 16) for f in side["features"] for h in f["hooks"] if h.get("rewrites_r0")}
    else:
        sites = {r["addr"] for r in p["records"] if r["kind"] in BL_KINDS and bl_target(r["addr"], r["before"])}
    for r in [r for r in p["records"] if r["kind"] in BL_KINDS and r["addr"] in sites]:
        orig = bl_target(r["addr"], r["before"])
        if orig != 0x8FF0E:
            u.stub(orig, "orig")
        u.calls.clear()
        sentinels = {UC_ARM_REG_R4: 0xB4, UC_ARM_REG_R5: 0xB5, UC_ARM_REG_R6: 0xB6}
        u.uc.mem_write(0x1F4DB3, b"\x22")
        u.thumb(r["addr"], {UC_ARM_REG_R0: 0x1234, **sentinels}, stop=r["addr"] + 4)
        pc = u.uc.reg_read(UC_ARM_REG_PC)
        orig_calls = [c for c in u.calls if c[0] in ("orig", "mono_8ff0e")]
        kept = all(u.uc.reg_read(k) == v for k, v in sentinels.items()) and u.uc.reg_read(UC_ARM_REG_SP) == STACK
        r0_rule = "r0 rewritten by design" if r["addr"] in rewrites else "r0 intact"
        r0_ok = r["addr"] in rewrites or (orig_calls and orig_calls[0][1] == 0x1234)
        rep.check(f"hook {r['addr']:#x}: chain returns to site+4, calls {orig:#x} once with {r0_rule}, regs kept",
                  pc == r["addr"] + 4 and len(orig_calls) == 1 and r0_ok and kept,
                  f"pc={pc:#x} calls={u.calls[:6]}")


def _negatives(module, p, rep):
    cases = []
    seen = set()
    for r in p["records"]:
        if r["kind"] in seen and r["kind"] != "check":
            continue
        seen.add(r["kind"])
        b = bytearray(r["before"])
        b[-1] ^= 0xFF
        cases.append((f"{r['kind']} {r['addr']:#x} before-byte differs", dict(ram_edits={r["addr"]: bytes(b)})))
    cases += [("image version 0x231", dict(version=0x231)), ("ABI 2", dict(abi=2))]
    static = p["header"]["static"]
    if not static:
        cases.append(("pool allocation fails", dict(alloc_result=None)))
    if any(r["kind"] in BL_KINDS for r in p["records"]) and not static:
        cases.append(("resident block out of BL range", dict(alloc_result=0x1000000)))
    for name, kw in cases:
        u = Unit(module, **kw)
        r0 = u.install()
        outside = u.outside_writes()
        if static:          # the static block is camera RAM too: a refused install must not touch it
            outside += [(a, n) for a, n in u.writes if u.static[0] <= a < u.static[0] + u.static[1]]
        allocs = [c for c in u.calls if c[0] == "alloc"]
        frees = [c for c in u.calls if c[0] == "free"]
        leak = bool(allocs) and kw.get("alloc_result", RES_POOL) is not None and not frees
        rep.check(f"[negative] {name}: nothing written, no leak", r0 == 0 and not outside and not leak,
                  f"r0={r0} outside={[hex(a) for a, _ in outside[:6]]} allocs={len(allocs)} frees={len(frees)}")


def selfcheck(module: bytes, side: dict | None = None, verbose=True) -> dict:
    rep = Report()
    p = parse(module)
    _static(p, rep, side)
    u = _install_ok(module, p, rep, side)
    _hooks(u, p, rep, side)
    _negatives(module, p, rep)
    ok = all(i["ok"] for i in rep.items)
    if verbose:
        for i in rep.items:
            print(f"{'ok  ' if i['ok'] else 'FAIL'} {i['check']}" + (f"   [{i['detail']}]" if i["detail"] and not i["ok"] else ""))
        print(f"selfcheck: {'PASS' if ok else 'FAIL'} ({sum(i['ok'] for i in rep.items)}/{len(rep.items)})")
    return dict(ok=ok, checks=rep.items, version=p["manifest"] and p["manifest"]["version"])
