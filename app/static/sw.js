/* BetFinder service worker: makes the app installable and gives an offline
 * fallback for the app shell.  API responses are never cached, so odds are
 * always live (or clearly unavailable). */
const CACHE = "betfinder-shell-v3";
const SHELL = ["/", "/static/css/style.css", "/static/js/app.js", "/static/js/i18n.js", "/static/icon.svg", "/static/manifest.json"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // network first, cache as fallback (so updates show immediately when online)
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok && (url.pathname === "/" || url.pathname.startsWith("/static/"))) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(url.pathname === "/" ? "/" : e.request, copy));
    }
    return res;
  }).catch(() => caches.match(url.pathname === "/" ? "/" : e.request)));
});
