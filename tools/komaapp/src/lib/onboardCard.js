// The memory card, in this computer's card reader. Koma writes the card here, never over USB: the bridge's USB writes
// stay limited to PARAM.DAT, poff and the remote keys (komabridge WRITE_ALLOWLIST). Two routes:
// - a browser with the File System Access API (Chrome, Edge): the user chooses the card's top level once; Koma
//     writes its files there, removes its own stale ones, then reads every file back and compares the SHA-256;
// - any other browser: Koma saves the files as downloads, the user copies them, then chooses the card folder and
//     Koma reads the files back the same way (read-only).
// Card rules, traced: files at the top level of the card (Ricoh's update procedure; the loader reads /ATA1/GRMOD.BIN);
// an SD card the camera can read (2 GB or less without Koma 2 firmware); on an update
// card, every QRFLECTn the camera finds is read, and one it rejects stops the update;
// QUICKVER.UP and SCRIPT.MG1 stay off an update card.

export const canWrite = () => typeof window !== 'undefined' && typeof window.showDirectoryPicker === 'function';

// ---------------------------------------------------------------- SHA-256
// crypto.subtle where the page is a secure context (localhost), else a small JS version (a phone on the LAN address).
export async function sha256(bytes) {
  if (globalThis.crypto && crypto.subtle && globalThis.isSecureContext !== false) {
    try { return hex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))); } catch (e) { /* fall through */ }
  }
  return sha256Js(bytes);
}
const hex = b => Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
// Ported from tools/komaset/web/koma-setup.html (sha256Js), same behaviour.
function sha256Js(msg) {
  const K = [], H = [];
  for (let n = 2; K.length < 64; n++) {
    let prime = true;
    for (let d = 2; d * d <= n; d++) if (n % d === 0) { prime = false; break; }
    if (!prime) continue;
    if (H.length < 8) H.push(((Math.sqrt(n) % 1) * 4294967296) >>> 0);
    K.push(((Math.cbrt(n) % 1) * 4294967296) >>> 0);
  }
  const len = msg.length, total = Math.ceil((len + 9) / 64) * 64, b = new Uint8Array(total), dv = new DataView(b.buffer);
  b.set(msg); b[len] = 0x80;
  dv.setUint32(total - 8, Math.floor(len / 536870912)); dv.setUint32(total - 4, (len * 8) >>> 0);
  const w = new Uint32Array(64), rotr = (x, r) => (x >>> r) | (x << (32 - r));
  for (let o = 0; o < total; o += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(o + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let a = H[0], bb = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], hh = H[7];
    for (let i = 0; i < 64; i++) {
      const t1 = (hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) >>> 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c))) >>> 0;
      hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = bb; bb = a; a = (t1 + t2) >>> 0;
    }
    H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + bb) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
    H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + hh) >>> 0;
  }
  return H.map(x => x.toString(16).padStart(8, '0')).join('');
}

// ---------------------------------------------------------------- what is on the card
const UPDATE_NAME = /^QRFLECT\d$/;
// What a GR DIGITAL card's top level holds besides DCIM: the camera's and Koma's own files, and what macOS and Windows
// add to any removable volume.
const KNOWN = /^(DCIM|MISC|GRMOD\.(BIN|OFF)|KOMAPKG\.BIN|KOMA(LOG|DIAG|TRC|TIME|RM|LS)\.TXT|IRODUMP\.TXT|IROMINST\.TXT|WIDEFS\.TXT|SDPROBE\.TXT|PARAM\.(CUR|DAT|OK|BAD)|QRFLECT\d|SYSTEM VOLUME INFORMATION)$/;

// The top level of a chosen folder: names (upper case), whether it holds DCIM, and the problems for this job.
// job.flash: an update file goes on this card (Install Koma, Return to official firmware).
export function judge(entries, job, folderName = '') {
  const visible = entries.filter(e => !e.name.startsWith('.'));
  const names = new Set(visible.map(e => e.name.toUpperCase()));
  const dirs = new Set(visible.filter(e => e.kind === 'directory').map(e => e.name.toUpperCase()));
  const up = String(folderName || '').toUpperCase();
  if (up === 'DCIM' || [...dirs].some(n => /^\d{3}RICOH$/.test(n)) || /^\d{3}RICOH$/.test(up)) return {ok: false, why: 'dcim'};
  if (!dirs.has('DCIM') && [...names].some(n => !KNOWN.test(n))) return {ok: false, why: 'not-card'};
  if (job.flash) {
    const other = [...names].filter(n => (UPDATE_NAME.test(n) && n !== 'QRFLECT3') || n === 'QUICKVER.UP' || n === 'SCRIPT.MG1').sort();
    if (other.length) return {ok: false, why: 'update-files', names: other};
  }
  return {ok: true, names: [...names].sort(), hasDcim: dirs.has('DCIM')};
}

async function listDir(dir) {
  const out = [];
  for await (const [name, h] of dir.entries()) out.push({name, kind: h.kind});
  return out;
}

export async function chooseCard() {
  return window.showDirectoryPicker({id: 'koma-card', mode: 'readwrite'});
}

async function writable(dir) {
  if (!dir.queryPermission) return true;
  let p = await dir.queryPermission({mode: 'readwrite'});
  if (p !== 'granted' && dir.requestPermission) p = await dir.requestPermission({mode: 'readwrite'});
  return p === 'granted';
}

