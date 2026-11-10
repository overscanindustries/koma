"""emulink: the bridge's third backend (`komabridge.py --emu`). Simulated; never touches hardware.

Drives the real 2.40 firmware plus a Koma module in the Koma emulator (tools/koma/emu, Unicorn), booted the way the
tether and live-view scenarios boot it:
module-loader image built in memory, the module as /ATA1/GRMOD.BIN on the modelled card, the loader installs it, the
model's internal memory is formatted with the menu keys, then USB attach (real 0x131b3a(0) in tOpeMain's poll
context). After that every command line the bridge writes goes through the firmware's own command state machine
0xcb6c4 (parser 0xcbd64, dispatcher 0xcc304, the stock or module handler), driven with the events the USB task would
send: 0x11 command received, 0x10 reply sent, 0x20 data frame sent, 0x21 data frame received. Replies and channel-2
data frames are taken from the state machine's output pointers and wrapped in camera frames for grdusb's FrameReader.

What is not the firmware (stand-ins, also listed in EmuCamera.standins):
  - USB controller, the qDCX queue, the receive path 0xcaf74 and the frame build 0xcb0d4: bypassed. Command text and
    uploaded data are placed on tOpeMain's stack below a 0x240-byte frame; uploads go in 0x200-byte pieces.
  - The card: grdemu's FileLayer stub at the file API (0x55dc0 family): /ATA1 root files only, in memory, no FAT, no
    directories. FileLayer does not stub 0x56204, the block writer -STOR uses (0xcb21c), so it is routed to
    FileLayer's write for FileLayer's own descriptors (CARD_WRITE). Verbs whose firmware path needs the FAT directory code (-NLST, and -INFO/-FDAT/PWD if the firmware
    path fails) fall back to a host model of that card (HOST_VERBS, reported per verb in `routes`).
  - Live view (-LVFR modes 0/1): the resize/JPEG engine semaphores return at once, 0x1201c018 = 2000 and
    0x1200c16c = 0x300 are supplied, FF D9 is written at the scan end; the CC1 timing model runs VD interrupts only
    around -LVFR. No sensor exists, so mode 0 is whatever is in the ring and mode 1 is not a decodable JPEG.
  - Time: the emulator advances only when the bridge asks for something: before each command it runs for the wall
    time since the last command (EMU_RATE instructions per second, capped at ADVANCE_CAP).
  - poff: the firmware handler answers (451 in 2.40 even when the restart was armed, by static reading: mg1_poff
    returns 2 after the restart post 0x66ab4, and 0xcd000 answers 200 only for 0; in the model mg1_poff fails earlier,
    saving CONFIG.TST, so 0x66ab4 is not reached); then the emulator is rebooted from scratch with the card contents kept
    (boot import of PARAM.DAT runs for real); everything else in RAM (settings not imported) starts from defaults.
"""
from __future__ import annotations

