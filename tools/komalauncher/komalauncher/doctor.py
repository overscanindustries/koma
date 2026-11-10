"""`koma doctor`: whether Koma can use USB on this computer, and the one next step when it cannot.

Nothing here enumerates, opens or configures a USB device. pyusb is imported and libusb's backend is loaded (the same
check as `--child usbcheck`); on Linux the udev rule is looked for on disk; on Windows the camera's driver is read
from the registry, where Windows records every device it has seen (no device is touched). The camera does not need
to be plugged in.
"""
from __future__ import annotations

import ctypes.util
import os
import sys
from pathlib import Path

from . import VERSION, paths, version_text, words

VID_PID = "VID_05CA&PID_2218"     # the GR DIGITAL in USB CONNECTION ORIGINAL (tools/grdusb/descriptors.py)
UDEV_DIRS = (Path("/etc/udev/rules.d"), Path("/run/udev/rules.d"), Path("/lib/udev/rules.d"),
             Path("/usr/lib/udev/rules.d"))
HOMEBREW_LIBUSB = ("/opt/homebrew/opt/libusb/lib/libusb-1.0.dylib", "/usr/local/opt/libusb/lib/libusb-1.0.dylib")


def reinstall_command() -> str:
    """How this copy of Koma was installed, as the command that installs it again."""
    prefix = Path(sys.prefix).parts
    if "Cellar" in prefix:
        return "brew reinstall koma"
    if "pipx" in prefix:
        return "pipx install --force koma-gr"
    return "python3 -m pip install --force-reinstall koma-gr"


def pip_command() -> str:
    """The pip of the Python running Koma, for the optional tray packages."""
    prefix = Path(sys.prefix).parts
    if "pipx" in prefix:
        return "pipx inject koma-gr"
    return f'"{sys.executable}" -m pip install'


def usb_problem_kind(problem: str) -> str:
    """'pyusb' or 'libusb' for an error from the usbcheck child or check_usb(); '' when there is none."""
    if not problem:
        return ""
    if "usb.core" in problem or "'usb'" in problem or "pyusb" in problem.lower():
        return "pyusb"
    return "libusb"


def usb_next_step(problem: str, platform: str | None = None) -> str:
    platform = platform or paths.platform_key()
    kind = usb_problem_kind(problem)
    if kind == "pyusb":
        return words.USB_NO_PYUSB.format(reinstall=reinstall_command())
    if kind == "libusb":
        return words.USB_NO_LIBUSB.get(platform, words.USB_NO_LIBUSB["linux"]).format(reinstall=reinstall_command())
    return ""


def check_usb() -> tuple[str, str, str]:
    """(pyusb version, libusb path or "", error). Imports pyusb and loads libusb's backend in this process, the bundled
    or packaged libusb first (child._libusb); enumerates nothing."""
    from . import child
    child._libusb()
    try:
        import usb
        import usb.core  # noqa: F401
    except Exception as error:  # noqa: BLE001
        return "", "", f"{type(error).__name__}: {error}"
    version = getattr(usb, "__version__", "") or "present"
    try:
        if str(paths.TOOLS) not in sys.path:
            sys.path.insert(0, str(paths.TOOLS))
        from grdusb import transport
        transport._backend()
    except Exception as error:  # noqa: BLE001
        return version, "", f"{type(error).__name__}: {error}"
    found = next((p for p in HOMEBREW_LIBUSB if os.path.exists(p)), None) if sys.platform == "darwin" else None
    return version, found or ctypes.util.find_library("usb-1.0") or "loaded", ""


def udev_rule_present(dirs=UDEV_DIRS) -> bool:
    """Linux: a udev rule names the camera (Koma's 70-koma-gr.rules, or one written by hand from docs/install.md)."""
    for folder in dirs:
        try:
            rules = sorted(folder.glob("*.rules"))
        except OSError:
            continue
        for rule in rules:
            try:
                text = rule.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if "05ca" in text and "2218" in text:
                return True
    return False


