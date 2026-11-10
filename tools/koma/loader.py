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


# ---------------------------------------------------------------- Koma 1.0 firmware (SDHC driver image, tag 240K1.0)
# The Koma firmware the app installs since Koma 1.0: flash-1 above plus the SD 2.0/SDHC/SDXC driver and the /IROM loader
# fallback, MakerNote tag '240K1.0' (docs/versioning.md). Stored as a delta against the
# stock payload: Koma's own bytes literally, relocated stock code as (stock address, length) copies, so no Ricoh bytes
# ship here. Public file name koma-firmware-1.0.bin, copied to the card as QRFLECT3.
KOMA10_TAG = "240K1.0"
# BEGIN KOMA10 DELTA (generated by analysis/sdhc/driver/make_delta.py; do not edit by hand)
KOMA10_SHA256 = "ab52104c4762ba13ab3081eba51804e480904b2ea9cbd6abe00d421e4c09649e"   # analysis/sdhc/driver/build.py, tag 240K1.0
# 110 ops: 1776 literal Koma bytes, 644 bytes copied from stock (generated by make_delta.py)
KOMA10_DELTA = [
    (0x281EF, bytes.fromhex("2ffec3")),
    (0x5087A, bytes.fromhex("e0")),
    (0x508B7, bytes.fromhex("06fdb4")),
    (0x508DF, bytes.fromhex("06fda0")),
    (0x50936, bytes.fromhex("f007f88e")),
    (0x50ABF, bytes.fromhex("06fcb3")),
    (0x50AE7, bytes.fromhex("06fc9f")),
    (0x50B36, bytes.fromhex("f006ff8e")),
    (0x57274, bytes.fromhex("b5f01c07a4b020006020606060a060e061201c38f000fb162800d13968382802d13521002000f000fb302800d12f2601"
                "22002100200af7cffb25f000fb2d2801d0023e01d5f4e02226004d8d21002037f7cff8ac2800d12b1c292000f7cffa64"
                "68280dc0d22449872069f000fb222800d11e1d292003f000fb2668682800da0a21010040d50021026021200160382000"
                "bcf0bc084718250022002100200af7cffaf1350142b5d9f74d7536012e18d9cd200043c0e7ecb570466a21002000f7cf"
                "fae13001d0264c704d70200063a063e065a065e0486e60a0200060e0486d6020f7f9fc991c066828496b4208d1120840"
                "d205f7f9fc901b802864d9f4e00a6de00400d10769a005000d0049614288d1012001e0002000bc70bc084718b510f072"
                "fca94b5e68d802000f80a46768212902d0062801d11f4a5a68122a01d11be0172801d115685800800a8030010d82d101"
                "0280e001200043c060a060e04a514290d9001c1002406198200102406158e002228043116021bc10bc084718b510f000"
                "faad2800d110a450682122804211d1062902d0094a4468120a5260e2e00449422200600a60e243d0bc10bc084718b411"
                "a041e001b411a042a44368242c02d100300468004684a44068e442a1d2041a6442a2d801bc114760bc11200043c04770"
                "b402a139482968000980d201200160486848bc022800d001200043c04770a132680a2a02d1016889e002492768090a49"
                "4672320247102800d00027102c01d115a02968012902d10269012900d00d690268c01c31318069cb2a00d1000a5b6b4a"
                "4282d8021a804283d90027104770236c43434916185b781b2801db0ba11a68092902d103a11869092900d00549108809"
                "428bd2011c1847702410480e470046c0001fb390401000001204400012044218000001aa00000408007f0000002dea68"
                "001bd210007fffff002dea80002dfc400019fe0e000b29c3000508f9000575b500050afd00057741")),
    (0x5753C, (0x8234C, 28)),
    (0x575B4, (0x508F8, 43)),
    (0x575DF, bytes.fromhex("cefeb1")),
    (0x575E2, (0x50926, 13)),
    (0x575EF, bytes.fromhex("cefec9f000fa30")),
    (0x575F6, (0x5093A, 7)),
    (0x575FD, bytes.fromhex("2dfb16")),
    (0x57600, (0x50944, 5)),
    (0x57605, bytes.fromhex("daf8fe")),
    (0x57608, (0x5094C, 8)),
    (0x57610, bytes.fromhex("0024")),
    (0x57612, (0x50956, 23)),
    (0x57629, bytes.fromhex("72fc12")),
    (0x5762C, (0x50970, 11)),
    (0x57637, bytes.fromhex("2dfaf9")),
    (0x5763A, (0x5097E, 7)),
    (0x57641, bytes.fromhex("01")),
    (0x57642, (0x50986, 4)),
    (0x57646, bytes.fromhex("46c0300146c0")),
    (0x5764C, (0x50990, 17)),
    (0x5765D, bytes.fromhex("72fc29")),
    (0x57660, (0x509A4, 13)),
    (0x5766D, bytes.fromhex("2dfade")),
    (0x57670, (0x509B4, 5)),
    (0x57675, bytes.fromhex("daf8c6")),
    (0x57678, (0x509BC, 8)),
    (0x57680, bytes.fromhex("18e0025b18f1f072fbe3")),
    (0x5768A, (0x509CE, 9)),
    (0x57693, bytes.fromhex("2dfacb")),
    (0x57696, (0x509DA, 27)),
    (0x576B1, bytes.fromhex("72fbce")),
    (0x576B4, (0x509F8, 11)),
    (0x576BF, bytes.fromhex("2dfab5")),
    (0x576C2, (0x50A06, 7)),
    (0x576C9, bytes.fromhex("01")),
    (0x576CA, (0x50A0E, 4)),
    (0x576CE, bytes.fromhex("46c0300146c0")),
    (0x576D4, (0x50A18, 23)),
    (0x576EB, bytes.fromhex("72fbe2")),
    (0x576EE, (0x50A32, 11)),
    (0x576F9, bytes.fromhex("2dfa98")),
    (0x576FC, (0x50A40, 5)),
    (0x57701, bytes.fromhex("daf880")),
    (0x57704, (0x50A48, 13)),
    (0x57711, bytes.fromhex("cefe18")),
    (0x57714, (0x50A58, 47)),
    (0x57743, (0x50AFF, 36)),
    (0x57767, bytes.fromhex("cefded")),
    (0x5776A, (0x50B26, 13)),
    (0x57777, bytes.fromhex("cefdfdf000f96c")),
    (0x5777E, (0x50B3A, 7)),
    (0x57785, bytes.fromhex("2dfa52")),
    (0x57788, (0x50B44, 5)),
    (0x5778D, bytes.fromhex("daf83a")),
    (0x57790, (0x50B4C, 22)),
    (0x577A6, bytes.fromhex("000d")),
    (0x577A8, (0x50B64, 21)),
    (0x577BD, bytes.fromhex("72fbae")),
    (0x577C0, (0x50B7C, 7)),
    (0x577C7, bytes.fromhex("2dfa31")),
    (0x577CA, (0x50B86, 7)),
    (0x577D1, bytes.fromhex("01")),
    (0x577D2, (0x50B8E, 4)),
    (0x577D6, bytes.fromhex("46c0300146c0")),
    (0x577DC, (0x50B98, 17)),
    (0x577ED, bytes.fromhex("72fbd6")),
    (0x577F0, (0x50BAC, 9)),
    (0x577F9, bytes.fromhex("2dfa18")),
    (0x577FC, (0x50BB8, 5)),
    (0x57801, bytes.fromhex("daf800")),
    (0x57804, (0x50BC0, 6)),
    (0x5780A, bytes.fromhex("18e8025b18f1f072fb84")),
    (0x57814, (0x50BD0, 5)),
    (0x57819, bytes.fromhex("2dfa08")),
    (0x5781C, (0x50BD8, 27)),
    (0x57837, bytes.fromhex("72fb71")),
    (0x5783A, (0x50BF6, 7)),
    (0x57841, bytes.fromhex("2df9f4")),
    (0x57844, (0x50C00, 7)),
    (0x5784B, bytes.fromhex("01")),
    (0x5784C, (0x50C08, 4)),
    (0x57850, bytes.fromhex("46c0300146c0")),
    (0x57856, (0x50C12, 23)),
    (0x5786D, bytes.fromhex("72fb96")),
    (0x57870, (0x50C2C, 7)),
    (0x57877, bytes.fromhex("2df9d9")),
    (0x5787A, (0x50C36, 5)),
    (0x5787F, bytes.fromhex("d9ffc1")),
    (0x57882, (0x50C3E, 7)),
    (0x57889, bytes.fromhex("cefd5c")),
    (0x5788C, (0x50C48, 44)),
    (0x578B8, bytes.fromhex("b50069610cc9d219f072f8ee493868090e0a06124b3718d22800d10c683b2b02d0060049d5072302603b041b18d2e002"
                "2301041b18d26162bc084718210260012000bc08471869620a120212320261624b294718b500f7fffd0c69620a120212"
                "32032801d10032016162bc08471869620c1204121c73021b18d2320561624b1e4718b500f7ceff2e686a2a00da026962"
                "32026162bc084718b5104c18f072f8ee69622800d11968212980d1180cd3d2162304041b431a61624811f7ceff794811"
                "f7fffc7c2800d103480e68002801d0e5490d2000600843c0e0072340e00223804219d002041b431a6162bc10bc084718"
                "001fb39400010001000264210005753c00000190001bd210002dea80")),
    (0x57A08, bytes.fromhex("4928680a0a1202121c63431a600a4b264718b5014a23681321010209430b6013bc012100f000fb4e42a0d0054a1d6813"
                "21020209430b6013bc084718b407481968012201029243116001bc074718b40e4916481768000980d21d22104b153b01"
                "d1fd481368000980d20f3a01d1f620016048694806004a0b6813021b0a1b43032002040043036013e0054a0668132001"
                "0400430360136848bc0e2800d001200043c0477000057554000288f70005753c1204421800002000")),
    (0x57F74, bytes.fromhex("477046c0b5f0b088f7d0f9a690064d3e240068282801d0072cfad2042028f7fffd393401e7f5e060240043e4a03a2100"
                "f7fffd3942a0d0f61c0646692214f7fef8372814d14a980049304288d1469801492f4288d1429d022d00d03f07a8d13d"
                "492b428dd83a980307c1d13742a8d2354828a905002a2300f107f81a2800d12d9f0507b8d12b00300039002af7fef810"
                "42a8d1240030a9072201f7fef8092800d11d0030f7fdff78200046692210f000f8210039002af000f81d99044288d109"
                "a0190039002a9b0319db3301f7fffd002800d10a0038f107f81be006e0020038f107f8160030f7fdff579806b008bcf0"
                "bc084718188a4291d204231f41d8c90818c0e7f847704718001bd3a847524d4400010000002bf81c2f415441312f4752"
                "4d4f442e42494e0000000001000002400015f0250015f085002bf81c00055dc10005602500055f0d000517dd0008ffc9"
                "0001c5b4000288f7b500f7fdfe7742a0d10a480678002800d1022000f7f1f950a0032100f7fdfe6abc084718001fb200"
                "2f49524f4d2f47524d4f442e42494e00")),
    (0xB26E8, bytes.fromhex("f7a4feeb")),
    (0xB3970, bytes.fromhex("f7a3fd8b")),
    (0xC9A6C, bytes.fromhex("f78dfc02")),
    (0xC9A86, bytes.fromhex("f78dfcb3")),
    (0xC9BD4, bytes.fromhex("f78dfbdc")),
    (0x112578, bytes.fromhex("3234304b312e3000")),
    (0x157134, bytes.fromhex("f700f99d")),
]
# END KOMA10 DELTA


