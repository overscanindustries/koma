#!/usr/bin/env python3
"""Tests for the Koma launcher. MOCK only: every bridge here is komabridge --mock (the simulated camera).

    python3 -m pytest tools/komalauncher/test_komalauncher.py -v

Unit tests for the menu and window model, the copy, the version, the lock, the port check, the window's measurements
(look.py), the Tk window at 100 % and 150 % (hidden), the AppKit window (offscreen), the fonts, the assets, the
terminal lines, `koma doctor` and the Python package's file list (packaging/koma_build.py); then the real supervisor
with real child processes on ports 9700-9720: start, a look at the camera,
a killed bridge and its restart, a bridge that keeps exiting, a port in use, a bridge that never comes up, an outside
webcam, Quit with remote control left on, a second launch, a launcher killed outright, and a child that inherited
SIGINT as ignored.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGING = HERE.parent.parent / "packaging"
sys.path.insert(0, str(HERE))

import komalauncher  # noqa: E402
from komalauncher import model, paths, screens, supervisor, words  # noqa: E402
from komalauncher.instance import InstanceLock, read_state  # noqa: E402

CAMERA = {"connected": True, "simulated": False, "usb_mode": "ORIGINAL", "message": "Camera connected.",
          "camera": {"model": "GR DIGITAL"}}


def texts(snap) -> list[str]:
    return ["---" if i.kind == "separator" else i.text for i in model.menu(snap)]


def actions(snap) -> dict:
    return {i.action: i.enabled for i in model.menu(snap) if i.kind == "action"}


class MenuModel(unittest.TestCase):
    def test_running_menu_open(self):
        snap = model.Snapshot(phase="running", port=8765, status=CAMERA)
        self.assertEqual(texts(snap), ["Koma running · localhost:8765", "Camera: GR DIGITAL", "---", "Open Koma",
                                       "Copy address", "---", "Quit Koma"])
        self.assertTrue(all(actions(snap).values()))
        self.assertEqual(model.menu(snap)[-1].help, "Disconnects the camera and stops the webcam.")

    def test_no_camera_line_without_a_look(self):
        """The camera line comes only from a /status read made while the menu is open (a read opens a plugged-in
        camera); with no read there is no line, not a guess."""
        snap = model.Snapshot(phase="running", port=8765)
        self.assertEqual(texts(snap), ["Koma running · localhost:8765", "---", "Open Koma", "Copy address", "---",
                                       "Quit Koma"])

    def test_the_menu_runs_no_webcam(self):
        for phase in model.PHASES:
            for webcam in (False, True):
                snap = model.Snapshot(phase=phase, status=CAMERA, webcam=webcam)
                self.assertFalse({"webcam", "webcam-on", "webcam-off"} & set(actions(snap)), phase)
                self.assertFalse([t for t in texts(snap) if "webcam" in t.lower() and t != "Webcam running"])

    def test_webcam_line_only_while_it_streams(self):
        self.assertNotIn("Webcam running", texts(model.Snapshot(phase="running", status=CAMERA)))
        self.assertEqual(texts(model.Snapshot(phase="running", status=CAMERA, webcam=True))[:3],
                         ["Koma running · localhost:8765", "Camera: GR DIGITAL", "Webcam running"])
        self.assertNotIn("Webcam running", texts(model.Snapshot(phase="starting", webcam=True)))

    def test_status_lines_are_not_actions(self):
        for phase in model.PHASES:
            for item in model.menu(model.Snapshot(phase=phase, status=CAMERA)):
                if item.kind == "status":
                    self.assertEqual(item.action, "")

    def test_starting_keeps_the_layout_with_actions_dimmed(self):
        snap = model.Snapshot(phase="starting", status=CAMERA)
        self.assertEqual(texts(snap)[0], "Starting")
        self.assertFalse([t for t in texts(snap) if t.startswith("Camera:")], "unknown is not 'not found'")
        self.assertEqual(actions(snap), {"open": False, "copy": False, "quit": True})

    def test_port_in_use_has_one_action(self):
        snap = model.Snapshot(phase="port_in_use", port=8765)
        self.assertEqual(texts(snap), ["Port 8765 is in use", "Quit the program using it, then try again", "---",
                                       "Try again", "---", "Quit Koma"])
        line = model.window_line(snap)
        self.assertEqual((line.text, line.action, line.details),
                         ("Port 8765 is in use. Quit the program using it, then try again.", "retry", False))

    def test_failures_name_their_cause(self):
        never = model.Snapshot(phase="failed", port=8765, reason="no_answer")
        self.assertEqual(texts(never), ["Koma could not start", "Nothing answered on port 8765", "---", "Try again",
                                        "---", "Quit Koma"])
        line = model.window_line(never)
        self.assertEqual((line.dot, line.text, line.action, line.details),
                         ("bad", "Koma could not start. Nothing answered on port 8765.", "retry", True))
        exited = model.Snapshot(phase="failed", port=8765, reason="exited")
        self.assertEqual(texts(exited)[:2], ["Koma stopped", "It failed 5 times in 5 minutes"])
        self.assertEqual(model.window_line(exited).text, "Koma stopped. It failed 5 times in 5 minutes.")

    def test_quit_explains_itself_only_while_something_runs(self):
        for phase, help_ in (("running", "Disconnects the camera and stops the webcam."),
                             ("starting", "Disconnects the camera and stops the webcam."), ("port_in_use", ""),
                             ("failed", "")):
            quit_item = [i for i in model.menu(model.Snapshot(phase=phase)) if i.action == "quit"][0]
            self.assertEqual(quit_item.help, help_, phase)
        self.assertEqual(texts(model.Snapshot(phase="stopping")), ["Stopping"])

    def test_camera_lines_follow_the_bridge(self):
        line = model.camera_line
        self.assertEqual(line(CAMERA), "Camera: GR DIGITAL")
        self.assertEqual(line({**CAMERA, "simulated": True}), "Camera: GR DIGITAL · SIM")
        self.assertEqual(line({"connected": True, "camera": None}), "Camera: connected", "no invented model")
        self.assertEqual(line({"connected": True, "camera": {"model": None}}), "Camera: connected")
        self.assertEqual(line({"connected": False, "message": "No camera on USB."}), "Camera: not found")
        self.assertEqual(line({"connected": False, "usb_mode": "MASS STORAGE"}),
                         "Camera: set USB CONNECTION to ORIGINAL")
        self.assertEqual(line({"connected": False, "message": "Could not open the camera: X"}),
                         "Camera: could not be opened")
        self.assertEqual(line({"connected": False, "message": "2 Ricoh devices on USB; connect one camera only."}),
                         "Camera: connect one camera only")
        self.assertEqual(line({"connected": False, "usb_mode": None,
                               "message": "USB is not available: NoBackendError: No backend available"}),
                         "Camera: USB not available")

    def test_window_lines(self):
        start = model.Snapshot(phase="starting", since=100.0)
        self.assertEqual(model.window_line(start, now=109.9).text, "Starting.")
        self.assertEqual(model.window_line(start, now=112.4).text, "Starting · 0:12")
        self.assertEqual(model.window_line(start, now=112.4).key, model.window_line(start, now=101).key,
                         "a counter tick is not a new state for a screen reader")
        stop = model.Snapshot(phase="stopping", since=0.0)
        self.assertEqual(model.window_line(stop, now=3).text, "Stopping.")
        self.assertEqual(model.window_line(stop, now=75).text, "Stopping · 1:15")
        for platform, text in (("darwin", "Koma is the K in the menu bar."),
                               ("win32", "Koma is the K in the system tray, or behind the tray's arrow."),
                               ("linux", "Koma is the K in the system tray.")):
            line = model.window_line(model.Snapshot(phase="running", platform=platform))
            self.assertEqual((line.dot, line.text, line.action), ("ok", text, ""))

    def test_a_restart_is_not_called_the_first_start(self):
        """A running bridge that exited and is being started again reads "Restarting", in the menu and the window,
        with its own counter; the plain "Starting." line belongs to the first start only."""
        again = model.Snapshot(phase="starting", restart=True, since=100.0)
        self.assertEqual(texts(again)[0], "Restarting")
        self.assertEqual(actions(again), {"open": False, "copy": False, "quit": True})
        self.assertEqual(model.window_line(again, now=101).text, "Restarting.")
        self.assertEqual(model.window_line(again, now=112.4).text, "Restarting · 0:12")
        self.assertNotEqual(model.window_line(again, now=101).key,
                            model.window_line(model.Snapshot(phase="starting", since=100.0), now=101).key)

    def test_the_tray_k_dims_while_koma_is_not_running(self):
        self.assertEqual({phase: model.dimmed(model.Snapshot(phase=phase)) for phase in model.PHASES},
                         {"starting": True, "running": False, "port_in_use": True, "failed": True, "stopping": True})

    def test_elapsed(self):
        self.assertEqual([model.elapsed(s) for s in (0, 12.9, 75, 3730)], ["0:00", "0:12", "1:15", "1:02:10"])


class TrayMapping(unittest.TestCase):
    """Windows and Linux build their pystray menu from the same model (the tray itself is not run here)."""

    def test_pystray_items_follow_the_model(self):
        from komalauncher import ui_tray

        class Item:
            def __init__(self, text, action, enabled=True, default=False):
                self.text, self.action, self.enabled, self.default = text, action, enabled, default

        class Menu:
            SEPARATOR = object()

        fake = type("pystray", (), {"MenuItem": Item, "Menu": Menu})
        calls = []
        snap = model.Snapshot(phase="running", webcam=True)
        items = ui_tray.tray_items(fake, snap, lambda name: (lambda _icon=None, _item=None: calls.append(name)))
        shown = ["---" if i is Menu.SEPARATOR else i.text for i in items]
        self.assertEqual(shown, texts(snap))
        statuses = [i for i in items if i is not Menu.SEPARATOR and i.action is None]
        self.assertTrue(statuses and not any(i.enabled for i in statuses))
        for item in items:
            if item is not Menu.SEPARATOR and item.action:
                item.action(None, item)
        self.assertEqual(calls, ["open", "copy", "quit"])
        self.assertEqual([i.text for i in items if i is not Menu.SEPARATOR and i.default], ["Open Koma"])

    def test_the_dimmed_k_is_the_same_k_at_half_alpha(self):
        from PIL import Image

        from komalauncher import ui_tray
        k = Image.open(paths.ASSETS / "tray-light-64.png").convert("RGBA")
        dimmed = ui_tray.dim(k)
        self.assertEqual(dimmed.size, k.size)
        self.assertEqual([a // 2 for a in k.getchannel("A").getdata()], list(dimmed.getchannel("A").getdata()))
        self.assertEqual(k.convert("RGB").tobytes(), dimmed.convert("RGB").tobytes())

    def test_tray_menus_text_has_no_camera_line(self):
        from komalauncher import ui_tray
        text = ui_tray.menus_text(8765)
        self.assertNotIn("Camera:", text)
        self.assertNotIn("⌘", text)
        self.assertIn("Webcam running", text)


class StartupWindowTk(unittest.TestCase):
    """The Tk window of Windows and Linux, built hidden on this machine: every state draws, the Details link and the
    button take the keyboard, and the elapsed counter moves. Its look is checked in CI (--render-screens)."""

    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
        except Exception as error:  # noqa: BLE001
            self.skipTest(f"no Tk here: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_states_details_and_keyboard(self):
        from komalauncher import ui_tray
        retries, reveals = [], []
        window = ui_tray.StartupWindow(self.root, lambda: retries.append(1), dark=True, scale=1.0,
                                       log_file=Path.home() / ".local" / "state" / "koma" / "logs" / "bridge.log",
                                       on_reveal=lambda: reveals.append(1))
        canvas = window.canvas

        def shown():
            return [canvas.itemcget(i, "text") for i in canvas.find_withtag("state")
                    if canvas.type(i) == "text" and "button" not in canvas.gettags(i)]

        for name, (snap, now, details) in screens.window_states(8765, "linux").items():
            window.update(snap, now=now)
            if details:
                window.toggle_details()
            line = model.window_line(snap, now)
            detail = line.detail or window.log_text   # the USB error replaces the log path under Details
            self.assertEqual(" ".join(t for t in shown() if t not in (words.DETAILS, detail)), line.text, name)
            self.assertEqual(words.DETAILS in shown(), line.details, name)
            self.assertEqual(detail in shown(), details, name)
        window.update(model.Snapshot(phase="failed", reason="no_answer"))
        window._cycle(1)
        self.assertEqual(window.focus, "details")
        self.assertTrue(canvas.find_withtag("focus"), "the focus ring is drawn")
        window._activate()
        self.assertTrue(window.details_open)
        self.assertTrue(canvas.find_withtag("log"), "the log path is a link")
        window._cycle(1)
        self.assertEqual(window.focus, "log")
        window._activate()
        self.assertEqual(reveals, [1], "the link shows the log file")
        window._cycle(1)
        self.assertEqual(window.focus, "button")
        window._activate()
        self.assertEqual(retries, [1])
        window.update(model.Snapshot(phase="running"))
        self.assertIsNone(window.focus)
        self.assertFalse(canvas.find_withtag("button"))

    def test_scaled_display(self):
        """At 150 % a DPI-aware Tk draws in device pixels: the window, its type, the button and its margins grow by
        1.5, so the window is the macOS window's size on the screen, not two thirds of it."""
        from komalauncher import look, ui_tray
        sizes = {}
        for scale in (1.0, 1.5):
            window = ui_tray.StartupWindow(self.root, lambda: None, dark=False, scale=scale,
                                           log_file=Path.home() / "bridge.log")
            window.update(model.Snapshot(phase="failed", reason="no_answer"))
            x1, y1, x2, y2 = window.button_box
            width, height = int(window.canvas.cget("width")), int(window.canvas.cget("height"))
            self.assertEqual((width, height), (round(look.W * scale), round(look.H * scale)))
            self.assertAlmostEqual(width - x2, look.BUTTON_MARGIN * scale)
            self.assertAlmostEqual(height - y2, look.BUTTON_MARGIN * scale, msg="bottom margin = right margin")
            self.assertAlmostEqual(y2 - y1, look.BUTTON_H * scale)
            sizes[scale] = -window.f_text.cget("size")
            window.destroy()
        self.assertEqual(sizes, {1.0: 14, 1.5: 21})

    def test_ui_scale_follows_the_display(self):
        from komalauncher import ui_tray

        class Root:
            def __init__(self, dpi):
                self.dpi = dpi

            def winfo_fpixels(self, _what):
                if self.dpi is None:
                    raise RuntimeError("no display")
                return self.dpi
        self.assertEqual([ui_tray.ui_scale(Root(d)) for d in (96, 120, 144, 192, 72, 99.5, None)],
                         [1.0, 1.25, 1.5, 2.0, 1.0, 1.0, 1.0])


