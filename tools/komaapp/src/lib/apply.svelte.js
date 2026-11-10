// Apply to camera: check the camera still holds the block the changes were made on, send the settings file, restart
// the camera, wait for it to come back, read the settings back and confirm. The camera loads PARAM.DAT at its next
// start (Koma's settings import). Bridge calls: GET /settings, GET /ram (the live block, when the camera answers it),
// POST /settings, POST /reboot, GET /status (polled by camera.svelte.js).
//
// Done means: the
// read-back block equals the sent block everywhere outside the map's exclude_on_merge ranges (PARAM.CUR is rewritten
// at boot: counters, the KSIO status and the checksum differ even after a good import), AND the KSIO import status at
// 0x8dc is 1 (applied) or 3 (applied, firmware tag differed). The per-setting rows are shown, and a row that did not
// take also fails, but they are never the only check: an Apply with no rows (raw bytes from Advanced, or a Restore
// with nothing read first) is judged by the block rule alone.
import * as E from '../engine/engine.js';
import {postJson, bridgeJson, b64encode, b64decode} from './bridge.js';
import {cam, poll} from './camera.svelte.js';
import {readLive} from './settings.svelte.js';
import {showValue, clean, differs, plural, STATE} from './words.js';

export const STEPS = [
  {id: 'send', label: 'Sending the settings'},
  {id: 'restart', label: 'Restarting the camera'},
  {id: 'wait', label: 'Waiting for the camera'},
  {id: 'confirm', label: 'Reading back'},
];

export const A = $state({
  open: false,
  title: '',
  step: 'idle',        // a STEPS id while running, then 'done' or 'failed'
  failedAt: '',
  retryFrom: '',       // where Try again starts ('send' after a read-back mismatch)
  message: '',
  sub: '',             // one line under the message (the different-settings stop: where else to apply)
  detail: '',          // technical text, shown only under Details
  waited: 0,
  results: [],         // [{name, label, want, got, ok}]
  canForce: false,
  canReread: false,    // the camera holds other settings than the ones the changes were made on
  rereadLabel: '',
  steps: {},           // the caller's words for the STEPS, by id (Looks: present participles); unset: the labels above
  done: '',            // the caller's done line ('NOIR is active.'); unset: the sheet's own
  note: '',            // the caller's line under it; unset: the sheet's own
});

const KSIO_STATUS = 0x8dc;
// Bytes the camera rewrites by itself (at boot, per shot, from the battery), all inside exclude_on_merge. They are
// ignored when telling whether the camera on the cable still holds the block the changes were made on, and they are
// taken from the camera's current block before sending, so a send never rolls back file numbers or shot counters.
const VOLATILE = [[0x010, 14], [0x028, 8], [0x057, 1], [0x05e, 1], [0x070, 4], [0x0a2, 2], [0x0ce, 1], [0x0f4, 8],
  [0x8bd, 1], [0x8d0, 48], [0x900, 16], [0xffc, 4]];

let job = null, waitTimer = null, lateTimer = null, backTimer = null;
// How long to wait for the restarted camera; the e2e tests shorten it (window.KOMA_WAIT_S).
const WAIT_LIMIT_S = () => Number(globalThis.KOMA_WAIT_S) || 60;

const sleep = ms => new Promise(r => setTimeout(r, ms));

function mask(ranges) {
  const m = new Uint8Array(4096);
  ranges.forEach(([o, n]) => m.fill(1, o, Math.min(o + n, 4096)));
  return m;
}
export const volatileMask = mask(VOLATILE);
function excludeMask() {
  const M = E.getMap();
  return M ? mask(M.exclude.map(e => [e.off, e.size]).concat([[M.csOff, M.csSize]])) : volatileMask;
}

// Same camera, same settings: equal outside the bytes the camera changes by itself.
export function sameSettings(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (!volatileMask[i] && a[i] !== b[i]) return false;
  return true;
}

