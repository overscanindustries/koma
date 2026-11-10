"""Photos import for the Koma app.

"Import new" copies the card's photos that were not imported yet into a folder on this computer, checks every copy and
remembers what it copied. Host side only: the camera sees the same reads as GET /photos and GET /photos/<F>/<N>
(-NLST of DCIM and its folders, -INFO, -FDAT, RETR). Nothing is written to the camera and the card root is never
listed. komabridge.py serves it as GET|POST|DELETE /photos/import and POST /photos/folder.

State lives on this computer, in the state directory: KOMA_STATE_DIR, else the platform's application-data folder. A
simulated camera gets a temporary one, so a mock never writes into the user's Pictures folder.
  photos.json     {"folder": "~/Pictures/Koma"}  the import folder (default ~/Pictures/Koma)
  imported.json   {"version": 1, "photos": {key: {"file": ..., "sha256": ..., "at": ...}}}
A photo is known by its name, size and capture time (photo_key), never by its name alone: file numbers restart on a new
card. Each copy lands in <folder>/YYYY/MM/DD/<name>. It is written to a .part file, flushed, renamed, read back and
compared (SHA-256) with the bytes read from the card before it counts as imported. A byte-identical file already there
counts as imported. A different file of the same name is never overwritten: the copy takes the next free name
(R0015710-2.JPG) and the job lists it. The import folder itself is created only when its parent exists, and it is
checked again before every file, with the day folders made one level at a time below it, so an unplugged drive is
reported instead of being recreated on the system disk.

Job ids count up within one bridge process and start again with the next one; `run` (in every answer) names the
process, so a page left open across a restart can tell its old job from a new one.
"""
from __future__ import annotations

import datetime as _dt
import errno
import hashlib
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path

LEDGER_VERSION = 1
STATE_FILE, LEDGER_FILE = "photos.json", "imported.json"

# Plain messages for API users and logs; the app words each `code` itself (tools/komaapp/src/lib/words.js, PHOTOS).
FOLDER_EMPTY = "Type the full path of a folder, such as ~/Pictures/Koma."
FOLDER_RELATIVE = "Type the full path of a folder, such as ~/Pictures/Koma."
FOLDER_IS_FILE = "That is a file, not a folder."
FOLDER_NO_PARENT = "The folder above it is not there. Connect the drive or choose another folder."
FOLDER_CANNOT_CREATE = "Koma cannot create this folder."
FOLDER_CANNOT_WRITE = "Koma cannot write to this folder."
FOLDER_HERE_ONLY = "Choose the import folder on the computer that runs Koma."
IMPORT_RUNNING = "An import is already running."
REMOTE_ON = "Remote mode is on. Leave Remote before reading photos."


class Refused(Exception):
    """A request the importer turns down: HTTP status, plain message, and the code the app words."""

    def __init__(self, status: int, message: str, code: str):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


class VerifyError(Exception):
    """The file read back from this computer differs from the bytes read from the card."""


def photo_key(p: dict) -> str:
    """A photo's identity: name, size and capture time as GET /photos reports them."""
    return f"{p['name']}|{p['size']}|{p['date']}"


def default_state_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Koma"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Koma"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "koma"


def jpeg_complete(data: bytes) -> bool:
    """A whole JPEG: SOI first and EOI last, allowing the 0xFF / 0x00 padding the GR DIGITAL writes after EOI (frames
    end ffd9, ffd9ff or ffd9ffff). A file read while the camera still writes it fails."""
    if data[:2] != b"\xff\xd8":
        return False
    t = data.rstrip(b"\x00\xff")
    return len(t) >= 4 and t[-1] == 0xD9 and t[-2] == 0xFF


def capture_time(date: str) -> _dt.datetime | None:
    """GET /photos `date` ("YYYY-MM-DD HH:MM:SS", from -FDAT or the directory time) as a naive local time."""
    try:
        return _dt.datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def day_folder(folder: Path, date: str) -> Path:
    d = capture_time(date)
    return folder / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}" if d else folder / "undated"