import concurrent.futures
import importlib.util
import os
import struct
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TOOLS = ROOT / "tools"
for p in (str(TOOLS), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from grdusb import descriptors  # noqa: E402
from grdusb.mock import _dirent, _frame  # noqa: E402
from grdusb.transport import TransportError, TransportTimeout  # noqa: E402

DEFAULT_MODULE = ROOT / "analysis/koma-0.4-candidate/koma04.grmod"
TETHER_SCENARIO = ROOT / "analysis/tether-feature/scenario.py"

XSTATE, USB_BUF = 0x1BD2D0, 0x1BD308
USB_BUF_LITERAL = 0x0BF844E0          # literal 0x537d0 (tDcx init 0x5373e is not run in the model)
DCX = 0xCB6C4
EV_COMMAND, EV_REPLY_SENT, EV_DATA_SENT, EV_DATA_RECEIVED = 0x11, 0x10, 0x20, 0x21
ROOM = 0x240                          # stack bytes reserved below tOpeMain's SP for one call (text at +0x20)
TEXT_MAX = ROOM - 0x40
UPLOAD_PIECE = 0x200
EMU_RATE = 1.5e7                      # virtual instructions per wall second between commands
ADVANCE_MIN, ADVANCE_CAP = int(2e6), int(6e7)
# -LVFR stand-ins
SUPPLIED = {0x1201C018: 2000, 0x1200C16C: 0x300}
SEM_OBTAIN, JPEG_RET, SCR_OUT = 0x5D978, 0x5E5F4, 0x859C000
# verbs the firmware path cannot serve with grdemu's flat FileLayer; answered by the host card model instead
HOST_VERBS = {"-NLST"}
POFF_ARM = 0x66AB4        # mg1_poff: reached only after CONFIG.TST was saved; posts the restart, then mg1_poff returns 2
WRITE_BLOCKS = 0x56204    # -STOR's write (0xcb21c -> 0x56204 -> FAT 0xb8f2c); FileLayer stubs 0x560ec only


def deps_ok() -> tuple[bool, str]:
    """(True, "") when --emu can run: unicorn imports, KOMA_FIRMWARE names a file, the koma package imports."""
    fwp = os.environ.get("KOMA_FIRMWARE")
    if not fwp or not Path(fwp).is_file():
        return False, "KOMA_FIRMWARE is not set to the 2.40 firmware file"
    try:
        import unicorn  # noqa: F401
        import koma.firmware  # noqa: F401
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"
    return True, ""


def _load_tether():
    spec = importlib.util.spec_from_file_location("tether_scenario_emulink", TETHER_SCENARIO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _session_class(T):
    """The tether scenario's session plus raw 0xcb6c4 calls with a larger stack frame (ROOM) than Tether.call's 0x40."""
    from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_SP)

    class EmuSession(T.Tether):
        def __init__(self, *a, **kw):
            self.raw, self.raw_ret = [], None
            super().__init__(*a, **kw)

        def _on_poll(self, m):
            if self.raw_ret is not None:
                lr, sp, frame, out = self.raw_ret
                self.raw_ret = None
                p, n, t = m.u32(frame + 0x10), m.u32(frame + 0x14), m.u32(frame + 0x18)
                out.update(t=t, n=n, xstate=m.u32(XSTATE))
                if t == 1 and n <= 0x10000:
                    out["data"] = bytes(m.uc.mem_read(p + 8, n))
                elif p and p != 0xDEADBEEF and 0 < n < 0x400:
                    out["reply"] = bytes(m.uc.mem_read(p, n))
                elif p and p != 0xDEADBEEF:
                    out["reply"] = m.cstr(p, 0x100).encode("latin-1")
                m.uc.reg_write(UC_ARM_REG_LR, lr)
                m.uc.reg_write(UC_ARM_REG_SP, sp)
                return
            if (self.raw and self._saved_lr is None and self._call_ret is None and not self.calls
                    and not self.pending_inject):
                event, data, out = self.raw.pop(0)
                lr, sp = m.uc.reg_read(UC_ARM_REG_LR), m.uc.reg_read(UC_ARM_REG_SP)
                frame = (sp - ROOM) & ~7
                m.uc.mem_write(frame + 0x20, data + b"\0")
                for off in (0x10, 0x14, 0x18):
                    m.w32(frame + off, 0xDEADBEEF)
                m.w32(frame, frame + 0x14)           # stack arg 5: int *len
                m.w32(frame + 4, frame + 0x18)       # stack arg 6: int *t (1 = data frame)
                for reg, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3),
                                  (event, frame + 0x20, len(data), frame + 0x10)):
                    m.uc.reg_write(reg, v)
                m.uc.reg_write(UC_ARM_REG_SP, frame)
                m.uc.reg_write(UC_ARM_REG_LR, T.S.POLL | 1)
                m.uc.reg_write(UC_ARM_REG_PC, DCX | 1)
                self.raw_ret = (lr, sp, frame, out)
                return
            super()._on_poll(m)
    return EmuSession


