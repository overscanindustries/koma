#!/usr/bin/env python3
"""Tether feature emulator scenario (offline, simulated; not camera evidence).

Used by emulink.py (komabridge --emu). Boots the Koma module-loader image (stock 2.40 + loader, built in memory from the owner's qrflect3 by
koma.firmware.loader_image) under Koma's own grdemu/grdui copy, with an optional GRMOD.BIN on the in-memory card, the
model's default dial (draws the Movie menu, like the spare's physical dial on Movie), formats the modelled internal
memory, then forks one child per arm from the same pre-attach state. koma smoke's passive capture device lets a shot
reach the develop step. Stand-ins:

  USB_ATTACH  real 0x131b3a(0) (UsbShutter's attach case: emits event (8,1)) called from tOpeMain's poll context.
              No USB controller, VBUS or host exists.
  CCTL_CALL   real -CCTL handler 0xcd000 called from tOpeMain's poll context with the parser globals
              (0x1fb3f0 id, 0x1fb3f4 argc, 0x1fb3f8.. args). USB transport, parser and reply transmission are bypassed.

Arms (comma list, --arms):
  usb       attach, remtin, diaset,4, keyset,1,26, keyset,1,27, wait for the develop step, keyset,0,27, keyset,0,26,
            then the restore: keyset,0,2, remtout, then a physical dial nudge (sync_dial) and a physical RL1/RL2 press
  buttons   attach, then physical-style RL1/RL2 through the key sink (no -CCTL)
  nousb     no attach: -CCTL state forced to 1 (stand-in for remtin, which cannot arrive without USB; the flag stays
            0), diaset,4, keyset RL1/RL2 (control: the shot path in this boot)
  stockpatch  'usb' with the stock bytes written back at 0x4fe44 and 0xcd090 (same boot, feature removed)
  detach    attach, remtin, diaset,4, keyset,1,26 (RL1 held), then the real detach 0x131b3a(1) with no release
  release   attach, remtin, diaset,4, keyset,1,26 (RL1 held), then remtout alone (no keyset,0,2), then -VER
  detach0, release0  the same without the half-press (in the model, polling nearly stops while RL1 is held, so the
            record runner never gets back to its message loop; these arms show the runner switch itself)

Assertions (PASS/FAIL per arm, printed and stored as `checks` in the arm's JSON; the exit code is 0 only if every
asserted arm passes): with the tether handler installed, usb: state 2 after remtin+diaset,4, exactly one shot
(0x9733a and the develop step), stored state 9 after keyset,0,2 and after remtout, injection off after remtout, -VER
answers 200, the session event handler 0x539b4 entered once and 0x801 never posted (the running state after the shot is
reported, not asserted: the model's post-shot pipeline does not return to the runner's message loop). stockpatch, and
usb without the handler (stock boot): state 9 from the attach on, no shot, 0x539b4 once, no 0x801. nousb: state 2 after
diaset,4, a shot, no session start. detach (v1.2): after the detach 0x23a888 = 0, flag, -CCTL state, injection and the
held RL1 cleared, stored and running state = the physical dial's (3, Movie), no power-off, no shot. release (v1.2):
after remtout alone, running state 9, injection and RL1 cleared, -VER answers 200, 0x539b4 once, no 0x801.
buttons: report only.

Usage: KOMA_FIRMWARE=... .venv/bin/python tools/komaset/bridge/tether_scenario.py --tag NAME [--module X.grmod] [--arms ...]
Writes tether-emu/<tag>-<arm>.json under the current directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))     # tools/

from unicorn import UC_HOOK_MEM_WRITE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP  # noqa: E402

from koma import features as F  # noqa: E402
from koma import firmware as fw  # noqa: E402
from koma.asm import bl_target  # noqa: E402
from koma.emu.grdemu.capture_device import install as cc1_install  # noqa: E402
from koma.emu.grdui import session as S  # noqa: E402
from koma.inspector import parse  # noqa: E402
from koma.smoke import ENTER, FORMAT_KEYS, LOADER_EXITS, RIGHT, RL1, RL2  # noqa: E402

OUT = Path("tether-emu")
STOCK_4FE44 = bytes.fromhex("48086a41")
STOCK_CD090 = bytes.fromhex("f782fdf8")      # -CCTL remtout: bl 0x4fc84 (tether v1.2 redirects it)
MOVIE = 3                                    # the model's default physical dial (Movie)
DEVELOP, DEVELOP_SITE_NEXT = 0x96350, 0x9643A
WATCH = {0x29B28: "capture_entry_29b28", 0x9733A: "normal_shot_9733a", 0x947E4: "line_wait_947e4",
         DEVELOP: "develop_96350", DEVELOP_SITE_NEXT: "develop_step_done_9643a",
         0x6D670: "state9_run_6d670", 0x6D6F0: "state9_sub0_6d6f0", 0x6D840: "state9_sub1_6d840",
         0x28560: "state_runner_28560", 0x32B74: "power_off_32b74", 0x5D5D0: "usb_session_msg_5d5d0",
         0x539B4: "usb_session_event_539b4", 0xC4B64: "ptp_switch_c4b64", 0x4FC78: "remtin_flag_set_4fc78",
         0x4FC84: "remtout_flag_clear_4fc84", 0x4FD1E: "resolver_4fd1e", 0x4FE44: "state9_force_site_4fe44",
         0x4FE52: "stock_force9_store_4fe52", 0x4FE6C: "release_path_4fe6c",
         0x28D7C: "runner_change_post_5105_28d7c", 0x28EC6: "exit_code6_28ec6", 0x28D94: "a894_cleared_28d94",
         0x985A0: "monitor_start_985a0", 0x98A32: "monitor_stop_98a32", 0x131B3A: "usbshutter_131b3a"}
ARG_HOOKS = {0x28560, 0x5D5D0, 0x539B4}


class Tether(S.Session):
    """Session + a 'call firmware function at the next poll' queue (unchanged logic from the earlier tether emulator script)."""

    def __init__(self, *a, **kw):
        self.calls, self._call_ret, self.call_log = [], None, []
        super().__init__(*a, **kw)

    def _install(self, e):
        super()._install(e)
        cc1_install(e, mode="passive")          # koma smoke's capture device: lets a shot reach the develop step

    def call(self, label, fn, args=(), pre=None, post=None):
        self.calls.append((label, fn, args, pre, post))

    def _on_poll(self, m):
        if self._call_ret is not None:
            label, lr, sp, post = self._call_ret
            self._call_ret = None
            m.uc.reg_write(UC_ARM_REG_LR, lr)
            result = post(m, sp) if post else None
            m.uc.reg_write(UC_ARM_REG_SP, sp)
            self.call_log.append(dict(vt=m.vt, label=label, phase="return", result=result))
            return
        if self.calls and self._saved_lr is None and not self.pending_inject:
            label, fn, args, pre, post = self.calls.pop(0)
            lr, sp = m.uc.reg_read(UC_ARM_REG_LR), m.uc.reg_read(UC_ARM_REG_SP)
            frame = (sp - 0x40) & ~7
            if pre:
                pre(m, frame)
            for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2), args if not callable(args) else args(frame)):
                m.uc.reg_write(reg, value)
            m.uc.reg_write(UC_ARM_REG_SP, frame)
            m.uc.reg_write(UC_ARM_REG_LR, S.POLL | 1)
            m.uc.reg_write(UC_ARM_REG_PC, fn | 1)
            self._call_ret = (label, lr, sp, post)
            self.call_log.append(dict(vt=m.vt, label=label, phase="call", fn=hex(fn)))
            return
        super()._on_poll(m)


def cctl(s, label, ident, *args):
    def pre(m, frame):
        m.w32(0x1FB3F0, ident)
        m.w32(0x1FB3F4, len(args))
        for i, v in enumerate(args):
            m.w32(0x1FB3F8 + 4 * i, v)
        m.w32(frame, 0)
        m.w32(frame + 4, 0)

    def post(m, sp):
        p = m.u32((sp - 0x40) & ~7)
        return dict(reply=m.cstr(p) if p else None, cctl_state_1bd2ec=m.u32(0x1BD2EC), remtin_flag=m.u32(0x1BD1B8))
    s.call(label, 0xCD000, args=lambda frame: (frame, frame + 4), pre=pre, post=post)


def ver(s, label="-VER"):
    """Real -VER handler 0xccb5c(char **reply, int *len) in tOpeMain's poll context (transport bypassed)."""
    def pre(m, frame):
        m.w32(frame, 0)
        m.w32(frame + 4, 0)

    def post(m, sp):
        p = m.u32((sp - 0x40) & ~7)
        return dict(reply=m.cstr(p) if p else None)
    s.call(label, 0xCCB5C, args=lambda frame: (frame, frame + 4), pre=pre, post=post)


