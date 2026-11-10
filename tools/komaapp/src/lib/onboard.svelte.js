// Setup (the first-run wizard) and Install / Update Koma: one flow engine, four plans.
//   wizard   Welcome, Your camera, Prepare card, Install Koma, Connect USB, Pick a look, Done
//   koma     Welcome, Your camera, Pick a look, Done        (a camera already running Koma: nothing to write)
//   update   Prepare card, On the camera, Connect USB, Done        (Settings -> Update Koma -> Install / Update Koma)
//   official Prepare card, On the camera, Connect USB, Done        (Settings -> Update Koma -> Return to official firmware)
// The job (st.mode) comes from what the camera runs: install (the official firmware: the Koma 1 firmware, then Koma, in
// two card trips as release/docs/install.md documents), update (Koma's file only), official (Ricoh's 2.40) or none.
// The state is saved after every move (localStorage 'koma.setup'), so a reload or a closed tab resumes on the same
// screen. Nothing here writes the camera over USB except Pick a look (-KSET, onboardLook.js). The card is written on this
// computer (onboardCard.js). Bridge calls: GET /status (camera.svelte.js), GET /komalog, GET /settings, GET /photos,
// GET /photos/<F>/<N>, GET /install, GET /install/QRFLECT3, POST /install/stock, the package at its static route.
import {bridgeJson, bridgeFetch, b64decode, lsGet, lsSet} from './bridge.js';
import {cam, cameraName, firmware, connectProblem} from './camera.svelte.js';
import {CONNECT, earlierTag, releaseOfTag, compareRelease} from './words.js';
import {komaVersion, ksioTag} from '../engine/engine.js';
import {makerTag, firmwareOfTag, generationOfTag} from './onboardExif.js';
import {sha256} from './onboardCard.js';
import {W} from './onboardWords.js';
import {refresh as refreshOverview} from './overview.svelte.js';

const KEY = 'koma.setup';
export const PLANS = {
  wizard: ['welcome', 'camera', 'card', 'install', 'connect', 'look', 'done'],
  koma: ['welcome', 'camera', 'look', 'done'],
  update: ['card', 'install', 'connect', 'done'],
  official: ['card', 'install', 'connect', 'done'],
};
// The screens of step 4 (Install Koma / On the camera), per job:
//   install  the keyed power-on (8 screens), then the second card trip (Koma's file on, the update file off) and the
//            card back in the camera: release/docs/install.md flashes QRFLECT3 alone, then adds GRMOD.BIN
//   official the keyed power-on (8)
//   update   the card back in the camera (1)
//   none     nothing to do (1)
// Where Koma goes (st.where, chosen on the trip that writes Koma's file): 'card' (GRMOD.BIN on the card; the default) or
// 'irom', the camera's internal memory, so Koma starts with any card, 64 GB too. The /IROM route is the existing card
// installer (docs/install.md "Internal memory"): the installer as
// GRMOD.BIN and the package as KOMAPKG.BIN on the 2 GB card, one start of about a minute ('run'), then the card back
// here: Koma reads IRODUMP.TXT and takes the installer off ('card3'), so it cannot hide /IROM. No USB write.
export const FLASH = 8;
export const FLASH_COMMIT = 5;   // ▶ then MENU/OK: the press that starts the write, the point of no return
export const FLASH_RUNS = 6;     // the camera writes its firmware; nothing to press
export function screenIds(mode, where = st.where) {
  const flash = Array.from({length: FLASH}, (_, i) => 'flash');
  const irom = where === 'irom' ? ['run', 'card3'] : [];
  if (mode === 'install') return [...flash, 'card', ...irom, 'back'];
  if (mode === 'official') return flash;
  if (mode === 'update') return [...irom, 'back'];
  return ['none'];
}
// The start that runs the installer: on for this long, then off.
export const IROM_RUN_S = 60;
// Koma cannot see the camera while it writes (the cable is out). The user says when the lamp has stopped, and that
// answer is offered only after this long, so the write cannot be clicked through: Ricoh's updater dialog says playback
// appears after about 2 minutes, and the lamp blinks for about 2 to 3 minutes. window.KOMA_FLASH_MIN_S shortens it for the tests (as window.KOMA_WAIT_S does for Apply).
export const flashMinMs = () => 1000 * ((typeof window !== 'undefined' && window.KOMA_FLASH_MIN_S) || 150);

