/**
 * INVARIANTE: no modo app (375×812), a barra do #toast e a bolinha do
 * #piggy-fab NÃO se sobrepõem — em nenhum estado do FAB.
 *
 * O defeito: os dois são `fixed` no canto inferior direito. O toast se
 * afastava pelo LADO (`right: 92px`, dashboard.css:1621); no modo app o FAB
 * anda para `right: 14px` e cresce o alcance, e a barra do toast tem LARGURA
 * VARIÁVEL — qualquer mensagem mais longa reconquista o canto e passa por
 * cima da bolinha. Medido aqui antes do conserto: 46 × 35 = 1610 px² de
 * interseção (a ficha registrou 46 × 35,39 = 1627,97 px² com outra mensagem).
 * O conserto separa na VERTICAL (app-mode.css,
 * `html.pb-app body.pb-page-app #toast`), que é a dimensão que o texto não
 * controla — e se dimensiona pelo PISO DO ARRASTO do FAB, não pelo repouso
 * dele; ver o bloco do caso (c).
 *
 * Por que a separação vertical é segura AGORA e não era em 8d757be (revertido):
 * aquele commit subia o toast por cima do `#piggy-panel` (bottom 90px, campo
 * de digitar no rodapé) e valia para o site inteiro. O painel flutuante não
 * existe mais — o chat virou ilha React em tela cheia, que ESCONDE o FAB
 * (`html.pb-chat-open #piggy-fab{visibility:hidden}`, chat-app.css) — e a
 * regra de hoje é escopada em `body.pb-page-app`, só o dashboard. O caso (d)
 * guarda justamente esse escopo.
 *
 * AS DUAS SONDAS CONTRA A CEGUEIRA DE env() (docs/ambiente.md §6c): no
 * Chromium headless `env(safe-area-inset-*)` vale 0, então medir só aqui
 * confirma a aritmética num aparelho que não existe. Daí (1) remedir com os
 * termos env() substituídos por valores de iPhone real (34px embaixo), e (2)
 * ler o CSS e comparar as constantes do toast com as do FAB — essa segunda
 * pega quem mudar o bottom do FAB e esquecer o toast, que é como a
 * sobreposição nasceu.
 *
 * Rodar: NODE_PATH=$(npm root -g) node --test tests/frontend/toast_fab_app_375.test.mjs
 * Precisa de `npm ci` na raiz (playwright) + `npx playwright install chromium`.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const DASHBOARD_CHAT_JS = join(FRONTEND, "dashboard-chat.js");
const APP_MODE_CSS = readFileSync(join(FRONTEND, "app-mode.css"), "utf8");

/** UA do app: é ele que liga o `pb-app` e monta a `.pb-tabbar` do caso (e). */
const APP_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
  + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1 PigBankApp/1.0";
const VIEWPORT = { width: 375, height: 812 };

// As duas começam com ✓: é o default do showToast e o ramo que injeta o
// sticker de 22px, ou seja, a caixa MAIS ALTA das duas possíveis.
const CURTA = "✓ Atualizado";
const LONGA = "✓ Lançamento salvo e a fatura do cartão foi recalculada com sucesso "
  + "para o mês corrente, incluindo as parcelas futuras.";

/**
 * Folga de projeto entre o rodapé do toast e o topo do FAB, no piso do
 * arrasto, e a tolerância com que ela é asserida.
 *
 * A tolerância NÃO é frescura de arredondamento: os dois lados medem o
 * viewport de fontes diferentes. O rodapé do toast é CSS — sai de
 * `altura_do_viewport - 158`, com a altura FRACIONÁRIA que o layout usa
 * (812,3694… neste headless). O topo do FAB é JS — o clampTop lê
 * `window.innerHeight`, que é INTEIRO (812). A folga real vale
 * `828 - altura_fracionária`, ou seja, 8px MENOS a parte fracionária do
 * viewport: medi 7,9987 numa execução e 7,6306 em outra, e nenhuma das duas é
 * bug. Daí 1px, que absorve a fração inteira e continua pegando qualquer
 * regressão de verdade — o menor passo que o CSS ou o RESERVE_BOTTOM dão é 8.
 */