async function findName(dir, want) {
  const W = want.toUpperCase();
  for await (const [name, h] of dir.entries()) if (h.kind === 'file' && name.toUpperCase() === W) return name;
  return null;
}

// job.check: {name, test(text or null) -> null when good, else why}: a file the camera wrote, read before anything is
// changed (the /IROM installer's IRODUMP.TXT). A refusal stops the job with nothing written.
function checked(job, text) {
  if (!job.check) return;
  const why = job.check.test(text);
  if (why) throw Object.assign(new Error(why), {stage: 'check', why});
}

// Write the job to the chosen card folder, then read every file back. onPhase('write' | 'verify').
// job: {flash, files: [{name, bytes, sha}], remove: [names], check?}. Returns {files: [{name, sha, bytes}],
// removed: [names]}.
export async function writeCard(dir, job, onPhase = () => {}) {
  const verdict = judge(await listDir(dir), job, dir.name);
  if (!verdict.ok) throw Object.assign(new Error(verdict.why), {stage: 'card', why: verdict.why, names: verdict.names || []});
  if (job.check) {
    const real = await findName(dir, job.check.name);
    checked(job, real ? await (await (await dir.getFileHandle(real)).getFile()).text() : null);
  }
  if (!(await writable(dir))) throw Object.assign(new Error('permission'), {stage: 'card', why: 'permission'});
  onPhase('write');
  const removed = [];
  try {
    for (const name of job.remove) {
      const real = await findName(dir, name);
      if (real) { await dir.removeEntry(real); removed.push(name); }
    }
    for (const f of job.files) {
      const real = await findName(dir, f.name);
      const fh = await dir.getFileHandle(real || f.name, {create: true});
      const w = await fh.createWritable();
      await w.write(f.bytes);
      await w.close();
    }
  } catch (e) {
    throw Object.assign(new Error(e && e.name === 'QuotaExceededError' ? 'full' : 'write'), {stage: 'write', why: e && e.name === 'QuotaExceededError' ? 'full' : 'write', detail: String(e && e.message || e)});
  }
  onPhase('verify');
  return verifyDir(dir, job, removed);
}

// Read back a card folder (also for the download route, read-only).
export async function verifyDir(dir, job, removed = []) {
  const out = [];
  for (const f of job.files) {
    const real = await findName(dir, f.name);
    if (!real) throw Object.assign(new Error('missing'), {stage: 'verify', why: 'missing', names: [f.name]});
    const data = new Uint8Array(await (await (await dir.getFileHandle(real)).getFile()).arrayBuffer());
    const sha = await sha256(data);
    if (sha !== f.sha) throw Object.assign(new Error('differs'), {stage: 'verify', why: 'differs', names: [f.name], detail: f.name + ': ' + sha.slice(0, 8) + ', expected ' + f.sha.slice(0, 8)});
    out.push({name: f.name, sha, bytes: data.length});
  }
  for (const name of job.remove) {
    if (await findName(dir, name)) throw Object.assign(new Error('still'), {stage: 'verify', why: 'still', names: [name]});
  }
  return {files: out, removed};
}

// The same read-back for a folder chosen with <input type=file webkitdirectory> (a FileList with relative paths).
export async function verifyFileList(list, job) {
  const files = Array.from(list);
  const root = files.length ? String(files[0].webkitRelativePath || '').split('/')[0] : '';
  const top = files.filter(f => String(f.webkitRelativePath || f.name).split('/').length === 2);
  const entries = top.map(f => ({name: f.name, kind: 'file'}));
  if (files.some(f => String(f.webkitRelativePath).split('/')[1]?.toUpperCase() === 'DCIM' && String(f.webkitRelativePath).split('/').length > 2)) entries.push({name: 'DCIM', kind: 'directory'});
  const verdict = judge(entries, job, root);
  if (!verdict.ok) throw Object.assign(new Error(verdict.why), {stage: 'card', why: verdict.why, names: verdict.names || []});
  if (job.check) {
    const hit = top.find(x => x.name.toUpperCase() === job.check.name.toUpperCase());
    checked(job, hit ? await hit.text() : null);
  }
  const out = [];
  for (const f of job.files) {
    const hit = top.find(x => x.name.toUpperCase() === f.name.toUpperCase());
    if (!hit) throw Object.assign(new Error('missing'), {stage: 'verify', why: 'missing', names: [f.name]});
    const sha = await sha256(new Uint8Array(await hit.arrayBuffer()));
    if (sha !== f.sha) throw Object.assign(new Error('differs'), {stage: 'verify', why: 'differs', names: [f.name], detail: f.name + ': ' + sha.slice(0, 8) + ', expected ' + f.sha.slice(0, 8)});
    out.push({name: f.name, sha, bytes: hit.size});
  }
  for (const name of job.remove) {
    if (top.some(x => x.name.toUpperCase() === name.toUpperCase())) throw Object.assign(new Error('still'), {stage: 'verify', why: 'still', names: [name]});
  }
  return {files: out, removed: []};
}

// A plain download (never the save dialog): the backup and the files for the download route.
export function download(bytes, name) {
  const blob = new Blob([bytes], {type: 'application/octet-stream'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
