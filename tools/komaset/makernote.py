"""Read the firmware tag (MakerNote tag 0x2, e.g. 'Rev0240', 'Rev024M', '240K2') from a GR DIGITAL JPEG.

Layout: APP1 'Exif\\0\\0' + TIFF; IFD0 -> EXIF IFD (tag 0x8769) -> MakerNote (tag 0x927c), which starts with
'Ricoh\\0\\0\\0' and holds a big-endian IFD at +8 whose value offsets count from the TIFF start. Reads the file's
segments up to SOS only; pixels are never decoded.
"""
from __future__ import annotations

import struct
from pathlib import Path

_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


class MetadataError(Exception):
    pass


def _app1(path: Path) -> bytes:
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            raise MetadataError(f"{path}: not a JPEG")
        while True:
            m = f.read(2)
            if len(m) < 2 or m[0] != 0xFF or m[1] == 0xDA:
                raise MetadataError(f"{path}: no EXIF APP1 segment")
            n = struct.unpack(">H", f.read(2))[0]
            p = f.read(n - 2)
            if m[1] == 0xE1 and p[:6] == b"Exif\0\0":
                return p[6:]


def _ifd(t: bytes, off: int, bo: str) -> dict:
    out = {}
    for i in range(struct.unpack(bo + "H", t[off:off + 2])[0]):
        e = off + 2 + 12 * i
        tag, ty, cnt = struct.unpack(bo + "HHI", t[e:e + 8])
        size, slot = _SIZE.get(ty, 1) * cnt, t[e + 8:e + 12]
        voff = struct.unpack(bo + "I", slot)[0] if size > 4 else None
        out[tag] = dict(type=ty, raw=slot[:size] if voff is None else t[voff:voff + size], voff=voff)
    return out


def read(path) -> dict:
    """{'rev': firmware tag or None}. Raises MetadataError when the file has no readable EXIF/MakerNote."""
    t = _app1(Path(path))
    try:
        bo = {b"MM": ">", b"II": "<"}[t[:2]]
        ifd0 = _ifd(t, struct.unpack(bo + "I", t[4:8])[0], bo)
        exif = _ifd(t, struct.unpack(bo + "I", ifd0[0x8769]["raw"])[0], bo)
        note_off = exif[0x927C]["voff"]
        if t[note_off:note_off + 5] != b"Ricoh":
            raise MetadataError(f"{path}: MakerNote header is not Ricoh")
        note = _ifd(t, note_off + 8, ">")
    except (KeyError, IndexError, struct.error, TypeError) as err:
        raise MetadataError(f"{path}: no readable MakerNote: {err!r}") from err
    rev = note.get(0x2)
    return {"rev": rev["raw"].split(b"\0")[0].decode("latin1") if rev and rev["type"] == 2 else None}
