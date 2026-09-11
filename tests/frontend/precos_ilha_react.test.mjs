/**
 * A ilha React do `#plans-v2` da /precos: o contrato de DOM e o pódio.
 *
 * Dois grupos, e os dois nasceram de defeito medido nesta árvore:
 *
 * PI — O CONTRATO. `createRoot` LIMPA o container e o `lerPlanos` só sabe
 *   reemitir `article.plan`. Antes do conserto, três formas de markup davam
 *   errado em silêncio: um `<p>` extra dentro do `#plans-v2` DESAPARECIA no
 *   mount e voltava no dia em que o bundle falhasse; um card sem `<button>`
 *   estourava `TypeError` no topo da IIFE (nem mount, nem
 *   `refreshPlanButtons`); e uma lista aninhada era IÇADA para o topo e
 *   DUPLICADA, porque o seletor era `ul > li` em vez de `:scope > ul > li`.
 *
 *   Mais duas (PI5/PI6) vieram do conserto da terceira: trocar `ul > li` por
 *   `:scope > ul > li` deixou de içar a lista aninhada e passou a APAGAR a lista
 *   embrulhada — 5 features viravam 0, com `montou: true` e nenhum aviso. Elas
 *   medem o nível que faltava, o dos filhos de cada CARD, e as duas formas de
 *   sair dele: nó que o card não conhece, e filho repetido.
 *
 * PO — O PÓDIO. A regra era `@media (min-width: 900px)` com um comentário
 *   afirmando que "abaixo de 900px o grid vira uma coluna". Era falso: o
 *   `style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))"` inline
 *   vence o `@media (max-width:900px)` do site.css, então de 508 a 899px havia
 *   2 ou 3 colunas SEM pódio. O critério é "mais de uma coluna", e este grupo
 *   exige a equivalência em cada largura da lista `LARGURAS`.
 *
 * Como se sabe se a ilha MONTOU, já que ela reemite o mesmo markup de propósito:
 * por um COMENTÁRIO HTML plantado dentro do `#plans-v2`. Comentário não é lido
 * pelo `lerPlanos` (é invisível, perdê-lo não muda a página), então ele
 * sobrevive ao fallback e morre no mount — é o detector, e está documentado no
 * cabeçalho do lerPlanos.js para não virar acidente.
 *
 * O que estes testes NÃO alcançam: como a página se comporta no WKWebView do
 * app (só no aparelho, pós-deploy) e o iOS 14 de verdade — aqui o Chromium
 * entende `:scope` e `@media (min-width:)` de qualquer jeito.
 *
 * Rodar:  npm run test:frontend
 *         (ou só este: node --test tests/frontend/precos_ilha_react.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const MARCA = "<!--pb-detector-de-mount-->";
const ABRE_PLANS = '<div class="plans" id="plans-v2"';

/**
 * Abre a /precos com o markup mutado por `mutar` (string → string) e devolve o
 * que sobrou no DOM. `semIlha` aborta o bundle: é o markup do servidor puro, o
 * lado de controle de toda comparação daqui.
 */
async function abrir({ mutar = (h) => h, semIlha = false } = {}) {
  const pagina = await browser.newPage();
  const erros = [];
  const avisos = [];
  pagina.on("pageerror", (e) => erros.push(String(e)));
  pagina.on("console", (m) => { if (m.type() === "warning") avisos.push(m.text()); });

  const cru = await (await fetch(ORIGIN + "/precos.html")).text();
  // A marca entra logo depois da abertura do `#plans-v2`, antes do 1º card.
  const i = cru.indexOf(">", cru.indexOf(ABRE_PLANS)) + 1;
  const html = mutar(cru.slice(0, i) + MARCA + cru.slice(i));

  await pagina.route("**/precos.html", (rota) =>
    rota.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html }));
  if (semIlha) await pagina.route("**/precos-app.js*", (rota) => rota.abort());

  await pagina.goto(ORIGIN + "/precos.html", { waitUntil: "load" });
  const medido = await pagina.evaluate(() => {
    const raiz = document.getElementById("plans-v2");
    const cards = [...raiz.querySelectorAll("article.plan")];
    const topo = (c) => [...c.querySelectorAll(":scope > ul > li")];
    return {
      // Comentário ainda no DOM = a ilha NÃO montou (ficou o markup do servidor).
      montou: ![...raiz.childNodes].some((n) => n.nodeType === 8),
      cards: cards.length,
      extra: !!document.getElementById("extra"),
      liTopoDo1: topo(cards[0]).length,
      liTotalDo1: cards[0].querySelectorAll("li").length,
      botoesDo1: cards[0].querySelectorAll("button").length,
      // O sub-item continua ANINHADO (dentro de um `<li>`) ou foi içado?
      subAninhado: [...cards[0].querySelectorAll("li")]
        .some((li) => li.textContent.includes("sub-item") && li.parentElement.closest("li") !== null),
      textoTopo: topo(cards[0]).map((li) => li.textContent.trim()),
      precos: cards.map((c) => !!c.querySelector(".price-block")),
    };
  });
  await pagina.close();
  return { ...medido, erros, avisos };
}

