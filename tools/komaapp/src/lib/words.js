// Plain-word helpers shared by the screens. Ported from the task UI of koma-setup.html (clean, showValue,
// plainReason, inspectBlock), same wording. No offsets, hex or raw values come out of these.
import {BLOCK_SIZE, magicOk, komaVersion, ksioTag, renderValue, uintOf} from '../engine/engine.js';

export const USB_NAME = 'camera (USB)';
export const NOT_GR = 'This file is not a GR settings file.';
export const VERSION_WARN = 'This file was saved by a different Koma version. Settings that version does not have are skipped.';

export const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);

// The map marks inferred labels and options with "(I)"; that is for engineers, not for the screens. The map's labels
// still say RECIPE (recipe_rcp_active, the RCP store); the user-facing word is LOOK,
// in the label's own case.
const RECIPE_WORD = {RECIPE: 'LOOK', RECIPES: 'LOOKS', Recipe: 'Look', Recipes: 'Looks', recipe: 'look', recipes: 'looks'};
export function clean(t) {
  return String(t).replace(/\s*\(I\)/g, '').trim()
    .replace(/\brecipes?\b/gi, w => RECIPE_WORD[w] || (w === w.toUpperCase() ? 'LOOK' + (w.length > 6 ? 'S' : '') : 'look' + (w.length > 6 ? 's' : '')));
}

// The user-facing word is "look", never "recipe".
export function plainHelp(t) {
  return String(t || '').replace(/\brecipes\b/g, 'looks').replace(/\brecipe\b/g, 'look').replace(/\bRecipe\b/g, 'Look');
}

export function differs(f, a, b) { for (let i = f.offset; i < f.end; i++) if (a[i] !== b[i]) return true; return false; }

function setBits(f, b) {
  const v = uintOf(f, b), out = [];
  Object.keys(f.bits).map(Number).sort((x, y) => x - y).forEach(i => { if ((v >> BigInt(i)) & 1n) out.push(f.bits[i]); });
  return out;
}

export function showValue(f, b) {
  if (f.type === 'bytes') return 'Updated';
  if (f.page === 'koma' && komaVersion(b) === null) return 'Needs Koma';  // a Koma row on a stock camera's block
  if (f.type === 'enum') {
    const v = String(uintOf(f, b));
    return Object.prototype.hasOwnProperty.call(f.values, v) ? clean(f.values[v]) : 'Not listed';
  }
  if (f.type === 'bitfield') { const s = setBits(f, b).map(clean); return s.length ? s.join(', ') : 'None'; }
  return renderValue(f, b) + (f.unit ? ' ' + f.unit : '');
}

export function plainReason(r) {
  const t = String(r || '');
  if (/firmware-version marker/i.test(t)) return 'Tells the camera which firmware saved these settings.';
  if (/koma/i.test(t)) return 'Koma writes this itself each time the camera starts.';
  if (/rewritten|transient|reset at|dirty-shutdown/i.test(t)) return 'The camera sets this itself each time it starts.';
  if (/factory default|calibration|factory/i.test(t)) return "Set at the factory for this camera's region, screen or parts.";
  if (/counter|count/i.test(t)) return 'Counts that belong to this camera, such as file numbers and shots taken.';
  if (/lens|focus-motor|sensor|wb gains/i.test(t)) return "Measured on this camera's own lens or sensor.";
  if (/battery|power history/i.test(t)) return "Learned from this camera's own battery.";
  if (/log|time stamp/i.test(t)) return "This camera's own service history.";
  if (/rate-control|learned/i.test(t)) return 'Learned by this camera while shooting.';
  return 'This belongs to this camera.';
}

