"""Host-side glue for the Koma app's setup and Update Koma flows (tools/komaapp).

Nothing here adds a camera write. The card is written by the browser on the computer (a card reader), never over USB:
the bridge's USB write allowlist (komabridge.WRITE_ALLOWLIST) is untouched. What this module adds:

  GET  /komalog                 KOMALOG.TXT from the card root over USB (RETR, the same read path as GET /settings),
                                decoded: Koma's build id and boot stage; {"present": false} when the card has none
                                (a plain answer, not a 404: a camera without Koma has no log, and that is the result).
  GET  /install                 what this computer can put on a card: the Koma package (PACKAGE_* below: its release,
                                build id, the minimum Koma firmware generation it needs, the builds it is known to
                                replace, "ready" when it can be built now), and whether Ricoh's 2.40 update file is
                                here to build the Koma firmware and package from (tools/koma/loader.py).
  GET  /install/GRMOD.BIN       the package's bytes, rebuilt in memory from PACKAGE_DELTA and the stock file
                                (tools/koma/loader.apply_delta), refused unless they are the recorded build
                                (PACKAGE_SHA256); 409 no_stock without the stock file.
  GET  /install/KOMAINST.BIN    the internal-memory installer (INSTALLER below), refused unless it is the recorded
                                build. On a card of 2 GB or less it runs as GRMOD.BIN, copies KOMAPKG.BIN into
                                /IROM/GRMOD.BIN, checks the copy and logs IRODUMP.TXT: the existing card route of
                                docs/install.md "Internal memory", no new camera capability.
  GET  /install/QRFLECT3?image=koma|official
                                the update file for the card: the Koma 1.0 firmware (SD/SDHC/SDXC driver + /IROM fallback,
                                tag 240K1.0; built in memory from the stock file by tools/koma/loader.build_koma10, refused
                                unless its sha256 is loader.KOMA10_SHA256) or Ricoh's own 2.40 file (for Return to official
                                firmware). Never written to disk by the bridge.
  POST /install/stock           the user's own Ricoh 2.40 file (qrflect3, gr-240.exe or a zip holding qrflect3), checked by
                                sha256 and kept in memory for this run, when none was found on this computer.

Koma ships no Ricoh code: the package holds stock before-bytes, so it ships as a delta, and the stock file comes from
KOMA_FIRMWARE, the research checkout's copy (tools/koma/firmware.py DEFAULT_FIRMWARE) or the user. The flash itself
is Ricoh's own updater at a keyed power-on; the app walks the user through it (docs/install.md).
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent.parent
REPO = TOOLS.parent
KOMALOG = "/EXT0/KOMALOG.TXT"
# The Koma package the app installs: Koma 1.0, header KOMA V1.0.0 094F6569 G1, shipped as dist-release/
# koma-1.0.0.grmod.delta (`koma package-delta`). Swapping the package is these constants.
# PACKAGE_VERSION is the public release (docs/versioning.md); the app shows it, not the header string.
# PACKAGE_MIN_FIRMWARE is the internal minimum Koma firmware generation (the header's trailing " G<n>"): 1, every Koma
# firmware.
PACKAGE_NAME = "koma-1.0.0.grmod"
PACKAGE_SHA256 = "dd67a69ec0de60997cc741351261b611a90142e09b9b1815ea3f8a315a0840c6"   # dist-release/SHA256SUMS
PACKAGE_BYTES = 61784
PACKAGE_BUILD = "094F6569"
PACKAGE_DELTA = REPO / "dist-release" / "koma-1.0.0.grmod.delta"
PACKAGE_DELTA_SHA256 = "4dd819c100eab3f9c5d585768eb944da2f505ad84be8e403bb00d15b1b9f4f58"
# Builds this package is known to replace (build ids; the app calls each "an earlier Koma build"): pre-release
# packages (747F061F, 896AEBBC, 4113C2A7, D4208A4F, 64CE9496) and an unreleased 1.0 candidate (898B5C3D).
# The app also treats a camera whose firmware tag names an earlier build (240K2, 240K2.x) or an older release as older
# (tools/komaapp/src/lib/onboard.svelte.js classify), and offers no update to any other build.
SUPERSEDES = ("747F061F", "896AEBBC", "4113C2A7", "D4208A4F", "64CE9496", "898B5C3D")
PACKAGE_VERSION = "1.0.0"
PACKAGE_MIN_FIRMWARE = 1
PACKAGE_ROUTE = "/install/GRMOD.BIN"
# The /IROM installer (analysis/sdhc/diag/irodump.grmod: irominstall v2 + a dump step; Koma's own Thumb). The loader
# rules it applies allow package bodies up to 0x10000 B.
INSTALLER = REPO / "analysis" / "sdhc" / "diag" / "irodump.grmod"
INSTALLER_SHA256 = "081090c47e80e2f61155cf33b271575d709655297270c470b354c45ba8a4107b"
INSTALLER_ROUTE = "/install/KOMAINST.BIN"
STOCK_MAX = 16 << 20          # gr-240.exe is 1.2 MB, qrflect3 2.1 MB; anything near this is not a Ricoh file
KOMA_TAG = "240K1.0"          # the Koma 1.0 firmware's MakerNote tag (tools/koma/loader.KOMA10_TAG); the package
                              # stamps its own photo tag at boot (tools/koma/features/fwtag.py), also 240K1.0 for 1.0

_lock = threading.Lock()
_uploaded: dict = {"data": None}   # the stock qrflect3 bytes the user sent this run
_found: dict = {}                  # (path, size, mtime) -> verified stock bytes, so gr-240.exe is unpacked once
_built: dict = {}                  # (stock sha, delta sha, PACKAGE_SHA256) -> the rebuilt package


class InstallError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def _loader():
    import sys
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    from koma import loader  # noqa: WPS433  (tools/koma/loader.py: self-contained, needs no assembler)
    return loader


# ------------------------------------------------------------------------------------------------ KOMALOG.TXT

def decode_komalog(data: bytes) -> dict:
    """Build id and stage from the telemetry log (tools/koma/features/telemetry.py layout: 'KOMA TLM V1', one
    'NAME =XXXXXXXX' line per field, 'END'). Same reading as selftest.decode_komalog's fallback parser."""
    text = data.decode("ascii", "replace")
    fields = {}
    for line in text.splitlines():
        key = line[:5].strip()
        if len(line) >= 14 and line[5:6] == "=" and key.isalnum():
            value = line[6:14]
            try:
                fields[key] = None if value == "--------" else int(value, 16)
            except ValueError:
                fields[key] = None
    build = fields.get("BUILD")
    return {"bytes": len(data), "header_ok": text.startswith("KOMA TLM V1"),
            "build_id": None if build is None else f"{build:08X}", "stage": fields.get("STAGE")}


