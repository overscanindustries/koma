"""koma smoke: one fast emulator run of a Koma GRMOD. One process, one boot of the module-loader image with the module
as /ATA1/GRMOD.BIN, stopped just after the install, then one still shot (RL1+RL2) stopped right after the develop step
that contains the hooks. Prints one pass/fail line per feature from cheap observable checks:

  installed   the installer ran and returned, the manifest status word is 1, and every record of the feature is in RAM
              (DATA bytes == after, BL decodes to the resident handler)
  hook        every hook handler of the feature executed during the shot
  feature     the feature's own smoke checks (feature module smoke_checks), e.g. LUT uploads, register writes

Simulated (koma/emu grdemu on Unicorn, CPU1 replies fabricated, no sensor). A PASS means the module installed and its code ran
in the model; it is not hardware evidence. Card modules only: pulling the card is the recovery, so this replaces the
7-step prove chain for modules (flash images still need it).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
import time
from pathlib import Path

from . import features as F
from . import firmware as fw
from .asm import bl_target
from .inspector import parse
from .linker import BL_KINDS

UP, DOWN, RIGHT, LEFT, ENTER, RL1, RL2 = 11, 12, 13, 14, 23, 26, 27
FORMAT_KEYS = ((ENTER, 3e7), (RIGHT, 3e7), (DOWN, 3e7), (DOWN, 4e7), (RIGHT, 4e7), (RIGHT, 4e7), (ENTER, 1.5e8),
               (ENTER, 8e7))
STILL = {0: 1, 1: 0, 2: 0}
CC1_PORT, CC2_PORT = 0x12014014, 0x1201811C
MODULE_RETURN = 0x58044          # loader: right after the module's entry returns
LOADER_EXITS = {0x58044: "module_returned", 0x58048: "freed", 0x58052: "free_close_reject",
                0x58058: "close_reject", 0x5805E: "done"}
DEVELOP, DEVELOP_SITE_NEXT = 0x96350, 0x9643A


class Obs:
    """What feature smoke checks see."""

    def __init__(self, m, cc2_uploads, feature_options, calls, card=None):
        self._m = m
        self._card = card if card is not None else {}
        self.cc2_uploads = cc2_uploads
        self.feature_options = feature_options
        self.calls = calls

    def ram8(self, a):
        return self._m.u8(a)

    def ram(self, a, n):
        return bytes(self._m.uc.mem_read(a, n))

    def card_file(self, path):
        """Bytes of an /ATA1 file on the emulated card (including files the module wrote), or None."""
        name = path[6:].upper() if path.upper().startswith("/ATA1/") else path.upper()
        return self._card.get(name)


def _known_tables():
    raw = fw.payload()
    known = {}
    for n, a in (("c1", 0x193B30), ("c2", 0x195B30), ("c4", 0x197B30), ("c5", 0x199B30), ("c3", 0x19BB30),
                 ("slot-e", 0x19DB30)):
        known[hashlib.sha256(raw[a - fw.LOAD:a - fw.LOAD + 0x2000]).hexdigest()] = n
    from .features import _legacy
    for (n, _, _), b in zip(_legacy.recipe_build().CURVES, _legacy.recipe_build().CURVE_BYTES):
        known[hashlib.sha256(b).hexdigest()] = "recipe:" + n
    return known


def run(module: bytes, side: dict | None = None, only: list[str] | None = None, out_json: Path | None = None, verbose=True,
        boot_limit=int(1.5e8), ready_vt=int(5e8), shot_limit=int(3e8), rl1_wait=int(1.2e8)):
    from .emu.grdemu.capture_device import install as cc1_install
    from .emu.grdui import session as S

    image = fw.loader_image()          # stock 2.40 + module loader, built in memory from the user's qrflect3
    t0 = time.time()
    p = parse(module)
    manifest = p["manifest"] or {}
    names = [f["name"] for f in manifest.get("features", [])]
    if only:
        missing = set(only) - set(names)
        if missing:
            raise SystemExit(f"--feature {sorted(missing)} not in this module ({names})")
    sel = only or names
    log = (lambda *a: print(*a, flush=True)) if verbose else (lambda *a: None)
    load = os.getloadavg()[0]
    log(f"smoke: {manifest.get('version', 'Koma module')} features={names} load={load:.1f}")

    scratch = Path(tempfile.mkdtemp(prefix="koma-smoke-"))
    S.RUNS = scratch

    class CcSession(S.Session):
        def _install(self, e):
            super()._install(e)
            cc1_install(e, mode="passive")

    s = CcSession("koma-smoke", keys=dict(STILL), log_access=False, files={"GRMOD.BIN": module},
                   image=image)        # card file in memory only
    m = s.m
    ev = dict(exits=[], develop=[], develop_done=None)
    ports = {CC1_PORT: [], CC2_PORT: []}
    phase = {"v": "boot"}
    for model in (s.emu.unknown, s.emu.cc1_timing):
        orig = model.write

        def w(addr, size, value, orig=orig):
            if addr in ports and phase["v"] == "shot":
                ports[addr].append(value)
            orig(addr, size, value)
        model.write = w
    for a, n in LOADER_EXITS.items():
        m.at(a, lambda mm, n=n: ev["exits"].append((n, mm.vt, mm.r(0))))
    m.at(DEVELOP, lambda mm: ev["develop"].append(mm.vt) if phase["v"] == "shot" else None)
    # Feature-declared install-time stubs (module SMOKE_STUBS = {addr: why}): firmware calls a feature's init makes that
    # the model cannot survive (e.g. fastkeys' fastpoll20 starves MainCtrl behind the model's bit-banged serial loop,
    # speed lane README section 6). Stubbed only while the loader runs the module; each call's r0 is recorded.
    stub_calls = []
    for n in names:
        mod = F.module(n) if n in F.REGISTRY else None
        for a, why in getattr(mod, "SMOKE_STUBS", {}).items():
            def stub(mm, a=a, n=n):
                if phase["v"] == "boot" and not any(e[0] == "module_returned" for e in ev["exits"]):
                    stub_calls.append((n, a, mm.r(0)))
                    mm.return_from_stub(0)
            m.at(a & ~1, stub)
            log(f"smoke stub {a:#x} for {n}: {why}")
    ev["shot_9733a"], ev["linewait_947e4"] = [], []
    m.at(0x9733A, lambda mm: ev["shot_9733a"].append(mm.vt) if phase["v"] == "shot" else None)
    m.at(0x947E4, lambda mm: ev["linewait_947e4"].append(mm.vt) if phase["v"] == "shot" else None)

    def develop_done(mm):
        if phase["v"] == "shot" and ev["develop_done"] is None:
            ev["develop_done"] = mm.vt
            mm.stop("koma-smoke: develop hook step done")
    m.at(DEVELOP_SITE_NEXT, develop_done)

    # ---- boot to just past the install
    while not any(e[0] in ("module_returned", "done", "close_reject", "free_close_reject") for e in ev["exits"]) \
            and m.vt < boot_limit and (m.stop_reason is None or m.stop_reason.startswith("horizon")):
        s.run_for(int(5e6))
    t_boot = time.time() - t0
    log(f"boot: loader exits {[(n, f'{vt:,}', hex(r0)) for n, vt, r0 in ev['exits']]} at {t_boot:.0f}s")

    # resident base: from the published manifest pointer, else from the first BL record
    res_base = None
    if p["header"]["publish"] and m.uc.mem_read(fw.PUBLISH, 4) == b"KOMA":
        res_base = struct.unpack(">I", bytes(m.uc.mem_read(fw.PUBLISH + 4, 4)))[0]
    for r in p["records"]:
        if r["kind"] in BL_KINDS and res_base is None:
            t = bl_target(r["addr"], bytes(m.uc.mem_read(r["addr"], 4)))
            if t is not None:
                res_base = t - r["target_offset"]
    status = m.u32(res_base + 12) if res_base else None

    # features rebuilt from the build's options (sidecar <module>.json) to know which records/hooks are whose
    feats = {n: F.make(n, opts) for n, opts in _feature_opts(side, manifest).items()}
    owner = _record_owners(p, feats)
    hook_hits = {}
    for f in feats.values():
        for h in f.hooks:
            r = next((r for r in p["records"] if r["kind"] in BL_KINDS and r["addr"] == h.site), None)
            if r is None or res_base is None:
                continue
            off = _handler_offset(side, f.name, h.handler)
            if off is None:
                continue
            addr = res_base + off
            hook_hits[(f.name, h.handler)] = []
            m.at(addr, lambda mm, k=(f.name, h.handler): hook_hits[k].append(mm.vt) if phase["v"] == "shot" else None)

    # ---- one shot, once the camera has finished starting (shutter keys before that are ignored in the model)
    s.run_until(max(ready_vt, m.vt))
    s.sync_dial()
    s.run_for(int(2e7))
    # The model only takes a full press after the modelled internal memory is formatted (capture-device notes; same
    # native-menu key sequence as the emu-frontier-2 long-exposure and recipe-slots emulator proofs).
    keys = list(FORMAT_KEYS)
    if "menu5" in names:          # five tabs: the first RIGHT lands on MOVIE; three more reach SETUP (same rows 1-2)
        keys[2:2] = [(RIGHT, 3e7)] * 3
    for code, wait in keys:
        s.tap(code)
        s.run_for(int(wait))
    log(f"ready + formatted: vt {m.vt:,} at {time.time() - t0:.0f}s")
    pokes = {}
    for n in sel:
        mod = F.module(n)
        if hasattr(mod, "smoke_setup"):
            pokes.update(mod.smoke_setup())
    for a, v in pokes.items():
        m.uc.mem_write(a, bytes([v]))
    s.run_for(int(1e7))
    phase["v"] = "shot"
    s.emu.cc1_timing.mode = "irq-suppressed"
    s.hold(RL1)
    s.run_for(rl1_wait)
    s.hold(RL2)
    start = m.vt
    while ev["develop_done"] is None and m.vt - start < shot_limit and \
            (m.stop_reason is None or m.stop_reason.startswith("horizon")):
        s.run_for(int(1e7))
    phase["v"] = "after"
    t_shot = time.time() - t0 - t_boot
    log(f"shot: capture 0x9733a {[f'{v:,}' for v in ev['shot_9733a'][:2]]}, line wait {len(ev['linewait_947e4'])}x, "
        f"develop {[f'{v:,}' for v in ev['develop'][:2]]}, hook step done {ev['develop_done']} at {t_shot:.0f}s")

    known = _known_tables()
    v = ports[CC2_PORT]
    cc2 = []
    for i in range(0, len(v) - len(v) % 4096, 4096):
        h = hashlib.sha256(struct.pack(">4096H", *[x & 0xFFFF for x in v[i:i + 4096]])).hexdigest()
        cc2.append((h, known.get(h, "?")))
    obs = Obs(m, cc2, {n: f.options for n, f in feats.items()}, stub_calls, card=s.emu.files.files)

    # ---- verdicts
    returned = [e for e in ev["exits"] if e[0] == "module_returned"]
    results = []
    for n in sel:
        f = feats[n]
        checks = []
        checks.append(("loader called the module", bool(returned), f"exits={[e[0] for e in ev['exits']]}"))
        checks.append(("manifest status = installed", status == 1, f"status={status} base={res_base and hex(res_base)}"))
        bad = []
        for r in owner.get(n, []):
            now = bytes(m.uc.mem_read(r["addr"], r["len"]))
            if r["kind"] == "data" and now != r["after"]:
                bad.append(f"{r['addr']:#x}")
            if r["kind"] in BL_KINDS and (res_base is None or
                                          bl_target(r["addr"], now) != res_base + r["target_offset"]):
                bad.append(f"{r['kind']} {r['addr']:#x}")
            if r["kind"] == "blhi" and now[2:4] != r["before"][2:4]:
                bad.append(f"blhi {r['addr']:#x} suffix changed")
            if r["kind"] == "ptr" and (res_base is None or
                                       struct.unpack(">I", now)[0] != res_base + r["target_offset"]):
                bad.append(f"ptr {r['addr']:#x}")
        checks.append((f"{len(owner.get(n, []))} record(s) applied", not bad, f"not applied: {bad}" if bad else ""))
        for h in f.hooks:
            if not h.smoke_reachable:
                checks.append((f"SKIP hook {h.site:#x} -> {h.handler} (not on the smoke path)", True, ""))
                continue
            hits = hook_hits.get((n, h.handler), [])
            checks.append((f"hook {h.site:#x} -> {h.handler} hit on the shot", bool(hits),
                           f"hits={len(hits)} develop_done={ev['develop_done']}"))
        if callable(f.smoke):
            checks.extend(f.smoke(obs))
        ok = all(c[1] for c in checks)
        results.append(dict(feature=n, ok=ok, checks=[dict(check=c, ok=o, detail=d) for c, o, d in checks]))
    wall = time.time() - t0
    for r in results:
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['feature']:<14} " +
              "; ".join(("ok " if c["ok"] else "FAIL ") + c["check"] for c in r["checks"]))
        for c in r["checks"]:
            if not c["ok"] and c["detail"]:
                print(f"        {c['check']}: {c['detail']}")
    print(f"smoke: {'PASS' if all(r['ok'] for r in results) else 'FAIL'}  ({wall:.0f}s wall, vt {m.vt:,}, "
          f"assertions {len(s.asserts)}, stop {m.stop_reason})")
    rep = dict(simulated=True, module_sha256=hashlib.sha256(module).hexdigest(), version=manifest.get("version"),
               results=results, loader_exits=ev["exits"], resident_base=res_base and hex(res_base), status=status,
               shot_9733a=ev["shot_9733a"][:4], develop=ev["develop"], develop_done=ev["develop_done"], cc2_uploads=cc2, pokes={hex(a): v for a, v in
                                                                                             pokes.items()},
               stub_calls=stub_calls, assertions=s.asserts[:10], stop_reason=m.stop_reason, vt=m.vt, wall_s=round(wall), load=load)
    if out_json:
        Path(out_json).write_text(json.dumps(rep, indent=1, default=str) + "\n")
    shutil.rmtree(scratch, ignore_errors=True)
    return rep


def _feature_opts(side, manifest):
    if side:
        return {f["name"]: f["options"] for f in side["features"]}
    return {f["name"]: {} for f in manifest.get("features", [])}


def _handler_offset(side, feat, handler):
    for f in (side or {}).get("features", []):
        if f["name"] == feat:
            for h in f["hooks"]:
                if h["handler"] == handler:
                    return int(h["handler_offset"], 16)
    return None


def _record_owners(p, feats):
    owners = {}
    for r in p["records"]:
        for n, f in feats.items():
            mine = any(x.addr == r["addr"] for x in f.patches) and r["kind"] == "data" or \
                any(h.site == r["addr"] for h in f.hooks + f.redirects) and r["kind"] in BL_KINDS or \
                any(q.addr == r["addr"] for q in f.pointers) and r["kind"] == "ptr"
            if mine:
                owners.setdefault(n, []).append(r)
    return owners
