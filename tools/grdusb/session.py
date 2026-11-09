"""Client side of the command sequences. Stops at the first off-spec reply and never retries or drains."""
from __future__ import annotations

import time

from .protocol import (CHANNEL_COMMAND, FrameReader, ProtocolError, encode_frame, fixed_command, parse_camera_id, parse_capacity,
                       parse_listing, parse_retrieve_size, parse_version, path_command, reply_code)
from .transport import TransportError


class CameraError(Exception):
    """The camera answered with a well-formed failure status."""


class SinkError(Exception):
    """The host could not store data the camera was sending. The transfer is abandoned mid-stream."""


class ShortWrite(TransportError):
    """A bulk write accepted fewer bytes than the frame holds."""


class Session:
    def __init__(self, transport):
        self._transport = transport
        self._frames = FrameReader(transport.read, getattr(transport, "clock", time.monotonic))
        self.requests_attempted = 0

    def _reply(self) -> str:
        frame = self._frames.next_frame()
        if frame.channel != CHANNEL_COMMAND:
            raise ProtocolError(f"expected a reply frame, got channel {frame.channel}")
        return frame.payload.decode("latin-1")

    def _ask(self, command: bytes) -> str:
        frame = encode_frame(CHANNEL_COMMAND, command)
        self.requests_attempted += 1  # counted before the write: a failed write may still have put bytes on the wire
        written = self._transport.write(frame)
        if written != len(frame):
            raise ShortWrite(f"bulk write accepted {written!r} of {len(frame)} bytes; treating as a failure")
        return self._reply()

    def version(self) -> str:
        """One -VER request, one reply. The model code in the reply identifies a GR DIGITAL."""
        return parse_version(self._ask(fixed_command("version")))

    def _simple(self, command: bytes) -> str:
        reply = self._ask(command)
        if reply_code(reply) != "200":
            raise CameraError(reply.strip())
        return reply.strip()

    def camera_id(self) -> str:
        return self._simple(fixed_command("camera_id"))

    def camera_id_value(self) -> str:
        """The bare CameraID= value from a -GCID reply."""
        return parse_camera_id(self.camera_id())

    def card_id(self) -> str:
        return self._simple(fixed_command("card_id"))

    def clock(self) -> str:
        return self._simple(fixed_command("clock"))

    def capacity(self, volume: str) -> tuple[str, int, int]:
        return parse_capacity(self._simple(path_command("capacity", volume)))

    def _data_until_reply(self, expected: int | None, sink) -> str:
        """Writes channel-2 payloads to sink until the closing reply. Returns that reply."""
        total = 0
        while True:
            frame = self._frames.next_frame()
            if frame.channel == CHANNEL_COMMAND:
                if expected is not None and total != expected:
                    raise ProtocolError(f"transfer ended at {total} of {expected} bytes")
                return frame.payload.decode("latin-1")
            total += len(frame.payload)
            if expected is not None and total > expected:
                raise ProtocolError(f"received {total} bytes, more than the announced {expected}")
            try:
                sink(frame.payload)
            except Exception as error:
                raise SinkError(f"could not store received data: {type(error).__name__}: {error}") from error

    def list_raw(self, path: str) -> bytes:
        reply = self._ask(path_command("list", path))
        if reply_code(reply) != "150":
            raise CameraError(reply.strip())
        chunks = bytearray()
        closing = self._data_until_reply(None, chunks.extend)
        if reply_code(closing) != "250":
            raise CameraError(closing.strip())
        return bytes(chunks)

    def list_directory(self, path: str):
        return parse_listing(self.list_raw(path))

    def retrieve(self, path: str, sink, allow_internal: bool = False) -> int:
        reply = self._ask(path_command("retrieve", path, allow_internal))
        if reply_code(reply) != "150":
            raise CameraError(reply.strip())
        size = parse_retrieve_size(reply)
        closing = self._data_until_reply(size, sink)
        if reply_code(closing) != "250":
            raise CameraError(closing.strip())
        return size
