#!/usr/bin/env python3
"""selftest: the hardware USB steps (connect, settings, photos, tether shot, live view, -KSET/-KGET) as one command.

  python3 tools/komaset/bridge/komabridge.py --real --selftest --out ~/koma-session/
  python3 tools/komaset/bridge/komabridge.py --mock --mock-tether --selftest --out /tmp/koma-mock/   (simulated)

Runs nine steps in a fixed, safe order and writes report.md, report.json and the saved artifacts into --out:

  1 connect      descriptors, -VER (once, as the bridge always does), USB speed if libusb exposes it
  2 settings     RETR /EXT0/PARAM.CUR, komaset check, KSIO magic / firmware tag / import status
  3 komalog      RETR /EXT0/KOMALOG.TXT, decoded with tools/koma/features/telemetry.py (build id, POOL, RES/CANARY)
  4 photos       the JPEGs under /EXT0/DCIM (-NLST, -INFO, -FDAT)
  5 throughput   timed RETR of the largest JPEG and of PARAM.CUR (bytes/s)
  6 tether       /tether/start (once, for steps 6-8), one shoot with fetch, save the JPEG; MakerNote firmware tag
               (the shoot's -INFO / RETR drop live view: diaset,4 is sent again and -LVFR 0 waited for before step 7;
               no -LVFR 150 after the start's re-arms fails the step "remote mode never armed", no key pressed)
  7 live view    30 pulls each of -LVFR mode 0, 1, 2 (inside remote control); fps, bytes/s, flags, one frame each
  8 exposure     -KGET EV, -KSET EV to the next odd value, -KGET, wait 0.5 s, restore (a busy reply retried up
                 to 15 s; the busy time after the EV change is reported), -KGET; then /tether/stop (ends the
                 one remote-control session that steps 6-8 share)
  9 recheck      PARAM.CUR and KOMALOG.TXT again, diffed against steps 2 and 3 (PARAM.CUR must not change)

Steps 6 to 8 run only when step 3 found a Koma build id (a stock camera skips them: "needs Koma firmware"). A failed
connect, or a camera lost mid-run (transport error), stops the run; every later step is SKIP with the reason. Other
failures are recorded and the run goes on, because the later steps still mean something.

Every camera command goes through komabridge's own endpoint functions, so every write is one komabridge already
allows: the tether keys (-CCTL remtin, diaset,4, keyset 1|0 on 26/27, keyset,0,2, remtout) and -KSET of the stock EV
byte 0x053 with a stock value. No -STOR, no poff, nothing on /IROM. The EV byte is put back before the step ends.
Hardware status: never run on a camera; tested against the simulated camera (mockcard.py) only.
"""
from __future__ import annotations

import base64
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import komabridge as KB  # noqa: E402  (puts tools/ and tools/komaset/ on sys.path)
import komaset  # noqa: E402
import verify_session as VS  # noqa: E402  (KOMALOG fallback parser, KSIO status names, MakerNote reader loader)

try:
    from koma.features import telemetry as TLM  # noqa: E402
except Exception:  # noqa: BLE001  (fall back to verify_session's line parser)
    TLM = None

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
KOMALOG = "/EXT0/KOMALOG.TXT"
LIVE_PULLS = 30
LIVE_MODES = (0, 1, 2)
LIVE_EXT = {0: "png", 1: "jpg", 2: "png"}
LIVE_OFF_GRACE_S = 3.0      # a 451 right after a shot is the rec-review pause; keep asking this long
EV_OFF = 0x053
EV_VALUES = sorted(KB.KSET_STOCK[EV_OFF])
EV_SETTLE_S = 0.5           # between the EV set and its restore (the camera is briefly busy after an EV change)
EV_RESTORE_WAIT_S = 15.0    # the restore retries a `451 -KSET busy` this long (the set uses the bridge's 5 s;
                            # 8.1 s of busy seen once on hardware)
EXPECTED_TAG = ("Rev024M", "240K")   # MakerNote tag 2 prefix: the loader Koma firmware, or 240K (the SDHC firmware's 240K2, a release's 240K1.0)
STEPS = ((1, "connect", "Connect, -VER, USB speed"), (2, "settings", "Settings pull (PARAM.CUR)"),
         (3, "komalog", "KOMALOG.TXT pull and decode"), (4, "photos", "Photos list"),
         (5, "throughput", "Throughput (RETR)"), (6, "tether", "Tether shot"), (7, "live", "Live view modes 0/1/2"),
         (8, "exposure", "Exposure round-trip (-KGET/-KSET EV)"), (9, "recheck", "Second pull and diff"))
