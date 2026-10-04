/**
 * Orientação durante a configuração (#728): o wizard /onboarding no navegador.
 *
 *  1. Passo 2: "Conectar seu banco" vem ANTES do campo de dinheiro.
 *  2. Passo 2: o estado real da conexão (snapshot GET /open-finance/{id}) troca
 *     de "Atualizando…" para "Atualizado" pelo repoll — e o repoll PARA quando
 *     não há mais `updating`, quando o usuário sai do passo e no teto.
 *  3. Passo 5: "Tudo pronto!" só com o 200; sem a conclusão gravada nada sai
 *     para o /home; a conversão sai uma vez, quando a conclusão grava.
 * Desktop (1280) e celular (390); relógio do Playwright (`page.clock`).
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const VIEWPORTS = [{ width: 1280, height: 800 }, { width: 390, height: 844 }];
let ORIGIN, server, browser;

before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

const UPDATING = { institution_name: "Nubank",
  ui: { state: "updating", label: "Atualizando…", detail: "Ainda não sincronizou" } };
const UPDATED = { institution_name: "Nubank", ui: { state: "updated", label: "Atualizado", detail: null } };

/**
 * Abre o wizard no `step` salvo. `snapshot(n)`: n-ésimo GET do OF; `saveStatus`:
 * status do POST (Promise segura em voo). `concluido`: já carimbado, nenhum POST
 * devolve `stamped: true`. `calls.conv`: Pixel e GA4, mesmo após ir ao /home.
 */
async function abrir(viewport, { step, snapshot = () => json({ ok: true, connections: [] }),
                                saveStatus = () => 200, concluido = false, estado = {} } = {}) {
  const page = await browser.newPage({ viewport });
  await page.clock.install();
  const calls = { of: 0, posts: [], conv: [] };
  let carimbado = concluido;
  await page.exposeFunction("__conv", (nome) => { calls.conv.push(nome); });
  await page.addInitScript(() => {
    window.fbq = (_tipo, nome) => window.__conv(nome);
    window.pbTrack = (nome, _p, depois) => { window.__conv(nome); if (depois) depois(); };
  });

  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/dashboard-profile", (route) =>
    route.fulfill(json({ user_id: 1, display_name: "Lucas", plan: "free" })));
  await page.route("**/onboarding/state", async (route) => {
    if (route.request().method() === "GET") return route.fulfill(json({ step, completed: concluido, total_steps: 5, ...estado }));
    const body = route.request().postDataJSON();
    calls.posts.push(body);
    const status = await saveStatus(body);
    if (status !== 200) return route.fulfill(json({ detail: "x" }, status));
    const stamped = !!body.completed && !carimbado;
    if (stamped) carimbado = true;
    return route.fulfill(json({ step: body.step, completed: carimbado, stamped }));
  });
  await page.route("**/open-finance/1", async (route) => { calls.of += 1; return route.fulfill(await snapshot(calls.of)); });
  await page.route("**/home", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<p>home</p>" }));

  await page.goto(`${ORIGIN}/comecar.html`);
  await page.waitForSelector(`.onb-step[data-step="${step}"]:not([hidden])`);
  return { page, calls };
}

/** O pedido de cada tique chega à rota depois: espera o contador parar 300 ms. */
async function assentado(calls) {
  for (;;) {
    const antes = calls.of;
    await new Promise((r) => setTimeout(r, 300));
    if (calls.of === antes) return antes;
  }
}

/** A conversão de ativação, uma vez: o Pixel e o par no GA4. */
const CONVERSAO = ["OnboardingComplete", "onboarding_complete"];

const syncText = (page) => page.$eval('[data-role="of-sync"]', (e) => e.innerText);