const FOLGA = 8, EPS = 1;

/**
 * O host TEM que ser 127.0.0.1: o app-mode.js só mapeia `/dashboard.html` para
 * a página "app" em localhost/127.0.0.1 (:64) — o caminho de produção é `/app`.
 * Com qualquer outro host `page` fica indefinido, o gate entra em `pb-no-tabs`
 * e a `.pb-tabbar` do caso (e) nunca é montada. A porta é ficção: toda
 * requisição é atendida pela rota acima, nada vai para a rede.
 */
const ORIGIN = "http://127.0.0.1:1";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const TIPOS = { html: "text/html", js: "application/javascript", mjs: "application/javascript",
                css: "text/css", json: "application/json", svg: "image/svg+xml",
                png: "image/png", webp: "image/webp", ico: "image/x-icon",
                woff2: "font/woff2", woff: "font/woff" };

/**
 * Os arquivos saem do DISCO, por rota do Playwright, e não do `_server.mjs`.
 *
 * Não é preferência de estilo: o `_server.mjs` é UM `python -m http.server`,
 * single-thread, e o `node --test` roda esta pasta em PARALELO. Este arquivo
 * pede o dashboard INTEIRO (dashboard.js tem ~11k linhas) seis vezes, e sob
 * disputa o download não terminava nem em 60s — dois casos morriam com
 * `waitForFunction: Timeout`, um vermelho que não tem nada a ver com o CSS
 * medido aqui. Ler do disco tira este arquivo da fila e ainda dispensa um
 * processo. Parente do agent_chat_fixture.mjs, mas NÃO igual: aquele monta a
 * página com `setContent` e um HTML remontado por regex; aqui o dashboard.html
 * de verdade é carregado por `page.goto` e só o TRANSPORTE muda. Mais fiel,
 * não mais frouxo.
 */
