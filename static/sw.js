// Keeps a copy of the app on the device, so it opens with no network. The budget is never here:
// it is in the page's own storage, or on the server. A new version of spare brings a new copy.
const VERSION = "__VERSION__";
const FILES = [
  "/",
  `/static/style.css?v=${VERSION}`,
  `/static/spare.js?v=${VERSION}`,
  "/static/logo.svg",
  "/static/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches
      .open(VERSION)
      .then((cache) => cache.addAll(FILES))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Only the app's own files come from the copy; logins and budgets always go to the server.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  const path = url.pathname === "/" ? "/" : url.pathname + url.search;
  if (!FILES.includes(path)) return;
  e.respondWith(caches.match(path).then((copy) => copy || fetch(e.request)));
});
