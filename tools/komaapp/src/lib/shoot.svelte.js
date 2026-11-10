// Shoot: remote control, live view, the shutter, interval shooting, the exposure controls and the remote buttons.
// Ported from koma-setup.html's Shoot, Remote and Exposure code; same bridge calls, same timings, same wording where
// the page had it. Remote control goes on when Shoot opens (POST /tether/start) and off when it is left or the page
// closes (POST /tether/stop, by beacon). The bridge lets every key go itself on every path.
import {bridgeJson, postJson, bridgeUrl, b64decode, beaconStop, lsGet, lsSet} from './bridge.js';
import {cam, connected} from './camera.svelte.js';
import * as E from '../engine/engine.js';
import {clean, plural} from './words.js';

export const TAB_ID = Math.random().toString(36).slice(2, 10);
// A fresh boot arms in under a second, a second session can take about 20 s (the bridge waits up
// to 30 s). The request gets 45 s and a ticking line shows meanwhile; nothing is called a failure before the bridge says so.
const ARM_TIMEOUT = 45000, SHOOT_TIMEOUT = 100000;
// Its status line carries Try again (it calls sync), so the sentence does not say it.
export const NOT_AWAKE = 'The camera did not wake for remote control in 30 seconds. If the camera shows its menu, close it first. '
  + 'If it still does not wake, unplug the cable, restart the camera, then plug it in again.';

export const sh = $state({
  view: false,          // Shoot is on screen
  active: false, armed: false, starting: false, busy: false, conn: -1,
  wakeS: -1,            // seconds waking (-1: not waking)
  wakeFailed: false,    // the last wake ended without the camera armed: the shutter waits for Try again
  status: '', error: '', replies: [],
  shots: [], cur: null,
  // live view
  lvOn: false, lvWant: true, lvSeen: false, lvMode: 1, lvPaused: false, lvOver: '', lvFps: 0, lvRate: '',
  held: '',            // the last live frame (a data URL), kept on screen at 0.6 while the camera is away
  lostAt: 0,           // Date.now when the camera left while Shoot was open
  // remote panel: camera screen layer over the picture
  osdWanted: false, osdOn: false, osdFailed: false,
  // interval
  iv: null, ivRunning: false,
  // exposure
  x: {cam: null, want: {}, reading: false, err: '', msg: ''},
  look: null,           // {name, kind} of the current look, from the camera's RAM
});

let startingP = null, STOPPING = Promise.resolve();
const LV = {gen: 0, timer: null, retry: null, tries: 0};
const WAKE = {t0: 0, n: 0, timer: null, last: 0};
let liveImg = null, osdImg = null;

function stopping(p) { STOPPING = Promise.all([STOPPING, p.catch(() => {})]); return STOPPING; }

function wake(p) {
  if (!WAKE.n++) {
    WAKE.t0 = Date.now(); clearInterval(WAKE.timer);
    WAKE.timer = setInterval(() => { sh.wakeS = Math.floor((Date.now() - WAKE.t0) / 1000); }, 500);
  }
  sh.wakeS = 0;
  const done = () => { if (--WAKE.n <= 0) { WAKE.n = 0; WAKE.t0 = 0; WAKE.last = Date.now(); clearInterval(WAKE.timer); sh.wakeS = -1; } };
  p.then(done, done);
  return p;
}

// ---------- remote control on / off
export function ensure() {
  if (sh.active && sh.armed) return Promise.resolve(true);
  if (!sh.view) return Promise.resolve(false);   // only an open Shoot turns remote control on
  if (!startingP) {
    sh.starting = true;
    startingP = wake(STOPPING.then(() => postJson('/tether/start', undefined, {timeout: ARM_TIMEOUT})))
      .then(d => {
        sh.active = !!d.ok; sh.armed = !!(d.ok && d.armed); sh.wakeFailed = !!(d.ok && !d.armed);
        if (d.ok && !sh.view) { startingP = null; stop(); return false; }
        if (!d.ok) { sh.replies = d.replies || []; sh.error = 'The camera did not accept remote control. Unplug the cable, plug it back in and try again.'; }
        else if (!d.armed) { sh.replies = d.replies || []; sh.error = NOT_AWAKE; }
        else if (sh.error === NOT_AWAKE) sh.error = '';
        return sh.armed;
      })
      .catch(e => { sh.error = e && e.status === 503 ? 'The camera disconnected. Check the cable.' : 'The camera did not answer. Check the cable and try again.'; return false; })
      .finally(() => { startingP = null; sh.starting = false; });
  }
  return startingP;
}