for (const vp of VIEWPORTS) {
  const tag = `[${vp.width}]`;

  test(`${tag} passo 2: "Conectar seu banco" vem antes do campo de dinheiro`, async () => {
    const { page } = await abrir(vp, { step: 2 });
    const banco = await page.locator(".onb-step[data-step='2'] .onb-block-title", { hasText: "Conectar seu banco" }).boundingBox();
    const saldo = await page.locator("#onb-balance").boundingBox();
    assert.ok(banco && saldo, "os dois têm de estar visíveis");
    assert.ok(banco.y < saldo.y, `banco em y=${banco.y}, saldo em y=${saldo.y}`);
    await page.close();
  });

  // `of_produtos` é o que o connect token pede (pluggy_products); a tela lista
  // isso e nada além. Controle negativo: a lista fixa antiga mostrava 4 itens
  // para 2 produtos e nenhum para o produto sem rótulo.
  const produtos = (page) => page.$$eval('[data-role="of-products"] li', (lis) => lis.map((li) => li.textContent));
  const produtosVisiveis = (page) => page.isVisible('[data-role="of-products"]');

  test(`${tag} passo 2: lista só os produtos que o servidor manda`, async () => {
    const { page } = await abrir(vp, { step: 2, estado: { of_produtos: ["ACCOUNTS", "TRANSACTIONS"] } });
    assert.deepEqual(await produtos(page), ["Contas e saldos", "Transações"]);
    assert.ok(await produtosVisiveis(page));
    await page.close();
  });

  test(`${tag} passo 2: produto sem rótulo aparece com o nome cru`, async () => {
    const { page } = await abrir(vp, { step: 2, estado: { of_produtos: ["ACCOUNTS", "PRODUTO_NOVO"] } });
    assert.deepEqual(await produtos(page), ["Contas e saldos", "PRODUTO_NOVO"]);
    await page.close();
  });

  test(`${tag} passo 2: sem of_produtos, nenhuma lista`, async () => {
    const { page } = await abrir(vp, { step: 2 });
    assert.deepEqual(await produtos(page), []);
    assert.equal(await produtosVisiveis(page), false, "a frase 'O que eu leio' some junto");
    await page.close();
  });

  test(`${tag} passo 2: updating → updated troca o texto e para o repoll`, async () => {
    const { page, calls } = await abrir(vp, {
      step: 2,
      snapshot: (n) => json({ ok: true, connections: [n === 1 ? UPDATING : UPDATED] }),
    });
    await page.waitForFunction(() => document.querySelector('[data-role="of-sync"]').innerText.includes("Atualizando"));
    let txt = await syncText(page);
    assert.match(txt, /Nubank/);
    assert.match(txt, /Ainda não sincronizou/, "detalhe do servidor como veio");
    assert.match(txt, /Pode continuar/);
    assert.equal(await page.$eval('[data-role="of-sync"]', (e) => e.getAttribute("aria-live")), "polite");

    await page.clock.runFor(5000);
    await page.waitForFunction(() => document.querySelector('[data-role="of-sync"]').innerText.includes("Atualizado"));
    txt = await syncText(page);
    assert.doesNotMatch(txt, /Pode continuar|Atualizando/);

    const depois = await assentado(calls);
    await page.clock.runFor(60000);
    assert.equal(await assentado(calls), depois, `o repoll continuou: ${depois} → ${calls.of} pedidos`);
    await page.close();
  });

  test(`${tag} passo 2: sair do passo para o repoll; e ele tem teto`, async () => {
    const { page, calls } = await abrir(vp, { step: 2, snapshot: () => json({ ok: true, connections: [UPDATING] }) });
    await page.waitForFunction(() => document.querySelector('[data-role="of-sync"]').innerText.includes("Atualizando"));
    await page.clock.runFor(10000);
    assert.ok(await assentado(calls) >= 2, `deveria repollar em updating (pedidos: ${calls.of})`);

    await page.click('.onb-step[data-step="2"] [data-action="next"]');
    await page.waitForSelector('.onb-step[data-step="3"]:not([hidden])');
    const saiu = await assentado(calls);
    await page.clock.runFor(60000);
    assert.equal(await assentado(calls), saiu, `o repoll seguiu fora do passo 2: ${saiu} → ${calls.of}`);
    await page.close();

    const { page: p2, calls: c2 } = await abrir(vp, { step: 2, snapshot: () => json({ ok: true, connections: [UPDATING] }) });
    await p2.waitForFunction(() => document.querySelector('[data-role="of-sync"]').innerText.includes("Atualizando"));
    await p2.clock.runFor(10 * 60 * 1000);
    const total = await assentado(c2);
    assert.ok(total >= 10 && total <= 1 + 24, `teto: ${total} pedidos em 10 min`);
    await p2.close();
  });

  test(`${tag} passo 2: resposta velha que chega depois da nova não repinta "Atualizando…"`, async () => {
    // 2º pedido lento (volta em 1 s de relógio real, "updating"); o 3º volta
    // na hora com "updated". Sem a guarda de sequência o 2º repinta por cima.
    const { page, calls } = await abrir(vp, { step: 2, snapshot: (n) => {
      if (n === 2) return new Promise((r) => setTimeout(() => r(json({ ok: true, connections: [UPDATING] })), 1000));
      return json({ ok: true, connections: [n === 1 ? UPDATING : UPDATED] });
    } });
    await page.waitForFunction(() => document.querySelector('[data-role="of-sync"]').innerText.includes("Atualizando"));
    await page.clock.runFor(10000);
    await assentado(calls);
    await new Promise((r) => setTimeout(r, 1300));
    assert.match(await syncText(page), /Atualizado/);
    assert.doesNotMatch(await syncText(page), /Atualizando/);
    await page.close();
  });

  test(`${tag} passo 2: needs_user_action mostra o detalhe do servidor e "Resolver em Ajustes"`, async () => {
    const { page } = await abrir(vp, { step: 2, snapshot: () => json({ ok: true, connections: [{
      institution_name: "Caixa", ui: { state: "needs_user_action", label: "Ação necessária", detail: "Autorize o acesso no app do banco" } }] }) });
    await page.waitForFunction(() => document.querySelector('[data-role="of-sync"]').innerText.includes("Caixa"));
    assert.match(await syncText(page), /Ação necessária[\s\S]*Autorize o acesso no app do banco/);
    const href = await page.$eval('[data-role="of-sync"] a', (a) => a.getAttribute("href"));
    assert.equal(href, "/settings?view=open-finance&onb=1");
    await page.close();
  });

  test(`${tag} passo 2: snapshot com 402 deixa o bloco como antes`, async () => {
    const { page, calls } = await abrir(vp, { step: 2, snapshot: () => json({ detail: "x" }, 402) });
    await page.waitForFunction(() => document.querySelector("#onb-balance"));
    await page.clock.runFor(30000);
    assert.equal(await page.$eval('[data-role="of-sync"]', (e) => e.children.length), 0);
    assert.equal(await assentado(calls), 1, "falha no snapshot não repolla");
    assert.ok(await page.isVisible('a[href="/settings?view=open-finance&onb=1"]'), "o convite continua");
    await page.close();
  });

  test(`${tag} passo 5: 500 ao gravar não mostra "Tudo pronto!"; Tentar de novo com 200 mostra e o Finish vai ao /home`, async () => {
    let falha = true;
    const { page, calls } = await abrir(vp, { step: 4, saveStatus: (b) => (b.completed && falha ? 500 : 200) });
    await page.click('.onb-step[data-step="4"] [data-action="skip"]');
    await page.waitForSelector('[data-role="done-fail"]:not([hidden])');
    assert.equal(await page.isVisible('[data-role="done-ok"]'), false, "Tudo pronto apareceu sem o 200");
    assert.equal(await page.isVisible("text=Tudo pronto"), false);
    assert.match(await page.$eval('[data-role="error"]', (e) => e.textContent), /Não consegui salvar/);
    assert.ok(calls.posts.some((b) => b.step === 5 && b.completed === true), "entrar no passo 5 tem de gravar a conclusão");

    falha = false;
    await page.click('[data-action="retry-complete"]');
    await page.waitForSelector('[data-role="done-ok"]:not([hidden])');
    assert.ok(await page.isVisible("text=Tudo pronto"));
    assert.equal(await page.$eval('[data-role="error"]', (e) => e.textContent), "");

    await Promise.all([page.waitForURL("**/home"), page.click('[data-action="finish"]')]);
    assert.deepEqual(calls.conv, CONVERSAO, "o Finish não pode repetir a conversão");
    await page.close();
  });

  test(`${tag} revisita ao passo 5 com a conclusão já gravada não dispara a conversão de novo`, async () => {
    // Controle negativo: `ok` em vez de `ok.stamped` no completeOnEnter dispara aqui.
    const { page, calls } = await abrir(vp, { step: 5, concluido: true });
    await page.waitForSelector('[data-role="done-ok"]:not([hidden])');
    await Promise.all([page.waitForURL("**/home"), page.click('[data-action="finish"]')]);
    assert.deepEqual(calls.conv, []);
    await page.close();
  });

  test(`${tag} Enter num CTA focado com escrita em voo não avança nem manda POST`, async () => {
    // O CSS só segura o mouse. Negativo: sem a guarda de inFlight no onClick, avança.
    let solta;
    const { page, calls } = await abrir(vp, { step: 2,
      saveStatus: (b) => (b.completed ? new Promise((r) => { solta = () => r(500); }) : 200) });
    await page.click('[data-action="skip-all"]');
    while (!solta) await new Promise((r) => setTimeout(r, 20));
    const antes = calls.posts.length;
    for (const sel of ['.onb-step[data-step="2"] [data-action="next"]', '.onb-step[data-step="2"] [data-action="skip"]']) {
      await page.focus(sel);
      await page.keyboard.press("Enter");
      await page.keyboard.press("Space");
    }
    await new Promise((r) => setTimeout(r, 300));
    assert.ok(await page.isVisible('.onb-step[data-step="2"]'), "o passo avançou com a escrita em voo");
    assert.equal(calls.posts.length, antes, JSON.stringify(calls.posts));
    solta();
    await page.waitForFunction(() => document.querySelector('[data-role="error"]').textContent.includes("Não consegui salvar"));
    await page.focus('.onb-step[data-step="2"] [data-action="next"]');
    await page.keyboard.press("Enter");
    await page.waitForSelector('.onb-step[data-step="3"]:not([hidden])'); // positivo: sem voo, o teclado vale
    await page.close();
  });

  test(`${tag} passo 2: poll com o mesmo estado não toca a lista aria-live; mudança toca`, async () => {
    // Recriar os <li> iguais faz o leitor de tela reanunciar "Atualizando…" a
    // cada 5 s. Controle negativo: tirar a comparação com `state.ofRendered`
    // no renderOfSync faz as mutações dos 3 polls iguais passarem de 0.
    let conexao = UPDATING;
    const { page, calls } = await abrir(vp, { step: 2, snapshot: () => json({ ok: true, connections: [conexao] }) });
    await page.waitForFunction(() => document.querySelector('[data-role="of-sync"]').innerText.includes("Atualizando"));
    await page.evaluate(() => {
      window.__mut = 0;
      new MutationObserver((ms) => { window.__mut += ms.length; })
        .observe(document.querySelector('[data-role="of-sync"]'), { childList: true, subtree: true, characterData: true });
    });
    await page.clock.runFor(15000);
    assert.ok(await assentado(calls) >= 3, `deveria repollar 3 vezes (pedidos: ${calls.of})`);
    assert.equal(await page.evaluate(() => window.__mut), 0, "poll com o mesmo estado mexeu na região aria-live");

    conexao = UPDATED;
    await page.clock.runFor(5000);
    await page.waitForFunction(() => document.querySelector('[data-role="of-sync"]').innerText.includes("Atualizado"));
    assert.ok(await page.evaluate(() => window.__mut) > 0, "a mudança de estado tem de redesenhar");
    await page.close();
  });

  test(`${tag} duplo clique em "Pular tudo" e em "Tentar de novo" manda UM completed cada`, async () => {
    // Pedido segurado 300 ms: o 2º clique cai com o 1º em voo. No "Tentar de
    // novo" o botão some no 1º clique e o 2º cai no que o layout pôs sob o
    // cursor; em 1280 isso ainda manda um 2º completed sem o withBusy do retry.
    // Controle negativo: tirar o withBusy do skipAll (ou do retryComplete) dá 2.
    const lento = (status) => new Promise((r) => setTimeout(() => r(status), 300));
    const { page, calls } = await abrir(vp, { step: 2, saveStatus: () => lento(200) });
    await Promise.all([page.waitForURL("**/home"), page.dblclick('[data-action="skip-all"]')]);
    assert.equal(calls.posts.filter((b) => b.completed).length, 1, JSON.stringify(calls.posts));
    await page.close();

    let falha = true;
    const { page: p2, calls: c2 } = await abrir(vp, { step: 4, saveStatus: (b) => lento(b.completed && falha ? 500 : 200) });
    await p2.click('.onb-step[data-step="4"] [data-action="skip"]');
    await p2.waitForSelector('[data-role="done-fail"]:not([hidden])');
    falha = false;
    const antes = c2.posts.filter((b) => b.completed).length;
    await p2.dblclick('[data-action="retry-complete"]');
    await new Promise((r) => setTimeout(r, 800));
    assert.equal(c2.posts.filter((b) => b.completed).length - antes, 1, JSON.stringify(c2.posts));
    // Em 1280 o 2º clique cai no "Pular tudo" e vai ao app sem o Finish. Seja qual
    // for a tela final, UMA conversão. Negativo: conversão só no finish() → 0.
    assert.deepEqual(c2.conv, CONVERSAO, `tela final: ${p2.url()}`);
    if (!/\/home$/.test(p2.url())) {
      await Promise.all([p2.waitForURL("**/home"), p2.click('[data-action="finish"]')]);
      assert.deepEqual(c2.conv, CONVERSAO, "o Finish repetiu a conversão");
    }
    await p2.close();
  });

  test(`${tag} "Pular tudo" durante o "Salvando…" do passo 5 espera a conclusão e vai ao /home com UM completed`, async () => {
    // Controle negativo: tirar o busyBegin/busyEnd do completeOnEnter deixa o
    // skipAll mandar o próprio completed por cima do auto-save → 2.
    const lento = (status) => new Promise((r) => setTimeout(() => r(status), 600));
    const { page, calls } = await abrir(vp, { step: 4, saveStatus: (b) => (b.completed ? lento(200) : 200) });
    await page.click('.onb-step[data-step="4"] [data-action="skip"]');
    await page.waitForSelector('[data-role="done-saving"]:not([hidden])');
    await Promise.all([page.waitForURL("**/home"), page.click('[data-action="skip-all"]')]);
    assert.equal(calls.posts.filter((b) => b.completed).length, 1, JSON.stringify(calls.posts));
    await page.close();
  });

  test(`${tag} com escrita em voo o wizard fica ocupado e os CTAs apagados; depois voltam`, async () => {
    // Controle negativo: sem o aria-busy no .onb-card (busyBegin) o "Continuar"
    // segue com opacidade 1 e clicável enquanto o clique seria engolido.
    let solta;
    const { page } = await abrir(vp, { step: 2,
      saveStatus: (b) => (b.completed ? new Promise((r) => { solta = () => r(500); }) : 200) });
    const cta = '.onb-step[data-step="2"] [data-action="next"]';
    const estilo = () => page.$eval(cta, (e) => {
      const s = getComputedStyle(e);
      return { op: s.opacity, pe: s.pointerEvents, busy: document.querySelector(".onb-card").getAttribute("aria-busy") };
    });
    assert.deepEqual(await estilo(), { op: "1", pe: "auto", busy: null });
    await page.click('[data-action="skip-all"]');
    while (!solta) await new Promise((r) => setTimeout(r, 20)); // o POST chegou à rota e está segurado
    assert.deepEqual(await estilo(), { op: "0.6", pe: "none", busy: "true" });
    solta();
    await page.waitForFunction(() => document.querySelector('[data-role="error"]').textContent.includes("Não consegui salvar"));
    assert.deepEqual(await estilo(), { op: "1", pe: "auto", busy: null }, "falha tem de desfazer o ocupado");
    await page.close();
  });

  test(`${tag} "Pular tudo" com 500 fica e avisa; com 200 vai ao /home — no passo 2 sem conversão`, async () => {
    let falha = true;
    const { page, calls } = await abrir(vp, { step: 2, saveStatus: (b) => (b.completed && falha ? 500 : 200) });
    await page.click('[data-action="skip-all"]');
    await page.waitForFunction(() => document.querySelector('[data-role="error"]').textContent.includes("Não consegui salvar"));
    assert.match(page.url(), /comecar\.html$/);

    falha = false;
    await Promise.all([page.waitForURL("**/home"), page.click('[data-action="skip-all"]')]);
    assert.deepEqual(calls.conv, [], "pular dos passos 1–4 não é ativação");
    await page.close();
  });

  test(`${tag} 500 na conclusão do passo 5 → "Pular tudo" grava, converte UMA vez e vai ao /home`, async () => {
    // Controle negativo: sem o concluiu() no skipAll, a conversão fica vazia.
    let falha = true;
    const { page, calls } = await abrir(vp, { step: 4, saveStatus: (b) => (b.completed && falha ? 500 : 200) });
    await page.click('.onb-step[data-step="4"] [data-action="skip"]');
    await page.waitForSelector('[data-role="done-fail"]:not([hidden])');
    falha = false;
    await Promise.all([page.waitForURL("**/home"), page.click('[data-action="skip-all"]')]);
    assert.deepEqual(calls.conv, CONVERSAO);
    await page.close();
  });
}
