"""The launcher's state, and what the tray menu and the startup window show for it. Pure: no processes, no UI.

Phases of the bridge, as the supervisor drives them:
  starting      the port is checked, the bridge is spawned, the page is being reached (with `restart`: a running
                bridge exited and is being started again, shown as "Restarting")
  running       GET /api and GET / answer
  port_in_use   something else listens on the port; nothing was started (one action: Try again)
  failed        the bridge never answered (reason "no_answer"), or a running bridge kept exiting ("exited")
  stopping      Quit was chosen: remote control and the bridge are being stopped

The camera line exists only while someone looks: the supervisor reads GET /status while the tray menu is open
(macOS), because a /status read opens a newly plugged camera, and the bridge then sends it -VER (komabridge.py,
Link). Nothing else the launcher reads touches the camera.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import words

PHASES = ("starting", "running", "port_in_use", "failed", "stopping")
ELAPSED_AFTER_S = 10.0     # every wait past 10 s carries an elapsed counter
RESTART_BUDGET = (5, 300.0)  # more than 5 restarts of a running bridge within 300 s: 'failed', not a restart loop


@dataclass(frozen=True)
class Snapshot:
    phase: str = "starting"
    port: int = 8765
    since: float = 0.0                # time.monotonic() when this phase began: the window's elapsed counter
    reason: str = ""                  # phase "failed": "no_answer" or "exited"
    status: dict | None = None        # the facts of the last GET /status read while the menu was open, else None
    webcam: bool = False              # komacam on the webcam port reports "streaming" (read-only, no camera traffic)
    platform: str = "darwin"
    restart: bool = False             # phase "starting" after a running bridge exited: "Restarting", not the first start
    usb_problem: str = ""             # the usbcheck child's error (pyusb or libusb not loadable); "" when USB is ready
    bundled: bool = True              # a bundle (Install Koma again) or the repository (install pyusb and libusb)
    udev_missing: bool = False        # Linux: /etc/udev/rules.d/70-koma-gr.rules is not installed (one action: Allow)

    @property
    def address(self) -> str:
        return words.ADDRESS.format(port=self.port)


@dataclass(frozen=True)
class Item:
    kind: str                         # "status", "action" or "separator"
    text: str = ""
    action: str = ""                  # "open", "copy", "retry", "quit"
    enabled: bool = True
    help: str = ""                    # a tooltip or subtitle where the OS shows one
    default: bool = False             # the item a click on the tray icon runs (Windows)


SEPARATOR = Item("separator")


def elapsed(seconds: float) -> str:
    """m:ss, h:mm:ss past an hour, counted from the phase start."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def camera_line(status: dict | None) -> str:
    """One line for the camera from GET /status. The bridge's own fields pick the case; nothing is guessed, and no
    model name is written that the bridge did not report."""
    status = status or {}
    camera = status.get("camera") or {}
    if status.get("connected"):
        model = camera.get("model")
        if not model:
            return words.CAMERA_CONNECTED
        return (words.CAMERA_SIMULATED if status.get("simulated") else words.CAMERA).format(model=model)
    message = str(status.get("message") or "")
    if message.startswith("USB is not available"):  # libusb missing or not loadable (komabridge.py, Link._ensure)
        return words.CAMERA_NO_USB
    mode = status.get("usb_mode")
    if mode and mode != "ORIGINAL":
        return words.CAMERA_WRONG_MODE
    if message.startswith("Could not open the camera"):
        return words.CAMERA_NOT_OPENED
    if "connect one camera only" in message:
        return words.CAMERA_ONE_ONLY
    return words.CAMERA_NOT_FOUND


def failure_lines(snap: Snapshot) -> tuple[str, str]:
    """The two status lines of a launch that could not start or a bridge that kept exiting: what, then why."""
    if snap.reason == "exited":
        count, window = RESTART_BUDGET
        return words.STOPPED, words.STOPPED_WHY.format(count=count, minutes=round(window / 60))
    return words.FAILED, words.FAILED_WHY.format(port=snap.port)


def dimmed(snap: Snapshot) -> bool:
    """The tray K is drawn dimmed whenever Koma is not running (starting, restarting, stopping, could not start): the
    icon's one piece of state, the way the system's own menu bar items show a service that is not up."""
    return snap.phase != "running"


