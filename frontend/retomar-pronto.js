/** /retomar/pronto: o comprador do link de retomada pede o e-mail para criar a senha. Nada é consultado pelo sid/gw. */
(function () {
  "use strict";
  const TEXTO = {
    401: "Este link de compra expirou. Entre com o e-mail da sua compra.",
    429: "Muitos pedidos seguidos. Espere um pouco e tente de novo.",
    0: "Sem conexão agora. Confira a internet e tente de novo.",
  };
  // Conversão: mesma regra e mesmo eventID da /home (home.html, handleUpgradeReturn) para deduplicar com a CAPI.
  // Sem valor: o do Purchase vem da CAPI. A URL é limpa em seguida, então o reload não dispara de novo.
  function converter() {
    const q = new URLSearchParams(location.search), sid = q.get("sid"), ev = q.get("ev");
    if (!sid) return;
    history.replaceState({}, "", location.pathname);
    if (ev === "trial") {
      if (window.fbq) window.fbq("track", "StartTrial", { currency: "BRL" }, { eventID: "trial_" + sid });
      if (window.gtag) window.gtag("event", "start_trial", { transaction_id: sid });
    } else if (window.fbq) {
      window.fbq("track", "Purchase", { currency: "BRL" }, { eventID: "purchase_" + sid });
    }   // GA4 purchase sai do servidor (ga4_mp.py), como na /home
  }
  document.addEventListener("DOMContentLoaded", function () {
    converter();
    const botao = document.getElementById("enviar"), aviso = document.getElementById("aviso");
    const mostra = function (texto, ok) {
      aviso.textContent = texto;
      aviso.className = "auth-error show" + (ok ? " ok" : "");
    };
    botao.addEventListener("click", async function () {
      botao.disabled = true;
      try {
        const r = await fetch("/billing/retomada/senha", {
          method: "POST", credentials: "same-origin", headers: window.pbCsrfHeaders({ "Content-Type": "application/json" }), body: "{}",
        });
        let d = {};
        try { d = await r.json(); } catch (_) { /* corpo não-JSON */ }
        if (r.ok) return mostra(d.message || "Pronto. Confira o seu e-mail.", true);
        mostra(TEXTO[r.status] || "Não deu para enviar agora. Tente de novo em instantes.");
      } catch (_) {
        mostra(TEXTO[0]);
      }
      botao.disabled = false;
    });
  });
})();
