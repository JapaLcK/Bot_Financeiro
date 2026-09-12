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
 *   PI7 é o oráculo de CONTEÚDO que faltava (a árvore canônica do servidor ==
 *   a da ilha, nos dois ciclos) e PI8 a janela em que o `refreshPlanButtons`
 *   roda ANTES do mount — medida, não suposta.
 *
 * PO — O PÓDIO. Os três cards ficam na mesma linha acima de 900px, com o
 *   Plus no centro, mais alto e alinhado pela base aos planos laterais. Abaixo
 *   disso a escada empilha em uma coluna, sem criar uma fileira órfã de um card.
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
 * A ÁRVORE CANÔNICA do `#plans-v2` — nome da tag, atributos ORDENADOS por nome
 * e texto com espaço normalizado — mais o que o usuário LÊ em cada bloco de
 * preço. Roda dentro da página.
 *
 * Canônica porque só a ordem dos atributos e o espaço em branco entre os nós
 * separam legitimamente o markup do servidor (indentado, atributos na ordem em
 * que foram escritos) do que o React emite. Todo o resto — cada atributo, cada
 * `style="display:none"`, cada `&nbsp;` — tem de bater, e é isso que torna esta
 * comparação um oráculo de CONTEÚDO em vez de presença.
 *
 * `innerText` e não `textContent` no preço: ele respeita o `display` que o
 * `setCycle` alterna, então é o número que está na tela, não os dois colados.
 */
function arvoreEPrecos() {
  const norm = (s) => s.replace(/\s+/g, " ").trim();
  const no = (n) => {
    // Texto em branco fora (o servidor indenta, o React não) e comentário fora
    // (é o detector de mount, e o `lerPlanos` o ignora de propósito).
    if (n.nodeType === 3) return norm(n.textContent) || null;
    if (n.nodeType !== 1) return null;
    // O NumberFlow é uma melhoria progressiva exclusiva do bundle. O contrato
    // comparado aqui continua sendo o markup estático que também funciona sem JS.
    if (n.matches(".price-flow-host")) return null;
    return [n.nodeName,
            [...n.attributes].map((a) => {
              if (a.name === "class" && n.matches(".price.has-number-flow")) return "class=price";
              return `${a.name}=${norm(a.value)}`;
            }).sort(),
            [...n.childNodes].map(no).filter((x) => x !== null)];
  };
  return {
    arvore: no(document.getElementById("plans-v2")),
    precos: [...document.querySelectorAll("#plans-v2 article.plan .price-block")]
      .map((e) => norm(e.innerText)),
  };
}

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
  // Os dois ciclos, na ordem em que o usuário os vê: o mensal é o estado inicial
  // e o anual só existe depois do clique, que é quem move os `[data-price-*]`.
  const ciclos = {};
  for (const [ciclo, botao] of [["mensal", null], ["anual", "#cycle-annual"]]) {
    if (botao) await pagina.click(botao);
    ciclos[ciclo] = await pagina.evaluate(arvoreEPrecos);
  }
  await pagina.close();
  return { ...medido, ciclos, erros, avisos };
}

/**
 * PI1 — CONTROLE POSITIVO do grupo. Com o markup de verdade a ilha MONTA e não
 * larga erro nem aviso. Sem este caso, um `lerPlanos` que devolvesse `null`
 * sempre passaria em PI2 e PI3 — o grupo aprovaria a ilha morta.
 */
