"""SIMULATED camera for tests. It implements our reading of the spec and nothing else.

Passing against this mock proves the tool is consistent with docs/internals/07-usb-protocol.md. It does not establish how a real
camera behaves. Commands the tool must never send are logged as violations. DELE, -STOR and host data frames are
accepted only when the transport is built writable (stage-update); otherwise they are violations too.
"""
from __future__ import annotations

import json
import struct

from .descriptors import Configuration, Device, Endpoint, Interface
from .transport import TransportError, TransportTimeout, configure_device

NOTICE = "[SIMULATED CAMERA: this result says nothing about a real GR DIGITAL]"

_ENDPOINTS = (Endpoint(0x81, 2, 512), Endpoint(0x02, 2, 512), Endpoint(0x83, 3, 64))
_VENDOR_CONFIG = (Interface(0, 0xff, 0, 0, _ENDPOINTS),)
DESCRIPTOR_VARIANTS = {
    "original": Device(0x05ca, 0x2218, 0x0200, 2, (Configuration(1, _VENDOR_CONFIG), Configuration(2, _VENDOR_CONFIG))),
    "mass-storage": Device(0x05ca, 0x0109, 0x0200, 2, (
        Configuration(1, (Interface(0, 8, 6, 0x50, _ENDPOINTS[:2]),)), Configuration(2, _VENDOR_CONFIG))),
    "ptp": Device(0x05ca, 0x0331, 0x0200, 1, (Configuration(1, (Interface(0, 6, 1, 1, _ENDPOINTS),)),)),
    "wrong-vendor": Device(0x1234, 0x2218, 0x0200, 2, (Configuration(1, _VENDOR_CONFIG),)),
    "wrong-class": Device(0x05ca, 0x2218, 0x0200, 2, (Configuration(1, (Interface(0, 8, 6, 0x50, _ENDPOINTS),)),)),
    "wrong-endpoints": Device(0x05ca, 0x2218, 0x0200, 2, (
        Configuration(1, (Interface(0, 0xff, 0, 0, (Endpoint(0x84, 2, 512), Endpoint(0x02, 2, 512))),)),)),
    "wrong-packet": Device(0x05ca, 0x2218, 0x0200, 2, (
        Configuration(1, (Interface(0, 0xff, 0, 0, (Endpoint(0x81, 2, 16), _ENDPOINTS[1], _ENDPOINTS[2])),)),)),
}
STRING_VARIANTS = {"good": ("Ricoh Company Ltd.", "GR DIGITAL"), "wrong": ("Ricoh Company Ltd.", "Caplio R5")}
FAULTS = ("none", "timeout", "short-read", "truncated-frame", "bad-start", "bad-reserved", "bad-trailer", "bad-channel",
          "oversize", "wrong-model", "garbage-reply", "data-as-reply", "short-transfer", "long-transfer",
          "bad-listing", "open-error", "no-card", "skip-configure", "stray-empty", "empty-forever", "missing-zlp",
          "short-write", "mid-timeout", "mid-bad-frame", "configure-fails", "claim-fails", "already-config-2",
          "config-sticks-at-2",
          # stage-update faults (writable mock only)
          "short-free", "stor-open-error", "write-error", "corrupt-readback", "stor-timeout", "short-data-write",
          "card-quickver", "card-script", "card-qrflect5", "card-old-update", "dele-odd", "stuck-qrflect",
          # get-many listing with a repeated 8.3 alias (AppleDouble sidecars)
          "dup-names")