class Copy(unittest.TestCase):
    """The voice rules and the banned list: checked on every string the launcher can show."""

    def strings(self):
        out = []
        for name in dir(words):
            value = getattr(words, name)
            if name.startswith("CLI_") or name.startswith("_"):
                continue
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, dict):
                out += [v for v in value.values() if isinstance(v, str)]
        return out

    def test_no_banned_words_or_marks(self):
        banned = re.compile(r"\b(seamless|effortless|simply|just|unlock|elevate|let's|please|oops|sorry|don't worry|"
                            r"your|successfully)\b|!|—|\.\.\.|…", re.I)
        for s in self.strings():
            self.assertIsNone(banned.search(s), s)

    def test_menu_lines_take_no_period_sentences_do(self):
        for name in ("STARTING", "RESTARTING", "RUNNING", "STOPPING", "PORT_IN_USE", "PORT_IN_USE_FIX", "FAILED",
                     "FAILED_WHY",
                     "STOPPED", "STOPPED_WHY", "CAMERA", "CAMERA_SIMULATED", "CAMERA_CONNECTED", "CAMERA_NOT_FOUND",
                     "CAMERA_WRONG_MODE", "CAMERA_NOT_OPENED", "CAMERA_ONE_ONLY", "CAMERA_NO_USB", "WEBCAM_RUNNING",
                     "OPEN", "COPY", "TRY_AGAIN", "QUIT", "DETAILS", "WINDOW_ELAPSED"):
            self.assertFalse(getattr(words, name).endswith("."), name)
        for name in ("WINDOW_STARTING", "WINDOW_RESTARTING", "WINDOW_STOPPING", "WINDOW_PORT_IN_USE", "WINDOW_FAILED",
                     "WINDOW_STOPPED", "QUIT_HELP"):
            self.assertTrue(getattr(words, name).endswith("."), name)
        for text in words.WINDOW_READY.values():
            self.assertTrue(text.endswith("."))

    def test_window_strings(self):
        """The startup window's strings: the port line does not guess that the holder is another Koma,
        and Quit's sentence names no part."""
        self.assertEqual(words.WINDOW_STARTING, "Starting.")
        self.assertEqual(words.WINDOW_PORT_IN_USE.format(port=8765),
                         "Port 8765 is in use. Quit the program using it, then try again.")
        self.assertEqual(words.RUNNING.format(port=8765), "Koma running · localhost:8765")
        self.assertEqual((words.OPEN, words.COPY, words.QUIT, words.QUIT_HELP, words.WEBCAM_RUNNING),
                         ("Open Koma", "Copy address", "Quit Koma", "Disconnects the camera and stops the webcam.",
                          "Webcam running"))

    def test_no_surface_names_a_part(self):
        """People see Koma, the camera and the webcam; "the bridge" and komacam stay in the logs."""
        for s in self.strings():
            self.assertIsNone(re.search(r"bridge|komacam|komabridge", s, re.I), s)


