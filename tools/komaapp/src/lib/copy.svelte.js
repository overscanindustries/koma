// Copy to another GR: the flow's state as a module store, so a remount (the screen swapped out while the cable is
// out between the two cameras) keeps the first GR and the step. Reads go through settings.svelte.js's readBlock:
// PARAM.CUR, or the live block when the camera answers it, the same source the Apply check compares against.
import {readBlock, readFailText} from './settings.svelte.js';
import {sameSettings} from './apply.svelte.js';
import {cam} from './camera.svelte.js';
import {inspectBlock, USB_NAME} from './words.js';

const FRESH = {
  step: 1,
  from: null, fromInfo: null,
  fromConn: -1,        // the connection the first GR was read on (-1: it came from a file)
  to: null, toInfo: null,
  anyway: false,       // Copy anyway across Koma versions
  reading: '',         // 'from' | 'to' while a read runs
  err: '',
  autoConn: -1,        // the connection step 2 last read by itself
  autoFailConn: -1,    // the connection that read failed on: the manual button shows there
  done: false,         // sent and confirmed
};
export const C = $state({...FRESH});

// The flow holds a first GR past step 1: the app keeps it (and this screen) through a disconnect.
export const copyHolds = () => C.step >= 2 && !!C.from;
export function resetCopy() { Object.assign(C, FRESH); }

export async function readCam(which, auto = false) {
  C.reading = which; C.err = '';
  const conn = cam.conn;
  try {
    const e = accept(which, (await readBlock()).bytes, USB_NAME, conn);
    if (e) { C.err = e; if (auto) C.autoFailConn = conn; }
  } catch (e) {
    C.err = readFailText(e, e.data && e.data.code === 'no_param_cur' ? 'noexport' : '');
    if (auto) C.autoFailConn = conn;
  } finally { C.reading = ''; }
}

export function accept(which, b, name, conn = -1) {
  const info = inspectBlock(b, name, null);
  if (!info.ok) return info.msg;
  if (which === 'to' && C.from && sameSettings(b, C.from)) {
    return name === USB_NAME ? 'This is the same camera as the first one. Connect the other camera.'
      : "This is the same file as the first camera's. Choose the file from the other camera's card.";
  }
  if (which === 'from') Object.assign(C, {from: b, fromInfo: info, fromConn: name === USB_NAME ? conn : -1, to: null, toInfo: null});
  else Object.assign(C, {to: b, toInfo: info, anyway: false});
  C.err = '';
  return '';
}
