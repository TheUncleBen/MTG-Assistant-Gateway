/* Service worker for /scan: caches the page's own static assets (including the
   OCR engine and language data, ~8 MB) so repeat visits and the installed app
   do not re-download them. Everything else (the page, the API, sign-in) always
   goes to the network: nothing authenticated is ever cached here. */
'use strict';
// The page registers sw.js?v=<asset version>; a new version activates a new cache and drops the old one.
const CACHE = 'scan-static-' + (new URL(self.location.href).searchParams.get('v') || 'v1');
const SCOPE_STATIC = '/scan/static/';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith(SCOPE_STATIC)) return; // network only
  const versioned = url.pathname.startsWith(SCOPE_STATIC + 'vendor/') || url.searchParams.has('v');
  if (!versioned) return;
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const hit = await cache.match(event.request);
      if (hit) return hit;
      const resp = await fetch(event.request);
      if (resp.ok) cache.put(event.request, resp.clone());
      return resp;
    }),
  );
});