class UsbReadiness(unittest.TestCase):
    """The usbcheck child and what the launcher shows for it. The child runs here in-process against a stubbed `usb`
    package only: the real pyusb is never imported and nothing is enumerated."""

    def stub_usb(self, backend):
        import types
        from unittest import mock
        usb = types.ModuleType("usb")
        core = types.ModuleType("usb.core")
        back = types.ModuleType("usb.backend")
        libusb1 = types.ModuleType("usb.backend.libusb1")
        libusb1.get_backend = lambda find_library=None: backend
        usb.core, usb.backend, back.libusb1 = core, back, libusb1
        mods = {"usb": usb, "usb.core": core, "usb.backend": back, "usb.backend.libusb1": libusb1}
        return mock.patch.dict(sys.modules, mods)

    def run_check(self):
        import contextlib
        import io
        from unittest import mock
        from komalauncher import child
        err = io.StringIO()
        with mock.patch.object(paths, "bundled_libusb", return_value=None), contextlib.redirect_stderr(err):
            code = child.usbcheck()
        return code, err.getvalue()

    def test_usbcheck_passes_with_a_backend(self):
        with self.stub_usb(object()):
            self.assertEqual(self.run_check(), (0, ""))

    def test_usbcheck_fails_without_libusb(self):
        with self.stub_usb(None):
            code, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("libusb not found", err)

    def test_usbcheck_fails_without_pyusb(self):
        from unittest import mock
        with mock.patch.dict(sys.modules, {"usb": None, "usb.core": None}):
            code, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("usb.core", err)

    def test_window_and_menu_lines(self):
        snap = model.Snapshot(phase="running", usb_problem="ImportError: No module named 'usb'", status=CAMERA)
        line = model.window_line(snap)
        self.assertEqual((line.dot, line.text, line.details, line.detail),
                         ("bad", words.USB_UNAVAILABLE, True, "ImportError: No module named 'usb'"))
        self.assertEqual(model.window_line(model.Snapshot(phase="running", usb_problem="x", bundled=False)).text,
                         "Install pyusb and libusb for this Python.")
        self.assertIn("Camera: USB not available", texts(snap))       # in place of the camera line
        self.assertNotIn("Camera: GR DIGITAL", texts(snap))
        udev = model.window_line(model.Snapshot(phase="running", udev_missing=True, platform="linux"))
        self.assertEqual((udev.text, udev.action, udev.action_text),
                         ("Koma needs permission to open the camera over USB.", "allow", "Allow"))
        self.assertEqual(model.usb_line_key(model.Snapshot(phase="running", udev_missing=True)), "udev")
        self.assertEqual(model.usb_line_key(model.Snapshot(phase="running")), "")

    def sup(self, tmp, **kw):
        return supervisor.Supervisor(data=tmp, logs=tmp, **kw)

    def test_supervisor_checks_once_in_real_mode_only(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            mock_sup = self.sup(tmp, mock=True, usb_check=lambda: calls.append(1) or "")
            mock_sup._check_usb()
            self.assertEqual(calls, [])
            real = self.sup(tmp, bridge_args=["--real"], platform="darwin",
                            usb_check=lambda: calls.append(1) or "OSError: libusb-1.0.dylib not found")
            real._check_usb()
            real._check_usb()
            self.assertEqual(calls, [1])
            self.assertEqual(real.snap.usb_problem, "OSError: libusb-1.0.dylib not found")
            self.assertFalse(real.snap.udev_missing)

    def test_linux_udev_rule_and_allow(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            rule = tmp / "70-koma-gr.rules"
            sup = self.sup(tmp, bridge_args=["--real"], platform="linux", usb_check=lambda: "", udev_rule=rule,
                           install_udev=lambda: rule.write_text("rule") and 0)
            sup._check_usb()
            self.assertTrue(sup.snap.udev_missing)
            sup._allow()                                   # what the "allow" command runs, off the supervisor thread
            self.assertIn("udev_check", sup._commands)
            sup._handle("udev_check")
            self.assertFalse(sup.snap.udev_missing)


class Version(unittest.TestCase):
    """One version line (docs/versioning.md): set in komalauncher.VERSION, shown as "Koma 1.0"."""

    def test_the_one_version(self):
        self.assertEqual(komalauncher.VERSION, "1.0.0")
        self.assertEqual(komalauncher.version_text(), "1.0")
        self.assertEqual(komalauncher.version_text("1.0.1"), "1.0.1")
        self.assertEqual(komalauncher.version_text(None), "")

    def test_the_package_reads_it(self):
        sys.path.insert(0, str(PACKAGING))
        import koma_build
        self.assertEqual(koma_build.version(), komalauncher.VERSION)

    def test_the_window_shows_no_full_version_line(self):
        for snap in [s for s, _, _ in screens.window_states(8765, "darwin").values()]:
            self.assertIsNone(re.search(r"\b\d+\.\d+\.\d+\b", model.window_line(snap, 101).text))


class LockAndPort(unittest.TestCase):
    def test_one_lock_per_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = InstanceLock(Path(tmp)), InstanceLock(Path(tmp))
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_port_in_use(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            self.assertTrue(supervisor.port_in_use(s.getsockname()[1]))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.assertFalse(supervisor.port_in_use(port))


class Logs(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Windows rolls at the next start only")
    def test_a_long_session_log_rolls_and_the_child_keeps_appending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge.log"
            script = "import sys, time\nfor i in range(40):\n    print('line', i, flush=True)\n    time.sleep(0.05)\n"
            child = supervisor.Child("test", [sys.executable, "-c", script], dict(os.environ), path)
            saved = supervisor.LOG_ROLL_BYTES
            supervisor.LOG_ROLL_BYTES = 64
            try:
                child.start()
                time.sleep(0.6)
                self.assertTrue(child.roll_log())
                child.proc.wait(10)
            finally:
                supervisor.LOG_ROLL_BYTES = saved
            rolled = path.with_suffix(".1.log").read_text()
            current = path.read_bytes()
            self.assertIn("line 0", rolled)
            self.assertNotIn(b"\0", current, "no hole where the old lines were")
            self.assertIn(b"line 39", current)


class Assets(unittest.TestCase):
    def test_tray_template_sizes_and_alpha_only(self):
        from PIL import Image
        for name, size in (("tray-template.png", 20), ("tray-template@2x.png", 40)):
            im = Image.open(paths.ASSETS / name).convert("RGBA")
            self.assertEqual(im.size, (size, size))
            self.assertEqual({px[:3] for px in im.getdata() if px[3]}, {(0, 0, 0)}, "template images are black")

    def test_app_icon_carries_the_koma_ma(self):
        import komamark
        import make_assets
        ink, tick = komamark.paths("ma")
        icon = make_assets.app_icon_svg(1024)
        self.assertIn(ink, icon)
        self.assertIn(tick, icon)

    def test_fonts_and_icons_present(self):
        for name in ("KomaWordmark.ttf", "KomaText.ttf", "KomaTextMedium.ttf", "OFL.txt"):
            self.assertTrue((paths.FONTS / name).is_file(), name)
        self.assertTrue((paths.ASSETS / "70-koma-gr.rules").is_file())

    def test_udev_rule_matches_the_gr_original_identity(self):
        sys.path.insert(0, str(paths.TOOLS))
        from grdusb import descriptors
        rule = (paths.ASSETS / "70-koma-gr.rules").read_text()
        self.assertIn(f'ATTR{{idVendor}}=="{descriptors.VENDOR:04x}"', rule)
        self.assertIn(f'ATTR{{idProduct}}=="{descriptors.PRODUCT_ORIGINAL:04x}"', rule)
        from komalauncher import doctor
        self.assertEqual(doctor.VID_PID, f"VID_{descriptors.VENDOR:04X}&PID_{descriptors.PRODUCT_ORIGINAL:04X}")


class Package(unittest.TestCase):
    """The koma-gr wheel's file list (packaging/koma_build.py). Pure: nothing is built."""

    @unittest.skipUnless((paths.APP_DIST / "index.html").is_file(), "the app is not built")
    def test_wheel_carries_bridge_webcam_app_launcher_and_the_card_package(self):
        sys.path.insert(0, str(PACKAGING))
        import koma_build
        root = paths.TOOLS.parent
        names = {p.relative_to(root).as_posix() for p in koma_build.files(root)}
        for needed in ("tools/komaset/bridge/komabridge.py", "tools/komaset/bridge/komainstall.py",
                       "tools/komaset/bridge/mockcard.py", "tools/komaset/settings-map.json",
                       "tools/grdusb/transport.py", "tools/komacam/komacam.py", "tools/koma/loader.py",
                       "tools/komaapp/dist/index.html", "tools/komalauncher/komalauncher/__main__.py",
                       "tools/komalauncher/komalauncher/doctor.py", "tools/komalauncher/komalauncher/ui_mac.py",
                       "tools/komalauncher/komalauncher/assets/70-koma-gr.rules",
                       "tools/komalauncher/komalauncher/assets/fonts/OFL.txt"):
            self.assertIn(needed, names)
        sys.path.insert(0, str(paths.BRIDGE_PY.parent))
        import komainstall
        self.assertIn(komainstall.PACKAGE_DELTA.resolve().relative_to(komainstall.REPO.resolve()).as_posix(), names)
        self.assertFalse([n for n in names if "/test_" in n], "no tests in the package")
        self.assertFalse([n for n in names if n.startswith("firmware/") or koma_build.RICOH.match(n.rsplit("/", 1)[-1])],
                         "no Ricoh file in the package")


class Terminal(unittest.TestCase):
    """`koma` in a terminal: what it prints for each change, the USB next steps, and `koma doctor`'s pieces."""

    def lines(self, snap, last=None):
        from komalauncher import headless
        return headless.terminal_lines(snap, last, "/x/bridge.log")

    def test_one_line_per_phase(self):
        start = model.Snapshot(phase="starting", port=9700)
        self.assertEqual(self.lines(start), ["Starting Koma 1.0."])
        running = model.Snapshot(phase="running", port=9700)
        self.assertEqual(self.lines(running, start), ["Koma is running at http://localhost:9700/  (Ctrl-C quits)"])
        self.assertEqual(self.lines(running, running), [])
        self.assertEqual(self.lines(model.Snapshot(phase="starting", restart=True), running), ["Restarting."])
        self.assertIn("Port 9700 is in use.", self.lines(model.Snapshot(phase="port_in_use", port=9700), start)[0])
        self.assertIn("/x/bridge.log", self.lines(model.Snapshot(phase="failed", reason="no_answer"), start)[0])

    def test_usb_next_steps(self):
        from komalauncher import doctor
        snap = model.Snapshot(phase="running", usb_problem="TransportError: libusb not found", platform="darwin")
        self.assertIn("brew install libusb", self.lines(snap, model.Snapshot(phase="running"))[0])
        self.assertIn("apt install libusb-1.0-0", doctor.usb_next_step("OSError: libusb", "linux"))
        self.assertIn("Reinstall Koma", doctor.usb_next_step("ModuleNotFoundError: No module named 'usb'", "win32"))
        self.assertIn("Reinstall Koma", doctor.usb_next_step("TransportError: libusb not found", "win32"))
        self.assertEqual(doctor.usb_next_step(""), "")
        udev = model.Snapshot(phase="running", udev_missing=True, platform="linux")
        self.assertIn("koma --install-udev-rule", self.lines(udev, model.Snapshot(phase="running"))[0])

    def test_windows_driver_from_the_registry(self):
        from komalauncher import doctor

        class Reg:
            HKEY_LOCAL_MACHINE = "HKLM"

            def __init__(self, instances):
                self.instances = instances   # None: the camera was never seen; {name: service or None}

            def OpenKey(self, root, name):
                if root == "HKLM":
                    if self.instances is None:
                        raise FileNotFoundError(name)
                    return self
                return Instance(self.instances[name])

            def EnumKey(self, _key, index):
                names = list(self.instances)
                if index >= len(names):
                    raise OSError("no more")
                return names[index]

            def QueryValueEx(self, key, value):
                if key.service is None:
                    raise FileNotFoundError(value)
                return key.service, 1

            def CloseKey(self, _key):
                pass

        class Instance:
            def __init__(self, service):
                self.service = service

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        self.assertEqual(doctor.windows_driver(Reg(None)), "unseen")
        self.assertEqual(doctor.windows_driver(Reg({"5&1": "WinUSB"})), "winusb")
        self.assertEqual(doctor.windows_driver(Reg({"5&1": None})), "none")
        self.assertEqual(doctor.windows_driver(Reg({"5&1": "usbccgp"})), "other:usbccgp")
        self.assertTrue(doctor.windows_driver_problem("none"))
        self.assertTrue(doctor.windows_driver_problem("other:usbccgp"))
        self.assertFalse(doctor.windows_driver_problem("unseen"))

    def test_udev_rule_on_disk(self):
        from komalauncher import doctor
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(doctor.udev_rule_present((Path(d),)))
            (Path(d) / "70-koma-gr.rules").write_text((paths.ASSETS / "70-koma-gr.rules").read_text())
            self.assertTrue(doctor.udev_rule_present((Path(d),)))

    def test_package_row(self):
        """A fresh install has the delta but not Ricoh's file: fine. Only a delta that is not the recorded one is not."""
        from komalauncher import doctor
        sys.path.insert(0, str(paths.BRIDGE_PY.parent))
        import komainstall
        base = {"name": "koma-1.0.0.grmod", "version": "1.0.0", "delta_sha256": "a" * 64,
                "delta_published_sha256": "a" * 64}
        orig = komainstall.package_info
        try:
            for info, want in ((dict(base, ready=True), "koma-1.0.0.grmod, Koma 1.0"),
                               (dict(base, ready=False), "koma-1.0.0.grmod, Koma 1.0, built from Ricoh's 2.40 file "
                                                         "when you set up a card"),
                               (dict(base, ready=False, delta_sha256="b" * 64),
                                "koma-1.0.0.grmod, Koma 1.0, not the recorded build"),
                               (None, "missing")):
                komainstall.package_info = lambda info=info: info
                self.assertEqual(doctor._package(), want)
        finally:
            komainstall.package_info = orig

    def test_help_lists_what_people_use(self):
        from komalauncher.__main__ import parser
        text = parser().format_help()
        for shown in ("--tray", "--port", "--no-browser", "koma doctor"):
            self.assertIn(shown, text)
        for hidden in ("--mock", "--headless", "--self-test", "--render-screens", "--data-dir"):
            self.assertNotIn(hidden, text)


class Layout(unittest.TestCase):
    """The startup window's measurements (look.py), shared by the macOS and the Tk window."""

    def test_the_button_sits_as_far_from_the_bottom_as_from_the_side(self):
        from komalauncher import look
        self.assertEqual(look.H - look.BUTTON_TOP - look.BUTTON_H, look.PAD)
        self.assertEqual(look.BUTTON_MARGIN, look.PAD)

    def test_the_chevrons_point_is_the_text_columns_edge(self):
        """Both chevrons' leftmost ink (a square cap reaches 0.75 * sqrt(2) past its point at 45 degrees) is TEXT_X."""
        import math

        from komalauncher import look
        reach = look.CHEVRON_STROKE / 2 * math.sqrt(2)
        for open_ in (False, True):
            points = look.chevron_points(look.TEXT_X, 160.0, open_)
            self.assertAlmostEqual(min(x for x, _ in points) - reach, look.TEXT_X)

    def test_bearings_are_the_fonts_own(self):
        from fontTools.ttLib import TTFont

        from komalauncher import look
        for family, file in (("Koma Wordmark", "KomaWordmark.ttf"), ("Koma Text", "KomaText.ttf"),
                             ("Koma Text Medium", "KomaTextMedium.ttf")):
            font = TTFont(str(paths.FONTS / file))
            cmap, hmtx, em = font.getBestCmap(), font["hmtx"], font["head"].unitsPerEm
            self.assertAlmostEqual(look.STEM_BEARING[family], hmtx[cmap[ord("K")]][1] / em, places=3, msg=family)
            if family == "Koma Text":
                for digit, value in look.DIGIT_BEARING.items():
                    self.assertAlmostEqual(value, hmtx[cmap[ord(digit)]][1] / em, places=3, msg=digit)

    def test_figures_are_tabular(self):
        """Every counter and the version in tabular figures; Tk cannot ask for tnum, so the fonts' default
        figures are the tabular ones."""
        from fontTools.ttLib import TTFont
        for file in ("KomaWordmark.ttf", "KomaText.ttf", "KomaTextMedium.ttf"):
            font = TTFont(str(paths.FONTS / file))
            cmap = font.getBestCmap()
            self.assertEqual(len({font["hmtx"][cmap[c]][0] for c in range(0x30, 0x3A)}), 1, file)

    def test_a_long_path_is_cut_in_the_middle(self):
        from komalauncher import look
        path = "C:\\Users\\someone with a long name\\AppData\\Local\\Koma\\Logs\\bridge.log"
        self.assertEqual(look.fit_middle(path, len, 200), path)
        cut = look.fit_middle(path, len, 30)
        self.assertLessEqual(len(cut), 30)
        self.assertTrue(cut.startswith("C:\\Users") and cut.endswith("bridge.log") and "\u2026" in cut, cut)


class RevealLog(unittest.TestCase):
    def test_the_command_per_system(self):
        from komalauncher import system
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "bridge.log"
            self.assertEqual(system.reveal_command(log, "darwin"), ["open", str(Path(tmp))], "no file yet: its folder")
            log.write_text("x")
            self.assertEqual(system.reveal_command(log, "darwin"), ["open", "-R", str(log)])
            self.assertEqual(system.reveal_command(log, "win32"), ["explorer", f"/select,{log}"])
            linux = system.reveal_command(log, "linux")
            self.assertIn(linux, (None, ["xdg-open", str(Path(tmp))]))


@unittest.skipUnless(sys.platform == "darwin", "AppKit")
class StartupWindowMac(unittest.TestCase):
    """The AppKit window, built offscreen: the button's margins, the version on the button title's baseline, the log
    path as a link."""

    def test_layout_and_links(self):
        from AppKit import NSAccessibilityLinkRole, NSApplication

        from komalauncher import look, ui_mac
        NSApplication.sharedApplication()
        ui_mac.register_fonts()
        window = ui_mac.StartupWindow(None, Path.home() / "Library" / "Logs" / "Koma" / "bridge.log", version="1.0.0")
        window.update(model.Snapshot(phase="failed", reason="no_answer"), announce=False)
        frame = window.button.frame()
        right = frame.origin.x + frame.size.width - ui_mac.RING
        bottom = frame.origin.y + frame.size.height - ui_mac.RING   # the view is flipped: y grows down
        self.assertEqual((look.W - right, look.H - bottom), (look.BUTTON_MARGIN, look.BUTTON_MARGIN))
        version = window.version.frame().origin.y + window.version.firstBaselineOffsetFromTop()
        title = ui_mac.on_grid(look.title_baseline(look.BUTTON_TOP, look.BUTTON_H, look.BUTTON_SIZE))
        self.assertEqual(version, title, "the version's baseline is the button title's")
        self.assertTrue(window.log_link.isHidden())
        window.toggle_details()
        self.assertFalse(window.log_link.isHidden())
        self.assertEqual(window.log_link.accessibilityRole(), NSAccessibilityLinkRole)
        self.assertEqual(window.log_link.accessibilityLabel(), "~/Library/Logs/Koma/bridge.log")
        self.assertEqual(window.log_link.action(), "revealLog:")
        self.assertEqual(window.log_link.frame().origin.x + ui_mac.RING, look.DETAILS_TEXT_X)
        self.assertEqual(window.details.frame().origin.x + ui_mac.RING, look.TEXT_X)


# -------------------------------------------------------------------------------------------- real processes

def wait(sup, test, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test(sup.snapshot()):
            return True
        time.sleep(0.1)
    return False


class Live(unittest.TestCase):
    """The supervisor with real children against the simulated camera."""

    def make(self, port, **kw):
        tmp = tempfile.mkdtemp(prefix="komalauncher-test-")
        kw.setdefault("bridge_args", ["--mock", "--mock-tether", "--mock-koma"])
        sup = supervisor.Supervisor(port=port, webcam_port=port + 1, data=Path(tmp), logs=Path(tmp), poll_s=0.3, **kw)
        self.addCleanup(self.stop, sup)
        return sup

    @staticmethod
    def stop(sup):
        sup.command("quit")
        sup.join(60)

    def outside_webcam(self, sup) -> supervisor.Child:
        """komacam started the way the app's Webcam screen would run it, not by the launcher."""
        argv, env = supervisor.child_command(
            "webcam", ["--bridge-port", str(sup.port), "--port", str(sup.webcam_port), "--status-every", "60"])
        child = supervisor.Child("webcam", argv, env, sup.logs / "webcam.log").start()
        self.addCleanup(child.stop, 40.0)
        return child

    def test_start_look_restart_quit(self):
        sup = self.make(9702)
        ready = []
        sup.on_ready.append(ready.append)
        sup.start()
        self.assertTrue(wait(sup, lambda s: s.phase == "running", 30))
        self.assertEqual(ready, ["http://localhost:9702/"])
        self.assertEqual(json.loads((sup.data / "launcher.json").read_text())["phase"], "running")
        time.sleep(1.0)  # a few polls
        self.assertIn("/api", sup.reads)
        self.assertNotIn("/status", sup.reads, "nothing reads the camera while nobody looks")
        self.assertIsNone(sup.snapshot().status)
        snap = sup.look(True, wait=5)
        self.assertEqual(model.camera_line(snap.status), "Camera: GR DIGITAL · SIM")
        self.assertIn("Camera: GR DIGITAL · SIM", texts(snap))
        sup.look(False)
        self.assertTrue(wait(sup, lambda s: s.status is None, 5), "the line goes with the look")
        reads = len([r for r in sup.reads if r == "/status"])
        time.sleep(1.0)
        self.assertEqual(len([r for r in sup.reads if r == "/status"]), reads, "no reads after the look")
        pid = sup.bridge.pid
        os.kill(pid, signal.SIGKILL)
        self.assertTrue(wait(sup, lambda s: s.phase == "starting", 10), "the death is shown at once")
        self.assertTrue(sup.snapshot().restart)
        self.assertEqual(texts(sup.snapshot())[0], "Restarting")
        self.assertTrue(wait(sup, lambda s: s.phase == "running" and sup.bridge.pid != pid, 30))
        self.assertFalse(sup.snapshot().restart)
        self.assertEqual(ready, ["http://localhost:9702/"], "a restart does not open the browser again")
        sup.command("quit")
        sup.join(60)
        self.assertEqual(sup.snapshot().phase, "stopping")
        self.assertFalse(supervisor.port_in_use(9702))
        self.assertFalse((sup.data / "launcher.json").exists())

    def test_port_in_use_then_try_again(self):
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 9704))
        blocker.listen(1)
        sup = self.make(9704)
        sup.start()
        self.assertTrue(wait(sup, lambda s: s.phase == "port_in_use", 10))
        self.assertEqual(sup.bridge_starts, 0, "nothing is started on a taken port")
        blocker.close()
        sup.command("retry")
        self.assertTrue(wait(sup, lambda s: s.phase == "running", 30))

    def test_a_bridge_that_never_comes_up_fails_with_one_action(self):
        sup = self.make(9706, bridge_args=["--no-such-flag"], restart_delays=(0.2, 0.2))
        sup.start()
        self.assertTrue(wait(sup, lambda s: s.phase == "failed", 30))
        self.assertEqual((sup.bridge_starts, sup.snapshot().reason), (3, "no_answer"))
        self.assertEqual([i.action for i in model.menu(sup.snapshot()) if i.kind == "action"], ["retry", "quit"])
        self.assertEqual(texts(sup.snapshot())[:2], ["Koma could not start", "Nothing answered on port 9706"])

    def test_a_bridge_that_keeps_exiting_stops_with_its_reason(self):
        sup = self.make(9714, restart_budget=(2, 300.0), restart_delays=(0.2, 0.2, 0.2))
        sup.start()
        for _ in range(2):
            self.assertTrue(wait(sup, lambda s: s.phase == "running", 30))
            pid = sup.bridge.pid
            os.kill(pid, signal.SIGKILL)
            self.assertTrue(wait(sup, lambda s: s.phase != "running", 10))
        self.assertTrue(wait(sup, lambda s: s.phase == "failed", 10))
        self.assertEqual(sup.snapshot().reason, "exited")
        self.assertEqual(texts(sup.snapshot())[0], "Koma stopped")
        self.assertEqual(sup.bridge_starts, 2)
        sup.command("retry")
        self.assertTrue(wait(sup, lambda s: s.phase == "running", 30), "Try again starts it")

    def test_webcam_line_follows_an_outside_webcam(self):
        sup = self.make(9708)
        sup.start()
        self.assertTrue(wait(sup, lambda s: s.phase == "running", 30))
        self.assertFalse(sup.snapshot().webcam)
        webcam = self.outside_webcam(sup)
        self.assertTrue(wait(sup, lambda s: s.webcam, 90), "Webcam running while komacam streams")
        self.assertIn("Webcam running", texts(sup.snapshot()))
        self.assertTrue(supervisor.get_json(9708, "/status", 2)["tether"])
        self.assertTrue(webcam.stop(40.0))
        self.assertTrue(wait(sup, lambda s: not s.webcam, 10))
        self.assertFalse(supervisor.get_json(9708, "/status", 2)["tether"], "komacam left remote control")

    def test_quit_leaves_remote_control_the_app_left_on(self):
        sup = self.make(9700)
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        logging.getLogger("komalauncher").addHandler(handler)
        self.addCleanup(logging.getLogger("komalauncher").removeHandler, handler)
        logging.getLogger("komalauncher").setLevel(logging.INFO)
        sup.start()
        self.assertTrue(wait(sup, lambda s: s.phase == "running", 30))
        code, _ = supervisor.http("POST", 9700, "/tether/start", 30, data=b"")  # as the app's Shoot does
        self.assertEqual(code, 200)
        self.assertTrue(supervisor.get_json(9700, "/status", 2)["tether"])
        sup.command("quit")
        sup.join(90)
        self.assertTrue([r for r in records if r.startswith("POST /tether/stop -> 200")], records)
        self.assertFalse(supervisor.port_in_use(9700))


class Cli(unittest.TestCase):
    """The command line as a bundle runs it: headless, a second launch, the stop pipe when the launcher dies, and a
    child that inherited SIGINT as ignored."""

    def launch(self, data, port, *extra):
        env = {**os.environ, "PYTHONPATH": str(HERE)}
        return subprocess.Popen([sys.executable, "-m", "komalauncher", "--mock", "--headless", "--no-browser",
                                 "--port", str(port), "--webcam-port", str(port + 1), "--data-dir", data, *extra],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def test_second_launch_hands_off_and_exits(self):
        with tempfile.TemporaryDirectory() as data:
            first = self.launch(data, 9710)
            try:
                for _ in range(150):
                    if (read_state(Path(data)) or {}).get("phase") == "running":
                        break
                    time.sleep(0.1)
                self.assertEqual(read_state(Path(data))["phase"], "running")
                second = self.launch(data, 9710)
                out, err = second.communicate(timeout=30)
                self.assertEqual(second.returncode, 0)
                self.assertIn("Koma is already running: http://localhost:9710/", err)
                self.assertEqual(first.poll(), None, "the first launcher keeps running")
            finally:
                first.send_signal(signal.SIGTERM)
                out, _ = first.communicate(timeout=60)
            self.assertEqual(first.returncode, 0)
            self.assertFalse(supervisor.port_in_use(9710))
            phases = [json.loads(line)["phase"] for line in out.splitlines() if line.startswith("{")]
            self.assertEqual(phases[-1], "stopping")
            self.assertNotIn("camera", out, "headless reads no camera")

    def test_children_stop_when_the_launcher_is_killed(self):
        with tempfile.TemporaryDirectory() as data:
            first = self.launch(data, 9712)
            for _ in range(150):
                if (read_state(Path(data)) or {}).get("phase") == "running":
                    break
                time.sleep(0.1)
            self.assertTrue(supervisor.port_in_use(9712))
            first.kill()  # no Quit: the stop pipe closes, the bridge stops by itself
            first.communicate(timeout=10)
            for _ in range(100):
                if not supervisor.port_in_use(9712):
                    break
                time.sleep(0.1)
            self.assertFalse(supervisor.port_in_use(9712), "an orphaned bridge would hold the port")

    def test_terminal_mode_reports_a_port_in_use_and_exits_1(self):
        with tempfile.TemporaryDirectory() as data, socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 9720))
            blocker.listen(1)
            env = {**os.environ, "PYTHONPATH": str(HERE)}
            done = subprocess.run([sys.executable, "-m", "komalauncher", "--mock", "--no-browser", "--port", "9720",
                                   "--data-dir", data], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stderr.splitlines()[:2], [
            "Starting Koma 1.0.",
            "Port 9720 is in use. Quit the program using it, or run koma --port with another number."])
        self.assertEqual(done.stdout, "")

    @unittest.skipIf(os.name == "nt", "SIG_IGN inheritance is a POSIX case")
    def test_a_bridge_that_inherited_sigint_ignored_still_stops(self):
        argv, env = supervisor.child_command("bridge", ["--mock", "--port", "9716", "--quiet"])
        child = subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, cwd=str(paths.TOOLS),
                                 preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
        try:
            for _ in range(150):
                if supervisor.port_in_use(9716):
                    break
                time.sleep(0.1)
            self.assertTrue(supervisor.port_in_use(9716))
            child.stdin.close()  # the launcher is gone
            child.wait(15)
        finally:
            if child.poll() is None:
                child.kill()
        self.assertFalse(supervisor.port_in_use(9716))


if __name__ == "__main__":
    unittest.main()
