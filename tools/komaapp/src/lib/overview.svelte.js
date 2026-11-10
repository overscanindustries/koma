// Overview's reads: the camera at rest. Nothing runs on the camera from here: no
// live view, no remote control, no restart, no write. Reads, cached per connection (a revisit asks for the photo list
// only, see load): GET /settings (PARAM.CUR: Koma version, menu layout; through settings.svelte.js), GET /ram for the look
// the camera holds now (two bytes by -KGET, a read verb; PARAM.CUR is written at boot and goes stale when the look is
// changed on the camera), GET /photos, and one GET /photos/<F>/<N> for the newest photo only, fetched again only when
// its path or size changes. Exposure comes from that file's EXIF, read here in the browser. Where Koma runs, one more
// card read per connection: GET /komalog (KOMALOG.TXT: the build that started), against this computer's package (a
// local file from the bridge), to tell an older build that this package replaces (lib/onboard.svelte.js classify).
import * as E from '../engine/engine.js';
import {cam, firmware} from './camera.svelte.js';
import {readPackage, pkgFacts, classify, modeFor} from './onboard.svelte.js';
import {S, B, readFromCamera} from './settings.svelte.js';
import {bridgeJson, bridgeFetch, b64decode} from './bridge.js';
import {readExif} from './exif.js';
import {clean, showValue, komaRelease} from './words.js';

const RCP_ACT = 0x8c0, IMG_SET = 0x030;

export const rest = $state({
  conn: -1,
  look: null,          // the look's name, or null when it cannot be read
  lookRead: false,
  photo: null,         // {path, name, size, date, width, height, url, exif}
  photoState: 'idle',  // 'idle' | 'reading' | 'ok' | 'none' | 'busy' | 'error'
  busy: false,         // live view holds the card (409 remote_on); a photo already shown stays on screen
  since: 0,            // Date.now when the current read began
  count: null,         // photos on the card (GET /photos count), null until read
  kind: null,          // the camera against this computer's package (onboard classify), null until read
  mode: null,          // what Update Koma would do: 'update' over an older build, else 'none' or null
  pkg: null,           // this computer's package {version, build, supersedes}
  fw: null,            // the firmware the camera reported on this connection, kept for a camera that went away
});

// The look by its camera name: a Koma look (the look store's active id) first, else the stock IMG SET value, with
// NORMAL shown as STANDARD (Look IA).
function lookOf(act, img) {
  const M = E.getMap();
  const rf = M && M.byName.recipe_rcp_active, f = M && M.byName.img_set;
  if (act && rf && rf.values[act] !== undefined) return clean(rf.values[act]);
  if (f && f.values[img] !== undefined) { const n = clean(f.values[img]); return n === 'NORMAL' ? 'STANDARD' : n; }
  return null;
}

// The settings read settled: read once per connection (never again after a failure on this connection), and wait
// for a read another screen started.
async function settings() {
  if (!B.orig && S.source !== 'card' && !S.reading && S.readFailConn !== cam.conn) await readFromCamera();
  for (let i = 0; S.reading && i < 200; i++) await new Promise(r => setTimeout(r, 150));
}

export async function readLook() {
  await settings();
  // A camera without Koma (no settings file, or a block without the KOMA header) has no look in RAM to read.
  if (komaFacts().state === 'none') { rest.look = null; rest.lookRead = true; return; }
  if (komaFacts().state === 'koma') readBuild();
  try {
    const a = b64decode((await bridgeJson('/ram?off=' + RCP_ACT + '&len=1', {timeout: 15000})).data)[0];
    const i = b64decode((await bridgeJson('/ram?off=' + IMG_SET + '&len=1', {timeout: 15000})).data)[0];
    rest.look = lookOf(a, i);
  } catch (e) {
    // No live settings on this camera (an older Koma): the block read from the card, if any.
    rest.look = B.orig && S.source === 'camera' ? lookOf(E.komaVersion(B.orig) !== null ? B.orig[RCP_ACT] : 0, B.orig[IMG_SET]) : null;
  }
  rest.lookRead = true;
}