test("PI1: no markup de hoje a ilha monta, sem erro e sem aviso", async () => {
  const r = await abrir();
  assert.equal(r.montou, true, "a ilha NÃO montou no markup de produção");
  assert.equal(r.cards, 3, `cards: ${r.cards}`);
  assert.deepEqual(r.precos, [true, true, true], "card sem .price-block");
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
 * desligá-la vira um QUARTO `article.plan` e o `#extra` some. O texto solto ao
 * lado mede a mesma guarda pelo ramo dos nós de texto, que `children` não vê.
 */
test("PI2: um nó estranho dentro do #plans-v2 impede o mount, sem perder o nó", async () => {
  const decoy = '<div id="extra"><h3>Promo</h3><div class="price-block">R$ 1</div>'
    + "<ul><li>x</li></ul><button>Quero</button></div>nota do servidor";
  const r = await abrir({ mutar: (h) => h.replace(MARCA, MARCA + decoy) });
  assert.equal(r.extra, true, "o #extra DESAPARECEU no mount (ou virou article.plan)");
  assert.equal(r.montou, false, "montou por cima de markup que não sabe reproduzir");
  assert.equal(r.cards, 3, `os cards continuam 3 — o intruso não vira card (${r.cards})`);
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
  assert.equal(r.cards, 4, `os 4 articles tinham de sobreviver (${r.cards})`);
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

/**
 * PI7 — SERVIDOR × ILHA, o oráculo que faltava.
 *
 * Os PI2–PI6 medem markup MUTADO; o PI1 mede que a ilha monta. Nenhum comparava
 * o que a ilha reemite com o que o servidor mandou no markup de HOJE, e o
 * comparador de preço (`tests/test_pix_preco_bate_com_a_precos.py`) lê o TEXTO
 * CRU do `precos.html` — ou seja, prova metade: que o markup do servidor tem o
 * preço certo. A outra metade é esta.
 *
 * *Negativo medido: `precoHtml: filhos.get(".price-block").innerHTML` →
 * `.textContent` (`webapp/src/precos/lerPlanos.js`) + rebuild. A página passa a
 * mostrar os dois preços colados (perde os `display:none` que o `setCycle`
 * alterna) e o PI1, os 3 do gate do bundle e o comparador de preço seguem
 * VERDES — só este caso fica vermelho.*
 *
 * Por que os dois ciclos: o preço anual mora no MESMO `.price-block`, escondido
 * por `style="display:none"`. Comparar só o mensal deixa passar a perda do
 * atributo (o mensal continuaria certo e o anual nunca apareceria).
 *
 * O que ele NÃO pega: defeito nos DOIS lados ao mesmo tempo — se o `setCycle`
 * parar de alternar, servidor e ilha erram igual e a árvore bate. Por isso o
 * bloco final mede o marcador do ciclo (`/mês` × `/ano`), que é estrutura, não
 * uma quarta cópia do preço (§0.7).
 */
test("PI7: a ilha reproduz o #plans-v2 do servidor, atributo por atributo, nos dois ciclos", async () => {
  const servidor = await abrir({ semIlha: true });
  const ilha = await abrir();
  // As duas âncoras, sem as quais a comparação é servidor × servidor:
  assert.equal(servidor.montou, false, "o lado de controle montou a ilha");
  assert.equal(ilha.montou, true, "a ilha não montou — nada foi comparado");

  for (const ciclo of ["mensal", "anual"]) {
    assert.deepEqual(ilha.ciclos[ciclo].arvore, servidor.ciclos[ciclo].arvore,
      `a árvore do #plans-v2 divergiu do markup do servidor no ciclo ${ciclo}`);
    assert.deepEqual(ilha.ciclos[ciclo].precos, servidor.ciclos[ciclo].precos,
      `os preços VISÍVEIS divergiram no ciclo ${ciclo}`);
  }
  // Os três cards têm preço, e o marcador do ciclo tem de ser o do ciclo
  // selecionado, um só por card. O
  // `/mês` com barra de propósito: o "Equivale a R$ 8,25 por mês" do anual
  // contém "mês" e não é preço de ciclo.
  for (const [ciclo, tem, naoTem] of [["mensal", /\/mês/, /\/ano/], ["anual", /\/ano/, /\/mês/]]) {
    const p = ilha.ciclos[ciclo].precos;
    assert.equal(p.length, 3, `cards com .price-block no ${ciclo}: ${p.length}`);
    assert.equal(p.filter((t) => tem.test(t)).length, 3,
      `no ciclo ${ciclo} os preços na tela são ${JSON.stringify(p)}`);
    assert.equal(p.filter((t) => naoTem.test(t)).length, 0,
      `o ciclo ${ciclo} está mostrando o preço do outro: ${JSON.stringify(p)}`);
  }
});

/**
 * PI8 — A JANELA DO `refreshPlanButtons`, e por que a linha 36 do `main.jsx` é
 * load-bearing.
 *
 * `refreshPlanButtons` tem quatro ramos, e dois NÃO são idempotentes por
 * reemissão: os que deixam `disabled = false` e põem o handler em PROPRIEDADE
 * (`btn.onclick = function () { openChangeModal(p); }`, `precos.html:875`, e o
 * `cancelChange` do `:864`). Handler de propriedade não vira atributo, e o
 * `lerCartao` lê `btn.getAttribute("onclick")` — que continua valendo
 * `startCheckout(...)`. Se o `refreshPlanButtons` rodar ANTES do mount, a ilha
 * reemite um botão escrito "Trocar pro Pro", HABILITADO, cujo clique abre um
 * checkout novo para quem já assina. O `globalThis.refreshPlanButtons?.()` do
 * `main.jsx` é o que reemite o handler em cima do botão novo.
 *
 * A JANELA É ALCANÇÁVEL SÓ POR REDE, e isto foi MEDIDO (não é hipótese): o
 * `refreshPlanButtons` não é chamado por um `<script>` — ele é chamado de dentro
 * do `await` do `loadPlansState`, que é inline e roda continuação enquanto o
 * parser está BLOQUEADO baixando o `/precos-app.js`. Com o bundle atrasado
 * 1500 ms, o observador abaixo viu o texto do botão do SERVIDOR virar "Trocar
 * pro Pro" em ~60 ms e o mount só chegar em ~1555 ms. A ordem dos `<script>` no
 * documento não protege nada aqui (ela protege o `pbPixInit`, que é outra coisa).
 *
 * Controles do §3, os dois nas linhas da tabela:
 *   · negativo — apague `globalThis.refreshPlanButtons?.()` do `main.jsx` e
 *     rebuilde: a linha de 1500 ms fica vermelha (o clique vira POST de
 *     `/billing/create-checkout`);
 *   · positivo — a 2ª linha é o caminho legítimo na ordem NORMAL (mount primeiro,
 *     `/billing/subscription` lento): o botão de troca abre o MODAL. Sem ela, um
 *     `refreshPlanButtons` que desabilitasse tudo passaria.
 */
const SUB_STRIPE = { active: true, gateway: "stripe", plan: "plus", interval: "monthly" };

test("PI8: com o bundle lento, o botão de TROCA não vira checkout novo", async () => {
  // Os dois lados da janela, os DOIS forçados por atraso explícito — nunca pela
  // ordem natural: sem o `atrasoSub` a linha de baixo depende de o bundle local
  // chegar antes de duas respostas mockadas, e a margem medida era de 22 ms.
  // Teste que depende de 22 ms é flake esperando a máquina carregada.
  for (const { atrasoBundle, atrasoSub, preMount } of [
    { atrasoBundle: 1500, atrasoSub: 0, preMount: "Trocar pro Pro" },
    { atrasoBundle: 0, atrasoSub: 800, preMount: "Assinar Pro" },
  ]) {
    const pagina = await browser.newPage();
    const erros = [];
    pagina.on("pageerror", (e) => erros.push(String(e)));
    // Guarda o nó do botão que o SERVIDOR mandou, no instante em que o parser o
    // insere. É o que separa "a ilha montou" de "a ilha nem rodou" depois, e o
    // que prova QUANDO o refreshPlanButtons mexeu: `preMount` é o texto daquele
    // nó, não do que está na tela.
    await pagina.addInitScript(() => {
      new MutationObserver((_, obs) => {
        const b = document.querySelector('#plans-v2 [data-plan-btn="pro"]');
        if (!b) return;
        window.__noDoServidor = b;
        obs.disconnect();
      }).observe(document, { subtree: true, childList: true });
    });
    let checkouts = 0;
    await pagina.route("**/billing/plans-config", (r) => r.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true }),
    }));
    await pagina.route("**/billing/subscription", async (r) => {
      if (atrasoSub) await new Promise((ok) => setTimeout(ok, atrasoSub));
      return r.fulfill({ contentType: "application/json", body: JSON.stringify(SUB_STRIPE) });
    });
    await pagina.route("**/billing/create-checkout", (r) => {
      checkouts += 1;
      return r.fulfill({ contentType: "application/json",
                         body: JSON.stringify({ checkout_url: `${ORIGIN}/precos.html?stripe=1` }) });
    });
    if (atrasoBundle) {
      await pagina.route("**/precos-app.js*", async (r) => {
        await new Promise((ok) => setTimeout(ok, atrasoBundle));
        return r.fallback();
      });
    }
    await pagina.goto(`${ORIGIN}/precos.html`, { waitUntil: "load" });
    const alvo = '#plans-v2 [data-plan-btn="pro"]';
    await pagina.waitForFunction((sel) =>
      document.querySelector(sel)?.textContent.startsWith("Trocar"), alvo);
    const estado = await pagina.evaluate((sel) => {
      const b = document.querySelector(sel);
      return { preMount: window.__noDoServidor.textContent,
               montou: b !== window.__noDoServidor,
               texto: b.textContent, disabled: b.disabled };
    }, alvo);

    const onde = `com bundle atrasado ${atrasoBundle}ms e /billing/subscription ${atrasoSub}ms`;
    assert.equal(estado.montou, true, `a ilha não montou ${onde} — nada é medido`);
    // A ÂNCORA da ordem: `preMount` é o texto do nó que o servidor mandou. Se ele
    // virou "Trocar pro Pro", o refreshPlanButtons rodou ANTES do mount; se
    // continuou "Assinar Pro", o mount veio primeiro e o nó nunca foi tocado.
    assert.equal(estado.preMount, preMount,
      `o refreshPlanButtons não rodou na ordem que este caso mede ${onde}`);
    assert.equal(estado.texto, "Trocar pro Pro", `o rótulo da troca não sobreviveu ${onde}`);
    assert.equal(estado.disabled, false, `o botão de troca ficou desabilitado ${onde}`);

    await pagina.click(alvo);
    await pagina.waitForTimeout(300);
    // O POST vem ANTES do modal na ordem das asserções de propósito: com o
    // defeito a página navega para o checkout, e aí um `waitForSelector` daria
    // timeout de seletor no lugar de nomear a causa.
    assert.equal(checkouts, 0,
      `o clique em "Trocar pro Pro" abriu um checkout NOVO para quem já assina ${onde}`);
    assert.equal(await pagina.evaluate(() =>
      document.getElementById("chg-overlay")?.style.display), "flex",
      `o clique em "Trocar pro Pro" não abriu o modal de troca ${onde}`);
    assert.deepEqual(erros, [], onde);
    await pagina.close();
  }
});

