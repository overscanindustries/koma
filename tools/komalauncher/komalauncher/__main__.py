"""Koma, the launcher. Installed, this is the `koma` command (packaging/koma_build.py); from the repository it is
`python -m komalauncher` with tools/komalauncher on PYTHONPATH.

    koma                    a real camera: komabridge --real on localhost:8765, the app in the browser, lines in the
                            terminal; Ctrl-C quits
    koma --tray             the same in the menu bar (macOS) or the system tray (Windows, Linux)
    koma doctor             can Koma use USB here (pyusb, libusb, the udev rule, the Windows driver); opens no device
    koma --mock             the simulated camera (komabridge --mock --mock-tether --mock-hw --mock-koma); for testing
    koma --mock --headless --self-test --no-browser    run the launcher's jobs once, print a JSON report
    koma --render-screens DIR                          draw the startup window in every state, and the menus

`--child bridge|webcam|usbcheck ARGS` is how the launcher runs its children (child.py); it is not for people.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from . import VERSION, paths, version_text, words


def _logging(logs, verbose: bool):
    root = logging.getLogger("komalauncher")
    root.setLevel(logging.INFO)
    handler = RotatingFileHandler(logs / "launcher.log", maxBytes=1 << 20, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(handler)
    if verbose and sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("komalauncher: %(message)s"))
        root.addHandler(console)


def parser() -> argparse.ArgumentParser:
    hidden = argparse.SUPPRESS  # testing and development options: they work, `koma --help` does not list them
    ap = argparse.ArgumentParser(
        prog="koma", description="Koma: runs the bridge for a GR DIGITAL on USB and opens the Koma app in the browser "
        "at http://localhost:8765/. Ctrl-C quits.",
        epilog="koma doctor   checks that Koma can use USB on this computer (pyusb, libusb, the Linux udev rule, the "
               "Windows driver)")
    ap.add_argument("--tray", action="store_true",
                    help="run in the menu bar (macOS) or the system tray (Windows, Linux) instead of the terminal")
    ap.add_argument("--port", type=int, default=8765, help="the port the app is served on (default 8765)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the app in the browser")
    ap.add_argument("--install-udev-rule", action="store_true",
                    help="Linux: install the udev rule that lets this user open the GR DIGITAL (asks for an "
                         "administrator password)")
    ap.add_argument("--verbose", action="store_true", help="also write the log to the terminal")
    ap.add_argument("--version", action="version",
                    version=f"Koma {version_text()}" if VERSION else words.CLI_NO_VERSION)
    ap.add_argument("--mock", action="store_true", help=hidden)  # the simulated camera, with its own data folder
    ap.add_argument("--webcam-port", type=int, default=8770, help=hidden)  # komacam's port, read for Webcam running
    ap.add_argument("--headless", action="store_true", help=hidden)  # no tray: one JSON line per change on stdout
    ap.add_argument("--data-dir", help=hidden)  # the launcher's folder (lock, launcher.json, logs)
    ap.add_argument("--first-run", action="store_true", help=hidden)  # the startup window as on a first launch
    # with --mock: run the launcher's jobs once (page, a look at the camera, a webcam started outside, a bridge
    # restart, quit), print a JSON report; exit 0 when every check passed
    ap.add_argument("--self-test", action="store_true", help=hidden)
    # draw the startup window in every state, dark and light, as PNG files, and the menus as text, then exit
    ap.add_argument("--render-screens", metavar="DIR", help=hidden)
    return ap


def install_udev_rule() -> int:
    """Copies 70-koma-gr.rules to /etc/udev/rules.d through pkexec (sudo when there is no pkexec) and reloads udev.
    The rule is copied to a temporary file first, readable by root wherever Koma is installed."""
    import os
    import shlex
    import subprocess
    import tempfile
    if paths.platform_key() != "linux":
        print("--install-udev-rule is for Linux", file=sys.stderr)
        return 2
    candidates = (paths.ASSETS / "70-koma-gr.rules", paths.TOOLS.parent / "70-koma-gr.rules")
    rule = next((c for c in candidates if c.is_file()), None)
    if rule is None:
        print("70-koma-gr.rules is not in this build", file=sys.stderr)
        return 2
    with tempfile.NamedTemporaryFile("wb", suffix=".rules", delete=False) as tmp:
        tmp.write(rule.read_bytes())
    os.chmod(tmp.name, 0o644)
    script = (f"install -m 0644 {shlex.quote(tmp.name)} /etc/udev/rules.d/70-koma-gr.rules && "
              "udevadm control --reload-rules && udevadm trigger --subsystem-match=usb")
    import shutil
    elevate = "pkexec" if shutil.which("pkexec") else "sudo"
    try:
        return subprocess.call([elevate, "sh", "-c", script])
    finally:
        os.unlink(tmp.name)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--child"]:
        from . import child
        if len(argv) < 2:
            print("usage: --child bridge|webcam|usbcheck [ARGS]", file=sys.stderr)
            return 2
        return child.run(argv[1], argv[2:])
    if argv[:1] == ["doctor"]:
        from . import doctor
        if len(argv) > 1:
            print("usage: koma doctor", file=sys.stderr)
            return 2
        return doctor.run()
    argv = [a for a in argv if not a.startswith("-psn_")]  # old Finder launches pass a process serial number
    a = parser().parse_args(argv)

    if a.render_screens:
        if paths.platform_key() == "darwin":
            from . import ui_mac
            return ui_mac.render_screens(a.render_screens, port=a.port)
        from . import ui_tray
        return ui_tray.render_screens(a.render_screens, port=a.port)
    if a.install_udev_rule:
        return install_udev_rule()
    if a.self_test and not a.mock:
        print("--self-test runs against the simulated camera only: add --mock", file=sys.stderr)
        return 2

    data = paths.data_dir(a.mock, a.data_dir)
    logs = paths.log_dir(data, override=bool(a.data_dir))
    _logging(logs, a.verbose or a.headless)
    log = logging.getLogger("komalauncher")

    from . import instance, system
    lock = instance.InstanceLock(data)
    if not lock.acquire():
        state = instance.wait_running(data) or {}
        url = state.get("url") or words.ADDRESS.format(port=a.port)
        print(words.CLI_ALREADY_RUNNING.format(url=url), file=sys.stderr if sys.stderr else None)
        log.info("second launch: the running launcher is %s (%s)", state.get("pid"), state.get("phase"))
        if not a.no_browser and state.get("phase") == "running":
            system.open_url(url)
        return 0

    from .supervisor import Supervisor
    sup = Supervisor(port=a.port, webcam_port=a.webcam_port, mock=a.mock, data=data, logs=logs)
    marker = data / "first-run-done"
    first_run = a.first_run or not marker.exists()

    def remember_first_run(_url):
        try:
            marker.touch()
        except OSError:
            pass

    sup.on_ready.append(remember_first_run)
    log.info("Koma %s starting: port %s, %s, data %s", VERSION or "(no version set)", a.port,
             "mock" if a.mock else "real", data)
    try:
        from . import headless
        if a.headless or (a.self_test and not a.tray):
            return headless.run(sup, open_browser=not a.no_browser, self_test=a.self_test)
        if not a.tray:
            return headless.terminal(sup, open_browser=not a.no_browser, log_file=str(logs / "bridge.log"))
        if paths.platform_key() == "darwin":
            try:
                from . import ui_mac
            except ImportError as error:
                log.info("no menu bar UI: %s", error)
                print(tray_missing(), file=sys.stderr)
                return 2
            return ui_mac.run(sup, open_browser=not a.no_browser, first_run=first_run, self_test=a.self_test)
        from . import ui_tray  # without pystray, Pillow or Tk it runs as the terminal does (ui_tray.run)
        return ui_tray.run(sup, open_browser=not a.no_browser, first_run=first_run, self_test=a.self_test)
    finally:
        lock.release()


def tray_missing() -> str:
    from . import doctor
    key = "darwin" if paths.platform_key() == "darwin" else "other"
    if "Cellar" in Path(sys.prefix).parts:  # the Homebrew formula carries PyObjC; a copy without it is broken
        return words.CLI_TRAY_REINSTALL.format(reinstall=doctor.reinstall_command())
    return words.CLI_TRAY_MISSING[key].format(pip=doctor.pip_command())


if __name__ == "__main__":
    sys.exit(main())