def menu(snap: Snapshot) -> list[Item]:
    """The tray menu, top to bottom: status lines, rule, actions, rule, Quit Koma."""
    if snap.phase == "stopping":
        return [Item("status", words.STOPPING)]
    if snap.phase in ("port_in_use", "failed"):  # nothing runs: Quit needs no explanation here
        lines = ((words.PORT_IN_USE.format(port=snap.port), words.PORT_IN_USE_FIX) if snap.phase == "port_in_use"
                 else failure_lines(snap))
        return [*(Item("status", line) for line in lines), SEPARATOR, Item("action", words.TRY_AGAIN, "retry"),
                SEPARATOR, Item("action", words.QUIT, "quit")]
    running = snap.phase == "running"
    items = [Item("status", words.RUNNING.format(port=snap.port) if running
                  else words.RESTARTING if snap.restart else words.STARTING)]
    if running and snap.usb_problem:  # the bridge cannot open any camera: this line replaces the camera's
        items.append(Item("status", words.CAMERA_NO_USB))
    elif running and snap.status is not None:
        items.append(Item("status", camera_line(snap.status)))
    if running and snap.webcam:
        items.append(Item("status", words.WEBCAM_RUNNING))
    return [*items, SEPARATOR,
            Item("action", words.OPEN, "open", enabled=running, default=True),
            Item("action", words.COPY, "copy", enabled=running),
            SEPARATOR,
            Item("action", words.QUIT, "quit", help=words.QUIT_HELP)]


@dataclass(frozen=True)
class WindowLine:
    dot: str                          # "faint" (working), "ok" (running), "bad" (could not start)
    text: str
    action: str = ""                  # "" or "retry"
    action_text: str = ""
    details: bool = False             # offer the Details disclosure (the bridge's log file)
    key: str = ""                     # the state without its counter: a screen reader hears a change of key only
    detail: str = ""                  # what Details holds instead of the log file's path (the USB error)


def window_line(snap: Snapshot, now: float | None = None) -> WindowLine:
    """The startup window's one status line, its one action when there is one, and past 10 s of a wait the elapsed
    counter in place of the first sentence."""
    if snap.phase == "running" and snap.usb_problem:
        return WindowLine("bad", words.USB_UNAVAILABLE if snap.bundled else words.USB_UNAVAILABLE_REPO,
                          details=True, detail=snap.usb_problem, key="usb_problem")
    if snap.phase == "running" and snap.udev_missing:
        return WindowLine("faint", words.UDEV_NEEDED, "allow", words.ALLOW, key="udev")
    if snap.phase == "running":
        return WindowLine("ok", words.WINDOW_READY.get(snap.platform, words.WINDOW_READY["linux"]), key="running")
    if snap.phase == "port_in_use":
        return WindowLine("bad", words.WINDOW_PORT_IN_USE.format(port=snap.port), "retry", words.TRY_AGAIN,
                          key="port_in_use")
    if snap.phase == "failed":
        if snap.reason == "exited":
            count, window = RESTART_BUDGET
            text = words.WINDOW_STOPPED.format(count=count, minutes=round(window / 60))
        else:
            text = words.WINDOW_FAILED.format(port=snap.port)
        return WindowLine("bad", text, "retry", words.TRY_AGAIN, details=True, key=f"failed-{snap.reason}")
    waited = (time.monotonic() if now is None else now) - snap.since
    if snap.phase == "stopping":
        phase, first, key = words.STOPPING, words.WINDOW_STOPPING, "stopping"
    elif snap.restart:
        phase, first, key = words.RESTARTING, words.WINDOW_RESTARTING, "restarting"
    else:
        phase, first, key = words.STARTING, words.WINDOW_STARTING, "starting"
    if waited >= ELAPSED_AFTER_S:
        return WindowLine("faint", words.WINDOW_ELAPSED.format(phase=phase, elapsed=elapsed(waited)), key=key)
    return WindowLine("faint", first, key=key)


def usb_line_key(snap: Snapshot) -> str:
    """'usb_problem' or 'udev' while the window's line is about USB readiness, else '': the UIs show the window once
    when one appears, since the bridge is running and nothing else would bring it up."""
    key = window_line(snap, snap.since).key if snap.phase == "running" else ""
    return key if key in ("usb_problem", "udev") else ""


@dataclass
class Changes:
    """Collects listeners; the supervisor calls fire() from its own thread after every change of the snapshot."""
    listeners: list = field(default_factory=list)

    def add(self, fn):
        self.listeners.append(fn)

    def fire(self, snap: Snapshot):
        for fn in list(self.listeners):
            try:
                fn(snap)
            except Exception:  # noqa: BLE001  (a UI listener never takes the supervisor down)
                import logging
                logging.getLogger("komalauncher").exception("listener failed")
