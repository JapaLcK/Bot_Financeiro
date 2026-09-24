/**
 * O header do dashboard e as grades do produto a 375px e 320px.
 *
 * O QUE ESTAVA ERRADO (medido no dashboard REAL logado, pigbankai.com/app,
 * conta com dados, aba Cartões — não é estimativa):
 *   375×812  header 206px em 4 faixas: marca / ‹ mês › / [4 .hbtn] / Minha conta
 *   320×800  header 258px em 5 faixas (Exportar cai sozinho numa linha)
 *   320×800  #cards-grid: track de 340px numa caixa de [10,310]; o filho mede
 *            [10,350] → 40px CORTADOS, sem rolagem (html,body overflow-x:hidden)
 *   375×812  #cards-grid: track 355px em caixa [10,365] — NÃO corta
 * Por isso o caso das grades roda a **320**: a 375 ele passa antes e depois do
 * conserto, e caso que não discrimina não é teste, é decoração.
 *
 * O `#month-label` NÃO estava truncado ("Setembro 2026", scrollWidth <=
 * clientWidth): o "—" que aparecia numa inspeção anterior é o estado de
 * carregamento, não um bug de largura. NENHUM caso aqui mede isso — o rótulo
 * fica no "—" inicial no runner, e travessão não trunca com CSS nenhum. O
 * `white-space:nowrap` deste PR é preventivo e segue sem cobertura (ver a nota
 * dentro do caso 1).
 *
 * COMO SE CONTA UMA FAIXA: pelo CENTRO vertical, tolerância de 12px, como em
 * `nav_publica_320.test.mjs` — agrupar por `top` dá faixa fantasma sempre que
 * itens de alturas diferentes (logo 30px, botão 44px) dividem a mesma linha.
 *
 * O que este arquivo NÃO alcança: o modo app (`html.pb-app`, que só liga em
 * `/app` e tem CSS próprio em `app-mode.css:505-590`, todo `!important`);
 * aparelho real; `env(safe-area-inset-*)`, que vale 0 no headless.
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

const json = (body) => (r) => r.fulfill({
  status: 200, contentType: "application/json", body: JSON.stringify(body) });

/**
 * Abre `/dashboard.html` na largura `w` com o boot resolvido: validate e me
 * aprovam, e o perfil decide o gate (`gates: {}` = Free, tudo `.pro-locked`).
 * O WebSocket é um stub ABERTO — `_doRefresh` só marca `.spinning` com
 * `ws.readyState === WebSocket.OPEN`.
 */
async function abrirDash(w, { gates = {}, plano = "free" } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: 812 } });
  await ctx.route("**/auth/validate", json({ ok: true, user_id: 42 }));
  await ctx.route("**/auth/me", json({ app_access: true, plan_tier: "essencial" }));
  await ctx.route("**/auth/dashboard-profile", json({
    email: "ana.beatriz@gmail.com", display_name: "Ana Beatriz",
    plan: plano, feature_gates: gates }));
  // Chart.js vem de CDN: sem rede no runner isso é um timeout de 30s por caso.
  await ctx.route("**cdnjs.cloudflare.com/**", (r) => r.abort());
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    class WSStub {
      static OPEN = 1;
      constructor(url) {
        this.url = url; this.readyState = 1; window._ws = this;
        // o onopen é o que leva o boot a setStatus("connected") — sem ele não há
        // sinal observável de que o connect() rodou
        setTimeout(() => this.onopen && this.onopen({}), 0);
      }
      send() {}
      close() { this.readyState = 3; }
    }
    window.WebSocket = WSStub;
  });
  await page.goto(`${ORIGIN}/dashboard.html`);
  // Espera um sinal REAL de boot. Esperar pelo `[data-pro-feature]` não serve:
  // ele é nó estático do dashboard.html e resolve no primeiro tick, antes do
  // await do /auth/validate — e o `ws` (que o `_doRefresh` exige em OPEN) só
  // nasce depois dele. `#dot.connected` só aparece no onopen do socket.
  await page.waitForSelector("#dot.connected", { timeout: 15000 });
  await page.waitForFunction(
    () => document.querySelector('[data-pro-feature="ofx_import"]')
            .hasAttribute("aria-disabled"));   // applyProGates já correu
  await page.evaluate(() => document.fonts.ready);
  return { ctx, page };
}

