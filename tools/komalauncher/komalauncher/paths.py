"""Where the launcher finds the bridge, the webcam and the app, and where it keeps its own files.

The repository and every bundle share one layout. This package sits at <root>/tools/komalauncher/komalauncher, the
bridge at <root>/tools/komaset/bridge/komabridge.py, the webcam at <root>/tools/komacam/komacam.py and the built app at
<root>/tools/komaapp/dist. A bundle is a copy of those files (packaging/manifest.py), so there is one path logic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent              # <root>/tools/komalauncher/komalauncher
LAUNCHER_DIR = PKG.parent                          # <root>/tools/komalauncher
TOOLS = LAUNCHER_DIR.parent                        # <root>/tools
BRIDGE_PY = TOOLS / "komaset" / "bridge" / "komabridge.py"
WEBCAM_PY = TOOLS / "komacam" / "komacam.py"
APP_DIST = TOOLS / "komaapp" / "dist"
ASSETS = PKG / "assets"
FONTS = ASSETS / "fonts"

APP_DIR_NAME = "Koma"


def platform_key() -> str:
    """'darwin', 'win32' or 'linux' (anything else counts as linux)."""
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


def data_dir(mock: bool = False, override: str | os.PathLike | None = None) -> Path:
    """The launcher's own folder: the single-instance lock, launcher.json and the first-run marker.
    A --mock launcher keeps a separate folder, so it never collides with the real one."""
    if override:
        base = Path(override)
    elif platform_key() == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    elif platform_key() == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_DIR_NAME
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "koma"
    if mock and not override:
        base = base / "mock"
    base.mkdir(parents=True, exist_ok=True)
    return base


def log_dir(data: Path, override: bool = False) -> Path:
    """Logs: ~/Library/Logs/Koma on macOS, <data>/Logs elsewhere; always <data>/logs when --data-dir is given."""
    if not override and platform_key() == "darwin":
        logs = Path.home() / "Library" / "Logs" / APP_DIR_NAME
        if data.name == "mock":
            logs = logs / "mock"
    else:
        logs = data / ("Logs" if platform_key() == "win32" else "logs")
    logs.mkdir(parents=True, exist_ok=True)
    return logs


def bundled_libusb() -> str | None:
    """The libusb a bundle carries, else None (the bridge then finds the system's, as in the repository).

    Order: the libusb-package wheel's library (the CI bundles); a libusb copied next to the bundled interpreter's
    libraries (local macOS builds copy Homebrew's there). The module is located, not imported, so its own
    dependencies are not needed."""
    names = {"darwin": "libusb-1.0.dylib", "win32": "libusb-1.0.dll", "linux": "libusb-1.0.so"}
    name = names[platform_key()]
    try:
        import importlib.util
        spec = importlib.util.find_spec("libusb_package")
    except (ImportError, ValueError):
        spec = None
    if spec and spec.submodule_search_locations:
        for folder in spec.submodule_search_locations:
            candidate = Path(folder) / name
            if candidate.is_file():
                return str(candidate)
    for folder in (Path(sys.prefix) / "lib", Path(sys.prefix) / "DLLs", Path(sys.prefix)):
        for candidate in (folder / name, folder / (name + ".0")):
            if candidate.is_file():
                return str(candidate)
    return None
