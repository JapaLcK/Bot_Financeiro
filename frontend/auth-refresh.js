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

  window.fetch = async function(input, init) {
    // Requests pra fora da própria origem (Stripe, CDN, etc) seguem direto
    if (!_isOwnApi(input)) return _origFetch(input, init);

    let resp = await _origFetch(input, init);
    if (resp.status !== 401) return resp;

    // 401 no próprio refresh: não tenta de novo — caller redireciona pro login
    if (_isRefreshEndpoint(input)) return resp;

    // Tenta renovar e refazer a request original
    const ok = await _doRefresh();
    if (!ok) return resp;
    return _origFetch(input, init);
  };
})();
