"""SIMULATED cameras and cards for the setup / Update Koma tests and screenshots.

Everything here plays the camera's part on mockcard.MockBody, the way the hardware behaved where it was observed:
a JPEG's MakerNote tag 0x2 names the running firmware ('Rev0240' official, 'Rev024M' the loader Koma firmware, '240K2' the pre-release SDHC Koma firmware, '240K1.0' the Koma 1.0
firmware and any Koma 1.0 package, which stamps its own tag at boot); Koma writes KOMALOG.TXT (its build id and start stage)
and PARAM.CUR (with the firmware tag in its KSIO sub-header) at every start. None of it is camera evidence.
"""
from __future__ import annotations

import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bridge"))
import mockcard  # noqa: E402
import selftest  # noqa: E402
from mockcard import FOLDER, MockBody, _dirent, encode_gray_jpeg, koma_cur, koma_ram, DEMO_CUR  # noqa: E402

PACKAGE_BUILD = 0x094F6569   # Koma 1.0 (dist-release/koma-1.0.0.grmod.delta), the package the bridge serves
OLDER_BUILD = 0x747F061F     # a pre-release build that 1.0 replaces
OTHER_BUILD = 0x5429091F     # a sibling pre-release build, not known to be older


def exif_app1(tag: str) -> bytes:
    """APP1 'Exif' with IFD0 -> EXIF IFD -> MakerNote 'Ricoh\\0\\0\\0' + big-endian IFD holding tag 0x2 = tag (ASCII)."""
    t = tag.encode("ascii") + b"\0"
    t += b"\0" * (-len(t) % 2)
    exif_at, note_at = 26, 44
    note_ifd = note_at + 8
    value_at = note_ifd + 18
    note = b"Ricoh\0\0\0" + struct.pack(">H", 1) + struct.pack(">HHII", 0x0002, 2, len(tag) + 1, value_at) + struct.pack(">I", 0) + t
    tiff = (b"MM\0\x2a" + struct.pack(">I", 8)
            + struct.pack(">H", 1) + struct.pack(">HHII", 0x8769, 4, 1, exif_at) + struct.pack(">I", 0)
            + struct.pack(">H", 1) + struct.pack(">HHII", 0x927C, 7, len(note), note_at) + struct.pack(">I", 0)
            + note)
    body = b"Exif\0\0" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def tagged_jpeg(tag: str, n: int = 7) -> bytes:
    jpeg = encode_gray_jpeg(mockcard.PHOTO_W, mockcard.PHOTO_H, mockcard._scene_shot(n))
    return jpeg[:2] + exif_app1(tag) + jpeg[2:]


def add_photo(body: MockBody, name: str, tag: str, stamp: str | None = None) -> str:
    """A new JPEG on the simulated card, newest by date, carrying the firmware tag."""
    stamp = stamp or time.strftime("%Y:%m:%d %H:%M:%S")
    path = f"{FOLDER}/{name}"
    body.card[path] = tagged_jpeg(tag, len(body.folder_entries) + 7)
    body.photo_dates[path] = stamp
    body.folder_entries.append(_dirent(name, 0x20, len(body.card[path]), stamp))
    return path


def stock_body(**kw) -> MockBody:
    """The official 2.40 firmware: no PARAM.CUR, no KOMALOG.TXT (both are Koma's), the newest photo tagged Rev0240."""
    body = MockBody(tether=True, koma=False, **kw)
    body.card.pop("/EXT0/PARAM.CUR", None)
    add_photo(body, "RIMG0004.JPG", "Rev0240", "2026:11:07 09:30:00")
    return body


def komalog(build: int = PACKAGE_BUILD, stage: int = 3) -> bytes:
    """KOMALOG.TXT naming the build; stage 3 = every start stage completed (tools/koma/features/telemetry.py)."""
    log = selftest.simulated_komalog(build=build)
    return log if stage == 3 else log.replace(b"STAGE=00000003", b"STAGE=%08X" % stage)


def koma_body(build: int = PACKAGE_BUILD, tag: bytes | None = None, **kw) -> MockBody:
    """Koma running: PARAM.CUR with the KOMA header and KSIO tag (mockcard's 240K1.0, a Koma 1.0 camera, unless `tag`),
    KOMALOG.TXT naming the build."""
    body = MockBody(tether=True, koma=True, **kw)
    body.card[selftest.KOMALOG] = komalog(build)
    if tag is not None:
        cur = bytearray(body.card["/EXT0/PARAM.CUR"])
        cur[mockcard.KSIO_TAG_OFF:mockcard.KSIO_TAG_OFF + 7] = tag.ljust(7, b"\0")
        body.card["/EXT0/PARAM.CUR"] = bytes(cur)
    return body


def after_koma_install(body: MockBody, build: int = PACKAGE_BUILD, stage: int = 3) -> None:
    """What the camera has after the Koma firmware and package went in and it started once: Koma's log and export."""
    body.card[selftest.KOMALOG] = komalog(build, stage)
    body.card["/EXT0/PARAM.CUR"] = koma_cur(DEMO_CUR.read_bytes())
    body.ram = koma_ram(body.card["/EXT0/PARAM.CUR"])
    body.ksta = True


def after_official(body: MockBody, name: str = "RIMG0009.JPG") -> None:
    """The official firmware again: Koma writes nothing any more; a photo taken afterwards carries Rev0240."""
    body.card.pop(selftest.KOMALOG, None)
    add_photo(body, name, "Rev0240")


# The card in this computer's reader, for a headless browser: showDirectoryPicker hands back a folder in the origin
# private file system (OPFS), pre-filled like a camera card (DCIM/100RICOH and any root files given).
def card_picker_script(root_files: dict | None = None, folder: str = "card") -> str:
    import json
    files = json.dumps(root_files or {})
    return f"""
(() => {{
  // init scripts also run in the page's first about:blank document, which has no storage: only the app's page
  if (!/^https?:$/.test(location.protocol) || !navigator.storage || !navigator.storage.getDirectory) return;
  const ready = (async () => {{
    const root = await navigator.storage.getDirectory();
    try {{ await root.removeEntry({json.dumps(folder)}, {{recursive: true}}); }} catch (e) {{}}
    const card = await root.getDirectoryHandle({json.dumps(folder)}, {{create: true}});
    const dcim = await card.getDirectoryHandle('DCIM', {{create: true}});
    await dcim.getDirectoryHandle('100RICOH', {{create: true}});
    const files = {files};
    for (const [name, text] of Object.entries(files)) {{
      const w = await (await card.getFileHandle(name, {{create: true}})).createWritable();
      await w.write(text); await w.close();
    }}
    return card;
  }})();
  window.__cardReady = ready;
  window.showDirectoryPicker = async () => ready;
}})();
"""


CARD_LISTING_JS = """async () => {
  const card = await window.__cardReady;
  const out = {};
  for await (const [name, h] of card.entries()) {
    if (h.kind !== 'file') { out[name] = 'dir'; continue; }
    const b = new Uint8Array(await (await h.getFile()).arrayBuffer());
    const d = new Uint8Array(await crypto.subtle.digest('SHA-256', b));
    out[name] = Array.from(d, x => x.toString(16).padStart(2, '0')).join('');
  }
  return out;
}"""
