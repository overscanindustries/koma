"""`komalauncher --child bridge|webcam ARGS`: runs komabridge.main(ARGS) or komacam.main(ARGS) unchanged, with four
pieces of host glue around them and nothing else. `--child usbcheck` is the USB readiness check (usbcheck() below).

1. SIGINT is Python's own handler again, whatever the parent left (a process started from a shell job or a service
   can inherit SIGINT as ignored, and then the stop below would do nothing). komacam installs its own handler anyway.
2. The stop pipe. A line "stop" on stdin, or stdin closing (the launcher quit or died), interrupts the main thread
   the way Ctrl-C does: the bridge leaves serve_forever and closes its socket; komacam's own SIGINT handler ends live
   view and sends POST /tether/stop (keyset,0,2 + remtout) before it exits. This works on Windows too, where a
   windowless process cannot be sent Ctrl-C.
3. The bundled libusb. tools/grdusb asks ctypes.util.find_library("usb-1.0") after its Homebrew paths; inside a
   bundle that answer is the libusb the bundle carries. Outside a bundle nothing changes.
4. Windows has no SIGHUP, which komacam registers; there it is SIGBREAK.

The launcher itself runs only the bridge. `--child webcam` runs komacam with the bundle's Python for whoever needs
it inside a bundle: the self-test (as the outside webcam the menu reports) and a bridge-side webcam control later.
"""
from __future__ import annotations

import _thread
import signal
import sys
import threading

from . import paths


def _watch_stdin():
    stream = sys.stdin
    if stream is None:
        return

    def watch():
        try:
            while True:
                line = stream.readline()
                if not line or line.strip() == "stop":
                    break
        except (OSError, ValueError):
            pass
        _thread.interrupt_main()  # SIGINT semantics: the bridge's KeyboardInterrupt, komacam's handler

    threading.Thread(target=watch, daemon=True, name="stop-pipe").start()


def _libusb():
    lib = paths.bundled_libusb()
    if not lib:
        return
    import ctypes.util
    original = ctypes.util.find_library

    def find_library(name):
        if name in ("usb-1.0", "libusb-1.0", "usb"):
            return lib
        return original(name)

    ctypes.util.find_library = find_library


def _signals():
    # interrupt_main() raises KeyboardInterrupt only while SIGINT has a Python handler; an inherited SIG_IGN would
    # turn the stop pipe into a no-op and leave the bridge holding its port
    signal.signal(signal.SIGINT, signal.default_int_handler)
    if sys.platform.startswith("win") and not hasattr(signal, "SIGHUP"):
        signal.SIGHUP = signal.SIGBREAK  # komacam: signal.signal(signal.SIGHUP, ...) must not fail here


def usbcheck() -> int:
    """Can this Python use USB? Imports pyusb (usb.core) and asks grdusb for its libusb backend, the bundled libusb
    first (_libusb). Nothing is enumerated or opened: no device is touched. Exit 0, or 1 with the error on stderr."""
    _libusb()
    try:
        import usb.core  # noqa: F401
        if str(paths.TOOLS) not in sys.path:
            sys.path.insert(0, str(paths.TOOLS))
        from grdusb import transport
        transport._backend()
    except Exception as error:  # noqa: BLE001  (ImportError, OSError, TransportError: all mean "no USB")
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


def run(kind: str, argv: list[str]) -> int:
    if kind == "usbcheck":
        return usbcheck()
    _signals()
    _watch_stdin()
    _libusb()
    if kind == "bridge":
        sys.path.insert(0, str(paths.BRIDGE_PY.parent))
        import komabridge
        return int(komabridge.main(argv) or 0)
    if kind == "webcam":
        sys.path.insert(0, str(paths.WEBCAM_PY.parent))
        import komacam
        return int(komacam.main(argv) or 0)
    print(f"unknown child {kind!r}: bridge, webcam or usbcheck", file=sys.stderr)
    return 2