// The build the camera started (KOMALOG.TXT) against this computer's package, once per connection: an older build this
// package is known to replace makes Update Koma the primary action. A log that cannot be read leaves things as they are.
async function readBuild() {
  const conn = cam.conn;
  try {
    let build = null;
    try { build = (await bridgeJson('/komalog', {timeout: 20000})).build_id || null; }
    catch (e) { if (e.status !== 404) throw e; }
    const pkg = pkgFacts(await readPackage().catch(() => null));
    if (rest.conn !== conn) return;
    const tag = B.orig ? komaRelease(B.orig).tag || null : null;   // an earlier build's tag makes it older too
    const kind = classify({fw: firmware(), build, kv: 0, photoName: null, tag}, pkg);
    Object.assign(rest, {kind, mode: modeFor(kind), pkg});
  } catch (e) { /* the camera keeps its primary; Update Koma reads again when it opens */ }
}

export async function readPhoto() {
  if (!rest.photo) { rest.photoState = 'reading'; rest.since = Date.now(); }
  let p;
  try {
    const d = await bridgeJson('/photos', {timeout: 60000});
    p = d.photos && d.photos[0];
    rest.count = Number.isFinite(d.count) ? d.count : (d.photos || []).length;
    rest.busy = false;
  } catch (e) {
    if (e.status === 409 && e.data && (e.data.code === 'remote_on' || e.data.code === 'webcam_on')) {
      rest.busy = true;
      if (!rest.photo) rest.photoState = 'busy';
    } else {
      rest.busy = false;
      rest.photoState = 'error';
    }
    return;
  }
  if (!p) { rest.photo = null; rest.photoState = 'none'; return; }
  if (rest.photo && rest.photo.path === p.path && rest.photo.size === p.size) { rest.photoState = 'ok'; return; }
  if (!rest.photo) { rest.photoState = 'reading'; rest.since = Date.now(); }
  try {
    const r = await bridgeFetch('/photos/' + p.path.split('/').map(encodeURIComponent).join('/'), {timeout: 60000});
    if (!r.ok) throw new Error('http ' + r.status);
    const buf = new Uint8Array(await r.arrayBuffer());
    if (rest.photo && rest.photo.url) URL.revokeObjectURL(rest.photo.url);
    rest.photo = {...p, url: URL.createObjectURL(new Blob([buf], {type: 'image/jpeg'})), exif: readExif(buf)};
    rest.photoState = 'ok';
  } catch (e) {
    rest.photoState = 'error';
  }
}

// Once per connection, when the settings list has loaded (the look's names come from it): the settings, the look and
// the photos. A later visit on the same connection asks only for the photo list (one -NLST of the DCIM folder; another
// window may have shot since), and fetches the photo only when the newest one changed.
export function load() {
  if (cam.state !== 'cam' || S.map !== 'ready') return;
  rest.fw = firmware() || rest.fw;
  if (rest.conn === cam.conn) { readPhoto(); return; }
  rest.conn = cam.conn;
  reset();
  readLook();
  readPhoto();
}

function reset() {
  rest.lookRead = false; rest.look = null; rest.busy = false; rest.count = null;
  rest.kind = null; rest.mode = null;
}

// Setup changed the camera (a look, Koma itself) on this connection: forget what was read, so the next load reads
// again as on a new connection.
export function refresh() {
  rest.conn = -1;
  reset();
}

// Koma on the camera, from the settings block: the release the firmware tag names (words.js komaRelease; null for an
// earlier build's tag and for Rev024M), the menu layout, the raw tag (words.js komaOnCamera, and Details).
// state: 'reading' | 'koma' | 'none' (no PARAM.CUR, or a block without the KOMA header) | 'error' | 'card'
export function komaFacts() {
  void S.rev;
  if (S.source === 'card') return {state: 'card'};
  if (S.source !== 'camera' || !B.orig) {
    if (S.reading || !S.readError) return {state: 'reading'};
    return {state: S.readCode === 'no_param_cur' ? 'none' : 'error'};
  }
  if (E.komaVersion(B.orig) === null) return {state: 'none'};
  const {version, tag} = komaRelease(B.orig);
  const M = E.getMap(), f = M && M.byName.menu_layout;
  return {state: 'koma', version, tag, layout: f ? showValue(f, B.orig) : null};
}
