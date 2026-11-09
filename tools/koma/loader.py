"""Build the flash-1 loader image (2.40 qrflect3 + card-module loader + Rev024M marker) from the user's own stock file.

Self-contained port of the flash-1 package build: the 344-byte loader cave is embedded as bytes (assembled from
the module-loader source), so this needs no assembler. Nothing here touches a
device; it reads one input file and writes one output file.

Edits, runtime addresses (payload offset = runtime - 0x1C000, file offset = payload offset + 0x200):
  0x57f74  344 B  'bx lr; nop' guard + loader at 0x57f78 + '/ATA1/GRMOD.BIN' + 48-byte service table (Dcfs island cave)
  0x281ee    4 B  hook 'bl 0x282cc' -> 'bl 0x57f78' (the loader calls 0x282cc first and returns its r0)
  0x112578   9 B  MakerNote version format 'Rev%4.4x\\0' -> 'Rev024M\\0\\0' (header +0x12 stays 0240)
  header +0x30    container checksum, recomputed (0x346f08ed -> 0x346e94a7)
"""
from __future__ import annotations

import hashlib
import io
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

# Stock inputs. qrflect3 is inside Ricoh's 2.40 Windows updater gr-240.exe (an LHa self-extractor):
# 'GR DIGITAL Update/Firmware rel 2.40/qrflect3'.
STOCK_SHA256 = "de00e03d563fd1bdd0a2a925761b1a029993425049841119e3e83eef990c783b"
STOCK_BYTES = 2_202_472
UPDATER_SHA256 = "e9257cbd89cd3f07163ef8700a9143121ecb69ecee94c1c935c7b7486fafa555"
UPDATER_MEMBER = "GR DIGITAL Update/Firmware rel 2.40/qrflect3"
OUTPUT_SHA256 = "107cb2b6ea500e7e9397f62937af327d306fb10fba66495f69a61d433286c995"
# The plain module-loader image (cave + hook, no Rev024M marker): what `koma smoke` boots, built in memory.
MODULE_LOADER_SHA256 = "86405d7d0cb997073742f4fa1dd123820a4bf1806fa886ecfa7b9e601bcc34ec"
NEXT = "next: copy to the card root as QRFLECT3 and run the keyed power-on (docs/install.md)"

HEADER_SIZE = 0x200
MAGIC = b"R_MG1_REFLECTOR\0"          # stored XOR 0x80
LOAD = 0x1C000

LOADER_CAVE = bytes.fromhex(
    "477046c0b5f0b088f7d0f9a690064d3e240068282801d0072cfad204200af7d0fcb03401e7f5e060240043e4a03a2100"
    "f7fdff0c42a0d0f61c0646692214f7fef8372814d14a980049304288d1469801492f4288d1429d022d00d03f07a8d13d"
    "492b428dd83a980307c1d13742a8d2354828a905002a2300f107f81a2800d12d9f0507b8d12b00300039002af7fef810"
    "42a8d1240030a9072201f7fef8092800d11d0030f7fdff78200046692210f000f8210039002af000f81d99044288d109"
    "a0190039002a9b0319db3301f000f81b2800d10a0038f107f81be006e0020038f107f8160030f7fdff579806b008bcf0"
    "bc084718188a4291d204231f41d8c90818c0e7f847704718001bd3a847524d4400010000002bf81c2f415441312f4752"
    "4d4f442e42494e0000000001000002400015f0250015f085002bf81c00055dc10005602500055f0d000517dd0008ffc9"
    "0001c5b4000288f7")
LOADER_CAVE_SHA256 = "be6912ddccc5492173c368cf0222dd0b96a332172ca270b3516f437bf21d68f7"
CAVE_BEFORE_SHA256 = "d978cbe5af610de2e989bd806f924feac73c3f38508ba4c4549ffbd2fe2e5093"

# (runtime address, stock bytes or their sha256, new bytes, what)
EDITS = [
    (0x57F74, CAVE_BEFORE_SHA256, LOADER_CAVE, "card-module loader cave"),
    (0x281EE, bytes.fromhex("f000f86d"), bytes.fromhex("f02ffec3"), "hook bl 0x282cc -> bl 0x57f78"),
    (0x112578, b"Rev%4.4x\0", b"Rev024M\0\0", "Rev024M marker"),
]


class LoaderError(Exception):
    pass


def checksum(payload: bytes, load: int) -> int:
    """Container checksum (Thumb routine 0x62cf0): sum of byte * (2 + (load + i) % 6), mod 2**32."""
    return sum((2 + (load + s) % 6) * sum(payload[s::6]) for s in range(6)) & 0xFFFFFFFF


