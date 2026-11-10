"""koma: build, list, inspect and smoke-test Koma card modules (GRMOD) for the GR DIGITAL 2.40.

  koma list
  koma build --features tone-r3,snap15,recipe-slots [--opt recipe-slots.slot=2] [--resident pool|static[:ADDR]] -o OUT.grmod
  koma inspect OUT.grmod
  koma selfcheck OUT.grmod
  koma smoke OUT.grmod [--feature recipe-slots]
  koma make-loader STOCK_QRFLECT3|gr-240.exe|ZIP -o OUT.bin
  koma make-firmware STOCK_QRFLECT3|gr-240.exe|ZIP -o koma-firmware-1.0.bin   (the Koma 1.0 firmware, tag 240K1.0)
  koma make-package STOCK_QRFLECT3|gr-240.exe|ZIP -o GRMOD.BIN     (the release package, from its .grmod.delta)
  koma package-delta STOCK_QRFLECT3|gr-240.exe|ZIP OUT.grmod -o OUT.grmod.delta

Every command except make-loader, make-firmware, make-package and package-delta reads your own stock 2.40 firmware:
--firmware PATH (before or after the command) or KOMA_FIRMWARE=PATH. PATH: qrflect3, gr-240.exe, a zip holding
qrflect3, or the bare 2.40 payload (not for smoke).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import features as F
from . import firmware as fw
from .linker import LinkError, link


def _features(spec: str, opts: list[str]):
    names = [n.strip() for n in spec.split(",") if n.strip()]
    for set_name, members in F.SETS.items():   # named sets (drop1..drop3, koma01); tolerate unregistered sets
        if set_name not in names:
            continue
        i = names.index(set_name)
        have = [n for n in members if n in F.REGISTRY]
        missing = [n for n in members if n not in F.REGISTRY]
        if missing:
            print(f"note: {set_name} features not registered yet, left out: {', '.join(missing)}", file=sys.stderr)
        names[i:i + 1] = [n for n in have if n not in names]
    per = {}
    for o in opts or []:
        key, _, val = o.partition("=")
        feat, _, name = key.partition(".")
        if not name or not _:
            raise SystemExit(f"--opt {o!r}: use FEATURE.OPTION=VALUE")
        per.setdefault(feat, {})[name] = val
    for feat in per:
        if feat not in names:
            raise SystemExit(f"--opt for {feat}, which is not in --features")
    try:
        return [F.make(n, per.get(n)) for n in names]
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e).strip('"'))


def cmd_list(a):
    for name in F.REGISTRY:
        mod = F.module(name)
        f = F.make(name)
        bits = []
        if f.patches:
            bits.append(f"{len(f.patches)} patch(es) {sum(len(p.before) for p in f.patches)} B")
        if f.hooks:
            bits.append("hooks " + ", ".join(f"{h.site:#x}" for h in f.hooks))
        if f.checks:
            bits.append(f"{len(f.checks)} check(s)")
        owned = [s for s in f.settings if s.owned]
        if owned:
            bits.append("settings " + ", ".join(f"{s.start:#x}..{s.end - 1:#x}" for s in owned))
        if f.requires:
            bits.append("requires " + ",".join(f.requires))
        if f.conflicts:
            bits.append("conflicts " + ",".join(f.conflicts))
        print(f"{name:<14} {f.version:<5} {mod.SUMMARY}")
        print(f"{'':<20} {'; '.join(bits)}")
        for k, v in getattr(mod, "OPTIONS", {}).items():
            print(f"{'':<20} --opt {name}.{k}=...  {v}")
    print(f"default set: {','.join(F.DEFAULT)}")
    for set_name, members in F.SETS.items():
        print(f"{set_name} set (--features {set_name}): {','.join(members)}")


def _set_resident(spec: str):
    """The resident placement a named set asks for (features.SET_RESIDENT), when every named set agrees."""
    vals = {F.SET_RESIDENT[n.strip()] for n in spec.split(",") if n.strip() in getattr(F, "SET_RESIDENT", {})}
    return vals.pop() if len(vals) == 1 else None


def cmd_build(a):
    feats = _features(a.features, a.opt)
    resident = a.resident or _set_resident(a.features) or "pool"
    try:
        r = link(feats, publish=not a.no_publish, resident=resident)
    except LinkError as e:
        print("koma build: refused\n" + str(e), file=sys.stderr)
        return 2
    out = Path(a.output)
    if out.name.upper() == "QUICKVER.UP":
        raise SystemExit("refusing to write QUICKVER.UP")
    out.write_bytes(r.module)
    side = out.with_name(out.name + ".json")
    side.write_text(json.dumps(r.manifest, indent=1) + "\n")
    if a.source:
        Path(a.source).write_text(r.source)
    for w in r.warnings:
        print("warning:", w, file=sys.stderr)
    m = r.manifest
    print(f"{out}  {m['module']['bytes']} B  sha256 {m['module']['sha256'][:16]}  {m['version_string']}")
    print(f"  resident {m['layout']['resident_bytes']} B, transient {m['layout']['transient_bytes']} B "
          f"(freed after install), {len(m['records'])} records, manifest {side}")
    lay = m["layout"]
    print(f"  resident placement: {lay['resident_mode']}" + (
        f" at {lay['static_addr']}..{lay['static_end']}, canary {lay['canary'][0]}..{lay['canary'][1]}"
        if lay["resident_mode"] == "static" else "") + f"; install pool peak {lay['pool_peak_bytes']} B (D)")
    for f in m["features"]:
        print(f"  {f['name']:<14} {f['version']:<5} resident {f['resident_bytes']:>6} B  {f['sha256'][:12]}"
              + (f"  options {f['options']}" if f["options"] else ""))
    return 0


def cmd_inspect(a):
    from .inspector import InspectError, describe
    try:
        print(describe(Path(a.module).read_bytes()))
    except InspectError as e:
        raise SystemExit(f"koma inspect: {e}")
    return 0


def cmd_selfcheck(a):
    from .selfcheck import selfcheck
    module = Path(a.module).read_bytes()
    rep = selfcheck(module, _sidecar(a.module))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1, default=str) + "\n")
    return 0 if rep["ok"] else 1


def cmd_smoke(a):
    from .smoke import run
    module = Path(a.module).read_bytes()
    rep = run(module, side=_sidecar(a.module), only=a.feature or None, out_json=a.json)
    return 0 if all(r["ok"] for r in rep["results"]) else 1


def cmd_make_loader(a):
    from .loader import NEXT, LoaderError, make
    image = getattr(a, "image", "flash1")
    try:
        sha = make(Path(a.stock), Path(a.output), image=image)
    except (LoaderError, OSError) as e:
        print(f"koma {'make-firmware' if image == 'koma10' else 'make-loader'}: refused\n{e}", file=sys.stderr)
        return 2
    print(f"{a.output}  sha256 {sha}")
    print(NEXT)
    return 0


PACKAGE_DELTA = Path(__file__).resolve().parents[2] / "dist-release" / "koma-1.0.0.grmod.delta"


def cmd_make_package(a):
    from .loader import LoaderError, apply_delta, read_stock
    out = Path(a.output)
    try:
        data = apply_delta(read_stock(Path(a.stock)), Path(a.delta).read_bytes())
        if out.exists() and out.read_bytes() != data:
            raise LoaderError(f"{out} exists with different bytes; remove it or choose another name")
        out.write_bytes(data)
    except (LoaderError, OSError) as e:
        print(f"koma make-package: refused\n{e}", file=sys.stderr)
        return 2
    print(f"{out}  sha256 {hashlib.sha256(data).hexdigest()}")
    return 0


def cmd_package_delta(a):
    from .loader import LoaderError, delta_stats, encode_delta, read_stock
    try:
        delta = encode_delta(read_stock(Path(a.stock)), Path(a.module).read_bytes())
        Path(a.output).write_bytes(delta)
    except (LoaderError, OSError) as e:
        print(f"koma package-delta: refused\n{e}", file=sys.stderr)
        return 2
    st = delta_stats(delta)
    print(f"{a.output}  sha256 {hashlib.sha256(delta).hexdigest()}  {len(delta)} bytes: {st['ops']} ops, "
          f"{st['literal']} literal, {st['copied']} copied from stock, {st['zero']} zero")
    return 0


def _sidecar(path):
    side = Path(str(path) + ".json")
    if not side.exists():
        print(f"note: {side} not found; feature options default and per-hook checks are skipped", file=sys.stderr)
        return None
    m = json.loads(side.read_text())
    if m["module"]["sha256"] != hashlib.sha256(Path(path).read_bytes()).hexdigest():
        raise SystemExit(f"{side} describes a different module (sha mismatch); rebuild")
    return m


def main(argv=None):
    fwopt = argparse.ArgumentParser(add_help=False)
    fwopt.add_argument("--firmware", default=argparse.SUPPRESS, metavar="PATH",
                       help=f"your stock 2.40 qrflect3 / gr-240.exe / zip / payload (default: ${fw.ENV}, then the "
                            "research checkout's copy)")
    ap = argparse.ArgumentParser(prog="koma", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--firmware", default=None, metavar="PATH", help="as above; may also follow the command")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list available features", parents=[fwopt]).set_defaults(fn=cmd_list)
    b = sub.add_parser("build", help="link features into one GRMOD", parents=[fwopt])
    b.add_argument("--features", default=",".join(F.DEFAULT), help="comma list (default: %(default)s)")
    b.add_argument("--opt", action="append", help="FEATURE.OPTION=VALUE (repeatable)")
    b.add_argument("-o", "--output", required=True)
    b.add_argument("--no-publish", action="store_true", help="do not publish the manifest pointer at 0x58190")
    b.add_argument("--source", help="also write the generated assembly here")
    b.add_argument("--resident", metavar="pool|static[:ADDR]",
                   help="resident block placement: pool (KernPool block, default) or static (linked at a fixed "
                        "low-RAM address, default the clean span start). Default: "
                        "the named set's choice (features.SET_RESIDENT), else pool")
    b.set_defaults(fn=cmd_build)
    i = sub.add_parser("inspect", help="decode a Koma GRMOD", parents=[fwopt])
    i.add_argument("module")
    i.set_defaults(fn=cmd_inspect)
    c = sub.add_parser("selfcheck", help="static (capstone) + unit-emulated install/shot checks of a built module",
                       parents=[fwopt])
    c.add_argument("module")
    c.add_argument("--json")
    c.set_defaults(fn=cmd_selfcheck)
    s = sub.add_parser("smoke", help="one fast emulator boot + shot; pass/fail per feature (needs the full qrflect3)",
                       parents=[fwopt])
    s.add_argument("module")
    s.add_argument("--feature", action="append", help="only report this feature (repeatable)")
    s.add_argument("--json")
    s.set_defaults(fn=cmd_smoke)
    m = sub.add_parser("make-loader", help="build the flash-1 loader image from your own stock 2.40 update file")
    m.add_argument("stock", help="stock 2.40 qrflect3, Ricoh's gr-240.exe, or a zip containing qrflect3")
    m.add_argument("-o", "--output", required=True)
    m.set_defaults(fn=cmd_make_loader, image="flash1")
    k = sub.add_parser("make-firmware", help="build the Koma 1.0 firmware (SD/SDHC driver, /IROM fallback, tag 240K1.0) "
                                             "from your own stock 2.40 update file")
    k.add_argument("stock", help="stock 2.40 qrflect3, Ricoh's gr-240.exe, or a zip containing qrflect3")
    k.add_argument("-o", "--output", required=True)
    k.set_defaults(fn=cmd_make_loader, image="koma10")
    mp = sub.add_parser("make-package", help="rebuild the release package from its .grmod.delta and your own stock "
                                             "2.40 update file")
    mp.add_argument("stock", help="stock 2.40 qrflect3, Ricoh's gr-240.exe, or a zip containing qrflect3")
    mp.add_argument("--delta", default=str(PACKAGE_DELTA),
                    help="the package delta (default: %(default)s)")
    mp.add_argument("-o", "--output", required=True)
    mp.set_defaults(fn=cmd_make_package)
    pd = sub.add_parser("package-delta", help="encode a package as a delta against your own stock 2.40 update file "
                                              "(what the release ships instead of the package)")
    pd.add_argument("stock", help="stock 2.40 qrflect3, Ricoh's gr-240.exe, or a zip containing qrflect3")
    pd.add_argument("module", help="the .grmod to encode")
    pd.add_argument("-o", "--output", required=True)
    pd.set_defaults(fn=cmd_package_delta)
    a = ap.parse_args(argv)
    fw.use(a.firmware)
    try:
        return a.fn(a) or 0
    except fw.FirmwareError as e:
        print(f"koma {a.cmd}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