def get_komalog(link) -> dict:
    import komabridge as KB

    data = link.run(lambda s: KB.read_root_file(s, KOMALOG))
    if data is None:
        return {"name": "KOMALOG.TXT", "present": False, "build_id": None}
    return {"name": "KOMALOG.TXT", "present": True, **decode_komalog(data)}


# ------------------------------------------------------------------------------------------------ files for the card

def _stock() -> tuple[bytes | None, str | None]:
    """(verified stock qrflect3 bytes, where they came from) or (None, None)."""
    with _lock:
        if _uploaded["data"] is not None:
            return _uploaded["data"], "upload"
    loader = _loader()
    candidates = []
    env = os.environ.get("KOMA_FIRMWARE")
    if env:
        candidates.append((Path(env).expanduser(), "env"))
    candidates.append((REPO / "firmware" / "extracted" / "GR DIGITAL Update" / "Firmware rel 2.40" / "qrflect3", "repo"))
    for path, source in candidates:
        try:
            st = path.stat()
            if not path.is_file() or st.st_size > STOCK_MAX:
                continue
            key = (str(path), st.st_size, st.st_mtime_ns)
            with _lock:
                data = _found.get(key)
            if data is None:
                data = loader.read_stock(path)
                with _lock:
                    _found[key] = data
            return data, source
        except (OSError, loader.LoaderError):
            continue
    return None, None


