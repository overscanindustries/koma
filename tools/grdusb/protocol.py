"""Frame format and the closed list of commands this tool can send. Spec: research/usb-protocol.md."""
from __future__ import annotations

import re
import struct
import time
from dataclasses import dataclass

from .transport import TransportTimeout

SOF = 0x01
ETX = 0x03
CHANNEL_COMMAND = 1
CHANNEL_DATA = 2
HEADER_SIZE = 8
MAX_REPLY_PAYLOAD = 0x100
MAX_DATA_PAYLOAD = 0x10000
MAX_PATH = 255
EMPTY_READ_DEADLINE_S = 6.0
MODEL_REPLY_PREFIX = "200 9.17 "


class ProtocolError(Exception):
    """The camera's bytes do not match the spec. The session must not continue."""


class CommandRefused(Exception):
    """The tool will not build this command."""


class VersionRefused(CommandRefused):
    """The camera's -VER or -GCID reply did not match what the operator required. Nothing further is sent."""


# Every command this tool can emit. A verb is here only if research/usb-protocol.md classes it as free of
# persistent effects. Anything that changes state is absent on purpose, with no flag to enable it.
_FIXED = {"version": "-VER", "camera_id": "-GCID", "card_id": "-GSID", "clock": "-TIME"}
_WITH_PATH = {"capacity": "-DCAP", "list": "-NLST", "retrieve": "RETR"}
_VOLUMES = ("/EXT0", "/IROM")


def _check_path(path: str, *, allow_root: bool) -> str:
    if allow_root and path == "/":
        return path
    if not (1 <= len(path) <= MAX_PATH):
        raise CommandRefused(f"path length {len(path)} out of range")
    if not path.isascii() or any(ch < " " or ch in ", \x7f" for ch in path):
        raise CommandRefused(f"path has a character the command parser treats as syntax: {path!r}")
    if not any(path == volume or path.startswith(volume + "/") for volume in _VOLUMES):
        raise CommandRefused(f"path must be under {' or '.join(_VOLUMES)}: {path!r}")
    if any(part in ("", ".", "..") for part in path.split("/")[1:]):
        raise CommandRefused(f"path has an empty or relative component: {path!r}")
    return path


def fixed_command(name: str) -> bytes:
    if name not in _FIXED:
        raise CommandRefused(f"not an allowed command: {name!r}")
    return f"{_FIXED[name]}\r\n".encode("ascii")


IROM_RETR_REFUSAL = ("get refuses /IROM paths: closing a file whose metadata is inconsistent can make the camera's "
                     "filesystem write a repair, and internal memory is not expendable (derivation, "
                     "research/usb-protocol.md note 3 on RETR). To read /IROM anyway, both --allow-internal and at least one "
                     "--require-version VV.VV are needed. Nothing was sent")


def path_command(name: str, path: str, allow_internal: bool = False) -> bytes:
    if name not in _WITH_PATH:
        raise CommandRefused(f"not an allowed command: {name!r}")
    if name == "retrieve" and not allow_internal and (path.upper() == "/IROM" or path.upper().startswith("/IROM/")):
        raise CommandRefused(IROM_RETR_REFUSAL)
    if name == "capacity" and path not in _VOLUMES:
        raise CommandRefused(f"capacity takes a volume name, not {path!r}")
    _check_path(path, allow_root=name == "list")
    return f"{_WITH_PATH[name]} {path}\r\n".encode("ascii")


def encode_frame(channel: int, payload: bytes) -> bytes:
    """Host to camera. Reserved bytes are zero; the padded frame length is a multiple of four."""
    if channel != CHANNEL_COMMAND:
        raise CommandRefused("this tool sends command frames only")
    if len(payload) > MAX_REPLY_PAYLOAD:
        raise CommandRefused("command too long")
    frame = struct.pack("<BBHI", SOF, channel, 0, len(payload)) + payload + bytes([ETX])
    return frame + bytes(-len(frame) & 3)


@dataclass(frozen=True)
class Frame:
    channel: int
    payload: bytes