def windows_driver(winreg=None) -> str:
    """Windows: the camera's driver as the registry records it, without touching the device.
    'winusb' (ready), 'none' (seen, no driver installed), 'other:<service>' (a driver that is not WinUSB),
    'unseen' (never plugged in as ORIGINAL), or 'unknown' (the registry could not be read)."""
    if winreg is None:
        try:
            import winreg  # noqa: PLC0415
        except ImportError:
            return "unknown"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Enum\USB\{VID_PID}")
    except FileNotFoundError:
        return "unseen"
    except OSError:
        return "unknown"
    services = []
    try:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(root, name) as instance:
                    services.append(str(winreg.QueryValueEx(instance, "Service")[0]))
            except FileNotFoundError:
                services.append("")
            except OSError:
                continue
    finally:
        winreg.CloseKey(root)
    if any(s.lower() == "winusb" for s in services):
        return "winusb"
    named = [s for s in services if s]
    if named:
        return f"other:{named[0]}"
    return "none" if services else "unknown"


def windows_driver_problem(state: str) -> bool:
    return state == "none" or state.startswith("other:")


def _package() -> str:
    """The Koma package this install puts on a card, as the bridge reports it (komainstall.package_info)."""
    bridge = str(paths.BRIDGE_PY.parent)
    if bridge not in sys.path:
        sys.path.insert(0, bridge)
    try:
        import komainstall
        info = komainstall.package_info()
    except Exception as error:  # noqa: BLE001
        return f"could not be read ({type(error).__name__}: {error})"
    if not info:
        return "missing"
    state = "" if info.get("sha256") == info.get("published_sha256") else ", not the recorded build"
    return f"{info.get('name')}, Koma {version_text(info.get('version'))}{state}"


def run(out=None) -> int:
    out = out or sys.stdout
    platform = paths.platform_key()
    rows: list[tuple[str, str, str]] = []
    steps: list[str] = []

    rows.append(("ok", "Koma", version_text(VERSION) or "version not set"))
    rows.append(("ok", "Python", f"{sys.version.split()[0]} ({sys.executable})"))
    pyusb, libusb, error = check_usb()
    rows.append(("ok" if pyusb else "no", "pyusb", pyusb or "missing"))
    if pyusb:
        rows.append(("ok" if libusb else "no", "libusb", libusb or "not loadable"))
    if error:
        rows.append(("", "", error))
        steps.append(usb_next_step(error, platform))
    if platform == "linux":
        present = udev_rule_present()
        rows.append(("ok" if present else "no", "udev rule", "installed" if present else "missing"))
        if not present:
            steps.append(words.USB_UDEV_MISSING)
    if platform == "win32":
        state = windows_driver()
        text = {"winusb": "WinUSB", "none": "no driver installed", "unseen": "camera not plugged in yet",
                "unknown": "could not be read"}.get(state, state.replace("other:", "") + " (not WinUSB)")
        rows.append(("ok" if state == "winusb" else "no" if windows_driver_problem(state) else "", "camera driver",
                     text))
        if windows_driver_problem(state):
            steps.append(words.USB_WINDOWS_DRIVER)
    app = (paths.APP_DIST / "index.html").is_file()
    rows.append(("ok" if app else "no", "app", "present" if app else f"missing ({paths.APP_DIST})"))
    package = _package()
    package_ok = ", Koma " in package and "not the recorded build" not in package
    rows.append(("ok" if package_ok else "no", "package", package))

    for mark, label, value in rows:
        mark = {"ok": "ok", "no": "--"}.get(mark, "  ")
        out.write(f"  {mark}  {label:<14}{value}\n" if label else f"      {'':<14}{value}\n")
    out.write("\n")
    if steps:
        for step in steps:
            out.write(step + "\n")
        out.write(words.USB_HELP + "\n")
        return 1
    out.write(words.USB_READY + "\n")
    return 0
