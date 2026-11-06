/* Service worker — Carte du front Ukraine.
   Stratégie « réseau d'abord » : en ligne, on sert toujours la version fraîche
   (site mis à jour chaque jour) ; hors-ligne, on sert la dernière copie en cache.
   N'intercepte que le même domaine en GET : analytics, polices et tuiles passent intacts. */
const CACHE = "uaf-v1";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (e) => e.waitUntil((async () => {
  const keys = await caches.keys();
  await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
  await self.clients.claim();
})()));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // laisse passer GoatCounter, Mapbox, etc.
  e.respondWith((async () => {
    try {
      const net = await fetch(req);
      if (net && net.ok && net.type === "basic") {
        const c = await caches.open(CACHE);
        c.put(req, net.clone());
      }
      return net;
    } catch (err) {
      const hit = await caches.match(req);
      if (hit) return hit;
      if (req.mode === "navigate") {
        const home = await caches.match("./") || await caches.match("index.html");
        if (home) return home;
      }
      throw err;
    }
  })());
});