class FrameReader:
    """Reads one camera-to-host frame at a time and rejects anything off-spec without resynchronising."""

    def __init__(self, read_chunk, clock=time.monotonic, deadline_s: float = EMPTY_READ_DEADLINE_S):
        self._read_chunk = read_chunk
        self._clock = clock
        self._deadline_s = deadline_s
        self._buffer = bytearray()

    def _need(self, count: int) -> None:
        """Empty reads count as nothing received (as Ricoh's client does); only a quiet deadline fails."""
        started = self._clock()
        while len(self._buffer) < count:
            chunk = self._read_chunk()
            if chunk:
                self._buffer += chunk
                started = self._clock()
            elif self._clock() - started >= self._deadline_s:
                raise TransportTimeout(f"only empty reads for {self._deadline_s:g} s")

    def next_frame(self) -> Frame:
        self._need(HEADER_SIZE)
        sof, channel, reserved, length = struct.unpack_from("<BBHI", self._buffer, 0)
        if sof != SOF:
            raise ProtocolError(f"bad start byte {sof:#04x}")
        if reserved != 0xffff:
            raise ProtocolError(f"bad reserved field {reserved:#06x}")
        limit = {CHANNEL_COMMAND: MAX_REPLY_PAYLOAD, CHANNEL_DATA: MAX_DATA_PAYLOAD}.get(channel)
        if limit is None:
            raise ProtocolError(f"unknown channel {channel}")
        if length > limit:
            raise ProtocolError(f"length {length} over the {limit}-byte limit for channel {channel}")
        total = (HEADER_SIZE + length + 1 + 3) & ~3
        self._need(total)
        if self._buffer[HEADER_SIZE + length] != ETX:
            raise ProtocolError(f"bad trailer {self._buffer[HEADER_SIZE + length]:#04x}")
        payload = bytes(self._buffer[HEADER_SIZE:HEADER_SIZE + length])
        del self._buffer[:total]
        return Frame(channel, payload)


def reply_code(reply: str) -> str:
    if not re.match(r"^\d{3}", reply):
        raise ProtocolError(f"reply has no status code: {reply!r}")
    return reply[:3]


def parse_version(reply: str) -> str:
    match = re.fullmatch(r"200 9\.17 (\d\d\.\d\d)", reply.rstrip("\r\n"))
    if not match:
        raise ProtocolError(f"not a GR DIGITAL version reply: {reply!r}")
    return match.group(1)


def parse_camera_id(reply: str) -> str:
    match = re.fullmatch(r"200 CameraID=(\S+) for -GCID\.", reply.rstrip("\r\n"))
    if not match:
        raise ProtocolError(f"not a camera id reply: {reply!r}")
    return match.group(1)


def parse_capacity(reply: str) -> tuple[str, int, int]:
    match = re.fullmatch(r"200 (/\w+) capacity (\d+) byte,free (\d+) byte\.", reply.rstrip("\r\n"))
    if not match:
        raise ProtocolError(f"unexpected capacity reply: {reply!r}")
    return match.group(1), int(match.group(2)), int(match.group(3))


def parse_retrieve_size(reply: str) -> int:
    match = re.fullmatch(r"150 data connection for RETR\.\((\d+)\)", reply.rstrip("\r\n"))
    if not match:
        raise ProtocolError(f"unexpected retrieve reply: {reply!r}")
    return int(match.group(1))


@dataclass(frozen=True)
class DirEntry:
    name: str
    is_dir: bool
    size: int
    modified: str


def parse_listing(data: bytes) -> list[DirEntry]:
    """Records are 32 bytes: FAT layout, except the camera pads names with NUL and swaps the size to big-endian."""
    if len(data) % 32:
        raise ProtocolError(f"listing length {len(data)} is not a multiple of 32")
    entries = []
    for offset in range(0, len(data), 32):
        record = data[offset:offset + 32]
        stem = record[:8].rstrip(b"\0 ").decode("latin-1")
        suffix = record[8:11].rstrip(b"\0 ").decode("latin-1")
        name = f"{stem}.{suffix}" if suffix else stem
        if not name or name in (".", ".."):
            continue
        time_word, date_word = struct.unpack_from("<HH", record, 0x16)
        modified = (f"{1980 + (date_word >> 9):04d}-{(date_word >> 5) & 15:02d}-{date_word & 31:02d} "
                    f"{time_word >> 11:02d}:{(time_word >> 5) & 63:02d}:{(time_word & 31) * 2:02d}")
        entries.append(DirEntry(name, bool(record[11] & 0x10), struct.unpack_from(">I", record, 0x1c)[0], modified))
    return entries
