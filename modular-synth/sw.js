/* Witchwave — service worker: офлайн-режим.
 * Кэшируем app-shell при установке, отдаём из кэша, обновляем в фоне. */
const CACHE = 'witchwave-v1';
const ASSETS = [
  './',
  './index.html',
  './style.css',
  './synth.js',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-192-maskable.png',
  './icons/icon-512-maskable.png',
  './apple-touch-icon.png'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Стратегия: кэш сначала (для speed), сеть как источник истины, fallback на кэш (для offline).
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  // Только свои ресурсы (без внешних запросов).
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Навигация: пробуем сеть, при падении — кэш index.html.
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put('./index.html', copy));
        return res;
      }).catch(() => caches.match('./index.html').then((r) => r || caches.match('./')))
    );
    return;
  }

  // Остальные ассеты: кэш -> сеть (и запоминаем в кэш).
  e.respondWith(
    caches.match(req).then((hit) => {
      if (hit) return hit;
      return fetch(req).then((res) => {
        if (res && res.status === 200 && res.type === 'basic') {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      });
    })
  );
});
