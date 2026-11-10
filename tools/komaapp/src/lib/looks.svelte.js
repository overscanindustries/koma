// The Looks area: the photos the try-on is drawn on (the last photo, the bundled sample), the edits made here (one draft
// per look, kept until applied or undone), the render queue, and the two writes: setting a look on the camera, and
// applying the edits of every edited look in one block (the area-wide pending bar). Both go
// through the verified Apply path (apply.svelte.js: send, restart, wait, read back, compare), on top of the block the
// camera last reported, so edits pending in Settings stay pending and are not sent with them.
//
// Bridge calls: GET /settings (through settings.svelte.js), GET /photos?newest=1 and one GET /photos/<F>/<N> (the
// newest photo, the whole JPEG; the bridge keeps it for the rest of the connection), and the Apply's own calls.
import {cam, connected} from './camera.svelte.js';
import {S, B, adopt, rereadKeepEdits} from './settings.svelte.js';
import {bridgeJson, bridgeFetch, postJson} from './bridge.js';
import {setOn as webcamSetOn} from './webcam.svelte.js';
import {startApply, A} from './apply.svelte.js';
import {STATE} from './words.js';
import * as LS from './looksStore.js';
import {makePlan, render, colorOf, castOf, ghostState} from './looksRender.js';
import {LOOKS_DATA as D} from './looksData.js';
import {W} from './looksWords.js';
import sampleUrl from '../assets/looks/sample.jpg';

export const LK = $state({
  src: 'idle',       // what the last photo's read gave: 'idle' | 'reading' | 'photo' | 'sample' | 'none'
  why: '',           // why no photo: 'none' | 'busy' | 'error' | 'card'
  name: '', path: '', date: '', size: 0,
  mono: false,       // the last photo has no color to show: the gallery draws on the sample instead
  srcRev: 0,         // bumps when a photo the looks are drawn on changes
  conn: -1,          // the connection the photo was read on
  readingS: 0,       // seconds spent reading the photo
  sel: 0,            // the look selected in the gallery (0: the one on the camera)
  rev: 0,            // bumps when a draft changes
  status: null,      // {tone: 'wait' | 'ok', text, id, ids}
});

// The two photos the looks can be drawn on: the last photo from the card, and the bundled sample (R0015657, color).
// Each keeps its bitmap (decoded once), how it was made (`state`: the look on the camera when the photo was read, null:
// STANDARD), whether it holds color, and its pixels at the sizes drawn.
const SRC = {
  photo: {bitmap: null, state: null, mono: false, cast: false, px: new Map()},
  sample: {bitmap: null, state: null, mono: false, cast: false, px: new Map()},
};

export function nameOf(id) { return D.names[id - 1] || ''; }

// ---------------------------------------------------------------- the photo
// What the gallery draws on: the last photo, unless it is black and white (the user's usual JPEG), where the filters,
// toning and SELECT's color would not show; then the sample. The editor's split draws on the last photo whenever there
// is one: "As shot" against the look is its point.
export function gallerySrc() { void LK.srcRev; return LK.src === 'photo' && !LK.mono ? 'photo' : 'sample'; }
export function editorSrc() { void LK.srcRev; return LK.src === 'photo' ? 'photo' : 'sample'; }
// The gallery draws on the sample although a photo was read (the Details line says why).
export function sampleForMono() { void LK.srcRev; return LK.src === 'photo' && LK.mono; }

async function decode(blob) {
  if (typeof createImageBitmap === 'function') return createImageBitmap(blob);
  const img = new Image();
  img.src = URL.createObjectURL(blob);
  await img.decode();
  return img;
}

// The bundled sample, decoded once. A color photo made as STANDARD: state null.
let sampleP = null;
function loadSample() {
  if (!sampleP) sampleP = (async () => {
    try {
      const r = await fetch(sampleUrl);
      if (!r.ok) throw new Error('http ' + r.status);
      setSource(SRC.sample, await decode(await r.blob()), null);
    } catch (e) { SRC.sample.bitmap = null; sampleP = null; }
    return !!SRC.sample.bitmap;
  })();
  return sampleP;
}

