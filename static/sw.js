const CACHE = 'spotalert-v1';
const PRECACHE = ['/', '/static/app.js', '/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.url.includes('/api/')) return; // never cache API calls
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
  const url = reg ? `/?flight=${reg}` : '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(ws => {
      const existing = ws.find(w => w.url.startsWith(self.location.origin));
      if (existing) { existing.focus(); existing.navigate(url); return; }
      return clients.openWindow(url);
    })
  );
});