/** Faixas do header por centro vertical + altura + itens fora da tela. */
const medirHeader = (page) => page.evaluate(() => {
  const h = document.querySelector("header");
  // os filhos de .header-right contam um a um: é lá que a quebra acontecia
  const itens = [...h.children].flatMap((c) =>
    (c.classList.contains("header-right") ? [...c.children] : [c]))
    .filter((x) => x.getBoundingClientRect().width > 0);
  const faixas = [];
  for (const x of itens) {
    const b = x.getBoundingClientRect(), meio = (b.top + b.bottom) / 2;
    if (!faixas.some((f) => Math.abs(f - meio) < 12)) faixas.push(meio);
  }
  const lbl = document.getElementById("month-label");
  return {
    faixas: faixas.length,
    altura: +h.getBoundingClientRect().height.toFixed(1),
    mes: lbl.textContent,
    hbtns: [...h.querySelectorAll(".hbtn")]
      .map((b) => b.getBoundingClientRect())
      .filter((b) => b.width > 0)
      .map((b) => ({ w: +b.width.toFixed(1), h: +b.height.toFixed(1),
                     left: +b.left.toFixed(1), right: +b.right.toFixed(1) })),
    fora: itens.filter((x) => x.getBoundingClientRect().right > innerWidth + 0.5).length,
    h1Visivel: document.querySelector("header h1").getBoundingClientRect().width > 0,
    // rótulos que ainda ocupam espaço (os 3 .hbtn-label + o .user-menu-main)
    rotulosVisiveis: [...h.querySelectorAll(".hbtn-label, .user-menu-main")]
      .filter((x) => x.getBoundingClientRect().width > 0).length,
    // nome acessível dos botões do header: o que o leitor de tela anuncia.
    // Os rótulos somem por CSS aqui, então sobra o `aria-label` escrito à mão —
    // e atributo à mão sem guarda é atributo que some no próximo diff.
    nomes: [...h.querySelectorAll(".hbtn")]
      .filter((b) => b.getBoundingClientRect().width > 0)
      .map((b) => ({
        id: b.id || b.dataset.proFeature || "?",
        aria: (b.getAttribute("aria-label") || "").trim(),
        // `title` é nome acessível de último recurso. Entra separado de
        // propósito: somado ao `aria`, ele deixaria o caso 1c passar com TODOS
        // os aria-label deste PR apagados.
        title: (b.getAttribute("title") || "").trim(),
      })),
  };
});
// `document.documentElement.scrollWidth` NÃO entra aqui: o
// `html,body{overflow-x:hidden}` do dashboard-mobile.css:8 (fora de media query,
// anterior a este PR) grampeia o scrollWidth na viewport, então o assert não
// teria como ficar vermelho. Quem discrimina overflow é o `right` de cada item.

// Faixas EXATAS, não teto: `<=` não pega regressão que ENCOLHE indevidamente, e
// o número difere entre as duas larguras por um motivo conhecido (abaixo).
const FAIXAS = { 375: 3, 320: 2 };

test("1) 375 e 320: header nas faixas medidas, ≤160px, alvos de 44px", async () => {
  for (const w of [375, 320]) {
    const { ctx, page } = await abrirDash(w);
    const m = await medirHeader(page);
    // Medido — antes: 375 -> 206px/4 faixas, 320 -> 258px/5 faixas
    //          depois: 375 -> 154px/3 faixas, 320 -> 112px/2 faixas
    // Por que 3 a 375 e 2 a 320: a 320 o `header h1` some (@media ≤360) e o
    // `.month-nav` sobe para a linha da marca (os dois com centro em 40); a 375
    // a marca fica e o mês ocupa faixa própria. Ou seja, o conserto tira UMA
    // faixa a 375 (a linha do "Minha conta") e TRÊS a 320.
    // O teto de altura é 160, não 130: 154 é o número real a 375.
    assert.equal(m.faixas, FAIXAS[w], `${w}: header em ${m.faixas} faixas (${m.altura}px)`);
    assert.ok(m.altura <= 160, `${w}: header com ${m.altura}px (máximo 160)`);
    assert.equal(m.fora, 0, `${w}: ${m.fora} item(ns) do header além da viewport`);
    assert.ok(m.hbtns.length >= 5, `${w}: só ${m.hbtns.length} .hbtn visíveis (5 esperados)`);
    // Largura EXATA de 44, não `>= 44`: o pedido é botão SÓ ÍCONE, quadrado de
    // alvo de polegar (medido em produção: [100,144], [152,196], …). Com
    // `>= 44` a reversão de `flex:0 0 44px;width:44px` para o `flex:1 1 0`
    // antigo passava verde — os botões esticam para ~55px e continuam acima do
    // mínimo. Medido: esse assert frouxo não discriminava nada.
    for (const b of m.hbtns)
      assert.ok(b.w === 44 && b.h >= 44, `${w}: .hbtn ${b.w}×${b.h} (esperado 44 de largura)`);
    // E o rótulo tem de estar REALMENTE escondido: sem isto, remover o
    // `.hbtn-label{display:none}` passava verde (o texto transborda invisível
    // dentro do botão de 44px, sem mexer em altura nem em faixas).
    assert.equal(m.rotulosVisiveis, 0,
      `${w}: ${m.rotulosVisiveis} rótulo(s) de texto ainda ocupando espaço no header`);
    // SEM assert de truncamento do #month-label: aqui ele fica no valor inicial
    // "—" (dashboard.html:125), porque nenhum endpoint mockado traz o mês. Um
    // travessão nunca tem scrollWidth > clientWidth, então o assert não podia
    // ficar vermelho com CSS nenhum. O `white-space:nowrap` que este PR pôs
    // segue SEM cobertura — em produção o rótulo já não truncava ("Setembro
    // 2026", medido), a regra é preventiva contra a linha só-ícone apertar.
    // A marca escrita só sai ≤360px; a 375 ela fica. Medido a 320 com o
    // conserto: h1 oculto, header 112px em 2 faixas.
    assert.equal(m.h1Visivel, w > 360,
      `${w}: header h1 ${m.h1Visivel ? "visível" : "oculto"} — esperado o contrário`);
    await ctx.close();
  }
});