async function useSample(why) {
  const ok = await loadSample();
  Object.assign(LK, {src: ok ? 'sample' : 'none', why, name: '', path: '', date: '', size: 0, mono: false});
  LK.srcRev++;
}

// A photo the looks are drawn on: its pixels cached anew, its color read, and how it was made squared with it. The
// camera's look and the photo must agree, or the photo was made some other way: a color photo was not made by a black
// and white look (read as STANDARD); a black and white photo was not made by a color look, nor a toned one by a look
// without TONING, nor a neutral one by a toned look (read as the stock B&W setting, the stock curve).
function setSource(s, bitmap, state) {
  s.bitmap = bitmap; s.px.clear(); s.state = state;
  const c = bitmap ? colorOf_(s) : {mono: false, cast: false};
  s.mono = c.mono; s.cast = c.mono && c.cast;
  const t = state ? state.kind & 3 : -1;
  const toned = castOf(state) > 2;
  if (t === LS.TYPE.bw && !c.mono) s.state = null;
  else if (c.mono && (t === -1 || t === LS.TYPE.color || (t === LS.TYPE.bw && toned !== c.cast))) s.state = ghostState(3, null);
  results.clear(); kept = 0;
}

// The newest photo on the card, read once. Asked again each time Looks opens and on a new connection, as one
// `?newest=1` listing (one -INFO and -FDAT, whatever the card holds); the photo itself is read again only when the
// newest one changed. `again`: the Read again action, shown as a read in progress.
let readTimer = null, loading = false;
export async function loadSource(again = false) {
  if (loading) return;
  loading = true;
  const conn = cam.conn;
  if (S.source === 'card' || !connected()) {
    LK.conn = conn;
    try { await useSample('card'); } finally { loading = false; }
    return;
  }
  const shown = !again && (LK.src === 'photo' || LK.src === 'sample');
  if (!shown) {
    LK.src = 'reading'; LK.readingS = 0;
    const t0 = Date.now();
    clearInterval(readTimer);
    readTimer = setInterval(() => { LK.readingS = Math.floor((Date.now() - t0) / 1000); }, 1000);
  }
  try {
    let d;
    try { d = await bridgeJson('/photos?newest=1', {timeout: 90000}); }
    catch (e) {
      if (e.status !== 409) throw e;
      await new Promise(r => setTimeout(r, 1500));      // Shoot was just left: remote control is still stopping
      d = await bridgeJson('/photos?newest=1', {timeout: 90000});
    }
    const p = d.photos && d.photos[0];
    if (!p) { LK.conn = conn; await useSample('none'); return; }
    if (LK.path && SRC.photo.bitmap && p.path === LK.path && p.size === LK.size && (LK.src === 'photo' || again)) {
      Object.assign(LK, {src: 'photo', why: '', conn});
      return;
    }
    const r = await bridgeFetch('/photos/' + p.path.split('/').map(encodeURIComponent).join('/'), {timeout: 90000});
    if (!r.ok) throw Object.assign(new Error('http ' + r.status), {status: r.status});
    const blob = await r.blob();
    const bitmap = await decode(blob);
    // the look the photo was made with: the look on the camera when the photo is read (PARAM.CUR, written when the
    // camera started). Kept with this photo: setting another look later does not change how it was made.
    const c = B.orig && S.source === 'camera' ? LS.current(B.orig) : null;
    setSource(SRC.photo, bitmap, !c ? null : c.ghost ? ghostState(c.value, B.orig) : LS.lookState(B.orig, c.id));
    if (SRC.photo.mono) await loadSample();             // the gallery draws on the sample
    Object.assign(LK, {src: 'photo', why: '', name: p.name, path: p.path, date: p.date || '', size: p.size || blob.size,
      conn, mono: SRC.photo.mono});
    LK.srcRev++;
  } catch (e) {
    LK.conn = conn;
    if (!(shown && LK.src === 'photo')) await useSample(e && e.status === 409 ? 'busy' : 'error');
  } finally { clearInterval(readTimer); loading = false; }
}