def build_koma10(stock: bytes) -> bytes:
    """The Koma 1.0 firmware image from verified stock bytes (sha256 KOMA10_SHA256)."""
    if hashlib.sha256(stock).hexdigest() != STOCK_SHA256:
        raise _refuse("input", hashlib.sha256(stock).hexdigest())
    payload = stock[HEADER_SIZE:]
    edits = []
    for addr, data in KOMA10_DELTA:
        if not isinstance(data, bytes):
            src, n = data
            data = payload[src - LOAD:src - LOAD + n]
        edits.append((addr, None, data, "Koma 1.0 firmware delta"))
    return _apply(stock, edits, KOMA10_SHA256)


# ---------------------------------------------------------------- package delta (the .grmod.delta files)
# A Koma package (.grmod) holds stock bytes: the installer compares its records' before-bytes and 'check' ranges with
# the camera's memory before it patches anything. So a package ships as a delta, rebuilt here from the user's stock file
# like the firmware above: Koma's own bytes literally, every stretch also found in the stock payload as a copy.
#   0x00  8s   DELTA_MAGIC
#   0x08  32s  sha256 of the stock qrflect3 the copies come from (STOCK_SHA256)
#   0x28  32s  sha256 of the output
#   0x48  u32  output length
#   0x4c  ops until the end, big-endian u32 w, n = w & 0x3fffffff: w & DELTA_COPY copies n bytes from the stock runtime
#         address in the next u32; w & DELTA_ZERO is n zero bytes; else n literal bytes follow.
# Long zero runs are ops, not literals, so the literal bytes and op words do not line up into stock-like zero tables.
DELTA_MAGIC = b"KOMADLT1"
DELTA_HEADER = 0x4C
DELTA_COPY = 0x80000000
DELTA_ZERO = 0x40000000
DELTA_LEN = 0x3FFFFFFF
DELTA_MIN_ZERO = 8
# Every window of this many stock bytes that is not all 0x00/0xFF is copied, so the literal bytes share no such
# window with stock.
DELTA_MIN_COPY = 16


