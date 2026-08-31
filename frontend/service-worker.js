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

// v9: o `activate` apaga todo cache de nome diferente deste, então bumpar a
// versão é o que REMOVE do aparelho o que o v8 já tinha guardado. Não é
// cosmético — sem o bump, a lista nova só impede gravação NOVA e o dado
// (linha de prova do gate — este commit NÃO bumpa de propósito)
// privado que já está lá continua lá.
const CACHE_NAME = "pigbank-v9";

// Pré-cache do casco. O Chart.js do cdnjs SAIU daqui de propósito:
// `cache.addAll` rejeita INTEIRO se qualquer item falhar, então CDN fora do ar,
// rede ruim ou CSP bloqueando fazia o service worker não instalar — e a PWA
// ficava sem offline nenhum, em silêncio. Ele continua sendo cacheado em
// runtime pelo `podeCachear` (é asset público, sem dado de usuário).
const PRECACHE = [
  "/",
  "/manifest.json",
  "/brand/icon-192.png",
];

// ── Quem pode entrar no cache ────────────────────────────────────────────
//
// ALLOWLIST, e a inversão é o conserto. Até aqui isto era uma blocklist
// (`SKIP_CACHE`) de 15 prefixos, e blocklist falha ABERTO: rota nova nasce
// cacheável. O inventário das rotas GET autenticadas que escapavam da lista —
// todas passando por `authorize_dashboard_access`, todas indo para o Cache
// Storage do aparelho:
//
//   /ai/messages (histórico do chat — texto livre do usuário)
//   /account/{id}/setup-status (devolve o `balance` da conta)
//   /analytics/{id}/* — 7 rotas: KPIs, evolução, categorias, padrão semanal,
//                       top merchants (onde a pessoa gasta), insights, patterns
//   /cards/{id}/summary · /bills/{id} · /bills/{id}/{bill_id}
//   /installments/{id}/list · os dois delete-impact
//   /goals/{id}/status · /pockets/{id}/{nome}/history
//   /recurring-bills/{id} + /projection · /recurring-expenses/{id}
//   /recurring-incomes/{id} · /expenses/daily/{id} · /forecast/{id}
//   /categories/{id} · /billing/subscription · /api/affiliate/me
//   /debug/ai/{id}/payload
//
// Isso contradizia o "HTML / auth: never cached" do topo deste arquivo e o
// motivo dado para `/history/` e `/open-finance/` estarem na lista antiga.
// Com allowlist a falha é FECHADA: rota nova nasce fora do cache, e ninguém
// precisa lembrar de acrescentá-la a lista nenhuma.
//
// Efeito colateral que some junto: o prefixo "/app" da lista antiga casava
// `/app-mode.css` e `/app-mode.js` por `startsWith`, e os dois nunca eram
// cacheados — por acidente, não por decisão.
const EXT_CACHEAVEL = /\.(css|js|mjs|png|jpg|jpeg|gif|svg|webp|ico|woff2?|ttf|otf)$/i;
const DESTINO_CACHEAVEL = new Set(["style", "script", "font", "image"]);

function podeCachear(request, url) {
  // Asset público de CDN: sem dado de usuário, e é o que segura o gráfico do
  // dashboard offline.
  if (url.hostname.includes("cdnjs")) return true;
  if (url.origin !== self.location.origin) return false;
  if (url.pathname === "/manifest.json") return true;
  if (EXT_CACHEAVEL.test(url.pathname)) return true;
  // Sobra o que o navegador declara como asset. Resposta de API tem
  // `destination` vazio, então não entra por aqui.
  return DESTINO_CACHEAVEL.has(request.destination);
}

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

  // Skip non-GET, navigations, WS
  if (request.method !== "GET") return;
  if (request.mode === "navigate") return;
  if (url.protocol === "ws:" || url.protocol === "wss:") return;
  // Tudo que não é asset estático PASSA DIRETO, sem `respondWith`. Além de não
  // ser cacheado, a falha de rede chega ao app como falha de rede de verdade —
  // antes o ramo de fallback devolvia um 503 com corpo "Offline" no lugar da
  // resposta da API, e quem lia `detail` do JSON recebia texto solto.
  if (!podeCachear(request, url)) return;

  // Network-first strategy
  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(() =>
        // Network failed — serve from cache
        caches.match(request).then(cached => {
          if (cached) return cached;
          return new Response("Offline", { status: 503, statusText: "Service Unavailable" });
        })
      )
  );
});

// A limpeza de logout NÃO mora aqui, e isso é decisão, não esquecimento: o
// aparelho que tem cache privado é o controlado por um worker ANTIGO, e worker
// antigo não escuta `message` nenhum — a mensagem cairia no vazio exatamente
// quando importa (Codex, #170). Quem apaga é a página, pela CacheStorage:
// `auth-refresh.js` nas telas autenticadas e `nav-auth.js` nas públicas.

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