// The camera is held (409): the busy line's action, the same as Photos'. The webcam is turned off; live view, an
// interval and remote control all end with POST /tether/stop. Then the photo is read again. `h`: camera.svelte.js holder.
export const release = $state({by: ''});
export async function releaseHolder(h) {
  release.by = h;
  try {
    if (h === 'webcam') await webcamSetOn(false);
    else await postJson('/tether/stop', undefined, {timeout: 15000});
  } catch (e) { /* the read below says what is true */ }
  try { await loadSource(true); } finally { release.by = ''; }
}

export function sourceSize(which) { void LK.srcRev; const b = SRC[which].bitmap; return b ? {w: b.width, h: b.height} : null; }

// RGBA pixels of a photo drawn at w x h device pixels (cached per size).
export function sourcePixels(w, h, which) {
  const s = typeof which === 'string' ? SRC[which] : which;
  const key = w + 'x' + h;
  let px = s.px.get(key);
  if (px) return px;
  const bitmap = s.bitmap;
  if (!bitmap) return null;
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d', {willReadFrequently: true});
  g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high';
  g.drawImage(bitmap, 0, 0, w, h);
  px = g.getImageData(0, 0, w, h);
  if (s.px.size > 6) s.px.clear();
  s.px.set(key, px);
  return px;
}

function colorOf_(s) {
  const w = 160, h = Math.max(1, Math.round(160 * s.bitmap.height / s.bitmap.width));
  const px = sourcePixels(w, h, s);
  return colorOf(px.data, w * h);
}

// ---------------------------------------------------------------- looks: what the camera holds, and the drafts
// id -> {st, hist: [earlier st,...], base}: `base` is the camera's state of the look when the draft was made, so a
// draft can be carried over when the camera's look changes under it (another GR on the cable, a look edited on the
// camera): the values edited here stay as edited, the rest follow the camera, as Settings keeps its pending edits
// on a block read again.
const drafts = new Map();

export function cameraState(id) {
  void S.rev;
  return B.orig ? LS.lookState(B.orig, id) : LS.stockState(id, LS.adjDefault(id));
}

// Does the Koma on the camera have the Look IA's STANDARD values and ADJ LEVER flags (looksStore.js lookIA)? Without
// a block (no camera, no file) the looks show as designed; nothing is written then.
export function lookIaOk() { void S.rev; return !B.orig || LS.lookIA(B.orig); }

// A look's draft as far as it can reach this camera: before the Look IA, STANDARD's values and every ADJ LEVER change
// are set aside (kept for a camera that has them, never shown or sent here).
function draftOf(id) {
  const d = drafts.get(id);
  if (!d || lookIaOk()) return d ? d.st : null;
  if (id === LS.STD) return null;
  const cur = cameraState(id);
  if (d.st.adj === cur.adj) return d.st;
  const st = {...clone(d.st), adj: cur.adj};
  return LS.sameState(st, cur) ? null : st;
}
export function stateOf(id) { void LK.rev; return draftOf(id) || cameraState(id); }
export function hasDraft(id) { void LK.rev; return !!draftOf(id); }

function clone(st) { return {p: st.p.slice(), kind: st.kind, g: st.g, v: st.v, adj: st.adj}; }

// Change one look's draft (a new object each time, so whatever shows it sees the change). `push` starts a new undo
// step: a slider gesture is one step.
export function edit(id, fn, push = true) {
  const cur = cameraState(id);
  const d = drafts.get(id) || {st: clone(cur), hist: [], base: clone(cur)};
  if (push) d.hist.push(d.st);
  const next = clone(d.st);
  fn(next);
  d.st = next;
  if (LS.sameState(next, cur)) drafts.delete(id); else drafts.set(id, d);
  LK.rev++;
}
export function undoLast(id) {
  const d = drafts.get(id);
  if (!d || !d.hist.length) return false;
  d.st = d.hist.pop();
  if (LS.sameState(d.st, cameraState(id))) drafts.delete(id);
  LK.rev++;
  return true;
}
export function dropDraft(id) { if (drafts.delete(id)) LK.rev++; }