/**
 * PI9 — O NÓ CAPTURADO ATRAVÉS DE UM `await`, na janela do PI8 e no botão que
 * VENDE para visitante deslogado — o funil inteiro.
 *
 * O `startCheckout` põe `loading` + "Carregando…" no botão, espera o
 * `/billing/create-checkout` e restaura depois. Se a ilha montar no meio do voo,
 * o nó que ele capturou SAI do documento: o estado sobrevive (o `lerCartao` lê
 * `className` e `textContent`, então o botão novo também nasce "Carregando…"),
 * mas a restauração escrevia no FANTASMA — e a guarda anti-duplo-clique
 * (`if (btn.classList.contains("loading")) return;`) matava todo clique seguinte.
 * Botão de compra morto até dar F5, com o toast dizendo "tente novamente".
 *
 * O `refreshPlanButtons` do `main.jsx` NÃO resgata este caso: sem assinatura ele
 * sai na 2ª linha (`if (!subState …) return`), que é exatamente quem clica
 * "Assinar".
 *
 * ── A CLASSE, enumerada (quem escreve no `#plans-v2` antes do mount) ─────────
 *
 *   `markUnavailable`      `disabled`/`textContent`/`dataset.unavailable` — as três
 *                          são LIDAS pelo `lerCartao`, reemitidas iguais.
 *   `refreshPlanButtons`   idem, mais o handler em PROPRIEDADE, que o `lerCartao`
 *                          não vê — resgatado pelo `main.jsx` (é o PI8).
 *   `setCycle`             `style.display` nos `[data-price-*]`, que viaja dentro
 *                          do `innerHTML` do `.price-block`.
 *   `pixCriarCta`          2º `<button>` no card → o card sai do contrato e NÃO se
 *                          monta (é o PI6); e ele carrega depois do bundle.
 *   `startCheckout`        guarda a REFERÊNCIA do nó através do fetch → este caso.
 *   `cancelChange`         operação compartilhada entre card e tabela; a guarda
 *                          cancelChangePending sobrevive ao mount (PI11).
 *   `document.activeElement`  o FOCO, que não é escrita de ninguém desta lista:
 *                          é estado do NAVEGADOR e some quando o `createRoot`
 *                          limpa o container → é o PI10, com a varredura do
 *                          resto da categoria no cabeçalho dele.
 *
 * Os três casos medem a MESMA equivalência em vez de um número escrito: com a
 * ilha ou sem ela, o botão volta ao rótulo original e o 2º clique dispara um POST
 * novo. O `semIlha` é o lado de controle (o comportamento da `main`).
 *
 * Controles do §3:
 *   · negativo — troque `restaurar()` pelas duas linhas de antes
 *     (`btn.classList.remove("loading"); btn.textContent = originalText;`) nos
 *     três sites do `startCheckout`: o caso "mount DENTRO do fetch" fica
 *     vermelho (`POSTs 1 -> 1`, botão em "Carregando…"), e os outros dois seguem
 *     verdes — é a linha do meio que discrimina;
 *   · positivo — o caso "mount ANTES do clique" é o caminho normal (bundle já
 *     chegou): a compra continua repetível depois de um erro. Sem ele, um
 *     `restaurar` que nunca limpasse o `loading` passaria.
 */
