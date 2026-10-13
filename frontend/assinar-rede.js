/**
 * Pedidos de rede da /assinar, sem estado da página (o estado mora no assinar.js). Síncrono e ANTES do
 * assinar.js no <head>: ele lê `window.PBAssinarRede` na carga. Publica `post` e `sessaoViva`.
 */
(function () {
  "use strict";

  /** POST da nossa API. Nunca lança: falha de rede volta como status 0. */
  async function post(url, corpo) {
    try {
      // pbCsrfHeaders relê o cookie a cada chamada: o logout reemite o CSRF.
      const r = await fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: window.pbCsrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(corpo || {}),
      });
      let d = null;
      try { d = await r.json(); } catch (_) { /* corpo vazio ou não-JSON */ }
      return { status: r.status, ok: r.ok, d: d && typeof d === "object" ? d : {} };
    } catch (_) {
      return { status: 0, ok: false, d: {} };
    }
  }

  /** O e-mail logado AGORA (carga ou outra aba); "" só no 401; null se não deu para saber (rede,
   *  5xx, `ms`, JSON quebrado). O S2 desloga com e-mail ou null (o /login devolveria a conta logada);
   *  com "" não (o Clear-Site-Data apagaria o _fbc). Via auth-refresh: 401 sem refresh limpa o storage
   *  → begin() depois. `ms` só no clique (o `voo` não fica preso); a carga espera (plano §3, C). */
  function sessaoViva(ms) {
    const c = new AbortController(), t = ms && setTimeout(function () { c.abort(); }, ms);
    return fetch("/auth/me", { credentials: "same-origin", signal: c.signal }).then(function (r) {
      if (r.status === 401) return "";
      return r.ok ? r.json().then(function (s) { return s && typeof s.email === "string" ? s.email : ""; }) : null;
    }).catch(function () { return null; }).finally(function () { clearTimeout(t); });
  }

  window.PBAssinarRede = { post: post, sessaoViva: sessaoViva };
})();