/**
 * PI1 — CONTROLE POSITIVO do grupo. Com o markup de verdade a ilha MONTA e não
 * larga erro nem aviso. Sem este caso, um `lerPlanos` que devolvesse `null`
 * sempre passaria em PI2 e PI3 — o grupo aprovaria a ilha morta.
 */
test("PI1: no markup de hoje a ilha monta, sem erro e sem aviso", async () => {
  const r = await abrir();
  assert.equal(r.montou, true, "a ilha NÃO montou no markup de produção");
  assert.equal(r.cards, 4, `cards: ${r.cards}`);
  assert.deepEqual(r.precos, [true, true, true, true], "card sem .price-block");
  assert.deepEqual(r.erros, []);
  assert.deepEqual(r.avisos, []);
});

/**
 * PI2 — Nó que o `lerPlanos` não sabe reemitir. Antes: ele sumia no mount e
 * reaparecia se o bundle falhasse — assimétrico, sem erro, e ninguém saberia.
 * Agora não se monta nada: fica o markup do servidor, INTEIRO, e com aviso.
 *
 * O nó intruso é um DECOY de propósito: um `<div>` com `h3`, `.price-block`,
 * `<ul>` e `<button>`, ou seja tudo o que o `lerCartao` exige. Um `<p>` simples
 * NÃO mediria a guarda de filhos — o `lerCartao` devolveria `null` nele e o
 * mount seria barrado pela outra guarda, então desligar esta ficaria verde
 * (medido: com `if (false && perdeConteudo)` o caso passava). Com o decoy,
 * desligá-la vira um QUINTO `article.plan` e o `#extra` some. O texto solto ao
 * lado mede a mesma guarda pelo ramo dos nós de texto, que `children` não vê.
 */
test("PI2: um nó estranho dentro do #plans-v2 impede o mount, sem perder o nó", async () => {
  const decoy = '<div id="extra"><h3>Promo</h3><div class="price-block">R$ 1</div>'
    + "<ul><li>x</li></ul><button>Quero</button></div>nota do servidor";
  const r = await abrir({ mutar: (h) => h.replace(MARCA, MARCA + decoy) });
  assert.equal(r.extra, true, "o #extra DESAPARECEU no mount (ou virou article.plan)");
  assert.equal(r.montou, false, "montou por cima de markup que não sabe reproduzir");
  assert.equal(r.cards, 4, `os cards continuam 4 — o intruso não vira card (${r.cards})`);
  assert.deepEqual(r.erros, []);
  assert.ok(r.avisos.some((a) => a.includes("#plans-v2 fora do contrato")),
    `o fallback foi silencioso: ${JSON.stringify(r.avisos)}`);
});

/**
 * PI3 — Card sem `<button>`. Antes: `btn.className` em `null` →
 * `TypeError: Cannot read properties of null` no TOPO da IIFE, então o mount
 * não acontecia E o `globalThis.refreshPlanButtons?.()` da linha seguinte
 * também não. Agora é fallback declarado, sem exceção.
 */
