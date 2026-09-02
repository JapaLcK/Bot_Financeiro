/**
 * /precos não oferece mais o plano Grátis.
 *
 * Assinar virou obrigatório pra entrar pela web, então o card do Grátis e os
 * dois CTAs `[data-free-cta]` saíram da página, junto com o `selectFree` que
 * batia em POST /billing/select-free. A COLUNA Grátis da tabela comparativa
 * FICA (é informação: o que você perde descendo o degrau) — só o CTA dela some.
 *
 * O grupo tem os dois controles do CLAUDE.md §3:
 *   · asserção do conserto — nenhum CTA de Grátis, nenhum card "Grátis", e
 *     nenhuma requisição pro /billing/select-free em nenhum dos dois estados
 *     (deslogado e needs_plan_selection);
 *   · copy do gate — as 6 células de {deslogado, logado sem gate, logado com
 *     gate} × {com ?escolha=1, sem marcador}: quem decide é o
 *     needs_plan_selection do /auth/me, não a URL (o marcador se perde num
 *     clique no "Planos" do próprio nav);
 *   · app iOS — mais 2 células com a UA PigBankApp: lá o gate não existe
 *     (routes/shared.py isenta o app), então o mandato "Escolha um plano pra
 *     continuar" seria falso pra quem não está travado;
 *   · controle POSITIVO — "Assinar Plus" ainda dispara EXATAMENTE 1
 *     POST /billing/create-checkout. Sem ele o grupo passaria numa página com
 *     todos os botões quebrados, que é pior que o bug.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

// UA do WebView do app (o `/PigBankApp/.test(navigator.userAgent)` de
// frontend/static/auth-refresh.js casa a SUBSTRING PigBankApp) — é o
// mecanismo real que liga window.PB_IN_APP, e não injeção da variável.
const APP_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
  + "AppleWebKit/605.1.15 Safari/604.1 PigBankApp/1.0";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Abre a /precos com o backend mockado. `me` vira o corpo do /auth/me
 * (`null` = 401, o visitante deslogado). Devolve a página e os contadores de
 * requisição por rota — a contagem é por INTERCEPTAÇÃO, não por efeito visível.
 */
async function abrirPrecos({ me = null, query = "", app = false } = {}) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 },
                                       ...(app ? { userAgent: APP_UA } : {}) });
  const chamadas = { selectFree: 0, checkout: 0 };
  const corposCheckout = [];

  await page.route("**/auth/me", (route) => (me
    ? route.fulfill({ contentType: "application/json", body: JSON.stringify(me) })
    : route.fulfill({ status: 401, contentType: "application/json", body: "{}" })));

  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, pro_available: true }),
  }));

  await page.route("**/billing/subscription", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ active: false }),
  }));

  await page.route("**/billing/select-free", (route) => {
    chamadas.selectFree += 1;
    // 410 é o que o backend passou a devolver; o corpo segue o formato que o
    // front lê nas outras rotas de billing (detail.message).
    return route.fulfill({
      status: 410, contentType: "application/json",
      body: JSON.stringify({ detail: { error: "free_plan_discontinued", message: "indisponível" } }),
    });
  });

  await page.route("**/billing/create-checkout", (route) => {
    chamadas.checkout += 1;
    corposCheckout.push(JSON.parse(route.request().postData() || "{}"));
    // checkout_url pra uma página do próprio servidor: o startCheckout navega
    // no sucesso, e mandá-lo pra lugar nenhum deixaria o teste cego pro ramo
    // "Resposta inesperada do servidor".
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ checkout_url: `${ORIGIN}/precos.html?stripe=1` }),
    });
  });

  await page.goto(`${ORIGIN}/precos.html${query}`);
  await page.waitForSelector("#plans-v2 .plan");
  // O IIFE loadPlansState é assíncrono (plans-config + subscription) e o
  // applyOnboardingChoice que ele chamava era quem reescrevia os CTAs do
  // Grátis. Sem esperar, "não existe CTA" passaria antes de o JS rodar — e o
  // teste ficaria verde por corrida, não pelo conserto.
  await page.waitForTimeout(600);
  return { page, chamadas, corposCheckout };
}

