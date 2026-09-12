/**
 * O Pix anual na /precos: o CTA, o modal do QR, o poll e o §13.6.
 *
 * Backend inteiro por `page.route` — o `POST /billing/pix/checkout` e o
 * `GET /billing/pix/{token}` estão sendo escritos em outro branch, e este PR é
 * seguro de mergear antes deles justamente porque sem `pix_annual_available` a
 * página é a de hoje. Contagem por INTERCEPTAÇÃO, não por efeito visível.
 *
 * Os dois controles do CLAUDE.md §3:
 *   · negativo — PT1, PT2, PT4 e PT7 têm mutação nomeada na tabela do plano, e a
 *     do PT2 é injetada num caso que estava VERDE;
 *   · positivo — PT2b (sem `expires_at` o poll RODA; sem ele, um poll que nunca
 *     começa passaria no PT2), PT2d (com `expires_at` longo o poll CONTINUA),
 *     PT5 (o caminho do cartão continua inteiro) e os dois primeiros casos do
 *     PT8 (copiar de verdade continua dizendo "Copiado").
 *
 * O que o grupo do §13.6 NÃO media, e agora mede: com `pixApagarQr` virando
 * `{ return; }` os três casos do PT7 saíam 12/12 VERDES — o overlay, o
 * `replaceChildren` e a navegação removiam o payload por cima do wipe, e a
 * asserção de DOM nunca via o `value` de um `<input>`. Quem discrimina o wipe é
 * o `retido`/`valores` do `vestigios` e o PT10 (modal aberto, nada removendo por
 * cima). Os casos "fechando" e "pagando" continuam medindo o §13.6 inteiro
 * (nada em storage, nada na URL), não o wipe.
 *
 * O que estes testes NÃO alcançam, e precisa de outro método (aparelho, pós-deploy):
 *   · a pausa/retomada por `document.hidden` — o Playwright não emula visibilidade
 *     de forma confiável;
 *   · o padding de área segura — `env(safe-area-inset-*)` vale 0 no headless;
 *   · a leitura do QR por câmera e o pagamento de verdade.
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

// O copia-e-cola de verdade tem esta cara. É a string que o PT7 procura em todo
// canto depois que a cobrança sai de cena.
const PAYLOAD = "00020126580014BR.GOV.BCB.PIX0136pigbank-teste-qr-payload-52040000AAAA";
// data: URI de 1x1 — o QR real é um `data:image/svg+xml` gerado pelo servidor
// (qr_svg_data_url, core/services/pix_brcode.py). O que se mede aqui é que o
// atributo SOME, não o desenho.
const QR_IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
// O documento do pagador. Dígito verificador NÃO é conferido na tela (quem
// confere o mod-11 é o servidor, uma cópia só, §0.7; e o Asaas ainda pode
// recusar depois), então o que importa aqui é a FORMA: 11 e 14 dígitos.
// São strings improváveis de aparecer por acaso — o PT12d procura por elas no
// storage, no DOM, no console e no corpo de TODA requisição da página.
const CPF = "11122233344";
const CNPJ = "11222333000181";

/**
 * Abre a /precos com o backend mockado e devolve a página + os contadores.
 *
 * `pix` controla o que o `POST /billing/pix/checkout` responde; `status` é a
 * função que decide o corpo de cada poll (recebe o número da chamada) e, quando
 * devolve `null`, a requisição é ABORTADA — é assim que se mede rede fora.
 *
 * `pix.expiresAt` aceita função: aí o deadline é contado a partir da RESPOSTA do
 * checkout, e não de antes de o navegador abrir. Sem isso, os 1–2 s de `goto` +
 * `waitForTimeout` comiam parte da janela que o teste quer medir.
 */
async function abrirPrecos({
  sub = { active: false },
  // O DESLOGADO não é `{active:false}` com 200: o /billing/subscription responde
  // não-ok e o `loadSubscription` guarda `subState = null`. É o caso que separa
  // "ainda não sei" de "sei que não tem assinatura" (PT19e).
  subStatus = 200,
  subRoute = null,
  plansConfig = { essencial_available: true, plus_available: true,
                  pro_available: true, pix_annual_available: true },
  pix = {},
  status = () => ({ status: "pending" }),
  viewport = { width: 1280, height: 900 },
  relogio = false,
  atrasos = {},
  initScript = null,
} = {}) {
  const page = await browser.newPage({ viewport });
  if (initScript) await page.addInitScript(initScript);
  // Relógio falso: o teto do CLIENTE é de 15 minutos, e a única forma de medir
  // que ele existe sem esperar 15 minutos é adiantar o relógio da página. Com
  // ele instalado o setTimeout do poll só anda por `fastForward` — nenhuma
  // requisição sai em tempo real.
  if (relogio) await page.clock.install();
  const chamadas = { checkout: 0, pixCheckout: 0, poll: 0, changePlan: 0 };
  const corposPix = [];

  await page.route("**/auth/me", (r) => r.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ user_id: 42, needs_plan_selection: true }),
  }));
  await page.route("**/billing/plans-config", (r) => r.fulfill({
    contentType: "application/json", body: JSON.stringify(plansConfig),
  }));
  await page.route("**/billing/subscription", subRoute || ((r) => r.fulfill({
    status: subStatus, contentType: "application/json", body: JSON.stringify(sub),
  })));
  await page.route("**/billing/change-plan", (r) => {
    chamadas.changePlan += 1;
    return r.fulfill({ contentType: "application/json", body: "{}" });
  });
  await page.route("**/billing/create-checkout", (r) => {
    chamadas.checkout += 1;
    return r.fulfill({ contentType: "application/json",
                       body: JSON.stringify({ checkout_url: `${ORIGIN}/precos.html?stripe=1` }) });
  });

  await page.route("**/billing/pix/checkout", (r) => {
    chamadas.pixCheckout += 1;
    corposPix.push(JSON.parse(r.request().postData() || "{}"));
    const { httpStatus = 200, corpo } = pix;
    return r.fulfill({
      status: httpStatus, contentType: "application/json",
      body: JSON.stringify(corpo || {
        public_token: "tok_abc123", qr_payload: PAYLOAD, qr_image: QR_IMG,
        amount_cents: 19900, credit_cents: 0, starts_at: null, plan: "plus",
        // O corpo traz DE PROPÓSITO o id do provedor: o PT6 prova que ele não
        // vai para a URL de sucesso (§13.6).
        asaas_payment_id: "pay_9999",
        ...(pix.expiresAt === undefined ? {} : {
          expires_at: typeof pix.expiresAt === "function" ? pix.expiresAt() : pix.expiresAt,
        }),
      }),
    });
  });
  // Depois do `checkout` na ordem de registro: o Playwright usa a ÚLTIMA rota
  // que casa, então o padrão mais amplo tem de vir por último para não engolir
  // o `/billing/pix/checkout`. Este `if` é o que devolve o checkout para ele.
  await page.route("**/billing/pix/*", async (r) => {
    if (r.request().url().endsWith("/checkout")) return r.fallback();
    chamadas.poll += 1;
    const corpo = await status(chamadas.poll);
    if (!corpo) return r.abort();          // rede fora: o fetch REJEITA
    return r.fulfill({ contentType: "application/json", body: JSON.stringify(corpo) });
  });
  // A volta do sucesso: sem isto o `/home?upgrade=success` daria 404 do
  // http.server e o PT7 leria o corpo de uma página de erro.
  await page.route("**/home*", (r) => r.fulfill({
    contentType: "text/html", body: "<html><body>home</body></html>",
  }));

  // Atraso artificial por pedaço de URL, e por ÚLTIMO de propósito: o Playwright
  // usa a rota registrada mais tarde, e o `fallback()` devolve o pedido para a
  // registrada antes (ou para a rede). É o que permite medir corrida de carga —
  // script que chega depois das requisições, /billing/subscription lento.
  if (Object.keys(atrasos).length) {
    await page.route("**/*", async (r) => {
      const k = Object.keys(atrasos).find((x) => r.request().url().includes(x));
      if (k) await new Promise((ok) => setTimeout(ok, atrasos[k]));
      return r.fallback();
    });
  }

  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForSelector("#plans-v2 .plan");
  await page.waitForTimeout(600);     // loadPlansState = 2 awaits de rede
  return { page, chamadas, corposPix };
}

const contarCtas = (page) => page.$$eval("[data-pix-cta]", (e) => e.length);

/**
 * Estado 1 do modal: o CPF/CNPJ. É AQUI que o POST sai — o clique no CTA só abre
 * a caixa, porque o Asaas exige o documento do pagador para criar a cobrança.
 */
async function enviarDoc(page, valor = CPF) {
  await page.fill(".pix-doc", valor);
  await page.click('.pix-form button[type="submit"]');
}

/** Abre o modal do QR: CTA do Plus no anual, documento, submit. */
async function abrirQr(ctx = {}) {
  const r = await abrirPrecos(ctx);
  await r.page.click("#cycle-annual");
  await r.page.click('[data-pix-cta="plus"]');
  await r.page.waitForSelector(".pix-doc");
  await enviarDoc(r.page, ctx.doc);
  await r.page.waitForTimeout(300);
  return r;
}

// ── PT1: o CTA só existe no ciclo anual ─────────────────────────────────────
// O bug barato é aparecer e não sumir na volta, então o mensal é medido DUAS
// vezes: na abertura e depois de voltar do anual.
test("PT1: nenhum CTA de Pix no mensal, 3 no anual, e some na volta", async () => {
  const { page } = await abrirPrecos();
  assert.equal(await contarCtas(page), 0, "o ciclo mensal nasceu com CTA de Pix");

  await page.click("#cycle-annual");
  assert.equal(await contarCtas(page), 3, "o ciclo anual devia ter 3 CTAs de Pix");
  const planos = await page.$$eval("[data-pix-cta]", (e) => e.map((b) => b.dataset.pixCta));
  assert.deepEqual(planos, ["essencial", "plus", "pro"]);
  // Nos cards, nunca no tfoot: o precos_sem_plano_gratis.test.mjs assevera as 4
  // células daquele rodapé, e uma célula nova o deixa vermelho.
  assert.equal(await page.$$eval("#plans-v2 [data-pix-cta]", (e) => e.length), 3);
  assert.equal(await page.$$eval(".cmp-table [data-pix-cta]", (e) => e.length), 0,
    "CTA de Pix vazou para a tabela comparativa");

  await page.click("#cycle-annual");
  assert.equal(await contarCtas(page), 0, "o CTA de Pix não sumiu na volta pro mensal");
  await page.close();
});

// A OUTRA metade do que torna este PR seguro de mergear antes do backend.
//
// Os dois últimos casos existem para prender a ESTRICTUDE do `!== true`: com os
// dois primeiros, trocar a guarda por `!pixCfg.pix_annual_available` deixava o
// arquivo inteiro verde. Portão de venda não abre com valor truthy qualquer —
// `1` e `"true"` são o que um backend novo devolve quando alguém troca o tipo da
// flag sem querer, e aí a venda de Pix abriria sozinha em produção.
test("PT1b: sem pix_annual_available === true, nenhum botão nasce em ciclo nenhum", async () => {
  const base = { essencial_available: true, plus_available: true, pro_available: true };
  for (const cfg of [
    base,
    { ...base, pix_annual_available: false },
    { ...base, pix_annual_available: 1 },
    { ...base, pix_annual_available: "true" },
  ]) {
    const { page } = await abrirPrecos({ plansConfig: cfg });
    assert.equal(await contarCtas(page), 0);
    await page.click("#cycle-annual");
    assert.equal(await contarCtas(page), 0,
      `com ${JSON.stringify(cfg)} o anual criou CTA de Pix`);
    await page.close();
  }
});