def _verified_payload(stock: bytes) -> bytes:
    if hashlib.sha256(stock).hexdigest() != STOCK_SHA256:
        raise _refuse("input", hashlib.sha256(stock).hexdigest())
    return stock[HEADER_SIZE:]


def encode_delta(stock: bytes, target: bytes, min_copy: int = DELTA_MIN_COPY) -> bytes:
    """Encode target against verified stock bytes; apply_delta(stock, result) == target."""
    payload = _verified_payload(stock)
    hits: dict[bytes, list[int]] = {}
    for i in range(len(target) - min_copy + 1):
        key = target[i:i + min_copy]
        if key.strip(b"\0\xff"):
            hits.setdefault(key, [])
    for s in range(len(payload) - min_copy + 1):
        found = hits.get(payload[s:s + min_copy])
        if found is not None:
            found.append(s)
    ops: list[tuple[int, int, bytes | int]] = []   # (kind, length, stock offset or literal bytes)
    lit = bytearray()
    i = 0
    while i < len(target):
        kind, best, at = 0, 0, 0
        zeros = len(target[i:]) - len(target[i:].lstrip(b"\0"))
        if zeros >= DELTA_MIN_ZERO:
            kind, best = DELTA_ZERO, zeros
        for s in hits.get(target[i:i + min_copy], ())[:256]:
            n = min_copy
            while i + n < len(target) and s + n < len(payload) and target[i + n] == payload[s + n]:
                n += 1
            if n > best:
                kind, best, at = DELTA_COPY, n, s
        if not kind:
            lit.append(target[i])
            i += 1
            continue
        if lit:
            ops.append((0, len(lit), bytes(lit)))
            lit = bytearray()
        ops.append((kind, best, at))
        i += best
    if lit:
        ops.append((0, len(lit), bytes(lit)))
    out = bytearray(DELTA_MAGIC + bytes.fromhex(STOCK_SHA256) + hashlib.sha256(target).digest()
                    + struct.pack(">I", len(target)))
    for kind, n, data in ops:
        out += struct.pack(">I", kind | n)
        out += struct.pack(">I", LOAD + data) if kind == DELTA_COPY else data if not kind else b""
    delta = bytes(out)
    if apply_delta(stock, delta) != target:
        raise LoaderError("delta does not rebuild its target")
    return delta


