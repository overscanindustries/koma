// The settings being worked on: the map, the block read from the camera (or a card file), the working copy with the
// user's edits, and the pending list derived from the two. The engine module does the byte work.
import * as E from '../engine/engine.js';
import {bridgeJson, b64decode} from './bridge.js';
import {cam} from './camera.svelte.js';
import {readCamera} from './onboard.svelte.js';
import {volatileMask} from './apply.svelte.js';
import {USB_NAME, inspectBlock, differs, clean} from './words.js';

export const S = $state({
  map: 'loading',      // 'loading' | 'ready' | 'error'
  mapNote: '',
  mapLabel: '',
  mapKV: null,
  source: null,        // 'camera' | 'card'
  name: '',
  info: null,
  reading: false,
  readError: '',
  readCode: '',        // the bridge's error code for the last failed read ('no_param_cur': no settings file on the card)
  readKind: '',        // with no_param_cur: 'stock' (no Koma on the camera) or 'noexport' (Koma that has not saved them)
  liveSource: '',      // where the loaded camera block came from: 'ram' (-KGET, the live block) or 'cur' (PARAM.CUR)
  readFailConn: -1,    // the connection the last read failed on: screens do not read again by themselves on it
  readConn: -1,
  readAt: 0,           // Date.now when the block last came from the camera
  errors: {},          // field name -> plain message
  rev: 0,              // bumps on every byte change (typed arrays are not reactive)
});
// Not in $state: Uint8Arrays are mutated in place; S.rev says when.
export const B = {orig: null, blk: null};

export function touch() { S.rev++; }
// Reactive reads of the two blocks for templates (they depend on S.rev).
export function blk() { void S.rev; return B.blk; }
export function orig() { void S.rev; return B.orig; }

function addExtras(d) {
  const M = E.getMap();
  (d.fields || []).forEach(raw => {
    const f = M.byName[raw.name]; if (!f) return;
    f.help = raw.help || '';
    f.order = Number.isFinite(Number(raw.order)) && raw.order !== null && raw.order !== '' ? Number(raw.order) : null;
    f.section = raw.section || '';
    f.hidden = raw.ui === 'hidden';
  });
}

export function useMap(d, label) {
  try {
    E.loadMap(d);
    addExtras(d);
    S.map = 'ready'; S.mapNote = ''; S.mapLabel = label;
    S.mapKV = d && d.meta && Number.isInteger(d.meta.koma_version) ? d.meta.koma_version : null;
  } catch (e) {
    S.map = 'error'; S.mapNote = e.message || String(e);
  }
  touch();
}

export async function loadMap() {
  try {
    const d = await bridgeJson('/settings-map.json', {timeout: 15000});
    useMap(d, 'settings-map.json');
  } catch (e) {
    S.map = 'error'; S.mapNote = 'The settings list did not load.';
  }
}

export function map() { return E.getMap(); }
export function userField(f) { return !f.hidden && f.type !== 'bytes'; }

export function setBlock(bytes, name, source) {
  B.orig = bytes.slice(); B.blk = bytes.slice();
  S.name = name; S.source = source; S.errors = {};
  S.info = inspectBlock(bytes, name, S.mapKV);
  E.bind(B.blk, B.orig);
  touch();
}

export function clearBlock() { B.orig = B.blk = null; S.source = null; S.name = ''; S.info = null; S.errors = {}; touch(); }

const NO_KOMA = 'The Camera menu needs Koma on the camera.';
const NO_EXPORT = 'This camera has not saved its settings to the memory card yet. That needs a Koma version that shares settings.';
const READ_FAILED = 'The settings could not be read from the camera. Check the cable and try again.';

// GET /ram?off=0&len=4096: the camera's live settings block (-KGET, a read verb in the bridge). It holds what the
// camera uses now, so a setting changed on the body since it was turned on shows; PARAM.CUR is only rewritten at
// boot. null when the camera does not answer it (stock firmware, an older Koma: 502 not_supported) or the block is
// not a Koma block. MOCK-verified; HW check pending (-KGET of the whole block has not run on a camera).
export async function readLive() {
  try {
    const b = b64decode((await bridgeJson('/ram?off=0&len=4096', {timeout: 15000})).data);
    return inspectBlock(b, USB_NAME, null).ok && E.komaVersion(b) !== null ? b : null;
  } catch (e) { return null; }
}

// The camera's settings block without touching the loaded one: PARAM.CUR, then the live block when the camera runs
// Koma and answers it. The bytes the camera writes for itself (counters, the KSIO record with its firmware tag and
// import status, the checksum: apply.svelte.js VOLATILE) still come from PARAM.CUR, where they are proven on
// hardware; the settings come from the live block. Throws the bridge's error (no_param_cur, a cable fault) or a
// plain one for a bad block.
export async function readBlock() {
  const b = b64decode((await bridgeJson('/settings', {timeout: 30000})).data);
  const info = inspectBlock(b, USB_NAME, S.mapKV);
  if (!info.ok) throw Object.assign(new Error(info.msg), {plain: true});
  if (E.komaVersion(b) === null) return {bytes: b, source: 'cur'};
  const live = await readLive();
  if (!live) return {bytes: b, source: 'cur'};
  for (let i = 0; i < live.length; i++) if (volatileMask[i]) live[i] = b[i];
  return {bytes: live, source: 'ram'};
}

