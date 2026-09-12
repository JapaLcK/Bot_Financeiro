/**
 * Os dois menus da barra pública — o da conta no `frontend/nav-auth.js`, o
 * burger no `frontend/nav-burger.js` — não ficam abertos ao mesmo tempo, e
 * ativar uma âncora do menu não deixa o foco órfão.
 *
 * `nav_publica_320.test.mjs` é sobre LARGURA — a barra cabendo numa faixa. Nada
 * ali toca comportamento de abrir/fechar, e o dropdown de conta nem é aberto.
 *
 * OS DOIS DEFEITOS, medidos a 390×780 em `/` antes do conserto:
 *
 *   após burger              {navOpen:true, ddOpen:false, expandidos:1, sobrepoe:false}
 *   após conta               {navOpen:true, ddOpen:true,  expandidos:2, sobrepoe:true}
 *   ordem inversa            {navOpen:true, ddOpen:false}   <- já funcionava
 *   Enter na âncora          activeElement = BODY
 *   Escape na âncora         activeElement = BUTTON.pb-burger
 *
 * Retângulos do `sobrepoe:true`: `.nav-links` [13,81,377,324] e `.pb-acct-dd`
 * [39,81,327,394] — o dropdown (z-index 99, absolute) nasce POR CIMA dos links
 * expandidos, com dois `aria-expanded="true"` anunciados ao mesmo tempo.
 *
 * SÓ UMA DIREÇÃO ESTAVA QUEBRADA: o clique no burger borbulha e o listener de
 * documento do dropdown o fecha; o clique na conta chamava `stopPropagation` e o
 * listener do burger nunca rodava.
 *
 * QUEM CONSERTA O FOCO É O `tabindex`, NÃO O `focus()`. Sete contrafactuais,
 * cada um rodado À MÃO naquele commit, patchando o handler (que hoje mora no
 * `nav-burger.js`) e reabrindo a página — menu aberto, Enter em
 * `#funcionalidades`, 390×780, reduced-motion. Os casos deste arquivo NÃO
 * patcham arquivo nenhum: eles mockam `/auth/*` e dirigem a página real.
 *
 *   variante                          activeElement           Tab seguinte
 *   shipped (tabindex + focus)        SECTION#funcionalidades A[/funcionalidades] y=367 DENTRO
 *   nada (nem tabindex nem focus)     BODY                    A[/funcionalidades] y=367 DENTRO
 *   só tabindex (sem focus)           SECTION#funcionalidades A[/funcionalidades] y=367 DENTRO
 *   só focus (sem tabindex)           BODY                    A[/funcionalidades] y=367 DENTRO
 *   b.focus() (sem tabindex)          BODY                    A[/funcionalidades] y=367 DENTRO
 *   tabindex + b.focus()              SECTION#funcionalidades A[/funcionalidades] y=367 DENTRO
 *   tabindex + foca a âncora interna  SECTION#funcionalidades A[/funcionalidades] y=367 DENTRO
 *
 * Duas coisas que essa tabela derruba, e que este cabeçalho AFIRMAVA errado:
 *
 *  1. O `focus()` explícito é INERTE no Chromium — some da tabela sem mudar
 *     nada, e apagá-lo deixa os 6 casos verdes. O `tabindex` é suficiente E
 *     necessário: a navegação para o fragmento foca o alvo sozinha depois que
 *     ele vira focável, e sobrescreve qualquer `focus()` do handler (é por isso
 *     que `tabindex + b.focus()` também termina na SECTION). A chamada FICA
 *     assim mesmo, deliberadamente: em WebKit e Firefox o foco automático em
 *     alvo de fragmento é menos consistente, e ISSO NÃO FOI MEDIDO — aqui só
 *     roda Chromium. Removê-la exige medição noutro motor.
 *  2. O contrafactual "B" do plano — `b.focus()` devolvendo o foco ao burger —
 *     NÃO REPRODUZ. A navegação roda depois do handler e reseta o foco por
 *     cima dele, para BODY (sem tabindex) ou para a SECTION (com). A regressão
 *     que a asserção do Tab deveria pegar não é alcançável neste motor.
 *
 * CONSEQUÊNCIA, dita sem enfeite: a asserção do Tab seguinte (`seguinte.dentro`,
 * no caso 4) é CONSTANTE nas sete variantes — dá `A[/funcionalidades] y=367 DENTRO` até na
 * linha "nada". Ela não discrimina nada aqui, nem junto nem sozinha; nas três
 * mutações que atacam o foco (só focus, b.focus, sem tabindex) o caso 4 cai
 * sempre na PRIMEIRA asserção. Fica como defesa para motor onde o foco
 * automático não é garantido — o mesmo motivo do `focus()`, e igualmente NÃO
 * MEDIDO. Não a leia como cobertura.
 *
 * Escape é OUTRA COISA — é dismiss, e devolver ao botão de disclosure é o certo;
 * o código já faz, e o caso 5 tranca isso. Ativar link é navegação.
 *
 * NORMA NÃO CONFERIDA NESTA SESSÃO: a explicação mecânica acima (navegação para
 * fragmento movendo o ponto de partida da navegação sequencial) bate com o que
 * foi medido, mas o `w3.org`/`whatwg.org` não abre deste ambiente (proxy
 * responde 403), então NÃO foi lida na fonte. O que está travado por teste é a
 * tabela, que saiu do navegador.
 *
 * `reducedMotion:"reduce"` no caso 4 não é enfeite: `site.css:31` tem
 * `scroll-behavior:smooth` e a `:718` o desliga sob reduced-motion. Sem isso a
 * medição pega o scroll no meio do caminho.
 *
 * Os dois controles do CLAUDE.md §3, para o GRUPO:
 *  · negativo — mutações rodadas, cada uma injetada num caso que estava VERDE:
 *      repor `e.stopPropagation()` no toggle da conta       -> 1 (expandidos 2, sobrepoe)
 *      voltar a guarda do burger para `!nav.contains(...)`   -> 1 (navOpen segue true)
 *      remover o `setAttribute("tabindex")` do handler       -> 4 e 6 (foco no BODY)
 *      `b.focus()` no lugar do foco no destino               -> 4 e 6 (foco no BODY)
 *      remover a linha de foco inteira                       -> 4 e 6 (foco no BODY)
 *      remover SÓ o `alvo.focus()`, mantendo o tabindex      -> 6/6 VERDE, ver acima
 *      alargar a guarda de `.pb-acct-btn` para `.pb-acct` (o container)   -> 2
 *      trocar o `closest` da guarda por `classList.contains`              -> 1 e 2
 *    A do `setAttribute` é a que prova o conserto do foco; a do `focus()` está
 *    na lista de propósito, como registro de que aquela chamada não é medida
 *    por nada. As duas últimas trancam a guarda do dropdown pelos dois lados: a
 *    do `classList` porque `page.click(".pb-acct-btn")` acerta o filho
 *    `SPAN.pb-acct-lbl` (alvo-filho), e a do `.pb-acct` porque a fronteira
 *    botão × dropdown decide se escolher um item fecha o menu.
 *    E a mutação que mata o conserto pelo lado errado — tirar o `stopPropagation`
 *    SEM pôr a guarda no listener de documento do dropdown — cai no caso 2: o
 *    dropdown abre e fecha no mesmo clique.
 *  · positivo — os casos 2, 3 e 5 provam que o que já funcionava continua: um
 *    clique só na conta ainda abre o dropdown, a ordem inversa segue intacta, e
 *    o Escape continua devolvendo o foco ao burger.
 *
 * O ANEL DE FOCO, que nenhum caso mede. `tabindex="-1"` faz a `<section>` casar
 * `:is(…,[tabindex]):focus-visible` da `site.css:705` (preexistente), e o alvo
 * ganha `outline: rgb(255,45,142) solid 2px` num retângulo de 342×1177 num
 * viewport de 390×780 — começa em y=96 e sai pela borda de baixo. SÓ no caminho
 * de teclado: com toque ou mouse o `:focus-visible` não casa e não há anel
 * (medido nos dois). É o efeito esperado do padrão de skip link e o lado certo
 * do WCAG 2.4.7, então fica — mas fica sem asserção de propósito: travá-lo aqui
 * amarraria este arquivo a uma regra de outra folha, que não é o assunto dele.
 *
 * ARMADILHA DE MÉTODO, para quem for reconferir o anel: `getComputedStyle()`
 * devolve `rgb(255,255,255) none 0px` num anel PINTADO se for lido antes de a
 * estilização assentar — aconteceu aqui, e a leitura seguinte, 300ms depois, deu
 * `rgb(255,45,142) solid 2px`. Quem conferir por computed style conclui errado.
 * O número acima está confirmado por PIXEL: colunas rosa em x=20, 21, 368 e 369,
 * em 258-259 de 260 linhas amostradas no caminho de teclado, e ZERO coluna rosa
 * no caminho de mouse.
 *
 * TRÊS COISAS REGISTRADAS, sem conserto (nenhuma é defeito alcançável hoje):
 *  · o `tabindex="-1"` NUNCA é removido — depois de ativar as duas âncoras, as
 *    duas `<section>` ficam com ele pelo resto da sessão. Não muda a ordem de
 *    Tab (a index já tem 3 `<article tabindex="0">` preexistentes) nem pinta
 *    anel fora do foco, mas é mutação permanente de DOM.
 *  · o handler não checa se o link é da MESMA página: `href="/precos#planos"`
 *    daria `a.hash="#planos"` e focaria um `#planos` da página atual antes de
 *    navegar. Varridas as 16 páginas com `.nav-links`: os únicos `href` com `#`
 *    são os 2 da index, ambos same-page. A guarda não existe por §0.2.
 *  · nenhum caso roda LOGADO + âncora. Medido à mão e funciona (foco na SECTION,
 *    `ddOpen:false`, 0 expandidos na nav); é lacuna de cobertura, não defeito.
 *
 * DESKTOP: o handler dos links roda em QUALQUER largura, e a >900px não há menu
 * nenhum (o burger é `display:none`). A 1280×900, "Funcionalidades" na barra
 * normal passou a gravar `tabindex="-1"` e mover o foco para a SECTION — antes
 * ficava no BODY. É a mesma melhoria de teclado, e o `setOpen(false)` ali é
 * inócuo; ficou, e o caso 6 cobre. O anel também aparece lá.
 *
 * O que este arquivo NÃO alcança: âncora em `.nav-links` só existe na
 * `index.html` (medido nas 12 páginas que carregam os DOIS arquivos da nav —
 * `nav-auth.js` e `nav-burger.js`, sempre em par); aparelho
 * real; e o app carrega o site AO VIVO, então nada disto aparece antes do
 * deploy (CLAUDE.md §5). E a classe do defeito 2 segue aberta em três irmãos
 * PREEXISTENTES que este PR não tocou: clique fora com foco de teclado num link
 * do burger, o mesmo com um `.pb-acct-link`, e Escape com o dropdown de conta
 * aberto (que não faz nada, num elemento `role="menu"`).
 *
 * ACOPLAMENTO REGISTRADO: o seletor `.nav-links` do listener de documento do
 * burger não é escopado à `.nav`. Das 16 páginas com `.nav-links`, 4 não
 * carregam este JS (`cadastro`, `login`, `home`, `comandos-app`), então não há
 * casamento acidental hoje — mas o dia em que uma delas carregar, há.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** `/` a 390×780 (ou `w`×`h`). Com `logado`, monta o menu da conta. */