def last_reply(s, label):
    r = [c for c in s.call_log if c["label"] == label and c["phase"] == "return"]
    return ((r[-1].get("result") or {}).get("reply") or "") if r else ""


def drain(s, limit=int(4e7)):
    """Run until the call queue is empty and the last call returned (bounded)."""
    end = s.m.vt + limit
    while (s.calls or s._call_ret is not None) and s.m.vt < end and not _stopped(s):
        s.run_for(int(2e6))


def _stopped(s):
    r = s.m.stop_reason
    return r is not None and not r.startswith("horizon")


def snapshot(s, tag):
    m = s.m
    rec = dict(tag=tag, vt=m.vt, app_state_1bd1c0=m.u32(0x1BD1C0), running_state_23a874=m.u32(0x23A874),
               exit_code_23a870=m.u32(0x23A870), usb_flag_23a888=m.u32(0x23A888), service_flag_23a88c=m.u32(0x23A88C),
               remtin_flag_1bd1b8=m.u32(0x1BD1B8), cctl_state_1bd2ec=m.u32(0x1BD2EC), state9_sub_1bef5c=m.u32(0x1BEF5C),
               inject_2b9d34=m.u32(0x2B9D34), vdial_2b9d39=m.uc.mem_read(0x2B9D39, 1)[0],
               key_rl1_2b9df2=m.uc.mem_read(0x2B9DD8 + RL1, 1)[0], a894_23a894=m.u32(0x23A894),
               monitor_1f502f=m.uc.mem_read(0x1F502F, 1)[0], code_cd090=bytes(m.uc.mem_read(0xCD090, 4)).hex(),
               code_4fe44=bytes(m.uc.mem_read(0x4FE44, 4)).hex(), polls=s.polls, stop_reason=m.stop_reason)
    s.snaps.append(rec)
    print(s.arm, json.dumps({k: rec[k] for k in ("tag", "vt", "app_state_1bd1c0", "running_state_23a874",
                                                   "remtin_flag_1bd1b8", "cctl_state_1bd2ec", "usb_flag_23a888",
                                                   "inject_2b9d34", "key_rl1_2b9df2", "monitor_1f502f")}), flush=True)
    return rec


