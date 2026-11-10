"""The Photos test card, for test_e2e_photos.py and tools/komaapp/design/screens.py. MOCK.

seed(body) puts the six frames of testdata/photos/ (from a GR DIGITAL, EXIF and 160x120 thumbnails as the camera
wrote them; README there) on a simulated card's 100RICOH, after the mock's own three. owner_card(body) is the card the
screenshots use: the real frames only, no test pattern. many(body, n) adds n small synthetic frames over several
days, for the lazy-loading test. importer(link, root) points the bridge's Photos import at a temporary home folder, so
nothing lands in a real Pictures folder.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bridge"))
import komabridge as KB  # noqa: E402
import mockcard  # noqa: E402

FRAMES = HERE / "testdata" / "photos"
_DATE = re.compile(rb"(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)")


def add(body: mockcard.MockBody, name: str, data: bytes, stamp: str) -> None:
    """A finished JPEG in 100RICOH with its -FDAT date ("YYYY:MM:DD HH:MM:SS", as the camera prints it)."""
    path = f"{mockcard.FOLDER}/{name}"
    body.card[path] = data
    body.photo_dates[path] = stamp
    body.folder_entries.append(mockcard._dirent(name, 0x20, len(data), stamp))


def _clear(body: mockcard.MockBody) -> None:
    """The simulated card without the mock's own three test-pattern frames."""
    for name, _, _ in mockcard.PHOTOS:
        path = f"{mockcard.FOLDER}/{name}"
        body.card.pop(path, None)
        body.photo_dates.pop(path, None)
    body.folder_entries.clear()


def seed(body: mockcard.MockBody, keep_mock: bool = True, camera_size: bool = False) -> list[str]:
    if not keep_mock:
        _clear(body)
    names = []
    for f in sorted(FRAMES.glob("*.JPG")):
        data = frame(f.name, camera_size=camera_size)
        m = _DATE.search(data)
        add(body, f.name, data, m.group(0).decode() if m else "2026:11:07 12:00:00")
        names.append(f.name)
    return names


def redate(data: bytes, stamp: str) -> bytes:
    """The frame with every date in its EXIF block set to stamp ("YYYY:MM:DD HH:MM:SS"), as exiftool's -AllDates would,
    so the file agrees with the -FDAT date it is listed under. Same length: no offset moves."""
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker == 0xE1:
            end = i + 2 + length
            return data[:i + 4] + _DATE.sub(stamp.encode(), data[i + 4:end]) + data[end:]
        if marker in (0xDA, 0xD9):
            break
        i += 2 + length
    return data


# The size of each frame as the camera wrote it (3264 x 2448), before the testdata downscale.
CAMERA_SIZE = {"R0015702.JPG": 2674095, "R0015707.JPG": 2690255, "R0015709.JPG": 2641546, "R0015710.JPG": 2772089,
               "R0015712.JPG": 3327836, "R0015713.JPG": 3326421}
CAMERA_PIXELS = (3264, 2448)  # every frame's EXIF before the downscale changed it to 1280 x 960 (testdata README)
# Three of them again under earlier numbers and days, so the sheet groups by day with the real frames only.
EARLIER = (("R0015691.JPG", "R0015702.JPG", "2026:11:06 20:05:33"), ("R0015688.JPG", "R0015709.JPG", "2026:11:06 19:42:10"),
           ("R0015604.JPG", "R0015713.JPG", "2026:11:05 08:14:52"))


def exif_pixels(data: bytes, w: int, h: int) -> bytes:
    """The frame with its EXIF PixelXDimension / PixelYDimension (0xa002 / 0xa003) set to w x h, in place: same length,
    no offset moves. The picture itself is unchanged."""
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\0\0":
            t, out = i + 10, bytearray(data)
            order = "big" if data[t:t + 2] == b"MM" else "little"

            def num(o, n):
                return int.from_bytes(out[t + o:t + o + n], order)

            def entries(ifd):
                return [ifd + 2 + 12 * k for k in range(num(ifd, 2))]
            exif = next((num(e + 8, 4) for e in entries(num(4, 4)) if num(e, 2) == 0x8769), None)
            for e in entries(exif) if exif is not None else []:
                tag, kind = num(e, 2), num(e + 2, 2)
                if tag in (0xA002, 0xA003) and kind in (3, 4):
                    size = 2 if kind == 3 else 4
                    out[t + e + 8:t + e + 8 + size] = (w if tag == 0xA002 else h).to_bytes(size, order)
            return bytes(out)
        if marker in (0xDA, 0xD9):
            break
        i += 2 + length
    return data


def frame(name: str, stamp: str | None = None, camera_size: bool = False) -> bytes:
    """One of the real frames, re-dated when stamp is given. camera_size gives it what the camera wrote: its pixel
    size back in the EXIF, and padding after its EOI to the length of the camera's file (zeros, which the import's
    completeness check allows), so Details and the byte counts read as on the camera; the picture stays the downscaled
    one."""
    data = (FRAMES / name).read_bytes()
    if stamp:
        data = redate(data, stamp)
    if camera_size:
        data = exif_pixels(data, *CAMERA_PIXELS)
        data += b"\x00" * max(0, CAMERA_SIZE[name] - len(data))
    return data


def owner_card(body: mockcard.MockBody, camera_size: bool = False) -> None:
    """The screenshots' card: the six real frames on 2026/11/07 and three of them again on the two days before. No
    test pattern."""
    seed(body, keep_mock=False, camera_size=camera_size)
    for name, source, stamp in EARLIER:
        add(body, name, frame(source, stamp, camera_size), stamp)


def many(body: mockcard.MockBody, n: int, start: int = 100) -> None:
    """n small grey frames, five a day from 2026/10/01 (synthetic; for the lazy-loading test)."""
    for i in range(n):
        day, k = divmod(i, 5)
        stamp = f"2026:10:{1 + day % 28:02d} {9 + k:02d}:{(i * 7) % 60:02d}:00"
        data = mockcard.encode_gray_jpeg(64, 48, lambda x, y, i=i: (x * 4 + y * 3 + i * 9) % 256)
        add(body, f"RIMG{start + i:04d}.JPG", data, stamp)


def importer(link: KB.Link, root: Path):
    """The bridge's Photos import with its state and a home folder (with Pictures) under root."""
    home = root / "home"
    (home / "Pictures").mkdir(parents=True, exist_ok=True)
    return KB.importer(link, state_dir=root / "state", folder=home / "Pictures" / "Koma", home=home)
