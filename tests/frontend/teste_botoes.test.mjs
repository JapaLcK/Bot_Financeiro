/**
 * Botões "Testar o Piggy agora" (a[data-teste] → /teste) em /lp, /, /precos.
 * Quem faz o trabalho é frontend/teste-piggy.js: repassa utm_ e fbclid e avisa o
 * Pixel e o GA no clique. Sem JS o href="/teste" funciona.
 *
 * Controles do CLAUDE.md §3 (cada um morre com a mutação correspondente):
 *   · filtro de utm — `x=1` não passa (tirar o filtro deixa `x` no href);
 *   · clique mede — TesteClick e teste_click (tirar o try/catch de um deles
 *     faz o outro sumir quando o primeiro lança);
 *   · /precos logado — o #testar-piggy some (tirar o `.remove()`); com 401
 *     ele aparece (controle positivo: sem ele o grupo passa numa página que
 *     sempre esconde o link);
 *   · layout — o botão existe e cabe na viewport nas 3 páginas em 2 tamanhos;
 *     a / não estoura em 320×568 (tirar o `flex-wrap` de .lp-actions);
 *   · portão da /lp — sem a marca da VSL o botão do teste fica `hidden` como o
 *     do quiz (regra `.lp-travado .lp-cta.lp-cta-sec{visibility:visible}` quebra);
 *   · idempotência — o script carregado 2× não duplica query nem evento;
 *   · player da /lp — com o botão 676x380, sem ele 747x420 como na main (a regra de 420px
 *     valer sem a classe `lp-com-teste` encolhe a VSL de quem nunca viu o botão).
 *
 * O servidor deste arquivo é o estático (frontend/ cru), que NÃO roda o app Python:
 * as páginas aqui são o template, com os botões. A regra "demo desligado => a página
 * não tem botão" é do servidor e mora em tests/test_static_pages_routes.py.
 *
 * Rodar: node --test tests/frontend/teste_botoes.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const DESKTOP = { width: 1280, height: 800 };
const MOBILE = { width: 390, height: 844 };
const PAGINAS = [["/lp.html", "a.lp-cta-sec"], ["/index.html", "a.lp-btn-line"], ["/precos.html", "#testar-piggy a"]];

/** `me` = corpo do /auth/me (null = 401, deslogado). O /teste é interceptado:
 *  o servidor estático não o conhece e a navegação não pode cair num 404. */
async function abrir(caminho, { viewport = DESKTOP, query = "", me = null, antes, visto = true, wav } = {}) {
  const page = await browser.newPage({ viewport });
  const erros = [];
  page.on("pageerror", e => erros.push(String(e)));
  if (visto) await page.addInitScript(() => localStorage.setItem("pb_lp_vsl_visto", "1"));
  if (antes) await page.addInitScript(antes);
  await page.route("**/auth/me", r => me
    ? r.fulfill({ contentType: "application/json", body: JSON.stringify(me) })
    : r.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
  await page.route("**/billing/plans-config", r => r.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true }) }));
  await page.route("**/billing/subscription", r => r.fulfill({
    contentType: "application/json", body: JSON.stringify({ active: false }) }));
  // 404 libera o botão da /lp (falha sempre aberta); `wav` mantém a trava de pé.
  await page.route("**/vsl.mp4*", r => wav
    ? r.fulfill({ status: 200, contentType: "audio/wav", body: wav })
    : r.fulfill({ status: 404, body: "no" }));
  await page.route(url => new URL(url).pathname === "/teste",
    r => r.fulfill({ contentType: "text/html", body: "<title>teste</title>" }));
  await page.goto(`${ORIGIN}${caminho}${query}`, { waitUntil: "load" });
  return { page, erros };
}

for (const [caminho, seletor] of PAGINAS) {
  for (const [nome, viewport] of [["desktop 1280x800", DESKTOP], ["mobile 390x844", MOBILE]]) {
    test(`${caminho}: o botão existe, aponta para /teste e cabe na largura (${nome})`, async () => {
      const { page } = await abrir(caminho, { viewport });
      const botoes = page.locator(seletor);
      assert.ok(await botoes.count() >= 1, "sem botão");
      for (const b of await botoes.all()) {
        await b.scrollIntoViewIfNeeded();
        assert.equal(await b.getAttribute("href"), "/teste");
        assert.match((await b.textContent()).trim(), /^Testar o Piggy agora$/);
        const r = await b.boundingBox();
        assert.ok(r && r.width > 40 && r.height > 10, `sem caixa: ${JSON.stringify(r)}`);
        assert.ok(r.x >= 0 && r.x + r.width <= viewport.width, `estoura a largura: ${JSON.stringify(r)}`);
        assert.ok(await b.isVisible());
      }
      await page.close();
    });
  }
}