NEEDS_KOMA = {6, 7, 8}
USB_SPEEDS = {1: "low (1.5 Mbit/s)", 2: "full (12 Mbit/s)", 3: "high (480 Mbit/s)", 4: "super (5 Gbit/s)"}


class Abort(Exception):
    """Later steps are meaningless (no connection, or the camera was lost)."""


def busy_text(res: dict) -> str:
    """' (after N busy retries, X s)' when a -KSET write was retried, else ''."""
    n = res.get("busy_retries") or 0
    return f" (after {n} busy retries, {res.get('busy_s', 0):.2f} s)" if n else ""


def ev_text(v: int) -> str:
    """Stock EV byte (odd 1..25, 13 = 0.0) as EV in thirds."""
    thirds = (v - 13) // 2
    return f"{thirds / 3:+.1f} EV" if v in KB.KSET_STOCK[EV_OFF] else f"unexpected byte {v}"


def decode_komalog(data: bytes) -> dict:
    """Build id, STAGE, POOL and the RES/CANARY line, via telemetry.py (or verify_session's parser if that is
    missing)."""
    if TLM is not None:
        p = TLM.parse(data)
        f, d = p["fields"], TLM.describe(p["fields"])
        header, trailer = p["header_ok"], p["trailer_ok"]
    else:
        text = data.decode("ascii", "replace")
        f, d = VS.parse_log(text), {}
        header, trailer = text.startswith(VS.LOG_HEADER), text.rstrip().endswith(VS.LOG_TRAILER)
    build = f.get("BUILD")
    return {"bytes": len(data), "header_ok": header, "trailer_ok": trailer,
            "build_id": None if build is None else f"{build:08X}", "stage": f.get("STAGE"),
            "stage_text": VS.STAGES.get(f.get("STAGE"), "no stage completed"),
            "pool": f.get("POOL"), "pool_tries": f.get("PTRY"), "res_line": d.get("RES_LINE"),
            "fields": {k: (None if v is None else f"{v:08X}") for k, v in f.items()}}


def simulated_komalog(build: int = 0x4B4D0400, pool: int = 1888) -> bytes:
    """A SIMULATED KOMALOG.TXT, in the telemetry feature's fixed layout, for the mock camera with --mock-tether.
    Not a camera reading: the numbers are the emulator's koma04 POOL and an invented build id."""
    values = {"STAGE": 3, "BUILD": build, "RES": 0x0001C000, "CANRY": 0, "CPSR": 0x600000D3, "SP": 0x0027F000,
              "POOL": pool, "PTRY": 12, "CBUF": 0x00300000, "SMC1": 0x5A, "SMC2": 0xA5, "FRESH": 0x3C, "CP15": 1}
    if TLM is None:
        lines = ["KOMA TLM V1"] + [f"{n.ljust(5)}={v:08X}" for n, v in values.items()] + ["END"]
        return ("\n".join(lines) + "\n").encode()
    out = bytearray(TLM.HEADER)
    for name in TLM.FIELDS:
        v = values.get(name)
        out += name.ljust(5).encode() + b"=" + (b"--------" if v is None else f"{v:08X}".encode()) + b"\n"
    return bytes(out + TLM.TRAILER)


def seed_mock_koma(body) -> None:
    """Make a MockBody look like a Koma camera to step 3: put a simulated KOMALOG.TXT on its card root."""
    body.card[KOMALOG] = simulated_komalog()


def usb_speed(backend) -> str | None:
    """libusb's device speed for the one Ricoh device (descriptor-only; opens nothing). None if not exposed."""
    if getattr(backend, "simulated", True):
        return None
    try:
        import usb.core
        from grdusb.descriptors import VENDOR
        from grdusb.transport import _backend
        devices = list(usb.core.find(find_all=True, idVendor=VENDOR, backend=_backend()))
        speed = getattr(devices[0], "speed", None) if len(devices) == 1 else None
        return None if speed is None else USB_SPEEDS.get(speed, f"code {speed}")
    except Exception:  # noqa: BLE001
        return None