// ── PT2: o poll tem TETO ────────────────────────────────────────────────────
test("PT2: o poll para no deadline do expires_at e o código some da tela", async () => {
  const { page, chamadas } = await abrirQr({
    pix: { expiresAt: () => new Date(Date.now() + 6000).toISOString() },
  });
  await page.waitForTimeout(9000);
  const noTeto = chamadas.poll;
  assert.ok(noTeto >= 2, `o poll rodou só ${noTeto} vez(es) dentro dos 6 s`);
  await page.waitForTimeout(6000);
  assert.equal(chamadas.poll, noTeto,
    `o poll continuou depois do deadline: ${noTeto} -> ${chamadas.poll}`);

  assert.match(await page.textContent(".pix-box"), /expirou/i);
  assert.equal(await page.$$eval(".pix-code", (e) => e.length), 0,
    "o <input> com o copia-e-cola continua na tela depois de expirar");
  // Pelo TEXTO, não por `$(".pix-box button")`: o "Fechar" nunca sai da caixa, e
  // a asserção antiga passaria com o "Gerar novo código" ausente.
  const botoes = await page.$$eval(".pix-box button", (e) => e.map((b) => b.textContent.trim()));
  assert.ok(botoes.includes("Gerar novo código"),
    `sumiu o botão de gerar novo código: ${JSON.stringify(botoes)}`);

  // Voltar para a aba DEPOIS de expirar não pode ressuscitar o poll nem remontar
  // o corpo por cima de quem está lendo: o listener de `visibilitychange` sai
  // junto com o poll. O evento é despachado à mão de propósito — o Playwright
  // não emula visibilidade (é o limite anotado no topo deste arquivo), e o que
  // se mede aqui é o HANDLER, não o `document.hidden`.
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await page.waitForTimeout(500);
  assert.equal(chamadas.poll, noTeto,
    `voltar para a aba ressuscitou o poll depois de expirar: ${noTeto} -> ${chamadas.poll}`);
  await page.close();
});

/**
 * PT2c — A CORRIDA DO ÚLTIMO INTERVALO, e é a de dinheiro.
 *
 * Na cauda o poll é de 10 s: a cobrança liquida DENTRO dessa janela, depois do
 * último poll. O código testava o deadline ANTES de perguntar ao servidor, então
 * a tela dizia "Este código expirou e nada foi cobrado" sobre cobrança PAGA — e
 * oferecia "Gerar novo código" ao lado, que CANCELA a cobrança remota e cria
 * outra (§10). Pagamento duplicado / `paid_orphan`.
 *
 * Aqui o servidor só responde `paid` DEPOIS do vencimento: quem passa é a
 * última pergunta, não o poll de antes. *Negativo: volte o `if (Date.now() >=
 * deadline) { pixExpirou(...); return; }` para o topo do `pixBater` → o
 * `waitForURL` estoura.*
 */
test("PT2c: liquidou dentro do último intervalo — pergunta antes de dizer que expirou",
  async () => {
    let vence = 0;
    const { page } = await abrirQr({
      pix: { expiresAt: () => { vence = Date.now() + 5000; return new Date(vence).toISOString(); } },
      status: () => (Date.now() >= vence ? { status: "paid" } : { status: "pending" }),
    });
    await page.waitForURL(/upgrade=success/, { timeout: 15000 });
    await page.close();
  });

/**
 * PT2d — QUEM MANDA É O SERVIDOR (o conflito dos dois tetos).
 *
 * O `Math.min(Date.parse(expires_at) || Infinity, Date.now() + PIX_TETO_MS)`
 * fazia o teto de 15 min do cliente atropelar o do servidor: com `expires_at` de
 * +1 h a tela parava aos 15 min e declarava expirada uma cobrança que o backend
 * ainda aceitava pagar. O comentário do arquivo já dizia que o teto de verdade é
 * o do servidor (§0.7) — o código é que fazia o contrário.
 *
 * O PT2b é o par deste: SEM `expires_at`, o teto do cliente continua valendo.
 */
test("PT2d: com expires_at longo, o teto de 15 min do cliente não atropela o servidor",
  async () => {
    const { page, chamadas } = await abrirQr({
      pix: { expiresAt: () => new Date(Date.now() + 3600000).toISOString() },
      relogio: true,
    });
    for (let i = 0; i < 20; i++) {
      await page.clock.fastForward(60000);
      await page.waitForTimeout(60);
    }
    await page.waitForTimeout(300);
    const aos20min = chamadas.poll;
    const texto = await page.textContent(".pix-box");
    assert.doesNotMatch(texto, /expirou|venceu/i,
      `aos 20 min, com 1 h de validade no servidor, a tela disse: "${texto}"`);
    assert.equal(await page.$$eval(".pix-code", (e) => e.length), 1,
      "o copia-e-cola sumiu aos 20 min de uma cobrança válida por 1 h");
    await page.clock.fastForward(60000);
    await page.waitForTimeout(300);
    assert.ok(chamadas.poll > aos20min,
      `o poll morreu no teto do cliente: ${aos20min} -> ${chamadas.poll}`);
    await page.close();
  });

/**
 * PT2b — as DUAS metades do `Date.parse(...) || Infinity`, e é o par que faz o
 * grupo medir alguma coisa:
 *
 *  · POSITIVO — sem `expires_at` o poll RODA. Sem esta metade, o PT2 sozinho
 *    passaria feliz num poll que nunca começa (basta o deadline nascer no
 *    passado);
 *  · o TETO DO CLIENTE ainda vale. Esta metade nasceu de uma medição: a mutação
 *    "tirar o `|| Infinity`" que o plano previa saía VERDE, porque
 *    `Math.min(NaN, x)` é `NaN` e `Date.now() >= NaN` é sempre falso — o efeito
 *    real de perder o fallback não é um poll que não começa, é um poll que NUNCA
 *    PARA. Só o relógio adiantado enxerga isso.
 */
test("PT2b: sem expires_at o poll roda e ainda para no teto do cliente", async () => {
  const { page, chamadas } = await abrirQr({ pix: { expiresAt: undefined }, relogio: true });

  // 12 s de relógio falso, em passos: cada `fastForward` dispara os timers
  // vencidos, e o respiro real deixa o fetch resolver e agendar o próximo.
  for (let i = 0; i < 4; i++) {
    await page.clock.fastForward(3000);
    await page.waitForTimeout(150);
  }
  const cedo = chamadas.poll;
  assert.ok(cedo >= 2, `sem expires_at o poll rodou só ${cedo} vez(es) em 12 s`);
  assert.equal(await page.$$eval(".pix-code", (e) => e.length), 1,
    "o código sumiu sem ter expirado");

  // Passa dos 15 minutos do teto do cliente.
  for (let i = 0; i < 20; i++) {
    await page.clock.fastForward(60000);
    await page.waitForTimeout(60);
  }
  await page.waitForTimeout(300);
  const noTeto = chamadas.poll;
  assert.match(await page.textContent(".pix-box"), /expirou/i,
    `passados 20 min sem expires_at, a tela não mostrou o teto do cliente`);
  await page.clock.fastForward(120000);
  await page.waitForTimeout(300);
  assert.equal(chamadas.poll, noTeto,
    `sem expires_at o poll não tem teto nenhum: ${noTeto} -> ${chamadas.poll}`);
  await page.close();
});

// ── PT3: a migração cartão -> Pix (§9) ──────────────────────────────────────
test("PT3: 409 stripe_active abre o modal com o aviso do Stripe e a data formatada",
  async () => {
    const { page } = await abrirPrecos({
      sub: { active: true, plan: "plus", interval: "monthly",
             current_period_end: "2026-12-05" },
      pix: { httpStatus: 409,
             corpo: { detail: { error: "stripe_active",
                                current_period_end: "2026-12-05" } } },
    });
    await page.click("#cycle-annual");
    await page.click('[data-pix-cta="plus"]');
    await page.waitForSelector(".pix-doc");
    await enviarDoc(page);
    await page.waitForTimeout(300);
    const texto = await page.textContent(".pix-box");
    // Mesma CAIXA do formulário, com o corpo trocado: nunca duas na tela.
    assert.equal(await page.$$eval(".pix-ov", (e) => e.length), 1,
      "a migração abriu um SEGUNDO overlay por cima do formulário");
    assert.equal(await page.$$eval(".pix-doc", (e) => e.length), 0,
      "o campo do CPF ficou na tela por baixo do aviso do Stripe");
    assert.match(texto, /não cancele pelo painel do stripe/i,
      `o modal de migração perdeu o aviso do Stripe: "${texto}"`);
    assert.ok(texto.includes("05/12/2026"), `data não formatada: "${texto}"`);
    assert.ok(!texto.includes("2026-12-05"), `o ISO cru vazou para a tela: "${texto}"`);
    await page.close();
  });

// ── PT4: assinante do Pix não recebe oferta de troca no Stripe ──────────────
const SUB_PIX = { active: true, gateway: "pix", plan: "plus", interval: "annual",
                  current_period_end: "2027-01-10" };

test("PT4: no anual o Plus diz Renovar, os outros Pagar, e nada bate no change-plan",
  async () => {
    const { page, chamadas } = await abrirPrecos({ sub: SUB_PIX });
    await page.click("#cycle-annual");
    const rotulos = await page.$$eval("[data-pix-cta]", (e) =>
      e.map((b) => [b.dataset.pixCta, b.textContent.trim()]));
    assert.deepEqual(rotulos.map(([p]) => p), ["essencial", "plus", "pro"]);
    assert.match(rotulos[1][1], /^Renovar no Pix/, `Plus leu "${rotulos[1][1]}"`);
    for (const i of [0, 2]) {
      assert.match(rotulos[i][1], /^Pagar no Pix/, `${rotulos[i][0]} leu "${rotulos[i][1]}"`);
    }

    // Nenhum [data-plan-btn] oferece troca: todos desabilitados, e o clique em
    // cada um deles não produz UMA requisição de troca.
    const cartao = await page.$$eval("#plans-v2 [data-plan-btn]", (e) => e.map((b) => ({
      plano: b.dataset.planBtn, off: b.disabled === true, txt: b.textContent.trim(),
    })));
    assert.deepEqual(cartao, [
      { plano: "essencial", off: true, txt: "Disponível no Pix" },
      { plano: "plus", off: true, txt: "✓ Seu plano atual" },
      { plano: "pro", off: true, txt: "Disponível no Pix" },
    ]);
    for (const p of ["essencial", "pro"]) {
      await page.click(`#plans-v2 [data-plan-btn="${p}"]`, { force: true }).catch(() => {});
    }
    await page.waitForTimeout(300);
    assert.equal(chamadas.changePlan, 0,
      `assinante Pix bateu ${chamadas.changePlan}x no /billing/change-plan`);
    await page.close();
  });