for (const [nome, viewport] of [["desktop 1280x800", DESKTOP], ["mobile 390x844", MOBILE]]) {
  test(`/lp: CTA principal e botão do teste ficam inteiros na primeira dobra (${nome})`, async () => {
    const { page } = await abrir("/lp.html", { viewport });
    for (const sel of ["#lp-cta", "a.lp-cta-sec"]) {
      const r = await page.locator(sel).boundingBox();
      assert.ok(r.y >= 0 && r.y + r.height <= viewport.height, `${sel} fora da dobra: ${JSON.stringify(r)}`);
    }
    await page.close();
  });
}

/** Tamanho do player da /lp. `semBotao` imita o que o servidor faz com o demo desligado
 *  (html_file sem_teste: tira o <a data-teste> e a classe `lp-com-teste`) — a remoção em si
 *  é testada em tests/test_static_pages_routes.py; aqui se mede o que o CSS faz com ela. */
async function playerDaLp(viewport, semBotao) {
  const { page } = await abrir("/lp.html", { viewport });
  if (semBotao) await page.evaluate(() => {
    document.querySelector("a[data-teste]").remove();
    document.querySelector("main").classList.remove("lp-com-teste");
  });
  const r = await page.locator(".lp-player").boundingBox();
  await page.close();
  return [Math.round(r.width), Math.round(r.height)];
}

test("/lp: com o botão do teste o player encolhe (676x380 em 1280x800); sem ele é o da main (747x420)", async () => {
  assert.deepEqual(await playerDaLp(DESKTOP, false), [676, 380]);
  assert.deepEqual(await playerDaLp(DESKTOP, true), [747, 420]);   // medido na main
  assert.deepEqual(await playerDaLp({ width: 1440, height: 900 }, true), [880, 495]);
  assert.deepEqual(await playerDaLp({ width: 1366, height: 657 }, true), [492, 277]);
});

test("/lp em 1366x657 (notebook): CTA rosa inteiro na dobra e sem overflow horizontal", async () => {
  // Aceito e declarado: neste tamanho o botão SECUNDÁRIO ("Testar o Piggy agora") pode
  // ficar abaixo da dobra (~11px). Só afirmamos o que é verdade: o CTA principal inteiro.
  const { page } = await abrir("/lp.html", { viewport: { width: 1366, height: 657 } });
  const r = await page.locator("#lp-cta").boundingBox();
  assert.ok(r.y >= 0 && r.y + r.height <= 657, `#lp-cta fora da dobra: ${JSON.stringify(r)}`);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "overflow horizontal");
  await page.close();
});

test("/: o botão do hero está na primeira dobra (desktop e mobile)", async () => {
  for (const viewport of [DESKTOP, MOBILE]) {
    const { page } = await abrir("/index.html", { viewport });
    const r = await page.locator("a.lp-btn-line").first().boundingBox();
    assert.ok(r.y >= 0 && r.y + r.height <= viewport.height, `${viewport.width}: hero fora da dobra ${JSON.stringify(r)}`);
    await page.close();
  }
});

for (const [caminho, seletor] of PAGINAS) {
  test(`${caminho}: utm_* (sem diferenciar maiúsculas) e fbclid vão ao /teste; o resto não`, async () => {
    const { page } = await abrir(caminho, { query: "?utm_source=ig&x=1&UTM_Campaign=a%20b&fbclid=AbC" });
    for (const b of await page.locator(seletor).all()) {
      assert.equal(await b.getAttribute("href"), "/teste?utm_source=ig&UTM_Campaign=a+b&fbclid=AbC");
    }
    await page.close();
    const sem = await abrir(caminho, { query: "?x=1" });
    assert.equal(await sem.page.locator(seletor).first().getAttribute("href"), "/teste");
    await sem.page.close();
  });
}

const clicarEMedir = async (page, seletor) => {
  await page.evaluate(() => {
    window.__ev = [];
    window.fbq = (...a) => window.__ev.push(["fbq", ...a]);
    window.gtag = (...a) => window.__ev.push(["gtag", ...a]);
    document.addEventListener("click", e => e.preventDefault());   // só mede; a navegação tem o caso abaixo
  });
  await page.locator(seletor).first().click();
  return page.evaluate(() => window.__ev);
};

for (const [caminho, seletor] of PAGINAS) {
  test(`${caminho}: o clique registra TesteClick (Pixel) e teste_click (GA, beacon)`, async () => {
    const { page, erros } = await abrir(caminho);
    assert.deepEqual(await clicarEMedir(page, seletor), [
      ["fbq", "trackCustom", "TesteClick"],
      ["gtag", "event", "teste_click", { transport_type: "beacon" }],
    ]);
    assert.deepEqual(erros, []);
    await page.close();
  });
}