export function stop() {
  liveStop(true, true);
  osdOff();
  if (!sh.active && !startingP) return;
  sh.active = sh.armed = false;
  stopping(postJson('/tether/stop', undefined, {timeout: 10000}));
}

// A start still in flight counts too: the bridge finishes it after the page has gone, and nothing would turn it off.
// Shoot also stops asking: a status answer that lands while the page closes would otherwise start remote control again.
let hidView = false;
window.addEventListener('pagehide', () => {
  hidView = sh.view;
  sh.view = false;
  clearTimeout(LV.retry);
  if (!sh.active && !startingP) return;
  sh.active = sh.armed = false;
  beaconStop();
});
// Back from the browser's page cache with Shoot open: carry on.
window.addEventListener('pageshow', e => { if (e.persisted && hidView) { hidView = false; enter(); } });

// Shoot opened, or the camera came or went while it is open.
export function enter() {
  sh.view = true;
  sync();
}
export function leave() {
  sh.view = false;
  clearTimeout(IV.timer);
  stop();
}
export function sync() {
  if (!sh.view) return;
  if (!connected()) {
    sh.active = sh.armed = false; sh.status = ''; clearTimeout(focusTimer);
    if (sh.lvOn) { holdFrame(); liveStop(false, true); }
    if (!sh.lostAt) sh.lostAt = Date.now();
    osdOff();
    return;
  }
  sh.lostAt = 0;
  if (sh.conn !== cam.conn) {
    sh.conn = cam.conn; sh.active = sh.armed = false;
    if (sh.lvOn) liveStop(false, true);
    sh.x.cam = null; sh.look = null;
  }
  // a wake that failed is asked again only from its status line's Try again (which calls this)
  const again = sh.active && !sh.armed && sh.error === NOT_AWAKE;
  if (again) { sh.error = ''; sh.replies = []; sh.active = false; }
  if (!sh.active && !sh.busy && !startingP) {
    ensure().then(ok => {
      if (ok && sh.view && connected()) {
        if (sh.lvWant && !sh.lvOn) liveStart(sh.lvMode);
        if (sh.osdWanted && !sh.osdOn) osdStart();
        if (!sh.x.cam && !sh.x.reading) xRead();
      }
    });
  }
  ivPoll();
}

// ---------- shutter
// Every photo of this session keeps the time it arrived on this computer (the camera's own clock may never have been
// set), for the photo sheet's title.
function notePhoto(p) { p.at = p.at || Date.now(); sh.shots = [p, ...sh.shots.filter(q => q.path !== p.path)]; sh.cur = p; }
// The line under the shutter at rest: the session count, when there is one.
const sessionLine = () => (sh.shots.length ? plural(sh.shots.length, 'photo', 'photos') + ' this session.' : '');
let focusTimer = null;
export function photoUrl(p) { return p.src || ''; }

export async function press(full) {
  if (sh.busy) return;
  sh.busy = true; sh.error = ''; sh.replies = [];
  clearTimeout(focusTimer);
  sh.status = full ? 'Taking the photo.' : 'Focusing.';
  if (full) livePause(true);
  const later = full ? setTimeout(() => { sh.status = 'Waiting for the camera to save the photo.'; }, 1500) : null;
  try {
    if (!(await ensure())) { sh.status = ''; return; }
    // fetch=1: the bridge returns the new JPEG with the answer. Card reads by /photos are refused while remote control is
    // on (a card read in shooting state drops live view), so this is the only way to show the shot here.
    const d = await postJson(full ? '/tether/shoot?fetch=1' : '/tether/focus', undefined, {timeout: SHOOT_TIMEOUT});
    if (d.photo && d.jpeg) d.photo.src = URL.createObjectURL(new Blob([b64decode(d.jpeg)], {type: 'image/jpeg'}));
    if (!full) {
      sh.status = d.ok ? 'Focused.' : sessionLine();
      // "Focused." for about 3 s, then the session count again
      if (d.ok) focusTimer = setTimeout(() => { if (sh.status === 'Focused.') sh.status = sessionLine(); }, 3000);
      if (!d.ok) { sh.replies = d.replies || []; sh.error = 'The camera did not accept the focus press.'; sh.active = sh.armed = false; }
      return;
    }
    if (d.outcome === 'shot' && d.photo) {
      notePhoto(d.photo);
      sh.status = sessionLine();
    } else if (d.outcome === 'no_file') {
      sh.status = '';
      sh.error = 'No photo arrived. The shutter did not fire; shooting from here needs Koma with the tether feature.';
      sh.replies = d.replies || [];
    } else if (d.outcome === 'not_armed') {
      sh.status = ''; sh.armed = false; sh.wakeFailed = true; sh.error = NOT_AWAKE; sh.replies = d.replies || [];
    } else {
      sh.status = '';
      sh.error = 'The camera did not take the shutter press. Unplug the cable, plug it back in and try again.';
      sh.replies = d.replies || []; sh.active = sh.armed = false;
    }
  } catch (e) {
    sh.status = ''; sh.active = sh.armed = false;
    sh.error = e && e.status === 503 ? 'The camera disconnected. Check the cable.' : 'The camera did not answer. Check the cable and try again.';
  } finally { clearTimeout(later); sh.busy = false; if (full) livePause(false); }
}