function servirDoDisco(route, url) {
  const ext = /\.([a-z0-9]+)$/i.exec(url.pathname)?.[1]?.toLowerCase();
  if (!ext) return route.fulfill(json({}));                 // rota de API
  try {
    return route.fulfill({ status: 200, contentType: TIPOS[ext] || "application/octet-stream",
                           body: readFileSync(join(FRONTEND, url.pathname.replace(/^\//, ""))) });
  } catch (_) {
    return route.fulfill({ status: 404, body: "" });        // asset que o app tolera faltar
  }
}

/**
 * Abre `pagina` no modo app. `fabPos` é semeado ANTES do load porque o
 * restore do dashboard-chat.js roda no boot do script.
 */
async function abrir(pagina = "dashboard.html", fabPos = null) {
  const ctx = await browser.newContext({ viewport: VIEWPORT, userAgent: APP_UA, hasTouch: true });
  const page = await ctx.newPage();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();        // CDN fora
    return servirDoDisco(route, url);
  });
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) => route.fulfill(json({ app_access: true })));
  // DIVISÃO DE TRABALHO, e ela não é arbitrária:
  //  - `pb-app` fica com o app-mode.js DE VERDADE (o UA acima o liga). Tem que
  //    ser ele porque o dashboard-chat.js LÊ essa classe para decidir se liga o
  //    arrasto do FAB (:140), e o app-mode.js a escreve em :46, síncrono, antes
  //    de qualquer outro script. Tentar escrevê-la daqui por MutationObserver
  //    era uma corrida: o callback do observer podia ser entregue DEPOIS do
  //    dashboard-chat.js, e aí o FAB semeado nunca era restaurado.
  //  - `pb-page-app` fica comigo. Ela só entra em app-mode.js:151, no fim de um
  //    boot que sob paralelismo não chegava nem em 120s, e NENHUM JS a lê — é
  //    puro seletor de CSS. Escrevê-la aqui tira o teste do caminho crítico do
  //    boot sem falsear o que está sendo medido.
  // SÓ no dashboard: o caso (d) depende de o /settings NÃO tê-la, que é o
  // escopo inteiro da regra em teste.
  await page.addInitScript(({ pos, dash }) => {
    if (pos) { try { localStorage.setItem("pbFabPos", JSON.stringify(pos)); } catch (_) {} }
    if (!dash) return;
    const obs = new MutationObserver(() => {
      if (document.body) { document.body.classList.add("pb-page-app"); obs.disconnect(); }
    });
    obs.observe(document, { childList: true, subtree: true });
    document.addEventListener("DOMContentLoaded",
      () => document.body.classList.add("pb-page-app"));
  }, { pos: pagina === "dashboard.html" ? fabPos : null, dash: pagina === "dashboard.html" });
  await page.goto(`${ORIGIN}/${pagina}`);
  // A espera cobre o que o init script NÃO controla: o #toast existir e, com
  // `pbFabPos` semeado, o dashboard-chat.js já ter rodado o restore. Medir
  // antes disso devolvia o FAB no repouso — vermelho de boot, não de CSS.
  // `pbFabPos` semeado ⇒ espera também o inline `top` que o place() escreve.
  await page.waitForFunction(({ ehDash, temFabPos }) => {
    if (!document.body || !document.getElementById("toast")) return false;
    if (typeof window.showToast !== "function") return false;   // estímulo real
    if (!document.documentElement.classList.contains("pb-app")) return false;
    if (ehDash && !document.body.classList.contains("pb-page-app")) return false;
    if (!temFabPos) return true;
    const fab = document.getElementById("piggy-fab");
    return !!fab && fab.style.top !== "";
  // 10s, e não os 60s de antes: com os arquivos vindo do disco cada caso boota
  // em ~450ms. 60s deixou de ser margem e virou um caso travado esperando para
  // falhar tarde. 10s ainda é ~20x a medição.
  }, { ehDash: pagina === "dashboard.html", temFabPos: !!fabPos }, { timeout: 10_000 });
  page.__ctx = ctx;
  return page;
}

/**
 * Acende o toast pelo CAMINHO REAL (`showToast`, dashboard.js:7572) e espera a
 * transição de .22s assentar.
 *
 * Escrever `textContent` + `.show` na mão parecia equivalente e não é: para
 * mensagem que começa com ✓ — que é o DEFAULT — o showToast monta `innerHTML`
 * com um `<img class="toast-sticker">` de 22px, e a caixa passa de 35px para
 * 42px de altura. Medir a caixa errada é medir outro elemento.
 *
 * O `setInterval` existe porque o showToast se apaga sozinho em 2s
 * (dashboard.js:7583) e o `toastT` que o agenda é `let` de escopo de script,
 * inalcançável daqui. Sob paralelismo uma medição podia cair depois dos 2s e
 * ler a caixa já escondida. Morre com a página.
 */
async function comToast(page, msg) {
  await page.evaluate((m) => {
    window.showToast(m);
    const t = document.getElementById("toast");
    clearInterval(window.__mantemToast);
    window.__mantemToast = setInterval(() => t.classList.add("show"), 100);
  }, msg);
  await page.waitForTimeout(300);
}

/** Retângulos + área de interseção, tudo medido no navegador. */
const medir = (page, seletor = "#piggy-fab") => page.evaluate((sel) => {
  const r = (el) => { const b = el.getBoundingClientRect();
    return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, w: b.width, h: b.height }; };
  const toast = document.getElementById("toast");
  const outro = document.querySelector(sel);
  if (!outro) return { faltando: sel };
  const a = r(toast), b = r(outro);
  const ow = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
  const oh = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  return { toast: a, outro: b, area: ow * oh, ow, oh,
           toastBottomCss: getComputedStyle(toast).bottom,
           fabVisibility: getComputedStyle(outro).visibility };
}, seletor);

