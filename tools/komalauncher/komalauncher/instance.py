"""One launcher per user and mode. The lock is an OS file lock (flock, or msvcrt on Windows), so it goes away with
the process, however the process ends; there is no stale lock to clean up. A second launch reads launcher.json,
which the running launcher writes on every phase change, opens the app in the browser and exits."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class InstanceLock:
    def __init__(self, data: Path):
        self.path = data / "launcher.lock"
        self._fd: int | None = None

    def acquire(self) -> bool:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self):
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(self._fd)
        self._fd = None


def read_state(data: Path) -> dict | None:
    try:
        state = json.loads((data / "launcher.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def wait_running(data: Path, seconds: float = 10.0) -> dict | None:
    """The running launcher's state once its phase is 'running', or its last state after the wait."""
    end = time.monotonic() + seconds
    state = None
    while time.monotonic() < end:
        state = read_state(data)
        if state and state.get("phase") in ("running", "port_in_use", "failed"):
            return state
        time.sleep(0.2)
    return state