def wait_shot(s, hits, limit):
    start = s.m.vt
    while "develop_step_done_9643a" not in hits and s.m.vt - start < limit and not _stopped(s):
        s.run_for(int(1e7))


def run_arm(s, tag, arm, settle, shot_limit, handler):
    m = s.m
    s.arm, s.snaps = arm, []
    hits, flag_writes, session_msgs = {}, [], []
    if handler:
        WATCH[handler] = "tether_handler"
    for addr, name in WATCH.items():
        def hook(mm, addr=addr, name=name):
            h = hits.setdefault(name, dict(count=0, first=[]))
            h["count"] += 1
            if addr == 0x5D5D0:
                session_msgs.append(mm.r(0))
            if len(h["first"]) < 8:
                d = dict(vt=mm.vt, lr=hex(mm.uc.reg_read(UC_ARM_REG_LR)))
                if addr in ARG_HOOKS:
                    d["r0"] = hex(mm.r(0))
                h["first"].append(d)
        m.at(addr, hook)

    def on_write(uc, access, addr, size, value, user):
        if len(flag_writes) < 200:
            flag_writes.append(dict(vt=m.vt, addr=hex(addr), value=value, pc=hex(uc.reg_read(UC_ARM_REG_PC))))
    m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=0x1BD1B8, end=0x1BD1C3)
    m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=0x23A888, end=0x23A88F)
    if arm == "stockpatch":
        m.uc.mem_write(0x4FE44, STOCK_4FE44)
        m.uc.mem_write(0xCD090, STOCK_CD090)
    m.uc.ctl_flush_tb()      # code hooks added now must also cover blocks translated earlier in the boot
    marks = {}

    def mark(name):
        marks[name] = {k: v["count"] for k, v in hits.items()}

    s.emu.cc1_timing.mode = "irq-suppressed"     # as koma smoke does for the shot phase
    snapshot(s, "pre-attach")
    if arm != "nousb":
        s.call("usb-attach 0x131b3a(0)", 0x131B3A, args=(0,))
        drain(s)
        s.run_for(settle)
        snapshot(s, "after-attach")
    if arm == "buttons":
        mark("before-shot")
        s.hold(RL1); s.run_for(int(1.2e8)); snapshot(s, "rl1-held")
        s.hold(RL2); wait_shot(s, hits, shot_limit); snapshot(s, "rl2-held")
        s.release(RL2); s.release(RL1); s.run_for(int(5e7)); snapshot(s, "released")
        mark("after-shot")
    elif arm.rstrip("0") in ("detach", "release"):
        cctl(s, "remtin", 0x1004); drain(s); s.run_for(int(2e7))
        cctl(s, "diaset,4", 0x1002, 4); drain(s); s.run_for(settle)
        snapshot(s, "after-diaset4")
        mark("before-shot")
        if not arm.endswith("0"):     # detach0 / release0: no half-press (the model stalls polling under a held RL1)
            cctl(s, "keyset,1,26 (RL1 press)", 0x1001, 1, RL1); drain(s); s.run_for(int(1.2e8))
        snapshot(s, "rl1-held")
        if arm.startswith("detach"):
            s.call("usb-detach 0x131b3a(1)", 0x131B3A, args=(1,))
            drain(s); s.run_for(settle)
            snapshot(s, "after-detach")
            s.run_for(settle)
            snapshot(s, "after-detach-settle")
        else:
            cctl(s, "remtout (no keyset,0,2 first)", 0x1003); drain(s); s.run_for(settle)
            snapshot(s, "after-remtout")
            s.run_for(settle)
            snapshot(s, "after-remtout-settle")
            ver(s, "-VER after remtout"); drain(s)
        mark("after-shot")
    else:
        if arm == "nousb":
            m.w32(0x1BD2EC, 1)       # stand-in for remtin's -CCTL state (no USB, so no remtin flag)
        else:
            cctl(s, "remtin", 0x1004); drain(s); s.run_for(int(2e7))
        cctl(s, "diaset,4", 0x1002, 4); drain(s); s.run_for(settle)
        snapshot(s, "after-diaset4")
        mark("before-shot")
        cctl(s, "keyset,1,26 (RL1 press)", 0x1001, 1, RL1); drain(s); s.run_for(int(1.2e8))
        snapshot(s, "rl1-pressed")
        cctl(s, "keyset,1,27 (RL2 press)", 0x1001, 1, RL2); drain(s)
        wait_shot(s, hits, shot_limit)
        snapshot(s, "rl2-pressed")
        mark("after-shot")
        if _stopped(s):
            pass
        else:
            cctl(s, "keyset,0,27", 0x1001, 0, RL2)
            cctl(s, "keyset,0,26", 0x1001, 0, RL1)
            drain(s); s.run_for(int(5e7))
            snapshot(s, "released")
            if arm != "nousb":
                # restore: clear injection (virtual dial back to 9), leave remote mode, nudge the physical dial
                cctl(s, "keyset,0,2 (clear injection)", 0x1001, 0, 2); drain(s); s.run_for(settle)
                snapshot(s, "after-clear")
                cctl(s, "remtout", 0x1003); drain(s); s.run_for(settle)
                snapshot(s, "after-remtout")
                ver(s, "-VER after remtout"); drain(s)
                s.sync_dial(); s.run_for(settle)
                snapshot(s, "after-dial-sync")
                mark("before-physical")
                s.hold(RL1); s.run_for(int(1.2e8)); s.hold(RL2); s.run_for(int(1.5e8))
                s.release(RL2); s.release(RL1); s.run_for(int(5e7))
                snapshot(s, "after-physical-shutter")
                mark("after-physical")
                cctl(s, "keyset,3,22 after remtout (expect 451)", 0x1001, 3, 22); drain(s)
                cctl(s, "remtin again", 0x1004); drain(s); s.run_for(int(2e7))
                snapshot(s, "after-remtin-again")
    checks = assert_arm(arm, handler, s.snaps, hits, marks, session_msgs, s)
    rep = dict(tag=tag, arm=arm, simulated=True, ok=all(c["ok"] for c in checks), checks=checks,
               session_msgs=[hex(x) for x in session_msgs],
               claim="Simulated. Firmware code from 0x131b3a / 0xcd000 on is original 2.40 + the module under test; "
                     "USB controller, host, transport, sensor and CPU1 are absent or fabricated. Not a camera result.",
               handler=hex(handler) if handler else None, snapshots=s.snaps, hits=hits, marks=marks,
               flag_writes=flag_writes, cctl_and_calls=s.call_log, assertions=s.asserts,
               file_ops_after=s.emu.files.ops[s._nops:][:100], stop_reason=m.stop_reason,
               standins=S.STANDINS + [
                   {"name": "USB_ATTACH", "what": "real 0x131b3a(0) called in tOpeMain poll context; no VBUS/controller/host"},
                   {"name": "CCTL_CALL", "what": "real -CCTL handler 0xcd000 called in tOpeMain poll context with parser "
                    "globals supplied; USB transport/parser/reply bypassed"},
                   {"name": "CAPTURE_DEVICE", "what": "koma smoke's passive CC1 capture device (koma.emu.grdemu.capture_device)"}])
    (OUT / f"{tag}-{arm}.json").write_text(json.dumps(rep, indent=1, default=str) + "\n")
    for c in checks:
        tag = "INFO" if c.get("info") else ("PASS" if c["ok"] else "FAIL")
        print(arm, f"{tag}  {c['check']}" + (f"   [{c['detail']}]" if tag != "PASS" else ""), flush=True)
    asserted = [c for c in checks if not c.get("info")]
    print(f"tether scenario {arm}: {'PASS' if rep['ok'] else 'FAIL'} ({sum(c['ok'] for c in asserted)}/{len(asserted)})",
          flush=True)
    return rep["ok"]