def package_info() -> dict | None:
    """What the app needs to plan without the bytes; "ready" says whether the bytes can be built here now."""
    if not PACKAGE_DELTA.is_file():
        return None
    stock, _ = _stock()
    return {"route": PACKAGE_ROUTE, "name": PACKAGE_NAME, "bytes": PACKAGE_BYTES, "published_sha256": PACKAGE_SHA256,
            "build_id": PACKAGE_BUILD, "ready": stock is not None, "supersedes": list(SUPERSEDES),
            "version": PACKAGE_VERSION, "min_firmware_gen": PACKAGE_MIN_FIRMWARE}


def package_bytes() -> bytes:
    """The package for the card, rebuilt from the delta and the stock file, only when it is the recorded build."""
    if not PACKAGE_DELTA.is_file():
        raise InstallError(404, "This computer has no Koma package.", code="no_package")
    stock, _ = _stock()
    if stock is None:
        raise InstallError(409, "Ricoh's 2.40 update file is not on this computer.", code="no_stock")
    delta = PACKAGE_DELTA.read_bytes()
    key = (hashlib.sha256(stock).hexdigest(), hashlib.sha256(delta).hexdigest(), PACKAGE_SHA256)
    if key[1] != PACKAGE_DELTA_SHA256:
        raise InstallError(409, "The Koma package on this computer is not the recorded build.", code="package_changed")
    with _lock:
        data = _built.get(key)
    if data is None:
        loader = _loader()
        try:
            data = loader.apply_delta(stock, delta, PACKAGE_SHA256)
        except loader.LoaderError:
            raise InstallError(409, "The Koma package on this computer is not the recorded build.", code="package_changed")
        with _lock:
            _built[key] = data
    return data


def installer_info() -> dict | None:
    if not INSTALLER.is_file():
        return None
    data = INSTALLER.read_bytes()
    return {"route": INSTALLER_ROUTE, "name": INSTALLER.name, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "published_sha256": INSTALLER_SHA256}


def installer_bytes() -> bytes:
    """The /IROM installer for the card, only when it is the recorded build."""
    if not INSTALLER.is_file():
        raise InstallError(404, "This computer has no Koma installer.", code="no_installer")
    data = INSTALLER.read_bytes()
    if hashlib.sha256(data).hexdigest() != INSTALLER_SHA256:
        raise InstallError(409, "The Koma installer on this computer is not the recorded build.", code="installer_changed")
    return data


def install_info() -> dict:
    stock, source = _stock()
    loader = _loader()
    return {"package": package_info(), "installer": installer_info(),
            "firmware": {"stock": stock is not None, "source": source, "koma_tag": KOMA_TAG,
                         "koma_sha256": loader.KOMA10_SHA256, "official_sha256": loader.STOCK_SHA256}}


def firmware_image(kind: str) -> bytes:
    if kind not in ("koma", "official"):
        raise InstallError(400, "GET /install/QRFLECT3?image=koma or ?image=official.")
    stock, _ = _stock()
    if stock is None:
        raise InstallError(409, "Ricoh's 2.40 update file is not on this computer.", code="no_stock")
    if kind == "official":
        return stock
    loader = _loader()
    try:
        return loader.build_koma10(stock)  # refuses unless the result is the pinned Koma 1.0 image (KOMA10_SHA256)
    except loader.LoaderError as error:
        raise InstallError(500, f"The Koma firmware could not be built: {error}")


def take_stock(body: bytes) -> dict:
    """Verify the user's Ricoh file with loader.read_stock (qrflect3, gr-240.exe through bsdtar, or a zip) and keep the
    qrflect3 bytes in memory for this run."""
    if not body:
        raise InstallError(400, "Send the file as the request body.")
    loader = _loader()
    fd, name = tempfile.mkstemp(prefix="koma-stock-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        try:
            data = loader.read_stock(Path(name))
        except loader.LoaderError:
            raise InstallError(422, "This is not Ricoh's GR DIGITAL 2.40 update file.", code="not_stock")
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass
    with _lock:
        _uploaded["data"] = data
    return {"ok": True, "sha256": hashlib.sha256(data).hexdigest()}


def forget_upload() -> None:
    """Tests: drop the uploaded stock file."""
    with _lock:
        _uploaded["data"] = None