// The camera menu's settings that differ between two blocks, by camera label (none inside the volatile bytes).
function changedFields(a, b) {
  const M = E.getMap();
  if (!M || !a || !b) return [];
  return M.fields.filter(f => !f.hidden && f.type !== 'bytes' && differs(f, a, b)
    && !volatileMask.subarray(f.offset, f.end).some(x => x)).map(f => clean(f.label));
}
// "WHITE BALANCE was changed on the camera." Three names at most, then a count.
export function changedLine(labels) {
  if (!labels.length) return 'The camera now holds other settings than the ones these changes were made on.';
  const named = labels.length > 3 ? labels.slice(0, 2) : labels, rest = labels.length - named.length;
  const list = rest ? named.join(', ') + ' and ' + plural(rest, 'other setting', 'other settings')
    : named.length > 1 ? named.slice(0, -1).join(', ') + ' and ' + named[named.length - 1] : named[0];
  return list + (labels.length === 1 ? ' was' : ' were') + ' changed on the camera.';
}

// The done rule (see the top of this file). Returns {ok, status, differ} for the Details text.
export function confirmBlock(now, sent) {
  const skip = excludeMask();
  let differ = 0;
  for (let i = 0; i < sent.length; i++) if (!skip[i] && now[i] !== sent[i]) differ++;
  const status = now[KSIO_STATUS];
  return {ok: now.length === sent.length && !differ && (status === 1 || status === 3), status, differ};
}

// The file that goes to the camera: the job's bytes, the camera's own volatile bytes, a fresh checksum. With edits
// (the pending bar), a volatile byte the user changed on purpose in Advanced is kept.
function toSend(now) {
  const out = job.bytes.slice();
  if (now) for (let i = 0; i < out.length; i++) {
    if (!volatileMask[i]) continue;
    if (job.edits && job.base && job.bytes[i] !== job.base[i]) continue;
    out[i] = now[i];
  }
  E.setChecksum(out);
  return out;
}

function shownFields(ref, sent) {
  const M = E.getMap();
  if (!M || !ref) return [];
  return M.fields.filter(f => !f.hidden && f.type !== 'bytes' && differs(f, ref, sent));
}

// bytes: the full 4096-byte block to send. fields: the fields that should change, for the rows (null: work them out).
// base: the block the changes were made on; the camera must still hold it (null: nothing to compare, e.g. a Restore
// before the camera was read). edits: bytes come from the user's edits on base (keeps volatile bytes changed in
// Advanced). onDone(now, bytes): the camera confirmed. onReread: offered when the camera holds other settings.
// differentMsg: the caller's own stop when the camera holds other settings (Copy: not the GR chosen under To); unset,
// the stop names the settings that changed. differentSub: the line under it (unset with onReread: apply on the GR
// the changes were made on).
export function startApply({bytes, fields = null, base = null, edits = false, title, force = false, onDone,
  onReread = null, rereadLabel = 'Apply to this GR', differentMsg = '', differentSub = null, steps = null, done = '',
  note = ''}) {
  clearInterval(lateTimer); lateTimer = null; stopWatching();
  job = {bytes: bytes.slice(), fields, base: base ? base.slice() : null, edits, force, onDone, onReread, differentMsg,
    differentSub: differentSub ?? (onReread && !differentMsg ? 'Or connect the first GR and apply there.' : ''),
    sent: null, lastSeen: null, wanted: []};
  Object.assign(A, {open: true, title: title || 'Apply to camera', step: 'send', failedAt: '', retryFrom: '', message: '',
    sub: '', detail: '', waited: 0, results: [], canForce: false, canReread: false, rereadLabel, steps: steps || {}, done,
    note});
  run(force);
}

