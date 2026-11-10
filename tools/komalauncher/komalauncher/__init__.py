"""Koma launcher: runs komabridge, opens the Koma app in the browser and shows the camera's state, from a terminal
(`koma`) or the menu bar and system tray (`koma --tray`). See tools/komalauncher/README.md."""
from __future__ import annotations

# The version Koma ships as, and the only place the launcher sets it: `koma --version`, the startup window, the
# terminal lines and the Python package (packaging/koma_build.py reads it). It is the Koma package version
# (docs/versioning.md: one product version, "Koma X.Y.Z"; the first public release is 1.0.0).
VERSION: str | None = "1.0.0"


def version_text(version: str | None = VERSION) -> str:
    """The version as text shows it (docs/versioning.md): "1.0" for 1.0.0, "1.0.1" when the patch digit is not 0."""
    if not version:
        return ""
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) == 3 and parts[2] == "0" else version
