// Nav ciente de login (páginas de marketing).
// Se o usuário já está autenticado, troca os CTAs "Entrar / Começar agora"
// por "Ir para o dashboard". NÃO redireciona — só ajusta o nav pra não
// parecer deslogado ao abrir uma página pública a partir do app.
(function () {
  // Paywall (/precos?ativar=1): conta criada, ainda SEM assinatura. Aqui o
  // usuário está logado mas não tem acesso ao app — "Ir para o dashboard" só
  // devolveria ele pro paywall. Então não mostramos esse CTA nessa página.
  var isPaywall =
    location.pathname.replace(/\/+$/, "") === "/precos" &&
    new URLSearchParams(location.search).get("ativar") === "1";

  fetch("/auth/validate", { credentials: "same-origin" })
    .then(function (r) {
      if (!r.ok) return; // deslogado: mantém os CTAs padrão
      // 1) Nav: troca "Entrar / Começar agora" por "Ir para o dashboard".
      //    No paywall, remove os CTAs do topo (nenhum faz sentido ali).
      var nr = document.querySelector(".nav .nav-right");
      if (nr) {
        nr.innerHTML = isPaywall
          ? ""
          : '<a class="btn btn-primary" href="/app">Ir para o dashboard</a>';
      }
      if (isPaywall) return; // no paywall, os CTAs do corpo são os planos — não mexe
      // 2) CTAs de cadastro no corpo (hero, seções finais) → dashboard.
      //    Roda DEPOIS do nav (o CTA do nav já foi removido acima).
      document.querySelectorAll('a[href="/cadastro"]').forEach(function (a) {
        a.setAttribute("href", "/app");
        a.textContent = "Ir para o dashboard";
      });
    })
    .catch(function () {
      /* offline/erro: mantém CTAs padrão */
    });
})();