// ── asserção do conserto ────────────────────────────────────────────────────

for (const [rotulo, ctx] of [
  ["deslogado", {}],
  ["needs_plan_selection", { me: { user_id: 42, needs_plan_selection: true }, query: "?escolha=1" }],
]) {
  test(`${rotulo}: nenhum CTA de Grátis e nenhum card Grátis na escada`, async () => {
    const { page, chamadas } = await abrirPrecos(ctx);

    assert.equal(await page.$$eval("[data-free-cta]", (e) => e.length), 0,
      "sobrou [data-free-cta] na página");

    const nomes = await page.$$eval("#plans-v2 .plan h3", (e) => e.map((h) => h.textContent.trim()));
    assert.deepEqual(nomes, ["Essencial", "Plus", "Pro", "Premium"]);

    // Qualquer clicável com a copy do Grátis, em QUALQUER lugar da página (a
    // classe, não o caso: o card era um, o rodapé da tabela era outro).
    const clicaveis = await page.$$eval("a, button", (els) =>
      els.map((e) => e.textContent.trim()).filter(Boolean));
    for (const proibido of ["Criar conta grátis", "Continuar com o plano Grátis", "Seu cadastro está pronto"]) {
      assert.ok(!clicaveis.includes(proibido),
        `clicável com a copy "${proibido}" ainda existe: ${JSON.stringify(clicaveis)}`);
    }

    assert.equal(chamadas.selectFree, 0, "alguém ainda bate no /billing/select-free");
    await page.close();
  });
}

test("a coluna Grátis da tabela comparativa fica: 6 colunas, tfoot sem CTA", async () => {
  const { page } = await abrirPrecos();
  // O cabeçalho continua com a coluna do Grátis (é informação, não oferta).
  const cabecalho = await page.$$eval(".cmp-table thead tr th", (e) => e.map((t) => t.textContent.trim()));
  assert.ok(cabecalho.some((t) => t.includes("Grátis")), `thead sem Grátis: ${JSON.stringify(cabecalho)}`);
  // 6 colunas no tfoot (1 rótulo + 5 planos), e a do Grátis vazia.
  const tfoot = await page.$$eval(".cmp-table tfoot tr td", (e) => e.map((t) => t.textContent.trim()));
  assert.equal(tfoot.length, 6, `tfoot com ${tfoot.length} células: ${JSON.stringify(tfoot)}`);
  assert.equal(tfoot[1], "", `a célula do Grátis não está vazia: ${JSON.stringify(tfoot)}`);
  assert.deepEqual(tfoot.slice(2), ["Assinar Essencial", "Assinar Plus", "Assinar Pro", "Em breve"]);
  await page.close();
});

// ── copy do subtítulo: as 6 células de estado × marcador ────────────────────
// A copy do gate é decidida pelo needs_plan_selection do /auth/me, não pelo
// ?escolha=1: o marcador se perde num clique no "Planos" do próprio nav, no
// gate_pro_page (routes/shared.py) e nos redirects de /conta. Então as 6
// células = 3 estados × {com marcador, sem marcador}, e a coluna do marcador
// não muda NENHUMA linha — é isso que o teste fixa.
//
// A célula que discrimina é "logado com gate SEM marcador": com a decisão pela
// URL ela mostrava a copy de visitante a quem está travado no gate. As duas
// "sem gate + COM marcador" são o outro lado: marcador velho (bookmark, link
// compartilhado) não pode inventar gate pra quem já pagou nem pra visitante.
const COPY_PADRAO = /^15 dias grátis/;
const COPY_GATE = /Escolha um plano pra continuar/;

for (const [estado, me] of [
  ["deslogado", null],
  ["logado sem gate", { user_id: 42, needs_plan_selection: false }],
  ["logado com gate", { user_id: 42, needs_plan_selection: true }],
]) {
  for (const query of ["?escolha=1", ""]) {
    const esperado = me && me.needs_plan_selection ? COPY_GATE : COPY_PADRAO;
    const rotulo = query ? "com marcador" : "sem marcador";
    test(`copy do subtítulo: ${estado}, ${rotulo}`, async () => {
      const { page } = await abrirPrecos({ me, query });
      const sub = await page.textContent("#precos-sub");
      assert.match(sub, esperado,
        `${estado} ${rotulo} devia bater ${esperado} e leu: "${sub}"`);
      await page.close();
    });
  }
}