/**
 * SONDA 1 contra a cegueira de env(): repete os DOIS seletores com os termos
 * env() trocados por valores de iPhone real (34px de home indicator embaixo,
 * 0 nas laterais em retrato). Sem isto o teste só prova a aritmética de um
 * aparelho sem área segura — que não é o aparelho do usuário.
 */
async function comSafeAreaDeIphone(page) {
  await page.addStyleTag({ content: `
    html.pb-app #piggy-fab { bottom: calc(84px + 34px) !important; right: calc(14px + 0px) !important; }
    html.pb-app body.pb-page-app #toast {
      bottom: calc(92px + 58px + 8px + 34px) !important;
      right: calc(26px + 0px) !important;
    }` });
  await page.waitForTimeout(50);
}

test("(a) toast curto e LONGO não tocam o FAB — e o longo tem mesmo ≥2 linhas", async () => {
  const page = await abrir();

  await comToast(page, CURTA);
  const curto = await medir(page);
  assert.equal(curto.area, 0,
    `toast curto sobre o FAB: ${curto.ow}×${curto.oh} = ${curto.area}px² — ${JSON.stringify(curto)}`);
  assert.ok(curto.toast.bottom <= curto.outro.top,
    `o rodapé do toast (${curto.toast.bottom}) tinha que ficar acima do topo do FAB (${curto.outro.top})`);

  await comToast(page, LONGA);
  const longo = await medir(page);
  assert.ok(longo.toast.h > curto.toast.h,
    `a mensagem longa não quebrou linha (h ${longo.toast.h} vs ${curto.toast.h}) — o caso perdeu o sentido`);
  // A barra cresce PARA CIMA (presa pelo bottom): o que tem que continuar
  // acima do FAB é o RODAPÉ dela.
  assert.equal(longo.area, 0,
    `toast longo sobre o FAB: ${longo.ow}×${longo.oh} = ${longo.area}px² — ${JSON.stringify(longo)}`);
  assert.ok(longo.toast.bottom <= longo.outro.top,
    `rodapé do toast longo (${longo.toast.bottom}) abaixo do topo do FAB (${longo.outro.top})`);

  // SONDA 1: mesma medição com a safe area de um iPhone de verdade.
  await comSafeAreaDeIphone(page);
  const iphone = await medir(page);
  assert.equal(iphone.area, 0,
    `com safe-area de iPhone (34px) o toast volta a cobrir o FAB: ${JSON.stringify(iphone)}`);

  await page.__ctx.close();
});

test("(b) chat aberto: o FAB some e o painel (z 1200) cobre o toast (z 999)", async () => {
  const page = await abrir();
  await comToast(page, CURTA);

  await page.evaluate(() => {
    document.documentElement.classList.add("pb-chat-open");
    // O painel da ilha React: reproduzido com as classes reais para pegar as
    // regras de chat-app.css (a ilha só monta com backend de chat).
    const p = document.createElement("div");
    p.className = "pc-chat-panel";
    p.id = "painel-falso";
    (document.getElementById("pigbank-chat-root") || document.body).appendChild(p);
  });
  // Ler no MESMO evaluate em que se escreve a classe é corrida: o estilo do
  // #piggy-fab ainda podia sair "visible" sob paralelismo, e o vermelho era de
  // timing, não de z-index. Espera a regra de chat-app.css ter sido aplicada.
  await page.waitForFunction(() =>
    getComputedStyle(document.getElementById("piggy-fab")).visibility === "hidden"
    && getComputedStyle(document.getElementById("painel-falso")).zIndex !== "auto",
    null, { timeout: 30_000 });

  const out = await page.evaluate(() => {
    const p = document.getElementById("painel-falso");
    const zi = (el) => getComputedStyle(el).zIndex;
    return {
      fabVisibility: getComputedStyle(document.getElementById("piggy-fab")).visibility,
      zPainel: Number(zi(p)),
      zToast: Number(zi(document.getElementById("toast"))),
      painelCobre: p.getBoundingClientRect().toJSON(),
      toast: document.getElementById("toast").getBoundingClientRect().toJSON(),
    };
  });
  assert.equal(out.fabVisibility, "hidden", "com o chat aberto o FAB tinha que estar escondido");
  assert.ok(out.zPainel > out.zToast,
    `painel (${out.zPainel}) tinha que pintar acima do toast (${out.zToast})`);
  // Tela cheia: o retângulo do painel contém o do toast.
  assert.ok(out.painelCobre.top <= out.toast.top && out.painelCobre.bottom >= out.toast.bottom
    && out.painelCobre.left <= out.toast.left && out.painelCobre.right >= out.toast.right,
    `o painel não cobre o toast: ${JSON.stringify(out)}`);

  await page.__ctx.close();
});

