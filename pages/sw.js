// TSW Hud Service Worker
//
// Caches the app "shell" (HTML pages, CSS, JS, icons, manifests) so pages
// can still open when the PC isn't reachable. Deliberately does NOT cache
// anything under /api/ - that data goes through IndexedDB (see
// offline-db.js/sync-client.js) so it can be properly merged and synced,
// not just served as a stale cached response.
//
// This file is only ever registered from pages that opt in (see
// install-prompt.js callers) and only actually takes effect over HTTPS -
// browsers refuse Service Worker registration over plain HTTP on any
// device other than the PC itself (see HTTPS_SETUP.md for why). Loading
// this file with no HTTPS is harmless - registration just fails silently
// and the page falls back to normal online-only behaviour, exactly as it
// worked before this existed.
//
// IMPORTANT: this file is served dynamically by app.py (see the
// /pages/sw.js route), NOT as a static file - __APP_VERSION__ below gets
// substituted with the real running APP_VERSION on every request. This
// is what makes CACHE_NAME change on every real update: browsers only
// re-check a Service Worker when its OWN bytes change, so without this,
// installing a new app version would never actually bust the old cached
// pages - the browser would just keep using the already-installed worker
// and its stale cache indefinitely, even though the server has moved on.
// This is not theoretical - it's exactly what happened testing this for
// real: shipping v6.0.0 changed every page's content, but sw.js's own
// bytes never changed, so already-cached devices kept showing old pages.

const CACHE_NAME = 'tsw-hud-shell-__APP_VERSION__';

const SHELL_URLS = [
  '/pages/style.css',
  '/pages/theme.js',
  '/pages/install-prompt.js',
  '/pages/offline-db.js',
  '/pages/sync-client.js',
  '/pages/timetables_browser.html',
  '/pages/train_classes.html',
  '/pages/dashboard_tablet.html',
  '/pages/known_trains.html',
  '/pages/known_trains_edit.html',
  '/pages/known_trains_group.html',
  '/pages/manifest-dashboard.json',
  '/pages/manifest-timetables.json',
  '/pages/manifest-train-classes.json',
  '/pages/icons/dashboard-192.png',
  '/pages/icons/dashboard-512.png',
  '/pages/icons/timetables-192.png',
  '/pages/icons/timetables-512.png',
  '/pages/icons/train_classes-192.png',
  '/pages/icons/train_classes-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).catch(() => {
      // If even one URL fails (e.g. a page that doesn't exist in an older
      // build), don't let that break installation of everything else -
      // best-effort caching, not all-or-nothing.
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never intercept API calls - those need real online/offline handling
  // in the page's own JS (sync-client.js), not a stale cached response.
  if (url.pathname.startsWith('/api/')) return;

  // Only handle GET requests for our own origin's shell assets.
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;

  event.respondWith(
    caches.match(event.request).then((cached) => {
      const networkFetch = fetch(event.request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached); // network failed - fall back to whatever's cached, if anything

      // Cache-first for instant loads when offline; still refreshes the
      // cache in the background from the network when reachable.
      return cached || networkFetch;
    })
  );
});