export function retry() { if (job) run(job.force, A.retryFrom || A.failedAt || 'send'); }
export function sendAnyway() { if (job) { job.force = true; run(true, 'send'); } }
// After "has not come back" the camera was gone: the first sign of it is the restarted camera.
export function keepWaiting() { if (job) run(job.force, 'wait', {gone: true}); }
export async function reread() {
  const fn = job && job.onReread;
  close();
  if (fn) await fn();
}
export function close() {
  const late = A.step === 'failed' && (A.failedAt === 'wait' || A.failedAt === 'restart') && job && job.sent;
  A.open = false; clearInterval(waitTimer); stopWatching();
  if (late) watchLate();
}

function fail(at, message, detail, retryFrom = '') {
  A.step = 'failed'; A.failedAt = at; A.retryFrom = retryFrom; A.message = message; A.detail = detail || '';
  clearInterval(waitTimer);
  if (at === 'wait' || (at === 'restart' && cam.state !== 'cam')) watchBack(at);
}

function stopWatching() { clearInterval(backTimer); backTimer = null; }

// While "has not come back" or "the cable came out" is open: the moment the camera is on the cable again, carry on
// by itself. After a lost restart the camera may have restarted anyway (the cable came out as it powered off), so it
// reads back first: the settings are there, done; not there, restart it.
function watchBack(at) {
  const j = job;
  let busy = false;
  stopWatching();
  backTimer = setInterval(async () => {
    if (job !== j || !A.open || A.step !== 'failed' || A.failedAt !== at) { stopWatching(); return; }
    if (busy || cam.state !== 'cam') return;
    busy = true;
    stopWatching();
    if (at === 'wait') { keepWaiting(); return; }
    let now = null;
    try { now = b64decode((await bridgeJson('/settings', {timeout: 30000})).data); } catch (e) { /* restart it */ }
    if (job !== j) return;
    run(j.force, now && confirmBlock(now, j.sent).ok ? 'confirm' : 'restart');
  }, 2000);
}

// After "has not come back" and Close: if the camera turns up later with the settings applied, take them in, so the
// pending bar shows only what is still left to apply. If it turns up without them, the changes stay pending.
function watchLate() {
  const j = job, t0 = Date.now();
  let busy = false;
  clearInterval(lateTimer);
  lateTimer = setInterval(async () => {
    if (job !== j || A.open || Date.now() - t0 > 10 * 60 * 1000) { clearInterval(lateTimer); lateTimer = null; return; }
    if (busy || cam.state !== 'cam') return;
    busy = true;
    try {
      const now = b64decode((await bridgeJson('/settings', {timeout: 30000})).data);
      clearInterval(lateTimer); lateTimer = null;
      if (confirmBlock(now, j.sent).ok && j.onDone) j.onDone(now, j.bytes);
    } catch (e) { /* not readable yet: try again on the next tick */ } finally { busy = false; }
  }, 2000);
}