/*
 * (c) O FAB tem DUAS posições que a geometria do toast precisa cobrir, e elas
 * não são a mesma:
 *   - REPOUSO, dado pelo CSS: bottom 84px (app-mode.css:596) → [670 ; 728].
 *   - PISO DO ARRASTO, dado pelo JS: clampTop com RESERVE_BOTTOM 92
 *     (dashboard-chat.js:145) → topo em 812-58-92 = 662, logo [662 ; 720].
 * O piso é MAIS ALTO que o repouso — e é por ele que o toast se dimensiona.
 * Medir só o repouso deixaria passar um toast em 150px, que encosta no piso
 * com folga ZERO. Com 158px: 16px de folga no repouso, 8px no piso.
 *
 * LIMITAÇÃO CONHECIDA E ACEITA (medida no caso "meio da tela" abaixo):
 * `clampTop` só limita o FAB POR BAIXO. Nada impede o usuário de largar a
 * bolinha numa posição INTERMEDIÁRIA que caia dentro da faixa do toast
 * [618,6 ; 654] — nenhum valor de RESERVE_BOTTOM alcança esse caso.
 *
 * E é preciso dizer o resto, que não é confortável: essa faixa de colisão não
 * sumiu nem encolheu com o conserto — ela SUBIU junto com o toast, ~35px, e em
 * parte dela o encobrimento PIOROU. No mesmo ponto de amostra (top=640) a
 * sobreposição era 46×8,63 = 397px² ANTES e é 46×14 = 644px² DEPOIS, 62% maior.
 * O que o conserto resolve são as duas posições que o usuário alcança sem
 * querer (repouso e piso do arrasto); a posição intermediária é escolha
 * deliberada dele e continua descoberta.
 *
 * Não vira asserção verde aqui porque o dano é pequeno e o conserto é caro: o #toast é
 * `pointer-events: none` (dashboard.css:1626), não intercepta toque nenhum e
 * some sozinho em segundos; cobri-lo exigiria o toast consultar o retângulo do
 * FAB em tempo de exibição, acoplando dashboard.js ao widget do chat por um
 * encobrimento visual passageiro.
 */
test("(c) FAB em REPOUSO (bottom do CSS) não toca o toast", async () => {
  const page = await abrir();                 // sem pbFabPos: posição do CSS
  await comToast(page, CURTA);
  const m = await medir(page);
  assert.equal(m.area, 0,
    `FAB em repouso sobrepõe o toast: ${m.ow}×${m.oh} = ${m.area}px² — ${JSON.stringify(m)}`);
  assert.ok(m.outro.top - m.toast.bottom >= FOLGA - EPS,
    `folga de repouso menor que ${FOLGA}px: ${m.outro.top - m.toast.bottom}px — ${JSON.stringify(m)}`);
  await page.__ctx.close();
});