test("PI9: clicar em Assinar com o bundle em voo não mata o botão de compra", async () => {
  for (const { nome, atrasoBundle = 0, semIlha = false, noServidor, montou } of [
    { nome: "mount ANTES do clique", noServidor: false, montou: true },
    { nome: "mount DENTRO do fetch", atrasoBundle: 1000, noServidor: true, montou: true },
    { nome: "sem ilha (markup do servidor)", semIlha: true, noServidor: true, montou: false },
  ]) {
    const pagina = await browser.newPage();
    const erros = [];
    pagina.on("pageerror", (e) => erros.push(String(e)));
    // O nó que o SERVIDOR mandou, guardado no instante em que o parser o insere:
    // é ele que separa "cliquei antes do mount" de "cliquei depois".
    await pagina.addInitScript(() => {
      new MutationObserver((_, obs) => {
        const b = document.querySelector('#plans-v2 [data-plan-btn="plus"]');
        if (!b) return;
        window.__noDoServidor = b;
        obs.disconnect();
      }).observe(document, { subtree: true, childList: true });
    });
    // Visitante DESLOGADO, que é quem clica "Assinar": 401 nos dois.
    for (const rota of ["**/auth/me", "**/billing/subscription"]) {
      await pagina.route(rota, (r) =>
        r.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
    }
    await pagina.route("**/billing/plans-config", (r) => r.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true }),
    }));
    let posts = 0;
    // A falha chega DEPOIS do mount de propósito: a janela que interessa é o
    // fetch EM VOO quando a ilha reemite o botão.
    await pagina.route("**/billing/create-checkout", async (r) => {
      posts += 1;
      await new Promise((ok) => setTimeout(ok, atrasoBundle + 1500));
      return r.abort();
    });
    if (semIlha) await pagina.route("**/precos-app.js*", (r) => r.abort());
    else if (atrasoBundle) {
      await pagina.route("**/precos-app.js*", async (r) => {
        await new Promise((ok) => setTimeout(ok, atrasoBundle));
        return r.fallback();
      });
    }
    // `commit` e não `load`: durante a janela o `load` ainda não aconteceu — é
    // justamente o bundle que o segura.
    await pagina.goto(`${ORIGIN}/precos.html`, { waitUntil: "commit" });
    const alvo = '#plans-v2 [data-plan-btn="plus"]';
    await pagina.waitForSelector(alvo);
    // `.click()` do elemento, não o mouse: durante a janela a nav grudenta ainda
    // intercepta ponteiro, e o que se mede aqui é o handler, não a hit area.
    const clicouNoNoDoServidor = await pagina.$eval(alvo, (b) => {
      const foi = b === window.__noDoServidor;
      b.click();
      return foi;
    });
    await pagina.waitForTimeout(atrasoBundle + 600);   // mount já aconteceu, fetch ainda em voo
    const voando = await pagina.evaluate((sel) => {
      const b = document.querySelector(sel);
      return { texto: b.textContent, loading: b.classList.contains("loading"),
               montou: b !== window.__noDoServidor };
    }, alvo);

    // As ÂNCORAS, sem as quais o caso não mede o que o nome dele diz:
    assert.equal(clicouNoNoDoServidor, noServidor,
      `${nome}: o clique caiu do outro lado do mount`);
    assert.equal(voando.montou, montou, `${nome}: a ilha montou ${voando.montou}`);
    assert.equal(voando.loading, true, `${nome}: o botão não está em voo (${voando.texto})`);

    await pagina.waitForTimeout(1200);                 // o fetch falha
    // O estado é lido ANTES do 2º clique: depois dele o botão volta LEGITIMAMENTE
    // para "Carregando…" (é o POST novo), e aí "Carregando…" não distingue mais
    // botão morto de botão funcionando.
    const depois = await pagina.evaluate((sel) => {
      const b = document.querySelector(sel);
      return { texto: b.textContent, loading: b.classList.contains("loading"), disabled: b.disabled };
    }, alvo);
    const postsAntes = posts;
    await pagina.$eval(alvo, (b) => b.click());        // o usuário tenta de novo
    await pagina.waitForTimeout(400);

    assert.equal(depois.texto, "Assinar Plus", `${nome}: o rótulo não voltou`);
    assert.equal(depois.loading, false, `${nome}: o botão ficou preso em "Carregando…"`);
    assert.equal(depois.disabled, false, `${nome}: o botão ficou desabilitado`);
    assert.equal(posts, postsAntes + 1,
      `${nome}: o 2º clique não fez POST nenhum — o botão de compra está morto`);
    assert.deepEqual(erros, [], nome);
    await pagina.close();
  }
});

