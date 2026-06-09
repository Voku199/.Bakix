const CACHE = 'bakix-v1';
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
  if (e.request.method !== 'GET') return;
  if (e.request.url.includes('/api/')) return;
  e.respondWith(
    fetch(e.request)
      .then(function (r) {
        var clone = r.clone();
        caches.open(CACHE).then(function (c) { c.put(e.request, clone); });
        return r;
      })
      .catch(function () { return caches.match(e.request); })
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
