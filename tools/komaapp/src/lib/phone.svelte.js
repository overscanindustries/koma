// Use on your phone. The bridge's network access (tools/komaset/bridge/phoneaccess.py): off by default,
// switched here at runtime, a QR code with a one-time pairing code, one click to revoke.
//
// On the computer (loopback, no token): GET /phone every 2 s while the section is open, so a phone that pairs shows up.
// On a phone: the QR opens /?pair=<code>; pairFromUrl trades the code once for a token (POST /phone/pair), keeps it
// where bridge.js looks (?token= and localStorage), and reloads; from then on every request carries it.
import {bridgeJson, postJson, lsSet, TOKEN} from './bridge.js';

export const ph = $state({st: null, req: '', err: false, revoked: false});
// state: '' | 'pairing' | 'failed'; why: 'stale' | 'unreachable' | 'computer'; local: this computer's own Koma address
export const pair = $state({state: '', why: '', local: ''});

let timer = null, viewing = false;

export async function refresh() {
  if (TOKEN) return;  // a paired phone does not manage access
  try { ph.st = await bridgeJson('/phone', {timeout: 8000}); } catch (e) { /* the /status poll reports a lost bridge */ }
}
function loop() {
  clearTimeout(timer);
  if (!viewing) return;
  timer = setTimeout(() => refresh().finally(loop), 2000);
}
export function enter() { if (viewing) return; viewing = true; refresh().finally(loop); }
export function leave() { viewing = false; clearTimeout(timer); ph.revoked = false; }

async function send(path, what) {
  if (ph.req) return;
  ph.req = what; ph.err = false; ph.revoked = false;
  try {
    ph.st = await postJson(path, undefined, {timeout: 15000});
    if (what === 'revoke') ph.revoked = true;
  } catch (e) { ph.err = true; }
  finally { ph.req = ''; }
}
export const setOn = on => send(on ? '/phone/on' : '/phone/off', on ? 'on' : 'off');
export const revoke = () => send('/phone/revoke', 'revoke');

// A phone opened the QR code's address: trade the one-time code for this phone's token, then start with it.
export function pairFromUrl() {
  let code = '';
  try { code = new URLSearchParams(location.search).get('pair') || ''; } catch (e) { code = ''; }
  if (!code) return false;
  pair.state = 'pairing';
  (async () => {
    let r = null, d = {};
    try {
      r = await fetch('/phone/pair', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({code}), cache: 'no-store'});
      d = await r.json().catch(() => ({}));
    } catch (e) { r = null; }
    if (r && r.ok && d.token) {
      lsSet('koma.token', d.token);
      location.replace(location.pathname + '?token=' + encodeURIComponent(d.token) + '#/home');
      return;
    }
    try { history.replaceState(null, '', location.pathname + location.hash); } catch (e) { /* keep the address */ }
    // not_lan: the link was opened on the computer itself; the bridge names its own address to go to instead.
    // Any other refusal: the code was used, revoked or replaced by a restart; "not current" covers each.
    pair.state = 'failed'; pair.why = !r ? 'unreachable' : d.code === 'not_lan' ? 'computer' : 'stale';
    pair.local = typeof d.local === 'string' && /^http:\/\/127\.0\.0\.1:\d+\/$/.test(d.local) ? d.local : '';
  })();
  return true;
}