/**
 * PI10 — O FOCO, o QUARTO membro da classe enumerada no PI9, e o único que não
 * mora no markup: `document.activeElement` é estado do NAVEGADOR. O `lerCartao`
 * lê `className`, `textContent`, `disabled` e `dataset` — nada disso é foco, e
 * `createRoot` LIMPA o container, então o nó focado sai do documento e o
 * navegador devolve o foco ao `<body>`.
 *
 * Alcançável pela MESMA janela do PI8/PI9, e medido nesta árvore antes do
 * conserto (bundle atrasado 1500 ms): `activeElement` BUTTON[plus] antes do
 * mount, BODY depois — o botão continua na tela, mas quem navega por teclado ou
 * leitor de tela é jogado para o começo do documento no meio da página que
 * vende. Os cards do servidor já são acionáveis nessa janela porque o handler é
 * ATRIBUTO inline (`onclick="startCheckout(…)"`), não um listener que o bundle
 * registra.
 *
 * ── O resto da classe "estado do navegador dentro do #plans-v2", MEDIDO ──────
 *
 *   focáveis        os 3 `<button>` dos cards e mais nada (nenhum `<a>`, nenhum
 *                   `tabindex`). Por isso `[data-plan-btn]` cobre a categoria
 *                   toda em vez de ser um atalho.
 *   `:focus-visible`  derivado do foco, não guardado: com o foco restaurado o
 *                   anel continua (medido `matches(":focus-visible") === true`
 *                   depois do mount). É por isso que este caso o assere.
 *   scroll          NENHUM contêiner rolável dentro do `#plans-v2` (medido:
 *                   `overflow` calculado é `visible` na raiz e em todos os
 *                   descendentes), então não há posição de rolagem a preservar.
 *                   O que HÁ é o risco inverso, e é defeito que o próprio
 *                   conserto criaria: `focus()` sem `preventScroll` rolou a
 *                   página 0 → 881px na medição. Daí a asserção de `scrollY`.
 *   seleção de texto  PERDIDA no mount (medido: "R$ 9,90/mês" selecionado antes,
 *                   `getSelection()` vazia depois). Teto ACEITO de propósito:
 *                   ninguém depende de seleção para comprar, restaurá-la exigiria
 *                   remapear `Range` por caminho de nó, e o dano é cosmético num
 *                   gesto que o usuário refaz. ponytail: se um dia der problema,
 *                   o conserto é guardar `startContainer`/`offset` por índice de
 *                   filho e refazer o Range depois do `flushSync`.
 *   `aria-*`        nenhum é escrito pelo usuário aqui; os do markup
 *                   (`aria-hidden` dos ícones) viajam dentro do `innerHTML` que
 *                   o `lerCartao` copia. O `aria-live` da página (`#pix-cycle-live`)
 *                   está FORA do `#plans-v2` e o mount não o toca.
 *   `:hover`/`:active`  re-derivados pelo navegador no próximo evento de ponteiro;
 *                   não há estado a restaurar.
 *   IME / valor de campo  não há `<input>`, `<select>` nem `contenteditable`
 *                   dentro do `#plans-v2`.
 *   animação CSS    o `.plan-badge` tem `pb-badge-pan` e ela REINICIA no mount.
 *                   Não é afetado no sentido que importa: é decorativa, infinita
 *                   e o reinício não é percebido num nó que acabou de entrar.
 *
 * Controles do §3:
 *   · negativo — apague o par captura/restauração do `main.jsx` e rebuilde: a
 *     1ª linha fica vermelha (`activeElement` BODY, `mesmoNo` inconclusivo), e as
 *     outras duas seguem VERDES — é a 1ª que discrimina;
 *   · positivo — a 2ª linha foca FORA da ilha (`#cycle-annual`) e exige que o
 *     foco continue no MESMO nó. Sem ela, um conserto que focasse o card sempre
 *     passaria — e roubar foco de quem está no toggle é pior que o bug. A 3ª
 *     ("ninguém focou") prende o outro lado: o mount não pode INVENTAR foco.
 */
