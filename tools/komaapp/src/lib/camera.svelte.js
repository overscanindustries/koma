// Bridge and camera state. GET /status is polled every 2 s (the bridge answers it from the USB descriptors and sends
// nothing to the camera unless a new one has to be opened). state: 'unknown' before the first answer, 'off' when the
// bridge does not answer, 'nocam' when it answers without a camera, 'cam' when a camera is connected.
import {bridgeJson} from './bridge.js';

export const cam = $state({
  state: 'unknown',
  st: null,          // the last /status body
  conn: 0,           // bumps on every connect and disconnect, so screens can tell a new session apart
  forbidden: false,  // phone mode without the right code
  tokenRefused: false,  // the network listener refused this page: a phone without a current token (PhoneGate)
  sawCamera: false,  // a camera was connected at least once in this tab (never reset): a drop then stays in the app
});

let timer = null, inflight = null, timeouts = 0;
// A /status that takes longer than this is "alive, busy", not offline: the first one after a plug-in opens the camera
// synchronously. Three in a row (about 15 s) and the bridge is treated as gone.
const POLL_TIMEOUT = 5000, TIMEOUTS_OFF = 3;

export function connected() { return cam.state === 'cam'; }

export function poll() {
  if (inflight) return inflight;
  let again = 2000;
  inflight = (async () => {
    try {
      const s = await bridgeJson('/status', {timeout: POLL_TIMEOUT});
      timeouts = 0;
      const was = cam.state;
      if (s.connected !== (was === 'cam') && was !== 'unknown') cam.conn++;
      else if (was === 'unknown' && s.connected) cam.conn++;
      cam.st = s; cam.state = s.connected ? 'cam' : 'nocam'; cam.forbidden = cam.tokenRefused = false;
      if (s.connected) cam.sawCamera = true;
    } catch (e) {
      if (e && e.timeout && ++timeouts < TIMEOUTS_OFF) { again = 200; return; }  // keep state and status; ask again
      timeouts = 0;
      if (cam.state === 'cam') cam.conn++;
      cam.st = null; cam.state = 'off'; cam.forbidden = e && e.status === 403;
      cam.tokenRefused = !!(cam.forbidden && e.data && e.data.code === 'token');
    } finally {
      inflight = null;
      clearTimeout(timer); timer = setTimeout(poll, again);
    }
  })();
  return inflight;
}

export function startPolling() { if (!timer && !inflight) poll(); }

// Model and firmware as the camera reports them; Koma's version comes from the settings block when it has been read.
export function cameraName() { return (cam.st && cam.st.camera && cam.st.camera.model) || 'GR DIGITAL'; }
export function firmware() { return cam.st && cam.st.camera ? cam.st.camera.firmware_display || cam.st.camera.firmware : null; }

// Why no camera is open, from the bridge's /status: null when connected or before any answer, else
// {kind: 'usb' | 'two' | 'open' | 'mode', message, n, sub, mode}. The bridge names the kind in `problem` (komabridge
// Link.status); an older bridge only has `message`, which is matched here. Words: words.js CONNECT.
//   usb   the USB library is missing or unusable          two   n Ricoh devices on the bus
//   open  the camera is on the bus but could not be opened; sub: 'langid' | 'denied' | 'driver' | 'other'
//   mode  USB CONNECTION is not ORIGINAL (mode: the bridge's name for it)
export function connectProblem() {
  const s = cam.st;
  if (!s || s.connected) return null;
  const message = String(s.message || '');
  let kind = s.problem || null;
  if (!kind) {
    if (message.startsWith('USB is not available')) kind = 'usb';
    else if (message.includes('connect one camera only')) kind = 'two';
    else if (message.startsWith('Could not open the camera')) kind = 'open';
    else if (s.usb_mode && s.usb_mode !== 'ORIGINAL') kind = 'mode';
  }
  if (!kind) return null;
  const out = {kind, message, n: null, sub: null, mode: s.usb_mode || ''};
  if (kind === 'two') { const m = /^(\d+)/.exec(message); out.n = m ? Number(m[1]) : 2; }
  if (kind === 'open') {
    const plat = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
    out.sub = message.includes('langid') ? 'langid'
      : message.includes('Access denied') || message.includes('insufficient permissions') ? 'denied'
      : message.includes('not supported') && /^Win/.test(plat) ? 'driver' : 'other';
  }
  return out;
}
export function wrongUsbMode() { return connectProblem()?.kind === 'mode'; }

// Who holds the camera, from the status poll: 'webcam' | 'interval' | 'live' | 'remote', or null when /status names no
// holder (a 409 the poll has not caught up with yet: the caller then says remote control). Words: words.js BUSY.
export function holder() {
  const s = cam.st || {};
  if (s.webcam) return 'webcam';
  if (s.interval && s.interval.running) return 'interval';
  if (s.live && s.live.running) return 'live';
  if (s.tether) return 'remote';
  return null;
}