async function run(force, from = 'send', {gone: wasGone = false} = {}) {
  const order = STEPS.map(s => s.id);
  let i = order.indexOf(from);
  stopWatching();
  A.message = ''; A.sub = ''; A.detail = ''; A.canForce = false; A.canReread = false; A.retryFrom = '';
  // 1. check the camera, then send. The live block (GET /ram) is what the camera holds now, so a setting changed on
  // the camera since it was turned on is caught; a camera that does not answer it is judged by PARAM.CUR, as before.
  if (i <= 0) {
    A.step = 'send'; A.results = [];
    let now = null;
    try { now = b64decode((await bridgeJson('/settings', {timeout: 30000})).data); }
    catch (e) {
      if (!(e.data && e.data.code === 'no_param_cur'))
        return fail('send', 'The settings could not be sent. Nothing on the camera changed. Check the cable, then try again.', e.message);
    }
    // With the live block the camera menu's settings are compared (the RAM block's other bytes are the camera's own
    // business); without it, the whole PARAM.CUR outside the volatile bytes, as before.
    const live = job.base && E.komaVersion(job.base) !== null ? await readLive() : null;
    const same = !job.base || (live
      ? !changedFields(job.base, live).length || (!!job.lastSeen && !changedFields(job.lastSeen, live).length)
      : sameSettings(now, job.base) || sameSettings(now, job.lastSeen));
    if (!same) {
      A.canReread = !!job.onReread;
      A.sub = job.differentSub;
      return fail('send', job.differentMsg || changedLine(changedFields(job.base, live || now)));
    }
    job.sent = toSend(now);
    job.wanted = (job.fields || shownFields(job.base || now, job.sent))
      .map(f => ({f, name: f.name, label: clean(f.label), want: showValue(f, job.sent)}));
    try {
      const r = await postJson('/settings', {data: b64encode(job.sent), force: !!force}, {timeout: 30000});
      if (r.warnings && r.warnings.length) A.detail = r.warnings.join(' ');
    } catch (e) {
      if (e.status === 422 && e.data && e.data.code === 'koma_mismatch') {
        A.canForce = true;
        return fail('send', 'This camera runs a different Koma version from these settings. Nothing was sent.', e.data.detail || e.message);
      }
      if (e.status === 422) return fail('send', 'These settings failed the safety check. Nothing was sent.', e.message);
      return fail('send', 'The settings could not be sent. Nothing on the camera changed. Check the cable, then try again.', e.message);
    }
    i = 1;
  }
  // 2. restart
  if (i <= 1) {
    A.step = 'restart';
    try { await bridgeJson('/reboot', {method: 'POST', timeout: 15000}); }
    catch (e) {
      await poll();
      if (cam.state !== 'cam') return fail('restart', 'The cable came out before the camera restarted. Plug it back in.', e.message);
      return fail('restart', 'The camera did not take the restart. Turn it off and on by hand, then choose Try again.', e.message);
    }
    i = 2;
  }
  // 3. wait: first gone (or a few seconds), then back
  if (i <= 2) {
    A.step = 'wait';
    A.waited = 0;
    const t0 = Date.now();
    clearInterval(waitTimer);
    waitTimer = setInterval(() => { A.waited = Math.floor((Date.now() - t0) / 1000); }, 500);
    let gone = wasGone;
    for (;;) {
      await poll();
      if (cam.state !== 'cam') gone = true;
      if (cam.state === 'cam' && (gone || Date.now() - t0 > 8000)) break;
      if (Date.now() - t0 > WAIT_LIMIT_S() * 1000)
        return fail('wait', STATE.notBack);
      await sleep(1000);
    }
    clearInterval(waitTimer);
    i = 3;
  }
  // 4. read back: the block rule, plus each changed setting for the rows
  A.step = 'confirm';
  let now;
  try { now = b64decode((await bridgeJson('/settings', {timeout: 30000})).data); }
  catch (e) { return fail('confirm', 'The camera is back, but its settings could not be read. Try again.', e.message); }
  job.lastSeen = now;
  A.results = job.wanted.map(w => {
    let same = true; for (let k = w.f.offset; k < w.f.end; k++) if (now[k] !== job.sent[k]) same = false;
    return {name: w.name, label: w.label, want: w.want, got: showValue(w.f, now), ok: same};
  });
  const c = confirmBlock(now, job.sent);
  const bad = A.results.filter(r => !r.ok).length;
  if (bad || !c.ok) {
    const statusOk = c.status === 1 || c.status === 3;
    const none = 'The camera restarted without loading the settings. It still has its previous ones.';
    return fail('confirm',
      bad && bad === A.results.length ? none
        : bad ? 'The camera is back. Some settings did not take.'
        : !statusOk ? none
        : 'The camera is back, but its settings do not match what was sent.',
      'Import status ' + c.status + ' (1 or 3 means applied). ' + plural(c.differ, 'byte differs', 'bytes differ')
        + ' from what was sent, outside the camera-specific ranges.',
      'send');
  }
  A.step = 'done';
  if (job.onDone) job.onDone(now, job.bytes);
}