async function abrir({ logado = false, reduced = false, w = 390, h = 780 } = {}) {
  const ctx = await browser.newContext({
    viewport: { width: w, height: h },
    ...(reduced ? { reducedMotion: "reduce" } : {}),
  });
  if (logado) {
    await ctx.route("**/auth/validate", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' }));
    await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ email: "ana.beatriz@gmail.com", display_name: "Ana", plan: "free" }) }));
  }
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/`);
  // o burger é injetado pelo nav-burger.js; o menu da conta, pelo nav-auth.js só depois do fetch.
  await page.waitForSelector(logado ? ".pb-acct-btn" : ".pb-burger", { state: "attached" });
  return { ctx, page };
}

/** Estado dos dois menus + se os painéis se sobrepõem na tela. */
const estado = (page) => page.evaluate(() => {
  const nav = document.querySelector(".nav");
  const a = nav.querySelector(".nav-links").getBoundingClientRect();
  const dd = document.getElementById("pb-acct-dd");
  const d = dd && dd.getBoundingClientRect();
  return {
    navOpen: nav.classList.contains("pb-nav-open"),
    ddOpen: !!dd && dd.classList.contains("open"),
    // o que o leitor de tela anuncia: dois menus expandidos é o defeito.
    // Escopado à `.nav` porque a index tem `[aria-expanded]` fora dela: medido,
    // 10 no total e 2 na nav LOGADO (que é o estado dos casos 1, 2 e 3), 9 e 1
    // deslogado. Os 8 de fora são os `.faq-q`, hoje todos "false"; um FAQ aberto
    // por padrão quebraria os casos 1 e 2 com mensagem de menu que não é de menu.
    expandidos: nav.querySelectorAll('[aria-expanded="true"]').length,
    // menu fechado tem rect zerado, que não intersecta ninguém — o teste não
    // mede "estão fechados", mede "não estão um por cima do outro"
    sobrepoe: !!d && !(a.right <= d.left || d.right <= a.left ||
                       a.bottom <= d.top || d.bottom <= a.top),
    rects: { links: [a.left, a.top, a.right, a.bottom].map(Math.round),
             dd: d && [d.left, d.top, d.right, d.bottom].map(Math.round) },
  };
});

test("1) burger aberto + clique na conta: um menu só fica expandido", async () => {
  const { ctx, page } = await abrir({ logado: true });
  await page.click(".pb-burger");
  const antes = await estado(page);
  assert.equal(antes.navOpen, true, "o burger não abriu — o caso perde o sentido");
  assert.equal(antes.expandidos, 1, `${antes.expandidos} expandidos só com o burger`);

  await page.click(".pb-acct-btn");
  const m = await estado(page);
  // antes do conserto: {navOpen:true, ddOpen:true, expandidos:2, sobrepoe:true}
  assert.equal(m.ddOpen, true, "o clique na conta não abriu o dropdown");
  assert.equal(m.navOpen, false, "o burger continuou aberto por baixo do dropdown");
  assert.equal(m.expandidos, 1,
    `${m.expandidos} menus anunciados como expandidos ao mesmo tempo`);
  assert.equal(m.sobrepoe, false,
    `dropdown por cima dos links: ${JSON.stringify(m.rects)}`);
  await ctx.close();
});

test("2) um clique só na conta ainda abre o dropdown (controle positivo)", async () => {
  // O jeito errado de tirar o `stopPropagation` é tirá-lo sem pôr a guarda no
  // listener de documento: o dropdown abriria e fecharia no MESMO clique, e o
  // caso 1 continuaria verde (um menu expandido — nenhum).
  const { ctx, page } = await abrir({ logado: true });
  await page.click(".pb-acct-btn");
  const m = await estado(page);
  assert.equal(m.ddOpen, true, "o dropdown não ficou aberto depois de um clique");
  assert.equal(m.expandidos, 1, `${m.expandidos} expandidos com só a conta aberta`);

  // A guarda é do BOTÃO, e a fronteira botão × dropdown precisa de asserção
  // própria: alargá-la para `.pb-acct` (o container) passava 6/6 sem isto, e aí
  // "Conectar WhatsApp" no desktop — `window.open`, sem navegação — deixaria o
  // dropdown aberto para sempre. O `preventDefault` é só para medir o estado
  // sem trocar de página; o clique borbulha igual até o listener de documento.
  await page.$eval('.pb-acct-link[href="/home"]',
    (a) => a.addEventListener("click", (e) => e.preventDefault()));
  await page.click('.pb-acct-link[href="/home"]');
  const depois = await estado(page);
  assert.equal(depois.ddOpen, false, "clicar num item do menu não fechou o dropdown");
  assert.equal(depois.expandidos, 0, `${depois.expandidos} expandidos depois de escolher um item`);
  await ctx.close();
});

test("3) conta primeiro, burger depois: a direção que já funcionava (positivo)", async () => {
  const { ctx, page } = await abrir({ logado: true });
  await page.click(".pb-acct-btn");
  await page.click(".pb-burger");
  const m = await estado(page);
  assert.equal(m.navOpen, true, "o burger não abriu");
  assert.equal(m.ddOpen, false, "o dropdown ficou aberto");
  assert.equal(m.sobrepoe, false, `sobreposição: ${JSON.stringify(m.rects)}`);
  await ctx.close();
});

test("4) Enter na âncora: o foco vai para o destino, e o Tab segue de lá", async () => {
  const { ctx, page } = await abrir({ reduced: true });
  await page.click(".pb-burger");
  await page.focus('.nav-links a[href="#funcionalidades"]');
  await page.keyboard.press("Enter");

  const foco = await page.evaluate(() => {
    const s = document.getElementById("funcionalidades"), a = document.activeElement;
    return { tag: a.tagName, id: a.id, no_alvo: a === s || s.contains(a),
             scrollY: Math.round(scrollY),
             navOpen: document.querySelector(".nav").classList.contains("pb-nav-open") };
  });
  assert.equal(foco.navOpen, false, "o menu ficou aberto por cima do destino");
  // antes do conserto: BODY — o link sumiu junto com o menu e o foco caiu fora
  assert.equal(foco.no_alvo, true,
    `foco em ${foco.tag}#${foco.id} depois do Enter, não no destino`);
  assert.ok(foco.scrollY > 1000, `a página não foi para a âncora (scrollY ${foco.scrollY})`);

  // NÃO discrimina nada: esta asserção dá `A[/funcionalidades] y=367 DENTRO` nas
  // sete variantes medidas no cabeçalho, inclusive na que não faz nada. Fica só
  // como defesa para motor onde o foco automático em fragmento não é garantido —
  // não medido aqui. Quem a ler como cobertura vai se enganar; ver o cabeçalho.
  await page.keyboard.press("Tab");
  const seguinte = await page.evaluate(() => {
    const s = document.getElementById("funcionalidades"), a = document.activeElement;
    return { tag: a.tagName, href: a.getAttribute("href"), dentro: s.contains(a),
             y: Math.round(a.getBoundingClientRect().top) };
  });
  assert.equal(seguinte.dentro, true,
    `o Tab seguinte caiu em ${seguinte.tag}[${seguinte.href}] em y=${seguinte.y}`);
  await ctx.close();
});

