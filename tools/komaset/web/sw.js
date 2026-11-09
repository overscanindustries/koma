// Koma Setup offline shell. Caches the page, the settings list and the manifest (plus the Koma module and its
// SHA256SUMS once the page has fetched them), so the file-based tasks open without a network: Copy, Change,
// Looks on a file, Set up a card, Back up. Everything else, above all the bridge's camera calls, goes to the
// network untouched. Network first for the cached files, so a running bridge always serves the current page;
// the cache answers when the network fails or refuses (a launch from the home screen carries no ?token=).
// Registered by the page only over http(s), with the page's ?token= on the script URL (phone mode needs it).
'use strict';
const CACHE = 'koma-setup-v1';
const TOKEN = new URL(self.location.href).searchParams.get('token') || '';
const FROM_BRIDGE = new URL('./', self.location.href).pathname === '/';  // komabridge.py serves the page at /
const SHELL = FROM_BRIDGE ? ['./', './settings-map.json', './manifest.webmanifest']
  : ['./koma-setup.html', '../settings-map.json', './manifest.webmanifest'];
const LATER = ['../../build/koma04.grmod', '../../build/koma04w.grmod', '../../build/SHA256SUMS',
  '../../../build/koma04.grmod', '../../../build/koma04w.grmod', '../../../build/SHA256SUMS'];

function key(u) { const x = new URL(u, self.location.href); x.search = ''; x.hash = ''; return x.href; }
function withToken(u) {
  const x = new URL(u, self.location.href);
  if (TOKEN) x.searchParams.set('token', TOKEN);
  return x.href;
}
const KEYS = new Set(SHELL.concat(LATER).map(key));

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => Promise.all(SHELL.map(u =>
    fetch(withToken(u), {cache: 'no-store'}).then(r => (r.ok ? c.put(key(u), r) : null)).catch(() => null))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const k = key(req.url);
  if (!KEYS.has(k)) return;  // the bridge's camera calls and everything else: not ours
  e.respondWith(fetch(req).then(r => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(k, copy)); return r; }
    return caches.match(k).then(m => m || r);
  }, () => caches.match(k).then(m => m || Response.error())));
});
