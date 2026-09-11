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
 * sozinho.
 *
 * A exigência é sobre FILHOS DIRETOS, em dois níveis, e é TUDO OU NADA nos dois:
 *
 *   `#plans-v2`     só `article.plan` (e texto em branco);
 *   `article.plan`  exatamente a lista `FILHOS` abaixo — uma de cada, na ordem
 *                   em que o `Cartao` as reemite, nada além;
 *   `<ul>`          só `<li>`.
 *
 * Por que o nível do CARD também, e por que ele é tudo-ou-nada: a primeira
 * versão guardava só os filhos do `#plans-v2` e lia o resto do card por
 * `querySelector`, o que é uma leitura PARCIAL — qualquer nó que o seletor não
 * alcançasse era apagado no mount, calado. Medido nesta árvore, com o `<ul>` de
 * features dentro de um `<div class="features">`: as 5 features do plano
 * DESAPARECIAM com `montou: true`, zero erro e zero aviso. Card mutilado que se
 * diz montado é o pior dos três estados — pior que o markup do servidor intacto,
 * e pior que o defeito cosmético (sub-item içado) que a versão anterior corrigia.
 * Por isso `lerFilhos` reprova nó desconhecido, filho repetido (um 2º `<button>`
 * era engolido pelo `querySelector`) e ordem trocada (que o mount reordenaria em
 * silêncio), e o fallback é o `console.warn` do `main.jsx`.
 *
 * ── O QUE ISTO ATA AO RESTO DA PÁGINA ──────────────────────────────────────
 *
 * O `pixCriarCta` insere um SEGUNDO `<button>` dentro do card
 * (`pix-checkout.js:97`, `cartao.after(b)`). Com o CTA de Pix na tela, o card
 * está FORA deste contrato — e isso não é bug hoje porque o mount é provadamente
 * anterior: `precos-app.js` é IIFE, executa síncrono no parse, e os três
 * `pix-*.js` vêm DEPOIS dele na precos.html (`:1156-1159`), então `pbPixInit` nem
 * existe ainda. O `markUnavailable` e o `refreshPlanButtons`, que o fetch pode ter
 * rodado antes, só mudam `textContent`/`disabled` do botão que já existe.
 *
 * O que quebra se alguém mexer nisso: qualquer mudança que ATRASE o mount (trocar
 * a IIFE por `type="module"`, reordenar os `<script>`, remontar depois de um
 * fetch) faz a ilha parar de montar quando o CTA existe — fallback silencioso,
 * só o `console.warn`. Antes desta guarda o mesmo cenário era PIOR e mais calado:
 * a ilha montava e APAGAVA o CTA de Pix, que é botão de venda.
 *
 * Comentário (`nodeType 8`) é ignorado de propósito: é invisível, perdê-lo não
 * muda a página, e é o que o `precos_ilha_react.test.mjs` usa para saber se a
 * ilha montou ou caiu no fallback. Um deles mora dentro de um `<ul>` da
 * precos.html (o cálculo do limite de mensagens da IA), então ignorá-lo não é
 * teoria.
 */

/**
 * Os filhos diretos que o `Cartao` sabe reemitir, na ORDEM em que ele os emite,
 * com `true` para obrigatório. Mexer no JSX sem mexer aqui é o que esta lista
 * existe para tornar vermelho.
 */
const FILHOS = [
  ["span.plan-badge", false],
  ["h3", true],
  [".plan-sub", false],
  [".price-block", true],
  ["ul", true],
  ["button", true],
];

/** Todo filho de `el` casa com `sel`? (texto em branco e comentário não contam.) */
const soFilhos = (el, sel) => [...el.childNodes].every((n) => (n.nodeType === 1
  ? n.matches(sel)
  : n.nodeType !== 3 || n.textContent.trim() === ""));

/**
 * Os filhos de `art` indexados pelo seletor de `FILHOS`, ou `null` se saem do
 * contrato. O `i <= ultimo` fecha as três formas de sair dele numa comparação:
 * `-1` é nó desconhecido, `=== ultimo` é filho repetido, `< ultimo` é ordem
 * trocada.
 */
function lerFilhos(art) {
  const achados = new Map();
  let ultimo = -1;
  for (const n of art.childNodes) {
    if (n.nodeType === 3) {
      if (n.textContent.trim() !== "") return null;
      continue;
    }
    if (n.nodeType !== 1) continue;
    const i = FILHOS.findIndex(([sel]) => n.matches(sel));
    if (i <= ultimo) return null;
    achados.set(FILHOS[i][0], n);
    ultimo = i;
  }
  return FILHOS.every(([sel, obrigatorio]) => !obrigatorio || achados.has(sel))
    ? achados
    : null;
}

/** Um `<article class="plan">` virado em dados, ou `null` se não fecha o contrato. */
function lerCartao(art) {
  const filhos = lerFilhos(art);
  if (!filhos) return null;
  const lista = filhos.get("ul");
  if (!soFilhos(lista, "li")) return null;
  const badge = filhos.get("span.plan-badge");
  const btn = filhos.get("button");
  return {
    titulo: filhos.get("h3").textContent,
    // `null` e não `""`: o `Cartao` só emite a `.plan-sub` quando ela existia no
    // markup — um `<div class="plan-sub">` vazio a mais é uma caixa a mais.
    sub: filhos.get(".plan-sub")?.textContent ?? null,
    // innerHTML e não texto: o bloco de preço carrega os `[data-price-monthly]`
    // e `[data-price-annual]` com o `style="display:none"` que o `setCycle`
    // alterna, e os `<li>` carregam `<strong>` e `&nbsp;`. Reemitir o HTML é o
    // que preserva os dois sem os reescrever aqui.
    precoHtml: filhos.get(".price-block").innerHTML,
    // Os filhos do `<ul>`, não um `querySelectorAll`: lista aninhada dentro de um
    // `<li>` viaja no `innerHTML` do pai em vez de ser IÇADA para o topo e
    // duplicada, que é o que `ul > li` fazia.
    itens: [...lista.children].map((li) => li.innerHTML),
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
  if (!soFilhos(raiz, "article.plan")) return null;
  const planos = [...raiz.children].map(lerCartao);
  return planos.length && planos.every(Boolean) ? planos : null;
}
