/**
 * Os planos saem do MARKUP que o servidor já mandou, nunca de literal daqui.
 *
 * Motivo (§0.7): o preço visível da /precos é atado ao preço que a cobrança Pix
 * emite por `tests/test_pix_preco_bate_com_a_precos.py`, que lê o TEXTO CRU de
 * `frontend/precos.html` com regex. Um preço literal neste arquivo seria uma
 * QUARTA cópia — dentro do sistema que existe para caçar cópia — e o comparador
 * ficaria verde com a página anunciando outro número.
 *
 * O efeito colateral vale por si: o componente não tem estado nenhum. Ele lê o
 * DOM como está no momento do mount e reemite o mesmo DOM, então o mount é
 * IDEMPOTENTE — o "Indisponível" do `markUnavailable` e o "Você tem acesso
 * vitalício" do `refreshPlanButtons`, se o fetch tiver voltado antes do parse
 * chegar até aqui, são LIDOS e reemitidos em vez de apagados.
 */

/** Um `<article class="plan">` virado em dados. */
function lerCartao(art) {
  const btn = art.querySelector("button");
  const badge = art.querySelector(".plan-badge");
  return {
    titulo: art.querySelector("h3")?.textContent ?? "",
    sub: art.querySelector(".plan-sub")?.textContent ?? "",
    // innerHTML e não texto: o bloco de preço carrega os `[data-price-monthly]`
    // e `[data-price-annual]` com o `style="display:none"` que o `setCycle`
    // alterna, e os `<li>` carregam `<strong>` e `&nbsp;`. Reemitir o HTML é o
    // que preserva os dois sem os reescrever aqui.
    precoHtml: art.querySelector(".price-block")?.innerHTML ?? "",
    itens: [...art.querySelectorAll("ul > li")].map((li) => li.innerHTML),
    featured: art.classList.contains("featured"),
    estilo: art.getAttribute("style"),
    badge: badge && { html: badge.innerHTML, estilo: badge.getAttribute("style") },
    botao: {
      classe: btn.className,
      texto: btn.textContent,
      plano: btn.dataset.planBtn ?? null,
      // O atributo, não um listener: o handler global é
      // `startCheckout(currentCycle, this, '<plano>')`, e o `this` ali é o
      // BOTÃO. Um `onClick` de React entregaria outro `this`, e o
      // `refreshPlanButtons` apaga o handler com `btn.onclick = null` — que só
      // funciona sobre o atributo.
      onclick: btn.getAttribute("onclick"),
      disabled: btn.disabled,
      indisponivel: btn.dataset.unavailable ?? null,
    },
  };
}

export function lerPlanos(raiz) {
  return [...raiz.querySelectorAll("article.plan")].map(lerCartao);
}
