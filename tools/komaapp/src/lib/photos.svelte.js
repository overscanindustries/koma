// Photos: the card's photos as a contact sheet by day, a
// full view with the file's own EXIF, Download selected, and Import new, which the bridge runs (photoimport.py: it
// copies, reads every copy back and remembers what it copied).
//
// Every card read goes through GET /photos and GET /photos/<F>/<N>: the DCIM folders only, never the card root (it hangs
// on a 64 GB FAT32 card, verified on a camera). Costs (bridge README): the first listing of 100 photos takes 7.5 to 8.7 s,
// then it is cached; a thumbnail is a RETR of the whole file, about 0.9 s for 3.3 MB. So thumbnails load in the order the
// cells come into view, two at a time, and stay in this tab once read. While remote control is on, the bridge answers
// 409 remote_on (a card read in shooting state drops live view, verified on a camera): Photos names the holder and offers to
// stop it.
import {SvelteMap, SvelteSet} from 'svelte/reactivity';
import {bridgeJson, bridgeFetch, postJson} from './bridge.js';
import {cam, holder} from './camera.svelte.js';
import {setOn as setWebcam} from './webcam.svelte.js';
import {BUSY} from './words.js';
import {readExif} from './photosExif.js';

export const ph = $state({
  list: 'idle',     // idle | reading | ok | busy | stopping | error
  photos: [],       // GET /photos records, newest first
  readT0: 0,        // Date.now when the running read began
  err: '',          // 'camera' | 'read' while list is 'error'
  conn: -1,         // the connection the list was read on
  imp: null,        // the import folder: {display, sep, choose}
  job: null,        // the import job this page started or watched run (stop.why 'restart': the bridge restarted under it)
  dl: null,         // Download selected: {i, n, t0, zip, state: 'running' | 'done' | 'failed' | 'busy'}
});
export const imported = new SvelteMap();  // photo key -> where the copy went ('' until the job that copied it ends)
export const selected = new SvelteSet();  // photo paths
export const thumbs = new SvelteMap();    // thumbnail key -> {state: 'loading' | 'ok' | 'bad', url}