class EmuCamera:
    """One emulated camera. Every Unicorn call happens on one worker thread (a single-thread executor); the public
    methods submit work there and wait. `lock` guards the small state shared with the HTTP threads."""

    def __init__(self, module: bytes | None = None, card: dict | None = None, log=print):
        self.module = module if module is not None else DEFAULT_MODULE.read_bytes()
        self.card0 = dict(card or {})
        self.log = log
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="emu")
        self.lock = threading.Lock()
        self.ready = False          # booted and attached: the bridge sees a camera
        self.booting = None         # future of a running boot
        self.boots = 0
        self.s = None
        self.timings: dict = {}     # name -> list of seconds
        self.routes: dict = {}      # verb -> "firmware" | "host"
        self.last_wall = None
        self.armed = False
        self.boot_info: dict = {}
        self.T = None
        self.pending_reboot = False
        self.upload_open = False
        self.poff_armed = False
        self.poff_log: list = []

    # ---- worker plumbing
    def _do(self, fn, timeout=600):
        return self.pool.submit(fn).result(timeout=timeout)

    def _time(self, name, t0):
        self.timings.setdefault(name, []).append(round(time.time() - t0, 3))

    def start(self, wait=False):
        """Boot in the background (or wait for it). The backend reports no camera until it is up."""
        with self.lock:
            self.ready = False
            card = self.card0 if self.s is None else dict(self.s.emu.files.files)
            self.booting = self.pool.submit(self._boot, card)
        if wait:
            self.booting.result()
        return self.booting

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ---- boot (worker thread)
    def _boot(self, card: dict):
        t0 = time.time()
        if self.T is None:
            self.T = _load_tether()
            self.Session = _session_class(self.T)
        T = self.T
        import tempfile
        from koma import features as F
        from koma import firmware as fw
        from koma.emu.grdui import session as S
        from koma.inspector import parse
        S.RUNS = Path(tempfile.mkdtemp(prefix="komabridge-emu-"))
        names = [f["name"] for f in parse(self.module)["manifest"]["features"]]
        files = {k: v for k, v in card.items() if k != "GRMOD.BIN"}
        files["GRMOD.BIN"] = self.module
        self.boots += 1
        s = self.Session(f"bridge-emu-{self.boots}", keys={}, log_access=False, files=files, image=fw.loader_image())
        m = s.m
        exits = []
        for addr, n in T.LOADER_EXITS.items():
            m.at(addr, lambda mm, n=n: exits.append(n))
        for n in names:   # feature-declared install-time stubs, exactly as koma smoke applies them
            for addr in getattr(F.module(n), "SMOKE_STUBS", {}):
                def stub(mm):
                    if "module_returned" not in exits:
                        mm.return_from_stub(0)
                m.at(addr & ~1, stub)
        self._install_lvfr_standins(s)
        fl = s.emu.files
        m.at(WRITE_BLOCKS, lambda mm: fl._write(mm))   # CARD_WRITE: -STOR's writer, routed to FileLayer for its fds
        m.at(POFF_ARM, lambda mm: setattr(self, "poff_armed", True))
        s.run_until(int(5e8))
        s.sync_dial()
        s.run_for(int(2e7))
        keys = list(T.FORMAT_KEYS)
        if "menu5" in names:
            keys[2:2] = [(T.RIGHT, 3e7)] * 3
        for code, wait in keys:
            s.tap(code)
            s.run_for(int(wait))
        s.run_for(int(1e7))
        s.emu.cc1_timing.mode = "irq-suppressed"
        s.call("usb-attach 0x131b3a(0)", 0x131B3A, args=(0,))
        T.drain(s)
        s.run_for(int(1.5e8))
        standins = []
        if m.u32(XSTATE) != 1:
            standins.append(f"[0x1bd2d0] was {m.u32(XSTATE)}, set to 1 (idle)")
            m.w32(XSTATE, 1)
        if m.u32(USB_BUF) == 0:
            standins.append("[0x1bd308] was 0, set to 0x0bf844e0 (literal 0x537d0)")
            m.w32(USB_BUF, USB_BUF_LITERAL)
        self.s = s
        self.boot_info = dict(boot=self.boots, features=names, loader_exits=exits, vt=m.vt,
                              app_state=m.u32(0x1BD1C0), usb_attached=m.u32(0x23A888), standins=standins,
                              card=sorted(s.emu.files.files), stop_reason=m.stop_reason, wall_s=round(time.time() - t0, 1))
        self._time("boot", t0)
        self.log(f"emu: booted ({self.boot_info['wall_s']} s, app state {self.boot_info['app_state']}, "
                 f"card {self.boot_info['card']})")
        with self.lock:
            self.ready = True
            self.last_wall = time.time()
        return self.boot_info

    def _install_lvfr_standins(self, s):
        m, unk = s.m, s.emu.unknown
        rd = unk.read

        def read(addr, size):
            if self.armed and addr in SUPPLIED:
                return SUPPLIED[addr]
            return rd(addr, size)
        unk.read = read

        def sem(mm):
            if self.armed and mm.r(0) in (mm.u32(0x1BD9D0), mm.u32(0x1BD9C4)):
                mm.return_from_stub(0)
        m.at(SEM_OBTAIN, sem)

        def jpeg_ret(mm):
            n = mm.r(0)
            if self.armed and 2 <= n <= 0x10000:
                mm.uc.mem_write(SCR_OUT + n - 2, b"\xff\xd9")
        m.at(JPEG_RET, jpeg_ret)

    # ---- one 0xcb6c4 call (worker thread)
    def _dcx(self, event: int, data: bytes = b""):
        """One 0xcb6c4(event, buf, len, &reply, &len, &t) call in tOpeMain's poll context (EmuSession.raw)."""
        s, m = self.s, self.s.m
        if len(data) > TEXT_MAX:
            raise TransportError(f"emulink: {len(data)} bytes do not fit the call frame")
        out = {}
        s.raw.append((event, data, out))
        end = m.vt + int(4e8)
        while (s.raw or s.raw_ret is not None) and m.vt < end:
            r = m.stop_reason
            if r is not None and not r.startswith("horizon"):
                break
            s.run_for(int(1e6))
        if "xstate" not in out:
            s.raw.clear()
            raise TransportTimeout(f"emulink: 0xcb6c4 event {event:#x} did not return (stop: {m.stop_reason})")
        return out

    # ---- one exchange (worker thread)
    def _advance(self):
        """Run the firmware for the wall time the bridge was idle since the last exchange ended (not counting the
        emulator's own work, which would feed back into ever longer advances), at least ADVANCE_MIN, at most ADVANCE_CAP."""
        now = time.time()
        dt = now - (self.last_wall or now)
        n = int(min(max(dt * EMU_RATE, ADVANCE_MIN), ADVANCE_CAP))
        self.s.run_for(n)

    def _command(self, line: bytes) -> list:
        """Run one command line through the firmware; returns [(channel, payload)] in wire order."""
        t0 = time.time()
        text = line.decode("latin-1").rstrip("\r\n")
        verb = text.split(" ", 1)[0].upper()
        self._advance()
        if verb in HOST_VERBS:
            self.routes[verb] = "host"
            out = self._host(verb, text.partition(" ")[2])
            self.last_wall = time.time()
            self._time(verb, t0)
            return out
        lvfr = verb == "-LVFR"
        self.poff_armed = False
        if lvfr:
            self.armed = True
            self.s.emu.cc1_timing.mode = "running"
        try:
            out = self._firmware(line)
        finally:
            if lvfr:
                self.armed = False
                self.s.emu.cc1_timing.mode = "irq-suppressed"
        self.routes[verb] = "firmware"
        self.last_wall = time.time()
        if verb == "-CCTL" and text.lower().startswith("-cctl poff"):
            # 2.40 answers 451 even when the restart was armed (mg1_poff returns 2 after 0x66ab4; 0xcd000 answers 200
            # only for 0), so the reboot follows every poff; whether 0x66ab4 ran is kept in poff_log.
            self.poff_log.append(dict(reply=out[0][1].decode("latin-1").strip() if out else None,
                                      armed_0x66ab4=self.poff_armed))
            self.pending_reboot = True
        self._time(verb, t0)
        return out

    def _firmware(self, line: bytes) -> list:
        m = self.s.m
        if m.u32(XSTATE) != 1:
            m.w32(XSTATE, 1)        # an earlier exchange may have left the machine mid-transfer; start clean
        r = self._dcx(EV_COMMAND, line)
        out = [(1, r.get("reply", b""))] if r.get("reply") else []
        st = r["xstate"]
        self.upload_open = False
        if st == 3:
            self._dcx(EV_REPLY_SENT)
        elif st == 4:
            ev = EV_REPLY_SENT
            for _ in range(4096):
                r = self._dcx(ev)
                ev = EV_DATA_SENT
                if r.get("t") == 1:
                    out.append((2, r.get("data", b"")))
                    continue
                if r.get("reply"):
                    out.append((1, r["reply"]))
                if r["xstate"] == 3:
                    self._dcx(EV_REPLY_SENT)
                break
        elif st == 5:
            self._dcx(EV_REPLY_SENT)        # -> state 7, waiting for data
            self.upload_open = True
        return out

    def _upload(self, payload: bytes) -> list:
        t0 = time.time()
        out = []
        for i in range(0, len(payload), UPLOAD_PIECE):
            r = self._dcx(EV_DATA_RECEIVED, payload[i:i + UPLOAD_PIECE])
            if r.get("reply"):
                out.append((1, r["reply"]))
            if r["xstate"] == 3:
                self._dcx(EV_REPLY_SENT)
                self.upload_open = False
                break
        self.last_wall = time.time()
        self._time("upload", t0)
        return out

    # ---- host card model (the verbs FileLayer cannot serve through the firmware)
    def _host(self, verb: str, arg: str) -> list:
        files = self.s.emu.files.files
        if verb == "-NLST":
            if arg == "/":
                return [(1, b"150 data connection for -NLST.\r\n"), (2, b"/EXT0"), (1, b"250 -NLST command success.\r\n")]
            if arg.upper() != "/EXT0":
                return [(1, b"450 Open error\r\n")]   # FileLayer has no directories (no DCIM)
            data = b"".join(_dirent(*n.partition(".")[::2], 0x20, len(b)) for n, b in sorted(files.items()))
            out = [(1, b"150 data connection for -NLST.\r\n")]
            if data:
                out.append((2, data))
            return out + [(1, b"250 -NLST command success.\r\n")]
        raise AssertionError(verb)

    # ---- public (any thread)
    def command(self, line: bytes, timeout=120) -> list:
        self.pending_reboot = False
        out = self._do(lambda: self._command(line), timeout)
        if self.pending_reboot:
            self.reboot()
        return out

    def upload(self, payload: bytes, timeout=120) -> list:
        return self._do(lambda: self._upload(payload), timeout)

    def reboot(self):
        """poff: keep the card, boot again in the background. The bridge sees no camera until the boot is done."""
        self.log("emu: poff -> rebooting the emulator with the card kept")
        with self.lock:
            self.ready = False
        return self.start(wait=False)

    def card(self) -> dict:
        return self._do(lambda: dict(self.s.emu.files.files))

    def peek(self, fn, timeout=60):
        """Run fn(session) on the worker (tests and diagnostics)."""
        return self._do(lambda: fn(self.s), timeout)

    def info(self) -> dict:
        err = None
        if self.booting is not None and self.booting.done() and self.booting.exception() is not None:
            err = f"{type(self.booting.exception()).__name__}: {self.booting.exception()}"
        return dict(ready=self.ready, boot_error=err, poff=list(self.poff_log), boots=self.boots, boot=self.boot_info, routes=dict(self.routes),
                    timings={k: dict(n=len(v), last=v[-1], max=max(v)) for k, v in self.timings.items()})