// A loaded block in plain words. mapKV: the Koma version the map was made for (null: any).
// Koma's release on a camera, from the firmware tag ('240K1.0' = Ricoh 2.40, Koma 1.0; the
// patch digit is left out for space). Builds made before 1.0 are internal: their tags ('240K' with no minor, the
// 240K2.x line) name no release, and the app says "An earlier Koma build", never their number. Retire the 240K2.x rule
// before a Koma 2.x release ships. 'Rev024M' (the loader Koma firmware's own tag)
// names no release either. The KOMA header's version byte is the settings store's format, not a release.
export const EARLIER_BUILD = 'An earlier Koma build';
export const INSTALLED = 'Installed';
const cleanTag = t => String(t || '').replace(/[\0 ]+$/, '').trim();
export function earlierTag(tag) {
  const t = cleanTag(tag);
  return /^\d{3}K\d*$/.test(t) || /^240K2\.\d+$/.test(t);
}
// '1.0' from '240K1.0'; null for an earlier build's tag, Rev024M, Rev0240 or anything else
export function releaseOfTag(tag) {
  const t = cleanTag(tag);
  if (earlierTag(t)) return null;
  const m = /^\d{3}K(\d+\.\d+(?:\.\d+)?)$/.exec(t);
  return m ? m[1] : null;
}
// -1, 0, 1 for two releases ('1.0', '1.0.0', '1.2.3'); a missing part is 0
export function compareRelease(a, b) {
  const pa = String(a).split('.').map(Number), pb = String(b).split('.').map(Number);
  for (let i = 0; i < 3; i++) { const d = (pa[i] || 0) - (pb[i] || 0); if (d) return d < 0 ? -1 : 1; }
  return 0;
}
// {version: '1.0' or null, tag: '240K1.0' or '', earlier: an earlier build's tag}
export function komaRelease(b) {
  const tag = cleanTag(b && ksioTag(b));
  return {version: releaseOfTag(tag), tag, earlier: earlierTag(tag)};
}

// The one place a Koma version is written (VERSIONING.md: "Koma 1.0"; the patch digit only when it is not 0). Takes a
// release ('1.0', '1.0.0', 'V1.0.0'), a 7-character maker tag ('240K1.0'), or a settings block (Uint8Array, through
// komaRelease). Returns 'Koma 1.0' / 'Koma 1.0.1', or '' when nothing names a public release (an earlier build's tag,
// a pre-1.0 number, a firmware tag such as Rev024M).
export function komaVersionText(v) {
  if (v === null || v === undefined) return '';
  let r;
  if (v instanceof Uint8Array) r = komaRelease(v).version;
  else {
    const s = String(v).replace(/[\0\s]+$/, '').trim().replace(/^Koma\s+/i, '');
    if (/^\d{3}K/.test(s)) r = releaseOfTag(s);
    else { const num = /^[Vv]?(\d+(?:\.\d+){1,2})$/.exec(s); r = num ? num[1] : null; }
  }
  if (!r || Number(r.split('.')[0]) < 1) return '';
  const [x, y = '0', z = '0'] = r.split('.');
  return 'Koma ' + x + '.' + y + (Number(z) ? '.' + z : '');
}

// The same release without the word, for a row already labelled Koma ("Koma  1.0", never "Koma  Koma 1.0").
export function komaVersionNumber(v) { return komaVersionText(v).replace(/^Koma /, ''); }

// Koma on a camera, for a row labelled Koma: this computer's release when the camera runs this computer's build
// (kind 'koma-current', lib/onboard.svelte.js classify), "An earlier Koma build" for a build this one replaces or an
// earlier build's tag, else the release the tag names, else the plain fact that Koma runs.
export function komaOnCamera(tag, {kind = null, pkgVersion = null} = {}) {
  if (kind === 'koma-current') return komaVersionNumber(tag) || komaVersionNumber(pkgVersion) || INSTALLED;
  if (kind === 'koma-older' || earlierTag(tag)) return EARLIER_BUILD;
  return komaVersionNumber(tag) || INSTALLED;
}

