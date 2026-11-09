"""The 2.40 firmware facts Koma builds against: payload bytes, loader ABI, ranges owned by the loader image.

Koma ships no Ricoh code. It reads the user's own stock 2.40 file, chosen in this order:
  1. `koma --firmware PATH` (or firmware.use(PATH))
  2. the KOMA_FIRMWARE environment variable
  3. firmware/ in this checkout (DEFAULT_FIRMWARE, see firmware/README.md)
PATH may be qrflect3 from Ricoh's 2.40 updater, gr-240.exe itself, a zip holding qrflect3, or the bare 2.40 payload
(qrflect3 minus its 512-byte header). Every form is verified by sha256. Builds need only the payload; `koma smoke`
needs the full qrflect3 (it boots the emulator from the update container).
"""
from __future__ import annotations

import functools
import hashlib
import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
DATA = PKG / "data"                    # vendored tables (tone curves, phase-A line set, derived GRD IV look values); no Ricoh bytes
ROOT = PKG.parents[1]                  # checkout root (tools/koma/..); only used for the default firmware location
ENV = "KOMA_FIRMWARE"
DEFAULT_FIRMWARE = (ROOT / "firmware/extracted/GR DIGITAL Update/Firmware rel 2.40/qrflect3",
                    ROOT / "firmware/derived/grd240-armbe.bin")
PAYLOAD_SHA256 = "60b0a387126347a37f7d2217bb0b36b5a70fc33ce28959a8244b07757b9887a4"
PAYLOAD_BYTES = 2_201_960
HEADER = 0x200

LOAD = 0x1C000
IMAGE_VERSION = 0x240          # service table +0x04
ABI = 1                        # service table +0x00
MAXLEN = 0x10000               # loader's body limit

# Service table offsets (module-loader README §1)
SVC_ABI, SVC_VERSION, SVC_ALLOC, SVC_FREE, SVC_POOL = 0x00, 0x04, 0x08, 0x0C, 0x10
SVC_IRQ_SET, SVC_SLEEP = 0x28, 0x2C

# Bytes no feature may write or depend on changing: the loader image's own edits, plus Koma's publish slot.
LOADER_CAVE = (0x57F74, 0x580CC)       # loader code + service table (loader image)
LOADER_HOOK = (0x281EE, 0x281F2)       # bl 0x282cc -> loader
PUBLISH = 0x58190                      # 8 dead bytes of FUN_00057f74 after the loader: 'KOMA' + manifest pointer
RESERVED = [(LOADER_CAVE, "module loader cave"), (LOADER_HOOK, "module loader hook"),
            ((PUBLISH, PUBLISH + 8), "Koma publish slot")]


class FirmwareError(Exception):
    pass


_explicit: Path | None = None


def use(path) -> None:
    """Select the stock firmware file for this process (the CLI's --firmware)."""
    global _explicit
    _explicit = Path(path).expanduser() if path else None
    payload.cache_clear()
    stock.cache_clear()


def source() -> Path:
    """The stock firmware file in use (see module doc for the order)."""
    if _explicit is not None:
        if not _explicit.is_file():
            raise FirmwareError(f"--firmware {_explicit}: no such file")
        return _explicit
    env = os.environ.get(ENV)
    if env:
        p = Path(env).expanduser()
        if not p.is_file():
            raise FirmwareError(f"{ENV}={env}: no such file")
        return p
    for p in DEFAULT_FIRMWARE:
        if p.is_file():
            return p
    raise FirmwareError(
        "no stock GR DIGITAL 2.40 firmware given. Koma ships no Ricoh code; point it at your own copy:\n"
        f"  koma --firmware PATH ...   or   export {ENV}=PATH\n"
        "PATH: qrflect3 from Ricoh's 2.40 updater, gr-240.exe, a zip holding qrflect3, or the 2.40 payload.")


@functools.lru_cache(maxsize=1)
def payload() -> bytes:
    """The verified 2.40 application payload (runtime image from LOAD)."""
    p = source()
    data = p.read_bytes()
    if hashlib.sha256(data).hexdigest() == PAYLOAD_SHA256:
        return data
    raw = stock()[HEADER:]
    if hashlib.sha256(raw).hexdigest() != PAYLOAD_SHA256:      # cannot happen for a verified qrflect3
        raise FirmwareError(f"{p}: payload sha256 does not match the 2.40 payload")
    return raw


@functools.lru_cache(maxsize=1)
def stock() -> bytes:
    """The verified stock 2.40 qrflect3 container (needed by `koma smoke`; builds only need payload())."""
    from .loader import LoaderError, read_stock
    p = source()
    head = p.read_bytes()
    if hashlib.sha256(head).hexdigest() == PAYLOAD_SHA256:
        raise FirmwareError(f"{p} is the bare 2.40 payload; this step needs the full qrflect3 "
                            "(or gr-240.exe / a zip holding it)")
    try:
        return read_stock(p)
    except LoaderError as e:
        raise FirmwareError(str(e)) from None


def loader_image() -> bytes:
    """The module-loader image (stock 2.40 + loader cave + hook, no Rev marker) the smoke boots, built in memory."""
    from .loader import build_module_loader
    return build_module_loader(stock())


def ram(addr: int, n: int) -> bytes:
    """Stock 2.40 bytes at runtime address addr (outside the loader cave these equal the loader image's)."""
    raw = payload()
    off = addr - LOAD
    if off < 0 or off + n > len(raw):
        raise ValueError(f"{addr:#x}+{n} is outside the 2.40 payload")
    return raw[off:off + n]


def in_payload(addr: int, n: int) -> bool:
    return LOAD <= addr and addr + n <= LOAD + len(payload())
