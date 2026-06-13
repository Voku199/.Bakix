const CACHE = 'bakix-v2';
const PRECACHE = ['/static/bakix.svg', '/static/js/main.js'];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) { return c.addAll(PRECACHE); })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;

  var url = new URL(req.url);

  // Only ever touch our own origin. Cross-origin assets (jsDelivr, Google Fonts)
  // and browser-extension requests (chrome-extension:) must go straight to the
  // network: re-fetching them here trips the page CSP (connect-src 'self') and
  // cache.put() rejects unsupported schemes. The browser loads those <script>/
  // <link> tags natively under script-src/style-src/font-src anyway.
  if (url.origin !== self.location.origin) return;

  // Never cache API traffic — always live.
  if (url.pathname.indexOf('/api/') === 0) return;

  e.respondWith(
    fetch(req)
      .then(function (r) {
        var clone = r.clone();
        caches.open(CACHE).then(function (c) { c.put(req, clone); });
        return r;
      })
      // Offline / network failure: serve from cache, or a clean error response
      // (returning undefined here is what threw "Failed to convert value to 'Response'").
      .catch(function () {
        return caches.match(req).then(function (cached) { return cached || Response.error(); });
      })
  );
});

self.addEventListener('push', function (e) {
  var data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) {}

  var title   = data.title || 'Bakix';
  var options = {
    body:     data.body || '',
    icon:     '/static/bakix.svg',
    badge:    '/static/bakix.svg',
    tag:      data.tag || 'bakix',   // per-category — different types stack independently
    renotify: true,
    vibrate:  [180, 90, 180],
    data:     { url: data.url || '/' },
  };

  e.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('pushsubscriptionchange', function (e) {
  e.waitUntil(
    self.registration.pushManager.subscribe({
      userVisibleOnly:      true,
      applicationServerKey: e.oldSubscription.options.applicationServerKey,
    }).then(function (sub) {
      return fetch('/api/push/subscribe', {
        method:      'POST',
        headers:     { 'Content-Type': 'application/json' },
        credentials: 'include',
        body:        JSON.stringify(sub.toJSON()),
      });
    })
  );
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var targetUrl = (e.notification.data && e.notification.data.url) || '/';

  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      if (list.length > 0) {
        // App already open — focus it and tell the page to scroll/navigate.
        var c = list[0];
        return c.focus().then(function () {
          c.postMessage({ type: 'push-navigate', url: targetUrl });
        });
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});
