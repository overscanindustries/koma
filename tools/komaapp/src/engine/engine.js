// Koma settings engine: block parsing, checksum, field codec, merge and diff helpers.
// Lifted unchanged from the first <script> of tools/komaset/web/koma-setup.html (the "ENGINE" section): every function
// body below is byte-identical to the page's, which tools/komaset/web/test_engine_module.py checks, and the same parity
// tokens hold (test_web_parity.py). Only the DOM parts (setMap's info line, the Advanced view) were left behind.
// Module state, as on the page: M is the parsed map; blk / orig are the working and loaded copies that writeUint,
// setFieldText, fieldChanged and changeCount act on. Call bind(working, original) before using those four.
const BLOCK_SIZE = 4096;
const CS_OFF_DEFAULT = 0x0ffc;
const CS_SIZE = 4;
const CS_ALGO = 'u32-sum-of-u16be';
const MAGIC_TEXT = 'MG1H';
const KOMA_MAGIC_TEXT = 'KOMA';
const KOMA_OFF = 0x8b0;
const KOMA_VER_OFF = 0x8b4;
const KSIO_OFF = 0x8d0;
const KSIO_TAG_OFF = 0x8d5;
const KSIO_TAG_LEN = 7;

const h = (n, w) => n.toString(16).padStart(w, '0');
const toInt = v => { const n = typeof v === 'string' ? Number(v.trim()) : Number(v); if (!Number.isFinite(n)) throw new Error('bad number ' + v); return n; };

let M = null;          // parsed map
let orig = null;       // Uint8Array as loaded
let blk = null;        // Uint8Array working copy

function parseMap(d) {
  const m = d.meta || {};
  const o = {};
  o.firmware = m.firmware || '?';
  o.base = toInt(m.block_base || '0x1b000');
  o.size = Number(m.block_size || BLOCK_SIZE);
  const mg = m.magic || {offset: 0, bytes: '4d473148'};
  o.magicOff = toInt(mg.offset);
  o.magic = new Uint8Array((mg.bytes.match(/../g) || []).map(x => parseInt(x, 16)));
  const cs = m.checksum || {};
  o.csAlgo = cs.algo || CS_ALGO;
  o.csRange = cs.range || [0, o.size - CS_SIZE];
  o.csOff = cs.offset !== undefined ? toInt(cs.offset) : o.size - CS_SIZE;
  o.csSize = Number(cs.size || CS_SIZE);
  o.csEndian = cs.endian || 'big';
  o.bigEndian = !/little/i.test(m.byte_order || 'big');
  if (o.size !== BLOCK_SIZE) throw new Error('block_size ' + o.size + ' is not ' + BLOCK_SIZE);
  if (o.csAlgo !== CS_ALGO || o.csSize !== CS_SIZE || o.csEndian !== 'big')
    throw new Error('unsupported checksum ' + o.csAlgo + ' size ' + o.csSize + ' ' + o.csEndian + ' (only ' + CS_ALGO + ', 4 bytes, big)');
  o.fields = (d.fields || []).map(f => {
    const x = {name: f.name, label: f.label || f.name, page: f.page || 'misc', offset: toInt(f.offset),
      size: Number(f.size || 1), type: f.type || 'u8', range: f.range || null,
      unit: f.unit || '', bodySpecific: !!f.body_specific, confidence: f.confidence || ''};
    if (['enum','u8','u16','u32','i8','bitfield','bytes'].indexOf(x.type) < 0) throw new Error('field ' + x.name + ': unknown type ' + x.type);
    x.values = {};
    Object.keys(f.values || {}).forEach(k => { x.values[toInt(k)] = f.values[k]; });
    let bits = f.bits || {};
    if (Array.isArray(bits)) { const b = {}; bits.forEach((n, i) => { if (n) b[i] = n; }); bits = b; }
    x.bits = {};
    Object.keys(bits).forEach(k => { x.bits[toInt(k)] = bits[k]; });
    x.end = x.offset + x.size;
    return x;
  });
  o.pages = [];
  o.byName = {};
  o.fields.forEach(f => {
    if (o.pages.indexOf(f.page) < 0) o.pages.push(f.page);
    if (o.byName[f.name]) throw new Error('duplicate field name ' + f.name);
    o.byName[f.name] = f;
  });
  o.exclude = (d.exclude_on_merge || []).map(e => ({off: toInt(e.offset), size: Number(e.size || 1), reason: e.reason || ''}));
  return o;
}

function coverage() {
  const cov = new Array(M.size).fill(false);
  M.fields.forEach(f => { for (let i = f.offset; i < Math.min(f.end, M.size); i++) cov[i] = true; });
  for (let i = M.csOff; i < Math.min(M.csOff + M.csSize, M.size); i++) cov[i] = true;
  return cov;
}

