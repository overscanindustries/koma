"""The states the screens are drawn in (`--render-screens`): the startup window's, with the clock each is drawn at,
and the menu's, written out as text (a menu cannot be captured without screen-recording permission). Pure data."""
from __future__ import annotations

from . import model

SAMPLE_STATUS = {"connected": True, "simulated": False, "usb_mode": "ORIGINAL", "message": "Camera connected.",
                 "camera": {"model": "GR DIGITAL"}}
# While komalauncher.VERSION is unset, the version slot is drawn with this for review (startup-failed-version-sample-*),
# the first public release (Koma 1.0, package 1.0.0). Never shown by the launcher itself.
SAMPLE_VERSION = "1.0.0"


def window_states(port: int, platform: str) -> dict:
    """name -> (snapshot, now for the elapsed counter or None, Details open)."""
    def snap(**kw):
        return model.Snapshot(port=port, platform=platform, **kw)
    return {
        "starting": (snap(phase="starting", since=100.0), 101.0, False),
        "starting-long": (snap(phase="starting", since=100.0), 112.4, False),
        "restarting": (snap(phase="starting", restart=True, since=100.0), 101.0, False),
        "ready": (snap(phase="running"), None, False),
        "port-in-use": (snap(phase="port_in_use"), None, False),
        "failed": (snap(phase="failed", reason="no_answer"), None, False),
        "failed-details": (snap(phase="failed", reason="no_answer"), None, True),
        "stopped": (snap(phase="failed", reason="exited"), None, False),
        "stopping-long": (snap(phase="stopping", since=100.0), 114.0, False),
        "usb-unavailable-details": (snap(phase="running", usb_problem="OSError: libusb-1.0.dylib not found"), None,
                                    True),
        "udev-allow": (snap(phase="running", udev_missing=True), None, False),
    }


def menu_states(port: int, looks: bool) -> dict:
    """The menu in each state. `looks`: the platform tells the launcher when the menu opens (macOS), so a menu on
    screen carries the camera line a look reads; Windows and Linux (pystray) have no such event and no camera line."""
    s = SAMPLE_STATUS

    def snap(**kw):
        return model.Snapshot(port=port, **kw)
    states = {"starting": snap(phase="starting")}
    if looks:
        states.update({
            "running, camera": snap(phase="running", status=s),
            "running, no camera": snap(phase="running", status={"connected": False, "message": "No camera on USB."}),
            "running, camera in MASS STORAGE": snap(phase="running",
                                                    status={"connected": False, "usb_mode": "MASS STORAGE"}),
            "running, no libusb": snap(phase="running",
                                       status={"connected": False, "message": "USB is not available: NoBackend"}),
            "running, simulated camera (--mock)": snap(phase="running", status={**s, "simulated": True}),
            "running, webcam streaming": snap(phase="running", status=s, webcam=True),
        })
    else:
        states.update({
            "running": snap(phase="running"),
            "running, webcam streaming": snap(phase="running", webcam=True),
        })
    states.update({
        "restarting (it exited while running)": snap(phase="starting", restart=True),
        "port in use": snap(phase="port_in_use"),
        "could not start": snap(phase="failed", reason="no_answer"),
        "kept exiting": snap(phase="failed", reason="exited"),
        "stopping": snap(phase="stopping"),
    })
    return states


def menus_text(port: int, looks: bool, quit_key: str = "", subtitles: bool = True) -> str:
    """Each state's menu as lines: status lines plain, actions after '>', '(dimmed)' when disabled; the state's
    header says when the tray K itself is drawn dimmed (Koma not running)."""
    lines = []
    for name, snap in menu_states(port, looks).items():
        lines.append(f"[{name}]{'   K dimmed' if model.dimmed(snap) else ''}")
        for entry in model.menu(snap):
            if entry.kind == "separator":
                lines.append("  ----------------")
            elif entry.kind == "status":
                lines.append(f"  {entry.text}")
            else:
                mark = "" if entry.enabled else "   (dimmed)"
                key = f"   {quit_key}" if quit_key and entry.action == "quit" else ""
                sub = f"\n    {entry.help}" if entry.help and subtitles else ""
                lines.append(f"  > {entry.text}{key}{mark}{sub}")
        lines.append("")
    return "\n".join(lines)