// ---------- live view: an <img> on the bridge's multipart /live stream (mode 1 the camera's JPEG, mode 0 grey)
export function attachLive(img) { liveImg = img; if (sh.lvOn && img) liveOpen(LV.gen); }
export function attachOsd(img) { osdImg = img; if (sh.osdOn && img) osdStart(); }

function liveRate(st) {
  if (!st || !st.running || !st.fps) return '';
  const kb = st.bytes_per_s / 1000;
  return Math.round(st.fps) + ' fps, ' + (kb >= 1000 ? (kb / 1000).toFixed(1) + ' MB/s' : Math.round(kb) + ' KB/s') + (st.torn ? ', torn frames ' + st.torn : '');
}

async function liveTick(gen) {
  if (gen !== LV.gen || !sh.lvOn) return;
  try {
    const st = await bridgeJson('/live/status', {timeout: 3000});
    if (gen !== LV.gen || !sh.lvOn) return;
    const mine = !st.client || st.client === TAB_ID;
    if (st.running && mine) {
      sh.lvSeen = true; LV.tries = 0;
      sh.lvPaused = !!st.paused;
      // a pause for the shutter is said once, by the line under the shutter; the dimmed picture shows the rest of it
      if (!sh.busy) sh.lvOver = st.paused && st.reason === 'camera busy' ? 'The camera is busy.' : '';
      sh.lvFps = st.running && !st.paused && st.fps ? Math.round(st.fps) : 0;
      sh.lvRate = liveRate(st);
    } else if (st.running && sh.lvSeen) {
      liveStop(false); sh.lvWant = false;
      sh.lvOver = 'Live view is open in another window. Choose Resume live view to show it here.';
      return;
    } else if (sh.lvSeen) {
      if (st.ended === 'stopped') sh.armed = false;
      sh.lvSeen = false;
      liveRetry(gen);
      return;
    }
  } catch (e) { /* the status poll reports a lost camera or bridge */ }
  LV.timer = setTimeout(() => liveTick(gen), 1000);
}

function liveOpen(gen) {
  if (!liveImg) return;
  liveImg.onload = () => { if (gen === LV.gen) { sh.lvSeen = true; sh.held = ''; LV.tries = 0; if (!sh.busy) sh.lvOver = ''; } };
  liveImg.onerror = () => { if (gen === LV.gen) liveRetry(gen); };
  liveImg.src = bridgeUrl('/live?mode=' + sh.lvMode + '&cid=' + TAB_ID + '&t=' + Date.now());
  clearTimeout(LV.timer); LV.timer = setTimeout(() => liveTick(gen), 1000);
}

function liveRetry(gen) {
  if (gen !== LV.gen || !sh.lvOn) return;
  if (!connected()) { liveStop(false, true); return; }
  if (++LV.tries > 6) {
    liveStop(false);
    sh.lvOver = sh.lvMode === 1 ? 'The camera is not sending live view. It needs Koma with the live view feature.' : 'The camera is not sending live view.';
    return;
  }
  sh.lvOver = "Waiting for the camera's picture.";
  sh.lvFps = 0;
  clearTimeout(LV.retry);
  LV.retry = setTimeout(async () => {
    if (gen !== LV.gen || !sh.lvOn) return;
    if (LV.tries >= 2) sh.armed = false;
    if (!(await ensure())) { if (gen === LV.gen && sh.lvOn) { liveStop(false); sh.lvOver = 'Live view is off.'; } return; }
    if (gen === LV.gen && sh.lvOn) liveOpen(gen);
  }, Math.min(1000 * LV.tries, 4000));
}