class EmuTransport:
    """grdusb transport interface over EmuCamera: write() takes host frames, read() hands back camera frames."""

    def __init__(self, cam: EmuCamera):
        self.cam = cam
        self._out = bytearray()
        self._buf = bytearray()
        self.closed = False

    def clock(self) -> float:
        return time.monotonic()

    def bring_up(self) -> None:
        if not self.cam.ready:
            raise TransportError("emulated camera is not booted")

    def write(self, data: bytes) -> int:
        if self.closed or not self.cam.ready:
            raise TransportError("emulated camera is gone")
        self._buf += data
        while len(self._buf) >= 8:
            sof, channel, _, length = struct.unpack_from("<BBHI", self._buf, 0)
            total = (8 + length + 1 + 3) & ~3
            if sof != 1 or len(self._buf) < total:
                break
            payload = bytes(self._buf[8:8 + length])
            del self._buf[:total]
            frames = self.cam.command(payload) if channel == 1 else self.cam.upload(payload)
            for ch, body in frames:
                self._out += _frame(ch, body)
        return len(data)

    def read(self) -> bytes:
        if not self._out:
            if not self.cam.ready:
                raise TransportError("emulated camera is gone")
            time.sleep(0.01)
            return b""
        chunk = bytes(self._out[:0x10400])
        del self._out[:0x10400]
        return chunk

    def close(self) -> None:
        self.closed = True


class EmuBackend:
    """The bridge backend: one EmuCamera, booted at start. Enumeration is "a camera in ORIGINAL mode" once booted."""
    simulated = True
    name = "emu"

    def __init__(self, module: bytes | None = None, card: dict | None = None, wait=False, log=print):
        self.cam = EmuCamera(module, card, log=log)
        self.cam.start(wait=wait)

    def scan(self) -> list:
        if not self.cam.ready:
            return []
        return [_Dev()]

    def info(self) -> dict:
        i = self.cam.info()
        i["booting"] = not self.cam.ready
        return i

    def open(self):
        t = EmuTransport(self.cam)
        t.bring_up()
        return t


class _Dev:
    """What Link._ensure reads from an enumerated device: the product id (ORIGINAL mode)."""
    vendor = descriptors.VENDOR
    product = descriptors.PRODUCT_ORIGINAL