for (const side of ["right", "left"]) {
  test(`(c) FAB no PISO do arrasto (${side}) continua abaixo do toast`, async () => {
    // top absurdo = "arrastado até o fundo": quem segura é o clampTop
    // (dashboard-chat.js:146). ATENÇÃO ao sentido do RESERVE_BOTTOM: ele
    // reserva a faixa de baixo, logo limita o TOPO do FAB — valores MAIORES
    // SOBEM o FAB, para dentro da faixa do toast.
    const page = await abrir("dashboard.html", { side, top: 5000 });
    await comToast(page, CURTA);

    const m = await medir(page);
    assert.equal(m.area, 0,
      `FAB no piso (${side}) sobrepõe o toast: ${m.ow}×${m.oh} = ${m.area}px² — ${JSON.stringify(m)}`);
    // O piso tem que ficar ACIMA do repouso, senão este caso é o mesmo do
    // anterior disfarçado e a cobertura do arrasto é fantasia.
    assert.ok(m.outro.top < 670,
      `o clamp não subiu o FAB acima do repouso (top ${m.outro.top}) — caso vacuo`);
    // `- EPS`: aqui a folga de projeto é EXATAMENTE FOLGA, e o layout devolve
    // 7.99871826171875. Ver o comentário de FOLGA.
    assert.ok(m.outro.top - m.toast.bottom >= FOLGA - EPS,
      `folga no piso menor que ${FOLGA}px: ${m.outro.top - m.toast.bottom}px — ${JSON.stringify(m)}`);

    await comSafeAreaDeIphone(page);
    const iphone = await medir(page);
    assert.equal(iphone.area, 0,
      `FAB no piso (${side}) com safe-area de iPhone: ${JSON.stringify(iphone)}`);
    await page.__ctx.close();
  });
}

test("(c-limite) FAB largado NO MEIO: mede e REGISTRA a sobreposição que sobra", async () => {
  // Posição intermediária, dentro da faixa do toast e longe do piso — o
  // clampTop não a toca. Este caso NÃO assere área 0 de propósito: ver a
  // LIMITAÇÃO CONHECIDA acima. Ele assere só que o clamp deixou o FAB onde
  // foi largado (senão a limitação teria sumido e o comentário, mentido) e
  // imprime o número para quem for reavaliar a decisão.
  const page = await abrir("dashboard.html", { side: "right", top: 640 });
  await comToast(page, CURTA);
  const m = await medir(page);
  assert.equal(m.outro.top, 640,
    `o clamp mexeu na posição intermediária (${m.outro.top}) — a limitação mudou, reavalie`);
  console.log(`(c-limite) FAB em top=640, toast [${m.toast.top} ; ${m.toast.bottom}]: `
    + `sobreposição ${m.ow}×${m.oh} = ${m.area}px² (antes do conserto, no mesmo ponto: `
    + "46×8,63 = 397px² — este ponto PIOROU 62%). Aceita: #toast é pointer-events:none "
    + "(dashboard.css:1626) e some em segundos.");
  assert.equal(await page.evaluate(() =>
    getComputedStyle(document.getElementById("toast")).pointerEvents), "none",
    "a limitação acima só é aceitável porque o toast não intercepta toque");
  await page.__ctx.close();
});

test("(d) o toast do settings NÃO foi mexido: continua centrado e em bottom 88px", async () => {
  const page = await abrir("settings.html");
  await comToast(page, CURTA);

  const out = await page.evaluate(() => {
    const t = document.getElementById("toast");
    const b = t.getBoundingClientRect();
    return { centro: (b.left + b.right) / 2, meio: window.innerWidth / 2,
             bottom: getComputedStyle(t).bottom,
             pageApp: document.body.classList.contains("pb-page-app") };
  });
  assert.equal(out.pageApp, false, "settings não é pb-page-app — se virar, o escopo da regra muda");
  assert.ok(Math.abs(out.centro - out.meio) <= 1,
    `o toast do settings saiu do centro: ${out.centro} vs ${out.meio}`);
  assert.equal(out.bottom, "88px",
    "a etapa 1 vazou para fora de pb-page-app e mexeu no toast do settings");
  await page.__ctx.close();
});

