"""The launcher without a tray. run(): `--headless`, for tests, CI, and a Linux desktop that has no tray: one JSON
line per change on stdout. terminal(): `koma` itself, plain lines for people on stderr. Either way Ctrl-C or SIGTERM
quits the same way Quit Koma does. Nobody opens a menu here, so nothing reads the camera: no line carries a camera
field."""
from __future__ import annotations

import json
import signal
import sys
import threading

from . import selftest, system

# Ctrl-C, SIGTERM, and on Windows Ctrl-Break (SIGBREAK): each quits the way Quit Koma does. Without a SIGBREAK
# handler Windows ends the process on Ctrl-Break with no Quit, and the bridge only stops later, when its stdin closes.
QUIT_SIGNALS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGBREAK") if hasattr(signal, name))


def line(snap) -> str:
    return json.dumps({"phase": snap.phase, "reason": snap.reason or None, "webcam": snap.webcam,
                       "url": snap.address})


def run(sup, open_browser: bool = True, self_test: bool = False, announce: str = "") -> int:
    exited = threading.Event()
    sup.on_exit.append(exited.set)
    last = {"text": ""}

    def changed(snap):
        text = line(snap)
        if text != last["text"]:
            last["text"] = text
            print(text, flush=True)

    sup.changes.add(changed)
    if open_browser:
        sup.on_ready.append(system.open_url)

    def on_signal(_signum, _frame):
        sup.command("quit")

    for signum in QUIT_SIGNALS:
        signal.signal(signum, on_signal)
    if announce:
        print(announce, file=sys.stderr, flush=True)
    sup.start()
    if self_test:
        report = selftest.run(sup)
        print(selftest.dumps(report), flush=True)
        return 0 if report.get("ok") else 1
    while not exited.wait(0.5):  # short waits keep the main thread able to take signals
        pass
    return 0


def terminal_lines(snap, last, log_file: str = "") -> list[str]:
    """What `koma` prints for a change, in words (words.CLI_*): one line per new phase, and the USB next step once
    when the USB check finds a problem. `last` is the previous snapshot, or None at the start."""
    from . import doctor, model, version_text, words
    out = []
    if last is None or snap.phase != last.phase or snap.restart != last.restart:
        if snap.phase == "starting":
            out.append(words.CLI_RESTARTING if snap.restart else words.CLI_STARTING.format(version=version_text()))
        elif snap.phase == "running":
            out.append(words.CLI_RUNNING.format(url=snap.address))
        elif snap.phase == "port_in_use":
            out.append(words.CLI_PORT_IN_USE.format(port=snap.port))
        elif snap.phase == "failed":
            if snap.reason == "exited":
                count, window = model.RESTART_BUDGET
                out.append(words.CLI_STOPPED.format(count=count, minutes=round(window / 60), log=log_file))
            else:
                out.append(words.CLI_FAILED.format(port=snap.port, log=log_file))
        elif snap.phase == "stopping":
            out.append(words.CLI_STOPPING)
    if snap.usb_problem and (last is None or snap.usb_problem != last.usb_problem):
        out.append(doctor.usb_next_step(snap.usb_problem, snap.platform))
    if snap.udev_missing and (last is None or not last.udev_missing):
        out.append(words.USB_UDEV_MISSING)
    return out


def terminal(sup, open_browser: bool = True, log_file: str = "") -> int:
    """`koma` in a terminal: plain lines on stderr, the app in the browser once Koma is running, Ctrl-C (or SIGTERM)
    quits the way Quit Koma does: remote control and the webcam are released, then the bridge stops. Nothing here has
    a Try again, so a port in use or a bridge that never answered ends the run with exit status 1."""
    from . import doctor, words
    exited = threading.Event()
    sup.on_exit.append(exited.set)
    state = {"last": None, "code": 0}

    def say(text):
        if sys.stderr is not None:
            print(text, file=sys.stderr, flush=True)

    def changed(snap):
        if not (state["code"] and snap.phase == "stopping"):  # after a failure, "Stopping." says nothing new
            for text in terminal_lines(snap, state["last"], log_file):
                say(text)
        state["last"] = snap
        if snap.phase in ("port_in_use", "failed"):
            state["code"] = 1
            sup.command("quit")

    sup.changes.add(changed)
    if open_browser:
        sup.on_ready.append(system.open_url)

    def on_signal(_signum, _frame):
        sup.command("quit")

    for signum in QUIT_SIGNALS:
        signal.signal(signum, on_signal)
    if sup.snap.platform == "win32" and "--real" in sup.bridge_args:
        if doctor.windows_driver_problem(doctor.windows_driver()):  # the registry only; no device is touched
            say(words.USB_WINDOWS_DRIVER)
    for text in terminal_lines(sup.snap, None, log_file):  # "Starting Koma 1.0.": the first phase fires no change
        say(text)
    state["last"] = sup.snap
    sup.start()
    while not exited.wait(0.5):  # short waits keep the main thread able to take signals
        pass
    return state["code"]