test("1c) 375: aria-label nos 4 botões cujo rótulo este PR escondeu", async () => {
  // O CSS apaga os rótulos; o nome passa a vir do `aria-label`, escrito à mão.
  // Sem este caso, apagar um aria-label deixa a suíte verde e o botão mudo —
  // foi o que aconteceu com o #user-menu-btn, o único dos cinco que ficou sem
  // o atributo na primeira volta.
  //
  // O `title` NÃO conta para estes quatro, embora seja nome acessível válido:
  // o que este PR criou foram os aria-label, e um caso que aceitasse `title`
  // passaria com os quatro apagados. O #hide-balance-btn é a exceção nomeada —
  // ele já era só-ícone ANTES deste PR e sempre se apoiou no `title`.
  const COM_ARIA = ["refresh-btn", "ofx_import", "export", "user-menu-btn"];
  const { ctx, page } = await abrirDash(375);
  const { nomes } = await medirHeader(page);
  assert.equal(nomes.length, 5, `${nomes.length} botões visíveis no header (5 esperados)`);
  for (const id of COM_ARIA) {
    const b = nomes.find((x) => x.id === id);
    assert.ok(b, `botão "${id}" sumiu do header`);
    assert.ok(b.aria.length > 0,
      `botão "${id}" sem aria-label — rótulo escondido por CSS e nome vazio`);
  }
  const olho = nomes.find((x) => x.id === "hide-balance-btn");
  assert.ok(olho && (olho.aria || olho.title).length > 0,
    "#hide-balance-btn sem nome acessível (aria-label ou title)");
  await ctx.close();
});

test("1b) 375 e 320: nenhum botão do header transborda para a ESQUERDA", async () => {
  // O caso 1 sozinho NÃO pega este bug: com o `.user-menu` herdando width:100%
  // de dashboard.css:1151 e o flex-wrap:nowrap, o header fica BAIXO (uma faixa)
  // e os 4 botões + o dot saem da tela pela esquerda, escondidos pelo
  // `overflow-x:hidden`. Medido nessa condição a 375px: #status [-201,-193],
  // hide-balance [-185,-141], refresh [-133,-89], OFX [-81,-37],
  // Exportar [-29,15] — só o ☰ [23,67] visível. Altura e faixas passavam.
  for (const w of [375, 320]) {
    const { ctx, page } = await abrirDash(w);
    const { hbtns } = await medirHeader(page);
    assert.ok(hbtns.length >= 5, `${w}: só ${hbtns.length} .hbtn visíveis`);
    for (const b of hbtns) {
      assert.ok(b.left >= -0.5, `${w}: .hbtn fora da tela à esquerda (left ${b.left})`);
      assert.ok(b.right <= w + 0.5, `${w}: .hbtn fora da tela à direita (right ${b.right})`);
    }
    await ctx.close();
  }
});

// NÃO é controle positivo: sem o conserto o `.hbtn-label` nem existe no HTML e
// o $eval LANÇA, em vez de falhar comparavelmente. É guarda de desktop — o que
// ele prende é alguém tirar o `@media` e apagar os rótulos em 1280 também.
test("2) 1280: guarda de desktop — os rótulos de texto continuam no header", async () => {
  const { ctx, page } = await abrirDash(1280);
  const visivel = (s) => page.$eval(s, (e) => e.getBoundingClientRect().width > 0);
  for (const s of [".hbtn .hbtn-label", ".user-menu-btn .user-menu-main"])
    assert.equal(await visivel(s), true, `desktop perdeu ${s}`);
  const m = await medirHeader(page);
  assert.equal(m.faixas, 1, `desktop em ${m.faixas} faixas`);
  await ctx.close();
});