export function inspectBlock(b, name, mapKV) {
  if (!b || b.length !== BLOCK_SIZE || !magicOk(b)) return {ok: false, msg: NOT_GR};
  const kv = komaVersion(b);
  const rel = kv === null ? null : komaVersionNumber(komaRelease(b).version) || null;
  const who = kv === null ? 'a GR on the original Ricoh firmware' : 'a GR running Koma' + (rel ? ' ' + rel : '');
  const n = String(name || '');
  const how = n === USB_NAME ? 'read from the camera' : /PARAM\.CUR$/i.test(n) ? 'saved by the camera'
    : /PARAM\.DAT$/i.test(n) ? 'prepared on a computer, not yet loaded by the camera'
    : /PARAM\.OK$/i.test(n) ? 'already loaded by the camera' : /^GR-settings-/i.test(n) ? 'saved as a backup' : '';
  const warn = mapKV !== null && mapKV !== undefined && kv !== mapKV ? VERSION_WARN : '';
  // runs and how: the same facts for a spec sheet (Copy): 'Koma 1.0' or 'Ricoh firmware'; 'Read from the camera'.
  const runs = kv === null ? 'Ricoh firmware' : 'Koma' + (rel ? ' ' + rel : '');
  return {ok: true, kv, summary: 'Settings from ' + who + (how ? ', ' + how : '') + '.', warn, runs,
    how: how ? how[0].toUpperCase() + how.slice(1) : ''};
}

