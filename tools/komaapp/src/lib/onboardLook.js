// Pick a look (setup step 6): make one look active on the camera, over USB, the way Koma's setup page "Make this
// the active look" does (same bytes, same order). Koma's look store lives in the settings block (tools/koma/features/bwrecipes.py): the active id at
// 0x8c0, the EDIT rows of the active look at 0x8c1..0x8c9, GRAIN 0x8cc, VIGNETTE 0x8cd, the ADJ LEVER row 0x8ce. Koma
// copies the rows into the active record whenever they differ from it, so the rows must be the new look's own values,
// written right after the id. Bridge calls: GET /ram (-KGET), POST /ram/batch (-KSET, the KSET_WINDOW rows only).
// The camera keeps the RAM when it is switched off normally with its power button.
import {bridgeJson, postJson, b64decode} from './bridge.js';

const RCP = {lo: 0x8b0, hi: 0xa28, store: 0x900, rec: 0x910, fx: 0xa00, act: 0x8c0, edit: 0x8c1, grain: 0x8cc, vig: 0x8cd,
  std: 0xa1c, adjm: 0xa18, adjrow: 0x8ce};
// From koma-setup.html (RCP_TABLE, generated from bwrecipes): defaults per id (p0..p8, kind), parameter limits per type,
// STANDARD's defaults, and the ADJ LEVER mask default.
const N = 20;
const DEFAULTS = [[0, 4, 4, 4, 4, 0, 0, 2, 3, 0], [3, 6, 4, 2, 5, 3, 0, 2, 4, 0], [3, 4, 4, 4, 4, 0, 0, 2, 4, 0], [2, 6, 4, 4, 4, 0, 3, 0, 3, 0],
  [4, 4, 4, 4, 4, 0, 0, 2, 1, 0], [5, 5, 4, 4, 4, 1, 0, 2, 3, 0], [6, 4, 4, 4, 4, 0, 0, 2, 2, 0], [4, 5, 3, 4, 4, 0, 0, 2, 3, 0],
  [3, 6, 2, 4, 4, 3, 0, 2, 3, 0], [1, 1, 4, 7, 2, 0, 0, 2, 2, 0], [0, 4, 4, 4, 4, 0, 0, 3, 3, 2], [1, 6, 6, 1, 1, 2, 3, 3, 2, 1],
  [1, 3, 5, 6, 4, 2, 3, 3, 2, 17], [7, 4, 4, 4, 4, 0, 0, 3, 3, 33], [1, 2, 5, 8, 2, 2, 5, 3, 2, 49], [1, 2, 6, 6, 1, 3, 6, 2, 2, 65],
  [1, 7, 4, 2, 3, 6, 4, 3, 2, 81], [0, 4, 4, 4, 4, 0, 0, 2, 3, 0], [0, 4, 4, 4, 4, 0, 0, 2, 3, 0], [0, 4, 4, 4, 4, 0, 0, 2, 3, 0]];
const LIMITS = [[8, 9, 9, 9, 9, 5, 5, 5, 5], [8, 9, 9, 9, 9, 7, 7, 7, 5], [8, 9, 9, 9, 9, 6, 2, 7, 5]];
const STD = [1, 4, 4, 4, 4, 3, 3, 3, 2, 33];
const ADJ_DEFAULT = 1181711;

function storeOk(b) {
  return b[RCP.store] === 0x52 && b[RCP.store + 1] === 0x43 && b[RCP.store + 2] === 0x50 && b[RCP.store + 3] === 0x36
    && b[RCP.store + 4] === 6 && b[RCP.store + 7] === N;
}
const addr = id => id > N ? RCP.std : RCP.rec + 12 * (id - 1);
function eff(b, id) {
  const o = addr(id), d = id > N ? STD : DEFAULTS[id - 1];
  if (storeOk(b) && b[o + 10] === 0xa5) {
    let x = 0x5a;
    for (let i = 0; i < 10; i++) x ^= b[o + i];
    const kind = b[o + 9], t = kind & 3, lim = LIMITS[t];
    if (x === b[o + 11] && t <= 2 && (kind >> 4) <= 5 && lim.every((n, i) => b[o + i] < n)) return Array.from(b.subarray(o, o + 9));
  }
  return d.slice(0, 9);
}
function fx(b, id) { const v = b[RCP.fx + id - 1]; return (v >> 4) === 5 ? {g: v & 3, v: (v >> 2) & 3} : {g: 0, v: 0}; }
function adjMask(b) {
  const m = b[RCP.adjm] | (b[RCP.adjm + 1] << 8) | (b[RCP.adjm + 2] << 16);
  return (b[RCP.adjm] ^ b[RCP.adjm + 1] ^ b[RCP.adjm + 2] ^ 0x5a) === b[RCP.adjm + 3] ? m : ADJ_DEFAULT;
}

// The bytes to write, in order: the id first, then the rows (koma-setup.html write order).
export function activation(b, id) {
  const p = eff(b, id), f = fx(b, id), adj = (adjMask(b) >> (id - 1)) & 1;
  const want = [[RCP.act, id]];
  for (let i = 0; i < 9; i++) want.push([RCP.edit + i, p[i]]);
  want.push([RCP.grain, f.g], [RCP.vig, f.v], [RCP.adjrow, adj ? 0 : 1]);
  return want;
}

// The camera's look store, as a 4096-byte block with only 0x8b0..0xa27 filled.
export async function readStore() {
  const d = await bridgeJson('/ram?off=' + RCP.lo + '&len=' + (RCP.hi - RCP.lo), {timeout: 15000});
  const b = new Uint8Array(4096);
  b.set(b64decode(d.data), RCP.lo);
  return b;
}

export function activeId(b) { const a = b[RCP.act]; return a >= 1 && a <= N + 1 ? a : null; }

// Make `id` the active look, then read the bytes back. Resolves to true when the camera holds exactly them.
export async function activate(id) {
  const b = await readStore();
  const want = activation(b, id).filter(([o, v]) => b[o] !== v || o === RCP.act);
  await postJson('/ram/batch', {writes: want.map(([off, val]) => ({off, val}))}, {timeout: 30000});
  const d = await bridgeJson('/ram?off=' + RCP.act + '&len=' + (RCP.adjrow - RCP.act + 1), {timeout: 15000});
  const now = b64decode(d.data);
  return activation(b, id).every(([o, v]) => now[o - RCP.act] === v);
}
