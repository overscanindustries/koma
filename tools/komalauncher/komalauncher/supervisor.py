"""The launcher's engine: runs komabridge as a child process, keeps it alive, and reads what the menu shows.

Everything happens on one supervisor thread, so there are no races between a restart, a look and Quit: the UI only
calls command() and look() (any thread) and listens to changes (model.Snapshot, fired from the supervisor thread).

The bridge is this same program started with `--child bridge` (child.py). It reads its stdin: a line "stop", or the
pipe closing because the launcher died, makes it stop the way Ctrl-C does. So a bridge never outlives the launcher.

What the launcher reads, and what that costs the camera:
  GET /api and GET /         readiness (the page reaches the bridge). No camera traffic.
  GET :8770/status.json      komacam's own state, for "Webcam running". No camera traffic.
  GET /status                the camera line, only while someone looks (the menu is open). On a camera that is on
                             USB and not open yet, the bridge opens it and sends -VER once (komabridge.py, Link).
  GET /status at Quit        whether the app left remote control on; then POST /live/stop and /tether/stop.
The launcher runs no webcam and presses nothing on the camera.

Before the first real-mode start it runs `--child usbcheck` once (child.py: pyusb imported, libusb's backend loaded,
no device enumerated). A failure goes to the snapshot's usb_problem and the bridge starts anyway, so card work and
the app keep going. On Linux it also looks for the udev rule (UDEV_RULE); without it the window offers Allow.

Evidence: the bridge and komacam behaviour this relies on is MOCK (tools/komaset/bridge, tools/komacam README).
"""
from __future__ import annotations

import collections
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from . import VERSION, model, paths

log = logging.getLogger("komalauncher")

BRIDGE_PORT = 8765         # http://localhost:8765
WEBCAM_PORT = 8770         # komacam's default; read only
POLL_S = 2.0               # the child's health and komacam's state while running; /status only while looking
READY_S = 30.0             # from spawn to "the page reaches the bridge"
BRIDGE_STOP_S = 8.0
TETHER_STOP_S = 35.0       # POST /tether/stop waits for an arm in progress
RESTART_DELAYS = (1.0, 2.0, 4.0)   # a bridge that does not come up is tried again after these pauses, then 'failed'
STABLE_S = 60.0            # a bridge that died sooner than this waits RESTART_DELAYS[0] before it is started again
STATUS_MISSES = 3          # the camera line goes after this many /status reads in a row got no answer
MOCK_BRIDGE_ARGS = ("--mock", "--mock-tether", "--mock-hw", "--mock-koma")
REAL_BRIDGE_ARGS = ("--real",)
USBCHECK_S = 20.0          # the usbcheck child: an import and a library load
UDEV_RULE = Path("/etc/udev/rules.d/70-koma-gr.rules")
LOG_ROLL_BYTES = 5 << 20
LOG_CHECK_S = 60.0

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy: loopback only


class Down(Exception):
    """Nothing answered on the port."""


def http(method: str, port: int, path: str, timeout: float, data: bytes | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.read()
        except OSError:
            return error.code, b""
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise Down(str(error)) from error


def get_json(port: int, path: str, timeout: float) -> dict | None:
    try:
        code, body = http("GET", port, path, timeout)
    except Down:
        return None
    if code != 200:
        return None
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def status_facts(status: dict | None) -> dict | None:
    """The part of GET /status the launcher shows. Live-view counters and the busy flag change every read while the
    camera works; keeping them out keeps the snapshot (and an open menu) still unless something shown changed."""
    if status is None:
        return None
    camera = status.get("camera") or {}
    return {"connected": bool(status.get("connected")), "simulated": bool(status.get("simulated")),
            "usb_mode": status.get("usb_mode"), "message": status.get("message"),
            "camera": {"model": camera.get("model")} if camera else None}


def usb_check(timeout: float = USBCHECK_S) -> str:
    """Run `--child usbcheck` (child.py) and return its error, or "" when this Python can use USB."""
    argv, env = child_command("usbcheck", [])
    kwargs: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        done = subprocess.run(argv, env=env, cwd=str(paths.TOOLS), stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"{type(error).__name__}: {error}"
    if done.returncode == 0:
        return ""
    return done.stderr.decode("utf-8", "replace").strip()[-2000:] or f"usbcheck exited {done.returncode}"


def port_in_use(port: int) -> bool:
    """True when something listens on the port (IPv4 or IPv6 loopback, where a browser's localhost can land) or the
    bridge could not bind it. The check binds the way the bridge does (127.0.0.1, SO_REUSEADDR off Windows), so a port
    in TIME_WAIT counts as free, as it is for the bridge."""
    for family, address in ((socket.AF_INET, ("127.0.0.1", port)), (socket.AF_INET6, ("::1", port))):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(address) == 0:
                    return True
        except OSError:
            continue  # no IPv6 here
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def child_command(kind: str, args: list[str]) -> tuple[list[str], dict]:
    """The command line that runs a child: this program with --child. Inside a bundle the stub executable is the
    interpreter (KOMA_LAUNCHER_STUB, set by packaging/stub.c); elsewhere `python -m komalauncher`."""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    if env.get("KOMA_LAUNCHER_STUB"):
        return [sys.executable, "--child", kind, *args], env
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(paths.LAUNCHER_DIR), env.get("PYTHONPATH", "")) if p)
    return [sys.executable, "-m", "komalauncher", "--child", kind, *args], env