// No PARAM.CUR: decided once per connection. No KOMALOG either means the official firmware; with it, a Koma that
// has not saved its settings yet.
let causeConn = -1, cause = '';
async function noCurCause() {
  if (causeConn === cam.conn && cause) return cause;
  try { cause = (await readCamera({photo: 'never'})).build ? 'noexport' : 'stock'; causeConn = cam.conn; }
  catch (e) { cause = 'noexport'; }
  return cause;
}
export function readFailText(e, kind) {
  return e.plain ? e.message : kind === 'stock' ? NO_KOMA : kind === 'noexport' ? NO_EXPORT : READ_FAILED;
}

export async function readFromCamera() {
  if (S.reading) return;
  S.reading = true; S.readError = ''; S.readCode = ''; S.readKind = '';
  try {
    const r = await readBlock();
    setBlock(r.bytes, USB_NAME, 'camera');
    S.readConn = cam.conn; S.readAt = Date.now(); S.liveSource = r.source;
  } catch (e) {
    S.readCode = (e.data && e.data.code) || '';
    S.readFailConn = cam.conn;
    if (S.readCode === 'no_param_cur') S.readKind = await noCurCause();
    S.readError = readFailText(e, S.readKind);
  } finally { S.reading = false; }
}

// The camera confirmed an Apply: its block becomes the loaded one. Edits made after the Apply started (bytes that differ
// from what was applied) stay pending on top of it.
export function adopt(now, applied) {
  const keep = B.blk ? B.blk.slice() : null;
  setBlock(now, USB_NAME, 'camera');
  S.readConn = cam.conn; S.readAt = Date.now();
  if (keep && applied) keepEdits(keep, applied);
}

// Read the camera again and put the pending edits (bytes that differ from the old loaded block) on top of the fresh
// block: the camera changed under the edits (another GR, or a setting changed on the camera).
export async function rereadKeepEdits() {
  const old = B.orig ? B.orig.slice() : null, edited = B.blk ? B.blk.slice() : null;
  await readFromCamera();
  if (S.readError || !old || !edited) return;
  keepEdits(edited, old);
}

function keepEdits(edited, from) {
  const M = E.getMap();
  const cs0 = M ? M.csOff : 0xffc, cs1 = cs0 + (M ? M.csSize : 4);
  let n = 0;
  for (let i = 0; i < B.blk.length; i++) if ((i < cs0 || i >= cs1) && edited[i] !== from[i]) { B.blk[i] = edited[i]; n++; }
  if (n) { E.bind(B.blk, B.orig); touch(); }
}

export function setField(f, text) {
  if (!B.blk) return;
  E.bind(B.blk, B.orig);
  const err = E.setFieldText(f, text);
  if (err) S.errors[f.name] = f.range ? 'Choose a number from ' + f.range[0] + ' to ' + f.range[1] + '.' : 'That value cannot be used here.';
  else delete S.errors[f.name];
  if (!err) { const i = EDITS.indexOf(f); if (i >= 0) EDITS.splice(i, 1); EDITS.push(f); }
  touch();
}

// The order of the pending edits, newest last, for Cmd/Ctrl+Z: undoLast undoes the most recent
// field that still differs from what was loaded.
const EDITS = [];
export function undoLast() {
  while (EDITS.length) {
    const f = EDITS.pop();
    if (B.blk && B.orig && differs(f, B.orig, B.blk)) { undoField(f); return f; }
  }
  return null;
}

export function undoField(f) {
  if (!B.blk) return;
  B.blk.set(B.orig.subarray(f.offset, f.end), f.offset);
  delete S.errors[f.name];
  touch();
}

export function undoAll() { if (B.blk) { B.blk.set(B.orig); S.errors = {}; touch(); } }

// Fields whose bytes differ from what was loaded, user rows first (in map order), then Advanced-only ones.
export function pending() {
  const M = E.getMap();
  if (!M || !B.blk) return [];
  return M.fields.filter(f => differs(f, B.orig, B.blk));
}
export function unmappedChanged() {
  const M = E.getMap();
  if (!M || !B.blk) return 0;
  return E.unmappedRanges(B.orig, B.blk).reduce((n, [a, e]) => n + e - a, 0);
}
// The pending count as the pending bar shows it (Shell shows it beside Settings): one per named setting, plus one
// for any raw edits made in Advanced (fields the menus do not show, unmapped bytes).
export function pendingCount() {
  void S.rev;
  const all = pending(), named = all.filter(userField).length;
  return named + (all.length > named || unmappedChanged() > 0 ? 1 : 0);
}

// Save a block to this computer: a save dialog where the browser has one (the name is the person's choice), else a
// download. Returns the file name used, or '' when the dialog was cancelled.
export async function saveBlock(bytes, name) {
  const blob = new Blob([bytes], {type: 'application/octet-stream'});
  if (window.showSaveFilePicker) {
    try {
      const fh = await window.showSaveFilePicker({suggestedName: name});
      const w = await fh.createWritable(); await w.write(blob); await w.close();
      return fh.name || name;
    } catch (e) { if (e && e.name === 'AbortError') return ''; }
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  return name;
}

export function withChecksum(b) { const out = b.slice(); E.setChecksum(out); return out; }
export function label(f) { return clean(f.label); }