// O irmão que o CLAUDE.md §2 manda varrer: no MENSAL o `currentCycle !==
// subState.interval` fazia o código de hoje cair no ramo "Trocar pro X".
test("PT4b: assinante Pix no ciclo MENSAL — zero CTA de Pix e zero change-plan",
  async () => {
    const { page, chamadas } = await abrirPrecos({ sub: SUB_PIX });
    assert.equal(await contarCtas(page), 0, "o mensal criou CTA de Pix");
    const cartao = await page.$$eval("#plans-v2 [data-plan-btn]", (e) => e.map((b) => ({
      plano: b.dataset.planBtn, off: b.disabled === true, txt: b.textContent.trim(),
    })));
    assert.deepEqual(cartao, [
      { plano: "essencial", off: true, txt: "Disponível no Pix" },
      { plano: "plus", off: true, txt: "✓ Seu plano atual" },
      { plano: "pro", off: true, txt: "Disponível no Pix" },
    ]);
    for (const p of ["essencial", "plus", "pro"]) {
      await page.click(`#plans-v2 [data-plan-btn="${p}"]`, { force: true }).catch(() => {});
    }
    await page.waitForTimeout(300);
    assert.equal(chamadas.changePlan, 0,
      `no mensal o assinante Pix bateu ${chamadas.changePlan}x no /billing/change-plan`);
    await page.close();
  });

// ── PT5: controle POSITIVO — o caminho do cartão continua inteiro ───────────
test("PT5: 'Assinar Plus' no anual dispara 1 create-checkout e 0 pix/checkout",
  async () => {
    const { page, chamadas } = await abrirPrecos();
    await page.click("#cycle-annual");
    await Promise.all([
      page.waitForURL(/stripe=1/, { timeout: 5000 }),
      page.click('#plans-v2 [data-plan-btn="plus"]'),
    ]);
    assert.equal(chamadas.checkout, 1, `foram ${chamadas.checkout} POSTs de cartão`);
    assert.equal(chamadas.pixCheckout, 0, "o botão de cartão bateu no checkout do Pix");
    await page.close();
  });

// ── PT6: o sid do sucesso é o public_token, nunca o id do provedor ──────────
test("PT6: pago -> /home?upgrade=success com sid = public_token", async () => {
  const { page, corposPix } = await abrirQr({
    status: (n) => (n >= 2 ? { status: "paid" } : { status: "pending" }),
  });
  await page.waitForURL(/upgrade=success/, { timeout: 15000 });
  const url = new URL(page.url());
  assert.equal(url.searchParams.get("sid"), "tok_abc123");
  assert.equal(url.searchParams.get("ev"), "purchase");
  assert.equal(url.searchParams.get("pl"), "plus");
  // D3: sem `ia=` — a home.html trata a ausência sem inventar número.
  assert.equal(url.searchParams.get("ia"), null, `sobrou ia= na URL: ${page.url()}`);
  assert.ok(!page.url().includes("pay_"), `o id do provedor vazou: ${page.url()}`);
  assert.ok(!page.url().includes("9999"), `o id do provedor vazou: ${page.url()}`);
  // `gw=pix` é o MARCADOR de gateway e viaja sempre; dinheiro e data NÃO
  // viajam (ver PT6c): a /home busca os dois em `/billing/pix/<sid>`.
  assert.equal(url.searchParams.get("gw"), "pix");
  assert.deepEqual(corposPix[0],
    { plan: "plus", interval: "annual", cpf_cnpj: CPF });
  await page.close();
});

// ── PT6b: quem separa agendado de imediato é `agendada`, não a data ──────────
/**
 * Os DOIS casos no mesmo teste, de propósito: eles só provam alguma coisa
 * juntos. `access_starts_at` é preenchido nas duas compras — na imediata, com
 * `agora` —, então "tem `starts_at`" NÃO quer dizer "começa depois". Gatilhar
 * pela presença da data fazia a TELA DO QR dizer "seu ano começa em 10/09/2026"
 * para quem começa ao pagar.
 *
 * Aqui se mede só a tela do QR: é o único lugar onde a resposta do CHECKOUT é
 * fonte legítima da data (é a promessa de antes de pagar). A promessa DEPOIS de
 * pagar é da /home, que busca a cobrança no servidor — por isso a URL de
 * sucesso não leva `inicio` em nenhum dos dois casos, e os dois casos verificam
 * isso.
 *
 * Controle negativo: troque o `d.agendada && d.starts_at` do pix-poll.js de
 * volta por `d.starts_at` e o caso IMEDIATO fica vermelho; apague o `agendada`
 * da `resposta()` do backend e o AGENDADO fica.
 */
test("PT6b: agendada=true data na tela do QR; agendada=false com starts_at, não", async () => {
  const base = { public_token: "tok_abc123", qr_payload: PAYLOAD, qr_image: QR_IMG,
                 amount_cents: 19900, credit_cents: 0, plan: "plus" };
  const pago = { status: (n) => (n >= 2
    ? { status: "paid", starts_at: "2026-01-02T12:00:00+00:00" }
    : { status: "pending" }) };

  const ag = await abrirQr({ ...pago, pix: { corpo: {
    ...base, agendada: true, starts_at: "2027-08-26T03:00:00+00:00" } } });
  // A MESMA promessa já na tela do QR, antes de pagar.
  assert.match(await ag.page.textContent(".pix-box"), /começa em 26\/08\/2027/,
    "a tela do QR não repetiu a data do agendamento");
  await ag.page.waitForURL(/upgrade=success/, { timeout: 15000 });
  const url = new URL(ag.page.url());
  assert.equal(url.searchParams.get("gw"), "pix");
  assert.equal(url.searchParams.get("inicio"), null,
    `a data do checkout viajou na URL: ${ag.page.url()}`);
  await ag.page.close();

  // Compra IMEDIATA: o backend preenche `access_starts_at` com `agora` e diz
  // `agendada: false`. Nem a tela do QR nem a URL podem falar em data.
  const hoje = new Date().toISOString();
  const im = await abrirQr({ ...pago, pix: { corpo: {
    ...base, agendada: false, starts_at: hoje } } });
  assert.match(await im.page.textContent(".pix-box"), /começa agora/,
    "a tela do QR datou uma compra imediata");
  await im.page.waitForURL(/upgrade=success/, { timeout: 15000 });
  assert.equal(new URL(im.page.url()).searchParams.get("inicio"), null,
    `mandou inicio numa compra imediata: ${im.page.url()}`);
  await im.page.close();
});

// ── PT6c: a URL de sucesso não carrega dinheiro nem data ────────────────────
/**
 * Ela carregava: `vl=` (o valor cobrado) e `inicio=` (a data do começo). Os dois
 * eram retrato tirado no checkout e query string editável — `?vl=999999` num
 * link forjado virava um Purchase de R$ 999.999 na NOSSA conta de anúncios, sem
 * deduplicar com a CAPI, e a data envelhecia quando o `_stripe_cancel` adiava o
 * acesso depois de o QR já estar na tela. Agora a /home busca a cobrança em
 * `/billing/pix/<sid>`, e a URL só leva IDENTIFICADORES.
 *
 * O corpo do checkout aqui traz valor E data agendada de propósito: é o caso em
 * que o código antigo escrevia os dois. Enumera a lista inteira de params em vez
 * de checar dois nomes — param novo de dinheiro entra vermelho.
 *
 * Controle negativo: reponha `+ (vl ? "&vl=" + vl : "")` (ou o `&inicio=`) no
 * `pixPago` do pix-poll.js e este caso fica vermelho.
 */
test("PT6c: a URL de sucesso leva só identificadores, sem vl e sem inicio", async () => {
  const { page } = await abrirQr({
    status: (n) => (n >= 2 ? { status: "paid" } : { status: "pending" }),
    pix: { corpo: { public_token: "tok_abc123", qr_payload: PAYLOAD, qr_image: QR_IMG,
                    amount_cents: 9900, credit_cents: 0, plan: "essencial",
                    agendada: true, starts_at: "2027-08-26T03:00:00+00:00" } },
  });
  await page.waitForURL(/upgrade=success/, { timeout: 15000 });
  const params = [...new URL(page.url()).searchParams.keys()].sort();
  assert.deepEqual(params, ["ev", "gw", "pl", "sid", "td", "upgrade"],
    `params da URL de sucesso: ${page.url()}`);
  await page.close();
});

// ── PT8: "Copiar código Pix" não pode mentir ────────────────────────────────
/**
 * A ação PRIMÁRIA do modal (§16.1: dentro do app o aparelho não se escaneia) não
 * tinha um único teste. E ela mentia: o `ok()` rodava incondicionalmente, então
 * com o clipboard recusando e o `execCommand` devolvendo `false` o botão dizia
 * "Copiado ✓" sem nada no clipboard — e o usuário voltava do app do banco sem
 * código nenhum para colar. Com o `execCommand` LANÇANDO era pior: `pageerror`
 * não tratado e zero feedback.
 *
 * O clipboard e o `execCommand` são trocados na página porque nenhum dos dois é
 * controlável de fora: em Chromium headless o `writeText` já recusa sozinho, o
 * que deixaria o caso feliz impossível de escrever.
 */
async function prepararCopia(page, clipboard, exec) {
  await page.evaluate(({ clipboard, exec }) => {
    window.__execs = 0;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: clipboard === "ausente" ? undefined : {
        writeText: () => (clipboard === "ok"
          ? Promise.resolve() : Promise.reject(new Error("negado"))),
      },
    });
    document.execCommand = () => {
      window.__execs += 1;
      if (exec === "lanca") throw new Error("execCommand explodiu");
      return exec === "ok";
    };
  }, { clipboard, exec });
}