test("5) Escape com o foco na âncora ainda devolve ao burger (positivo)", async () => {
  const { ctx, page } = await abrir({ reduced: true });
  await page.click(".pb-burger");
  await page.focus('.nav-links a[href="#funcionalidades"]');
  await page.keyboard.press("Escape");
  const m = await page.evaluate(() => ({
    ae: document.activeElement.className,
    navOpen: document.querySelector(".nav").classList.contains("pb-nav-open"),
    scrollY: Math.round(scrollY),
  }));
  assert.equal(m.navOpen, false, "o Escape não fechou o menu");
  assert.equal(m.ae, "pb-burger", `o Escape deixou o foco em .${m.ae}`);
  assert.equal(m.scrollY, 0, "o Escape navegou para a âncora");
  await ctx.close();
});

test("6) 1280: o handler roda onde não há menu, e o foco vai ao destino igual", async () => {
  // Mudança de comportamento que o conserto trouxe de brinde e nenhum dos
  // outros casos vê: os 5 rodam a 390px, e a >900px o burger é display:none.
  // Antes, "Funcionalidades" na barra normal deixava o foco no BODY.
  const { ctx, page } = await abrir({ reduced: true, w: 1280, h: 900 });
  assert.equal(await page.$eval(".pb-burger", (e) => e.getBoundingClientRect().height), 0,
    "o burger apareceu no desktop — o caso perde o sentido");
  await page.focus('.nav-links a[href="#funcionalidades"]');
  await page.keyboard.press("Enter");
  const m = await page.evaluate(() => {
    const s = document.getElementById("funcionalidades"), a = document.activeElement;
    return { no_alvo: a === s || s.contains(a), tag: a.tagName,
             tabindex: s.getAttribute("tabindex"), scrollY: Math.round(scrollY) };
  });
  assert.equal(m.tabindex, "-1", "o alvo não virou focável no desktop");
  assert.equal(m.no_alvo, true, `foco em ${m.tag} no desktop, não no destino`);
  assert.ok(m.scrollY > 1000, `a página não foi para a âncora (scrollY ${m.scrollY})`);
  await ctx.close();
});