// The camera's state of a look changed under its draft (read from another GR, or from this one after a change made
// on the camera, or after an Apply that finished late): the draft is carried onto the new state. A draft that
// changed the look's type or kind, or a look whose kind changed on the camera, keeps the look as edited here.
// Undo history starts again from the carried draft. Called by Looks.svelte whenever the loaded block changes; with
// no block loaded (between a card file and the camera) there is nothing to carry onto yet.
export function rebaseDrafts() {
  void S.rev;
  if (!B.orig) return;
  let changed = false;
  for (const [id, d] of drafts) {
    const now = cameraState(id);
    if (LS.sameState(now, d.base)) continue;
    let st = clone(d.st);
    if (d.st.kind === d.base.kind && now.kind === d.base.kind) {
      st = clone(now);
      d.st.p.forEach((x, i) => { if (x !== d.base.p[i]) st.p[i] = x; });
      for (const k of ['g', 'v', 'adj']) if (d.st[k] !== d.base[k]) st[k] = d.st[k];
    }
    if (LS.sameState(st, now)) drafts.delete(id);
    else drafts.set(id, {st, hist: [], base: clone(now)});
    changed = true;
  }
  if (changed) LK.rev++;
}

// ---------------------------------------------------------------- the editor's rows, in the camera's order
// [{key, label, options, index(st) -> i, set(st, i), def(id) -> i, kind}]: 'scale' for steps on one axis (a detent
// slider), 'list' for named choices (a select), 'seg' for two named choices. p5..p7 change with the look's type, as
// the camera's editor rows do: B&W FILTER EFF. / TONING / TONE LEVEL, color SATURATION / WARMTH / TINT, SELECT
// COLOR / RANGE / SATURATION.
const SLOT_KIND = [['list', 'list', 'scale'], ['scale', 'scale', 'scale'], ['list', 'seg', 'scale']];
export function rowsFor(kind) {
  const t = Math.min(2, kind & 3), slots = D.slots[t], R = D.rows;
  const param = (key, label, options, i, kindOf) => ({key, label, options, kind: kindOf,
    index: st => st.p[i], set: (st, v) => { st.p[i] = v; }, def: id => LS.defaults(id).p[i]});
  const rows = [
    param('curve', R.rp_curve.label, R.rp_curve.options, 0, 'list'),
    param('contrast', R.rp_contrast.label, R.rp_contrast.options, 1, 'scale'),
    param('key', 'KEY', R.rp_contrast.options, 2, 'scale'),
    param('shadow', 'SHADOW', R.rp_contrast.options, 3, 'scale'),
    param('highlight', 'HIGHLIGHT', R.rp_contrast.options, 4, 'scale'),
  ];
  slots.forEach(([label, options], k) => rows.push(param('p' + (5 + k), label, options, 5 + k, SLOT_KIND[t][k])));
  rows.push(param('sharpness', R.rp_sharp.label, R.rp_sharp.options, 8, 'scale'));
  rows.push({key: 'grain', label: R.rp_grain.label, options: R.rp_grain.options, kind: 'scale',
    index: st => st.g, set: (st, v) => { st.g = v; }, def: () => 0});
  rows.push({key: 'vignette', label: R.rp_vig.label, options: R.rp_vig.options, kind: 'scale',
    index: st => st.v, set: (st, v) => { st.v = v; }, def: () => 0});
  return rows;
}
// The ADJ LEVER row as the camera's editor names it (stored 0 ON, 1 OFF; shown OFF | ON, as every on/off value).
export const ADJ_ROW = {key: 'adj', label: D.rows.rp_adjlever.label, on: D.rows.rp_adjlever.options[0],
  off: D.rows.rp_adjlever.options[1]};