def display_path(path: Path | str, home: Path) -> str:
    """The path as the app shows it: ~/... under the home folder (macOS and Linux), the full path otherwise."""
    path = Path(path)
    if os.name != "nt":
        try:
            rel = path.relative_to(home)
        except ValueError:
            return str(path)
        return "~" if str(rel) == "." else "~/" + rel.as_posix()
    return str(path)


def os_reason(error: OSError) -> str:
    """The `why` code for a failed write, from the OS error."""
    if error.errno == errno.ENOSPC:
        return "disk_full"
    if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "no_write"
    if error.errno == errno.ENOENT:
        return "folder_missing"
    return "write"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write_json(path: Path, obj) -> None:
    """Atomic: a temporary file next to it, flushed, then renamed over it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def make_below(folder: Path, dest_dir: Path) -> None:
    """Make dest_dir and the folders between it and the import folder, one level at a time. Never the import folder or
    anything above it: when the import folder has gone (its drive unplugged), this fails with ENOENT."""
    d = folder
    for part in dest_dir.relative_to(folder).parts:
        d = d / part
        try:
            d.mkdir()
        except FileExistsError:
            if not d.is_dir():
                raise


def store(data: bytes, dest_dir: Path, name: str, mtime: float | None) -> tuple[Path, str, str]:
    """Write one photo into dest_dir (which exists) and read it back. Returns (file, outcome, sha256), outcome "copied",
    "renamed" (the name was taken by a different file) or "already" (a byte-identical file is there; nothing written).
    Raises OSError on a failed write and VerifyError when the read-back differs (the bad copy is removed)."""
    digest = hashlib.sha256(data).hexdigest()
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        cand = dest_dir / (name if n == 1 else f"{stem}-{n}{ext}")
        if not cand.exists():
            break
        if cand.is_file() and cand.stat().st_size == len(data) and _sha256_file(cand) == digest:
            return cand, "already", digest
        n += 1
    tmp = dest_dir / f".{cand.name}.{os.getpid()}.part"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mtime is not None:
            os.utime(tmp, (mtime, mtime))
        os.replace(tmp, cand)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if _sha256_file(cand) != digest:
        try:
            cand.unlink()
        except OSError:
            pass
        raise VerifyError(f"{cand} read back different from the card")
    return cand, ("renamed" if n > 1 else "copied"), digest


class Importer:
    """The import folder, the list of imported photos and one import job at a time.

    list_photos() -> GET /photos records; read(rel_path, size) -> the file's bytes from the card; blocked() -> True while
    remote control is on (a card read then drops live view on a real camera). Errors from them carry the bridge's HTTP
    `status` (503 camera lost, 502 the camera refused a read, 409 remote control on)."""

    def __init__(self, list_photos, read, blocked, simulated: bool = False, state_dir=None, folder=None, home=None):
        self.list_photos, self.read, self.blocked = list_photos, read, blocked
        self.home = Path(home) if home else Path.home()
        if state_dir is None:
            env = os.environ.get("KOMA_STATE_DIR")
            state_dir = Path(env).expanduser() if env else (
                Path(tempfile.gettempdir()) / f"komabridge-sim-{os.getpid()}" if simulated else default_state_dir())
        self.state_dir = Path(state_dir)
        self.default_folder = Path(folder) if folder else (
            self.state_dir / "Pictures" / "Koma" if simulated else self.home / "Pictures" / "Koma")
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread = None
        self.run = secrets.token_hex(4)  # this process: job ids below start again with the next one
        self.seq = 0
        self.job = None
        self.folder = self._load_folder()
        self.ledger = self._load_ledger()

    # ---------------------------------------------------------------- state files
    def _load_folder(self) -> Path:
        try:
            saved = json.loads((self.state_dir / STATE_FILE).read_text("utf-8")).get("folder")
            if isinstance(saved, str) and Path(saved).is_absolute():
                return Path(saved)
        except (OSError, ValueError, AttributeError):
            pass
        return self.default_folder

    def _load_ledger(self) -> dict:
        path = self.state_dir / LEDGER_FILE
        try:
            raw = json.loads(path.read_text("utf-8"))
            photos = raw.get("photos") if isinstance(raw, dict) else None
            if isinstance(photos, dict):
                return {str(k): v for k, v in photos.items() if isinstance(v, dict)}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, UnicodeDecodeError):
            pass
        # unreadable: kept for a person to look at; a new list starts (a re-import then finds the same files in place)
        try:
            os.replace(path, path.with_name(LEDGER_FILE + ".bad"))
        except OSError:
            pass
        return {}

    # ---------------------------------------------------------------- API
    def snapshot(self, keys: bool = True) -> dict:
        with self.lock:
            out = {"folder": str(self.folder), "display": display_path(self.folder, self.home),
                   "sep": "\\" if os.name == "nt" else "/", "default": self.folder == self.default_folder,
                   "run": self.run, "job": self._job_view()}
            if keys:
                out["imported"] = {k: display_path(v.get("file", ""), self.home) for k, v in sorted(self.ledger.items())}
            return out

    def set_folder(self, text) -> dict:
        t = text.strip() if isinstance(text, str) else ""
        if not t:
            raise Refused(400, FOLDER_EMPTY, "folder_empty")
        if t == "~" or t.startswith(("~/", "~" + os.sep)):
            path = self.home / t[2:] if len(t) > 1 else self.home
        else:
            path = Path(t)
        if not path.is_absolute():
            raise Refused(400, FOLDER_RELATIVE, "folder_relative")
        path = Path(os.path.normpath(path))
        if path.exists() and not path.is_dir():
            raise Refused(400, FOLDER_IS_FILE, "folder_is_file")
        if not path.exists():
            if not path.parent.is_dir():
                raise Refused(400, FOLDER_NO_PARENT, "folder_no_parent")
            try:
                path.mkdir()
            except OSError:
                raise Refused(400, FOLDER_CANNOT_CREATE, "folder_cannot_create")
        try:
            fd, probe = tempfile.mkstemp(prefix=".koma-", dir=path)
            os.close(fd)
            os.unlink(probe)
        except OSError:
            raise Refused(400, FOLDER_CANNOT_WRITE, "folder_cannot_write")
        with self.lock:
            self.folder = path
        try:
            _write_json(self.state_dir / STATE_FILE, {"folder": str(path)})
        except OSError:
            raise Refused(500, "Koma could not save the folder choice.", "state")
        return self.snapshot()

    def start(self) -> dict:
        with self.lock:
            if self.job and self.job["state"] == "running":
                raise Refused(409, IMPORT_RUNNING, "import_running")
            self.seq += 1
            self.cancel.clear()
            folder = self.folder
            self.job = {"id": self.seq, "state": "running", "phase": "reading", "display": display_path(folder, self.home),
                        "total": 0, "done": 0, "copied": 0, "already": 0, "bytes_total": 0, "bytes_done": 0,
                        "renamed": [], "skipped": [], "keys": [], "current": None, "stop": None,
                        "_t0": time.monotonic(), "_t1": None}
            self.thread = threading.Thread(target=self._run, args=(folder,), daemon=True, name="photo-import")
            self.thread.start()
        return self.snapshot(keys=False)

    def stop(self) -> dict:
        """Cancel between files: the file being copied is finished and checked first."""
        self.cancel.set()
        return self.snapshot(keys=False)

    def wait(self, timeout: float | None = None) -> None:
        t = self.thread
        if t is not None:
            t.join(timeout)

    # ---------------------------------------------------------------- the job
    def _job_view(self):
        if not self.job:
            return None
        j = {k: v for k, v in self.job.items() if not k.startswith("_")}
        j["renamed"], j["skipped"], j["keys"] = list(j["renamed"]), list(j["skipped"]), list(j["keys"])
        j["elapsed_s"] = round((self.job["_t1"] or time.monotonic()) - self.job["_t0"], 1)
        return j

    def _end(self, state: str, why: str | None = None, detail: str | None = None) -> None:
        with self.lock:
            self.job.update(state=state, phase=None, current=None, _t1=time.monotonic(),
                            stop={"why": why, "detail": detail} if why else None)

    def _run(self, folder: Path) -> None:
        try:
            self._import(folder)
        except Exception as error:  # noqa: BLE001  (a job never stays "running")
            self._end("failed", "error", f"{type(error).__name__}: {error}")

    def _ready(self, folder: Path):
        """(why, detail) when the import folder cannot take files, else None. The folder itself is made only when the
        folder above it exists (an unplugged drive is never recreated on the system disk)."""
        if folder.is_dir():
            return None
        if folder.exists():
            return "folder_is_file", str(folder)
        if not folder.parent.is_dir() and folder != self.default_folder:  # the default sits in the home folder
            return "folder_missing", str(folder)
        try:
            folder.mkdir(parents=True)
        except OSError as error:
            return os_reason(error), str(error)
        return None

    def _import(self, folder: Path) -> None:
        if self.blocked():
            return self._end("stopped", "remote_on", REMOTE_ON)
        problem = self._ready(folder)
        if problem:
            return self._end("failed", *problem)
        try:
            photos = self.list_photos()
        except Exception as error:  # noqa: BLE001  (the bridge's own error, with its HTTP status)
            status = getattr(error, "status", None)
            return self._end("stopped" if status == 409 else "failed",
                             "remote_on" if status == 409 else "camera", getattr(error, "message", str(error)))
        with self.lock:
            known = set(self.ledger)
        # Newest first, the contact sheet's order (GET /photos), so each frame turns IMPORTED where the eye already is
        # and a stopped import has the newest frames. The newest may still be being written: it is checked and skipped
        # like any other (jpeg_complete), and the next import takes it.
        new = sorted((p for p in photos if photo_key(p) not in known), key=lambda p: (p["date"], p["path"]), reverse=True)
        with self.lock:
            self.job.update(phase="copying", total=len(new), bytes_total=sum(p["size"] for p in new))
        for p in new:
            if self.cancel.is_set():
                return self._end("stopped", "cancelled")
            if self.blocked():
                return self._end("stopped", "remote_on", REMOTE_ON)
            if not folder.is_dir():  # its drive went away since the job began
                return self._end("failed", "folder_missing", str(folder))
            with self.lock:
                self.job["current"] = p["name"]
            try:
                data = self.read(p["path"], p["size"])
            except Exception as error:  # noqa: BLE001
                status = getattr(error, "status", None)
                if status == 502:  # the camera refused this one file (gone since the listing): the rest go on
                    self._skip(p, "unreadable")
                    continue
                return self._end("stopped" if status == 409 else "failed",
                                 "remote_on" if status == 409 else "camera", getattr(error, "message", str(error)))
            if len(data) != p["size"] or not jpeg_complete(data):
                self._skip(p, "incomplete")  # still being written, or changed since the listing: the next import
                continue
            when = capture_time(p["date"])
            dest = day_folder(folder, p["date"])
            try:
                make_below(folder, dest)
                final, outcome, digest = store(data, dest, p["name"], time.mktime(when.timetuple()) if when else None)
            except VerifyError as error:
                return self._end("failed", "verify", str(error))
            except OSError as error:
                return self._end("failed", os_reason(error), str(error))
            key = photo_key(p)
            with self.lock:
                self.ledger[key] = {"file": str(final), "sha256": digest,
                                    "at": _dt.datetime.now().isoformat(timespec="seconds")}
                ledger = dict(self.ledger)
            try:
                _write_json(self.state_dir / LEDGER_FILE, {"version": LEDGER_VERSION, "photos": ledger})
            except OSError as error:
                return self._end("failed", "state", str(error))
            with self.lock:
                j = self.job
                j["done"] += 1
                j["already" if outcome == "already" else "copied"] += 1
                j["bytes_done"] += p["size"]
                j["keys"].append(key)
                if outcome == "renamed":
                    j["renamed"].append({"name": p["name"], "saved_as": final.name,
                                         "file": display_path(final, self.home)})
        self._end("done")

    def _skip(self, p: dict, why: str) -> None:
        with self.lock:
            self.job["done"] += 1
            self.job["skipped"].append({"name": p["name"], "why": why})