for (const [rotulo, clipboard, exec, copiou] of [
  ["clipboard aceita", "ok", "nao", true],
  ["clipboard recusa e o execCommand copia", "recusa", "ok", true],
  ["clipboard recusa e o execCommand também", "recusa", "nao", false],
  ["clipboard ausente e o execCommand LANÇA", "ausente", "lanca", false],
]) {
  test(`PT8: ${rotulo} -> o botão diz ${copiou ? "copiado" : "a verdade"}`, async () => {
    const { page } = await abrirQr();
    const erros = [];
    page.on("pageerror", (e) => erros.push(String(e)));
    await prepararCopia(page, clipboard, exec);

    await page.click(".pix-box .btn-primary");
    await page.waitForTimeout(400);
    const texto = (await page.textContent(".pix-box .btn-primary")).trim();
    if (copiou) {
      assert.match(texto, /^Copiado/, `copiou de verdade e o botão diz "${texto}"`);
    } else {
      assert.doesNotMatch(texto, /Copiado/,
        `NÃO copiou e o botão disse "${texto}" — é a tela mentindo sobre a ação primária`);
      assert.match(texto, /copie à mão/i, `sem saída para o usuário: "${texto}"`);
      // A saída oferecida tem de existir de fato: o campo fica SELECIONADO.
      const sel = await page.$eval(".pix-code", (e) => e.selectionEnd - e.selectionStart);
      assert.ok(sel > 0, "mandou copiar à mão sem selecionar o campo");
      // …e tem de caber. `.btn` é `white-space: nowrap` no site.css e este
      // rótulo é 3x mais longo que "Copiar código Pix": sem o `normal` do CSS
      // ele vazava para fora do botão E do card (medido em 390px).
      const vaza = await page.$eval(".pix-box .btn-primary", (e) => {
        const r = e.getBoundingClientRect();
        const caixa = e.closest(".pix-box").getBoundingClientRect();
        return { larg: Math.round(r.width), conteudo: e.scrollWidth,
                 esq: Math.round(caixa.left - r.left), dir: Math.round(r.right - caixa.right) };
      });
      assert.ok(vaza.conteudo <= vaza.larg + 1 && vaza.esq <= 0 && vaza.dir <= 0,
        `o rótulo de falha vazou do botão/card: ${JSON.stringify(vaza)}`);
    }
    // O `execCommand` só é tentado quando o clipboard não resolveu — e quando é
    // tentado, o retorno dele MANDA (era o valor ignorado).
    assert.equal(await page.evaluate(() => window.__execs), clipboard === "ok" ? 0 : 1);
    assert.deepEqual(erros, [], `sobrou erro não tratado na página: ${erros}`);
    await page.close();
  });
}

// ── PT9: um QR por vez — o Tab preso e a guarda do segundo checkout ─────────
/**
 * Sem trap de Tab o foco alcançava o CTA ATRÁS do overlay; Enter ali abria um
 * SEGUNDO modal, o `pixPoll` global era sobrescrito e o PRIMEIRO overlay ficava
 * órfão no DOM com o copia-e-cola visível para sempre — dois instrumentos ao
 * portador na tela e duas cobranças no provedor.
 *
 * O trap é o `pigTrapTab` do modals.js, o mesmo que o modal_keys.test.mjs e o
 * settings_security_fanout.test.mjs asseveram (§0.1). *Negativo: tire a chamada
 * do `pixOverlay` — ou o `<script src="/modals.js">` da precos.html — e o Tab
 * escapa.*
 */
test("PT9: o Tab não sai do modal do QR, e um segundo checkout não nasce", async () => {
  const { page, chamadas } = await abrirQr();
  const fora = [];
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    const onde = await page.evaluate(() => {
      const a = document.activeElement;
      const ov = document.querySelector(".pix-ov");
      return ov && ov.contains(a)
        ? null
        : { tag: a.tagName, txt: (a.textContent || a.value || "").trim().slice(0, 30) };
    });
    if (onde) fora.push({ tab: i + 1, ...onde });
  }
  assert.deepEqual(fora, [], `o Tab saiu do modal: ${JSON.stringify(fora)}`);

  // O irmão do trap (CLAUDE.md §2 — a classe, não a instância): mesmo quem
  // chegar ao CTA por outro caminho não abre a segunda cobrança.
  await page.evaluate(() => pixCheckout("pro"));
  await page.waitForTimeout(400);
  assert.equal(chamadas.pixCheckout, 1,
    `com o QR na tela, nasceu um SEGUNDO checkout (${chamadas.pixCheckout} no total)`);
  assert.equal(await page.$$eval(".pix-ov", (e) => e.length), 1, "ficou um overlay órfão");
  await page.close();
});

// ── PT11: campo ausente no corpo do checkout não vira lixo na tela ──────────
// Fronteira de confiança de VALOR MONETÁRIO: sem `amount_cents` o título dizia
// "Plus anual · R$ NaN", e sem `qr_image` o `<img>` desenhava um quadrado branco
// de 230px (o fundo claro que o leitor exige) com cara de QR ilegível.
test("PT11: sem amount_cents e sem qr_image não sobra R$ NaN nem quadrado branco",
  async () => {
    const { page } = await abrirQr({
      pix: { corpo: { public_token: "tok_x", qr_payload: PAYLOAD, plan: "plus" } },
    });
    const titulo = await page.textContent("#pix-modal-titulo");
    assert.ok(!titulo.includes("NaN"), `valor ilegível no título: "${titulo}"`);
    assert.match(titulo, /anual/, `o título perdeu o plano: "${titulo}"`);
    assert.equal(await page.$$eval(".pix-qr", (e) => e.length), 0,
      "sobrou o <img> do QR sem imagem nenhuma");
    // O que importa continua na tela: o copia-e-cola é a ação primária.
    assert.equal(await page.inputValue(".pix-code"), PAYLOAD);
    await page.close();
  });

// ── PT7: §13.6 — o QR é instrumento ao portador ─────────────────────────────
/**
 * localStorage + sessionStorage + DOM + `<img src="data:...">` + os dois que
 * faltavam, e sem os quais o grupo era VERDE POR CONSTRUÇÃO:
 *
 *  · `valores` — `code.value = payload` escreve a PROPRIEDADE, e o
 *    `document.body.innerHTML` nunca contém o valor de um `<input>`, nem com o
 *    modal aberto. A asserção sobre o `dom` passava sem ninguém apagar nada.
 *  · `retido` — o `pixPoll.code` do nó JÁ DESTACADO. É o único observável que
 *    separa "o `pixApagarQr` apagou" de "o container foi removido por cima": nos
 *    três casos do laço abaixo o payload sai da tela junto com o overlay ou com
 *    o `replaceChildren`, então com `pixApagarQr` virando `{ return; }` os três
 *    continuavam VERDES (12/12, medido). Depois de EXPIRAR o `pixPoll` continua
 *    vivo segurando o `<input>` destacado, e é aí que o wipe se mede.
 *    (`pixPoll` é `let` de topo de script clássico: escopo léxico global, lido
 *    pelo nome nu — não existe `window.pixPoll`.)
 */
function vestigios(page) {
  return page.evaluate(() => ({
    local: JSON.stringify({ ...localStorage }),
    sessao: JSON.stringify({ ...sessionStorage }),
    dom: document.body.innerHTML,
    valores: [...document.querySelectorAll("input,textarea")].map((e) => e.value).join("|"),
    retido: typeof pixPoll !== "undefined" && pixPoll && pixPoll.code
      ? pixPoll.code.value : "",
    imgsData: [...document.images].filter((i) => (i.getAttribute("src") || "")
      .startsWith("data:")).length,
    campos: document.querySelectorAll(".pix-code").length,
  }));
}

for (const [rotulo, encerrar] of [
  ["fechando o modal", async (page) => {
    await page.click(".pix-box .pix-ghost");
    await page.waitForTimeout(200);
  }],
  ["deixando expirar", async (page) => { await page.waitForTimeout(8000); }],
  ["pagando", async (page) => {
    await page.waitForURL(/upgrade=success/, { timeout: 15000 });
  }],
]) {
  test(`PT7: o copia-e-cola não sobra em lugar nenhum — ${rotulo}`, async () => {
    const { page } = await abrirQr({
      pix: { expiresAt: () => new Date(Date.now() + 5000).toISOString() },
      status: (n) => (rotulo === "pagando" && n >= 2
        ? { status: "paid" } : { status: "pending" }),
    });
    // Âncora do caso: antes de encerrar, o payload ESTÁ na tela. Sem ela o teste
    // passaria numa página que nunca mostrou QR nenhum.
    assert.equal(await page.inputValue(".pix-code"), PAYLOAD);

    await encerrar(page);
    const v = await vestigios(page);
    for (const [onde, texto] of [["localStorage", v.local], ["sessionStorage", v.sessao],
                                 ["DOM", v.dom], ["valor dos campos", v.valores],
                                 ["<input> destacado do pixPoll", v.retido]]) {
      assert.ok(!texto.includes(PAYLOAD), `o qr_payload sobrou no ${onde}`);
      assert.ok(!texto.includes("pigbank-teste-qr-payload"),
        `pedaço do qr_payload sobrou no ${onde}`);
    }
    assert.equal(v.imgsData, 0, "sobrou <img src=\"data:...\"> na página");
    assert.equal(v.campos, 0, "sobrou o <input> do copia-e-cola");
    // O token pode ficar na URL (é chave pública); o payload, nunca.
    assert.ok(!page.url().includes(PAYLOAD), "o qr_payload foi parar na URL");
    await page.close();
  });
}

/**
 * PT10 — o poll morreu, e o §13.6 vale mesmo assim.
 *
 * Três falhas de rede seguidas matavam o poll (`pixDesistir`) e com ele a
 * avaliação do deadline: medido, o payload continuava na tela UMA HORA depois de
 * vencer, sob a mensagem "o código continua válido". O docstring do arquivo
 * promete que ele sai ao pagar, EXPIRAR ou fechar.
 *
 * É também o caso que DISCRIMINA o `pixApagarQr` (o modal segue aberto, então
 * nada mais remove o `<input>` por cima) e o que prova a segunda metade do P1-1:
 * sem confirmação do servidor a tela **não** diz "nada foi cobrado" e **não**
 * oferece "Gerar novo código", que cancelaria uma cobrança talvez paga.
 */
test("PT10: rede fora — no vencimento o payload sai da tela e a mensagem não mente",
  async () => {
    const { page } = await abrirQr({
      pix: { expiresAt: () => new Date(Date.now() + 15000).toISOString() },
      status: () => null,                    // toda consulta ABORTA
    });
    assert.equal(await page.inputValue(".pix-code"), PAYLOAD);

    // 3 falhas em 3 s cada -> desiste. Aqui o código AINDA vale: não some.
    await page.waitForTimeout(9000);
    assert.match(await page.textContent(".pix-box"), /não consegui confirmar/i);
    assert.equal(await page.$$eval(".pix-code", (e) => e.length), 1,
      "apagou o copia-e-cola de uma cobrança que ainda era pagável");

    // Passado o vencimento, sai — com o modal ainda ABERTO.
    await page.waitForTimeout(9000);
    const v = await vestigios(page);
    assert.equal(v.campos, 0, "o <input> do copia-e-cola ficou na tela depois de vencer");
    for (const [onde, texto] of [["DOM", v.dom], ["valor dos campos", v.valores],
                                 ["<input> destacado do pixPoll", v.retido]]) {
      assert.ok(!texto.includes(PAYLOAD), `o qr_payload sobrou no ${onde}`);
    }
    const texto = await page.textContent(".pix-box");
    assert.match(texto, /venceu/i, `a tela não avisou do vencimento: "${texto}"`);
    assert.ok(!texto.includes("nada foi cobrado"),
      `sem resposta do servidor, a tela AFIRMOU que nada foi cobrado: "${texto}"`);
    const botoes = await page.$$eval(".pix-box button", (e) => e.map((b) => b.textContent.trim()));
    assert.deepEqual(botoes, ["Fechar"],
      `ofereceu gerar código novo sem saber se este foi pago: ${JSON.stringify(botoes)}`);
    await page.close();
  });