test("PI3: card sem <button> cai no fallback em vez de estourar TypeError", async () => {
  const r = await abrir({
    mutar: (h) => h.replace(MARCA, MARCA + '<article class="plan"><h3>Sem botão</h3>'
      + '<div class="price-block">R$ 1</div><ul><li>x</li></ul></article>'),
  });
  assert.deepEqual(r.erros, [], "a ilha estourou exceção no lugar de cair no fallback");
  assert.equal(r.montou, false);
  assert.equal(r.cards, 5, `os 5 articles tinham de sobreviver (${r.cards})`);
  assert.ok(r.avisos.some((a) => a.includes("#plans-v2 fora do contrato")), JSON.stringify(r.avisos));
});

/**
 * PI4 — Lista aninhada. Antes, com `querySelectorAll("ul > li")`: o sub-item era
 * lido como item de TOPO e ainda vinha de carona no `innerHTML` do `<li>` pai —
 * 5 `<li>` viravam 6 de topo e 7 no total, com o sub-item no meio das features.
 * O controle é o markup do servidor (`semIlha`), não um número escrito aqui.
 */
test("PI4: lista aninhada num card não é içada nem duplicada", async () => {
  const comSub = (h) => h.replace("<li><span class=\"tick\">", "<li><ul><li>sub-item</li></ul><span class=\"tick\">");
  const servidor = await abrir({ mutar: comSub, semIlha: true });
  const ilha = await abrir({ mutar: comSub });

  assert.equal(ilha.montou, true, "a lista aninhada não devia impedir o mount");
  assert.equal(ilha.liTopoDo1, servidor.liTopoDo1,
    `itens de TOPO: ${ilha.liTopoDo1} com a ilha, ${servidor.liTopoDo1} no servidor`);
  assert.equal(ilha.liTotalDo1, servidor.liTotalDo1,
    `itens no total: ${ilha.liTotalDo1} com a ilha, ${servidor.liTotalDo1} no servidor`);
  assert.equal(ilha.subAninhado, true, "o sub-item foi içado para o nível de topo");
  assert.deepEqual(ilha.textoTopo, servidor.textoTopo);
  assert.deepEqual(ilha.erros, []);
});

/**
 * PI5 — O `<ul>` de features embrulhado num `<div>`. É o defeito que o conserto
 * do PI4 criou: com `:scope > ul > li`, o `<ul>` de dentro do wrapper não é mais
 * alcançado, e o card era reemitido com a lista VAZIA — as 5 features do plano
 * sumiam da página que vende, com `montou: true`, zero erro e zero aviso.
 *
 * O controle é o markup do servidor, não um número escrito aqui.
 */
test("PI5: <ul> embrulhado num <div> cai no fallback em vez de perder as features", async () => {
  const embrulhar = (h) => {
    const i = h.indexOf("<ul>", h.indexOf(MARCA));
    const j = h.indexOf("</ul>", i) + "</ul>".length;
    return `${h.slice(0, i)}<div class="features">${h.slice(i, j)}</div>${h.slice(j)}`;
  };
  const servidor = await abrir({ mutar: embrulhar, semIlha: true });
  const ilha = await abrir({ mutar: embrulhar });

  assert.equal(ilha.liTotalDo1, servidor.liTotalDo1,
    `features do 1º card: ${ilha.liTotalDo1} com a ilha, ${servidor.liTotalDo1} no servidor`);
  assert.equal(ilha.montou, false, "montou um card cuja lista ele não sabe ler");
  assert.deepEqual(ilha.erros, []);
  assert.ok(ilha.avisos.some((a) => a.includes("#plans-v2 fora do contrato")),
    `o fallback foi silencioso: ${JSON.stringify(ilha.avisos)}`);
});

/**
 * PI6 — Filho REPETIDO. Mesma classe do PI5 pelo outro ramo: `querySelector`
 * devolve o primeiro e o segundo desaparecia no mount. Um 2º `<button>` é o caso
 * que dói (botão de checkout), e é o mesmo predicado que cobre `<h3>`,
 * `.price-block` e `<ul>` repetidos, e ordem trocada.
 */