def _rate(nbytes: int, seconds: float) -> str:
    return f"{nbytes / seconds / 1e6:.2f} MB/s" if seconds > 0 else "instant"


class SelfTest:
    def __init__(self, link: "KB.Link", out: Path, clock=time.monotonic, sleep=time.sleep, log=None,
                 connect_wait_s: float = 0.0, live_pulls: int = LIVE_PULLS):
        self.link, self.out, self.clock, self.sleep = link, Path(out).expanduser(), clock, sleep
        self.log = log or (lambda text: None)
        self.connect_wait_s, self.live_pulls = connect_wait_s, live_pulls
        self.ev_settle_s, self.ev_restore_wait_s = EV_SETTLE_S, EV_RESTORE_WAIT_S
        self.steps: list[dict] = []
        self.artifacts: list[str] = []
        self.koma = False
        self.koma_note = ""
        self.cur1 = self.log1 = None
        self.photos: list = []
        self.live_dropped = False  # a RETR / -INFO ran inside the remote session: diaset,4 again before steps 7/8

    # ------------------------------------------------------------------ helpers
    def save(self, name: str, data: bytes) -> str:
        (self.out / name).write_bytes(data)
        if name not in self.artifacts:
            self.artifacts.append(name)
        return name

    def _read_optional(self, path: str) -> bytes | None:
        """RETR of a card-root file, or None on `450` (absent). The card root is never listed."""
        return self.link.run(lambda s: KB.read_root_file(s, path))

    def _koma_by_ksta(self):
        """No Koma build id from KOMALOG.TXT: a -KSTA that decodes still proves a Koma build (stock answers 501)."""
        try:
            KB.ram_state(self.link)
        except Exception as error:  # noqa: BLE001
            self.koma_note = f"no Koma firmware: no build id in KOMALOG.TXT and -KSTA failed ({type(error).__name__})"
            return
        self.koma = True
        self.koma_note = "Koma build proven by -KSTA (build id not read from KOMALOG.TXT)"

    def _timed_retr(self, path: str) -> tuple[int, float]:
        t0 = self.clock()
        data = self.link.run(lambda s: s.read_file(path))  # straight RETR, not the photo cache
        return len(data), self.clock() - t0

    # ------------------------------------------------------------------ run
    def run(self) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        abort = None
        for n, key, title in STEPS:
            step = {"n": n, "key": key, "title": title, "status": SKIP, "seconds": 0.0, "summary": "",
                    "details": [], "artifacts": []}
            self.steps.append(step)
            if abort:
                step["summary"] = abort
            elif n in NEEDS_KOMA and not self.koma:
                step["summary"] = "needs Koma firmware (no Koma build id in KOMALOG.TXT)"
            else:
                before = len(self.artifacts)
                t0 = self.clock()
                try:
                    getattr(self, f"step_{key}")(step)
                except Abort as error:
                    step["status"], step["summary"] = FAIL, str(error)
                    abort = f"not run: step {n} stopped the run ({error})"
                except KB.BridgeError as error:
                    step["status"], step["summary"] = FAIL, f"HTTP-equivalent {error.status}: {error.message}"
                    if error.status == 503:
                        abort = f"not run: camera lost in step {n} ({error.message})"
                except Exception as error:  # noqa: BLE001  (record it, keep going)
                    step["status"], step["summary"] = FAIL, f"{type(error).__name__}: {error}"
                    step["details"].append(traceback.format_exc(limit=3).strip())
                step["seconds"] = round(self.clock() - t0, 3)
                step["artifacts"] = self.artifacts[before:]
                for w in step.get("lvfr_150", []):  # the measured live-view start, in the step's one-line result
                    note = (f"first -LVFR 150 after {w['s']:.1f} s ({w['when']})" if w["s"] is not None else
                            f"no -LVFR 150 ({w['when']}, wait limit {self.link.lvfr_wait_s:.0f} s)")
                    step["summary"] = f"{step['summary']}; {note}" if step["summary"] else note
            if n == 3 and not self.koma and not abort:
                self._koma_by_ksta()
                step["details"].append(self.koma_note)
            if n == 8 or abort:  # the one remote session of steps 6-8 ends here (or at once on an aborted run)
                self._end_session(step)
            self.log(f"[{n}/9] {title}: {step['status']} ({step['seconds']:.1f} s) {step['summary']}")
        self._cleanup()
        report = {"tool": "komabridge --selftest", "bridge_version": KB.BRIDGE_VERSION, "started": started,
                  "backend": self.link.backend.name, "simulated": bool(self.link.backend.simulated),
                  "firmware": self.link.firmware, "koma": self.koma, "out": str(self.out),
                  "result": FAIL if any(s["status"] == FAIL for s in self.steps) else PASS, "steps": self.steps}
        self._write(report)
        return report

    def _cleanup(self):
        """Never leave remote control (or a held key) on, whatever happened above."""
        if self.link.remote_on:
            try:
                KB.tether_stop(self.link)
            except Exception as error:  # noqa: BLE001
                self.log(f"cleanup: /tether/stop failed: {error}")

    # ------------------------------------------------------------------ steps
    def step_connect(self, step):
        deadline = self.clock() + self.connect_wait_s
        while True:
            st = self.link.status()
            if st["connected"] or self.clock() >= deadline:
                break
            self.sleep(1.0)
        try:
            devices = self.link.backend.scan()
        except Exception:  # noqa: BLE001
            devices = []
        if devices:
            d = devices[0]
            step["details"].append(f"descriptor: {d.vendor:04x}:{d.product:04x} bcdUSB {d.bcd_usb:04x} "
                                   f"({KB.usb_mode_name(d.product)})" + (f", {d.location}" if d.location else ""))
        speed = usb_speed(self.link.backend)
        step["details"].append(f"USB speed: {speed or ('n/a (simulated)' if self.link.backend.simulated else 'not exposed by libusb')}")
        step["usb_speed"] = speed
        if not st["connected"]:
            raise Abort(st["message"])
        step["firmware"] = st["camera"]["firmware"]
        step["details"].append("card root files fetched by RETR (root listing skipped; -NLST /EXT0 hangs on big cards)")
        step["details"].append(f"-VER: {st['camera']['firmware']}; USB mode {st['usb_mode']}; "
                               f"backend {st['backend']}{' (SIMULATED)' if st['simulated'] else ''}")
        step["status"] = PASS
        step["summary"] = f"connected, firmware {st['camera']['firmware']}" + (f", {speed}" if speed else "")

    def step_settings(self, step):
        try:
            r = KB.get_settings(self.link)
        except KB.BridgeError as error:
            if error.extra.get("code") == "no_param_cur":
                step["summary"] = "no PARAM.CUR on the card (needs Koma settingsio)"
                return
            raise
        data = base64.b64decode(r["data"])
        self.cur1 = data
        self.save("PARAM-1.CUR", data)
        chk = r["check"]
        step["details"] += chk["text"].splitlines()
        blk = bytearray(data)
        tag = komaset.ksio_tag(blk) if len(blk) == 4096 else None
        if tag is None:
            ksio = "KSIO: absent (the block was not written by the settingsio feature)"
        else:
            sb = blk[0x8DC]
            ksio = (f"KSIO: version {blk[komaset.KSIO_OFF + 4]}, firmware tag {tag!r}, import status {sb} = "
                    f"{VS.STATUS.get(sb, 'unexpected')}")
        step["details"].append(ksio)
        step["ksio_tag"] = tag
        step["check_ok"] = chk["ok"]
        step["status"] = PASS if chk["ok"] else FAIL
        step["summary"] = (f"{len(data)} B, envelope {'ok' if chk['ok'] else 'BAD'}, "
                           + (f"tag {tag!r}" if tag else "no KSIO"))

    def step_komalog(self, step):
        data = self._read_optional(KOMALOG)
        if data is None:
            step["summary"] = "no KOMALOG.TXT on the card: stock camera, or a Koma build without telemetry"
            return
        self.log1 = data
        self.save("KOMALOG-1.TXT", data)
        d = decode_komalog(data)
        step["decode"] = d
        pool = d["pool"]
        if pool is None:
            pool_line = "POOL: not recorded"
        else:
            ship = pool > VS.POOL_MIN_SHIP
            pool_line = (f"POOL: {pool:,} B ({'above' if ship else 'NOT above'} 1,500 B: the mode rows "
                         f"{'can' if ship else 'cannot'} ship)")
        step["details"] += [f"{d['bytes']} B, header {'ok' if d['header_ok'] else 'MISSING'}, "
                            f"trailer {'ok' if d['trailer_ok'] else 'missing'}",
                            f"build id: {d['build_id'] or '--'}", f"STAGE={d['stage']}: {d['stage_text']}",
                            pool_line, d["res_line"] or "RES/CANARY: not recorded"]
        self.koma = bool(d["build_id"]) and int(d["build_id"], 16) != 0
        if not d["header_ok"] or not self.koma:
            step["status"] = FAIL
            step["summary"] = "KOMALOG.TXT present but no header or no build id"
            return
        step["status"] = PASS
        step["summary"] = (f"build {d['build_id']}, POOL {d['pool'] if d['pool'] is not None else '--'}, "
                           f"STAGE {d['stage']}")

    def step_photos(self, step):
        r = KB.list_photos(self.link)
        self.photos = r["photos"]
        self.save("photos.json", json.dumps(r, indent=1).encode())
        for p in self.photos[:5]:
            step["details"].append(f"{p['path']}: {p['size']:,} B, {p['width']}x{p['height']}, {p['date']}")
        if len(self.photos) > 5:
            step["details"].append(f"... {len(self.photos) - 5} more (photos.json)")
        step["count"] = len(self.photos)
        step["status"] = PASS
        step["summary"] = f"{len(self.photos)} JPEG(s)"

    def step_throughput(self, step):
        rates, parts = {}, []
        if self.photos:
            big = max(self.photos, key=lambda p: p["size"])
            n, s = self._timed_retr(f"{KB.DCIM}/{big['path']}")
            rates["jpeg"] = {"path": big["path"], "bytes": n, "seconds": round(s, 4),
                             "bytes_per_s": int(n / s) if s > 0 else None}
            parts.append(f"JPEG {_rate(n, s)}")
            step["details"].append(f"RETR {big['path']}: {n:,} B in {s:.3f} s = {_rate(n, s)} "
                                   "(stock RETR ran about 4.09 MB/s on a camera)")
        if self.cur1 is not None:
            n, s = self._timed_retr(KB.PARAM_CUR)
            rates["param_cur"] = {"bytes": n, "seconds": round(s, 4), "bytes_per_s": int(n / s) if s > 0 else None}
            parts.append(f"PARAM.CUR {s * 1000:.1f} ms")
            step["details"].append(f"RETR PARAM.CUR: {n:,} B in {s:.3f} s = {_rate(n, s)}")
        step["rates"] = rates
        if not rates:
            step["summary"] = "nothing to time (no JPEG, no PARAM.CUR)"
            return
        step["status"] = PASS
        step["summary"] = ", ".join(parts)

    def _begin_session(self, step) -> bool:
        """Steps 6-8 share ONE remote-control session: remtin/diaset (and the -LVFR 0 wait) run once, here, in the first
        of them; the session ends in _end_session after step 8. False (and nothing left on) if the camera refuses;
        False with remote control left on if it never armed (no -LVFR 150 after the re-arms; no key was pressed)."""
        if self.link.remote_on:
            return True
        r = KB.tether_start(self.link)
        step["details"] += [f"{x['command']} -> {x['reply']}" for x in r["replies"]]
        self._note_lvfr(step, "start", r["replies"])
        if not r["ok"]:
            KB.tether_stop(self.link)
            step["status"], step["summary"] = FAIL, "the camera refused remote control"
            return False
        if not r.get("armed", True):
            # remote control stays on: the next step re-arms (diaset,4 + -LVFR wait) before it goes on
            self.live_dropped = True
            step["status"], step["summary"] = FAIL, r["message"]
            return False
        return True

    def _relive(self, step):
        """After a RETR or -INFO inside the remote session the camera drops live view (seen on hardware): re-send
        diaset,4 and wait for -LVFR 0 to answer 150 (KB.wait_lvfr, link.lvfr_wait_s) before step 7 or 8 goes on."""
        if not (self.live_dropped and self.link.remote_on):
            return
        self.live_dropped = False

        def fn(s):
            log = []
            KB._cctl(s, f"diaset,{KB.TETHER_DIAL}", log)
            armed = KB.wait_lvfr(self.link, s, log) is not False
            if self.link.tether:
                self.link.tether["armed"] = armed
            return log
        log = self.link.run(fn)
        step["details"] += [f"re-entry after RETR/-INFO: {x['command']} -> {x['reply']}" for x in log]
        self._note_lvfr(step, "re-entry", log)

    def _note_lvfr(self, step, when: str, log: list):
        """Record this step's -LVFR 0 wait (time to the first 150, or None on a timeout) for the step's summary."""
        if any(x["command"] == "-LVFR 0 (wait)" for x in log):
            step.setdefault("lvfr_150", []).append({"when": when, "s": self.link.lvfr_150_s})

    def _end_session(self, step):
        """keyset,0,2 + remtout, once, whatever the steps did; idempotent."""
        if not self.link.remote_on:
            return
        try:
            stop = KB.tether_stop(self.link)
            step["details"] += [f"{x['command']} -> {x['reply']}" for x in stop["replies"]]
        except Exception as error:  # noqa: BLE001
            step["details"].append(f"/tether/stop failed: {type(error).__name__}: {error}")

    def step_tether(self, step):
        if not self._begin_session(step):
            return
        try:
            shot = KB.tether_shoot(self.link, True)
        finally:
            self.live_dropped = True  # the shoot polls -INFO and RETRs the JPEG inside the session
        step["details"] += [f"{x['command']} -> {x['reply']}" for x in shot.get("replies", [])]
        step["outcome"] = shot.get("outcome")
        if not shot.get("ok"):
            step["status"], step["summary"] = FAIL, f"{shot.get('outcome')}: {shot.get('message')}"
            return
        photo = shot["photo"]
        name = self.save(f"tether-{photo['name']}", base64.b64decode(shot["jpeg"]))
        tag_note = self._makernote(self.out / name)
        step["makernote_tag"] = tag_note[0]
        step["details"].append(tag_note[1])
        step["status"] = PASS
        step["summary"] = f"shot {photo['path']} ({photo['size']:,} B); {tag_note[1]}"

    def _makernote(self, path: Path) -> tuple[str | None, str]:
        meta = VS._metadata()
        if meta is None:
            return None, "MakerNote: reader tools/spare_test/metadata.py not loadable"
        try:
            rev = meta.read(path).get("rev")
        except Exception as error:  # noqa: BLE001
            return None, f"MakerNote: none readable ({str(error).split(': ')[-1]})"
        ok = isinstance(rev, str) and rev.startswith(EXPECTED_TAG)
        return rev, f"MakerNote tag {rev!r}" + ("" if ok else f" (expected {EXPECTED_TAG!r})")

    def _pull(self, mode: int) -> dict:
        """One frame of `mode`, retrying a 451 for LIVE_OFF_GRACE_S (the rec-review pause after a shot)."""
        deadline = self.clock() + LIVE_OFF_GRACE_S
        while True:
            try:
                return KB.live_pull(self.link, mode)
            except KB.LiveOff:
                if self.clock() >= deadline:
                    raise
                self.sleep(0.1)

    def _live_mode(self, mode: int) -> dict:
        res = {"mode": mode, "ok": False, "frames": 0, "bytes": 0, "torn": 0, "sync_timeouts": 0, "truncated": 0}
        gap = 1.0 / self.link.live_max_fps
        t0 = last = self.clock()
        try:
            for i in range(self.live_pulls):
                wait = last + gap - self.clock()
                if i and wait > 0:
                    self.sleep(wait)
                last = self.clock()
                frame = self._pull(mode)
                res["frames"] += 1
                res["bytes"] += KB.LVFR_HEADER.size + len(frame["data"])
                res["torn"] += frame["torn"]
                res["sync_timeouts"] += frame["sync_timeout"]
                res["truncated"] += frame["truncated"]
                if i == 0:
                    image, _ = KB.frame_image(frame)
                    res["size"] = f"{frame['width']}x{frame['height']}"
                    res["file"] = self.save(f"live-mode{mode}.{LIVE_EXT[mode]}", image)
                    if frame.get("palette_note"):
                        res["note"] = frame["palette_note"]
            res["ok"] = True
        except KB.LiveOff as off:
            res["error"] = f"camera answered {off.reply!r} ({KB.live_off_message(mode)})"
        except KB.ProtocolError as error:
            res["error"] = f"bad frame: {error}"
        span = self.clock() - t0
        res["seconds"] = round(span, 3)
        res["fps"] = round(res["frames"] / span, 1) if span > 0 else None
        res["bytes_per_s"] = int(res["bytes"] / span) if span > 0 else None
        return res

    def step_live(self, step):
        if not self._begin_session(step):
            return
        self._relive(step)
        modes = [self._live_mode(m) for m in LIVE_MODES]
        step["modes"] = modes
        for m in modes:
            line = (f"mode {m['mode']}: {m['frames']}/{self.live_pulls} frames, {m.get('size', '?')}, "
                    f"{m['fps']} fps, {m['bytes_per_s']:,} B/s, torn {m['torn']}, sync timeouts {m['sync_timeouts']}, "
                    f"truncated {m['truncated']}" + (f", {m['note']}" if m.get("note") else ""))
            if m.get("error"):
                line += f"; FAILED: {m['error']}"
            step["details"].append(line)
        bad = [m["mode"] for m in modes if not m["ok"]]
        step["status"] = FAIL if bad else PASS
        step["summary"] = "; ".join(f"mode {m['mode']} {m['fps']} fps" if m["ok"] else f"mode {m['mode']} FAILED"
                                    for m in modes)

    def step_exposure(self, step):
        link = self.link
        if not self._begin_session(step):
            return
        self._relive(step)
        before = changed = None
        try:
            before = KB.ram_read(link, EV_OFF, 1)["hex"]
            before = int(before, 16)
            step["details"].append(f"-KGET {EV_OFF},1 (EV byte 0x{EV_OFF:03x}) -> {before} ({ev_text(before)})")
            if before not in KB.KSET_STOCK[EV_OFF]:
                step["status"], step["summary"] = FAIL, f"EV byte is {before}, not a stock value; nothing written"
                return
            target = before + 2 if before + 2 <= EV_VALUES[-1] else before - 2
            res = KB.ram_write(link, KB._ram_checked([{"off": EV_OFF, "val": target}]))
            changed, t_set = target, link.clock()
            step["details"].append(f"-KSET {EV_OFF},{target} -> 200 ({ev_text(target)})" + busy_text(res))
            after = int(KB.ram_read(link, EV_OFF, 1)["hex"], 16)
            step["details"].append(f"-KGET {EV_OFF},1 -> {after}")
            self.sleep(self.ev_settle_s)
            res = KB.ram_write(link, KB._ram_checked([{"off": EV_OFF, "val": before}]),
                               busy_wait_s=self.ev_restore_wait_s)
            changed = None
            # measured: how long -KSET kept answering busy after the EV change (0 if the restore went in first try)
            step["busy_after_ev_s"] = round(link.clock() - t_set, 2) if res["busy_retries"] else 0.0
            restored = int(KB.ram_read(link, EV_OFF, 1)["hex"], 16)
            step["details"].append(f"-KSET {EV_OFF},{before} (restore, {self.ev_settle_s:g} s later) -> 200"
                                   + busy_text(res) + f"; -KGET -> {restored}")
            step["details"].append(
                f"measured: busy for {step['busy_after_ev_s']:.2f} s after an EV change" if res["busy_retries"] else
                f"measured: not busy {self.ev_settle_s:g} s after an EV change (restore went in first try)")
            ok = after == target and restored == before
            step["status"] = PASS if ok else FAIL
            step["summary"] = (f"{ev_text(before)} -> {ev_text(target)} -> {ev_text(before)}" if ok else
                               f"read back {after} after writing {target}, {restored} after restoring {before}")
        except KB.BridgeError as error:
            code = error.extra.get("code")
            if code == "camera_busy":
                step["status"], step["summary"] = FAIL, ("busy: the camera answered 451 -KSET busy (menu or ADJ "
                                                         "open, or not idle in still record)")
            elif code == "not_supported":
                step["status"], step["summary"] = FAIL, KB.RAM_NOT_SUPPORTED
            else:
                raise
            step["details"].append(f"{error.status}: {error.message} {error.extra.get('reply', '')}".strip()
                                   + busy_text(error.extra))
        finally:
            if changed is not None and before is not None:  # never leave the EV byte moved
                try:
                    KB.ram_write(link, KB._ram_checked([{"off": EV_OFF, "val": before}]),
                                 busy_wait_s=self.ev_restore_wait_s)
                    step["details"].append(f"restore after error: -KSET {EV_OFF},{before} sent")
                except Exception as error:  # noqa: BLE001
                    step["details"].append(f"RESTORE FAILED: EV byte may still be {changed} ({error}); set EV by hand")

    def step_recheck(self, step):
        bad = []
        cur2 = self._read_optional(KB.PARAM_CUR)
        if cur2 is not None:
            self.save("PARAM-2.CUR", cur2)
        if self.cur1 is None and cur2 is None:
            step["details"].append("PARAM.CUR: absent both times")
        elif cur2 == self.cur1:
            step["details"].append("PARAM.CUR: unchanged (byte-identical to step 2)")
        else:
            bad.append("PARAM.CUR changed")
            step["details"].append("PARAM.CUR: CHANGED since step 2 (it should not; no write touches the card)")
            if self.cur1 is not None and cur2 is not None and len(cur2) == len(self.cur1) == 4096:
                m = KB.load_map()
                rows, ranges = komaset.diff_blocks(bytearray(cur2), bytearray(self.cur1), m)
                step["details"] += [f"  {f.name} (0x{f.offset:03x}): now {va}, was {vb}" for f, va, vb in rows[:30]]
                step["details"] += [f"  unmapped 0x{s:03x}-0x{e - 1:03x}" for s, e in ranges[:20]]
        log2 = self._read_optional(KOMALOG)
        if log2 is not None:
            self.save("KOMALOG-2.TXT", log2)
        if self.log1 is None and log2 is None:
            step["details"].append("KOMALOG.TXT: absent both times")
        elif log2 == self.log1:
            step["details"].append("KOMALOG.TXT: unchanged")
        else:
            step["details"].append("KOMALOG.TXT: changed since step 3 (no reboot happened; a finding)")
        if self.cur1 is None and cur2 is None and self.log1 is None and log2 is None:
            step["summary"] = "nothing to compare (no PARAM.CUR, no KOMALOG.TXT)"
            return
        step["status"] = FAIL if bad else PASS
        if bad:
            step["summary"] = "; ".join(bad)
        else:
            step["summary"] = ("PARAM.CUR unchanged" if cur2 is not None else "no PARAM.CUR") + (
                ", KOMALOG changed" if log2 != self.log1 else ", KOMALOG unchanged" if log2 is not None else "")

    # ------------------------------------------------------------------ report
    def _write(self, report: dict):
        sendback = sorted(set(self.artifacts) | {"report.md", "report.json"})
        report["send_back"] = sendback
        (self.out / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
        (self.out / "report.md").write_text(render_md(report))


def render_md(r: dict) -> str:
    sim = " (SIMULATED camera: nothing here is a hardware result)" if r["simulated"] else ""
    lines = [f"# Koma USB self-test{sim}", "",
             f"- started {r['started']}, komabridge {r['bridge_version']}, backend `{r['backend']}`",
             f"- camera firmware {r['firmware'] or '--'}; Koma build "
             f"{'found' if r['koma'] else 'not found (steps 6-8 skipped)'}",
             f"- result: **{r['result']}**", ""]
    for s in r["steps"]:
        lines.append(f"## {s['n']}. {s['title']}: {s['status']} ({s['seconds']:.2f} s)")
        lines.append("")
        lines.append(s["summary"] or "-")
        if s["details"]:
            lines.append("")
            lines += ["    " + d.replace("\n", "\n    ") for d in s["details"]]
        if s["artifacts"]:
            lines += ["", "Saved: " + ", ".join(f"`{a}`" for a in s["artifacts"])]
        lines.append("")
    lines += ["## Summary", "", "| # | Step | Result | Time | Note |", "|---|---|---|---|---|"]
    for s in r["steps"]:
        note = (s["summary"] or "").replace("|", "/")
        lines.append(f"| {s['n']} | {s['title']} | {s['status']} | {s['seconds']:.1f} s | {note} |")
    lines += ["", "## Send back", "", f"Everything in `{r['out']}`:", ""]
    lines += [f"- `{name}`" for name in r["send_back"]]
    lines += ["- the terminal output of this run", ""]
    return "\n".join(lines)


def run(link: "KB.Link", out, log=None, connect_wait_s: float = 0.0) -> int:
    """komabridge --selftest: run, write the report, print where it is. 0 when no step FAILED, else 1."""
    t = SelfTest(link, Path(out), log=log, connect_wait_s=connect_wait_s)
    report = t.run()
    if log:
        log(f"selftest {report['result']}: {t.out / 'report.md'}")
    return 0 if report["result"] == PASS else 1
