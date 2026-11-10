// The local Koma bridge (tools/komaset/bridge/komabridge.py). The app never talks to USB itself.
// Served by the bridge (the normal case), every call goes to the page's own origin. Run from `vite dev`, the dev
// server proxies the API to the bridge (vite.config.js). Phone mode (--lan): the ?token= in the address goes on every
// request, image and beacon, and is remembered for a start from the home screen.

const TOKEN_KEY = 'koma.token';

export function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
export function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage off: fine */ } }

const urlToken = (() => { try { return new URLSearchParams(location.search).get('token') || ''; } catch (e) { return ''; } })();
if (urlToken) lsSet(TOKEN_KEY, urlToken);
export const TOKEN = urlToken || lsGet(TOKEN_KEY) || '';

export function bridgeUrl(path) {
  return path + (TOKEN ? (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN) : '');
}

// status 0: no HTTP answer. timeout: the bridge took the connection but did not answer in time (alive, busy: the first
// /status after a plug-in opens the camera, about 2 s on hardware); otherwise refused or offline.
export class BridgeError extends Error {
  constructor(message, status, data, timeout = false) {
    super(message); this.status = status; this.data = data || {}; this.timeout = timeout;
  }
}

export async function bridgeFetch(path, opts = {}) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), opts.timeout || 60000);
  try {
    return await fetch(bridgeUrl(path), {method: opts.method || 'GET', body: opts.body, headers: opts.headers,
      signal: ctl.signal, cache: 'no-store'});
  } finally { clearTimeout(t); }
}

export async function bridgeJson(path, opts = {}) {
  let r;
  try { r = await bridgeFetch(path, opts); }
  catch (e) {
    const t = !!(e && e.name === 'AbortError');
    throw new BridgeError(t ? 'timeout' : 'offline', 0, null, t);
  }
  let d = {};
  try { d = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new BridgeError(d.error || ('http ' + r.status), r.status, d);
  return d;
}

export function postJson(path, body, opts = {}) {
  return bridgeJson(path, {...opts, method: 'POST', headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
}

export function b64decode(s) {
  const t = atob(s), b = new Uint8Array(t.length);
  for (let i = 0; i < t.length; i++) b[i] = t.charCodeAt(i);
  return b;
}
export function b64encode(bytes) {
  let bin = '';
  bytes.forEach(x => { bin += String.fromCharCode(x); });
  return btoa(bin);
}

// Remote control off when the page goes away (the bridge also lets every key go itself).
export function beaconStop() {
  const url = bridgeUrl('/tether/stop');
  let sent = false;
  try { sent = navigator.sendBeacon && navigator.sendBeacon(url); } catch (e) { /* no beacon */ }
  if (!sent) { try { fetch(url, {method: 'POST', keepalive: true}); } catch (e) { /* page closing */ } }
}