function fresh(plan) {
  return {v: 1, seen: true, active: true, done: false, plan, step: 0, sub: 0, mode: plan === 'official' ? 'official' : null,
    cam: null, pkg: null, backup: null, card: null, card2: null, card3: null, where: 'card', flashFrom: 0, waitFrom: 0,
    confirmed: null, look: null, before: null, stopped: false};
}

function load() {
  try { const s = JSON.parse(lsGet(KEY) || 'null'); return s && s.v === 1 ? s : null; } catch (e) { return null; }
}

export const st = $state(load() || {v: 1, seen: false, active: false, done: false, plan: 'wizard', step: 0, sub: 0, mode: null,
  cam: null, backup: null, card: null, card2: null, card3: null, where: 'card', flashFrom: 0, waitFrom: 0, confirmed: null,
  look: null, before: null, stopped: false});
export function save() { lsSet(KEY, JSON.stringify(st)); }
export const firstRun = () => !load();
// A setup or update left part way (Exit, Stop here, a closed tab): it can go on from the same screen.
export const resumable = () => !st.active && !st.done && !!st.seen && !!PLANS[st.plan] && (st.step > 0 || st.sub > 0);
// The held-key screens of the keyed power-on (2 to 4) cannot be resumed: nobody knows what the camera did while the page
// was away. Going on starts again from "Set the mode dial to Movie" (screen 1), the last state that holds by itself.
// Screens 5 and 6 (MENU/OK pressed, the camera writing) keep their place.
function settleSub() {
  if (PLANS[st.plan] && PLANS[st.plan][st.step] === 'install' && (st.mode === 'install' || st.mode === 'official')
    && st.sub >= 2 && st.sub <= 4) st.sub = 1;
}
export function resume() {
  settleSub();
  st.active = true; st.stopped = false; save();
  if (location.hash !== '#/setup') location.hash = '#/setup';
  onb.view = 'setup';
}
// The one way into setup from anywhere in the app (Overview, Update Koma, an empty state): goes on with a setup left part
// way, else starts one.
export function setupEntry() {
  const r = resumable();
  return {label: r ? W.update.resume[st.plan] || W.update.resume.wizard : W.setup, onclick: () => (r ? resume() : open('wizard')),
    href: '#/setup', resuming: r};
}