export async function liveStart(mode) {
  if (!connected()) return;
  sh.lvMode = mode; sh.lvOn = true; sh.lvWant = true; sh.lvSeen = false; LV.tries = 0;
  const gen = ++LV.gen;
  sh.lvOver = sh.armed ? 'Starting live view.' : 'Waiting for the camera to wake up.';
  if (!(await ensure()) || gen !== LV.gen) { if (gen === LV.gen) { sh.lvOn = false; sh.lvOver = 'Live view is off.'; } return; }
  liveOpen(gen);
}

export function liveMode(mode) {
  if (mode === sh.lvMode && sh.lvOn) return;
  sh.lvMode = mode;
  if (!sh.lvOn) return;
  sh.lvSeen = false; LV.tries = 0; clearTimeout(LV.retry);
  liveOpen(++LV.gen);
}

// The camera left mid-stream: keep its last frame (drawn once to a canvas; the stream itself ends with the img's src).
function holdFrame() {
  try {
    if (!liveImg || !liveImg.naturalWidth) return;
    const c = document.createElement('canvas');
    c.width = liveImg.naturalWidth; c.height = liveImg.naturalHeight;
    c.getContext('2d').drawImage(liveImg, 0, 0);
    sh.held = c.toDataURL('image/jpeg', 0.9);
  } catch (e) { sh.held = ''; }
}

export function liveStop(tellBridge, keepWant) {
  const was = sh.lvOn;
  sh.lvOn = false; LV.gen++; clearTimeout(LV.timer); clearTimeout(LV.retry);
  if (!keepWant) sh.lvWant = false;
  if (liveImg) { liveImg.onload = liveImg.onerror = null; liveImg.removeAttribute('src'); }
  sh.lvPaused = false; sh.lvFps = 0; sh.lvRate = '';
  sh.lvOver = 'Live view is off.';
  if (was && tellBridge !== false) stopping(postJson('/live/stop?mode=1', undefined, {timeout: 5000}));
}

export function liveToggle() { if (sh.lvOn) liveStop(); else liveStart(sh.lvMode); }

// While the shutter is busy its line says "Taking the photo."; the live line stays empty, so it is said once.
function livePause(on) {
  sh.lvPaused = on && sh.lvOn;
  if (sh.lvOn) sh.lvOver = on ? '' : (sh.lvSeen ? '' : "Waiting for the camera's picture.");
}

// ---------- the camera's own screen (menus and icons, /live?mode=2) over the picture, for the Remote panel
let osdRetry = null;
export function osdStart() {
  sh.osdWanted = true;
  if (!osdImg || !connected()) return;
  clearTimeout(osdRetry);
  osdImg.onload = () => { sh.osdOn = true; sh.osdFailed = false; };
  osdImg.onerror = () => {
    sh.osdOn = false; sh.osdFailed = true;
    if (osdImg) { osdImg.onload = osdImg.onerror = null; osdImg.removeAttribute('src'); }
    clearTimeout(osdRetry); osdRetry = setTimeout(() => { if (sh.osdWanted && sh.view) osdStart(); }, 3000);
  };
  osdImg.src = bridgeUrl('/live?mode=2&t=' + Date.now());
}
export function osdOff(tell) {
  clearTimeout(osdRetry);
  if (osdImg) { osdImg.onload = osdImg.onerror = null; osdImg.removeAttribute('src'); }
  const was = sh.osdOn;
  sh.osdOn = false; sh.osdFailed = false;
  if (tell && was) postJson('/live/stop?mode=2', undefined, {timeout: 5000}).catch(() => {});
}
export function osdToggle(on) {
  sh.osdWanted = on;
  if (on) { if (sh.active) osdStart(); } else osdOff(true);
}

// ---------- remote buttons: POST /keys/tap {code}, the dial POST /dial {n}; queued so presses land in order
export const RKEYS = {ArrowUp: 11, ArrowDown: 12, ArrowRight: 13, ArrowLeft: 14, Enter: 23, Escape: 22};
let rQueue = Promise.resolve();
function rPost(path, body, what) {
  rQueue = rQueue.then(async () => {
    if (!(await ensure()) && !sh.active) return;
    try {
      const d = await postJson(path, body, {timeout: 10000});
      if (d.ok) { sh.error = ''; sh.replies = []; }
      else { sh.error = 'The camera did not take ' + what + '.'; sh.replies = d.replies || []; }
    } catch (e) {
      if (e.status === 409 && e.data && e.data.code === 'remote_off') {
        sh.active = sh.armed = false;
        if (await ensure()) {
          try { await postJson(path, body, {timeout: 10000}); sh.error = ''; } catch (e2) { sh.error = 'The camera did not take ' + what + '.'; }
        }
      } else sh.error = 'The camera did not answer. Check the cable and try again.';
    }
  });
  return rQueue;
}
export function tap(code) { if (sh.active) return rPost('/keys/tap', {code}, 'that button press'); }
export function dial(n) { return rPost('/dial', {n}, 'the dial setting'); }