def _header(data: bytes) -> tuple[int, int]:
    if len(data) < HEADER_SIZE or bytes(v ^ 0x80 for v in data[:16]) != MAGIC:
        raise LoaderError("not a GR DIGITAL update file (reflector magic missing)")
    load, length, _entry, total, stored = struct.unpack_from(">5I", data, 0x20)
    if total != len(data) or length != len(data) - HEADER_SIZE:
        raise LoaderError("declared update-file size does not match the data")
    if checksum(data[HEADER_SIZE:], load) != stored:
        raise LoaderError("update-file checksum does not verify")
    return load, stored


def _refuse(what: str, sha: str) -> LoaderError:
    return LoaderError(f"{what} is not the stock GR DIGITAL 2.40 update file (sha256 {sha}).\n"
                       f"  expected qrflect3 sha256 {STOCK_SHA256} ({STOCK_BYTES:,} bytes)\n"
                       f"  or Ricoh's gr-240.exe sha256 {UPDATER_SHA256}, or a zip containing that qrflect3")


def _from_updater(path: Path) -> bytes:
    tar = shutil.which("bsdtar")
    if not tar:
        raise LoaderError("gr-240.exe needs bsdtar to unpack (LHa); install libarchive, or extract qrflect3 yourself "
                          f"('{UPDATER_MEMBER}') and pass that")
    r = subprocess.run([tar, "-xOf", str(path), UPDATER_MEMBER], capture_output=True, check=False)
    if r.returncode:
        raise LoaderError(f"bsdtar could not read {UPDATER_MEMBER} from {path}: {r.stderr.decode(errors='replace')}")
    return r.stdout


def read_stock(path: Path) -> bytes:
    """Return the verified stock 2.40 qrflect3 bytes from qrflect3 itself, Ricoh's gr-240.exe, or a zip holding it."""
    path = Path(path)
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if sha == UPDATER_SHA256:
        data = _from_updater(path)
        sha = hashlib.sha256(data).hexdigest()
        if sha != STOCK_SHA256:
            raise _refuse(f"{UPDATER_MEMBER} in {path}", sha)
        return data
    if sha != STOCK_SHA256 and zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.is_dir() or info.file_size != STOCK_BYTES:
                    continue
                member = z.read(info)
                if hashlib.sha256(member).hexdigest() == STOCK_SHA256:
                    return member
        raise _refuse(f"no member of {path}", "n/a")
    if sha != STOCK_SHA256:
        raise _refuse(str(path), sha)
    return data


def build(stock: bytes) -> bytes:
    """Apply the flash-1 edits to verified stock bytes; returns the image (sha256 OUTPUT_SHA256)."""
    return _apply(stock, EDITS, OUTPUT_SHA256)


def build_module_loader(stock: bytes) -> bytes:
    """Cave + hook only (no Rev024M marker): the module-loader image the emulator smoke boots (MODULE_LOADER_SHA256).
    Returned in memory; Koma never writes it to disk."""
    return _apply(stock, EDITS[:2], MODULE_LOADER_SHA256)


def _apply(stock: bytes, edits, expect_sha: str) -> bytes:
    if hashlib.sha256(stock).hexdigest() != STOCK_SHA256:
        raise _refuse("input", hashlib.sha256(stock).hexdigest())
    assert hashlib.sha256(LOADER_CAVE).hexdigest() == LOADER_CAVE_SHA256
    load, _ = _header(stock)
    if load != LOAD:
        raise LoaderError(f"unexpected load address {load:#x}")
    payload = bytearray(stock[HEADER_SIZE:])
    for addr, before, after, what in edits:
        off = addr - LOAD
        cur = bytes(payload[off:off + len(after)])
        ok = hashlib.sha256(cur).hexdigest() == before if isinstance(before, str) else cur == before
        if not ok:
            raise LoaderError(f"stock bytes at {addr:#x} ({what}) are not as expected")
        payload[off:off + len(after)] = after
    header = bytearray(stock[:HEADER_SIZE])
    struct.pack_into(">I", header, 0x30, checksum(bytes(payload), load))
    image = bytes(header) + bytes(payload)
    _header(image)
    sha = hashlib.sha256(image).hexdigest()
    if sha != expect_sha:
        raise LoaderError(f"built image sha256 {sha} != expected {expect_sha}; not writing it")
    return image


def make(src: Path, out: Path) -> str:
    """Read src, build, write out (refuses to replace a different existing file). Returns the output sha256."""
    image = build(read_stock(src))
    out = Path(out)
    if out.exists():
        if out.read_bytes() != image:
            raise LoaderError(f"{out} exists with different bytes; remove it or choose another name")
    else:
        with out.open("xb") as f:
            f.write(image)
    return hashlib.sha256(image).hexdigest()