// What a draft changes against the camera, row by row, for the bar and its Review. A CUSTOM slot that held a color
// look and went back to its stock values (MONO) changes type: its rows are listed together and undo together.
export function changesOf(id) {
  void LK.rev; void S.rev;
  const dst = draftOf(id);
  if (!dst) return [];
  const d = {st: dst};
  const cur = cameraState(id), out = [];
  const rows = rowsFor(d.st.kind), same = (d.st.kind & 3) === (cur.kind & 3);
  const curRows = same ? rows : rowsFor(cur.kind);
  rows.forEach((r, i) => {
    const a = curRows[i].index(cur), b = r.index(d.st);
    if (a !== b || curRows[i].label !== r.label) {
      out.push({key: r.key, label: r.label, from: curRows[i].label === r.label ? curRows[i].options[a] : '', to: r.options[b],
        undo: same ? () => edit(id, st => { r.set(st, a); }) : () => dropDraft(id)});
    }
  });
  if (cur.adj !== d.st.adj) out.push({key: 'adj', label: ADJ_ROW.label, from: cur.adj ? ADJ_ROW.on : ADJ_ROW.off,
    to: d.st.adj ? ADJ_ROW.on : ADJ_ROW.off,
    undo: () => edit(id, st => { st.adj = cur.adj; })});
  return out;
}

// ---------------------------------------------------------------- renders
const results = new Map();    // key -> ImageData
const queue = [];
let pumping = false;

export function planFor(st, ghostOf, which = 'photo') {
  const s = SRC[which];
  return makePlan(ghostOf !== undefined ? ghostState(ghostOf, B.orig) : st, s.state, s.mono, s.cast);
}
function stKey(st) { return st.kind + ':' + st.p.join(',') + ':' + st.g + ',' + st.v + (st.te ? ':' + st.te.join(',') : ''); }

// Finished renders, least recently used first, kept under a byte budget (a slider drag makes one large render per
// step; the oldest of them go before the cards that are on screen).
const BUDGET = 96 * 1024 * 1024;
let kept = 0;
function remember(key, img) {
  results.set(key, img);
  kept += img.data.length;
  while (kept > BUDGET && results.size > 1) {
    const [k, old] = results.entries().next().value;
    results.delete(k); kept -= old.data.length;
  }
}

// job: {st, ghost?, src: 'photo' | 'sample', w, h, done(ImageData)}. Returns a cancel function.
export function requestRender(job) {
  job.src = job.src || 'photo';
  const key = job.src + LK.srcRev + '|' + job.w + 'x' + job.h + '|' + (job.ghost !== undefined ? 'g' + job.ghost : stKey(job.st));
  const hit = results.get(key);
  if (hit) { results.delete(key); results.set(key, hit); job.done(hit); return () => {}; }
  job.key = key;
  if (job.urgent) queue.unshift(job); else queue.push(job);
  if (!pumping) { pumping = true; requestAnimationFrame(pump); }
  return () => { job.cancelled = true; };
}
function pump() {
  const t0 = performance.now();
  while (queue.length && performance.now() - t0 < 14) {
    const job = queue.shift();
    if (job.cancelled) continue;
    const px = sourcePixels(job.w, job.h, job.src);
    if (!px) continue;
    const out = new ImageData(job.w, job.h);
    render(planFor(job.st, job.ghost, job.src), px.data, out.data, job.w, job.h);
    remember(job.key, out);
    job.done(out);
  }
  if (queue.length) requestAnimationFrame(pump); else pumping = false;
}

// ---------------------------------------------------------------- writes
export function canWrite() {
  void S.rev;
  return connected() && S.source === 'camera' && !!B.orig && LS.koma(B.orig) && LS.storeOk(B.orig);
}
// Why nothing is written from here, for the one line that says so: 'store' when the camera's Koma keeps its looks in
// an older store, 'camera' for everything else (a memory card file, or no camera on the cable).
export function blockedBy() {
  void S.rev;
  return connected() && S.source === 'camera' && B.orig && LS.koma(B.orig) && !LS.storeOk(B.orig) ? 'store' : 'camera';
}