class Child:
    """One child process with its log file and the stdin stop pipe."""

    def __init__(self, name: str, argv: list[str], env: dict, log_path: Path):
        self.name, self.argv, self.env, self.log_path = name, argv, env, log_path
        self.proc: subprocess.Popen | None = None
        self.started_at = 0.0

    def start(self) -> "Child":
        try:
            if self.log_path.stat().st_size > LOG_ROLL_BYTES:
                self.log_path.replace(self.log_path.with_suffix(".1.log"))
        except OSError:
            pass
        out = open(self.log_path, "ab", buffering=0)  # noqa: SIM115  (handed to the child; closed with it)
        out.write(f"\n--- {time.strftime('%Y/%m/%d %H:%M:%S')} start: {' '.join(self.argv[1:])}\n".encode())
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True  # a Ctrl-C in the launcher's terminal reaches the launcher only
        try:
            self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT,
                                         env=self.env, cwd=str(paths.TOOLS), **kwargs)
        finally:
            out.close()
        self.started_at = time.monotonic()
        log.info("%s started (pid %s)", self.name, self.proc.pid)
        return self

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def roll_log(self) -> bool:
        """A log past LOG_ROLL_BYTES in a long session: copied to .1.log, then emptied in place. The child keeps
        writing, at the new end, since its file was opened for appending (POSIX). Windows rolls at the next start only:
        a handle there keeps its own offset, so emptying the file under it would leave a hole."""
        if os.name == "nt":
            return False
        try:
            if self.log_path.stat().st_size <= LOG_ROLL_BYTES:
                return False
            shutil.copyfile(self.log_path, self.log_path.with_suffix(".1.log"))
            with open(self.log_path, "r+b") as f:
                f.truncate(0)
            return True
        except OSError as error:
            log.warning("could not roll %s: %s", self.log_path, error)
            return False

    def stop(self, timeout: float) -> bool:
        """Ask the child to stop (the stop pipe), wait, then kill. True when it ended by itself."""
        if self.proc is None:
            return True
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"stop\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout)
            log.info("%s stopped (exit %s)", self.name, self.proc.returncode)
            return True
        except subprocess.TimeoutExpired:
            log.warning("%s did not stop within %.0f s; killed", self.name, timeout)
            self.proc.kill()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
            return False