function unmappedRanges(a, b) {
  const cov = coverage(), out = []; let s = null;
  for (let i = 0; i < M.size; i++) {
    const hit = !cov[i] && (!a || a[i] !== b[i]);
    if (hit && s === null) s = i;
    if (!hit && s !== null) { out.push([s, i]); s = null; }
  }
  if (s !== null) out.push([s, M.size]);
  return out;
}

function computeChecksum(b) {
  const lo = M ? M.csRange[0] : 0, hi = M ? M.csRange[1] : CS_OFF_DEFAULT;
  let sum = 0;
  for (let i = lo; i + 1 < hi; i += 2) sum = (sum + ((b[i] << 8) | b[i + 1])) >>> 0;
  return sum >>> 0;
}

function csOff() { return M ? M.csOff : CS_OFF_DEFAULT; }

function storedChecksum(b) {
  const o = csOff();
  return ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0;
}

function setChecksum(b) {
  const v = computeChecksum(b), o = csOff();
  b[o] = (v >>> 24) & 0xff; b[o + 1] = (v >>> 16) & 0xff; b[o + 2] = (v >>> 8) & 0xff; b[o + 3] = v & 0xff;
}

function magicOk(b) {
  const mg = M ? M.magic : new TextEncoder().encode(MAGIC_TEXT), off = M ? M.magicOff : 0;
  for (let i = 0; i < mg.length; i++) if (b[off + i] !== mg[i]) return false;
  return true;
}

function komaVersion(b) {
  const k = new TextEncoder().encode(KOMA_MAGIC_TEXT);
  for (let i = 0; i < k.length; i++) if (b[KOMA_OFF + i] !== k[i]) return null;
  return b[KOMA_VER_OFF];
}

// Firmware tag from the KSIO sub-header (same as komaset.py ksio_tag): text, or null when the block has no 'KSIO' magic.

function ksioTag(b) {
  const k = 'KSIO';
  for (let i = 0; i < k.length; i++) if (b[KSIO_OFF + i] !== k.charCodeAt(i)) return null;
  let t = '';
  for (let i = 0; i < KSIO_TAG_LEN; i++) t += String.fromCharCode(b[KSIO_TAG_OFF + i]);
  return t;
}

function tagDiffers(a, b) { const x = ksioTag(a), y = ksioTag(b); return x !== null && y !== null && x !== y; }
// Tag rule (same as komaset.py tag_problem): a differing firmware tag is a warning, never a refusal. The same Koma module
// runs on different firmware tags (Rev024M, 240K2); the camera imports with KSIO status 3.

function tagWarning(source, tgt) {
  if (!tagDiffers(source, tgt)) return null;
  const trim = t => t.replace(/[\0 ]+$/, '');
  return 'These cameras run different firmware tags (' + trim(ksioTag(source)) + ', ' + trim(ksioTag(tgt)) + ').' +
    (komaVersion(source) === komaVersion(tgt) ? ' Settings still copy; Koma version matches.' : '');
}

function rawBytes(f, b) { return b.slice(f.offset, f.end); }
// Multi-byte fields are big-endian (map meta byte_order); byte k of the value is at bytePos(f, k).

function bytePos(f, k) { return f.offset + (!M || M.bigEndian ? f.size - 1 - k : k); }

function uintOf(f, b) { let v = 0n; for (let k = f.size - 1; k >= 0; k--) v = (v << 8n) | BigInt(b[bytePos(f, k)] || 0); return v; }

function writeUint(f, v) { let x = BigInt(v); for (let k = 0; k < f.size; k++) { blk[bytePos(f, k)] = Number(x & 0xffn); x >>= 8n; } }

function hexOf(a) { return Array.prototype.map.call(a, x => h(x, 2)).join(' '); }

function renderValue(f, b) {
  if (f.type === 'bytes') return hexOf(rawBytes(f, b));
  const v = uintOf(f, b), raw = '0x' + v.toString(16).padStart(f.size * 2, '0');
  if (f.type === 'enum') return Object.prototype.hasOwnProperty.call(f.values, String(v)) ? f.values[String(v)] : raw;
  if (f.type === 'bitfield') {
    const n = [];
    for (let i = 0; i < f.size * 8; i++) if ((v >> BigInt(i)) & 1n) n.push(f.bits[i] || 'BIT' + i);
    return n.length ? n.join('|') : 'NONE';
  }
  if (f.type === 'i8') return String(v > 127n ? v - 256n : v);
  return v.toString();
}
// Returns null on success or an error string. Writes into blk only on success.