// ── PT12: o CPF/CNPJ — o Asaas exige, e nós não guardamos ───────────────────
/**
 * O clique no CTA parou de cobrar: ele abre o ESTADO 1 do modal (o documento) e
 * o `POST` só sai no submit. Sem isso o cliente clicava em "Pagar no Pix" e
 * tomava o erro do provedor, que recusa cobrança sem `cpfCnpj`.
 *
 * A validação da tela é de FORMA e só (11 ou 14 dígitos). Dígito verificador é
 * do Asaas: uma segunda cópia da regra aqui recusaria o que ele aceita (§0.7).
 *
 * *Negativo do grupo (a/b/c): troque o `pixFormaOk` por `() => true` e o PT12a e
 * o PT12b ficam vermelhos (o POST passa a sair vazio); troque por `() => false`
 * e o PT12c fica vermelho (o caminho legítimo para de vender). O par existe
 * porque este conserto RESTRINGE: sozinho, o "a/b" passaria num código que
 * recusa todo mundo.*
 */

/** Abre o modal no estado 1 e devolve o contexto, sem enviar nada. */
async function abrirForm(ctx = {}) {
  const r = await abrirPrecos(ctx);
  await r.page.click("#cycle-annual");
  await r.page.click('[data-pix-cta="plus"]');
  await r.page.waitForSelector(".pix-doc");
  return r;
}

test("PT12a: sem documento o POST não sai, e o erro diz o que fazer", async () => {
  const { page, chamadas } = await abrirForm();
  // O foco inicial do modal é o campo — quem abriu quer digitar.
  assert.equal(await page.evaluate(() => document.activeElement.className), "pix-doc",
    "o modal não nasceu com o foco no campo do documento");
  await enviarDoc(page, "");
  await page.waitForTimeout(300);
  assert.equal(chamadas.pixCheckout, 0,
    `o checkout saiu SEM documento (${chamadas.pixCheckout} chamadas) — o Asaas recusa`);
  assert.equal(await page.textContent(".pix-erro"),
    "Informe os 11 dígitos do CPF ou os 14 do CNPJ.");
  assert.equal(await page.$$eval(".pix-code", (e) => e.length), 0,
    "apareceu QR sem cobrança nenhuma ter sido pedida");
  // Erro não tira a pessoa de onde ela conserta.
  assert.equal(await page.evaluate(() => document.activeElement.className), "pix-doc");
  await page.close();
});

for (const [rotulo, valor] of [
  ["letras", "abcdefghijk"],
  ["10 dígitos", "1112223334"],
  ["12 dígitos", "111222333444"],
  ["15 dígitos", "112223330001812"],
]) {
  test(`PT12b: forma errada (${rotulo}) — erro na tela e zero POST`, async () => {
    const { page, chamadas } = await abrirForm();
    await enviarDoc(page, valor);
    await page.waitForTimeout(300);
    assert.equal(chamadas.pixCheckout, 0,
      `"${valor}" passou pela validação de forma e virou cobrança`);
    const erro = (await page.textContent(".pix-erro")).trim();
    assert.match(erro, /11 dígitos do CPF/, `erro ilegível ou ausente: "${erro}"`);
    assert.match(erro, /14 do CNPJ/, `o erro não diz a saída do CNPJ: "${erro}"`);
    // O erro é visível de fato: `.pix-erro:empty` some, e um `display:none` que
    // sobrasse deixaria a asserção de texto acima verde numa tela muda.
    assert.ok(await page.isVisible(".pix-erro"), "a mensagem de erro está no DOM e invisível");
    await page.close();
  });
}

for (const [rotulo, digitado, esperado] of [
  ["CPF de 11 dígitos", CPF, CPF],
  ["CNPJ de 14 dígitos", CNPJ, CNPJ],
  ["CPF pontuado — a pontuação é limpa no envio", "111.222.333-44", CPF],
]) {
  test(`PT12c: ${rotulo} passa e o QR aparece no MESMO modal`, async () => {
    const { page, chamadas, corposPix } = await abrirForm();
    await enviarDoc(page, digitado);
    await page.waitForSelector(".pix-code");
    assert.equal(chamadas.pixCheckout, 1);
    assert.equal(corposPix[0].cpf_cnpj, esperado,
      `o corpo do POST levou "${corposPix[0].cpf_cnpj}" em vez de "${esperado}"`);
    assert.equal(await page.inputValue(".pix-code"), PAYLOAD);
    // MESMO modal, não uma segunda caixa: um overlay só, e o formulário sumiu.
    assert.equal(await page.$$eval(".pix-ov", (e) => e.length), 1);
    assert.equal(await page.$$eval(".pix-form", (e) => e.length), 0,
      "o formulário do documento ficou por baixo do QR");
    // O foco muda de dono junto com o estado: a ação primária agora é copiar.
    const foco = (await page.evaluate(() => document.activeElement.textContent)).trim();
    assert.match(foco, /Copiar código Pix/,
      `depois do QR o foco ficou em "${foco}" em vez do botão de copiar`);
    await page.close();
  });
}

/**
 * PT12d — o documento é PII e NÃO é persistido: nem por nós, nem pelo cliente.
 *
 * Mede o `value` da PROPRIEDADE e o nó JÁ DESTACADO (`pixDoc`), pelo mesmo
 * motivo medido no PT7: `document.body.innerHTML` nunca contém o valor de um
 * `<input>`, então asserção só sobre o DOM serializado é verde por construção —
 * o overlay/`replaceChildren` removeria o campo por cima e ninguém veria a
 * diferença entre "apagou" e "sumiu de cena".
 *
 * *Negativo: troque o corpo do `pixApagarDoc` por `{ return; }` — medido, os dois
 * primeiros casos ficam VERMELHOS no `<input> destacado do pixDoc`.* O terceiro
 * ("indo até o fim da compra") NÃO discrimina o wipe e continua verde: o
 * `pixPago` navega para a /home e o contexto inteiro morre junto. Ele mede a
 * outra metade — nada em storage, nada na URL, nada no console e nada no corpo
 * de requisição nenhuma —, que é onde um `history.state` ou um `sessionStorage`
 * apareceriam depois da compra.
 */
function espiarFugas(page) {
  const fugas = [];
  page.on("console", (m) => fugas.push("console: " + m.text()));
  page.on("pageerror", (e) => fugas.push("pageerror: " + String(e)));
  // GA4 e Meta CAPI saem como REQUISIÇÃO: se o documento entrasse num deles,
  // apareceria aqui. O POST do checkout é o único destino legítimo dele.
  page.on("request", (r) => {
    if (r.url().endsWith("/billing/pix/checkout")) return;
    fugas.push(r.url() + " " + (r.postData() || ""));
  });
  return fugas;
}

const vestigiosDoc = (page) => page.evaluate(() => ({
  local: JSON.stringify({ ...localStorage }),
  sessao: JSON.stringify({ ...sessionStorage }),
  dom: document.body.innerHTML,
  valores: [...document.querySelectorAll("input,textarea")].map((e) => e.value).join("|"),
  retido: typeof pixDoc !== "undefined" && pixDoc ? pixDoc.value : "",
  campos: document.querySelectorAll(".pix-doc").length,
}));

for (const [rotulo, encerrar] of [
  // Digitou e desistiu ANTES do submit: nenhuma cobrança nasceu, e é o único
  // caso em que o `pixApagarQr` não passa por perto — quem limpa é o `aoFechar`.
  ["cancelando antes de enviar", async (page) => {
    await page.fill(".pix-doc", CPF);
    await page.click(".pix-box .pix-ghost");
    await page.waitForTimeout(200);
  }],
  ["fechando o modal com o QR na tela", async (page) => {
    await enviarDoc(page, CPF);
    await page.waitForSelector(".pix-code");
    await page.click(".pix-box .pix-ghost");
    await page.waitForTimeout(200);
  }],
  ["indo até o fim da compra", async (page) => {
    await enviarDoc(page, CPF);
    await page.waitForURL(/upgrade=success/, { timeout: 15000 });
  }],
]) {
  test(`PT12d: o CPF não sobra em lugar nenhum — ${rotulo}`, async () => {
    const { page } = await abrirForm({
      status: (n) => (n >= 2 ? { status: "paid" } : { status: "pending" }),
    });
    const fugas = espiarFugas(page);
    // Âncora do caso: antes de encerrar, o número ESTÁ na tela. Sem ela o teste
    // passaria numa página que nunca chegou a receber documento nenhum.
    await page.fill(".pix-doc", CPF);
    assert.equal(await page.inputValue(".pix-doc"), CPF);
    await encerrar(page);

    const v = await vestigiosDoc(page);
    for (const [onde, texto] of [["localStorage", v.local], ["sessionStorage", v.sessao],
                                 ["DOM", v.dom], ["valor dos campos", v.valores],
                                 ["<input> destacado do pixDoc", v.retido],
                                 ["URL", page.url()]]) {
      assert.ok(!texto.includes(CPF), `o CPF sobrou no ${onde}`);
    }
    assert.equal(v.campos, 0, "sobrou o <input> do documento na página");
    const vazou = fugas.filter((t) => t.includes(CPF) || t.includes("111.222.333-44"));
    assert.deepEqual(vazou, [],
      `o CPF saiu por um caminho que não é o POST do checkout: ${JSON.stringify(vazou)}`);
    await page.close();
  });
}

/**
 * PT12e — o Tab preso vale nos DOIS estados do modal.
 *
 * O PT9 mede o estado do QR. Este mede o do formulário, que é o estado NOVO: sem
 * o trap o foco alcança o CTA atrás do overlay e o Enter ali abriria um segundo
 * modal por cima de um campo com CPF digitado dentro.
 *
 * *Negativo: tire a chamada do `pigTrapTab` do `pixOverlay` — ou o
 * `<script src="/modals.js">` da precos.html — e o Tab escapa.*
 */
test("PT12e: no estado do FORMULÁRIO o Tab não sai do modal", async () => {
  const { page, chamadas } = await abrirForm();
  const fora = [];
  for (let i = 0; i < 10; i++) {
    await page.keyboard.press("Tab");
    const onde = await page.evaluate(() => {
      const a = document.activeElement;
      const ov = document.querySelector(".pix-ov");
      return ov && ov.contains(a)
        ? null
        : { tag: a.tagName, txt: (a.textContent || a.value || "").trim().slice(0, 30) };
    });
    if (onde) fora.push({ tab: i + 1, ...onde });
  }
  assert.deepEqual(fora, [], `o Tab saiu do formulário: ${JSON.stringify(fora)}`);
  // A classe, não a instância (§2): quem chegar ao CTA por outro caminho também
  // não abre um segundo modal por cima do documento já digitado.
  await page.evaluate(() => pixCheckout("pro"));
  assert.equal(await page.$$eval(".pix-ov", (e) => e.length), 1,
    "nasceu um SEGUNDO modal com o formulário do documento aberto");
  assert.equal(chamadas.pixCheckout, 0, "o segundo caminho cobrou sem pedir documento");
  await page.close();
});