// ---------- interval: the bridge runs the shoots itself; the app polls GET /tether/interval once a second
const IV = {timer: null};
async function ivPoll() {
  clearTimeout(IV.timer);
  try {
    const st = await bridgeJson('/tether/interval', {timeout: 5000});
    ivShow(st);
    if (st.running && sh.view) IV.timer = setTimeout(ivPoll, 1000);
  } catch (e) { if (sh.view) IV.timer = setTimeout(ivPoll, 3000); }
}
function ivShow(st) {
  if (!st) return;
  sh.iv = st;
  sh.ivRunning = !!(st && st.running);
  if (st && st.last && st.last.photo && st.last.ok) {
    const path = st.last.photo, [folder, name] = path.split('/');
    if (!sh.shots.some(p => p.path === path)) notePhoto({path, folder, name});
  }
}
export function ivText(st) {
  if (!st || !st.count) return '';
  let text = 'Photo ' + st.done + ' of ' + st.count;
  if (st.failed) text += ', ' + st.failed + ' did not arrive';
  if (st.running && st.next_in_s != null && st.next_in_s >= 0.5) text += ' · Next in ' + Math.ceil(st.next_in_s) + ' s';
  if (!st.running && st.ended) text += ' · ' + ({finished: 'Done', cancelled: 'Stopped', 'tether stopped': 'Stopped', 'camera lost': 'Camera disconnected'}[st.ended] || 'Stopped');
  return text;
}
export async function ivStart(every, count) {
  sh.error = ''; sh.status = ''; clearTimeout(focusTimer);
  if (!(every >= 1 && every <= 86400) || !(count >= 1 && count <= 9999) || !Number.isInteger(count)) {
    sh.error = 'Every: at least 1 second. Photos: 1 to 9999.'; return;
  }
  if (!(await ensure())) return;
  try {
    const d = await postJson('/tether/interval', {every_s: every, count}, {timeout: 10000});
    ivShow(d.interval); ivPoll();
  } catch (e) { sh.error = 'The interval did not start. Check the cable and try again.'; sh.replies = [{command: 'POST /tether/interval', reply: e.message || String(e)}]; }
}
export async function ivStop() {
  try { ivShow((await bridgeJson('/tether/interval', {method: 'DELETE', timeout: 10000})).interval); } catch (e) { /* poll shows it */ }
  ivPoll();
}

// ---------- exposure: the camera's EV, ISO, white balance and metering, live (GET /ram, POST /ram)
// After an EV change the camera answers -KSET busy for up to about 9 s; the app keeps the new
// value on screen and sends it again every second for up to 25 s before it shows what the camera has.
const XP_LO = 0x30, XP_LEN = 0x5a - 0x30 + 1, XP_WBD = 0xa6, RCP_ACT = 0x8c0;
export const XP = [
  {k: 'ev', name: 'exposure_comp', off: 0x53, kind: 'ev', hw: true, vals: [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25]},
  {k: 'iso', name: 'iso', off: 0x59, kind: 'seg', vals: [0, 1, 2, 4, 6, 7, 8], short: t => t.replace(/^ISO\s*/, '')},
  {k: 'wb', name: 'white_balance', off: 0x54, kind: 'sel', vals: [0, 1, 2, 3, 4, 5, 7]},
  {k: 'wbd', name: 'wb_detail', off: XP_WBD, kind: 'sel', vals: Array.from({length: 17}, (_, i) => i), when: v => v[0x54] === 7},
  {k: 'met', name: 'photometry', off: 0x50, kind: 'seg', vals: [2, 0, 1]},
];
const XP_IMG = 0x30;
const XP_BUSY = "The camera is busy. Close the camera's menu and try again.";
const XP_RETRY_MS = 25000;
const XS = {timer: null, flushing: false, again: false, busySince: 0};

