/**
 * frontend/auth-refresh.js — Interceptor global de fetch que renova access
 * automaticamente em 401.
 *
 * Estratégia: monkey-patch window.fetch. Em request 401 que NÃO seja o próprio
 * /auth/refresh, dispara POST /auth/refresh, e re-tenta a request original.
 * Se refresh falhar, deixa o 401 passar pro caller decidir (geralmente redireciona
 * pra login). Refresh em paralelo é deduplicado.
 *
 * Servido como arquivo externo em /static/auth-refresh.js — incluído via <script>
 * no <head> das páginas autenticadas (dashboard, home, settings, onboarding).
 *
 * Também expõe `window.pbCsrfHeaders` — a implementação compartilhada do header
 * de CSRF, pra código novo não virar mais uma cópia (ver o bloco no fim).
 */
(() => {
  const _origFetch = window.fetch;
  let _refreshPromise = null;

  function _isOwnApi(url) {
    if (typeof url !== "string") url = (url && url.url) || "";
    if (!url) return false;
    if (url.startsWith("/")) return true;
    try {
      const u = new URL(url, window.location.origin);
      return u.host === window.location.host;
    } catch (_) { return false; }
  }

  function _isRefreshEndpoint(url) {
    if (typeof url !== "string") url = (url && url.url) || "";
    return url.includes("/auth/refresh");
  }

  function _getCsrfToken() {
    const m = document.cookie.split("; ").find(r => r.startsWith("csrf_token="));
    return m ? decodeURIComponent(m.split("=")[1]) : "";
  }

  async function _doRefresh() {
    if (_refreshPromise) return _refreshPromise;
    _refreshPromise = (async () => {
      try {
        const csrf = _getCsrfToken();
        const headers = { "Content-Type": "application/json" };
        if (csrf) headers["X-CSRF-Token"] = csrf;
        const r = await _origFetch("/auth/refresh", {
          method: "POST",
          credentials: "same-origin",
          headers,
        });
        return r.ok;
      } catch (_) {
        return false;
      } finally {
        // Solta o lock na próxima volta do event loop
        setTimeout(() => { _refreshPromise = null; }, 0);
      }
    })();
    return _refreshPromise;
  }

  /** URL desta request, para as duas checagens abaixo. "" quando não dá. */
  function _caminho(input) {
    try {
      const u = typeof input === "string" ? input : (input && input.url) || "";
      return new URL(u, location.origin).pathname;
    } catch (_) { return ""; }
  }

  /**
   * Logout bem-sucedido → apaga o Cache Storage deste dispositivo.
   *
   * DA PÁGINA, não por mensagem ao service worker. O caso que justifica esta
   * limpeza é justamente o aparelho ainda controlado por um worker ANTIGO —
   * que é onde o cache privado está —, e worker antigo não tem listener de
   * `message`: o `postMessage` cairia no vazio exatamente quando importa
   * (Codex, #170). A CacheStorage é acessível da janela, então apagar daqui
   * funciona com worker velho, com worker novo e sem worker nenhum.
   *
   * Este helper existe em DOIS arquivos, de propósito: aqui, para as páginas
   * autenticadas (dashboard, home, settings, comecar), e em `nav-auth.js`,
   * para as 12 páginas públicas — que carregam o nav-auth e NÃO carregam este
   * arquivo, então o logout do menu de conta delas não passa por aqui. A
   * duplicação é comparada por `tests/frontend/sw_cache_privado.test.mjs`
   * (§0.7): se um dos dois parar de limpar, o teste fica vermelho.
   *
   * Cinto e suspensório sobre a allowlist do `service-worker.js` — com ela só
   * asset estático entra no cache, e asset estático não tem dado de ninguém.
   * O que esta limpeza alcança é o que ficou gravado ANTES.
   */
  function _limpaCacheNoLogout() {
    try {
      if (!window.caches) return Promise.resolve();
      return caches.keys()
        .then(function (ks) { return Promise.all(ks.map(function (k) { return caches.delete(k); })); })
        .catch(function () {});
    } catch (_) { /* CacheStorage indisponível: não há cache a limpar */ }
    return Promise.resolve();
  }

  window.fetch = async function(input, init) {
    // Requests pra fora da própria origem (Stripe, CDN, etc) seguem direto
    if (!_isOwnApi(input)) return _origFetch(input, init);

    let resp = await _origFetch(input, init);

    // AGUARDA antes de devolver a resposta. Quem chama o logout navega assim
    // que o fetch resolve (`location.replace`/`reload`), e navegação descarta
    // o documento: uma limpeza disparada e esquecida não tem garantia de
    // terminar, e o cache privado sobrevive ao logout no aparelho compartilhado
    // — o cenário inteiro que justifica esta limpeza (Codex, #170). Segurar o
    // `await` aqui é o que empurra a navegação para depois do delete.
    if (resp.ok && _caminho(input) === "/auth/logout") await _limpaCacheNoLogout();

    if (resp.status !== 401) return resp;

    // 401 no próprio refresh: não tenta de novo — caller redireciona pro login
    if (_isRefreshEndpoint(input)) return resp;

    // Tenta renovar e refazer a request original
    const ok = await _doRefresh();
    if (!ok) return resp;
    return _origFetch(input, init);
  };

  // ── Modo app (iOS/Capacitor) ─────────────────────────────────────────────
  // O WebView do app anexa "PigBankApp" ao user agent. Dentro do app as
  // diretrizes da App Store proíbem link/CTA de compra externa, então os
  // anchors pra /precos somem via CSS e as páginas usam window.PB_IN_APP
  // pra trocar o redirect de paywall por uma tela neutra.
  // Exceção: elementos marcados com .pb-keep-in-app continuam visíveis — ex.:
  // o botão "Ver planos" do modal de paywall do dashboard, que precisa
  // redirecionar o usuário sem plano pra tela de planos.
  if (/PigBankApp/.test(navigator.userAgent)) {
    window.PB_IN_APP = true;
    document.documentElement.classList.add("pb-app");
    const st = document.createElement("style");
    st.textContent = 'html.pb-app a[href^="/precos"]:not(.pb-keep-in-app){display:none !important}';
    (document.head || document.documentElement).appendChild(st);
  }

  // ── CSRF ────────────────────────────────────────────────────────────────
  // Implementação única, exposta pras páginas que carregam este arquivo. O
  // helper está copiado em 8 lugares hoje (home.html, dashboard.js,
  // completar-cadastro.html, cadastro.html, login.html, reset-password.html,
  // nav-auth.js, admin-login.html); esta é a versão compartilhada pro código
  // novo não virar a nona cópia. Consolidar as 8 é PR de limpeza próprio.
  window.pbGetCookie = function (name) {
    const hit = document.cookie
      .split("; ")
      .find((row) => row.indexOf(name + "=") === 0);
    return hit ? hit.split("=")[1] : "";
  };

  window.pbCsrfHeaders = function (extra) {
    const headers = Object.assign({}, extra || {});
    const token = decodeURIComponent(window.pbGetCookie("csrf_token") || "");
    if (token) headers["X-CSRF-Token"] = token;
    return headers;
  };
})();