// A photo's identity, as the bridge's import list keeps it: name, size and capture time, never the name alone.
export const keyOf = p => p.name + '|' + p.size + '|' + p.date;
export const tkey = p => p.path + '|' + p.size + '|' + p.date;
export const photoUrl = p => '/photos/' + p.path.split('/').map(encodeURIComponent).join('/');
// The camera's file number: the last four digits (RIMG0003 -> 0003; R0015710 -> 5710, file 5710 of folder 100RICOH).
export const fileNumber = name => { const m = /(\d{1,4})$/.exec(String(name).split('.')[0]); return m ? m[1] : name; };
export const dayOf = date => String(date || '').slice(0, 10);                // '2026-11-07'
export const dayText = date => dayOf(date).replace(/-/g, '/');                 // '2026/11/07'
// Megabytes (10^6 bytes; the camera's own files are 2.5 to 3.4 MB): one decimal under 10, whole numbers above. A file
// with bytes never prints as 0.
export function mb(bytes) {
  const v = bytes / 1e6;
  if (!bytes) return '0';
  if (v < 0.05) return '< 0.1';
  return v < 10 ? v.toFixed(1) : String(Math.round(v));
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
const later = fn => setTimeout(fn, 60000);
const GRACE_MS = 3000;  // leaving Shoot turns remote control off a moment later: read again quietly before saying busy

let active = false;

// ---------------------------------------------------------------- open, close, the list
export function open() {
  active = true;
  // The camera went away (or has not answered yet): the frames already read stay, nothing is read, and no error is
  // set (the Shell's Disconnected line is the fact). Its next connection reads again (Photos calls open on cam.conn).
  if (cam.state !== 'cam') { keepOnDrop(); return; }
  if (ph.conn !== cam.conn) { reset(); ph.conn = cam.conn; }
  refreshImport();
  readList();
}

function keepOnDrop() {
  if (ph.list === 'error' && ph.err === 'camera') { ph.list = ph.photos.length ? 'ok' : 'idle'; ph.err = ''; }
  if (ph.list === 'reading' && !ph.photos.length) ph.list = 'idle';
}

export function close() {
  active = false;
  clearTimeout(jobT);
  if (io) { io.disconnect(); io = null; }
  watched.clear();
  queue.length = 0;
}

function reset() {
  for (const t of thumbs.values()) if (t.url) URL.revokeObjectURL(t.url);
  thumbs.clear();
  for (const f of fulls.values()) later(() => URL.revokeObjectURL(f.url));
  fulls.clear();
  queue.length = 0;
  selected.clear();
  anchor = '';
  ph.photos = [];
  ph.list = 'idle';
  ph.dl = null;
}

let reading = null;
export function readList() {
  if (reading) return reading;
  reading = (async () => {
    if (!ph.photos.length) ph.list = 'reading';
    ph.readT0 = Date.now();
    for (;;) {
      try {
        const d = await bridgeJson('/photos', {timeout: 180000});
        ph.photos = d.photos || [];
        ph.list = 'ok';
        if (!ph.imp) refreshImport();  // the import folder's last read missed: no camera traffic behind it
        for (const [k, t] of thumbs) if (t.state === 'bad') thumbs.delete(k);  // tried again when seen
        const paths = new Set(ph.photos.map(p => p.path));
        for (const s of [...selected]) if (!paths.has(s)) selected.delete(s);
        pump();
        return;
      } catch (e) {
        if (e.status === 409 && e.data && e.data.code === 'remote_on') {
          if (active && await remoteOff(ph.readT0 + GRACE_MS)) continue;
          ph.list = 'busy';
          return;
        }
        const gone = e.status === 503 || e.status === 0;
        if (gone && cam.state !== 'cam') { ph.list = ph.photos.length ? 'ok' : 'idle'; return; }
        ph.list = 'error';
        ph.err = gone ? 'camera' : 'read';
        return;
      }
    }
  })().finally(() => { reading = null; });
  return reading;
}

// True once GET /status (no card read) says remote control is off, false at the deadline.
async function remoteOff(until) {
  while (active && Date.now() < until) {
    await sleep(300);
    try { if (!(await bridgeJson('/status', {timeout: 3000})).tether) return true; } catch (e) { return false; }
  }
  return false;
}

// The busy line for whoever holds the camera (camera.svelte.js holder: webcam, interval, live view or remote control,
// from the 2 s status poll): {text, action, stopping}. The interval's text carries its count when /status has it
// (interval.done of interval.count, bridge README POST /tether/interval).
export function busyLine(who = holder()) {
  const b = BUSY[who] || BUSY.remote;
  const it = cam.st && cam.st.interval;
  const text = typeof b.text === 'function' ? b.text(it ? it.done : undefined, it ? it.count : undefined) : b.text;
  return {...b, text};
}

// Free the camera for Photos. The webcam stops through its own switch (POST /webcam/stop: the bridge refuses
// /tether/stop while the webcam holds the camera, webcamhost.refuse), then the card is read once /status says it has
// let go. Live view, the interval and remote control stop with POST /tether/stop (bridge tether_stop).
export async function release() {
  const who = holder();
  ph.list = 'stopping';
  ph.readT0 = Date.now();
  if (who === 'webcam') {
    await setWebcam(false);
    const until = Date.now() + 30000;
    while (Date.now() < until) {
      try { if (!(await bridgeJson('/status', {timeout: 3000})).webcam) break; } catch (e) { break; }
      await sleep(400);
    }
  } else {
    try { await postJson('/tether/stop', undefined, {timeout: 15000}); } catch (e) { /* the read below says what is true */ }
  }
  ph.list = ph.photos.length ? 'ok' : 'reading';
  await readList();
}

// ---------------------------------------------------------------- selection
// A click selects or clears one photo; with Shift held, every photo between the last one toggled and this one (in the
// sheet's order, newest first) takes this one's new state.
let anchor = '';
export function pick(p, on, range = false) {
  const j = ph.photos.findIndex(q => q.path === p.path);
  const i = range && anchor ? ph.photos.findIndex(q => q.path === anchor) : -1;
  const span = i >= 0 && j >= 0 ? ph.photos.slice(Math.min(i, j), Math.max(i, j) + 1) : [p];
  for (const q of span) { if (on) selected.add(q.path); else selected.delete(q.path); }
  anchor = p.path;
}
// A day head's Select: every photo of that day, or none once the whole day is selected.
export function pickDay(photos, on) {
  for (const q of photos) { if (on) selected.add(q.path); else selected.delete(q.path); }
  if (photos.length) anchor = photos[photos.length - 1].path;
}

// ---------------------------------------------------------------- thumbnails
const queue = [];
let running = 0;
const MAX_THUMBS = 2;
let io = null;
const watched = new Map();  // cell element -> photo

function observer() {
  if (io || typeof IntersectionObserver === 'undefined') return io;
  io = new IntersectionObserver(entries => {
    for (const en of entries) {
      const p = watched.get(en.target);
      if (!p) continue;
      if (en.isIntersecting) want(p); else unwant(p);
    }
  }, {root: document.getElementById('main'), rootMargin: '480px 0px'});
  return io;
}

// A contact-sheet cell asks for its thumbnail when it comes near the view, and lets go when it leaves before its turn.
export function watch(el, p) {
  const o = observer();
  if (!o) { want(p); return () => {}; }
  watched.set(el, p);
  o.observe(el);
  return () => { o.unobserve(el); watched.delete(el); unwant(p); };
}

function want(p) {
  const k = tkey(p);
  if (thumbs.has(k) || queue.some(q => tkey(q) === k)) return;
  queue.push(p);
  pump();
}
function unwant(p) {
  const i = queue.findIndex(q => tkey(q) === tkey(p));
  if (i >= 0) queue.splice(i, 1);
}

function pump() {
  while (running < MAX_THUMBS && queue.length && (ph.list === 'ok' || (ph.list === 'reading' && ph.photos.length))) {
    const p = queue.shift();
    const k = tkey(p);
    if (thumbs.has(k)) continue;
    running++;
    thumbs.set(k, {state: 'loading'});
    loadThumb(p, k).finally(() => { running--; pump(); });
  }
}

async function loadThumb(p, k) {
  const conn = ph.conn;
  try {
    const r = await bridgeFetch(photoUrl(p) + '?thumb=1', {timeout: 60000});
    if (r.status === 409) { thumbs.delete(k); queue.unshift(p); ph.list = 'busy'; return; }
    if (!r.ok) { thumbs.set(k, {state: 'bad'}); return; }
    const url = URL.createObjectURL(await r.blob());
    if (conn !== ph.conn) { URL.revokeObjectURL(url); return; }
    thumbs.set(k, {state: 'ok', url});
  } catch (e) {
    thumbs.set(k, {state: 'bad'});
  }
}

// A thumbnail that does not decode (read while the camera was still writing the file) shows as reading.
export function thumbFailed(p) {
  const k = tkey(p), t = thumbs.get(k);
  if (t && t.url) URL.revokeObjectURL(t.url);
  thumbs.set(k, {state: 'bad'});
}

// ---------------------------------------------------------------- the whole file (full view, download)
const fulls = new Map();   // thumbnail key -> {url, exif, bytes}, the last FULL_KEEP read
const pendingFull = new Map();
const FULL_KEEP = 6;

export function loadFull(p) {
  const k = tkey(p);
  if (fulls.has(k)) { const v = fulls.get(k); fulls.delete(k); fulls.set(k, v); return Promise.resolve(v); }
  if (pendingFull.has(k)) return pendingFull.get(k);
  const conn = ph.conn;
  const pr = (async () => {
    const r = await bridgeFetch(photoUrl(p), {timeout: 120000});
    if (r.status === 409) { ph.list = 'busy'; throw Object.assign(new Error('busy'), {busy: true}); }
    if (!r.ok) throw new Error('http ' + r.status);
    const buf = await r.arrayBuffer();
    const v = {url: URL.createObjectURL(new Blob([buf], {type: 'image/jpeg'})), exif: readExif(buf), bytes: buf.byteLength, buf};
    if (conn === ph.conn) {
      fulls.set(k, v);
      while (fulls.size > FULL_KEEP) {
        const [old, ov] = fulls.entries().next().value;
        fulls.delete(old);
        later(() => URL.revokeObjectURL(ov.url));
      }
    } else {
      later(() => URL.revokeObjectURL(v.url));  // read for a connection that has gone: not kept, let go once shown
    }
    return v;
  })().finally(() => pendingFull.delete(k));
  pendingFull.set(k, pr);
  return pr;
}

export function saveUrl(url, name) {
  const a = document.createElement('a');
  a.href = url; a.download = name; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
}

// Download selected. One photo saves as its JPEG. Several on a desktop save as one ZIP (stored, not compressed: JPEGs do
// not shrink), named for the newest photo's day, built here from the files as they arrive. A phone saves one file at a
// time (a phone browser keeps one download per tap, so several files there come one after another).
let zipParts = [];
export async function downloadSelected(phone = false) {
  if (ph.dl && ph.dl.state === 'running') return;
  const list = ph.photos.filter(p => selected.has(p.path));
  if (!list.length) return;
  const zip = !phone && list.length > 1;
  // After a failure or a pause, the same selection carries on from the photo that did not arrive (any change to the
  // selection clears the line, so a line still here means the same photos): nothing is read or saved twice.
  const from = ph.dl && (ph.dl.state === 'failed' || ph.dl.state === 'busy') && ph.dl.n === list.length
    && ph.dl.zip === zip ? ph.dl.i - 1 : 0;
  if (!from) zipParts = [];
  ph.dl = {i: from, n: list.length, zip, state: 'running', t0: Date.now()};
  for (const p of list.slice(from)) {
    ph.dl.i += 1;
    try {
      const f = await loadFull(p);
      if (zip) zipParts.push({name: p.name, date: p.date, data: new Uint8Array(f.buf)});
      else saveUrl(f.url, p.name);
    } catch (e) {
      ph.dl.state = e && e.busy ? 'busy' : 'failed';
      return;
    }
  }
  if (zip) {
    const url = URL.createObjectURL(zipStore(zipParts));
    saveUrl(url, zipName(list[0]));
    zipParts = [];
    later(() => URL.revokeObjectURL(url));
  }
  ph.dl.state = 'done';
}
export const zipName = p => 'Koma ' + dayOf(p.date) + '.zip';

// A ZIP of stored (method 0) entries: local header and data for each file, then the central directory and its end
// record (PKWARE APPNOTE 4.3). Times are the photo's capture time in DOS form; names are the camera's ASCII names.
let crcTable = null;
function crc32(b) {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  let c = 0xffffffff;
  for (let i = 0; i < b.length; i++) c = crcTable[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function dosStamp(date) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):?(\d{2})?/.exec(String(date || ''));
  if (!m || +m[1] < 1980) return [0, 0x21];  // 1980-01-01 00:00
  return [(+m[4] << 11) | (+m[5] << 5) | ((+(m[6] || 0)) >> 1), ((+m[1] - 1980) << 9) | (+m[2] << 5) | +m[3]];
}
export function zipStore(files) {
  const parts = [], central = [];
  let offset = 0;
  for (const f of files) {
    const name = new TextEncoder().encode(f.name), crc = crc32(f.data), [tm, dt] = dosStamp(f.date), n = f.data.length;
    const head = new DataView(new ArrayBuffer(30));
    head.setUint32(0, 0x04034b50, true); head.setUint16(4, 20, true); head.setUint16(6, 0x0800, true);
    head.setUint16(8, 0, true); head.setUint16(10, tm, true); head.setUint16(12, dt, true);
    head.setUint32(14, crc, true); head.setUint32(18, n, true); head.setUint32(22, n, true);
    head.setUint16(26, name.length, true); head.setUint16(28, 0, true);
    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true); cd.setUint16(4, 20, true); cd.setUint16(6, 20, true); cd.setUint16(8, 0x0800, true);
    cd.setUint16(10, 0, true); cd.setUint16(12, tm, true); cd.setUint16(14, dt, true);
    cd.setUint32(16, crc, true); cd.setUint32(20, n, true); cd.setUint32(24, n, true);
    cd.setUint16(28, name.length, true); cd.setUint32(42, offset, true);
    parts.push(head.buffer, name, f.data);
    central.push(cd.buffer, name);
    offset += 30 + name.length + n;
  }
  const size = central.reduce((a, b) => a + b.byteLength, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, size, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end.buffer], {type: 'application/zip'});
}