class Supervisor:
    def __init__(self, *, port: int = BRIDGE_PORT, webcam_port: int = WEBCAM_PORT, mock: bool = False,
                 bridge_args: list[str] | tuple | None = None, data: Path | None = None, logs: Path | None = None,
                 platform: str | None = None, poll_s: float = POLL_S, ready_s: float = READY_S,
                 restart_delays: tuple = RESTART_DELAYS, stable_s: float = STABLE_S,
                 restart_budget: tuple = model.RESTART_BUDGET, usb_check=usb_check, udev_rule: Path = UDEV_RULE,
                 install_udev=None):
        self.port, self.webcam_port, self.mock = port, webcam_port, mock
        self.bridge_args = list(bridge_args if bridge_args is not None else (MOCK_BRIDGE_ARGS if mock else REAL_BRIDGE_ARGS))
        self.data = data or paths.data_dir(mock)
        self.logs = logs or paths.log_dir(self.data)
        self.poll_s, self.ready_s, self.restart_delays, self.stable_s = poll_s, ready_s, tuple(restart_delays), stable_s
        self.restart_budget = tuple(restart_budget)
        self.changes = model.Changes()
        self.snap = model.Snapshot(phase="starting", port=port, since=time.monotonic(),
                                   platform=platform or paths.platform_key(),
                                   bundled=bool(os.environ.get("KOMA_LAUNCHER_STUB")))
        self.usb_check, self.udev_rule, self.install_udev = usb_check, Path(udev_rule), install_udev
        self._usb_checked = False
        self.bridge: Child | None = None
        self.on_ready: list = []       # fn(url) after a start the user asked for succeeded (launch, Try again)
        self.on_exit: list = []        # fn() after Quit finished
        self.bridge_starts = 0         # every spawn of the bridge, for the tests and the log
        self.reads: collections.deque = collections.deque(maxlen=200)  # the bridge paths read, newest last (tests)
        self._commands: list[str] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._looking = False
        self._look_waiters: list[threading.Event] = []
        self._status_misses = 0
        self._exits: list[float] = []  # monotonic times a running bridge exited, within the restart budget's window
        self._log_checked = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True, name="supervisor")

    # ------------------------------------------------------------------ any thread

    def start(self) -> "Supervisor":
        self._thread.start()
        return self

    def command(self, name: str):
        """'retry', 'quit', 'look', 'unlook'."""
        if name == "quit":
            self._quit.set()
        with self._lock:
            self._commands.append(name)
        self._wake.set()

    def look(self, on: bool = True, wait: float = 0.0) -> model.Snapshot:
        """Someone looks at the camera line (the menu opens: on) or stops looking (it closes: off). While someone
        looks, GET /status is read now and every poll; when they stop, the camera line goes. With `wait`, returns
        after the first read (or after `wait` seconds), so a menu can open with the line already in it."""
        done = threading.Event()
        with self._lock:
            self._looking = on
            if on:
                self._look_waiters.append(done)
        self.command("look" if on else "unlook")
        if on and wait and self.snap.phase == "running":
            done.wait(wait)
        return self.snap

    def snapshot(self) -> model.Snapshot:
        return self.snap

    @property
    def url(self) -> str:
        return self.snap.address

    def join(self, timeout: float | None = None):
        self._thread.join(timeout)

    # ------------------------------------------------------------------ supervisor thread

    def _set(self, **changes):
        if "phase" in changes and changes["phase"] != self.snap.phase:
            changes["since"] = time.monotonic()
        new = replace(self.snap, **changes)
        if new == self.snap:
            return
        old, self.snap = self.snap, new
        if new.phase != old.phase:
            log.info("phase %s -> %s%s", old.phase, new.phase, f" ({new.reason})" if new.reason else "")
            self._write_state()
        self.changes.fire(new)

    def _write_state(self):
        state = {"pid": os.getpid(), "port": self.port, "url": self.url, "phase": self.snap.phase,
                 "version": VERSION, "mock": self.mock}
        path = self.data / "launcher.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as error:
            log.warning("could not write %s: %s", path, error)

    def _get(self, port: int, path: str, timeout: float) -> dict | None:
        if port == self.port:
            self.reads.append(path)
        return get_json(port, path, timeout)

    def _sleep(self, seconds: float) -> bool:
        """Wait, unless Quit comes; True when Quit came."""
        return self._quit.wait(seconds)

    def _run(self):
        try:
            self._start_bridge(user=True)
            while True:
                self._wake.wait(self.poll_s)
                self._wake.clear()
                with self._lock:
                    commands, self._commands = self._commands, []
                if self._quit.is_set() or "quit" in commands:
                    break
                for name in commands:
                    self._handle(name)
                    if self._quit.is_set():
                        break
                if self._quit.is_set():
                    break
                self._tick()
        except Exception:  # noqa: BLE001
            log.exception("supervisor failed")
        finally:
            self._release_lookers()
            self._shutdown()

    def _handle(self, name: str):
        if name == "retry" and self.snap.phase in ("port_in_use", "failed"):
            self._exits.clear()
            self._start_bridge(user=True)
        elif name == "allow" and self.snap.udev_missing:
            threading.Thread(target=self._allow, daemon=True, name="udev").start()
        elif name == "udev_check":
            self._set(udev_missing=not self.udev_rule.exists())
        elif name == "unlook" and not self._looking:
            self._status_misses = 0
            self._set(status=None)

    def _release_lookers(self):
        with self._lock:
            waiters, self._look_waiters = self._look_waiters, []
        for done in waiters:
            done.set()

    # USB readiness -----------------------------------------------------

    def _real(self) -> bool:
        return "--real" in self.bridge_args

    def _check_usb(self):
        """Once, before the first real-mode bridge: the usbcheck child, and on Linux the udev rule."""
        if self._usb_checked or not self._real():
            return
        self._usb_checked = True
        problem = self.usb_check() if self.usb_check else ""
        if problem:
            log.warning("USB is not usable: %s", problem)
        self._set(usb_problem=problem,
                  udev_missing=self.snap.platform == "linux" and not problem and not self.udev_rule.exists())

    def _allow(self):
        """Allow (Linux): the udev rule through pkexec (__main__.install_udev_rule), off the supervisor thread, since
        pkexec waits for the password; then the supervisor looks for the rule again."""
        try:
            install = self.install_udev
            if install is None:
                from .__main__ import install_udev_rule as install
            code = install()
            log.info("udev rule install exited %s", code)
        except Exception:  # noqa: BLE001
            log.exception("udev rule install failed")
        self.command("udev_check")

    # bridge ------------------------------------------------------------

    def _start_bridge(self, user: bool) -> bool:
        """user: a start someone asked for (the launch, Try again); else a restart after a running bridge exited,
        which the menu and the window call "Restarting"."""
        self._set(phase="starting", reason="", status=None, webcam=False, restart=not user)
        self._check_usb()
        failures = 0
        while not self._quit.is_set():
            if port_in_use(self.port):
                log.info("port %s is in use; nothing started", self.port)
                self._set(phase="port_in_use", restart=False)
                return False
            argv, env = child_command("bridge", [*self.bridge_args, "--port", str(self.port), "--quiet"])
            self.bridge = Child("bridge", argv, env, self.logs / "bridge.log").start()
            self.bridge_starts += 1
            if self._wait_ready():
                self._set(phase="running", restart=False)
                if user:
                    for fn in list(self.on_ready):
                        try:
                            fn(self.url)
                        except Exception:  # noqa: BLE001
                            log.exception("on_ready failed")
                return True
            if self._quit.is_set():
                return False
            code = self.bridge.proc.poll() if self.bridge.proc else None
            log.warning("bridge did not come up (exit %s); see %s", code, self.bridge.log_path)
            self.bridge.stop(2.0)
            if port_in_use(self.port):  # something else took the port meanwhile
                self._set(phase="port_in_use", restart=False)
                return False
            if failures >= len(self.restart_delays):
                self._set(phase="failed", reason="no_answer", status=None, webcam=False, restart=False)
                return False
            if self._sleep(self.restart_delays[failures]):
                return False
            failures += 1
        return False

    def _wait_ready(self) -> bool:
        """GET /api answers as komabridge and GET / serves the page: the page reaches the bridge. Neither touches the
        camera (GET /status would open one that is plugged in)."""
        deadline = time.monotonic() + self.ready_s
        while time.monotonic() < deadline and not self._quit.is_set():
            if not self.bridge.alive():
                return False
            api = self._get(self.port, "/api", 1.5)
            if api and api.get("bridge") == "komabridge":
                try:
                    code, _ = http("GET", self.port, "/", 3.0)
                except Down:
                    code = 0
                if code == 200:
                    return True
            self._sleep(0.2)
        return False

    def _tick(self):
        if self.snap.phase != "running":
            self._release_lookers()
            return
        if not self.bridge or not self.bridge.alive():
            self._bridge_exited()
            return
        if self._looking:
            status = self._get(self.port, "/status", 1.5)
            if status is None:
                self._status_misses += 1
                if self._status_misses >= STATUS_MISSES:
                    self._set(status=None)
            else:
                self._status_misses = 0
                self._set(status=status_facts(status))
        self._release_lookers()
        state = get_json(self.webcam_port, "/status.json", 1.0)
        self._set(webcam=bool(state) and state.get("state") == "streaming")
        if time.monotonic() - self._log_checked > LOG_CHECK_S:
            self._log_checked = time.monotonic()
            if self.bridge.roll_log():
                log.info("rolled %s", self.bridge.log_path)

    def _bridge_exited(self):
        code = self.bridge.proc.returncode if self.bridge and self.bridge.proc else None
        now = time.monotonic()
        ran = now - (self.bridge.started_at if self.bridge else 0)
        count, window = self.restart_budget
        self._exits = [t for t in self._exits if now - t < window] + [now]
        if len(self._exits) >= count:
            log.error("bridge exited (exit %s); %d exits in %.0f s, not starting it again", code, count, window)
            self._set(phase="failed", reason="exited", status=None, webcam=False, restart=False)
            return
        log.warning("bridge exited (exit %s) after %.0f s; starting it again", code, ran)
        self._set(phase="starting", reason="", status=None, webcam=False, restart=True)
        if ran < self.stable_s and self._sleep(self.restart_delays[0]):
            return
        self._start_bridge(user=False)

    def _leave_remote_control(self):
        """What the app's Shoot and komacam do when they stop, for when they could not: end live view, then remote
        control (keyset,0,2 + remtout). Only while the bridge runs and reports remote control on."""
        if not self.bridge or not self.bridge.alive():
            return
        status = self._get(self.port, "/status", 1.5) or {}
        if not status.get("tether"):
            return
        for path, timeout in (("/live/stop", 5.0), ("/tether/stop", TETHER_STOP_S)):
            try:
                code, body = http("POST", self.port, path, timeout, data=b"")
                log.info("POST %s -> %s %s", path, code, body[:200])
            except Down as error:
                log.warning("POST %s: %s", path, error)

    # quit --------------------------------------------------------------

    def _shutdown(self):
        self._set(phase="stopping")
        if self.bridge and self.bridge.alive():
            self._leave_remote_control()  # the app may have left remote control on (Shoot open at Quit)
            self.bridge.stop(BRIDGE_STOP_S)
        try:
            (self.data / "launcher.json").unlink()
        except OSError:
            pass
        log.info("stopped")
        for fn in list(self.on_exit):
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("on_exit failed")
