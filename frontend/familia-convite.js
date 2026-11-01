/**
 * Continuidade do convite do Plano Família entre /familia/entrar, login e cadastro.
 *
 * Guarda só o token do convite, nesta aba (sessionStorage), por 2 horas. O token nunca
 * vai para URL, localStorage nem log. Molde: purchase-intent.js.
 */
(function () {
  "use strict";

  const KEY = "pb_familia_convite_v1";
  const MAX_AGE_MS = 2 * 60 * 60 * 1000;
  const TOKEN = /^[A-Za-z0-9_-]{1,256}$/;
  const ENTRAR = "/familia/entrar";

  function limpar() {
    try { window.sessionStorage.removeItem(KEY); } catch (_) {}
  }

  // O token guardado, ou null (ausente, vencido, fora do formato).
  function ler() {
    let raw = null;
    try { raw = window.sessionStorage.getItem(KEY); } catch (_) { return null; }
    if (!raw) return null;
    try {
      const v = JSON.parse(raw);
      const idade = Date.now() - v.criadoEm;
      if (typeof v.t === "string" && TOKEN.test(v.t) && idade <= MAX_AGE_MS && idade >= -60000) return v.t;
    } catch (_) {}
    limpar();
    return null;
  }

  function guardar(token) {
    if (typeof token !== "string" || !TOKEN.test(token)) return false;
    try { window.sessionStorage.setItem(KEY, JSON.stringify({ t: token, criadoEm: Date.now() })); return true; }
    catch (_) { return false; }
  }

  // A compra pendente manda mais que o convite: o convite espera, guardado, até o fim dela.
  function livre() {
    return !(window.PBPurchaseIntent && window.PBPurchaseIntent.pending());
  }

  function destino(fallback) {
    return ler() && livre() ? ENTRAR : fallback;
  }

  // O Google volta ao convite pelo cookie `next` do /auth/google/start.
  function mountAuth() {
    if (!ler() || !livre()) return;
    document.querySelectorAll('a[href="/auth/google/start"]').forEach(function (link) {
      link.href = "/auth/google/start?next=" + encodeURIComponent(ENTRAR);
    });
  }

  window.PBFamiliaConvite = { guardar: guardar, ler: ler, limpar: limpar, destino: destino };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountAuth, { once: true });
  else mountAuth();
})();