// ---------------------------------------------------------------- import (GET|POST|DELETE /photos/import, POST /photos/folder)
let jobT = null;
let seen = 0;     // the newest job id this page started or saw running: only its result is news here
let run = null;   // the bridge process those ids belong to; ids start again when the bridge restarts

export async function refreshImport() {
  try { applyImport(await bridgeJson('/photos/import', {timeout: 10000})); }
  catch (e) { /* no camera traffic behind it; a miss keeps the last state, and the next card read asks again */ }
}

function applyImport(d) {
  ph.imp = {display: d.display, sep: d.sep || '/', choose: d.choose !== false};
  if (d.imported) {
    imported.clear();
    for (const [k, v] of Object.entries(d.imported)) imported.set(k, v);
  }
  applyJob(d.job, d.run);
}

function applyJob(j, r) {
  if (r !== undefined && r !== run) {
    // Another bridge process: its job ids start again at 1, and a job this page watched ended with the old process.
    if (run !== null && ph.job && ph.job.state === 'running') ph.job = {...ph.job, state: 'stopped', stop: {why: 'restart'}};
    run = r;
    seen = 0;
  }
  if (!j) return;
  if (j.state === 'running') seen = Math.max(seen, j.id);
  if (j.id === seen) {
    const was = ph.job;
    ph.job = j;
    // Stopped because remote control came on (live view, interval shooting or remote control in another tab): Photos
    // is held, and the busy line names the holder and offers to stop it, once.
    if (j.state === 'stopped' && j.stop && j.stop.why === 'remote_on' && was && was.id === j.id && was.state === 'running'
      && ph.list === 'ok') ph.list = 'busy';
  }
  for (const k of j.keys || []) if (!imported.has(k)) imported.set(k, '');
  if (j.state === 'running' && active) pollJob();
}

