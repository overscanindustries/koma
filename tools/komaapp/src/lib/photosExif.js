// Photos: the capture facts read from a JPEG's own EXIF, in the browser, from the file the bridge downloaded
// (GET /photos carries no exposure). Only what the GR DIGITAL writes and Photos shows: ExposureTime,
// FNumber, ISOSpeedRatings, DateTimeOriginal, the pixel size. The camera's frames are big-endian TIFF,
// ExposureTime always 1/N (1/11... 1/1), FNumber 240/100, ISO 154 with ISO SETTING on AUTO.
// No look: the files carry none.

const T = {exifIfd: 0x8769, exposure: 0x829a, fnumber: 0x829d, iso: 0x8827, taken: 0x9003, w: 0xa002, h: 0xa003};

// Exposure in the order the camera's LCD prints it. STATIC until checked. This mirrors
// EXPOSURE_ORDER in src/lib/format.js.
export const EXPOSURE_ORDER = ['aperture', 'shutter', 'iso'];

export function readExif(buf) {
  try {
    const v = new DataView(buf);
    if (v.byteLength < 4 || v.getUint16(0) !== 0xffd8) return null;
    let i = 2;
    while (i + 4 <= v.byteLength) {
      if (v.getUint8(i) !== 0xff) return null;
      const m = v.getUint8(i + 1);
      if (m === 0xda || m === 0xd9) return null;           // image data before any EXIF
      const len = v.getUint16(i + 2);
      if (m === 0xe1 && len > 8 && v.getUint32(i + 4) === 0x45786966 && v.getUint16(i + 8) === 0) {
        return parseTiff(new DataView(buf, i + 10, Math.min(len - 8, v.byteLength - i - 10)));
      }
      i += 2 + len;
    }
  } catch (e) { /* a broken file: no facts */ }
  return null;
}

function parseTiff(t) {
  const order = t.getUint16(0);
  if (order !== 0x4d4d && order !== 0x4949) return null;
  const le = order === 0x4949;
  const u16 = o => t.getUint16(o, le), u32 = o => t.getUint32(o, le);
  if (u16(2) !== 42) return null;
  const ifd = off => {
    const out = {}, n = u16(off);
    for (let k = 0; k < n; k++) { const e = off + 2 + 12 * k; out[u16(e)] = e; }
    return out;
  };
  const value = e => {
    if (e === undefined) return undefined;
    const type = u16(e + 2), count = u32(e + 4);
    if (type === 3) return u16(e + 8);
    if (type === 4) return u32(e + 8);
    if (type === 5) { const o = u32(e + 8); return [u32(o), u32(o + 4)]; }
    if (type === 2) {
      const o = count <= 4 ? e + 8 : u32(e + 8);
      let s = '';
      for (let k = 0; k < count; k++) { const c = t.getUint8(o + k); if (!c) break; s += String.fromCharCode(c); }
      return s;
    }
    return undefined;
  };
  const ifd0 = ifd(u32(4));
  const exifOff = value(ifd0[T.exifIfd]);
  const ex = typeof exifOff === 'number' ? ifd(exifOff) : {};
  const exposure = value(ex[T.exposure]), f = value(ex[T.fnumber]);
  return {
    exposure: Array.isArray(exposure) && exposure[0] > 0 && exposure[1] > 0 ? exposure : null,
    fnumber: Array.isArray(f) && f[1] > 0 && f[0] > 0 ? f[0] / f[1] : null,
    iso: typeof value(ex[T.iso]) === 'number' ? value(ex[T.iso]) : null,
    taken: /^\d{4}:\d\d:\d\d \d\d:\d\d:\d\d$/.test(value(ex[T.taken]) || '') ? value(ex[T.taken]) : null,
    width: value(ex[T.w]) || null,
    height: value(ex[T.h]) || null,
  };
}

const trim1 = x => (Math.round(x * 10) / 10).toString();

// Bare, as the camera prints it: 1/250, 1/11, 1/2; a second or longer as 1″, 1.3″.
export function shutterText([n, d]) {
  const t = n / d;
  if (t >= 1) return trim1(t) + '\u2033';
  if (n === 1) return '1/' + d;
  const r = d / n;
  return '1/' + (Number.isInteger(r) || r >= 10 ? Math.round(r) : trim1(r));
}
export function apertureText(f) { return 'F' + (f < 10 ? f.toFixed(1) : String(Math.round(f))); }
export function isoText(n) { return 'ISO ' + n; }

// The exposure facts present in the file, in EXPOSURE_ORDER; missing ones are left out, never guessed.
export function exposureParts(x) {
  if (!x) return [];
  const part = {
    aperture: x.fnumber ? apertureText(x.fnumber) : null,
    shutter: x.exposure ? shutterText(x.exposure) : null,
    iso: x.iso ? isoText(x.iso) : null,
  };
  return EXPOSURE_ORDER.map(k => part[k]).filter(Boolean);
}