test("PI6: um 2º <button> no card cai no fallback em vez de desaparecer", async () => {
  const doisBotoes = (h) => h.replace("</ul>\n            <button", "</ul>\n"
    + '            <button type="button" id="b2">Falar com vendas</button>\n            <button');
  const servidor = await abrir({ mutar: doisBotoes, semIlha: true });
  const ilha = await abrir({ mutar: doisBotoes });

  assert.equal(servidor.botoesDo1, 2, "a mutação não pegou: o 1º card não ficou com 2 botões");
  assert.equal(ilha.botoesDo1, servidor.botoesDo1,
    `botões do 1º card: ${ilha.botoesDo1} com a ilha, ${servidor.botoesDo1} no servidor`);
  assert.equal(ilha.montou, false, "montou engolindo um botão que não sabe reemitir");
  assert.deepEqual(ilha.erros, []);
  assert.ok(ilha.avisos.some((a) => a.includes("#plans-v2 fora do contrato")),
    `o fallback foi silencioso: ${JSON.stringify(ilha.avisos)}`);
});

// ── PO: o pódio ─────────────────────────────────────────────────────────────
//
// A equivalência, não o número: para CADA largura, mais de uma coluna tem de
// significar `align-items: end`, e uma coluna `normal`. Foi assim que o "abaixo
// de 900px vira uma coluna" deixou de poder ser escrito sem alguém medir.
// 507/508 são o par que cerca o degrau (2ª faixa = 220+20+220 = 460px de
// container, e o wrap é `100vw - 48px`).
const LARGURAS = [320, 390, 507, 508, 600, 760, 820, 899, 900, 1024, 1440];

test("PO1: pódio se e somente se o grid tem mais de uma coluna", async () => {
  const pagina = await browser.newPage();
  const fora = [];
  for (const largura of LARGURAS) {
    await pagina.setViewportSize({ width: largura, height: 900 });
    await pagina.goto(ORIGIN + "/precos.html", { waitUntil: "load" });
    const r = await pagina.evaluate(() => {
      const g = document.getElementById("plans-v2");
      const cs = getComputedStyle(g);
      return { colunas: cs.gridTemplateColumns.split(" ").length, align: cs.alignItems };
    });
    const esperado = r.colunas > 1 ? "end" : "normal";
    if (r.align !== esperado) fora.push(`${largura}px: ${r.colunas} coluna(s) com align-items: ${r.align}`);
  }
  await pagina.close();
  assert.deepEqual(fora, [], `o pódio e a contagem de colunas discordam em:\n${fora.join("\n")}`);
});

/**
 * PO2 — CONTROLE POSITIVO: o pódio EXISTE, e é pódio — o destaque é o card mais
 * alto e as bases estão alinhadas. O PO1 mede uma equivalência, então ele
 * ficaria verde num layout em que o grid nunca passa de uma coluna: a escada
 * inteira empilhada em qualquer tela passa em "uma coluna ⟹ normal". Este caso
 * é o que prende o outro lado.
 */
test("PO2: em 1024px o destaque é o card mais alto, com bases alinhadas", async () => {
  const pagina = await browser.newPage();
  await pagina.setViewportSize({ width: 1024, height: 900 });
  await pagina.goto(ORIGIN + "/precos.html", { waitUntil: "load" });
  const r = await pagina.evaluate(() => {
    const cards = [...document.querySelectorAll("#plans-v2 article.plan")];
    const cx = (c) => c.getBoundingClientRect();
    return {
      alturas: cards.map((c) => Math.round(cx(c).height)),
      destaque: cards.findIndex((c) => c.classList.contains("featured")),
      bases: cards.map((c) => Math.round(cx(c).bottom)),
    };
  });
  await pagina.close();
  // ESTRITAMENTE mais alto, e não `=== Math.max(...)`: sem as regras do pódio o
  // grid volta ao `stretch` e iguala os quatro cards, e aí o destaque EMPATA em
  // primeiro — a asserção por `max` ficava verde num layout sem pódio nenhum
  // (medido: apagando as duas regras do precos.css, este caso passava).
  const outros = r.alturas.filter((_, i) => i !== r.destaque);
  assert.ok(outros.every((h) => r.alturas[r.destaque] > h),
    `o destaque (${r.alturas[r.destaque]}px) não é mais alto que todos: ${r.alturas.join("/")}`);
  assert.equal(new Set(r.bases).size, 1, `as bases não estão alinhadas: ${r.bases.join("/")}`);
});
