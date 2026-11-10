// The few EXIF facts the app shows, read in the browser from a JPEG the bridge already returned (GET /photos/<F>/<N>).
// The bridge lists no exposure (GET /photos has name, size, date and dimensions only), so exposure exists only for a
// file that was downloaded. Returns null when the file has no Exif block.
//
// {fNumber, exposureTime, iso, dateTimeOriginal, focalLength, model}; a missing tag is undefined.

const TAGS = {0x010f: 'make', 0x0110: 'model', 0x829a: 'exposureTime', 0x829d: 'fNumber', 0x8827: 'iso',
  0x9003: 'dateTimeOriginal', 0x920a: 'focalLength'};

export function readExif(buf) {
  const b = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  if (b[0] !== 0xff || b[1] !== 0xd8) return null;
  let i = 2;
  while (i + 4 <= b.length && b[i] === 0xff) {
    const marker = b[i + 1], len = (b[i + 2] << 8) | b[i + 3];
    if (marker === 0xe1 && b[i + 4] === 0x45 && b[i + 5] === 0x78 && b[i + 6] === 0x69 && b[i + 7] === 0x66) {
      try { return tiff(b.subarray(i + 10, i + 2 + len)); } catch (e) { return null; }
    }
    if (marker === 0xda || marker === 0xd9) return null;
    i += 2 + len;
  }
  return null;
}

function tiff(t) {
  const little = t[0] === 0x49;
  const u16 = o => little ? t[o] | (t[o + 1] << 8) : (t[o] << 8) | t[o + 1];
  const u32 = o => (little ? (t[o] | (t[o + 1] << 8) | (t[o + 2] << 16) | (t[o + 3] << 24))
    : ((t[o] << 24) | (t[o + 1] << 16) | (t[o + 2] << 8) | t[o + 3])) >>> 0;
  const out = {};
  function value(type, count, at) {
    const inline = (type === 3 && count <= 2) || ((type === 4 || type === 2) && count <= 4) || (type === 1 && count <= 4);
    const o = inline ? at : u32(at);
    if (type === 2) { let s = ''; for (let k = 0; k < count - 1 && o + k < t.length; k++) s += String.fromCharCode(t[o + k]); return s.trim(); }
    if (type === 3) return u16(o);
    if (type === 4) return u32(o);
    if (type === 5) { const d = u32(o + 4); return d ? u32(o) / d : undefined; }
    return undefined;
  }
  function ifd(at, deep) {
    const n = u16(at);
    for (let k = 0; k < n; k++) {
      const e = at + 2 + 12 * k, tag = u16(e), type = u16(e + 2), count = u32(e + 4);
      if (tag === 0x8769 && deep) ifd(u32(e + 8), false);
      else if (TAGS[tag]) out[TAGS[tag]] = value(type, count, e + 8);
    }
  }
  ifd(u32(4), true);
  return out;
}