SETTLE_S = 2.0
PACKET = 512
EMPTY_READ_COST_S = 0.5  # virtual seconds one empty read costs; tests never sleep
ALLOWED_VERBS = {"-VER", "-GCID", "-GSID", "-TIME", "-DCAP", "-NLST", "RETR"}
WRITE_VERBS = {"DELE", "-STOR"}
WRITABLE_CAPACITY, WRITABLE_FREE, SHORT_FREE = 32_000_000, 24_000_000, 2_000_000
CARD_EXTRAS = {"card-quickver": {"/EXT0/QUICKVER.UP": b"x"}, "card-script": {"/EXT0/SCRIPT.MG1": b"x"},
               "card-qrflect5": {"/EXT0/QRFLECT5": b"x" * 10},
               "card-old-update": {"/EXT0/QRFLECT3": b"old3" * 100, "/EXT0/QRFLECT7": b"old7" * 10},
               "stuck-qrflect": {"/EXT0/QRFLECT9": b"x" * 10}}  # DELE says 250 but keeps it


def _dirent(name: str, ext: str, attr: int, size: int) -> bytes:
    record = bytearray(32)
    record[0:8] = name.encode().ljust(8, b"\0")
    record[8:11] = ext.encode().ljust(3, b"\0")
    record[11] = attr
    struct.pack_into("<HH", record, 0x16, (12 << 11) | (30 << 5), ((2007 - 1980) << 9) | (10 << 5) | 24)
    struct.pack_into(">I", record, 0x1c, size)
    return bytes(record)


PHOTO = bytes(range(256)) * 700  # 179200 bytes: forces three data frames at 64 KiB per frame.
# EXACT.JPG: 503 bytes gives an 8+503+1 padded frame of exactly 512 bytes, which the camera must follow with a ZLP.
# FULL.JPG: a 65536-byte payload is a 65548-byte frame, never a packet multiple, so no ZLP.
FILES = {"/EXT0/DCIM/MANY/A.JPG": b"aaa", "/EXT0/DCIM/MANY/B.JPG": b"bbbb",
         "/EXT0/DCIM/100RICOH/RIMG0001.JPG": PHOTO, "/EXT0/DCIM/100RICOH/EMPTY.JPG": b"",
         "/EXT0/DCIM/100RICOH/EXACT.JPG": bytes(range(251)) * 2 + b"\x07" * 1,
         "/EXT0/DCIM/100RICOH/FULL.JPG": b"\x5a" * 0x10000,
         "/IROM/DATA/ONE.BIN": b"internal-one", "/IROM/DATA/TWO.BIN": bytes(range(200))}
DIRECTORIES = {
    "/EXT0": [_dirent("DCIM", "", 0x10, 0)],
    "/EXT0/DCIM": [_dirent(".", "", 0x10, 0), _dirent("..", "", 0x10, 0), _dirent("100RICOH", "", 0x10, 0)],
    "/EXT0/DCIM/100RICOH": [_dirent("RIMG0001", "JPG", 0x20, len(PHOTO))],
    "/IROM": [],
    "/IROM/DATA": [_dirent("ONE", "BIN", 0x20, 12), _dirent("SUB", "", 0x10, 0), _dirent("TWO", "BIN", 0x20, 200)],
    "/EXT0/DCIM/MANY": [_dirent("A", "JPG", 0x20, 3), _dirent("SUB", "", 0x10, 0), _dirent("B", "JPG", 0x20, 4)],
}


def _frame(channel: int, payload: bytes, *, start=1, reserved=b"\xff\xff", trailer=3) -> bytes:
    frame = bytes([start, channel]) + reserved + struct.pack("<I", len(payload)) + payload + bytes([trailer])
    return frame + bytes(-len(frame) & 3)