test("PI10: o foco dentro do card sobrevive ao mount, e o de fora não é roubado", async () => {
  const PLUS = '#plans-v2 [data-plan-btn="plus"]';
  for (const { nome, focar, esperado, mesmoNo } of [
    { nome: "foco no botão do card", focar: PLUS, esperado: "plus", mesmoNo: false },
    { nome: "foco fora da ilha", focar: "#cycle-annual", esperado: "cycle-annual", mesmoNo: true },
    { nome: "ninguém focou nada", focar: null, esperado: "BODY", mesmoNo: true },
  ]) {
    const pagina = await browser.newPage();
    const erros = [];
    pagina.on("pageerror", (e) => erros.push(String(e)));
    // O nó que o SERVIDOR mandou: é o detector de mount deste caso (ele SAI do
    // documento quando a ilha reemite o card) e a âncora de "o foco foi posto
    // ANTES do mount".
    await pagina.addInitScript(() => {
      new MutationObserver((_, obs) => {
        const b = document.querySelector('#plans-v2 [data-plan-btn="plus"]');
        if (!b) return;
        window.__noDoServidor = b;
        obs.disconnect();
      }).observe(document, { subtree: true, childList: true });
    });
    await pagina.route("**/precos-app.js*", async (r) => {
      await new Promise((ok) => setTimeout(ok, 1500));
      return r.fallback();
    });
    // `commit`: durante a janela o `load` ainda não aconteceu — é o bundle que
    // o segura, e é exatamente aí que o usuário já pode tabular até o card.
    await pagina.goto(`${ORIGIN}/precos.html`, { waitUntil: "commit" });
    await pagina.waitForSelector(PLUS);
    const antes = await pagina.evaluate((sel) => {
      if (sel) document.querySelector(sel).focus();
      window.__foco = document.activeElement;
      window.scrollTo(0, 0);   // o zero é o referencial do `scrollY` de depois
      return { ae: document.activeElement === document.body ? "BODY"
                 : document.activeElement.dataset.planBtn || document.activeElement.id,
               noDoServidor: document.activeElement === window.__noDoServidor,
               scrollY: window.scrollY };
    }, focar);
    // O mount: o nó do servidor deixa de estar no documento.
    await pagina.waitForFunction(() => window.__noDoServidor?.isConnected === false);
    const depois = await pagina.evaluate(() => {
      const ae = document.activeElement;
      return { ae: ae === document.body ? "BODY" : ae.dataset.planBtn || ae.id,
               mesmoNo: ae === window.__foco,
               anel: ae !== document.body && ae.matches(":focus-visible"),
               scrollY: window.scrollY };
    });
    await pagina.close();

    // As ÂNCORAS, sem as quais o caso não mede o que o nome dele diz:
    assert.equal(antes.ae, esperado, `${nome}: o foco não foi para onde este caso mede`);
    assert.equal(antes.noDoServidor, focar === PLUS,
      `${nome}: o foco caiu do outro lado do mount`);
    assert.equal(antes.scrollY, 0, `${nome}: a página não estava no topo antes do mount`);

    assert.equal(depois.ae, esperado,
      `${nome}: depois do mount o foco está em "${depois.ae}" e devia estar em "${esperado}"`);
    // Para o botão do card, `mesmoNo: false` é o que separa "o conserto
    // restaurou" de "o mount nunca tocou no nó" — sem isto o caso ficaria verde
    // numa ilha que não montou. Para o de fora, `true` é a exigência: o mesmo nó.
    assert.equal(depois.mesmoNo, mesmoNo, `${nome}: mesmoNo=${depois.mesmoNo}`);
    if (focar) assert.equal(depois.anel, true, `${nome}: o anel de foco sumiu`);
    assert.equal(depois.scrollY, 0,
      `${nome}: o mount rolou a página para ${depois.scrollY}px`);
    assert.deepEqual(erros, [], nome);
  }
});

// ── PO: o pódio ─────────────────────────────────────────────────────────────
//
// O pódio só existe quando o trio cabe na mesma linha. 900/901 cerca o breakpoint
// que separa o empilhamento mobile das três colunas.
const LARGURAS = [320, 390, 600, 768, 820, 900, 901, 1024, 1440];