function setFieldText(f, text) {
  const t = String(text).trim();
  if (f.type === 'bytes') {
    const parts = t.replace(/,/g, ' ').split(/\s+/).filter(Boolean);
    if (parts.some(p => !/^[0-9a-fA-F]{1,2}$/.test(p))) return 'not hex bytes';
    if (parts.length !== f.size) return 'need ' + f.size + ' bytes, got ' + parts.length;
    parts.forEach((p, i) => { blk[f.offset + i] = parseInt(p, 16); });
    return null;
  }
  if (!/^-?(0x[0-9a-f]+|\d+)$/i.test(t)) return 'not an integer';
  const v = t.startsWith('-') ? -Number(t.slice(1)) : Number(t);
  if (f.type === 'enum') {
    if (f.values && Object.keys(f.values).length && !(v in f.values)) return 'not a known enum value';
  } else if (f.type === 'bitfield') {
    if (v < 0 || v >= Math.pow(2, f.size * 8)) return 'flags exceed field width';
  } else {
    if (f.range && !(f.range[0] <= v && v <= f.range[1])) return v + ' outside range ' + f.range[0] + '..' + f.range[1];
    if (f.type === 'i8') { if (v < -128 || v > 127) return 'outside i8'; }
    else if (v < 0 || v >= Math.pow(2, f.size * 8)) return 'does not fit in ' + f.size + ' byte(s)';
  }
  writeUint(f, v < 0 ? v & 0xff : v);
  return null;
}

function fieldChanged(f) { for (let i = f.offset; i < f.end; i++) if (blk[i] !== orig[i]) return true; return false; }

function changeCount() { return M && blk ? M.fields.filter(fieldChanged).length : 0; }

function mergeInto(tgt, source, force, incl) {
  const problems = [];
  [['target', tgt], ['source', source]].forEach(([n, b]) => { if (!magicOk(b)) problems.push(n + ' magic is not ' + MAGIC_TEXT); });
  const kt = komaVersion(tgt), ks = komaVersion(source);
  if (kt !== ks) problems.push('Koma versions differ (target ' + kt + ', source ' + ks + ')');
  const tw = tagWarning(source, tgt), warnings = tw ? [tw] : [];   // ksioTag(tgt) differs: warn only, no force needed
  if (problems.length && !force) return {ok: false, problems: problems, warnings: warnings, copied: 0, lines: ['refusing (tick force to override)']};
  const skip = new Array(M.size).fill(false);
  for (let i = M.magicOff; i < M.magicOff + M.magic.length; i++) skip[i] = true;
  for (let i = M.csOff; i < M.csOff + M.csSize; i++) skip[i] = true;
  if (!incl) M.exclude.forEach(e => { for (let i = e.off; i < Math.min(e.off + e.size, M.size); i++) skip[i] = true; });
  let copied = 0;
  for (let i = 0; i < M.size; i++) if (!skip[i] && tgt[i] !== source[i]) { tgt[i] = source[i]; copied++; }
  setChecksum(tgt);
  const lines = ['merged: ' + copied + ' byte(s) changed in this body (checksum recomputed)', 'skipped: magic, checksum (recomputed)'];
  if (incl) lines.push('skipped: (exclusions disabled by include excluded)');
  else M.exclude.forEach(e => {
    let differs = false; for (let i = e.off; i < e.off + e.size; i++) if (tgt[i] !== source[i]) differs = true;
    lines.push('skipped: 0x' + h(e.off, 3) + ' +' + e.size + ' ' + e.reason + (differs ? '' : ' (already equal)'));
  });
  return {ok: true, problems: problems, warnings: warnings, copied: copied, lines: lines};
}

// ---------- module glue (new; not on the page)
// Parse and install a map (the page's setMap without its DOM lines). Throws on a bad map, leaving M as it was.
function loadMap(d) { const m = parseMap(d); M = m; return M; }
function clearMap() { M = null; }
function getMap() { return M; }
// The page's bindEdit: point the codec at a working copy and the bytes it was loaded from.
function bind(working, original) { blk = working; orig = original; }

export {
  BLOCK_SIZE, CS_OFF_DEFAULT, CS_SIZE, CS_ALGO, MAGIC_TEXT, KOMA_MAGIC_TEXT, KOMA_OFF, KOMA_VER_OFF, KSIO_OFF, KSIO_TAG_OFF,
  KSIO_TAG_LEN, h, toInt, parseMap, coverage, unmappedRanges, computeChecksum, csOff, storedChecksum, setChecksum, magicOk,
  komaVersion, ksioTag, tagDiffers, tagWarning, rawBytes, bytePos, uintOf, writeUint, hexOf, renderValue, setFieldText,
  fieldChanged, changeCount, mergeInto, loadMap, clearMap, getMap, bind
};
