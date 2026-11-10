// The firmware tag a GR DIGITAL writes into every JPEG: MakerNote tag 0x2, ASCII 'Rev%4.4x' from the running firmware's
// format string ('Rev0240' on the official 2.40). The Koma firmware changes
// that string: 'Rev024M' on the flash-1 loader image (tools/koma/loader.py), '240K2' on the SDHC Koma firmware.
// Layout: APP1 'Exif\0\0', big-endian TIFF, IFD0 ->
// EXIF IFD (0x8769) -> MakerNote (0x927c) starting 'Ricoh\0\0\0' with a big-endian IFD at +8; value offsets are from
// the TIFF start. Host-side reading of bytes the camera already wrote; nothing here talks to the camera.

export function makerTag(jpeg) {
  try {
    const b = jpeg instanceof Uint8Array ? jpeg : new Uint8Array(jpeg);
    if (b[0] !== 0xff || b[1] !== 0xd8) return null;
    let i = 2;
    while (i + 4 <= b.length && b[i] === 0xff) {
      const marker = b[i + 1], len = (b[i + 2] << 8) | b[i + 3];
      if (marker === 0xe1 && String.fromCharCode(...b.subarray(i + 4, i + 10)) === 'Exif\0\0') return fromTiff(b.subarray(i + 10, i + 2 + len));
      if (marker === 0xda || marker === 0xd9) return null;
      i += 2 + len;
    }
  } catch (e) { /* not a readable JPEG */ }
  return null;
}

function fromTiff(t) {
  const le = t[0] === 0x49 && t[1] === 0x49;
  const dv = new DataView(t.buffer, t.byteOffset, t.byteLength);
  const u16 = o => dv.getUint16(o, le), u32 = o => dv.getUint32(o, le);
  const entries = (off, bigEndian) => {
    const g16 = o => dv.getUint16(o, bigEndian === undefined ? le : !bigEndian);
    const g32 = o => dv.getUint32(o, bigEndian === undefined ? le : !bigEndian);
    const n = g16(off), out = {};
    for (let k = 0; k < n; k++) {
      const e = off + 2 + 12 * k;
      out[g16(e)] = {type: g16(e + 2), count: g32(e + 4), raw: e + 8, value: g32(e + 8)};
    }
    return out;
  };
  const ifd0 = entries(u32(4));
  if (!ifd0[0x8769]) return null;
  const exif = entries(ifd0[0x8769].value);
  const mn = exif[0x927c];
  if (!mn) return null;
  const at = mn.count <= 4 ? mn.raw : mn.value;
  if (String.fromCharCode(...t.subarray(at, at + 5)) !== 'Ricoh') return null;
  const note = entries(at + 8, true);
  const tag = note[0x2];
  if (!tag || tag.type !== 2) return null;
  const start = tag.count <= 4 ? tag.raw : tag.value;
  let s = '';
  for (let k = 0; k < tag.count && start + k < t.length; k++) {
    const c = t[start + k];
    if (!c) break;
    s += String.fromCharCode(c);
  }
  void u16;
  return s || null;
}

// What the tag says about the running firmware: 'official' | 'koma' | null (unknown tag).
export function firmwareOfTag(tag) {
  if (!tag) return null;
  if (/^Rev0240/.test(tag)) return 'official';
  if (/^Rev024M/.test(tag) || /^240K/.test(tag)) return 'koma';
  return null;
}

// The internal Koma firmware generation a tag shows (never shown to users):
// 1 for the loader image ('Rev024M', the only image tools/koma/loader.py builds, so the one this app flashes), the
// digit of an earlier build's firmware tag ('240K2' on the SDHC Koma firmware; '240K2.x'),
// null for a release tag ('240K1.0': the package rewrites the tag, so it does not show the firmware), the official
// firmware or a tag Koma does not know. The same tag sits in a JPEG's MakerNote and in PARAM.CUR's KSIO sub-header.
export function generationOfTag(tag) {
  if (!tag) return null;
  const t = String(tag).replace(/[\0 ]+$/, '');
  if (/^Rev024M/.test(t)) return 1;
  const m = /^240K(\d+)$/.exec(t) || /^240K(2)\.\d+$/.exec(t);
  return m ? Number(m[1]) : null;
}