/**
 * PT13 — A RESPOSTA É DA COBRANÇA QUE A PEDIU, e é a de dinheiro.
 *
 * Fechar o modal com o poll no ar e abrir OUTRO checkout deixava `pixPoll`
 * não-nulo de novo, e a guarda `if (!pixPoll) return` aceitava a resposta da
 * cobrança VELHA: um `paid` antigo redirecionava para a /home com o `sid` da
 * cobrança NOVA (atribuição errada no GA4 e no pixel, sobre uma compra que não
 * aconteceu), e um terminal antigo apagava o QR novo da tela.
 *
 * A 1ª pergunta fica PENDURADA no servidor e só responde `paid` quando o teste
 * solta — que é a cobrança velha liquidando depois de a tela já ser de outra.
 *
 * *Negativo: troque os `if (pixPoll !== meu) return;` do `pixBater` de volta por
 * `if (!pixPoll) return;` → o `waitForTimeout` seguinte encontra a /home.*
 */
test("PT13: resposta do poll da cobrança velha não decide sobre o modal novo", async () => {
  let soltar;
  const velha = new Promise((ok) => { soltar = ok; });
  const { page, chamadas } = await abrirQr({
    status: async (n) => {
      if (n !== 1) return { status: "pending" };
      await velha;
      return { status: "paid" };
    },
  });
  assert.equal(chamadas.poll, 1, "a primeira pergunta não chegou a sair");
  await page.click(".pix-box .pix-ghost");          // fecha com a requisição no ar
  await page.waitForTimeout(150);
  await page.click('[data-pix-cta="pro"]');
  await page.waitForSelector(".pix-doc");
  await enviarDoc(page);
  await page.waitForSelector(".pix-code");
  const url = page.url();

  soltar();                                          // a cobrança VELHA diz "paid"
  await page.waitForTimeout(900);
  assert.equal(page.url(), url,
    `a resposta da cobrança velha navegou a página: ${page.url()}`);
  assert.equal(await page.$$eval(".pix-code", (e) => e.length), 1,
    "a resposta da cobrança velha apagou o QR da cobrança nova");
  assert.equal(await page.inputValue(".pix-code"), PAYLOAD);
  await page.close();
});

/**
 * PT14 — VITALÍCIO NÃO COMPRA, e o CTA some quando a assinatura chega depois.
 *
 * O `refreshPlanButtons` já marca os cards como acesso permanente; o laço do Pix
 * só olhava `gateway` e `plan`, então o vitalício via os três CTAs, digitava o
 * CPF e tomava o 409 `lifetime` do backend. Como o CTA agora nasce ANTES do
 * /billing/subscription (PT15), o caso que importa é o da assinatura ATRASADA:
 * ele aparece e tem de SAIR.
 *
 * Positivo (já no arquivo): o PT4 prova que assinante de Pix não-vitalício
 * continua vendo os três CTAs, com "Renovar" no plano dele.
 *
 * *Negativo: tire o `&& !(pixSub && pixSub.lifetime === true)` do
 * `pbPixRefresh` → sobram os 3 CTAs depois da assinatura chegar.*
 */
test("PT14: vitalício não fica com CTA de Pix nenhum", async () => {
  const { page } = await abrirPrecos({
    sub: { active: true, lifetime: true },
    atrasos: { "/billing/subscription": 1200 },
  });
  await page.click("#cycle-annual");
  assert.equal(await contarCtas(page), 3,
    "âncora do caso: antes da assinatura chegar os CTAs existem");
  await page.waitForTimeout(1500);
  assert.equal(await contarCtas(page), 0,
    "o vitalício ficou com CTA de compra de Pix depois da assinatura chegar");
  assert.equal(await page.textContent('[data-plan-btn="plus"]'), "Você tem acesso vitalício");
  await page.close();
});

/**
 * PT15 — O CTA DE PIX NÃO ESPERA O STRIPE.
 *
 * Para quem já é cliente do Stripe, o /billing/subscription consulta o Stripe e
 * pode demorar ou travar. Enquanto ele não voltava, NENHUM CTA de Pix existia —
 * escondendo a migração cartão → Pix exatamente quando o Stripe está ruim.
 *
 * *Negativo: volte o `publicarPix(null)` da precos.html para depois do
 * `await loadSubscription()` → zero CTA aqui.*
 */
test("PT15: com /billing/subscription lento, os CTAs de Pix já estão na tela", async () => {
  const { page } = await abrirPrecos({
    sub: { active: true, gateway: "stripe", plan: "plus", interval: "monthly" },
    atrasos: { "/billing/subscription": 2500 },
  });
  await page.click("#cycle-annual");
  assert.equal(await contarCtas(page), 3,
    "os CTAs de Pix esperaram o /billing/subscription para nascer");
  await page.close();
});

/**
 * PT16 — O SCRIPT PODE CHEGAR DEPOIS DAS REQUISIÇÕES.
 *
 * O `loadPlansState` é inline e começa antes de o parser alcançar os dois
 * `<script>` do Pix; a única inicialização era guardada por `typeof pbPixInit`.
 * Com as duas requisições terminando enquanto o script ainda baixa, a guarda
 * dava falso e ninguém tentava de novo: página sem CTA de Pix, com a flag ligada
 * no servidor. Agora quem chega por último lê o `window.pbPixState`.
 *
 * *Negativo: tire o `if (window.pbPixState) pbPixInit(...)` do fim do
 * pix-checkout.js → zero CTA aqui, e o `pixCfg` fica nulo (o setCycle também não
 * salva, porque o `pbPixRefresh` sai na primeira linha sem cfg).*
 */
test("PT16: pix-checkout.js chegando depois das requisições ainda cria os CTAs", async () => {
  const { page } = await abrirPrecos({ atrasos: { "pix-checkout.js": 1500 } });
  await page.click("#cycle-annual");
  assert.equal(await contarCtas(page), 3,
    "o script chegou depois das requisições e a página ficou sem CTA de Pix");
  await page.close();
});

/**
 * PT17 — FECHAR ENTRE OS CABEÇALHOS E O CORPO.
 *
 * O `ctx.box.isConnected` era conferido antes do `await r.json()`: dá para
 * fechar o modal enquanto o corpo ainda baixa, e aí o QR era montado numa caixa
 * já destacada, com o poll rodando por trás dela — e o `pixPoll` invisível
 * impedia o próximo checkout até a cobrança vencer.
 *
 * O Esc sai de DENTRO do `Response.prototype.json`, que é o único ponto em que a
 * janela existe: `route.fulfill` não separa cabeçalho de corpo. O evento é o
 * mesmo que o teclado do usuário dispara.
 *
 * *Negativo: mova o `if (!ctx.box.isConnected) return;` do `pixEnviar` para
 * antes do `const d = await r.json()` → o poll começa e o segundo checkout não
 * abre.*
 */
test("PT17: fechar durante o download do corpo não deixa QR nem poll órfãos", async () => {
  const { page, chamadas } = await abrirForm({
    initScript: () => {
      const orig = Response.prototype.json;
      Response.prototype.json = function () {
        return orig.call(this).then((d) => {
          if (String(this.url).includes("/billing/pix/checkout")) {
            document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
          }
          return d;
        });
      };
    },
  });
  await enviarDoc(page);
  await page.waitForTimeout(600);
  assert.equal(chamadas.pixCheckout, 1, "âncora: o POST do checkout saiu");
  assert.equal(await page.$$eval(".pix-ov", (e) => e.length), 0,
    "sobrou modal na tela depois de fechar durante o corpo da resposta");
  assert.equal(await page.$$eval(".pix-code", (e) => e.length), 0,
    "o copia-e-cola foi montado numa caixa já destacada");
  assert.equal(chamadas.poll, 0,
    "o poll começou contra um modal que o usuário já tinha fechado");
  // A consequência visível para o usuário: o próximo checkout tem de abrir.
  await page.click('[data-pix-cta="plus"]');
  await page.waitForSelector(".pix-doc", { timeout: 3000 });
  await page.close();
});

/**
 * PT18 — A RECUSA CHEGA À TELA, NO LUGAR ONDE ELA SE LÊ.
 *
 * O `detail` do FastAPI é STRING quando o `raise` passa texto (o 400 do CPF
 * inválido) e OBJETO nos 409. O ramo do `!r.ok` lia só `det.message`: com
 * string, `det.message` é `undefined` e a pessoa recebia o genérico "não
 * consegui gerar o código Pix agora" — que sugere problema nosso, quando o
 * conserto é digitar o documento certo.
 *
 * Ler a mensagem certa não bastava: ela ia toda para o `#toast`, que fica ATRÁS
 * do véu do modal. Quem digita 11 dígitos com DV errado (o caminho comum desde
 * que o servidor passou a conferir o mod-11) via a caixa não fazer nada. Agora
 * o 400 é erro DO CAMPO e vai para o `#pix-doc-erro` — o alvo do
 * `aria-describedby` do `<input>`, dentro do modal —, e o resto (409, 503, 429,
 * 403, 401), que NÃO é erro do documento, continua no toast, que passou a ficar
 * por cima do véu.
 *
 * E a mensagem velha morre num PONTO SÓ: no início do envio. A enumeração
 * (desfecho do `pixEnviar` × toast) não achou um único desfecho em que o toast
 * anterior devesse sobreviver — o que trocasse o corpo do modal (QR, migração,
 * inline) deixava a frase velha por cima do véu, agora perfeitamente legível
 * com o z-index 1000. `showToast("")` limpa CLASSE e TEXTO: o `#toast` é
 * `role="status"` e o texto velho seguia na árvore de acessibilidade.
 *
 * *Negativos (rodados um a um, medidos em 2026-09-10 sobre a ceca7ff; remeça
 * antes de reusar os números):*
 * - troque `r.status === 400` por `false` → PT18a vermelho (inline sai `""`);
 * - volte o `#toast` da `precos.html` para `z-index: 999` → PT18c vermelho nos
 *   2 viewports (brilho máximo cai para 30/255);
 * - tire o `showToast("")` do início do `pixEnviar` → PT18d, PT18e e os DOIS
 *   PT18f vermelhos nos 2 viewports, 8 falhas (o toast do 503 fica por cima do
 *   campo, do QR, da migração e do "já pago");
 * - tire o `showToast("")` do ramo da forma inválida → PT18g vermelho (só ele:
 *   é o único desfecho que não passa pelo `pixEnviar`);
 * - `.pix-erro { display: none !important }` → 6 vermelhos: PT18a nos 2
 *   viewports e os 4 PT12b junto (a `isVisible(".pix-erro")` da linha 873, que
 *   já existia). O que o PT18a acrescenta é a visibilidade do inline no caminho
 *   do 400 DO SERVIDOR — o da forma inválida já tinha quem o medisse;
 * - `showToast` de volta ao `add("show")` → PT18d (×2) e PT18g vermelhos: o
 *   `remove` implícito é o que apaga a frase velha do `role="status"`.
 * *Positivos do grupo:* PT18b (o 409 objeto continua sendo lido pela `message`,
 * o par que um `String(d.detail)` destruiria) e o PT12c (documento válido ainda
 * vende — uma correção que sequestrasse todo erro para o campo passaria no a/c
 * e mataria a venda).
 */
const TELAS = [["desktop", { width: 1280, height: 900 }],
               ["mobile", { width: 390, height: 844 }]];
// A falha do provedor: é ela que convida ao reenvio ("tenta de novo"), e é o
// reenvio que põe o toast velho por cima do desfecho novo.
const FALHA_503 = "Não consegui emitir o Pix agora. Tenta de novo em instantes.";

