/**
 * PigBank – Service Worker
 *
 * Strategy:
 *   - HTML / auth: never cached
 *   - Assets: network-first with cache fallback
 *   - API / WebSocket: Never cached (pass-through)
 *
 * This allows the dashboard shell to load even when offline,
 * showing the last cached state. WebSocket reconnects automatically
 * once the network is restored.
 */

const CACHE_NAME = "pigbank-v7";

// Resources to pre-cache on install
const PRECACHE = [
  "/",
  "/manifest.json",
  "/brand/icon-192.png",
  "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js",
];

// Never cache these (API calls, WebSocket upgrades, dados financeiros).
// Open Finance e histórico trazem saldo/transações do banco: nunca cachear
// (privacidade no dispositivo + evita mostrar dado velho depois de um sync).
const SKIP_CACHE = [
  "/app",
  "/settings",
  "/auth/",
  "/ws/",
  "/export/",
  "/budgets/",
  "/data/",
  "/open-finance/",
  "/history/",
  "/agents/",
  "/health",
  "/admin",
  "/admin/",
];

/* ── Install: pre-cache shell ────────────────────────────────────────── */
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

/* ── Activate: clean up old caches ──────────────────────────────────── */
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== CACHE_NAME)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

/* ── Fetch: network-first for HTML/assets, skip for API ─────────────── */
self.addEventListener("fetch", event => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET, navigations, auth, API and WS routes
  if (request.method !== "GET") return;
  if (request.mode === "navigate") return;
  if (SKIP_CACHE.some(p => url.pathname.startsWith(p))) return;
  if (url.protocol === "ws:" || url.protocol === "wss:") return;

  // Network-first strategy
  event.respondWith(
    fetch(request)
      .then(response => {
        // Only cache successful same-origin and CDN responses
        if (response.ok && (url.origin === self.location.origin || url.hostname.includes("cdnjs"))) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(() =>
        // Network failed — serve from cache
        caches.match(request).then(cached => {
          if (cached) return cached;
          // For navigation requests, return the cached root
          if (request.mode === "navigate") return caches.match("/");
          return new Response("Offline", { status: 503, statusText: "Service Unavailable" });
        })
      )
  );
});

/* ── Push notifications (optional, for budget alerts) ───────────────── */
self.addEventListener("push", event => {
  if (!event.data) return;
  let data;
  try { data = event.data.json(); } catch { data = { title: "PigBank", body: event.data.text() }; }
  event.waitUntil(
    self.registration.showNotification(data.title || "PigBank", {
      body:  data.body  || "",
      icon:  "/brand/icon-192.png",
      badge: "/brand/icon-192.png",
      tag:   data.tag   || "pigbank-alert",
      data:  { url: "/app" },
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/app";
  event.waitUntil(
    clients.matchAll({ type: "window" }).then(wins => {
      if (wins.length) return wins[0].focus();
      return clients.openWindow(url);
    })
  );
});
