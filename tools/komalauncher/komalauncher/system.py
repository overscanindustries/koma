"""Small host services with one call each: open the app in the browser, copy the address, show the log file, read
the light or dark setting the tray icon follows. macOS uses AppKit directly in ui_mac.py; these are the
cross-platform versions."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

log = logging.getLogger("komalauncher")


def open_url(url: str) -> bool:
    try:
        if sys.platform.startswith("win"):
            os.startfile(url)  # noqa: S606  (the default browser)
            return True
        return bool(webbrowser.open(url, new=2))
    except Exception as error:  # noqa: BLE001
        log.warning("could not open %s: %s", url, error)
        return False


def reveal_command(path: Path, platform: str | None = None) -> list[str] | None:
    """The command that shows a file in the system's file browser: selected in Windows Explorer and the macOS Finder;
    on Linux the folder through xdg-open (no common way to select a file there). A file that is not there yet: its
    folder."""
    platform = platform or ("win32" if sys.platform.startswith("win") else sys.platform)
    there = path.exists()
    if platform == "win32":
        return ["explorer", f"/select,{path}"] if there else ["explorer", str(path.parent)]
    if platform == "darwin":
        return ["open", "-R", str(path)] if there else ["open", str(path.parent)]
    return ["xdg-open", str(path.parent)] if shutil.which("xdg-open") else None


def reveal_file(path: Path) -> bool:
    argv = reveal_command(Path(path))
    if argv is None:
        log.warning("no file browser to show %s", path)
        return False
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
        return True
    except OSError as error:
        log.warning("could not show %s: %s", path, error)
        return False


def copy_text(text: str, tk_root=None) -> bool:
    """The text on the clipboard. Windows: the clipboard API. Linux: wl-copy, xclip or xsel, else Tk (kept alive by
    the tray's Tk root). macOS: pbcopy (ui_mac.py uses NSPasteboard)."""
    try:
        if sys.platform.startswith("win"):
            return _copy_windows(text)
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
            return True
        for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if shutil.which(argv[0]):
                subprocess.run(argv, input=text.encode("utf-8"), check=True, timeout=5)
                return True
        if tk_root is not None:
            tk_root.clipboard_clear()
            tk_root.clipboard_append(text)
            return True
    except Exception as error:  # noqa: BLE001
        log.warning("could not copy: %s", error)
    return False


def _copy_windows(text: str) -> bool:
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    data = (text + "\0").encode("utf-16-le")
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(13, handle))  # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()


def tray_is_dark() -> bool:
    """Whether the taskbar or panel the tray icon sits on is dark (the icon is then drawn light).
    Windows: SystemUsesLightTheme. Linux: the GNOME colour scheme when it says so; panels are dark by default."""
    if sys.platform.startswith("win"):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 0
        except OSError:
            return True
    return True


def window_is_dark() -> bool:
    """Whether app windows use the dark appearance (the startup window follows it)."""
    if sys.platform.startswith("win"):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            return True
    if shutil.which("gsettings"):
        try:
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                                 capture_output=True, text=True, timeout=2).stdout
            if "prefer-dark" in out:
                return True
            if "default" in out or "prefer-light" in out:  # GNOME's 'default' is the light style
                return False
        except (OSError, subprocess.SubprocessError):
            pass
    return True
