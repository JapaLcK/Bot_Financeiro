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
 *
 * ── O CONTRATO DE DOM, enumerado ────────────────────────────────────────────
 *
 * `createRoot` LIMPA o container, e o que este arquivo não sabe ler o mount
 * APAGA. Então a leitura não é "o que der": ou o `#plans-v2` inteiro é
 * reproduzível, ou não se monta nada e fica o markup do servidor — que vende
 * sozinho. O que é exigido, e o que acontecia sem a exigência:
 *
 * | do `#plans-v2`  | exigência            | sem ela                            |
 * |-----------------|----------------------|------------------------------------|
 * | filhos          | só `article.plan` (e | o nó extra DESAPARECE no mount e   |
 * |                 | texto em branco)     | volta se o bundle falhar           |
 * | de cada card    | um `<button>`        | `TypeError` no topo da IIFE: nem   |
 * |                 |                      | mount, nem `refreshPlanButtons`    |
 * | de cada card    | um `<h3>`            | card sem nome, calado              |
 * | de cada card    | uma `.price-block`   | card SEM PREÇO, calado             |
 * | itens           | `:scope > ul > li`   | lista aninhada IÇADA para o topo e |
 * |                 |                      | DUPLICADA (o `innerHTML` do `<li>` |
 * |                 |                      | pai também a carrega)              |
 * | `.plan-sub`     | opcional             | —                                  |
 * | `.plan-badge`   | opcional             | —                                  |
 *
 * Comentário (`nodeType 8`) é ignorado de propósito: é invisível, perdê-lo não
 * muda a página, e é o que o `precos_ilha_contrato.test.mjs` usa para saber se a
 * ilha montou ou caiu no fallback.
 */

/** Um `<article class="plan">` virado em dados, ou `null` se não fecha o contrato. */
function lerCartao(art) {
  const btn = art.querySelector("button");
  const titulo = art.querySelector("h3");
  const preco = art.querySelector(".price-block");
  if (!btn || !titulo || !preco) return null;
  const badge = art.querySelector(".plan-badge");
  return {
    titulo: titulo.textContent,
    sub: art.querySelector(".plan-sub")?.textContent ?? "",
    // innerHTML e não texto: o bloco de preço carrega os `[data-price-monthly]`
    // e `[data-price-annual]` com o `style="display:none"` que o `setCycle`
    // alterna, e os `<li>` carregam `<strong>` e `&nbsp;`. Reemitir o HTML é o
    // que preserva os dois sem os reescrever aqui.
    precoHtml: preco.innerHTML,
    itens: [...art.querySelectorAll(":scope > ul > li")].map((li) => li.innerHTML),
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

/**
 * Os planos do `#plans-v2`, ou `null` quando o markup sai do contrato acima —
 * e aí quem chama NÃO monta.
 */
export function lerPlanos(raiz) {
  const perdeConteudo = [...raiz.childNodes].some((n) => (n.nodeType === 1
    ? !n.matches("article.plan")
    : n.nodeType === 3 && n.textContent.trim() !== ""));
  if (perdeConteudo) return null;
  const planos = [...raiz.children].map(lerCartao);
  return planos.length && planos.every(Boolean) ? planos : null;
}