def _delta_ops(delta: bytes):
    """(kind, length, stock runtime address or literal bytes) per op."""
    p = DELTA_HEADER
    try:
        while p < len(delta):
            (w,) = struct.unpack_from(">I", delta, p)
            kind, n = w & ~DELTA_LEN, w & DELTA_LEN
            if kind == DELTA_COPY:
                yield kind, n, struct.unpack_from(">I", delta, p + 4)[0]
                p += 8
            elif kind == DELTA_ZERO:
                yield kind, n, None
                p += 4
            elif kind:
                raise LoaderError(f"unknown delta op {w:#010x}")
            else:
                if p + 4 + n > len(delta):
                    raise LoaderError("delta literal runs past the end of the file")
                yield kind, n, delta[p + 4:p + 4 + n]
                p += 4 + n
    except struct.error:
        raise LoaderError("delta file is truncated") from None


def delta_stats(delta: bytes) -> dict:
    """Op count and bytes per op kind."""
    st = {"ops": 0, "literal": 0, "copied": 0, "zero": 0}
    for kind, n, _ in _delta_ops(delta):
        st["ops"] += 1
        st[{DELTA_COPY: "copied", DELTA_ZERO: "zero"}.get(kind, "literal")] += n
    return st


def apply_delta(stock: bytes, delta: bytes, expect_sha: str | None = None) -> bytes:
    """Rebuild a delta's output from verified stock bytes; refuses unless it is the output the delta (and expect_sha,
    when given) names."""
    payload = _verified_payload(stock)
    if len(delta) < DELTA_HEADER or delta[:8] != DELTA_MAGIC:
        raise LoaderError("not a Koma delta file")
    if delta[8:0x28] != bytes.fromhex(STOCK_SHA256):
        raise LoaderError("the delta was made against another stock file")
    (size,) = struct.unpack_from(">I", delta, 0x48)
    out = bytearray()
    for kind, n, data in _delta_ops(delta):
        if len(out) + n > size:
            raise LoaderError("delta output is longer than declared")
        if kind == DELTA_COPY:
            off = data - LOAD
            if off < 0 or off + n > len(payload):
                raise LoaderError(f"delta copy {data:#x}+{n} is outside the stock payload")
            out += payload[off:off + n]
        else:
            out += bytes(n) if kind == DELTA_ZERO else data
    sha = hashlib.sha256(out).hexdigest()
    if len(out) != size or sha != delta[0x28:0x48].hex():
        raise LoaderError(f"rebuilt output sha256 {sha} is not the one the delta names")
    if expect_sha and sha != expect_sha:
        raise LoaderError(f"rebuilt output sha256 {sha} != expected {expect_sha}")
    return bytes(out)


IMAGES = {"koma10": (build_koma10, "KOMA10_SHA256"), "flash1": (None, "OUTPUT_SHA256")}


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
        ok = before is None or (hashlib.sha256(cur).hexdigest() == before if isinstance(before, str) else cur == before)
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


def make(src: Path, out: Path, image: str = "flash1") -> str:
    """Read src, build (image "flash1": the card-loader image; "koma10": the Koma 1.0 firmware), write out (refuses to
    replace a different existing file). Returns the output sha256."""
    image = (build_koma10 if image == "koma10" else build)(read_stock(src))
    out = Path(out)
    if out.exists():
        if out.read_bytes() != image:
            raise LoaderError(f"{out} exists with different bytes; remove it or choose another name")
    else:
        with out.open("xb") as f:
            f.write(image)
    return hashlib.sha256(image).hexdigest()