export function xVal(off) { const x = sh.x; return off in x.want ? x.want[off] : x.cam ? x.cam[off] : undefined; }
export function xLabel(c, v) {
  const M = E.getMap(), f = M && M.byName[c.name];
  const t = f && f.values && f.values[v] !== undefined ? clean(f.values[v]).trim() : v === undefined ? '' : String(v);
  return c.short ? c.short(t) : t;
}
export function xFieldLabel(c) { const M = E.getMap(), f = M && M.byName[c.name]; return f ? clean(f.label) : c.k.toUpperCase(); }

function lookFrom(cam) {
  const M = E.getMap();
  const act = cam[RCP_ACT];
  const rf = M && M.byName.recipe_rcp_active;
  if (act && rf && rf.values[act] !== undefined) return {name: clean(rf.values[act]), kind: 'look'};
  const f = M && M.byName.img_set;
  const v = cam[XP_IMG];
  if (f && f.values[v] !== undefined) {
    const n = clean(f.values[v]);
    return {name: n === 'NORMAL' ? 'STANDARD' : n, kind: 'stock'};  // NORMAL is shown as STANDARD (Look IA)
  }
  return null;
}

export async function xRead() {
  const x = sh.x;
  if (x.reading) return;
  x.reading = true; x.err = ''; x.msg = '';
  try {
    const a = await bridgeJson('/ram?off=' + XP_LO + '&len=' + XP_LEN, {timeout: 15000});
    const b = await bridgeJson('/ram?off=' + XP_WBD + '&len=1', {timeout: 15000});
    let c = null;
    try { c = await bridgeJson('/ram?off=' + RCP_ACT + '&len=1', {timeout: 15000}); } catch (e) { c = null; }
    const camv = {}, da = b64decode(a.data), db = b64decode(b.data);
    for (let i = 0; i < da.length; i++) camv[XP_LO + i] = da[i];
    camv[XP_WBD] = db[0];
    if (c) camv[RCP_ACT] = b64decode(c.data)[0];
    x.cam = camv; x.want = {};
    sh.look = lookFrom(camv);
  } catch (e) {
    x.cam = null;
    x.err = e.data && e.data.code === 'not_supported'
      ? 'These cannot be changed from here. That needs Koma with live settings.'
      : "The camera's settings could not be read. Check the cable and try again.";
  }
  x.reading = false;
}

export function xPick(off, v) {
  const x = sh.x;
  if (!x.cam) return;
  if (x.cam[off] === v) delete x.want[off]; else x.want[off] = v;
  x.err = ''; x.msg = '';
  clearTimeout(XS.timer); XS.timer = setTimeout(xFlush, 150);
}

async function xFlush() {
  const x = sh.x;
  if (!x.cam) return;
  if (XS.flushing) { XS.again = true; return; }
  const offs = Object.keys(x.want).map(Number).filter(o => x.want[o] !== x.cam[o]);
  if (!offs.length) { XS.busySince = 0; return; }
  XS.flushing = true;
  let retry = false;
  try {
    if (!(await ensure())) throw Object.assign(new Error('no remote'), {status: 0});
    for (const off of offs) {
      const v = x.want[off];
      if (v === undefined || v === x.cam[off]) continue;
      if (!XS.busySince) x.msg = 'Sending to the camera.';
      await postJson('/ram', {off, val: v}, {timeout: 20000});
      x.cam[off] = v;
      if (x.want[off] === v) delete x.want[off];
    }
    x.err = ''; x.msg = ''; XS.busySince = 0;
  } catch (e) {
    const d = e.data || {};
    const busy = e.status === 409 && d.code === 'camera_busy';
    if (busy && sh.view && (!XS.busySince || Date.now() - XS.busySince < XP_RETRY_MS)) {
      XS.busySince = XS.busySince || Date.now();
      x.err = ''; x.msg = 'The camera is still busy with the last change. Sending again.';
      retry = true;
    } else {
      x.want = {}; XS.busySince = 0; x.msg = '';
      x.err = busy ? XP_BUSY
        : d.code === 'not_supported' ? 'The camera does not take live changes. That needs Koma with live settings.'
        : e.status === 403 || e.status === 422 ? 'The camera rejected that value.'
        : 'The change did not reach the camera. Check the cable and try again.';
    }
  } finally {
    XS.flushing = false;
    if (retry) { clearTimeout(XS.timer); XS.timer = setTimeout(xFlush, 1000); }
    else if (XS.again) { XS.again = false; xFlush(); }
  }
}

// Seen notes (first-use hints), as on the page
export const notes = $state({shoot: lsGet('koma.shootNoteSeen') === '1'});
export function noteSeen() { notes.shoot = true; lsSet('koma.shootNoteSeen', '1'); }
