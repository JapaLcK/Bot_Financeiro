/* Repassa utm_* e fbclid da URL de entrada aos links da campanha.
   · a[data-teste] ("Testar o Piggy agora"): href vira /teste + a query, e o clique avisa o Pixel/GA.
   · a[data-utm] (CTAs e cartões de plano da /): a query entra no fim do href de origem; sem
     utm/fbclid o href fica. Cartão "Indisponível" (sem href) fica de fora.
   Sem JS os hrefs do HTML funcionam igual. Idempotente: o /teste parte sempre de "/teste", cada
   a[data-utm] passa uma vez só (data-utm-base guarda o href original) e o listener entra uma vez. */
try {
  const params = new URLSearchParams();
  new URLSearchParams(location.search).forEach(function (valor, nome) {
    if (/^utm_/i.test(nome) || nome === "fbclid") params.append(nome, valor);
  });
  const query = params.toString();
  document.querySelectorAll("a[data-utm][href]").forEach(function (a) {
    if (a.dataset.utmBase) return;  // 2ª passada não duplica, nem desfaz o /precos que o oferta.js pôs
    const base = a.dataset.utmBase = a.getAttribute("href");
    if (query) a.setAttribute("href", base + (base.includes("?") ? "&" : "?") + query);
  });
  document.querySelectorAll("a[data-teste]").forEach(function (a) {
    a.href = "/teste" + (query ? "?" + query : "");
    if (a.dataset.testePronto) return;
    a.dataset.testePronto = "1";
    a.addEventListener("click", function () {
      try { if (window.fbq) window.fbq("trackCustom", "TesteClick"); } catch { /* medir nunca barra a navegação */ }
      try { if (window.gtag) window.gtag("event", "teste_click", { transport_type: "beacon" }); } catch { /* idem */ }
    });
  });
} catch { /* sem o repasse os hrefs do HTML ainda funcionam */ }
