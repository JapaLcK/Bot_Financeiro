// Nav ciente de login (páginas de marketing).
// Logado: o canto direito do nav vira o MENU DA CONTA (Dashboard / Assinatura /
// Sair) — antes era só um botão "Ir para o dashboard", o que deixava o usuário
// sem logout em toda a landing (bug: cadastrou, se arrependeu, ficou preso).
// Os CTAs do corpo (hero/seções) continuam apontando pro dashboard.
// NÃO redireciona — só ajusta o nav pra não parecer deslogado.
// Estilos inline de propósito: nada de classe nova no site.css (cache).
(function () {
  // Paywall (/precos?ativar=1): conta criada, ainda SEM assinatura. O menu da
  // conta APARECE (é exatamente onde o "quero sair" acontece); só os CTAs do
  // corpo ficam quietos — ali os botões são os planos.
  var isPaywall =
    location.pathname.replace(/\/+$/, "") === "/precos" &&
    new URLSearchParams(location.search).get("ativar") === "1";

  function getCsrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function doLogout() {
    fetch("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "x-csrf-token": getCsrf() },
    })
      .catch(function () {})
      .finally(function () {
        try { localStorage.setItem("finbot_logout_at", String(Date.now())); } catch (e) {}
        location.reload(); // CTAs voltam ao padrão deslogado
      });
  }

  function renderAccountMenu(nr) {
    var item =
      "display:block;padding:10px 16px;color:#fff;text-decoration:none;font-size:.9rem;white-space:nowrap";
    nr.innerHTML =
      '<div id="acct-menu" style="position:relative">' +
      '<button id="acct-btn" type="button" style="display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);color:#fff;padding:9px 16px;border-radius:999px;font-size:.9rem;font-weight:600;cursor:pointer">' +
      "🐷 Minha conta <span style=\"font-size:.7rem;opacity:.7\">▾</span></button>" +
      '<div id="acct-dd" style="display:none;position:absolute;right:0;top:calc(100% + 10px);background:rgba(23,23,26,.98);border:1px solid rgba(255,255,255,.12);border-radius:14px;min-width:210px;padding:6px 0;box-shadow:0 18px 44px rgba(0,0,0,.5);backdrop-filter:blur(12px);z-index:99">' +
      '<a href="/app" style="' + item + '">📊 Ir para o dashboard</a>' +
      '<a href="/conta" style="' + item + '">💳 Gerenciar assinatura</a>' +
      '<div style="height:1px;background:rgba(255,255,255,.08);margin:6px 0"></div>' +
      '<a href="#" id="acct-logout" style="' + item + '">🚪 Sair da conta</a>' +
      "</div></div>";

    var btn = document.getElementById("acct-btn");
    var dd = document.getElementById("acct-dd");
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      dd.style.display = dd.style.display === "none" ? "block" : "none";
    });
    document.addEventListener("click", function () {
      dd.style.display = "none";
    });
    document.getElementById("acct-logout").addEventListener("click", function (e) {
      e.preventDefault();
      doLogout();
    });
  }

  fetch("/auth/validate", { credentials: "same-origin" })
    .then(function (r) {
      if (!r.ok) return; // deslogado: mantém os CTAs padrão
      // 1) Nav: "Entrar / Começar agora" → menu da conta (com logout).
      var nr = document.querySelector(".nav .nav-right");
      if (nr) renderAccountMenu(nr);
      if (isPaywall) return; // no paywall, os CTAs do corpo são os planos — não mexe
      // 2) CTAs de cadastro no corpo (hero, seções finais) → dashboard.
      //    É o "acesse o dashboard" da landing pra quem já tem conta.
      document.querySelectorAll('a[href="/cadastro"]').forEach(function (a) {
        a.setAttribute("href", "/app");
        a.textContent = "Ir para o dashboard";
      });
    })
    .catch(function () {
      /* offline/erro: mantém CTAs padrão */
    });
})();