/**
 * Brilho máximo (0–255) dentro do retângulo de um elemento — o instrumento do
 * PT18c. `elementFromPoint` NÃO serve aqui: o `#toast` tem `pointer-events:
 * none`, então o hit-test devolve o `pix-ov` com z-index 999 E com 1000, e o
 * teste ficaria vermelho com e sem o conserto (§3, teatro). O que discrimina é
 * a foto: com o toast atrás do véu, o pixel mais claro do retângulo é o preto
 * translúcido do overlay.
 */
async function brilhoMax(page, seletor) {
  const r = await page.$eval(seletor, (e) => {
    const b = e.getBoundingClientRect();
    return { x: b.x, y: b.y, width: b.width, height: b.height };
  });
  const png = (await page.screenshot({ clip: r })).toString("base64");
  return page.evaluate(async (b64) => {
    const img = new Image();
    img.src = "data:image/png;base64," + b64;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const ctx2 = c.getContext("2d");
    ctx2.drawImage(img, 0, 0);
    const d = ctx2.getImageData(0, 0, c.width, c.height).data;
    let max = 0;
    for (let i = 0; i < d.length; i += 4) max = Math.max(max, d[i], d[i + 1], d[i + 2]);
    return max;
  }, png);
}

for (const [tela, viewport] of TELAS) {
  test(`PT18a (${tela}): o 400 do documento fica DENTRO do modal, não no toast`, async () => {
    const { page } = await abrirForm({
      viewport,
      pix: { httpStatus: 400, corpo: { detail: "Informe um CPF ou CNPJ válido." } },
    });
    await enviarDoc(page);
    await page.waitForTimeout(300);
    const visto = await page.evaluate(() => ({
      texto: document.getElementById("pix-doc-erro").textContent,
      foco: document.activeElement.className,
      toast: document.getElementById("toast").textContent,
    }));
    assert.equal(visto.texto, "Informe um CPF ou CNPJ válido.",
      `a recusa do documento não chegou ao campo: "${visto.texto}"`);
    assert.equal(visto.toast, "",
      `a recusa do documento ainda foi para o toast, atrás do véu: "${visto.toast}"`);
    // O `botao.disabled` do envio largou o foco no <body>: quem vai corrigir o
    // número tem de estar com o cursor nele.
    assert.equal(visto.foco, "pix-doc",
      `depois da recusa o foco ficou em ".${visto.foco}" em vez do campo`);
    // Texto no `textContent` não é texto NA TELA: com `.pix-erro { display: none }`
    // as três asserções acima passam (medido, rects=0). A vizinha `:empty` já mexe
    // no `display` deste seletor — uma regra de CSS reintroduzia o bug original com
    // o grupo inteiro verde. O brilho é o mesmo instrumento do toast: o `#ffb4b4`
    // do `.pix-erro` pinta o retângulo bem acima do fundo da caixa.
    assert.ok(await page.$eval("#pix-doc-erro", (e) => e.getClientRects().length > 0),
      "a recusa está no DOM mas não ocupa área nenhuma: ninguém a lê na tela");
    const brilhoInline = await brilhoMax(page, "#pix-doc-erro");
    assert.ok(brilhoInline >= 150,
      `a recusa não foi pintada: brilho máximo ${brilhoInline}/255 no retângulo do <p>`);
    await page.close();
  });

  test(`PT18c (${tela}): o 503 não sequestra o campo, e o toast é legível`, async () => {
    const { page } = await abrirForm({
      viewport,
      pix: { httpStatus: 503, corpo: { detail: FALHA_503 } },
    });
    await enviarDoc(page);
    await page.waitForTimeout(500);          // o toast entra com transição de .25s
    const visto = await page.evaluate(() => ({
      inline: document.getElementById("pix-doc-erro").textContent,
      toast: document.getElementById("toast").textContent,
    }));
    assert.equal(visto.inline, "",
      `falha do provedor virou erro do CPF no campo: "${visto.inline}"`);
    assert.equal(visto.toast, FALHA_503,
      `a falha do provedor não chegou ao toast: "${visto.toast}"`);
    const brilho = await brilhoMax(page, "#toast");
    assert.ok(brilho >= 200,
      `o toast ficou atrás do véu do modal: brilho máximo ${brilho}/255 no retângulo dele`);
    await page.close();
  });

  test(`PT18d (${tela}): o inline do 400 apaga o toast anterior — UMA mensagem na tela`, async () => {
    // O objeto `pix` é lido pela rota a CADA requisição: mudá-lo aqui troca a
    // resposta do REENVIO sem tocar no harness. É a sequência real — o Pix caiu,
    // a pessoa tenta de novo, e agora o servidor recusa o documento.
    const pix = { httpStatus: 503, corpo: { detail: FALHA_503 } };
    const { page } = await abrirForm({ viewport, pix });
    await enviarDoc(page);
    await page.waitForTimeout(500);
    assert.ok(await page.$eval("#toast", (e) => e.classList.contains("show")),
      "o 503 nem chegou a mostrar o toast: o cenário das duas mensagens não foi montado");
    pix.httpStatus = 400;
    pix.corpo = { detail: "Informe um CPF ou CNPJ válido." };
    await enviarDoc(page, CPF);            // dentro dos 3800 ms do timer do showToast
    await page.waitForTimeout(400);        // > .25s da transição de opacidade
    const visto = await page.evaluate(() => {
      const t = document.getElementById("toast");
      return {
        inline: document.getElementById("pix-doc-erro").textContent,
        toastVisivel: t.classList.contains("show"),
        opacidade: getComputedStyle(t).opacity,
        toastTexto: t.textContent,
      };
    });
    assert.equal(visto.inline, "Informe um CPF ou CNPJ válido.",
      `a recusa do documento não chegou ao campo: "${visto.inline}"`);
    assert.equal(visto.toastVisivel, false,
      `DUAS mensagens na tela: o toast do 503 ("${await page.textContent("#toast")}") continua`
      + " por cima do véu, contradizendo o campo");
    assert.ok(parseFloat(visto.opacidade) <= 0.05,
      `o toast velho ainda está visível: opacidade ${visto.opacidade}`);
    // Opacidade 0 não some para leitor de tela: o `#toast` é `role="status"` e
    // seguia com `display: block`, `visibility: visible` e o TEXTO velho na
    // árvore de acessibilidade, contradizendo o campo para quem não vê a tela.
    assert.equal(visto.toastTexto, "",
      `o texto velho continua no \`role="status"\`: "${visto.toastTexto}"`);
    await page.close();
  });

  test(`PT18e (${tela}): reenviar depois do 503 não deixa o toast velho por cima do QR`,
    async () => {
      const pix = { httpStatus: 503, corpo: { detail: FALHA_503 } };
      const { page } = await abrirForm({ viewport, pix });
      await enviarDoc(page);
      await page.waitForTimeout(500);
      assert.ok(await page.$eval("#toast", (e) => e.classList.contains("show")),
        "o 503 nem mostrou o toast: o cenário das duas mensagens não foi montado");
      pix.httpStatus = 200;
      pix.corpo = null;                    // volta ao corpo padrão do harness: o QR
      await enviarDoc(page, CPF);          // dentro dos 3800 ms do timer do showToast
      await page.waitForSelector(".pix-code");
      await page.waitForTimeout(400);
      const brilho = await brilhoMax(page, "#toast");
      assert.ok(brilho < 100,
        `o QR está na tela e o toast do 503 ("${await page.textContent("#toast")}")`
        + ` continua por cima: brilho máximo ${brilho}/255 no retângulo dele`);
      await page.close();
    });

  // Os DOIS 409 que TROCAM o corpo do modal — migração e "já pago" (este entrou
  // no #361, depois da enumeração) — são a mesma classe: caixa nova por baixo do
  // toast velho. Mesmo corpo de teste, um `for` em vez de um irmão copiado.
  for (const [caso, corpo, marca] of [
    ["migração", { detail: { error: "stripe_active", current_period_end: "2026-12-05" } },
      /trocar o cartão pelo pix/i],
    ["já pago", { detail: { error: "pix_future_purchase_conflict",
                            covered_until: "2027-03-04T00:00:00+00:00" } },
      /já tem tempo pago/i],
  ]) {
    test(`PT18f (${tela}, ${caso}): reenviar depois do 503 não deixa o toast por cima da caixa nova`,
      async () => {
        const pix = { httpStatus: 503, corpo: { detail: FALHA_503 } };
        const { page } = await abrirForm({ viewport, pix });
        await enviarDoc(page);
        await page.waitForTimeout(500);
        assert.ok(await page.$eval("#toast", (e) => e.classList.contains("show")),
          "o 503 nem mostrou o toast: o cenário das duas mensagens não foi montado");
        pix.httpStatus = 409;
        pix.corpo = corpo;
        await enviarDoc(page, CPF);
        await page.waitForTimeout(400);
        assert.match(await page.textContent(".pix-box"), marca,
          `o 409 de ${caso} não trocou o corpo do modal: o cenário não foi montado`);
        const brilho = await brilhoMax(page, "#toast");
        assert.ok(brilho < 100,
          `a caixa de ${caso} está na tela e o toast do 503 continua por cima:`
          + ` brilho máximo ${brilho}/255 no retângulo dele`);
        await page.close();
      });
  }
}

/**
 * O desfecho que NÃO passa pelo `pixEnviar`: a forma inválida é recusada no
 * submit e volta na hora. Sem a limpeza no início do submit, o único ponto do
 * `pixEnviar` não alcança este caso — e era o que a mutação do Tester provou
 * (só o sítio da forma revertido: 47 pass, 0 fail).
 */
test("PT18g: a recusa de FORMA também apaga o toast do 503, sem POST nenhum", async () => {
  const pix = { httpStatus: 503, corpo: { detail: FALHA_503 } };
  const { page, chamadas } = await abrirForm({ pix });
  await enviarDoc(page);
  await page.waitForTimeout(500);
  assert.ok(await page.$eval("#toast", (e) => e.classList.contains("show")),
    "o 503 nem mostrou o toast: o cenário das duas mensagens não foi montado");
  await enviarDoc(page, "1112223334");     // 10 dígitos: nem chega a sair
  await page.waitForTimeout(400);
  assert.equal(chamadas.pixCheckout, 1,
    `o documento malformado foi para o servidor (${chamadas.pixCheckout} chamadas)`);
  const visto = await page.evaluate(() => ({
    inline: document.getElementById("pix-doc-erro").textContent,
    toast: document.getElementById("toast").classList.contains("show"),
  }));
  assert.equal(visto.inline, "Informe os 11 dígitos do CPF ou os 14 do CNPJ.",
    `a recusa de forma não chegou ao campo: "${visto.inline}"`);
  assert.equal(visto.toast, false,
    "DUAS mensagens na tela: o toast do 503 continua por cima do véu, contradizendo o campo");
  const brilho = await brilhoMax(page, "#toast");
  assert.ok(brilho < 100,
    `o toast do 503 continua legível por cima do modal: brilho máximo ${brilho}/255`);
  await page.close();
});