test("3) gate Free a 375: tocar em Importar OFX abre o #upgrade-overlay", async () => {
  // o botão é só ícone e perdeu o selo "PigBank+"; o que não pode sumir é o
  // esmaecido (.pro-locked) nem o modal de upgrade
  const { ctx, page } = await abrirDash(375, { gates: {} });
  assert.equal(await page.$eval('[data-pro-feature="ofx_import"]',
    (e) => e.classList.contains("pro-locked")), true, "botão pago não ficou esmaecido");
  // `page.click` não serve: o Playwright se recusa a clicar em elemento com
  // aria-disabled="true" ("element is not enabled") e estoura 30s. O listener
  // do gate está em fase de CAPTURA no document (dashboard.js:6636), então o
  // evento sintético percorre o mesmo caminho do toque real.
  await page.$eval('[data-pro-feature="ofx_import"]',
    (e) => e.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })));
  await page.waitForSelector("#upgrade-overlay.open", { timeout: 5000 });
  await ctx.close();
});

test("4) 375: o botão de atualizar ainda gira (comportamento preservado)", async () => {
  const { ctx, page } = await abrirDash(375);
  await page.click("#refresh-btn");
  assert.equal(await page.$eval("#refresh-btn",
    (e) => e.classList.contains("spinning")), true, "#refresh-btn não girou");
  await ctx.close();
});

// TAUTOLÓGICO como prova do conserto, e fica registrado: ANTES da mudança o
// `.user-menu` já ocupava a linha inteira e o dropdown já media left≈10 /
// width 300 / right≈310 — os três asserts passavam iguais. Fica como guarda de
// não-regressão do `left:auto;right:0` que agora ancora o menu num botão de
// 44px: sem ele o dropdown herda left:0/width:100% do pai e vira uma coluna de
// 44px. Não conte este caso como cobertura do item 2.
test("5) 320 (não-regressão): o menu da conta abre DENTRO da tela", async () => {
  const { ctx, page } = await abrirDash(320);
  await page.click("#user-menu-btn");
  const d = await page.$eval("#user-dropdown", (e) => {
    const b = e.getBoundingClientRect();
    return { left: +b.left.toFixed(1), right: +b.right.toFixed(1), w: +b.width.toFixed(1) };
  });
  assert.ok(d.right <= 320.5, `dropdown vazando à direita (right ${d.right})`);
  assert.ok(d.left >= -0.5, `dropdown vazando à esquerda (left ${d.left})`);
  assert.ok(d.w >= 200, `dropdown espremido em ${d.w}px`);
  await ctx.close();
});

test("6) 320: cartão no #cards-grid cabe dentro da caixa da grade", async () => {
  const { ctx, page } = await abrirDash(320);
  // medido antes: track de 340px em caixa de 300 → filho de [10,350], 40px cortados
  const m = await page.evaluate(() => {
    document.getElementById("cards-view").classList.add("active");
    const g = document.getElementById("cards-grid");
    const c = document.createElement("div");
    c.className = "mock-card";
    c.textContent = "Cartão";
    g.appendChild(c);
    const gb = g.getBoundingClientRect(), cb = c.getBoundingClientRect();
    return {
      gridRight: +gb.right.toFixed(1), cardRight: +cb.right.toFixed(1),
      track: getComputedStyle(g).gridTemplateColumns,
    };
  });
  // único assert que discrimina: pré-fix o filho ia até 350 numa grade que
  // termina em 310. `documentElement.scrollWidth` não serve — ver nota acima.
  assert.ok(m.cardRight <= m.gridRight + 0.5,
    `cartão até ${m.cardRight} numa grade que termina em ${m.gridRight} (track ${m.track})`);
  await ctx.close();
});

test("7) formulários públicos: 16px no mobile e desktop (controle positivo)", async () => {
  const campos = {
    "/login.html": ["#email", "#senha", "#mfa-code"],
    "/cadastro.html": ["#reg-name", "#reg-email", "#reg-phone", "#reg-password"],
    "/contato.html": ["#nome", "#email", "#assunto", "#msg"],
  };
  for (const [rota, ids] of Object.entries(campos)) {
    for (const [w, esperado] of [[375, "16px"], [1280, "16px"]]) {
      const ctx = await browser.newContext({ viewport: { width: w, height: 812 } });
      const page = await ctx.newPage();
      await page.goto(`${ORIGIN}${rota}`);
      for (const id of ids) {
        const fs = await page.$eval(id, (e) => getComputedStyle(e).fontSize);
        assert.equal(fs, esperado, `${rota} ${id} a ${w}px: ${fs}`);
      }
      await ctx.close();
    }
  }
});
