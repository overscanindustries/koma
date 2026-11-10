// The Webcam area's state. The bridge runs the webcam (tools/komaset/bridge/webcamhost.py: komacam in
// the bridge's process), so it keeps running when this page closes; this module only reads it and switches it.
// GET /webcam once a second while the page is open; ?check=1 when it opens and when the window gets focus back (the
// person may have just installed OBS or allowed its extension). Nothing here touches the camera except Start and
// Stop, which the bridge turns into POST /tether/start, GET /live?mode=1 and POST /tether/stop.
import {bridgeJson, postJson} from './bridge.js';
import {cam} from './camera.svelte.js';
import {WC, webcamDevice} from './webcamWords.js';

// The webcam holds the camera (on, or still stopping): /status.webcam, which every screen already polls. Shoot waits
// while it does (Shell), and the app stays on Webcam when the camera goes away mid-call (App).
export function webcamHolds() { return !!(cam.st && cam.st.webcam); }

export const wc = $state({
  st: null,        // the last GET /webcam
  req: '',         // 'start' or 'stop' while that request is out
  err: false,      // the last request got no answer
  phase: '',       // the phase shown, and when it began (the status line counts from it)
  phaseT0: 0,
});

let timer = null, viewing = false;

export async function refresh(check = false) {
  try {
    wc.st = await bridgeJson('/webcam' + (check ? '?check=1' : ''), {timeout: 8000});
  } catch (e) { /* the /status poll reports a lost bridge */ }
}

function loop() {
  clearTimeout(timer);
  if (!viewing) return;
  timer = setTimeout(() => refresh().finally(loop), 1000);
}
function onFocus() { refresh(true); }

export function enter() {
  if (viewing) return;
  viewing = true;
  refresh(true).finally(loop);
  window.addEventListener('focus', onFocus);
}
export function leave() {
  viewing = false;
  clearTimeout(timer);
  window.removeEventListener('focus', onFocus);
}

export async function setOn(on) {
  if (wc.req) return;
  wc.req = on ? 'start' : 'stop'; wc.err = false;
  try { wc.st = await postJson(on ? '/webcam/start' : '/webcam/stop', undefined, {timeout: 15000}); }
  catch (e) { wc.err = true; }
  finally { wc.req = ''; }
}

// What the page shows, from the bridge's answer. phase: off, waking, live, nocam, nolive, stop. phase follows komacam's
// supervisor (the bridge returns it as `phase`): live while its stream is open, frames fresh or paused (a paused stream
// keeps the spec sheet with the last frame's age); nocam when the camera has gone away.
export function view(st, req) {
  const s = st || {};
  const on = req === 'start' || (!!s.on && req !== 'stop');
  const stopping = req === 'stop' || !!s.stopping;
  const fresh = !!(s.on && s.live && !stopping);   // a camera frame newer than 1.5 s (komacam's stale limit)
  let phase = 'off';
  if (stopping) phase = 'stop';
  else if (s.on && s.phase === 'streaming') phase = 'live';
  else if (s.on && s.phase === 'no camera') phase = 'nocam';
  // no live view: /live refused now (live_off counts refusals since the last stream), or no arm has ever given a frame
  else if (s.on && ((s.live_off || 0) > 0 || ((s.arm_failures || 0) > 0 && !(s.frames > 0)))) phase = 'nolive';
  else if (on) phase = 'waking';
  const device = s.device || webcamDevice(s.backend);
  const vc = s.vcam || {};
  const readers = s.readers || 0;   // OBS's Browser source or the macOS extension reading 127.0.0.1:8770
  const taking = !!vc.open || readers > 0;   // something a call can use takes komacam's output
  // amber (principle 5): the camera's frames flow and something a call can use takes them
  const flowing = fresh && taking;
  // Receiving only while the call gets the camera's picture; an open virtual camera showing the state card is Ready.
  let vdev = WC.starting;
  if (flowing) vdev = WC.receiving;
  else if (taking || s.backend === 'extension') vdev = WC.ready;
  else if (vc.issue) vdev = WC.notStarted;
  else if (s.on && !vc.used) vdev = WC.notAvailable;
  const all = s.steps || [];
  // vcam_other (an error with no step of its own) and any id these words do not know: the status line says it
  const steps = all.filter(x => WC.step[x.id]);
  // the camera streams but nothing takes it: the status line says so, and Setup holds the fix
  const vcamDown = phase === 'live' && !taking && all.length > 0;
  const age = s.frame_age_s;
  return {
    on, fresh, flowing, stopping, phase, device, vdev, vcamDown, steps,
    last: age == null ? WC.noneYet : age <= 1.5 ? WC.now : WC.secondsAgo(Math.round(age)),
    stale: age != null && age > 1.5,
  };
}

// Copy to the clipboard, phone-safe: the shared CopyButton's.
export {copyText} from '../components/ui/CopyButton.svelte';
