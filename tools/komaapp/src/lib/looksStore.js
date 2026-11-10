// The camera's looks as the 4096-byte settings block holds them (Koma's look store, tools/koma/features/bwrecipes.py
// 5.0, Look IA). Pure functions on a Uint8Array; no state, no bridge. STATIC: traced from bwrecipes'
// `eff`, `sync`, `fxsync`, `getcur` and `stdclean`. MOCK: the simulated camera copies an imported block as it is; it does not run
// bwrecipes' `sync`, so every write below already leaves the block the way that `sync` would.
//
// Layout (block offsets): the active look id 0x8c0 (0 = none); the editor rows of the active look 0x8c1..0x8c9
// (p0..p8), GRAIN 0x8cc, VIGNETTE 0x8cd, ADJ LEVER 0x8ce (0 ON, 1 OFF); the 'RCP6' store header 0x900 (+4 version,
// +5 row owner, +6 last applied, +7 record count 20, +8.. published values; rewritten by the camera, excluded from
// the Apply check); 12-byte records from 0x910 for ids 1..20 and at 0xa1c for STANDARD (21): p0..p8, kind, 0xa5,
// xor of bytes 0..9 with 0x5a; the FX table 0xa00 + id - 1 (0x50 | grain | vignette << 2); the ADJ LEVER mask 0xa18
// (21 bits, little-endian, then m0 ^ m1 ^ m2 ^ 0x5a; a bad check byte reads as the default mask). IMG SET 0x030 holds
// the stock base of the look that applies (1 NORMAL for color and SELECT, 3 B&W, 6 B&W (TE) with toning); a look
// applies only while IMG SET holds its base.
import {LOOKS_DATA as D} from './looksData.js';
import {komaVersion, getMap} from '../engine/engine.js';

export const AT = {imgset: 0x030, act: 0x8c0, rows: 0x8c1, save: 0x8ca, reset: 0x8cb, grain: 0x8cc, vig: 0x8cd,
  adjrow: 0x8ce, store: 0x900, rec: 0x910, fx: 0xa00, adjm: 0xa18, std: 0xa1c};
export const STD = D.std;            // 21
const N = D.std - 1;                 // 20 records in the main store
export const P = {curve: 0, con: 1, key: 2, shd: 3, hil: 4, s5: 5, s6: 6, s7: 7, sharp: 8};
export const TYPE = {bw: 0, color: 1, select: 2};

export function typeOf(kind) { return kind & 3; }
export function koma(b) { return !!b && komaVersion(b) !== null; }

export function storeOk(b) {
  const o = AT.store;
  return b[o] === 0x52 && b[o + 1] === 0x43 && b[o + 2] === 0x50 && b[o + 3] === 0x36 && b[o + 4] === 6 && b[o + 7] === N;
}

// The Look IA (bwrecipes 5.0: STANDARD is look 21, each look has an ADJ LEVER flag) or a Koma build before it (bwrecipes 4.2)? Both keep this same 'RCP6' store, and nothing the bridge returns names the
// module: the Koma version is the settings version and the KSIO tag is the firmware's under either module. The ADJ LEVER row 0x8ce tells them apart:
//   - HW: builds before the Look IA never write it; their PARAM.CUR
//     holds 0xff there, the stock block's value;
//   - STATIC: under the Look IA it is a Koma row with two options, which koma_settings_init (tools/koma/linker.py)
//     puts back in range at every start, and bwrecipes' sync writes from the active look's flag; PARAM.CUR is
//     exported after both.
// This app writes 0x8ce only on a block that already shows the Look IA (putRows), so it never makes the sign itself.
// A host that wrote it to such a camera (for example a settings copy from a Look IA camera) leaves
// that camera reading as the Look IA.
export function lookIA(b) { return !!b && koma(b) && storeOk(b) && b[AT.adjrow] <= 1; }

function recAddr(id) { return id === STD ? AT.std : AT.rec + 12 * (id - 1); }

export function defaults(id) {
  const d = D.defaults[id - 1];
  return {p: d.slice(0, 9), kind: d[9]};
}

// The look's parameters as the camera uses them: its record when valid, else its defaults (bwrecipes `eff`).
export function params(b, id) {
  const o = recAddr(id), d = defaults(id);
  if (storeOk(b) && b[o + 10] === 0xa5) {
    let x = 0x5a;
    for (let i = 0; i < 10; i++) x ^= b[o + i];
    const kind = b[o + 9], t = kind & 3, lim = D.limits[t];
    if (x === b[o + 11] && t <= 2 && (kind >> 4) <= 5 && lim && lim.every((n, i) => b[o + i] < n))
      return {p: Array.from(b.subarray(o, o + 9)), kind, stored: true};
  }
  return {p: d.p, kind: d.kind, stored: false};
}

export function fx(b, id) {
  const v = b[AT.fx + id - 1];
  return (v >> 4) === 5 ? {g: v & 3, v: (v >> 2) & 3} : {g: 0, v: 0};
}

export function adjMask(b) {
  const o = AT.adjm, m = b[o] | (b[o + 1] << 8) | (b[o + 2] << 16);
  return (b[o] ^ b[o + 1] ^ b[o + 2] ^ 0x5a) === b[o + 3] ? m : D.adj;
}
export function adjOn(b, id) { return ((adjMask(b) >> (id - 1)) & 1) === 1; }
export function adjDefault(id) { return ((D.adj >> (id - 1)) & 1) === 1; }