class MockTransport:
    """Parses host frames as the firmware does and answers with simulated replies."""

    def __init__(self, fault: str = "none", log_path: str | None = None, line_state: bool = False,
                 version: str = "02.40", camera_id: str = "0011223344556677", writable: bool = False):
        self.fault = fault
        self.writable = writable
        self.files = {path: bytes(body) for path, body in FILES.items()}
        self.files.update(CARD_EXTRAS.get(fault, {}) if writable else {})
        self.capacity = WRITABLE_CAPACITY if writable else 1024000
        self.free = (SHORT_FREE if fault == "short-free" else WRITABLE_FREE) if writable else 512000
        self.free -= sum(len(body) for body in CARD_EXTRAS.get(fault, {}).values()) if writable else 0
        self.directories = dict(DIRECTORIES)
        if fault == "dup-names":
            alias = _dirent("_R001~39", "JPG", 0x20, 6)
            self.directories["/EXT0/DCIM/MANY"] = [DIRECTORIES["/EXT0/DCIM/MANY"][0], alias, alias, alias,
                                                   DIRECTORIES["/EXT0/DCIM/MANY"][2]]
            self.files["/EXT0/DCIM/MANY/_R001~39.JPG"] = b"sidecr"
        self._upload = None  # [path, remaining] while a -STOR data phase is open
        self._data_frames = 0
        self.version = version
        self.camera_id = camera_id
        self.sent = []
        self.violations = []
        self.events = []
        self._segments = []  # one entry per camera-to-host transfer (a frame)
        self._log_path = log_path
        self._line_state = line_state
        self._configured = False
        self._claimed = False
        self._empties = 3 if fault == "stray-empty" else 0
        self.now = 0.0

    def clock(self) -> float:
        """Virtual time so empty-read deadlines cost no real seconds."""
        return self.now

    def bring_up(self) -> None:
        """Runs the real configure_device against a simulated libusb device, so the order of steps, the refusal
        messages and the failure handling are the production code's."""
        device = _SimulatedDevice(self)
        try:
            configure_device(device, device.claim, lambda seconds: self.events.append(f"settle {seconds}"), SETTLE_S,
                             self._line_state)
        finally:
            self._save()

    def _unconfigured(self, what: str):
        if not self._configured:
            self.violations.append(f"{what} before configuration")
            self._save()
            raise TransportError(f"simulated: bulk {what} before set_configuration; firmware stays silent")

    def write(self, data: bytes) -> int:
        self._unconfigured("write")
        if not data:
            self.violations.append("zero-length write: Ricoh's client appends no ZLP")
            self._save()
            return 0
        start, channel, reserved, length = struct.unpack_from("<BBHI", data, 0)
        if channel == 2:
            return self._data(data, start, reserved, length)
        malformed = (start != 1 or channel != 1 or reserved != 0 or data[8 + length] != 3 or len(data) % 4
                     or not data[8:8 + length].endswith(b"\r\n"))
        text = data[8:8 + length].decode("latin-1")
        self.sent.append(text)
        verb = text.split(" ")[0].strip()
        if self._upload is not None:  # the firmware drops everything but -ABOR during a data phase (0xcb834)
            self.violations.append(f"command during the upload data phase: {text}")
            self._save()
            return len(data)
        if malformed or verb not in ALLOWED_VERBS | (WRITE_VERBS if self.writable else set()):
            self.violations.append(text)
            self._queue(_frame(1, b"501 Syntax error in parameters or arguments.\r\n"))
            self._save()
            return len(data)
        if self.fault == "short-write":
            self.events.append(f"short write: accepted {len(data) - 1} of {len(data)} bytes, no answer")
            self._save()
            return len(data) - 1
        self._queue(self._answer(verb, text[len(verb):].strip()))
        self._save()
        return len(data)

    def _data(self, data: bytes, start: int, reserved: int, length: int) -> int:
        """Host data frame (channel 2), valid only inside a -STOR data phase. The camera sends no reply per frame; it
        subtracts each frame's length and answers 250 when the count reaches zero (D 0xcbc30)."""
        malformed = (start != 1 or reserved != 0 or not 1 <= length <= 0x10000 or len(data) < 9 + length
                     or data[8 + length] != 3 or len(data) % 4)
        if malformed or self._upload is None or not self.writable:
            self.violations.append(f"data frame of {length} bytes outside an upload or malformed")
            self._save()
            return len(data)
        self.sent.append(f"<data {length}>")
        self._data_frames += 1
        if self.fault == "stor-timeout" and self._data_frames == 2:
            self.events.append("bulk write timed out in data frame 2")
            self._save()
            raise TransportTimeout("simulated: bulk write timed out mid-upload")
        if self.fault == "short-data-write":
            self.events.append(f"short write: accepted {len(data) - 1} of {len(data)} bytes, data frame")
            self._save()
            return len(data) - 1
        path, remaining = self._upload
        if length > remaining:
            self.violations.append(f"data frame of {length} bytes, only {remaining} announced bytes left")
            self._save()
            return len(data)
        self.files[path] += data[8:8 + length]
        self.free -= length
        self._upload[1] -= length
        if self._upload[1] == 0:
            self._upload = None
            self.files[path] = bytes(self.files[path])
            self._queue(self._reply("451 Write error" if self.fault == "write-error" else "250 -STOR command success."))
        self._save()
        return len(data)

    def _root_listing(self) -> bytes:
        records = list(DIRECTORIES["/EXT0"])
        for path, body in self.files.items():
            if path.startswith("/EXT0/") and "/" not in path[6:]:
                stem, _, ext = path[6:].partition(".")
                records.append(_dirent(stem, ext, 0x20, len(body)))
        return b"".join(records)

    def _queue(self, blob: bytes) -> None:
        """Splits camera output into one transfer per frame, as the firmware sends them."""
        while blob:
            size = len(blob)
            if len(blob) >= 8 and blob[0] == 1:
                size = min(size, (8 + struct.unpack_from("<I", blob, 4)[0] + 1 + 3) & ~3)
            self._segments.append([bytearray(blob[:size]), size])
            blob = blob[size:]

    def _save(self) -> None:
        if self._log_path:
            with open(self._log_path, "w") as target:
                json.dump({"sent": self.sent, "violations": self.violations, "simulated": True, "events": self.events}, target)

    def _reply(self, text: str) -> bytes:
        payload = text.encode() + b"\r\n"
        faults = {"bad-start": {"start": 2}, "bad-reserved": {"reserved": b"\0\0"}, "bad-trailer": {"trailer": 4}}
        if self.fault in faults:
            return _frame(1, payload, **faults[self.fault])
        if self.fault == "bad-channel":
            return _frame(7, payload)
        if self.fault == "oversize":
            return _frame(1, b"x" * 0x101)
        if self.fault == "truncated-frame":
            return _frame(1, payload)[:6]
        if self.fault == "data-as-reply":
            return _frame(2, payload)
        if self.fault == "garbage-reply":
            return _frame(1, b"hello\r\n")
        return _frame(1, payload)

    def _answer(self, verb: str, argument: str) -> bytes:
        if self.fault == "timeout":
            return b""
        if verb == "-VER":
            return self._reply("200 3.14 01.00" if self.fault == "wrong-model" else f"200 9.17 {self.version}")
        if verb == "-GCID":
            return self._reply(f"200 CameraID={self.camera_id} for -GCID.")
        if verb == "-GSID":
            return self._reply("200 SD ID=SIMULATEDCARD0000 for -GSID.")
        if verb == "-TIME":
            return self._reply("200 2007/10/24 12:00:00 for -TIME.")
        if verb == "-DCAP":
            if self.fault == "no-card" and argument == "/EXT0":
                return _frame(1, b"451 Requested action aborted. Local error in processing.\r\n")
            return self._reply(f"200 {argument} capacity {self.capacity} byte,free {self.free} byte.")
        if verb == "-NLST":
            if self.fault == "open-error" or argument not in self.directories and argument != "/":
                return _frame(1, b"450 Open error\r\n")
            data = (b"/EXT0" if argument == "/" else self._root_listing() if argument == "/EXT0"
                    else b"".join(self.directories[argument]))
            if self.fault == "bad-listing":
                data = b"\0" * 33
            frames = _frame(2, data) if data else b""
            return (self._reply("150 data connection for -NLST.") + frames
                    + self._reply("250 -NLST command success."))
        if verb == "RETR":
            if self.fault == "open-error" or argument not in self.files:
                return _frame(1, b"450 Open error\r\n")
            body = self.files[argument]
            if self.fault == "corrupt-readback" and argument.startswith("/EXT0/QRFLECT"):
                body = body[:100] + bytes([body[100] ^ 0xff]) + body[101:]
            announced = len(body)
            if self.fault == "short-transfer":
                body = body[:1000]
            if self.fault == "long-transfer":
                body = body + b"extra"
            out = self._reply(f"150 data connection for RETR.({announced})")
            for index, offset in enumerate(range(0, len(body), 0x10000)):
                out += _frame(2, body[offset:offset + 0x10000], trailer=4 if self.fault == "mid-bad-frame" and index else 3)
            if self.fault == "mid-timeout":
                return out  # the camera goes quiet after the data it had; no closing reply
            return out + self._reply("250 RETR command success.")
        if verb == "DELE":
            if self.fault == "dele-odd":
                return self._reply("501 Syntax error in parameters or arguments.")
            if self.fault == "stuck-qrflect" and argument == "/EXT0/QRFLECT9":
                return self._reply("250 DELE command success.")
            if argument in self.files:
                self.free += len(self.files.pop(argument))
                return self._reply("250 DELE command success.")
            return self._reply("500 DELE command failure")
        if verb == "-STOR":
            size, _, path = argument.partition(" ")
            if self.fault == "stor-open-error":
                return _frame(1, b"450 Open error\r\n")
            if not (size.isdigit() and 1 <= len(size) <= 8 and int(size) > 0 and path.startswith("/EXT0/")):
                self.violations.append(f"bad -STOR arguments: {argument!r}")
                return self._reply("501 Syntax error in parameters or arguments.")
            if path in self.files:  # the firmware's open deletes any existing file first
                self.free += len(self.files.pop(path))
            self.files[path] = bytearray()
            self._upload = [path, int(size)]
            return self._reply("150 data connection for -STOR.")
        raise AssertionError(verb)

    def read(self) -> bytes:
        self._unconfigured("read")
        if self.fault == "empty-forever" or self._empties:
            self._empties = max(0, self._empties - 1)
            self.now += EMPTY_READ_COST_S
            return b""
        if not self._segments:
            raise TransportTimeout("simulated timeout: no reply pending")
        size = 3 if self.fault == "short-read" else 0x10400
        segment, total = self._segments[0]
        chunk = bytes(segment[:size])
        del segment[:size]
        if segment:
            return chunk
        self._segments.pop(0)
        # A transfer of an exact packet multiple ends only with a zero-length packet. Without one the read hangs.
        if total % PACKET == 0 and self.fault == "missing-zlp":
            raise TransportTimeout("simulated timeout: exact-multiple transfer without a zero-length packet")
        return chunk

    def close(self) -> None:
        if self._claimed:
            self._claimed = False
            self.events.append("release_interface(0)")
        self.events.append("dispose_resources")
        self._save()


class _Config:
    def __init__(self, value: int):
        self.bConfigurationValue = value


class _SimulatedDevice:
    """Stands in for a pyusb device during bring-up; records each request in the mock's event list."""

    def __init__(self, owner: MockTransport):
        self._owner = owner
        self._active = 2 if owner.fault == "already-config-2" else 0

    def get_active_configuration(self):
        self._owner.events.append("get_configuration")
        return _Config(self._active)

    def set_configuration(self, value: int) -> None:
        if self._owner.fault == "configure-fails":
            raise RuntimeError("simulated: SET_CONFIGURATION failed")
        self._owner.events.append(f"set_configuration({value})")
        if self._owner.fault == "config-sticks-at-2":
            self._active = 2
            return
        if self._owner.fault != "skip-configure":
            self._owner._configured = True
            self._active = value
        else:
            self._active = 1  # reports configured but the firmware stays silent: the bulk-before-config test

    def ctrl_transfer(self, *args) -> None:
        self._owner.events.append("control 0x41/0x22 wValue 3")

    def claim(self, _device, number: int) -> None:
        if self._owner.fault == "claim-fails":
            raise RuntimeError("simulated: claim_interface failed")
        self._owner.events.append(f"claim_interface({number})")
        self._owner._claimed = True
