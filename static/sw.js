const CACHE        = 'spotalert-v611';
const LOGOS_CACHE  = 'airline-logos-v3';  // persistent — never cleared on SW update
const PRECACHE = ['/', '/static/app.js', '/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c =>
      Promise.all(PRECACHE.map(url =>
        fetch(new Request(url, { cache: 'reload' })).then(r => c.put(url, r))
      ))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE && k !== LOGOS_CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  // Airline logos (both local API and legacy CDN) — cache-first, persistent
  if (e.request.url.includes('/api/airline-logo/') || e.request.url.includes('airline-logos')) {
    e.respondWith(
      caches.open(LOGOS_CACHE).then(cache =>
        cache.match(e.request).then(cached => {
          if (cached) return cached;
          return fetch(e.request).then(resp => {
            if (resp && resp.status === 200) cache.put(e.request, resp.clone());
            return resp;
          }).catch(() => new Response('', { status: 404 }));
        })
      )
    );
    return;
  }

  if (e.request.url.includes('/api/')) return;

  // App shell (page, script, styles): network-first so the HTML and app.js always come from the same release — a
  // cache-first page next to a freshly fetched script rendered new markup with old CSS. The cache is the offline fallback.
  e.respondWith(
    fetch(e.request).then(resp => {
      if (resp && resp.status === 200 && e.request.method === 'GET' && new URL(e.request.url).origin === self.location.origin) {
        const copy = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
      }
      return resp;
    }).catch(() => caches.match(e.request, { ignoreSearch: true }))
  );
});

// ── Push notifications ────────────────────────────────────────────────────
self.addEventListener('push', e => {
  let data = { title: 'SpotAlert', body: '', data: {}, actions: [] };
  try { data = { ...data, ...e.data.json() }; } catch {}
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      data: data.data,
      actions: data.actions || [],
      tag: data.data?.registration || 'spotalert',
      renotify: true,
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const reg = e.notification.data?.registration || '';
  const isSpottingReminder = !!e.notification.data?.spotting_reminder;
  const url = isSpottingReminder ? '/?spotting=tomorrow' : (reg ? `/?flight=${reg}` : '/');
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(ws => {
      const existing = ws.find(w => w.url.startsWith(self.location.origin));
      if (existing) { existing.focus(); existing.navigate(url); return; }
      return clients.openWindow(url);
    })
  );
});