test("PT18b: o 409 com detail objeto continua mostrando a `message`", async () => {
  const { page } = await abrirForm({
    pix: { httpStatus: 409,
           corpo: { detail: { error: "lifetime",
                              message: "Você já tem acesso vitalício de brinde." } } },
  });
  await enviarDoc(page);
  await page.waitForTimeout(300);
  const toast = await page.textContent("#toast");
  assert.equal(toast, "Você já tem acesso vitalício de brinde.",
    `o detail objeto parou de ser lido pela message: "${toast}"`);
  await page.close();
});

/**
 * PT19 — A ETIQUETA DO TOGGLE: O PIX PRECISA SER VISÍVEL NO CICLO MENSAL.
 *
 * O CTA "Pagar no Pix" só nasce no anual (PT1), então quem abre a página — que
 * carrega em `monthly` — não tinha nenhum sinal de que Pix existe. A etiqueta
 * `#pix-cycle-note` é esse sinal, e por isso NÃO pode depender do ciclo.
 *
 * Os dois controles do §3, no grupo:
 *   · negativo — apague `if (nota) nota.hidden = !pixAVenda();` do `pbPixInit`
 *     (pix-checkout.js), ou mova a linha para dentro do `pbPixRefresh` com o
 *     `anual` na condição: o caso do MENSAL fica vermelho, e ele é o que estava
 *     verde antes da mutação;
 *   · positivo — o PT19b prova que a etiqueta continua ESCONDIDA sem a flag e
 *     para o vitalício. Sem ele, um `nota.hidden = false` fixo passaria no PT19
 *     anunciando meio de pagamento que a página não vende.
 */
// Só `offsetParent`, e o `&& !e.hidden` SAIU: ele olhava o atributo em vez da
// tela, e com isso o grupo ficava cego para CSS que vence o `hidden`. Foi um bug
// real — `#pix-cycle-note { display: inline-block }` (seletor de ID) vence o
// `[hidden] { display: none }` do navegador, e a etiqueta aparecia com a flag
// desligada enquanto PT19b continuava verde porque `e.hidden` ainda era true.
const etiquetaVisivel = (page) => page.$eval(
  "#pix-cycle-note", (e) => e.offsetParent !== null);

test("PT19: a etiqueta de Pix aparece no ciclo mensal e continua no anual", async () => {
  const { page } = await abrirPrecos();
  assert.equal(await contarCtas(page), 0, "âncora: no mensal não há CTA de Pix nenhum");
  assert.equal(await etiquetaVisivel(page), true,
    "o ciclo mensal não anuncia o Pix em lugar nenhum da tela");
  assert.match(await page.textContent("#pix-cycle-note"), /Pix/,
    "a etiqueta existe mas não diz Pix");
  await page.click("#cycle-annual");
  assert.equal(await etiquetaVisivel(page), true, "a etiqueta sumiu ao trocar para o anual");
  await page.click("#cycle-annual");
  assert.equal(await etiquetaVisivel(page), true, "a etiqueta sumiu na volta para o mensal");
  await page.close();
});

test("PT19b: sem a flag, e para o vitalício, a etiqueta não aparece", async () => {
  const base = { essencial_available: true, plus_available: true, pro_available: true };
  for (const [nome, plansConfig, sub] of [
    ["sem pix_annual_available", base, { active: false }],
    ["flag false", { ...base, pix_annual_available: false }, { active: false }],
    ["vitalício", { ...base, pix_annual_available: true }, { active: true, lifetime: true }],
  ]) {
    const { page } = await abrirPrecos({ plansConfig, sub });
    assert.equal(await etiquetaVisivel(page), false,
      `${nome}: a página anunciou Pix que ela não vende (mensal)`);
    await page.click("#cycle-annual");
    assert.equal(await etiquetaVisivel(page), false,
      `${nome}: a página anunciou Pix que ela não vende (anual)`);
    await page.close();
  }
});

/**
 * PT19c — A ETIQUETA PARA QUEM NÃO VÊ A TELA.
 *
 * Ela é revelada DEPOIS do load (o `pbPixInit` só roda quando as duas
 * requisições voltam), e quem navega controle por controle chega ao botão
 * "Anual" sem passar por ela. Duas amarras, medidas aqui:
 *
 *   · `#pix-cycle-live` com `aria-live="polite"` EM VOLTA da pílula, presente
 *     desde o parse — região registrada e revelada no mesmo instante não
 *     anuncia, então são dois elementos e não um `aria-live` na própria pílula;
 *   · `aria-describedby` no `#cycle-annual`, posto e RETIRADO junto com ela.
 *
 * O `aria-describedby` sair é a metade que vale dinheiro: elemento diretamente
 * referenciado é lido mesmo `hidden` (accname), então um atributo fixo no HTML
 * anunciaria "Pix disponível no anual" para quem não pode comprar — o mesmo
 * defeito que o `hidden` existe para evitar, por outra porta. O caso sem flag é
 * o controle positivo deste par.
 */
test("PT19c: a etiqueta é anunciável, e o vínculo com o Anual entra e sai com ela", async () => {
  const base = { essencial_available: true, plus_available: true, pro_available: true };
  const lido = (page) => page.evaluate(() => ({
    live: document.getElementById("pix-cycle-live")?.getAttribute("aria-live"),
    // O `aria-live` precisa ENVOLVER a pílula: irmão não anuncia a revelação.
    envolve: !!document.getElementById("pix-cycle-live")
      ?.contains(document.getElementById("pix-cycle-note")),
    describedby: document.getElementById("cycle-annual")?.getAttribute("aria-describedby"),
  }));

  const { page } = await abrirPrecos();
  assert.deepEqual(await lido(page),
    { live: "polite", envolve: true, describedby: "pix-cycle-note" });
  await page.click("#cycle-annual");
  assert.equal((await lido(page)).describedby, "pix-cycle-note",
    "o vínculo caiu ao trocar de ciclo");
  await page.close();

  for (const plansConfig of [base, { ...base, pix_annual_available: false }]) {
    const semPix = await abrirPrecos({ plansConfig });
    const r = await lido(semPix.page);
    assert.equal(r.live, "polite", "a região aria-live tem de existir mesmo sem a flag");
    assert.equal(r.describedby, null,
      "o botão Anual descreve um Pix que a página não vende");
    await semPix.page.close();
  }
});

/**
 * PT19d–f — A ETIQUETA ESPERA SABER QUEM ESTÁ OLHANDO; O CTA NÃO.
 *
 * O `loadPlansState` publica o estado do Pix DUAS vezes: `publicarPix(null,
 * false)` antes do /billing/subscription e `publicarPix(subState, resolvida)` depois.
 * A primeira existe para o CTA nascer cedo (PT15) e é deliberada — caminho de
 * RESGATE de quem migra do cartão enquanto o Stripe está lento. Mas `sub = null`
 * também é o valor do DESLOGADO, então o `pixAVenda()` lia o estado ainda
 * desconhecido como elegível e a ETIQUETA — que é ANÚNCIO — subia para o
 * vitalício até a requisição voltar. Se ela pendura, o anúncio fica.
 *
 * O conserto é o terceiro estado (`resolvida`), não um teste de `null`: testar
 * `null` esconderia a etiqueta justamente de quem ela existe para convencer.
 *
 * Os dois controles do §3, no grupo:
 *   · negativo — tire o `&& pixSubResolvida` do `pbPixInit` (pix-checkout.js) ou
 *     troque o `publicarPix(null, false)` da precos.html por `(null, true)`: o
 *     PT19d fica VERMELHO, e ele é caso novo que já nasce verde com o conserto;
 *   · positivo — PT19e (deslogado, que é 401 e não 200) e PT19f (assinante não
 *     vitalício) provam que a etiqueta continua aparecendo para quem pode
 *     comprar. Sem eles, `nota.hidden = true` fixo passaria no PT19d.
 *
 * PT19g fecha a outra metade: a consulta que FALHA (5xx, rede fora, JSON
 * malformado) também guarda `subState = null`, e chamá-la de resolvida deixava o
 * anúncio de pé para o vitalício indefinidamente. O sinal é o retorno novo do
 * `loadSubscription` — 200 com JSON válido e 401 resolvem, o resto não.
 * Negativo dele: troque o `publicarPix(subState, resolvida)` da precos.html de
 * volta por `(subState, true)` — PT19g fica VERMELHO e PT19d/e/f seguem verdes.
 */
test("PT19d: com /billing/subscription pendurado, o vitalício não vê a etiqueta", async () => {
  const { page } = await abrirPrecos({
    sub: { active: true, lifetime: true },
    atrasos: { "/billing/subscription": 4000 },
  });
  assert.equal(await etiquetaVisivel(page), false,
    "a etiqueta anunciou Pix antes de saber se este usuário pode comprar");
  await page.waitForTimeout(1500);
  assert.equal(await etiquetaVisivel(page), false,
    "a etiqueta subiu durante a janela do /billing/subscription (1,5 s depois)");
  // Âncora do PT15: o que espera é a ETIQUETA, não o CTA. Se este 3 virar 0, o
  // conserto atropelou a migração cartão → Pix com o Stripe ruim.
  await page.click("#cycle-annual");
  assert.equal(await contarCtas(page), 3,
    "o CTA de Pix passou a esperar o /billing/subscription");
  await page.close();
});

test("PT19e: deslogado (401 no /billing/subscription) continua vendo a etiqueta", async () => {
  const { page } = await abrirPrecos({ subStatus: 401, sub: { detail: "Não autenticado" } });
  assert.equal(await etiquetaVisivel(page), true,
    "a etiqueta sumiu para o deslogado, que é o público que ela existe para convencer");
  assert.equal(
    await page.$eval("#cycle-annual", (e) => e.getAttribute("aria-describedby")),
    "pix-cycle-note", "o vínculo com o botão Anual não voltou para o deslogado");
  await page.close();
});

test("PT19f: assinante não vitalício vê a etiqueta", async () => {
  const { page } = await abrirPrecos({
    sub: { active: true, gateway: "stripe", plan: "plus", interval: "monthly" },
  });
  assert.equal(await etiquetaVisivel(page), true,
    "a etiqueta sumiu para quem PODE comprar o anual no Pix");
  await page.close();
});

// Falha de consulta não identifica o visitante; o CTA continua nascendo cedo.
for (const [nome, subRoute] of [
  ["500", (r) => r.fulfill({ status: 500, body: "erro interno" })],
  ["403", (r) => r.fulfill({ status: 403, body: "proibido" })],
  ["rede", (r) => r.abort()],
  ["JSON malformado", (r) => r.fulfill({ status: 200, contentType: "application/json", body: "{" })],
]) {
  test(`PT19g: /billing/subscription em ${nome} não resolve — a etiqueta não aparece`, async () => {
    const { page } = await abrirPrecos({ subRoute });
    assert.equal(await etiquetaVisivel(page), false,
      "a falha do /billing/subscription foi lida como 'sem assinatura' e anunciou Pix");
    await page.click("#cycle-annual");
    assert.equal(await contarCtas(page), 3,
      "o CTA de resgate morreu junto com a etiqueta quando a consulta falhou");
    await page.close();
  });
}
