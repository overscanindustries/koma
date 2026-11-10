"""Every word the launcher shows, in one place for the copy audit.

Voice: the Koma app's. Object as subject, one fact per line, no exclamation, no
reassurance, sentence case; menu lines and labels take no terminal period, sentences in the startup window do. The
camera's own strings keep the camera's spelling (GR DIGITAL, USB CONNECTION, ORIGINAL). No surface names the
launcher's parts (the bridge, komacam): people see Koma, the camera and the webcam. Part names stay in the logs.
"""
from __future__ import annotations

APP = "Koma"
ADDRESS = "http://localhost:{port}/"

# Tray menu: status lines (shown, not clickable), top to bottom
STARTING = "Starting"
RESTARTING = "Restarting"                        # Koma was running, exited, and is being started again
RUNNING = "Koma running · localhost:{port}"
STOPPING = "Stopping"
PORT_IN_USE = "Port {port} is in use"
PORT_IN_USE_FIX = "Quit the program using it, then try again"
FAILED = "Koma could not start"
FAILED_WHY = "Nothing answered on port {port}"
STOPPED = "Koma stopped"
STOPPED_WHY = "It failed {count} times in {minutes} minutes"

# The camera line, read from GET /status while the menu is open
CAMERA = "Camera: {model}"
CAMERA_SIMULATED = "Camera: {model} · SIM"
CAMERA_CONNECTED = "Camera: connected"
CAMERA_NOT_FOUND = "Camera: not found"
CAMERA_WRONG_MODE = "Camera: set USB CONNECTION to ORIGINAL"
CAMERA_NOT_OPENED = "Camera: could not be opened"
CAMERA_ONE_ONLY = "Camera: connect one camera only"
CAMERA_NO_USB = "Camera: USB not available"

WEBCAM_RUNNING = "Webcam running"

# USB readiness, checked once before the bridge starts (child.py usbcheck; Linux: the udev rule). The bridge starts
# either way, so card work goes on; the error itself is under Details.
USB_UNAVAILABLE = "Koma cannot use USB on this computer. Install Koma again."
USB_UNAVAILABLE_REPO = "Install pyusb and libusb for this Python."   # run from the repository, not a bundle
UDEV_NEEDED = "Koma needs permission to open the camera over USB."
ALLOW = "Allow"

# Tray menu: actions
OPEN = "Open Koma"
COPY = "Copy address"
TRY_AGAIN = "Try again"
QUIT = "Quit Koma"
QUIT_HELP = "Disconnects the camera and stops the webcam."

# Startup window (first launch, or a launch that could not start)
WINDOW_TITLE = "Koma"
WINDOW_STARTING = "Starting."
WINDOW_RESTARTING = "Restarting."
WINDOW_STOPPING = "Stopping."
WINDOW_ELAPSED = "{phase} · {elapsed}"          # past 10 s: "Starting · 0:12"
WINDOW_READY = {
    "darwin": "Koma is the K in the menu bar.",
    # Windows 10 and 11 place a new tray icon behind the arrow at the tray's edge (Show hidden icons) by default
    "win32": "Koma is the K in the system tray, or behind the tray's arrow.",
    "linux": "Koma is the K in the system tray.",
}
WINDOW_PORT_IN_USE = "Port {port} is in use. Quit the program using it, then try again."
WINDOW_FAILED = "Koma could not start. Nothing answered on port {port}."
WINDOW_STOPPED = "Koma stopped. It failed {count} times in {minutes} minutes."
DETAILS = "Details"                              # the disclosure that shows where the log file is
LOG_REVEAL = {                                   # the log path under Details is a link: what it does (tooltip, VoiceOver)
    "darwin": "Show in Finder",
    "win32": "Show in File Explorer",
    "linux": "Open the folder",
}

# Command line only (a second launch, `koma` in a terminal, --headless, `koma doctor`); never on a screen
CLI_ALREADY_RUNNING = "Koma is already running: {url}"
CLI_NO_TRAY = "No system tray here. Koma runs without one: {url} (Ctrl-C quits)."
CLI_NO_TRAY_TERMINAL = "No system tray here, or pystray and Pillow are missing. Koma runs in this terminal."
CLI_NO_VERSION = "Koma, version not set"
CLI_STARTING = "Starting Koma {version}."
CLI_RESTARTING = "Restarting."
CLI_RUNNING = "Koma is running at {url}  (Ctrl-C quits)"
CLI_STOPPING = "Stopping."
CLI_PORT_IN_USE = "Port {port} is in use. Quit the program using it, or run koma --port with another number."
CLI_FAILED = "Koma could not start. Nothing answered on port {port}. Log: {log}"
CLI_STOPPED = "Koma stopped. It failed {count} times in {minutes} minutes. Log: {log}"
CLI_TRAY_MISSING = {
    "darwin": "The menu bar icon needs PyObjC. Install it into Koma's Python: {pip} pyobjc-framework-Cocoa",
    "other": "The tray icon needs pystray and Pillow. Install them into Koma's Python: {pip} pystray Pillow",
}
CLI_TRAY_REINSTALL = "The menu bar icon needs PyObjC. Reinstall Koma: {reinstall}"

# USB readiness in the terminal and in `koma doctor`: one plain next step each
USB_NO_PYUSB = "pyusb is missing from Koma's Python. Reinstall Koma: {reinstall}"
USB_NO_LIBUSB = {
    "darwin": "libusb is not installed. Install it: brew install libusb",
    "linux": "libusb is not installed. Install it with the package manager (Debian and Ubuntu: sudo apt install "
             "libusb-1.0-0).",
    "win32": "libusb did not load. Reinstall Koma: {reinstall}",
}
USB_UDEV_MISSING = "This user cannot open the camera yet. Add the udev rule once: koma --install-udev-rule"
USB_WINDOWS_DRIVER = ("The camera has no WinUSB driver. With the camera on and in USB CONNECTION ORIGINAL, install "
                      "WinUSB for it with Zadig (zadig.akeo.ie): the device with USB ID 05CA 2218.")
USB_READY = "USB is ready."
USB_HELP = "Install steps: https://github.com/overscanindustries/koma/blob/main/docs/install.md"
