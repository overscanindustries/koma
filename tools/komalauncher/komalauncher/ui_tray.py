"""Windows and Linux: the tray icon (pystray) and the startup window (Tk). NOT TESTED on Windows or Linux: written to
the same model as the macOS UI, exercised hidden on macOS by the unit tests, and drawn by the CI workflow
(`--render-screens` under Xvfb and on the Windows runner).

Tk owns the main thread; pystray runs in its own thread (win32 message loop, or GLib for AppIndicator on Linux).
The supervisor's changes reach Tk through a queue polled every 100 ms. Without a tray that has menus (a Linux
desktop with no AppIndicator support), the launcher runs headless and still opens the app in the browser.

pystray cannot tell when its menu opens ("not all supported platforms allow the menu to be generated when shown",
pystray's update_menu), and a timed GET /status would open a newly plugged camera. So this menu carries no camera
line; the app shows the camera.

The startup window is drawn on one canvas, to the macOS window's measurements (look.py):
baselines placed from the font's own ascent, so Archivo and the fallbacks (Segoe UI, DejaVu Sans) sit alike, and every
measurement multiplied by the display's scale (ui_scale), since a DPI-aware Tk draws in device pixels. The tray K dims
while Koma is not running.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import queue
import sys
import threading
import time
from pathlib import Path

from . import VERSION, look, model, paths, screens, selftest, system, version_text, words
from .look import (BUTTON_H, BUTTON_PAD_X, BUTTON_SIZE, BUTTON_TOP, DETAILS_GAP, DETAILS_LINE, DETAILS_TEXT_X, H,
                   LINE, PAD, SMALL_SIZE, STATUS_BASELINE, TEXT_SIZE, TEXT_X, TOKENS, W, WORDMARK_BASELINE,
                   WORDMARK_SIZE, WORDMARK_TRACK)

log = logging.getLogger("komalauncher")

X_HEIGHT = {"Koma Text": look.X_HEIGHT, "Segoe UI": 0.5, "DejaVu Sans": 0.547}  # per em: the dot, the chevron
CAP_HEIGHT = {"Koma Text Medium": look.CAP_HEIGHT, "Segoe UI": 0.7, "DejaVu Sans": 0.729}


def ui_scale(root) -> float:
    """Device pixels per 96-dpi pixel: 1 on a 100 % display, 1.5 at 150 %. Windows makes this process DPI aware (crisp
    text), after which Tk draws in device pixels, so every measurement is multiplied by this. In quarter steps (the
    Windows scale factors), never below 1 (an X server that reports a low DPI)."""
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:  # noqa: BLE001
        return 1.0
    return max(1.0, round(dpi / 96 * 4) / 4)


def register_fonts() -> bool:
    """Koma Wordmark / Text / Text Medium for this process: GDI private fonts on Windows, fontconfig on Linux."""
    fonts = sorted(paths.FONTS.glob("*.ttf"))
    try:
        if sys.platform.startswith("win"):
            gdi = ctypes.windll.gdi32
            return all(gdi.AddFontResourceExW(str(f), 0x10, 0) for f in fonts)  # FR_PRIVATE
        name = ctypes.util.find_library("fontconfig")
        if not name:
            return False
        fc = ctypes.CDLL(name)
        fc.FcConfigGetCurrent.restype = ctypes.c_void_p
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        config = fc.FcConfigGetCurrent()
        return all(fc.FcConfigAppFontAddFile(config, str(f).encode()) for f in fonts)
    except (OSError, AttributeError) as error:
        log.warning("fonts: %s", error)
        return False


def _mix(a: str, b: str, amount: float) -> str:
    """a over b at `amount` (Tk has no alpha): the link's underline at rest is --sub at 55 % (the app's .link)."""
    pa, pb = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (a, b))
    return "#" + "".join(f"{round(x * amount + y * (1 - amount)):02x}" for x, y in zip(pa, pb, strict=True))


def _rounded(x1, y1, x2, y2, r, steps=6) -> list:
    """A rounded rectangle as polygon points (quarter circles of `steps` segments)."""
    import math
    points = []
    for cx, cy, start in ((x2 - r, y1 + r, -90), (x2 - r, y2 - r, 0), (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180)):
        for i in range(steps + 1):
            angle = math.radians(start + 90 * i / steps)
            points += [cx + r * math.cos(angle), cy + r * math.sin(angle)]
    return points


class StartupWindow:
    """The wordmark, one status line, the version (once one is set), one action, and in the failed state a Details
    link that shows where the log file is, as a link to it. Same places as the macOS window (look.py), in 96-dpi
    pixels times the display's scale."""

    def __init__(self, root, on_retry, dark: bool | None = None, log_file: Path | None = None,
                 scale: float | None = None, version: str | None = version_text(), on_reveal=None):
        import tkinter as tk
        import tkinter.font as tkfont
        self.tk, self.root, self.on_retry = tk, root, on_retry
        self.s = ui_scale(root) if scale is None else scale
        dark = system.window_is_dark() if dark is None else dark
        self.c = {k: v[0] if dark else v[1] for k, v in TOKENS.items()}
        have = register_fonts()
        fallback = "Segoe UI" if sys.platform.startswith("win") else "DejaVu Sans"
        self.family = "Koma Text" if have else fallback
        self.mark_family = "Koma Wordmark" if have else fallback
        self.button_family = "Koma Text Medium" if have else fallback
        self.f_mark = tkfont.Font(root=root, family=self.mark_family, size=-self.px(WORDMARK_SIZE), weight="bold")
        self.f_text = tkfont.Font(root=root, family=self.family, size=-self.px(TEXT_SIZE))
        self.f_small = tkfont.Font(root=root, family=self.family, size=-self.px(SMALL_SIZE))
        self.f_button = tkfont.Font(root=root, family=self.button_family, size=-self.px(BUTTON_SIZE))
        self.hairline = max(1, round(self.s))
        self.log_file = log_file
        self.log_text = system_path(log_file) if log_file else ""
        self.on_reveal = on_reveal or (lambda: system.reveal_file(log_file))
        root.title(words.WINDOW_TITLE)
        root.configure(bg=self.c["bg"])
        root.geometry(f"{self.px(W)}x{self.px(H)}")
        root.resizable(False, False)
        try:
            root.iconphoto(True, tk.PhotoImage(file=str(paths.ASSETS / "icon-256.png")))
        except tk.TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        canvas = tk.Canvas(root, width=self.px(W), height=self.px(H), bg=self.c["bg"], highlightthickness=0, bd=0,
                           takefocus=1)
        canvas.place(x=0, y=0)
        self.canvas = canvas
        # the wordmark: KOMA, tracked 0.14 em (Tk has no letter spacing: one glyph at a time, each placed where the
        # word up to it ends, so the font's own kerning still applies where Tk measures it); its K's ink on PAD
        size = self.px(WORDMARK_SIZE)
        top = self.p(WORDMARK_BASELINE) - self.f_mark.metrics("ascent")
        left = self.p(PAD) - look.bearing(self.mark_family, size)
        for i, ch in enumerate("KOMA"):
            x = left + self.f_mark.measure("KOMA"[:i]) + WORDMARK_TRACK * size * i
            canvas.create_text(x, top, text=ch, font=self.f_mark, fill=self.c["ink"], anchor="nw")
        if version:  # bottom-left, its baseline on the button title's, its first figure's ink on PAD
            small = self.px(SMALL_SIZE)
            canvas.create_text(self.p(PAD) - look.bearing(self.family, small, version[0]),
                               self.title_baseline() - self.f_small.metrics("ascent"), text=version,
                               font=self.f_small, fill=self.c["faint"], anchor="nw")
        self.line: model.WindowLine | None = None
        self.details_open = False
        self.focus = None             # "details", "log", "button" or None: what Return and Space act on
        self.pressed = False
        for sequence, handler in (("<Escape>", lambda _e: root.withdraw()), ("<Return>", self._activate),
                                  ("<KP_Enter>", self._activate), ("<space>", self._activate),
                                  ("<Tab>", lambda _e: self._cycle(1)), ("<Shift-Tab>", lambda _e: self._cycle(-1)),
                                  ("<ISO_Left_Tab>", lambda _e: self._cycle(-1))):
            root.bind(sequence, handler)

    # measurements -------------------------------------------------------------

    def p(self, value: float) -> float:
        """A measurement from look.py in this display's pixels."""
        return value * self.s

    def px(self, value: float) -> int:
        """A size in whole pixels (Tk font sizes), rounded half up: 12.5 at 1x is 13."""
        return int(value * self.s + 0.5)

    def _x_height(self, size: float) -> float:
        return X_HEIGHT.get(self.family, 0.53) * size

    def _cap(self, size: float) -> float:
        return CAP_HEIGHT.get(self.button_family, 0.7) * size

    def title_baseline(self) -> float:
        """The button title's baseline: its cap height centred in the button (look.title_baseline)."""
        return self.p(BUTTON_TOP) + self.p(BUTTON_H) / 2 + self._cap(self.px(BUTTON_SIZE)) / 2

    def _wrap(self, value: str, width: float) -> list[str]:
        """Two lines at most, broken between words (Tk's own wrapping cannot set a 20 px line)."""
        out, current = [], ""
        for word in value.split(" "):
            trial = f"{current} {word}".strip()
            if current and self.f_text.measure(trial) > width:
                out.append(current)
                current = word
            else:
                current = trial
        out.append(current)
        return out[:1] + [" ".join(out[1:])] if len(out) > 2 else out

    # drawing ------------------------------------------------------------------

    def update(self, snap: model.Snapshot, now: float | None = None):
        line = model.window_line(snap, now)
        if line == self.line:
            return
        if self.line is None or line.key != self.line.key:
            self.details_open = False
            self.focus = None
        self.line = line
        self.draw()

    def draw(self):
        c, canvas, line = self.c, self.canvas, self.line
        canvas.delete("state")
        size = self.px(TEXT_SIZE)
        x = self.p(TEXT_X) - look.bearing(self.family, size)   # the stems of the first letter on TEXT_X
        lines = self._wrap(line.text, self.p(W - PAD) - x)
        for i, part in enumerate(lines):
            baseline = self.p(STATUS_BASELINE + LINE * i)
            canvas.create_text(x, baseline - self.f_text.metrics("ascent"), text=part, font=self.f_text,
                               fill=c["ink"], anchor="nw", tags=("state",))
        middle = self.p(STATUS_BASELINE) - self._x_height(size) / 2   # the dot on the first line's x-height
        canvas.create_oval(self.p(PAD), middle - self.p(3), self.p(PAD + 6), middle + self.p(3), fill=c[line.dot],
                           outline="", tags=("state",))
        if line.details and self.log_text:
            self._draw_details(self.p(STATUS_BASELINE + LINE * (len(lines) - 1) + DETAILS_GAP))
        if line.action:
            self._draw_button(line.action_text)
        self._draw_focus()

    def _draw_link(self, value: str, baseline: float, tag: str, action) -> tuple:
        """A link's words at DETAILS_TEXT_X on baseline (--sub, underlined at 55 %); returns its box, which
        takes the click (an unfilled Tk item takes clicks on its outline only)."""
        c, canvas = self.c, self.canvas
        tx = self.p(DETAILS_TEXT_X)
        width = self.f_small.measure(value)
        box = (self.p(TEXT_X - 4) if tag == "details" else tx - self.p(4), baseline - self.p(13),
               tx + width + self.p(4), baseline + self.p(6))
        canvas.create_rectangle(*box, outline="", fill=c["bg"], tags=("state", tag))
        canvas.create_text(tx, baseline - self.f_small.metrics("ascent"), text=value, font=self.f_small,
                           fill=c["sub"], anchor="nw", tags=("state", tag))
        y = round(baseline + 0.2 * self.px(SMALL_SIZE))
        canvas.create_line(tx, y, tx + width, y, fill=_mix(c["sub"], c["bg"], 0.55), width=self.hairline,
                           tags=("state", tag))
        canvas.tag_bind(tag, "<Button-1>", lambda _e: action())
        canvas.tag_bind(tag, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind(tag, "<Leave>", lambda _e: canvas.configure(cursor=""))
        return box

    def _draw_details(self, baseline: float):
        self.details_box = self._draw_link(words.DETAILS, baseline, "details", self._toggle_details)
        middle = baseline - self._x_height(self.px(SMALL_SIZE)) / 2
        points = look.chevron_points(self.p(TEXT_X), middle, self.details_open, self.s)  # its point on TEXT_X
        self.canvas.create_line(*[v for point in points for v in point], fill=self.c["sub"],
                                width=self.p(look.CHEVRON_STROKE), capstyle="projecting", joinstyle="miter",
                                tags=("state", "details"))
        if self.details_open:   # the log file's path (or the USB error), a link that shows the log (cut in the middle)
            text = (self.line.detail if self.line and self.line.detail else self.log_text)
            shown = look.fit_middle(text, self.f_small.measure, self.p(W - PAD - DETAILS_TEXT_X))
            self.log_box = self._draw_link(shown, baseline + self.p(DETAILS_LINE), "log", self.on_reveal)

    def _draw_button(self, title: str):
        c, canvas = self.c, self.canvas
        width = self.f_button.measure(title) + 2 * self.p(BUTTON_PAD_X)
        x2, y1 = self.p(W - PAD), self.p(BUTTON_TOP)       # the outer box: PAD from the right, BUTTON_MARGIN up
        x1, y2 = x2 - width, y1 + self.p(BUTTON_H)
        inset = self.hairline / 2
        canvas.create_polygon(_rounded(x1 + inset, y1 + inset, x2 - inset, y2 - inset, self.p(4) - inset),
                              fill=c["raise"], outline=c["line"], width=self.hairline, tags=("state", "button"))
        canvas.create_text((x1 + x2) / 2, self.title_baseline() - self.f_button.metrics("ascent"), text=title,
                           font=self.f_button, fill=c["ink"], anchor="n", tags=("state", "button"))
        self.button_box = (x1, y1, x2, y2)
        canvas.tag_bind("button", "<ButtonPress-1>", self._press)
        canvas.tag_bind("button", "<ButtonRelease-1>", self._release)
        canvas.tag_bind("button", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind("button", "<Leave>", lambda _e: canvas.configure(cursor=""))

    def _draw_focus(self):
        """A 2 px --ink ring 2 px outside whatever has keyboard focus."""
        self.canvas.delete("focus")
        box = {"details": getattr(self, "details_box", None), "log": getattr(self, "log_box", None),
               "button": getattr(self, "button_box", None)}.get(self.focus)
        if box and self.focus in self._focusables():
            x1, y1, x2, y2 = box
            gap = self.p(3)
            self.canvas.create_polygon(_rounded(x1 - gap, y1 - gap, x2 + gap, y2 + gap, self.p(7)), fill="",
                                       outline=self.c["ink"], width=self.p(2), tags=("focus",))

    # behaviour ----------------------------------------------------------------

    def _focusables(self) -> list[str]:
        line = self.line
        out = []
        if line and line.details and self.log_text:
            out.append("details")
            if self.details_open:
                out.append("log")
        if line and line.action:
            out.append("button")
        return out

    def _cycle(self, step: int):
        items = self._focusables()
        if not items:
            return "break"
        index = items.index(self.focus) + step if self.focus in items else (0 if step > 0 else -1)
        self.focus = items[index % len(items)]
        self._draw_focus()
        return "break"

    def _activate(self, _event=None):
        target = self.focus or ("button" if self.line and self.line.action else None)
        if target == "details":
            self._toggle_details()
        elif target == "log":
            self.on_reveal()
        elif target == "button":
            self.on_retry()
        return "break"

    def _toggle_details(self):
        self.details_open = not self.details_open
        if not self.details_open and self.focus == "log":
            self.focus = "details"
        self.draw()

    def _press(self, _event):
        self.pressed = True
        self.canvas.move("button", 0, 1)   # pressed: 1 px down, no colour change

    def _release(self, event):
        if not self.pressed:
            return
        self.pressed = False
        self.canvas.move("button", 0, -1)
        x1, y1, x2, y2 = self.button_box
        if x1 <= event.x <= x2 and y1 <= event.y <= y2:
            self.on_retry()

    def toggle_details(self):
        self._toggle_details()

    def destroy(self):
        self.canvas.destroy()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.canvas.focus_set()


def system_path(path: Path) -> str:
    """A path as people read it on this system: ~ for the home folder on Linux, the full path on Windows."""
    if sys.platform.startswith("win"):
        return str(path)
    home = str(Path.home())
    value = str(path)
    return "~" + value[len(home):] if value.startswith(home + "/") else value


def tray_items(pystray, snap: model.Snapshot, act) -> list:
    """The tray menu as pystray items: status lines shown and disabled, actions bound through act(name), the separator
    where the model has one. pystray rebuilds the menu from this on every update_menu()."""
    out = []
    for entry in model.menu(snap):
        if entry.kind == "separator":
            out.append(pystray.Menu.SEPARATOR)
        elif entry.kind == "status":
            out.append(pystray.MenuItem(entry.text, None, enabled=False))
        else:
            out.append(pystray.MenuItem(entry.text, act(entry.action), enabled=entry.enabled, default=entry.default))
    return out


def dim(image):
    """The tray K while Koma is not running: the same K at half its alpha, as the system draws a disabled item."""
    image = image.convert("RGBA")
    out = image.copy()
    out.putalpha(image.getchannel("A").point(lambda a: a // 2))
    return out


def tray_images(Image) -> dict:
    """{dimmed: image}: the light K on a dark taskbar or panel, the dark K on a light one."""
    image = Image.open(paths.ASSETS / ("tray-light-64.png" if system.tray_is_dark() else "tray-dark-64.png"))
    image = image.convert("RGBA")
    return {False: image, True: dim(image)}


def run(sup, open_browser: bool = True, first_run: bool = False, self_test: bool = False) -> int:
    try:
        import pystray
        from PIL import Image
        if not getattr(pystray.Icon, "HAS_MENU", True):
            raise RuntimeError(f"the {pystray.Icon.__module__} tray has no menus")
        import tkinter as tk
    except Exception as error:  # noqa: BLE001
        log.warning("no tray with menus here (%s); running without one", error)
        from . import headless
        if self_test:
            return headless.run(sup, open_browser=open_browser, self_test=True,
                                announce=words.CLI_NO_TRAY.format(url=sup.url))
        print(words.CLI_NO_TRAY_TERMINAL, file=sys.stderr, flush=True)  # then the terminal's own lines
        return headless.terminal(sup, open_browser=open_browser, log_file=str(sup.logs / "bridge.log"))

    if sys.platform.startswith("win"):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text in the startup window
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    root.withdraw()
    events: queue.Queue = queue.Queue()
    state = {"snap": sup.snapshot(), "phase": None, "code": 0}
    # the window's one button: Try again, or Allow (the udev rule) when the line asks for it
    window = StartupWindow(root, lambda: sup.command(window.line.action if window.line and window.line.action
                                                     else "retry"), log_file=sup.logs / "bridge.log")
    window.update(state["snap"])

    def act(action: str):
        def handler(_icon=None, _item=None):
            snap = state["snap"]
            if action == "open":
                system.open_url(snap.address)
            elif action == "copy":
                events.put(("copy", snap.address))
            elif action == "retry":
                sup.command("retry")
            elif action == "quit":
                sup.command("quit")
        return handler

    images = tray_images(Image)
    icon = pystray.Icon("koma", images[model.dimmed(state["snap"])], words.APP,
                        menu=pystray.Menu(lambda: tray_items(pystray, state["snap"], act)))

    sup.changes.add(lambda snap: events.put(("snap", snap)))
    sup.on_exit.append(lambda: events.put(("exit", None)))
    if open_browser:
        sup.on_ready.append(system.open_url)

    def pump():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "snap":
                    state["snap"] = value
                    window.update(value)
                    image = images[model.dimmed(value)]
                    if icon.icon is not image:   # the K dims while Koma is not running
                        icon.icon = image
                    if value.phase != state["phase"] and value.phase in ("port_in_use", "failed"):
                        window.show()
                    state["phase"] = value.phase
                    usb = model.usb_line_key(value)   # USB not usable, or the udev rule missing: shown once
                    if usb and usb != state.get("usb", ""):
                        window.show()
                    state["usb"] = usb
                    try:
                        icon.update_menu()
                    except Exception:  # noqa: BLE001
                        pass
                elif kind == "copy":
                    system.copy_text(value, tk_root=root)
                elif kind == "report":
                    print(selftest.dumps(value), flush=True)
                    state["code"] = 0 if value.get("ok") else 1
                    icon.stop()
                    root.quit()
                    return
                elif kind == "exit" and not self_test:
                    icon.stop()
                    root.quit()
                    return
        except queue.Empty:
            pass
        if state["snap"].phase in ("starting", "stopping"):
            window.update(state["snap"])   # the elapsed counter past 10 s
        root.after(100, pump)

    import signal
    for signum in (signal.SIGINT, signal.SIGTERM):  # a logout or `kill` quits the way Quit Koma does
        signal.signal(signum, lambda *_: sup.command("quit"))
    threading.Thread(target=icon.run, daemon=True, name="tray").start()
    if first_run:
        window.show()
    sup.start()
    if self_test:
        threading.Thread(target=selftest.run, args=(sup, lambda report: events.put(("report", report))),
                         daemon=True, name="self-test").start()
    root.after(100, pump)
    root.mainloop()
    return state["code"]


# ------------------------------------------------------------------------------------------------ screens

def menus_text(port: int) -> str:
    """The tray menu in each state, built through tray_items with pystray's own classes when pystray is here (CI),
    else through the model alone: what the Windows and Linux menus show."""
    try:
        import pystray
    except Exception:  # noqa: BLE001
        return screens.menus_text(port, looks=False, subtitles=False)
    lines = []
    for name, snap in screens.menu_states(port, looks=False).items():
        lines.append(f"[{name}]{'   K dimmed' if model.dimmed(snap) else ''}")
        for entry, item in zip(model.menu(snap), tray_items(pystray, snap, lambda _name: (lambda *_: None)),
                               strict=True):
            if item is pystray.Menu.SEPARATOR:
                lines.append("  ----------------")
            elif entry.kind == "status":
                lines.append(f"  {item.text}")
            else:
                lines.append(f"  > {item.text}{'' if item.enabled else '   (dimmed)'}")
        lines.append("")
    return "\n".join(lines)


def render_screens(out: str, port: int = 8765) -> int:
    """Windows and Linux: the Tk startup window in each state, dark and light, captured from the screen (CI: Xvfb on
    Linux, the Windows runner's desktop) as startup-<state>-<theme>-tk.png, plus menus-tray.txt."""
    import tkinter as tk
    from PIL import ImageGrab
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    log_file = paths.log_dir(paths.data_dir()) / "bridge.log" if sys.platform.startswith("win") else \
        Path.home() / ".local" / "state" / "koma" / "logs" / "bridge.log"
    states = screens.window_states(port, paths.platform_key())
    shots = [(name, "", version_text()) for name in states]
    if not VERSION:   # the version slot, drawn for review only
        shots.append(("failed", "-version-sample", screens.SAMPLE_VERSION))
    for dark, tag in ((True, "dark"), (False, "light")):
        for name, suffix, version in shots:
            snap, now, details = states[name]
            window = StartupWindow(root, lambda: None, dark=dark, log_file=log_file, version=version)
            window.update(snap, now=now)
            if details:
                window.toggle_details()
            root.deiconify()
            root.lift()
            for _ in range(5):
                root.update()
                time.sleep(0.1)
            x, y = root.winfo_rootx(), root.winfo_rooty()
            width, height = root.winfo_width(), root.winfo_height()
            ImageGrab.grab(bbox=(x, y, x + width, y + height)).save(folder / f"startup-{name}{suffix}-{tag}-tk.png")
            window.destroy()
    root.destroy()
    (folder / "menus-tray.txt").write_text(menus_text(port), encoding="utf-8")
    return 0