// Everything a look is: {p, kind, g, v, adj}. The unit the editor edits and the Apply writes.
export function lookState(b, id) {
  const e = params(b, id), f = fx(b, id);
  return {p: e.p.slice(), kind: e.kind, g: f.g, v: f.v, adj: adjOn(b, id)};
}
// Its stock values (what RESET gives on the camera). The ADJ LEVER flag is a lever choice, not a look value: RESET
// leaves it alone, so does this.
export function stockState(id, adj) {
  const d = defaults(id);
  return {p: d.p.slice(), kind: d.kind, g: 0, v: 0, adj};
}
export function sameValues(a, b) {
  return a.kind === b.kind && a.g === b.g && a.v === b.v && a.p.every((x, i) => x === b.p[i]);
}
export function sameState(a, b) { return sameValues(a, b) && a.adj === b.adj; }

export function imgsetOf(p, kind) { return kind & 3 ? 1 : p[P.s6] ? 6 : 3; }

// STANDARD at its stock values: the camera shoots plain NORMAL with no look id (bwrecipes `stdclean`).
export function stdClean(b) { return sameValues(lookState(b, STD), stockState(STD, false)); }

function stockLabel(v) {
  const M = getMap(), f = M && M.byName.img_set;
  const t = f && f.values[String(v)];
  return t ? String(t).replace(/\s*\(I\)/g, '').trim() : 'IMG SET ' + v;
}

// The look the next photo gets (bwrecipes `getcur`, with the menu's display rules): {id} for one of the 21 looks,
// {ghost: label, value} for a stock IMG SET value outside the list (the ghost entry), null without Koma.
export function current(b) {
  if (!koma(b)) return null;
  const act = b[AT.act], img = b[AT.imgset];
  if (act >= 1 && act <= STD) {
    const e = params(b, act);
    if (imgsetOf(e.p, e.kind) === img) return {id: act};
  }
  if (img === 1) return stdClean(b) ? {id: STD} : {ghost: stockLabel(1), value: 1};
  return {ghost: stockLabel(img), value: img};
}
export function currentName(b) {
  const c = current(b);
  return !c ? '' : c.ghost ? c.ghost : D.names[c.id - 1];
}

// ---------------------------------------------------------------- writes (on a copy of the block)
// Only on a block whose store reads as 'RCP6' (storeOk): the app writes no look into a store it does not know, it
// says so instead (looks.svelte.js canWrite).
function putMask(b, m) {
  const o = AT.adjm;
  b[o] = m & 255; b[o + 1] = (m >> 8) & 255; b[o + 2] = (m >> 16) & 255;
  b[o + 3] = b[o] ^ b[o + 1] ^ b[o + 2] ^ 0x5a;
}
function putRows(b, st) {
  for (let i = 0; i < 9; i++) b[AT.rows + i] = st.p[i];
  b[AT.grain] = st.g; b[AT.vig] = st.v;
  if (lookIA(b)) b[AT.adjrow] = st.adj ? 0 : 1;           // older builds have no such row: its 0xff stays
  b[AT.save] = 0; b[AT.reset] = 0;
}

// Write look `id` as `st`, touching only what differs. Values equal to the stock ones drop the record, as RESET does.
// While the look's id is the active one its editor rows follow (or the camera's `sync` would copy the old rows back
// into the record), and IMG SET follows a base the edit moved (toning on or off), as `sync` does when IMG SET still
// held the old base. Before the Look IA there is no STANDARD record and no ADJ LEVER mask: neither is written.
export function writeLook(b, id, st) {
  if (id === STD && !lookIA(b)) return;
  if (!lookIA(b)) st = {...st, adj: lookState(b, id).adj};
  const was = current(b), wasOn = !!(was && was.id === id);
  const now = lookState(b, id), oldBase = imgsetOf(now.p, now.kind);
  if (!sameValues({...now, g: 0, v: 0}, {...st, g: 0, v: 0})) {
    const o = recAddr(id), d = defaults(id);
    if (st.kind === d.kind && st.p.every((x, i) => x === d.p[i])) b[o + 10] = 0;
    else {
      let x = 0x5a;
      for (let i = 0; i < 9; i++) { b[o + i] = st.p[i]; x ^= st.p[i]; }
      b[o + 9] = st.kind; x ^= st.kind; b[o + 10] = 0xa5; b[o + 11] = x;
    }
  }
  if (now.g !== st.g || now.v !== st.v) b[AT.fx + id - 1] = 0x50 | st.g | (st.v << 2);
  if (now.adj !== st.adj) {
    const bit = 1 << (id - 1), m = adjMask(b);
    putMask(b, st.adj ? m | bit : m & ~bit);
  }
  if (b[AT.act] === id) {
    const after = lookState(b, id);
    putRows(b, after);
    if (b[AT.imgset] === oldBase) b[AT.imgset] = imgsetOf(after.p, after.kind);
  }
  // STANDARD on the camera (with its id, or as plain NORMAL) stays on it: edited it takes its id, at its stock values
  // it is plain NORMAL again
  if (id === STD && (wasOn || b[AT.act] === STD)) activate(b, STD);
}

// Make `id` the look on the camera: its id, its editor rows and its stock base in IMG SET, as the camera's own `sync`
// leaves them after the IMG SET menu. STANDARD at its stock values is plain NORMAL with no id.
export function activate(b, id) {
  if (id === STD && stdClean(b)) { b[AT.act] = 0; b[AT.imgset] = 1; return; }
  b[AT.act] = id;
  const st = lookState(b, id);
  putRows(b, st);
  b[AT.imgset] = imgsetOf(st.p, st.kind);
}

// The block with look `id` written as `st` (when given) and made the look on the camera (when `pick`).
export function withLook(base, id, st, pick) {
  const b = base.slice();
  if (st) writeLook(b, id, st);
  if (pick) activate(b, id);
  return b;
}
