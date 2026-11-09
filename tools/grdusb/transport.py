"""Real USB access through pyusb and libusb. Tests never import the opening path; they use mock.py."""
from __future__ import annotations

import ctypes.util
import os
import time

from .descriptors import (BULK_IN, BULK_OUT, Configuration, Device, Endpoint, Interface, VENDOR,
                          mismatches, string_mismatches)

READ_SIZE = 0x10400
TIMEOUT_MS = 6000
SETTLE_S = 2.0  # Ricoh's XP client waits ~2 s before opening; the Mac client does not (D)
LINE_STATE_REQUEST = (0x41, 0x22, 3, 0)  # vendor "set DTR+RTS" as sent by Ricoh's Mac client. Opt-in only; unverified.


class TransportError(Exception):
    pass


class TransportTimeout(TransportError):
    pass


class GateRefused(Exception):
    """A gate refused the device. The message states exactly what had been sent by then."""


def _backend():
    import usb.backend.libusb1

    def find(_name):
        for path in ("/opt/homebrew/opt/libusb/lib/libusb-1.0.dylib", "/usr/local/opt/libusb/lib/libusb-1.0.dylib"):
            if os.path.exists(path):
                return path
        return ctypes.util.find_library("usb-1.0")

    backend = usb.backend.libusb1.get_backend(find_library=find)
    if backend is None:
        raise TransportError("libusb not found")
    return backend


def _model(device) -> Device:
    """Builds the descriptor model from what libusb holds. Does not open the device."""
    configs = []
    for config in device:
        interfaces = [Interface(i.bInterfaceNumber, i.bInterfaceClass, i.bInterfaceSubClass, i.bInterfaceProtocol,
                                tuple(Endpoint(e.bEndpointAddress, e.bmAttributes, e.wMaxPacketSize) for e in i))
                      for i in config]
        configs.append(Configuration(config.bConfigurationValue, tuple(interfaces)))
    return Device(device.idVendor, device.idProduct, device.bcdUSB, device.bNumConfigurations, tuple(configs),
                  f"bus {device.bus} address {device.address}")


def enumerate_ricoh() -> list[Device]:
    """Read-only listing of Ricoh devices. Never opens or configures anything."""
    import usb.core
    return [_model(device) for device in usb.core.find(find_all=True, idVendor=VENDOR, backend=_backend())]


def configure_device(device, claim, sleep, settle_s, line_state=False, unconfigured_errors=()):
    """Bring-up after the gate, in this order, each step recorded so every message says what was sent:
    1. GET_CONFIGURATION (read only). Already configured to something other than 1: refuse before any change.
    2. set_configuration(1) always (firmware bulk IN needs a configured state, D 0x5fda8), then verify it took.
    3. wait; optional unverified line-state request; claim interface 0. No reset or clear-halt.
    Any failure other than a gate refusal becomes TransportError naming the step. The caller must still call close()."""
    done = []
    try:
        try:
            before = device.get_active_configuration().bConfigurationValue
        except unconfigured_errors:
            before = 0  # libusb reports an unconfigured device as an error
        done.append("GET_CONFIGURATION")
        if before not in (0, 1):
            raise GateRefused(f"active configuration is {before}, expected 0 or 1; only GET_CONFIGURATION was sent, "
                              "no configuration change and no command")
        done.append("set_configuration(1)")  # recorded first: a failure inside it may already have changed state
        device.set_configuration(1)
        active = device.get_active_configuration().bConfigurationValue
        if active != 1:
            raise GateRefused(f"active configuration is {active} after set_configuration(1) was sent; the interface "
                              "was not claimed and no command was sent")
        sleep(settle_s)
        done.append("settle")
        if line_state:
            done.append("control 0x41/0x22")
            device.ctrl_transfer(*LINE_STATE_REQUEST, None)
        done.append("claim_interface(0)")
        claim(device, 0)
    except GateRefused:
        raise
    except Exception as error:
        raise TransportError(f"bring-up failed during {done[-1] if done else 'GET_CONFIGURATION'} "
                             f"({type(error).__name__}: {error}); steps begun: {', '.join(done) or 'none'}; "
                             "no command was sent") from error


def open_gated(line_state=False, sleep=time.sleep, settle_s=SETTLE_S, usb_core=None, usb_util=None, backend=None):
    """Finds the single Ricoh device and applies every gate that needs no state change, as early as possible:
    numeric descriptors (nothing opened), then string descriptors (device opened, control reads only), then the
    active-configuration check happens in bring_up step 1. Releases the handle on every failure path."""
    if usb_core is None:
        import usb.core as usb_core
        import usb.util as usb_util
    if backend is None:
        backend = _backend()
    found = list(usb_core.find(find_all=True, idVendor=VENDOR, backend=backend))
    if len(found) != 1:
        raise GateRefused(f"expected exactly one Ricoh device, found {len(found)}; nothing was opened")
    device = found[0]
    problems = mismatches(_model(device))
    if problems:
        raise GateRefused("descriptors do not match; nothing was opened: " + "; ".join(problems))
    try:
        problems = string_mismatches(device.manufacturer, device.product)
    except Exception as error:
        usb_util.dispose_resources(device)
        raise TransportError(f"could not read the descriptor strings ({type(error).__name__}: {error}); "
                             "only string-descriptor control reads were attempted, no command was sent") from error
    if problems:
        usb_util.dispose_resources(device)
        raise GateRefused("descriptor strings do not match; the device was opened for string-descriptor reads only, "
                          "no configuration change and no command was sent: " + "; ".join(problems))
    return UsbTransport(device, usb_core, usb_util, line_state, sleep, settle_s)


class UsbTransport:
    """Only built by open_gated, after the descriptor and string gates pass. Sends no reset or clear-halt. The vendor
    control request 0x41/0x22 goes out only when line_state=True (off by default, unverified)."""

    def __init__(self, device, usb_core, usb_util, line_state: bool = False, sleep=time.sleep,
                 settle_s: float = SETTLE_S):
        self._device, self._usb_core, self._usb_util = device, usb_core, usb_util
        self._line_state, self._sleep, self._settle_s = line_state, sleep, settle_s
        self._claimed = False

    def bring_up(self) -> None:
        def claim(device, number):
            self._usb_util.claim_interface(device, number)
            self._claimed = True

        configure_device(self._device, claim, self._sleep, self._settle_s, self._line_state,
                         (self._usb_core.USBError,))

    def write(self, data: bytes) -> int:
        """Returns the byte count libusb reports; the caller treats anything but len(data) as a failure."""
        try:
            return self._device.write(BULK_OUT, data, TIMEOUT_MS)
        except self._usb_core.USBTimeoutError as error:
            raise TransportTimeout(str(error)) from error
        except self._usb_core.USBError as error:
            raise TransportError(str(error)) from error

    def read(self) -> bytes:
        try:
            return bytes(self._device.read(BULK_IN, READ_SIZE, TIMEOUT_MS))
        except self._usb_core.USBTimeoutError as error:
            raise TransportTimeout(str(error)) from error
        except self._usb_core.USBError as error:
            raise TransportError(str(error)) from error

    def close(self) -> None:
        """Safe at any point after open_gated, including after a failed bring_up. Always disposes the handle."""
        try:
            if self._claimed:
                self._claimed = False
                self._usb_util.release_interface(self._device, 0)
        finally:
            self._usb_util.dispose_resources(self._device)
