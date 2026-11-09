/* BetFinder service worker: makes the app installable and gives an offline
 * fallback for the app shell.  API responses are never cached, so odds are
 * always live (or clearly unavailable). */
const CACHE = "betfinder-shell-v7";
const SHELL = ["/", "/static/css/style.css", "/static/js/app.js", "/static/js/i18n.js", "/static/js/auth.js", "/static/js/nav.js", "/static/icon.svg", "/static/manifest.json"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // one cache entry per file: the ?v=<version> query only busts the browser cache, so it is left out here
  // (otherwise every release would add another copy, and the offline fallback would miss the shell files)
  const key = url.pathname;
  // network first, cache as fallback (so updates show immediately when online)
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok && (key === "/" || key.startsWith("/static/"))) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(key, copy));
    }
    return res;
  }).catch(() => caches.match(key)));
});