export function today() {
  const d = new Date(), p = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

// Save bytes: a save dialog where the browser has one (the file can go straight to the card), else a download.
export async function saveBytes(bytes, name) {
  const blob = new Blob([bytes], {type: 'application/octet-stream'});
  if (window.showSaveFilePicker) {
    try {
      const fh = await window.showSaveFilePicker({suggestedName: name});
      const w = await fh.createWritable(); await w.write(blob); await w.close();
      return true;
    } catch (e) { if (e && e.name === 'AbortError') return false; }
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  return true;
}

// ---------------------------------------------------------------- shared copy
// Who holds the camera (GET /status; camera.svelte.js holder), with the fix and the line while it stops. Overview,
// Photos and Looks read this one map.
export const BUSY = {
  webcam: {text: 'The webcam is on. Photos pause while it runs.', action: 'Turn the webcam off',
    stopping: 'Turning the webcam off.'},
  interval: {
    text: (n, m) => n != null
      ? 'Interval shooting is on, ' + n + ' of ' + m + '. Photos pause while it runs.'
      : 'Interval shooting is on. Photos pause while it runs.',
    action: 'Stop interval shooting', stopping: 'Stopping interval shooting.'},
  live: {text: 'Live view is on. Photos pause while it runs.', action: 'Stop live view', stopping: 'Stopping live view.'},
  remote: {text: 'Remote control is on. Photos pause until it is off.', action: 'Turn off remote control',
    stopping: 'Turning off remote control.'},
};

// The state lines for every screen.
export const STATE = {
  offline: 'Koma is not running on this computer.',
  offlineWhere: {mac: 'Open Koma again from Applications.', win: 'Open Koma again from the Start menu.', other: 'Start Koma again.'},
  offlinePhone: 'Koma is not running on the computer.',
  lost: 'Camera disconnected. Turn it on, then reconnect USB.',
  disconnected: 'Disconnected',
  notBack: 'The camera has not come back. Reconnect it and keep waiting.',
  busyLive: BUSY.live.text,
  applied: 'Applied. Every setting matches what was sent.',
  keeps: 'The camera keeps them once it is turned off with its power button.',
  keepsLook: 'The camera keeps the look once it is turned off with its power button.',
  applyRestarts: 'Applying restarts the camera.',
  shootLive: 'Shoot changes this live, without a restart.',
  onComputer: 'On the computer: ',
  toFolder: 'To ',
  downloadsNote: "To this browser's Downloads folder.",
  saveToPhotos: 'Press and hold the photo to save it to Photos.',
};

// Every connection problem, one place (Connect, the setup Connect step, the Shell's Disconnected line). Kinds come from
// camera.svelte.js connectProblem.
export const CONNECT = {
  usb: {label: 'No USB', text: 'Koma cannot use USB on this computer.', sub: 'Run koma doctor in a terminal for the next step.',
    pip: 'Reinstall Koma: pipx install --force koma-gr', brew: 'Install libusb: brew install libusb'},
  two: n => (n > 2 ? n + ' cameras are' : 'Two cameras are') + ' plugged in. Unplug one.',
  open: {text: 'The camera is plugged in, but Koma could not open it.',
    langid: 'Charge the battery, then turn the camera off and on.',
    denied: 'Run koma --install-udev-rule in a terminal, then plug the camera in again.',
    driver: "The camera's WinUSB driver is missing. Run koma doctor in a terminal for the steps.",
    other: 'Quit any other app using the camera, then unplug it and plug it in again.'},
  mode: {label: 'USB mode', text: m => 'A camera is plugged in, but USB CONNECTION is set to ' + m + '.',
    sub: "Unplug the cable, set USB CONNECTION to ORIGINAL in the camera's SETUP menu, then plug it in again."},
  found: 'Camera found. Opening it.',
  looking: 'Looking for the camera.',
  plugOrder: [
    {lead: 'Turn the camera on', small: 'Wait for the lens to come out.'},
    {lead: 'Then plug in the USB cable', small: 'The camera must be on first.'},
  ],
  ladder: [
    "USB CONNECTION must be set to ORIGINAL in the camera's SETUP menu.",
    'Try another USB cable.',
    'Plug it into this computer directly, not through a hub.',
    'Unplug, turn the camera on, then plug in again.',
    'Another app may be using the camera. Quit it.',
    'Quit Koma and start it again.',
  ],
  pendingWaits: n => plural(n, 'change waits', 'changes wait') + ' to be applied.',
};

// ---------- Photos. Every string the Photos area shows.
const importedOf = (c, n) => (n ? c + ' of ' + n + ' imported.' : 'Nothing was imported.');
export const PHOTOS = {
  title: 'Photos',
  reading: 'Reading the card.',
  empty: {label: 'No photos', text: 'The card holds no photos.'},
  // Busy: the bridge answers 409 remote_on / webcam_on; the holder is named from GET /status. The one BUSY map above.
  busy: BUSY,
  readError: {camera: 'The camera did not answer. The card was not read.', read: 'The card could not be read.'},
  readAgain: 'Read again',
  importN: n => 'Import ' + n + ' new',
  chooseFolder: 'Choose folder',
  tagNew: 'New',
  tagImported: 'Imported',
  openPhoto: (num, time) => 'Open photo ' + num + ', ' + time,
  dayCount: n => (n === 1 ? ' photo' : ' photos'),
  selected: n => n + ' selected',
  selectedWord: 'selected',
  select: 'Select',
  downloadN: n => (n === 1 ? 'Download 1 photo' : 'Download ' + n + ' photos'),
  importToComputer: n => 'Import ' + n + ' new to the computer',
  selectedLabel: 'Selected',
  deselect: 'Deselect',
  downloadSelected: 'Download selected',
  downloading: (i, n) => 'Downloading ' + i + ' of ' + n,
  downloaded: n => (n === 1 ? 'Downloaded 1 photo.' : 'Downloaded ' + n + ' photos.'),
  // Paused by live view, interval shooting or remote control: the busy line above names it and offers to stop it.
  downloadStopped: (d, n) => 'Stopped. ' + d + ' of ' + n + ' downloaded.',
  downloadFailed: (i, n) => 'Photo ' + i + ' of ' + n + ' did not arrive from the camera. The download stopped.',
  tryAgain: 'Try again',
  mb: 'MB',
  // The import job (photoimport.py): phase, progress, how it ended. c = copies checked, n = photos to import. The
  // status line carries what was imported and what still waits on the camera (Import N new is the action); the rest
  // is under Details.
  job: {
    reading: 'Reading the card.',
    copied: (c, n) => 'Copied ' + c + ' of ' + n,
    of: 'of',
    stop: 'Stop',
    done: n => (n === 1 ? 'Imported 1 photo. The copy matches the card.' : 'Imported ' + n + ' photos. Every copy matches the card.'),
    nothing: 'Nothing new to import.',
    // A count that starts a clause keeps its verb on its line (no-break space).
    incomplete: s => (s === 1 ? '1 was still being written on the camera.' : s + ' were still being written on the camera.'),
    unreadable: s => (s === 1 ? '1 could not be read from the card.' : s + ' could not be read from the card.'),
    // Details
    already: a => (a === 1 ? '1 was already in the folder.' : a + ' were already in the folder.'),
    renamedAs: (name, as) => name + ' was saved as ' + as + '.',
    skipped: {
      incomplete: name => name + ' was still being written on the camera.',
      unreadable: name => name + ' could not be read from the card.',
    },
    stopped: {
      // remote_on words the same: the busy line names who holds the camera, once
      cancelled: (c, n) => 'Stopped. ' + importedOf(c, n),
      restart: () => 'Koma restarted. The import stopped.',
    },
    failed: {
      camera: (c, n) => 'The camera stopped answering. ' + importedOf(c, n),
      disk_full: (c, n) => 'The disk is full. ' + importedOf(c, n),
      no_write: (c, n) => 'Koma cannot write to the import folder. ' + importedOf(c, n),
      folder_missing: (c, n) => 'The import folder is not there. ' + (c ? importedOf(c, n) + ' ' : '')
        + 'Connect its drive, or choose another folder.',
      folder_is_file: () => 'The import folder is a file. Choose another folder.',
      verify: (c, n) => 'A copy did not match the card and was removed. ' + importedOf(c, n),
      write: (c, n) => 'The import folder could not be written. ' + importedOf(c, n),
      state: (c, n) => 'The list of imported photos could not be saved. ' + importedOf(c, n),
      start: () => 'The import did not start.',
      other: (c, n) => 'The import stopped. ' + importedOf(c, n),  // the reason goes under Details
    },
  },
  // Choose folder (POST /photos/folder; the refusal codes come from photoimport.py)
  folder: {
    title: 'Import folder',
    label: 'Folder',
    help: sample => 'One folder for each day, such as ' + sample,
    cancel: 'Cancel',
    use: 'Use this folder',
    refused: {
      folder_empty: 'Type the full path, such as ~/Pictures/Koma.',
      folder_relative: 'Type the full path, such as ~/Pictures/Koma.',
      folder_is_file: 'That is a file, not a folder.',
      folder_no_parent: 'The folder above it is not there. Connect its drive, or type another path.',
      folder_cannot_create: 'Koma cannot make this folder.',
      folder_cannot_write: 'Koma cannot write to this folder.',
      folder_here_only: 'Choose the folder on the computer that runs Koma.',
      other: 'The folder was not saved. Try again.',
    },
  },
  // The full view
  view: {
    close: 'Close',
    previous: 'Previous photo',
    next: 'Next photo',
    download: 'Download',
    titleWord: 'Photo ',
    alt: (num, day, time) => 'Photo ' + num + ', taken ' + day + ' ' + time,
    taken: 'Taken',  // read out before the date line, which names itself on the screen
    imported: 'Imported',
    notYet: 'Not yet',
    yes: 'Yes',
    reading: 'Reading the photo.',
    failed: 'The photo could not be read.',
    tryAgain: 'Try again',
    details: 'Details',
    file: 'File',
    cardFolder: 'Card folder',
    size: 'Size',
    pixels: 'Pixels',
    copy: 'On this computer',
  },
};

// The Webcam area and Use on your phone.
export * from './webcamWords.js';