test("PO1: pódio nas três colunas e cards contidos no empilhamento", async () => {
  const pagina = await browser.newPage();
  const fora = [];
  for (const largura of LARGURAS) {
    await pagina.setViewportSize({ width: largura, height: 900 });
    await pagina.goto(ORIGIN + "/precos.html", { waitUntil: "load" });
    const r = await pagina.evaluate(() => {
      const g = document.getElementById("plans-v2");
      const cs = getComputedStyle(g);
      return { colunas: cs.gridTemplateColumns.split(" ").length, align: cs.alignItems,
        larguraCard: Math.round(g.querySelector("article.plan").getBoundingClientRect().width) };
    });
    const esperado = r.colunas === 3 ? "end" : "normal";
    if (r.align !== esperado) fora.push(`${largura}px: ${r.colunas} coluna(s) com align-items: ${r.align}`);
    if (r.colunas === 1 && r.larguraCard > 380) {
      fora.push(`${largura}px: card empilhado com ${r.larguraCard}px`);
    }
  }
  await pagina.close();
  assert.deepEqual(fora, [], `o pódio e a contagem de colunas discordam em:\n${fora.join("\n")}`);
});

/**
 * PO2 — CONTROLE POSITIVO: o pódio EXISTE, e é pódio — são três cards, o Plus
 * ocupa o centro, é o mais alto, o Pro ocupa o degrau intermediário e as bases
 * estão alinhadas.
 */
test("PO2: em 1024px os cards formam três degraus, com bases alinhadas", async () => {
  const pagina = await browser.newPage();
  await pagina.setViewportSize({ width: 1024, height: 900 });
  await pagina.goto(ORIGIN + "/precos.html", { waitUntil: "load" });
  const r = await pagina.evaluate(() => {
    const cards = [...document.querySelectorAll("#plans-v2 article.plan")];
    const cx = (c) => c.getBoundingClientRect();
    return {
      nomes: cards.map((c) => c.querySelector("h3").textContent.trim()),
      alturas: cards.map((c) => Math.round(cx(c).height)),
      paddingsTopo: cards.map((c) => getComputedStyle(c).paddingTop),
      espacosTituloPreco: cards.map((c) => Math.round(
        c.querySelector(".price").getBoundingClientRect().top
          - c.querySelector("h3").getBoundingClientRect().bottom,
      )),
      finaisTexto: cards.map((c) => Math.round(
        c.querySelector("ul > li:last-child").getBoundingClientRect().bottom,
      )),
      vaosAteCta: cards.map((c) => Math.round(
        c.querySelector("[data-plan-btn]").getBoundingClientRect().top
          - c.querySelector("ul > li:last-child").getBoundingClientRect().bottom,
      )),
      destaque: cards.findIndex((c) => c.classList.contains("featured")),
      bases: cards.map((c) => Math.round(cx(c).bottom)),
    };
  });
  await pagina.close();
  assert.deepEqual(r.nomes, ["Essencial", "Plus", "Pro"]);
  assert.equal(r.destaque, 1, "o Plus não está no centro do trio");
  // ESTRITAMENTE mais alto, e não `=== Math.max(...)`: sem as regras do pódio o
  // grid volta ao `stretch` e iguala os três cards, e aí o destaque EMPATA em
  // primeiro — a asserção por `max` ficava verde num layout sem pódio nenhum
  // (medido: apagando as duas regras do precos.css, este caso passava).
  const outros = r.alturas.filter((_, i) => i !== r.destaque);
  assert.ok(outros.every((h) => r.alturas[r.destaque] > h),
    `o destaque (${r.alturas[r.destaque]}px) não é mais alto que todos: ${r.alturas.join("/")}`);
  const degrauMinimo = 40;
  assert.ok(r.alturas[2] - r.alturas[0] >= degrauMinimo,
    `o degrau Essencial→Pro é menor que ${degrauMinimo}px: ${r.alturas.join("/")}`);
  assert.ok(r.alturas[1] - r.alturas[2] >= degrauMinimo,
    `o degrau Pro→Plus é menor que ${degrauMinimo}px: ${r.alturas.join("/")}`);
  assert.ok(r.espacosTituloPreco.every((espaco) => espaco <= 36),
    `há espaço demais entre título e preço: ${r.espacosTituloPreco.join("/")}px`);
  assert.ok(Math.max(...r.finaisTexto) - Math.min(...r.finaisTexto) <= 1,
    `os últimos benefícios não terminam na mesma linha: ${r.finaisTexto.join("/")}px`);
  assert.ok(r.vaosAteCta.every((vao) => vao >= 20 && vao <= 30),
    `o vão entre o texto final e o CTA não é o respiro previsto: ${r.vaosAteCta.join("/")}px`);
  assert.equal(new Set(r.paddingsTopo).size, 1,
    `o conteúdo não começa no mesmo recuo: ${r.paddingsTopo.join("/")}`);
  assert.equal(new Set(r.bases).size, 1, `as bases não estão alinhadas: ${r.bases.join("/")}`);
});