test("fbq que lança não impede o gtag nem a navegação, e sem fbq/gtag o clique navega sem erro", async () => {
  const { page, erros } = await abrir("/lp.html");
  await page.evaluate(() => {
    window.__ev = [];
    window.fbq = () => { throw new Error("fbq quebrou"); };
    window.gtag = (...a) => window.__ev.push(a);
  });
  const nav = page.waitForURL(/\/teste$/);
  await page.click("a.lp-cta-sec");
  await nav;
  assert.deepEqual(erros, [], "o erro do fbq vazou para a página");
  await page.close();

  const b = await abrir("/precos.html");
  await b.page.evaluate(() => { delete window.fbq; delete window.gtag; });
  const nav2 = b.page.waitForURL(/\/teste$/);
  await b.page.click("#testar-piggy a");
  await nav2;
  assert.deepEqual(b.erros, []);
  await b.page.close();

  // o gtag da primeira parte precisa ter recebido o evento mesmo com o fbq lançando
  const c = await abrir("/lp.html");
  await c.page.evaluate(() => {
    window.__ev = [];
    window.fbq = () => { throw new Error("fbq quebrou"); };
    window.gtag = (...a) => window.__ev.push(a);
    document.addEventListener("click", e => e.preventDefault());
  });
  await c.page.click("a.lp-cta-sec");
  assert.deepEqual(await c.page.evaluate(() => window.__ev), [["event", "teste_click", { transport_type: "beacon" }]]);
  assert.deepEqual(c.erros, []);
  await c.page.close();
});

test("/precos: logado não vê o #testar-piggy; deslogado (401) vê", async () => {
  const logado = await abrir("/precos.html", { me: { user_id: 42, needs_plan_selection: false } });
  await logado.page.waitForTimeout(600);
  assert.equal(await logado.page.locator("#testar-piggy").count(), 0, "logado ainda vê o link");
  await logado.page.close();

  const anonimo = await abrir("/precos.html");
  await anonimo.page.waitForTimeout(600);
  assert.equal(await anonimo.page.locator("#testar-piggy").isVisible(), true, "deslogado não vê o link");
  await anonimo.page.close();
});

/** WAV mudo de 2 s (como no lp.test.mjs): mídia que carrega, para a /lp não liberar por erro. */
const WAV = (() => {
  const n = 8000 * 2, b = Buffer.alloc(44 + n, 0x80);
  b.write("RIFF", 0); b.writeUInt32LE(36 + n, 4); b.write("WAVEfmt ", 8); b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(8000, 24); b.writeUInt32LE(8000, 28);
  b.writeUInt16LE(1, 32); b.writeUInt16LE(8, 34); b.write("data", 36); b.writeUInt32LE(n, 40);
  return b;
})();

test("/lp sem a marca da VSL: o botão do teste fica escondido como o do quiz; com a marca, os dois aparecem", async () => {
  const visivel = (page, sel) => page.$eval(sel, a => getComputedStyle(a).visibility === "visible");
  const travada = await abrir("/lp.html", { visto: false, wav: WAV });
  assert.equal(await travada.page.evaluate(() => document.documentElement.classList.contains("lp-travado")), true,
    "a página não nasceu travada: o caso não mede o portão");
  assert.equal(await visivel(travada.page, "#lp-cta"), false, "CTA do quiz visível antes da VSL");
  assert.equal(await visivel(travada.page, "a.lp-cta-sec"), false, "botão do teste visível antes da VSL");
  await travada.page.close();

  const liberada = await abrir("/lp.html", { visto: true });   // controle positivo: a marca libera os dois
  assert.equal(await visivel(liberada.page, "#lp-cta"), true);
  assert.equal(await visivel(liberada.page, "a.lp-cta-sec"), true);
  await liberada.page.close();
});

test("/: em 320x568 hero e CTA final não estouram a largura (os botões quebram de linha)", async () => {
  const viewport = { width: 320, height: 568 };
  const { page } = await abrir("/index.html", { viewport });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "overflow horizontal na /");
  const blocos = await page.locator(".lp-actions").all();
  assert.equal(blocos.length, 2, "esperava .lp-actions no hero e no CTA final");
  for (const bloco of blocos) {
    await bloco.scrollIntoViewIfNeeded();
    for (const filho of await bloco.locator("> a").all()) {
      const r = await filho.boundingBox();
      assert.ok(r.x >= 0 && r.x + r.width <= viewport.width, `filho fora da viewport: ${JSON.stringify(r)}`);
    }
  }
  await page.close();
});

for (const [caminho, seletor] of PAGINAS) {
  test(`${caminho}: o script carregado duas vezes não duplica a query nem o evento de clique`, async () => {
    const { page, erros } = await abrir(caminho, { query: "?utm_source=ig" });
    await page.addScriptTag({ url: "/teste-piggy.js" });
    for (const b of await page.locator(seletor).all()) {
      assert.equal(await b.getAttribute("href"), "/teste?utm_source=ig");
    }
    assert.deepEqual(await clicarEMedir(page, seletor), [
      ["fbq", "trackCustom", "TesteClick"],
      ["gtag", "event", "teste_click", { transport_type: "beacon" }],
    ]);
    assert.deepEqual(erros, []);
    await page.close();
  });
}
