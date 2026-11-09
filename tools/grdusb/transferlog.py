"""Timestamped log of every USB transfer, one JSON object per line, flushed as it happens so a crash keeps it."""
from __future__ import annotations

import datetime
import json
import os
import time

HEX_LIMIT = 256  # bytes of each transfer kept as hex; the length field always holds the full size


class TransferLog:
    def __init__(self, path: str, command: str, simulated: bool):
        self.path = path
        self._file = open(path, "x", encoding="utf-8")  # exclusive: never overwrites an earlier log
        self._seq = 0
        self.event("log_open", command=command, simulated=simulated)

    def event(self, kind: str, **fields) -> None:
        self._seq += 1
        record = {"seq": self._seq, "wall": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
                  "mono": round(time.monotonic(), 6), "event": kind, **fields}
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def transfer(self, kind: str, data: bytes, **fields) -> None:
        self.event(kind, bytes=len(data), hex=bytes(data[:HEX_LIMIT]).hex(), truncated=len(data) > HEX_LIMIT, **fields)

    def close(self) -> None:
        if not self._file.closed:
            self.event("log_close")
            self._file.close()


class LoggedTransport:
    """Wraps a transport and logs bring-up, every write and read (including empty reads), errors and close."""

    def __init__(self, inner, log: TransferLog):
        self._inner, self._log = inner, log
        if hasattr(inner, "clock"):
            self.clock = inner.clock

    def bring_up(self) -> None:
        self._log.event("bring_up_begin")
        try:
            self._inner.bring_up()
        except BaseException as error:
            self._log.event("bring_up_failed", error=f"{type(error).__name__}: {error}")
            raise
        self._log.event("bring_up_done")

    def write(self, data: bytes):
        try:
            written = self._inner.write(data)
        except BaseException as error:
            self._log.transfer("write_failed", data, error=f"{type(error).__name__}: {error}")
            raise
        self._log.transfer("write", data, accepted=written)
        return written

    def read(self) -> bytes:
        try:
            data = self._inner.read()
        except BaseException as error:
            self._log.event("read_failed", error=f"{type(error).__name__}: {error}")
            raise
        self._log.transfer("read", data)
        return data

    def close(self) -> None:
        try:
            self._inner.close()
        except BaseException as error:
            self._log.event("close_failed", error=f"{type(error).__name__}: {error}")
            raise
        self._log.event("close_done")