// PI11: o cancelamento atravessa o mount sem duplicar POST; após falha pode repetir.
// O POST confirmado limpa só o agendamento local; erros continuam permitindo retry.
// Negativo: restaurar a releitura pós-POST torna os casos de sucesso vermelhos.
for (const [atrasoBundle, largura] of [[0, 1280], [1200, 1280], [1200, 390]]) {
  for (const [falha, releitura] of [
    [false, "200"], [true, "200"], [false, "500"], [false, "rede"],
    [false, "JSON malformado"], [false, "degradada"],
  ]) {
    test(`PI11: desfazer troca em ${largura}px com bundle ${atrasoBundle}ms e resposta ${falha ? 500 : 200}${releitura === "200" ? "" : `, releitura ${releitura}`}`, async () => {
      const pagina = await browser.newPage({ viewport: { width: largura, height: 900 } });
      let posts = 0, consultas = 0, agendada = true, liberar;
      const resposta = new Promise((ok) => { liberar = ok; });
      await pagina.addInitScript(() => {
        new MutationObserver((_, obs) => {
          const b = document.querySelector('#plans-v2 [data-plan-btn="pro"]');
          if (b) { window.__noDoServidor = b; obs.disconnect(); }
        }).observe(document, { subtree: true, childList: true });
      });
      await pagina.route("**/billing/plans-config", (r) => r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true }),
      }));
      await pagina.route("**/billing/subscription", (r) => {
        consultas += 1;
        if (consultas > 1) {
          if (releitura === "500") return r.fulfill({ status: 500, body: "erro interno" });
          if (releitura === "rede") return r.abort();
          if (releitura === "JSON malformado") return r.fulfill({ body: "{" });
          if (releitura === "degradada") return r.fulfill({ contentType: "application/json",
            body: JSON.stringify({ active: false, degraded: true }) });
        }
        return r.fulfill({ contentType: "application/json", body: JSON.stringify({ ...SUB_STRIPE,
          scheduled_change: agendada ? { plan: "pro", effective_at: "2026-10-01" } : null }) });
      });
      await pagina.route("**/billing/cancel-change", async (r) => {
        posts += 1;
        await resposta;
        if (!falha) agendada = false;
        await r.fulfill({ status: falha ? 500 : 200, contentType: "application/json", body: "{}" });
      });
      if (atrasoBundle) await pagina.route("**/precos-app.js*", async (r) => {
        await new Promise((ok) => setTimeout(ok, atrasoBundle));
        await r.fallback();
      });
      try {
        await pagina.goto(`${ORIGIN}/precos.html`, { waitUntil: "commit" });
        if (!atrasoBundle) await pagina.waitForLoadState("load");
        const alvo = '#plans-v2 [data-plan-btn="pro"]';
        await pagina.waitForFunction((sel) => document.querySelector(sel)?.textContent.includes("desfazer"), alvo);
        const antesDoMount = await pagina.$eval(alvo, (b) => {
          const antes = b === window.__noDoServidor;
          b.click();
          return antes;
        });
        assert.equal(antesDoMount, !!atrasoBundle, "o clique precisa cair do lado esperado do mount");
        await pagina.waitForLoadState("load");
        assert.equal(await pagina.evaluate(() => window.__noDoServidor.isConnected), false);
        await pagina.$eval(alvo, (b) => b.click());
        // O botão equivalente na tabela também não pode duplicar a operação.
        await pagina.$eval('.cmp-table [data-plan-btn="pro"]', (b) => b.click());
        await pagina.waitForTimeout(100);
        assert.equal(posts, 1, "o cancelamento foi reenviado durante a mesma operação");
        liberar();
        await pagina.waitForFunction((sel) => !document.querySelector(sel).disabled, alvo);
        if (falha) {
          await pagina.$eval(alvo, (b) => b.click());
          await pagina.waitForTimeout(100);
          assert.equal(posts, 2, "uma falha não pode bloquear a tentativa seguinte");
        } else {
          // O POST confirmou que só o agendamento mudou; nenhuma consulta nova é necessária.
          for (const ciclo of ["annual", "monthly"]) {
            await pagina.locator("#cycle-annual").click();
            assert.equal(await pagina.locator("#cycle-annual").getAttribute("aria-checked"),
              ciclo === "annual" ? "true" : "false", `o switch não entrou no ciclo ${ciclo}`);
            for (const sel of [alvo, '.cmp-table [data-plan-btn="pro"]']) {
              assert.equal(await pagina.locator(sel).innerText(), "Trocar pro Pro");
              await pagina.$eval(sel, (b) => b.click());
              assert.equal(await pagina.locator("#chg-overlay").isVisible(), true,
                "o controle deve oferecer uma nova troca, não cancelar a anterior");
              await pagina.evaluate(() => closeChangeModal());
            }
          }
          assert.equal(await pagina.locator('#plans-v2 [data-plan-btn="plus"]').innerText(),
            "✓ Seu plano atual", "cancelar a troca não pode mudar o plano vigente");
          assert.equal(posts, 1, "uma troca já desfeita não pode ser cancelada novamente");
          assert.equal(consultas, 1, "o cancelamento voltou a depender de uma releitura");
        }
      } finally {
        liberar();
        await pagina.close();
      }
    });
  }
}
