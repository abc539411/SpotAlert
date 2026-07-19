const CACHE        = 'spotalert-v588';
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

  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
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
