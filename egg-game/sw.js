/* Волк ловит яйца — офлайн-оболочка.
   Network-first: в сети свежие файлы, без сети — копия из кэша.
   Кэш имеет собственный префикс, чтобы не задеть другие игры на том же origin. */
"use strict";

const CACHE = "egg-game-v1.0.0";
const CACHE_PREFIX = "egg-game-";
const ASSETS = [
  "./",
  "./index.html",
  "./style.css",
  "./game.js",
  "./manifest.webmanifest",
  "./icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  const scope = new URL(self.registration.scope);
  if (url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return;
  if (url.protocol !== "http:" && url.protocol !== "https:") return;

  const indexURL = new URL("index.html", scope).href;
  const network = fetch(request).then(async (response) => {
    if (response.ok && response.type === "basic") {
      const copy = response.clone();
      try {
        const cache = await caches.open(CACHE);
        await cache.put(request, copy);
      } catch { /* Quota/private mode: network response is still usable. */ }
    }
    return response;
  });

  event.waitUntil(network.catch(() => {}));
  event.respondWith(
    network.catch(async () => {
      const cached = await caches.match(request);
      if (cached) return cached;
      if (request.mode === "navigate") {
        const shell = await caches.match(indexURL);
        if (shell) return shell;
      }
      return Response.error();
    })
  );
});