// ── app iOS: o gate não se aplica, então a copy de mandato seria falsa ───────
// routes/shared.py isenta o _is_pigbank_app do gate, e a /precos é alcançável de
// dentro do app pelo "Ver planos" .pb-keep-in-app do paywall (`#upg-cta` em
// frontend/dashboard.html)
// — dizer "Escolha um plano pra continuar" a quem não está travado é errado.
// 2 células, não 6: as 4 de {deslogado, logado sem gate} × marcador saem da
// função pelo mesmo `!me.needs_plan_selection`, com e sem app — a guarda não
// alcança nenhuma delas. A coluna do marcador fica porque é o mesmo invariante
// das células web: a URL não decide nada.
for (const query of ["?escolha=1", ""]) {
  const rotulo = query ? "com marcador" : "sem marcador";
  test(`copy do subtítulo: app iOS, logado com gate, ${rotulo}`, async () => {
    const { page } = await abrirPrecos({
      me: { user_id: 42, needs_plan_selection: true }, query, app: true,
    });
    assert.equal(await page.evaluate(() => window.PB_IN_APP === true), true,
      "a UA do app não ligou window.PB_IN_APP — a célula não mediria o app");
    const sub = await page.textContent("#precos-sub");
    assert.match(sub, COPY_PADRAO,
      `app iOS ${rotulo} devia ler a copy padrão e leu: "${sub}"`);
    await page.close();
  });
}

// ── controle POSITIVO: o caminho legítimo continua funcionando ───────────────

test("controle positivo: 'Assinar Plus' dispara exatamente 1 POST /billing/create-checkout", async () => {
  const { page, chamadas, corposCheckout } = await abrirPrecos({
    me: { user_id: 42, needs_plan_selection: true }, query: "?escolha=1",
  });
  await Promise.all([
    page.waitForURL(/stripe=1/, { timeout: 5000 }),
    page.click('#plans-v2 [data-plan-btn="plus"]'),
  ]);
  assert.equal(chamadas.checkout, 1, `foram ${chamadas.checkout} POSTs de checkout`);
  assert.deepEqual(corposCheckout[0], { interval: "monthly", plan: "plus" });
  await page.close();
});

test("controle positivo: os 6 CTAs pagos continuam habilitados (card e tabela)", async () => {
  // Clicar nos seis não dá: o primeiro clique bem-sucedido NAVEGA pro Stripe.
  // Então a prova de "não quebrei os outros" é o estado do DOM — o clique de
  // verdade é o teste acima. Cada plano pago tem DOIS botões (card + rodapé da
  // tabela) e o do rodapé já tinha ficado clicável rumo ao erro uma vez.
  const { page } = await abrirPrecos({ me: { user_id: 42, needs_plan_selection: true } });
  const estado = await page.$$eval("[data-plan-btn]", (els) => els.map((e) => ({
    plano: e.dataset.planBtn,
    onde: e.closest("#plans-v2") ? "card" : "tabela",
    desabilitado: e.disabled === true,
    texto: e.textContent.trim(),
  })));
  assert.deepEqual(estado, [
    { plano: "essencial", onde: "card",   desabilitado: false, texto: "Assinar Essencial" },
    { plano: "plus",      onde: "card",   desabilitado: false, texto: "Assinar Plus" },
    { plano: "pro",       onde: "card",   desabilitado: false, texto: "Assinar Pro" },
    { plano: "essencial", onde: "tabela", desabilitado: false, texto: "Assinar Essencial" },
    { plano: "plus",      onde: "tabela", desabilitado: false, texto: "Assinar Plus" },
    { plano: "pro",       onde: "tabela", desabilitado: false, texto: "Assinar Pro" },
  ]);
  await page.close();
});