function pollJob() {
  clearTimeout(jobT);
  jobT = setTimeout(async () => {
    if (!active) return;
    try {
      const d = await bridgeJson('/photos/import?job=1', {timeout: 5000});
      applyJob(d.job, d.run);
      if (!d.job || d.job.state !== 'running') refreshImport();
    } catch (e) { pollJob(); }
  }, 500);
}

export async function startImport() {
  try {
    const d = await postJson('/photos/import', undefined, {timeout: 15000});
    applyJob(null, d.run);
    seen = Math.max(seen, d.job.id);
    applyJob(d.job);
  } catch (e) {
    const code = e && e.data && e.data.code;
    if (code === 'remote_on') { ph.list = 'busy'; return; }
    if (code === 'import_running') { refreshImport(); return; }
    ph.job = {id: -1, state: 'failed', total: 0, done: 0, copied: 0, already: 0, bytes_total: 0, bytes_done: 0,
      renamed: [], skipped: [], keys: [], stop: {why: 'start'}, elapsed_s: 0};
  }
}

export async function stopImport() {
  try { const d = await bridgeJson('/photos/import', {method: 'DELETE', timeout: 10000}); applyJob(d.job, d.run); }
  catch (e) { /* the poll shows how it ends */ }
}

// The folder a person typed: null when the bridge took it, else the refusal code (photoimport.py) the sheet words.
export async function chooseFolder(text) {
  try {
    applyImport(await postJson('/photos/folder', {path: text}, {timeout: 15000}));
    return null;
  } catch (e) {
    return (e && e.data && e.data.code) || 'other';
  }
}

// ---------------------------------------------------------------- derived views of the list
export function newPhotos() { return ph.photos.filter(p => !imported.has(keyOf(p))); }

// For Overview: how many photos on the card are not yet imported, from the list this session read on this connection
// and the bridge's remembered import set; null when either is unknown (Photos has not read the card yet).
export function newCount() {
  if (ph.conn !== cam.conn || ph.list === 'idle' || ph.list === 'error' || !ph.imp) return null;
  if (!ph.photos.length && ph.list !== 'ok') return null;
  return newPhotos().length;
}

export function byDay() {
  const out = [];
  for (const p of ph.photos) {
    const d = dayOf(p.date);
    if (!out.length || out[out.length - 1].day !== d) out.push({day: d, photos: []});
    out[out.length - 1].photos.push(p);
  }
  return out;
}

// Where the next import puts a photo: the folder, then year, month and day of the newest new photo (Capture One's
// live sample path).
export function samplePath(photo) {
  if (!ph.imp) return '';
  const sep = ph.imp.sep, d = photo ? dayOf(photo.date).split('-') : [];
  return ph.imp.display + sep + (d.length === 3 ? d.join(sep) + sep : '');
}