// After a confirmed write. The camera restarted, which is a new connection: the photo shown is still the newest one
// (a restart takes no photo), so it is not asked for again. A draft that was sent goes, unless it changed while a
// late Apply was pending (the camera came back after "has not come back" was closed): then it stays, and
// rebaseDrafts carries it onto what the camera now holds. sent: [[id, st],...].
function written(sent, now, bytes, text, id) {
  adopt(now, bytes);
  if (LK.src === 'photo') LK.conn = cam.conn;
  for (const [k, st] of sent) {
    const d = drafts.get(k);
    if (d && st && LS.sameState(d.st, st)) drafts.delete(k);
  }
  LK.rev++;
  LK.status = {tone: 'ok', text, id, ids: sent.map(([k]) => k)};
}

// Set look `id` on the camera, with its draft if it has one ("the look as it shows here"). The sheet's confirmation is
// its done line, "SHINJUKU is active."; no read-back row repeats it.
export function pick(id) {
  if (!canWrite() || A.open) return;
  const st = draftOf(id) ? clone(draftOf(id)) : null;
  const bytes = LS.withLook(B.orig, id, st, true);
  const name = nameOf(id);
  LK.status = {tone: 'wait', text: W.setting, id};
  startApply({bytes, base: B.orig, edits: true, title: W.settingTitle(name), done: W.isActive(name),
    note: STATE.keepsLook, fields: [],
    onDone: (now, sent) => {
      if (B.blk) { if (st) LS.writeLook(B.blk, id, st); LS.activate(B.blk, id); }
      written([[id, st]], now, sent, W.isSet, id);
    },
    onReread: rereadKeepEdits, rereadLabel: W.readAgain});
}

// Every look with a draft that reaches this camera, in the camera's order: [{id, name, changes}] (the
// pending bar is area-wide, one Apply for all of them).
export function drafted() {
  void LK.rev; void S.rev;
  return D.order.filter(id => draftOf(id)).map(id => ({id, name: nameOf(id), changes: changesOf(id)}))
    .filter(x => x.changes.length);
}
export function pendingCount() { return drafted().reduce((n, x) => n + x.changes.length, 0); }
export function dropAll() { const ids = drafted().map(x => x.id); ids.forEach(k => drafts.delete(k)); if (ids.length) LK.rev++; }

// Apply every look's draft to the camera in one block, one send, one restart, one read-back, without changing which
// look is on it. `id`: the look the person is on (its editor), for the done line there.
export function applyAll(id = 0) {
  const list = drafted();
  if (!canWrite() || A.open || !list.length) return;
  const sent = list.map(x => [x.id, clone(draftOf(x.id))]);
  const bytes = sent.reduce((b, [k, st]) => LS.withLook(b, k, st, false), B.orig);
  const n = list.reduce((a, x) => a + x.changes.length, 0);
  const to = list.length === 1 ? list[0].name : W.looksN(list.length);
  LK.status = {tone: 'wait', text: '', id, ids: sent.map(([k]) => k)};
  startApply({bytes, base: B.orig, edits: true, title: W.applyingTitle(n, to), done: W.appliedTo(to),
    note: W.keepsChanges(n), fields: [],
    onDone: (now, bytesSent) => {
      if (B.blk) for (const [k, st] of sent) LS.writeLook(B.blk, k, st);
      written(sent, now, bytesSent, W.applied, id || sent[0][0]);
    },
    onReread: rereadKeepEdits, rereadLabel: W.readAgain});
}
// The Apply sheet was closed without success: the status line goes back to rest.
export function settleStatus() {
  if (LK.status && LK.status.tone === 'wait' && !A.open) LK.status = null;
}