test("(e) o FAB é clicável no centro e o toast também não invade a tab bar", async () => {
  const page = await abrir();
  await comToast(page, LONGA);

  const out = await page.evaluate(() => {
    const fab = document.getElementById("piggy-fab");
    const b = fab.getBoundingClientRect();
    const alvo = document.elementFromPoint((b.left + b.right) / 2, (b.top + b.bottom) / 2);
    return { hit: alvo && alvo.closest("#piggy-fab") ? "piggy-fab" : (alvo && alvo.id) || "?",
             lado: Math.min(b.width, b.height) };
  });
  assert.equal(out.hit, "piggy-fab", "o centro do FAB devolveu outro elemento — está coberto");
  assert.ok(out.lado >= 44, `alvo do FAB abaixo de 44px: ${out.lado}`);

  const tab = await medir(page, ".pb-tabbar");
  assert.ok(!tab.faltando, "a tab bar do modo app não montou — a medição abaixo seria vazia");
  assert.equal(tab.area, 0, `o toast invade a tab bar: ${JSON.stringify(tab)}`);
  await page.__ctx.close();
});

test("SONDA 2 (lida do CSS): o bottom do toast cobre o bottom do FAB + o lado dele", () => {
  const somaDe = (seletor) => {
    const bloco = new RegExp(seletor.replace(/[.#]/g, "\\$&") + "\\s*\\{([^}]*)\\}").exec(APP_MODE_CSS);
    assert.ok(bloco, `regra "${seletor}" sumiu de app-mode.css`);
    const calc = /bottom:\s*calc\(([^)]*\([^)]*\)[^)]*|[^)]*)\)/.exec(bloco[1]);
    assert.ok(calc, `"${seletor}" perdeu o bottom em calc(): ${bloco[1]}`);
    // O SINAL importa: somar todo `(\d+)px` faria `calc(92px + 58px - 8px)`
    // contar 158 e a sonda aprovaria uma folga que não existe.
    const termos = [...calc[1].matchAll(/([+-]?)\s*(\d+)px/g)];
    assert.ok(termos.length, `"${seletor}" não tem termo em px: ${calc[1]}`);
    return termos.reduce((s, m) => s + (m[1] === "-" ? -1 : 1) * Number(m[2]), 0);
  };
  const fab = somaDe("html.pb-app #piggy-fab");
  const toast = somaDe("html.pb-app body.pb-page-app #toast");
  const LADO_FAB = 58;   // dashboard.css:2324
  assert.ok(toast >= fab + LADO_FAB + FOLGA,
    `o toast (${toast}px) não limpa o FAB em repouso (${fab}px + ${LADO_FAB}px de lado `
    + `+ ${FOLGA}px de folga). Mexeu no bottom do FAB? O do toast tem que subir junto.`);

  // E o piso do ARRASTO, que é mais alto que o repouso e é quem de fato
  // dimensiona: o 92 do CSS é CÓPIA do RESERVE_BOTTOM, em outro arquivo.
  // A FOLGA entra na conta de propósito. Sem ela a asserção era `>= 150` e
  // um toast de 150px — que encosta no piso com folga ZERO — passava; foi
  // exatamente o erro de conta desta ficha, e a sonda ficou VERDE nele
  // enquanto os casos (c) geométricos vermelhavam. Sonda que não pega o que o
  // comentário promete é pior que sonda nenhuma.
  const reserve = Number(
    /const RESERVE_BOTTOM = (\d+)/.exec(readFileSync(DASHBOARD_CHAT_JS, "utf8"))?.[1]);
  assert.ok(Number.isFinite(reserve), "RESERVE_BOTTOM sumiu de dashboard-chat.js");
  assert.ok(toast >= Math.max(fab, reserve) + LADO_FAB + FOLGA,
    `o toast (${toast}px) não limpa o PISO do arrasto `
    + `(RESERVE_BOTTOM ${reserve}px + ${LADO_FAB}px de lado + ${FOLGA}px de folga). `
    + "Os dois 92 são cópias: mexeu num, mexa no outro.");
});
