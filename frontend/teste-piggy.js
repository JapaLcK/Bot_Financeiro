/* Botões "Testar o Piggy agora" (a[data-teste]): repassam utm_* e fbclid ao /teste e
   avisam o Pixel/GA no clique. Sem JS o href="/teste" funciona igual. Idempotente:
   o href parte sempre de "/teste" e o listener entra uma vez só por botão. */
try {
  const params = new URLSearchParams();
  new URLSearchParams(location.search).forEach(function (valor, nome) {
    if (/^utm_/i.test(nome) || nome === "fbclid") params.append(nome, valor);
  });
  const query = params.toString();
  document.querySelectorAll("a[data-teste]").forEach(function (a) {
    a.href = "/teste" + (query ? "?" + query : "");
    if (a.dataset.testePronto) return;
    a.dataset.testePronto = "1";
    a.addEventListener("click", function () {
      try { if (window.fbq) window.fbq("trackCustom", "TesteClick"); } catch { /* medir nunca barra a navegação */ }
      try { if (window.gtag) window.gtag("event", "teste_click", { transport_type: "beacon" }); } catch { /* idem */ }
    });
  });
} catch { /* sem o repasse o href="/teste" ainda funciona */ }
