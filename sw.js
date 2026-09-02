/* Buy Side Signals service worker.
   Network-first so online visitors always get fresh data/assets; cache is only
   a fallback for offline (and enables install-to-home-screen). */
const CACHE = "bss-v4";
const SHELL = [
  "./", "index.html", "stock.html", "leaps.html",
  "assets/styles.css", "assets/app.js", "assets/leaps.js",
  "assets/stock.js", "assets/filters.js", "assets/favicon.svg",
  "assets/account.js", "assets/supabase-config.js",
  "assets/icon-192.png", "assets/icon-512.png", "assets/apple-touch-icon.png",
  "manifest.webmanifest",
];

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.map((k) => (k !== CACHE ? caches.delete(k) : null))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