// ---------------------------------------------------------------- what App.svelte shows
function routeOf() {
  const h = location.hash.replace(/^#\/?/, '');
  if (h === 'gr' || h === 'gallery') return 'gallery';
  if (h === 'setup' || h.startsWith('setup/')) return 'setup';
  return null;
}
export const onb = $state({view: routeOf()});
// #/setup goes on with a setup left part way, else starts one.
window.addEventListener('hashchange', () => {
  onb.view = routeOf();
  if (onb.view === 'setup' && !st.active) { if (resumable()) resume(); else open('wizard'); }
});
// A setup left part way comes back after a reload.
if (st.active) { settleSub(); save(); }
if (st.active && onb.view !== 'setup' && onb.view !== 'gallery') location.hash = '#/setup';
if (onb.view === 'setup' && !st.active) {
  if (resumable()) { settleSub(); Object.assign(st, {active: true, stopped: false}); } else Object.assign(st, fresh('wizard'));
  save();
}

// extra: what the opener already knows (Update Koma: the mode, the camera, the backup, the newest photo).
export function open(plan, extra = {}) {
  Object.assign(st, fresh(plan), extra);
  save();
  resetMemory();
  if (location.hash !== '#/setup') location.hash = '#/setup';
  onb.view = 'setup';
}
// Exit from the short plan (a camera already on Koma) ends it: nothing is left half done on the camera.
export function exit() {
  st.active = false;
  if (st.plan === 'koma' || (st.plan === 'wizard' && PLANS.wizard[st.step] === 'done')) st.done = true;
  save();
  refreshOverview();
  location.hash = st.plan === 'wizard' || st.plan === 'koma' ? '#/home' : '#/settings/update';
}
export function finish(to = '#/home') {
  st.active = false; st.done = true; save();
  refreshOverview();
  location.hash = to;
}

// The screens of step 4 where stopping is not possible: MENU/OK starts the write, then the camera writes.
export const committed = () => stepId() === 'install' && (st.mode === 'install' || st.mode === 'official')
  && (st.sub === FLASH_COMMIT || st.sub === FLASH_RUNS);

export const plan = () => PLANS[st.plan] || PLANS.wizard;
export const stepId = () => plan()[st.step];
export function go(i, sub = 0) {
  const to = Math.max(0, Math.min(plan().length - 1, i));
  if (plan()[to] === 'connect' && to !== st.step) st.waitFrom = 0;   // Connect USB counts from its own start
  st.step = to; st.sub = sub; save();
}
export function next() { go(st.step + 1); }
export function back() {
  if (stepId() === 'install' && st.sub > 0) { st.sub--; save(); return; }
  if (st.step === 0) return;
  const prev = plan()[st.step - 1];
  go(st.step - 1, prev === 'install' ? Math.max(0, screens() - 1) : 0);
}
// The number of on-camera screens for this job.
export const screens = () => screenIds(st.mode).length;

// Why no camera is open, in Connect's words (words.js CONNECT), for the setup steps that wait for one: {text, sub} or
// null when the bridge names no problem (then the step's own waiting line stands).
export function problemWords() {
  const p = cam.state === 'cam' ? null : connectProblem();
  if (!p) return null;
  if (p.kind === 'two') return {text: CONNECT.two(p.n), sub: ''};
  if (p.kind === 'open') return {text: CONNECT.open.text, sub: CONNECT.open[p.sub] || CONNECT.open.other};
  if (p.kind === 'mode') return {text: CONNECT.mode.text(p.mode), sub: CONNECT.mode.sub};
  if (p.kind === 'usb') return {text: CONNECT.usb.text, sub: CONNECT.usb.sub};
  return null;
}

// Koma runs on the camera (its log or its settings say so): the short plan fits it.
export const komaRuns = kind => ['koma-current', 'koma-older', 'koma-other', 'koma-unknown'].includes(kind);

// First run: when no setup record exists, setup opens by itself once the bridge answers, at the address the launcher
// opens (no hash; a deep link such as #/home or #/settings keeps its page). A camera already running Koma gets the short
// plan (Welcome, Your camera, Pick a look, Done); any other camera, or none yet, the full one (Your camera waits for
// the camera with the plug-in figure). The record is written only by a setup the user has seen.
let firstRunChecked = false;
export async function firstRunCheck() {
  if (firstRunChecked || !firstRun() || (cam.state !== 'cam' && cam.state !== 'nocam')) return;
  firstRunChecked = true;
  if (location.hash.replace(/^#\/?/, '')) return;
  if (cam.state !== 'cam') { open('wizard'); return; }
  try {
    const [c, p] = await Promise.all([settled(() => readCamera({photo: 'auto'})), readPackage().catch(() => null)]);
    if (!firstRun() || location.hash.replace(/^#\/?/, '')) return;   // the user went somewhere meanwhile
    const {cur, ...facts} = c;
    const kind = classify(c, pkgFacts(p));
    open(komaRuns(kind) ? 'koma' : 'wizard', {cam: {...facts, kind}});
  } catch (e) {
    if (firstRun() && !location.hash.replace(/^#\/?/, '')) open('wizard');
  }
}

// ---------------------------------------------------------------- the camera
// Model and firmware from GET /status; Koma's build from KOMALOG.TXT; Koma's settings export (and the backup) from
// PARAM.CUR; the firmware tag from the newest photo when neither file says Koma (photo: 'auto'), always (true) or never.
// gen: the Koma firmware generation from PARAM.CUR's tag or the photo's (1, 2, or null when neither names one).
export async function readCamera({photo = 'auto'} = {}) {
  const out = {at: Date.now(), model: cameraName(), fw: firmware(), build: null, kv: null, tag: null, cur: null,
    photoName: null, photoTag: null, gen: null};
  try { out.build = (await bridgeJson('/komalog', {timeout: 20000})).build_id || null; }
  catch (e) { if (e.status !== 404) throw e; }
  try {
    const s = await bridgeJson('/settings', {timeout: 30000});
    const b = b64decode(s.data);
    out.kv = komaVersion(b);
    const t = ksioTag(b);
    out.tag = t ? t.replace(/[\0 ]+$/, '') : null;
    out.cur = s.data;
  } catch (e) { if (!(e.data && e.data.code === 'no_param_cur')) throw e; }
  if (photo === true || (photo === 'auto' && !out.build && out.kv === null)) Object.assign(out, await newestPhotoTag());
  out.gen = generationOfTag(out.tag) || generationOfTag(out.photoTag);
  return out;
}

export async function newestPhotoTag() {
  const d = await bridgeJson('/photos', {timeout: 60000});
  const p = d.photos && d.photos[0];
  if (!p) return {photoName: null, photoTag: null};
  const r = await bridgeFetch('/photos/' + p.path.split('/').map(encodeURIComponent).join('/'), {timeout: 60000});
  if (!r.ok) throw Object.assign(new Error('photo'), {status: r.status});
  return {photoName: p.path, photoTag: makerTag(new Uint8Array(await r.arrayBuffer()))};
}

// What the camera is, for this package: one of wrong-firmware, koma-current (this build), koma-older (a build this
// package replaces: recorded as replaced, an earlier build's tag, or a release tag older than this package's),
// koma-other (any other build: not known to be older, so never offered as a downgrade), koma-unknown (Koma's
// settings, no log), official, koma-fw (the Koma firmware with no Koma file running), no-photo, tag-unknown.
export function classify(c, pkg) {
  if (!c) return null;
  if (c.fw && c.fw !== '2.40') return 'wrong-firmware';
  if (c.build) {
    if (pkg && pkg.build && c.build === pkg.build) return 'koma-current';
    return pkg && ((pkg.supersedes || []).includes(c.build) || olderThan(c, pkg)) ? 'koma-older' : 'koma-other';
  }
  if (c.kv !== null) return pkg && olderThan(c, pkg) ? 'koma-older' : 'koma-unknown';
  if (!c.photoName) return 'no-photo';
  const fw = firmwareOfTag(c.photoTag);
  return fw === 'official' ? 'official' : fw === 'koma' ? 'koma-fw' : 'tag-unknown';
}
// The camera's Koma is older than this package: an earlier (pre-1.0) build's tag, or a release tag below this package's
// release. A tag naming no release (Rev024M) proves nothing.
function olderThan(c, pkg) {
  const tag = c.tag || c.photoTag;
  if (earlierTag(tag)) return true;
  const rel = releaseOfTag(tag);
  return !!(rel && pkg.version && compareRelease(rel, pkg.version) < 0);
}
// What to do for that state. Koma is written over another build only when this package is known to replace it: a
// card package wins over the one in the camera's internal memory. Otherwise the camera
// keeps its build.
export function modeFor(kind) {
  if (kind === 'official') return 'install';
  if (kind === 'koma-older' || kind === 'koma-fw') return 'update';
  if (kind === 'koma-current' || kind === 'koma-other' || kind === 'koma-unknown') return 'none';
  return null;
}
export const isKoma = kind => ['koma-current', 'koma-older', 'koma-other', 'koma-unknown', 'koma-fw'].includes(kind);

// The backup: PARAM.CUR as it was read, kept in this browser; saved as a file when the user moves on.
export function backupName() {
  const d = new Date(), p = n => String(n).padStart(2, '0');
  return 'GR-settings-' + d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + '.dat';
}
export function keepBackup(c) {
  // Back to Your camera reads the camera again: the same bytes, already saved as a file, stay that backup
  if (c && c.cur) {
    if (!(st.backup && st.backup.saved && st.backup.b64 === c.cur)) st.backup = {name: backupName(), at: Date.now(), b64: c.cur, saved: false};
  } else if (!st.backup) st.backup = {skipped: true};
  save();
}

// ---------------------------------------------------------------- what goes on the card
let mem = {info: null, pkg: null, koma: null, official: null, installer: null};
export function resetMemory() { mem = {info: null, pkg: null, koma: null, official: null, installer: null}; }

export async function installInfo(force = false) {
  if (!mem.info || force) mem.info = await bridgeJson('/install', {timeout: 30000});
  return mem.info;
}

// The Koma package this computer has: the file the bridge serves, its fingerprint checked against SHA256SUMS, and the
// version and build id read from its manifest (tools/koma/inspector.py layout). The bridge builds the file from Ricoh's
// 2.40 update file; until it has that file (p.ready false) only the facts come back, with bytes null.
export async function readPackage() {
  if (mem.pkg) return mem.pkg;
  const info = await installInfo();
  const p = info.package;
  if (!p) return null;
  if (p.ready === false) {
    const facts = {bytes: null, sha: p.published_sha256, size: p.bytes, name: p.name, version: p.version, build: p.build_id,
      supersedes: p.supersedes || [], minFirmware: p.min_firmware_gen || 1};
    if (st.active && (!st.pkg || st.pkg.build !== facts.build)) { st.pkg = pkgFacts(facts); save(); }
    return facts;
  }
  const r = await bridgeFetch(p.route, {timeout: 60000});
  if (r.status === 409) throw Object.assign(new Error('no stock'), {why: 'stock'});
  if (!r.ok) throw new Error('package ' + r.status);
  const bytes = new Uint8Array(await r.arrayBuffer());
  const sha = await sha256(bytes);
  if (p.published_sha256 && sha !== p.published_sha256) throw Object.assign(new Error('package fingerprint'), {why: 'fetch'});
  const m = parseModule(bytes);
  // the release is the bridge's record of this package (its header string can predate the release number)
  mem.pkg = {bytes, sha, size: bytes.length, name: p.name, version: p.version || (m ? m.version : null), build: m ? m.build : null,
    supersedes: p.supersedes || [], minFirmware: p.min_firmware_gen || (m ? m.minFirmware : 1)};
  if (st.active && (!st.pkg || st.pkg.build !== mem.pkg.build)) { st.pkg = pkgFacts(mem.pkg); save(); }
  return mem.pkg;
}
export const pkgFacts = p => p ? {version: p.version, build: p.build, supersedes: p.supersedes || [],
  minFirmware: p.minFirmware || 1} : null;
// The package's version and build: from memory, else as last saved (a reload on the last steps).
export const packageMeta = () => mem.pkg ? {...pkgFacts(mem.pkg), sha: mem.pkg.sha} : st.pkg || null;

// GRMOD container (tools/koma/vendor/grmod.py): 'GRMD', body at 0x14; body 'KMOD' header (>HHIIIIII: format, flags,
// records, relocs, inits, resident, resident length); resident manifest 'KOMA', >HHII (format, features, build id,
// status), version string at +0x10 ('KOMA V1.0.0 1A2B3C4D G1': release, build id, minimum Koma firmware generation;
// no ' G<n>' means 1).
export function parseModule(b) {
  if (b.length < 0x40) return null;
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  if (dv.getUint32(0) !== 0x47524d44 || 0x14 + dv.getUint32(8) !== b.length) return null;
  const body = 0x14;
  if (String.fromCharCode(...b.subarray(body, body + 4)) !== 'KMOD') return null;
  const oRes = dv.getUint32(body + 20), resLen = dv.getUint32(body + 24);
  const r = body + oRes;
  if (!resLen || r + 0x28 > b.length || String.fromCharCode(...b.subarray(r, r + 4)) !== 'KOMA') return null;
  const build = dv.getUint32(r + 8).toString(16).toUpperCase().padStart(8, '0');
  let s = '';
  for (let i = 0; i < 24 && b[r + 0x10 + i]; i++) s += String.fromCharCode(b[r + 0x10 + i]);
  const m = /V(\d+\.\d+(?:\.\d+)?)/.exec(s), g = / G(\d+)$/.exec(s);
  return {build, version: m ? m[1] : null, minFirmware: g ? Number(g[1]) : 1, text: s};
}

export async function firmwareFile(kind) {
  if (mem[kind]) return mem[kind];
  const info = await installInfo();
  const r = await bridgeFetch('/install/QRFLECT3?image=' + kind, {timeout: 60000});
  if (r.status === 409) throw Object.assign(new Error('no stock'), {why: 'stock'});
  if (!r.ok) throw Object.assign(new Error('firmware ' + r.status), {why: 'fetch'});
  const bytes = new Uint8Array(await r.arrayBuffer());
  const sha = await sha256(bytes);
  const want = kind === 'koma' ? info.firmware.koma_sha256 : info.firmware.official_sha256;
  if (sha !== want) throw Object.assign(new Error('firmware fingerprint'), {why: 'fetch'});
  mem[kind] = {bytes, sha};
  return mem[kind];
}

// The /IROM installer the bridge serves (komainstall.INSTALLER), its fingerprint checked against the recorded one.
export async function installerFile() {
  if (mem.installer) return mem.installer;
  const info = await installInfo();
  const i = info.installer;
  if (!i) throw Object.assign(new Error('no installer'), {why: 'fetch'});
  const r = await bridgeFetch(i.route, {timeout: 60000});
  if (!r.ok) throw Object.assign(new Error('installer ' + r.status), {why: 'fetch'});
  const bytes = new Uint8Array(await r.arrayBuffer());
  const sha = await sha256(bytes);
  if (sha !== i.published_sha256) throw Object.assign(new Error('installer fingerprint'), {why: 'fetch'});
  mem.installer = {bytes, sha};
  return mem.installer;
}

// The installer's log (IRODUMP.TXT, 'key=value' lines): null when
// /IROM holds this package (result installed or same, the copy valid and its size this package's, end=1), else why not.
export function iromVerdict(text, size) {
  if (text === null || text === undefined) return 'irom-none';
  const kv = {};
  for (const line of String(text).split(/\r?\n/)) {
    const m = /^([a-z_]+)=(\S*)/.exec(line.trim());
    if (m && !(m[1] in kv)) kv[m[1]] = m[2];
  }
  if (kv.end !== '1') return 'irom-none';
  if (kv.result === 'unmounted') return 'irom-unmounted';
  if (kv.result === 'verify_fail') return 'irom-verify';
  if ((kv.result === 'installed' || kv.result === 'same') && kv.dst_valid === '1' && Number(kv.dst_size) === size) return null;
  return 'irom-refused';
}

export async function sendStock(file) {
  const r = await bridgeFetch('/install/stock', {method: 'POST', body: file, timeout: 120000,
    headers: {'Content-Type': 'application/octet-stream'}});
  mem.info = null; mem.koma = null; mem.official = null; mem.pkg = null;
  return r.ok;
}

// The card job: the files to write (with their fingerprints) and Koma's own files to remove. Two card trips for an
// install, as docs/install.md documents them:
//   install, trip 1  the Koma 1 firmware (QRFLECT3) alone; a stale GRMOD.BIN goes, so the first start after the flash
//                    runs the firmware by itself
//   install, trip 2  the package (GRMOD.BIN); the update file goes; KOMALOG.TXT goes, so the one read after the restart
//                    is new
//   update           the package; KOMALOG.TXT goes
//   official         Ricoh's QRFLECT3; Koma's files go (PARAM.CUR backed up first)
// With st.where 'irom', the package trip writes the installer as GRMOD.BIN and the package as KOMAPKG.BIN (and takes
// off an old IRODUMP.TXT, and KOMARM.TXT / KOMALS.TXT, which would turn the install into a removal or a listing);
// trip 3 reads IRODUMP.TXT (check) and takes the installer and KOMAPKG.BIN off.
async function packageFile() {
  const p = await readPackage();
  if (!p) throw Object.assign(new Error('no package'), {why: 'fetch'});
  if (!p.bytes) throw Object.assign(new Error('no stock'), {why: 'stock'});
  return p;
}
export async function cardJob(mode, trip = 1, where = st.where) {
  const files = [], remove = [];
  const pkgTrip = (mode === 'install' && trip === 2) || (mode === 'update' && trip === 1);
  if (trip === 3) {
    const p = await readPackage();
    if (!p) throw Object.assign(new Error('no package'), {why: 'fetch'});
    return {flash: false, files, remove: ['GRMOD.BIN', 'KOMAPKG.BIN'],
      check: {name: 'IRODUMP.TXT', test: text => iromVerdict(text, p.size)}};
  }
  if (pkgTrip && where === 'irom') {
    const [i, p] = await Promise.all([installerFile(), packageFile()]);
    files.push({name: 'GRMOD.BIN', bytes: i.bytes, sha: i.sha}, {name: 'KOMAPKG.BIN', bytes: p.bytes, sha: p.sha});
    if (mode === 'install') remove.push('QRFLECT3');
    remove.push('KOMALOG.TXT', 'IRODUMP.TXT', 'KOMARM.TXT', 'KOMALS.TXT');
    return {flash: false, files, remove};
  }
  if (mode === 'install' && trip === 1) {
    const k = await firmwareFile('koma');
    files.push({name: 'QRFLECT3', bytes: k.bytes, sha: k.sha});
    remove.push('GRMOD.BIN');
  } else if (mode === 'install' || mode === 'update') {
    const p = await packageFile();
    files.push({name: 'GRMOD.BIN', bytes: p.bytes, sha: p.sha});
    if (mode === 'install') remove.push('QRFLECT3');
    remove.push('KOMALOG.TXT', 'KOMAPKG.BIN');
  } else if (mode === 'official') {
    const o = await firmwareFile('official');
    files.push({name: 'QRFLECT3', bytes: o.bytes, sha: o.sha});
    remove.push('GRMOD.BIN', 'KOMALOG.TXT', 'PARAM.CUR');
  }
  return {flash: (mode === 'install' && trip === 1) || mode === 'official', files, remove};
}

// ---------------------------------------------------------------- after the restart
// install / update: Koma's own log names the build that started; it must be this package's, and every start stage must
// have completed (STAGE 3: tools/koma/features/telemetry.py). The log was taken
// off the card before the restart, so the one read now is new. official: the newest photo is new and carries the
// official tag.
export async function confirm() {
  if (st.mode === 'official') {
    const t = await newestPhotoTag();
    if (!t.photoName || (st.before && t.photoName === st.before)) return {ok: false, why: 'noNewPhoto'};
    const fw = firmwareOfTag(t.photoTag);
    if (fw === 'official') return {ok: true, tag: t.photoTag, photo: t.photoName};
    return {ok: false, why: fw === 'koma' ? 'stillKoma' : 'tagUnknown', tag: t.photoTag, photo: t.photoName};
  }
  let log = null;
  try { log = await bridgeJson('/komalog', {timeout: 20000}); } catch (e) { if (e.status !== 404) throw e; }
  if (!log || !log.build_id) return {ok: false, why: 'noKoma'};
  const p = packageMeta() || (await readPackage());
  if (p && p.build && log.build_id !== p.build) return {ok: false, why: 'otherBuild', build: log.build_id};
  if (log.stage !== 3) return {ok: false, why: 'incomplete', build: log.build_id, stage: log.stage};
  return {ok: true, build: log.build_id, stage: log.stage};
}

// A read right after the camera appears can meet the bridge still dropping the old USB session: try again a few times
// before calling it a failure (an answer such as 404 is not retried; it is the result).
export async function settled(fn, tries = 3, gapMs = 1500) {
  for (let i = 1; ; i++) {
    try { return await fn(); } catch (e) {
      if (i >= tries || (e && e.status === 404)) throw e;
      await new Promise(r => setTimeout(r, gapMs));
    }
  }
}

// Elapsed time as m:ss (1:02:10 past an hour), tabular in the view.
export function elapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? h + ':' + String(m).padStart(2, '0') + ':' + String(r).padStart(2, '0') : m + ':' + String(r).padStart(2, '0');
}