def assert_arm(arm, handler, snaps, hits, marks, session_msgs, s):
    """The README table's expectations for this arm, as PASS/FAIL records (INFO lines are reported, not asserted)."""
    out = []

    def check(name, ok, detail=""):
        out.append(dict(check=name, ok=bool(ok), detail=str(detail)))

    def info(name, detail):
        out.append(dict(check=name, ok=True, info=True, detail=str(detail)))
    st = {x["tag"]: x["app_state_1bd1c0"] for x in snaps}
    snap = {x["tag"]: x for x in snaps}
    events = hits.get("usb_session_event_539b4", {}).get("count", 0)

    def cleared(tag):
        x = snap.get(tag, {})
        return (x.get("remtin_flag_1bd1b8"), x.get("cctl_state_1bd2ec"), x.get("inject_2b9d34"),
                x.get("key_rl1_2b9df2"), x.get("vdial_2b9d39")) == (0, 0, 0, 0, 9)

    def remote(tag):
        x = snap.get(tag, {})
        return {k: x.get(k) for k in ("remtin_flag_1bd1b8", "cctl_state_1bd2ec", "inject_2b9d34", "key_rl1_2b9df2",
                                      "vdial_2b9d39")}

    def shots(name="normal_shot_9733a"):
        return marks.get("after-shot", {}).get(name, 0) - marks.get("before-shot", {}).get(name, 0)
    if arm == "usb" and handler:
        check("usb: state 9 after attach", st.get("after-attach") == 9, st)
        check("usb: state 2 after remtin + diaset,4", st.get("after-diaset4") == 2, st)
        check("usb: exactly one shot (0x9733a) reaching the develop step", shots() == 1 and
              shots("develop_step_done_9643a") == 1, marks.get("after-shot"))
        check("usb: state 9 after keyset,0,2", st.get("after-clear") == 9, st)
        check("usb: state 9 after remtout", st.get("after-remtout") == 9, st)
        check("usb: remote state cleared after remtout (flag, -CCTL state, injection, RL1, virtual dial)",
              cleared("after-remtout"), remote("after-remtout"))
        check("usb: -VER answers 200 after remtout", last_reply(s, "-VER after remtout").startswith("200 "),
              last_reply(s, "-VER after remtout"))
        info("usb: running state after keyset,0,2 / remtout / dial sync (post-shot, model-limited)",
             [snap.get(t, {}).get("running_state_23a874") for t in ("after-clear", "after-remtout", "after-dial-sync")])
        info("usb: 0x5105 runner-change posts / exit-code-6 stores",
             (hits.get("runner_change_post_5105_28d7c", {}).get("count", 0),
              hits.get("exit_code6_28ec6", {}).get("count", 0)))
    elif arm.rstrip("0") in ("detach", "release") and handler:
        key0 = arm.endswith("0")
        arm = arm.rstrip("0")
        check(f"{arm}: state 2 after remtin + diaset,4", st.get("after-diaset4") == 2, st)
        check(f"{arm}: RL1 held by injection before the exit",
              snap.get("rl1-held", {}).get("key_rl1_2b9df2") == (0 if key0 else 1), remote("rl1-held"))
        check(f"{arm}: no shot", hits.get("normal_shot_9733a", {}).get("count", 0) == 0, hits.get("normal_shot_9733a"))
        check(f"{arm}: no power-off (0x32b74)", hits.get("power_off_32b74", {}).get("count", 0) == 0,
              hits.get("power_off_32b74"))
        check(f"{arm}: 0x801 never posted", 0x801 not in session_msgs, [hex(x) for x in session_msgs])
        if arm == "detach":
            end = "after-detach-settle"
            check("detach: 0x23a888 = 0 after the detach", snap.get(end, {}).get("usb_flag_23a888") == 0, snap.get(end))
            check("detach: remote state cleared (flag, -CCTL state, injection, RL1, virtual dial)", cleared(end),
                  remote(end))
            check("detach: stored and running state = the physical dial's (3, Movie)",
                  (st.get(end), snap.get(end, {}).get("running_state_23a874")) == (MOVIE, MOVIE),
                  (st.get(end), snap.get(end, {}).get("running_state_23a874")))
        else:
            end = "after-remtout-settle"
            check("release: remote state cleared by remtout alone", cleared(end), remote(end))
            check("release: stored and running state 9 after remtout",
                  (st.get(end), snap.get(end, {}).get("running_state_23a874")) == (9, 9),
                  (st.get(end), snap.get(end, {}).get("running_state_23a874")))
            check("release: -VER answers 200 after remtout", last_reply(s, "-VER after remtout").startswith("200 "),
                  last_reply(s, "-VER after remtout"))
            check("release: session event handler 0x539b4 entered once (state 9 re-entry does not restart it)",
                  events == 1, events)
    elif arm in ("usb", "stockpatch"):
        after = [x for x in snaps if x["tag"] != "pre-attach"]
        check(f"{arm}: state 9 from the attach on", after and all(x["app_state_1bd1c0"] == 9 for x in after), st)
        check(f"{arm}: no shot", hits.get("normal_shot_9733a", {}).get("count", 0) == 0, hits.get("normal_shot_9733a"))
    if arm in ("usb", "stockpatch"):
        check(f"{arm}: session event handler 0x539b4 entered once", events == 1, [hex(x) for x in session_msgs])
        check(f"{arm}: 0x801 never posted", 0x801 not in session_msgs, [hex(x) for x in session_msgs])
    elif arm == "nousb":
        check("nousb: state 2 after diaset,4", st.get("after-diaset4") == 2, st)
        check("nousb: shot (0x9733a) reaching the develop step", shots() >= 1 and
              shots("develop_step_done_9643a") >= 1, marks.get("after-shot"))
        check("nousb: no USB session start", 0x800 not in session_msgs, [hex(x) for x in session_msgs])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--arms", default="usb,stockpatch,nousb,detach,release")
    ap.add_argument("--settle", type=int, default=int(1.5e8))
    ap.add_argument("--shot-limit", type=int, default=int(3e8))
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    S.RUNS = Path(tempfile.mkdtemp(prefix="tether-scn-"))
    t0 = time.time()
    module = Path(a.module).read_bytes() if a.module else None
    names = [f["name"] for f in parse(module)["manifest"]["features"]] if module else []
    s = Tether(f"tether-{a.tag}", keys={}, log_access=False, files={"GRMOD.BIN": module} if module else {},
               image=fw.loader_image())
    m = s.m
    exits = []
    for addr, n in LOADER_EXITS.items():
        m.at(addr, lambda mm, n=n: exits.append((n, mm.vt, mm.r(0))))
    stubbed = []
    for n in names:   # feature-declared install-time stubs, exactly as koma smoke applies them
        mod = F.module(n)
        for addr, why in getattr(mod, "SMOKE_STUBS", {}).items():
            def stub(mm, n=n, addr=addr):
                if not any(e[0] == "module_returned" for e in exits):
                    stubbed.append((n, hex(addr), mm.r(0)))
                    mm.return_from_stub(0)
            m.at(addr & ~1, stub)
    s.run_until(int(5e8))
    s.sync_dial()
    s.run_for(int(2e7))
    keys = list(FORMAT_KEYS)
    if "menu5" in names:
        keys[2:2] = [(RIGHT, 3e7)] * 3
    for code, wait in keys:
        s.tap(code)
        s.run_for(int(wait))
    s.run_for(int(1e7))
    s._nops = len(s.emu.files.ops)
    now = bytes(m.uc.mem_read(0x4FE44, 4))
    handler = bl_target(0x4FE44, now) if now != STOCK_4FE44 else None
    base = dict(tag=a.tag, module=a.module, module_sha256=module and hashlib.sha256(module).hexdigest(), features=names,
                loader_exits=exits, install_stubs=stubbed, code_4fe44=now.hex(), handler=handler and hex(handler),
                manifest_status=None, pre_attach_vt=m.vt, app_state=m.u32(0x1BD1C0), boot_wall_s=round(time.time() - t0),
                format_file_ops=len(s.emu.files.ops))
    if m.uc.mem_read(fw.PUBLISH, 4) == b"KOMA":
        base["manifest_status"] = m.u32(struct.unpack(">I", bytes(m.uc.mem_read(fw.PUBLISH + 4, 4)))[0] + 12)
    print("base", json.dumps(base), flush=True)
    (OUT / f"{a.tag}-base.json").write_text(json.dumps(base, indent=1) + "\n")
    pids = []
    for arm in a.arms.split(","):
        pid = os.fork()
        if pid == 0:
            code = 1
            try:
                code = 0 if run_arm(s, a.tag, arm, a.settle, a.shot_limit, handler) else 3
            finally:
                os._exit(code)
        pids.append((pid, arm))
    codes = {}
    for pid, arm in pids:
        codes[arm] = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
        print(arm, "exit", codes[arm], flush=True)
    ok = all(c == 0 for c in codes.values())
    print("wall", round(time.time() - t0), "s")
    print(f"tether scenario {a.tag}: {'PASS' if ok else 'FAIL'} "
          f"({', '.join(f'{k} {'ok' if v == 0 else 'FAIL (exit %d)' % v}' for k, v in codes.items())})", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
